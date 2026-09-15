"""WN-A1 top-level orchestration: WeatherNext -> current Kalshi daily-high -> plain alert.

``run_canonical`` is the ONE canonical, research-only, end-to-end entrypoint: live Kalshi
event/contract acquisition -> reviewed WeatherNext GCS acquisition -> probability
calculation -> current orderbook/taker economics -> side-aware candidate selection ->
plain-English alert -> durable attempt persistence. It takes no transport-override
parameters at all, so no test fixture or injected fake reader can ever produce a record
indistinguishable from a genuine canonical live run. It also takes no caller-supplied clock
or timestamp: ``pipeline_started_at``, the WeatherNext acquisition timestamp, and each
market's ``decision_at`` are all captured internally from the real UTC clock -- see
``_run_evaluation``'s docstring for the exact chronology this enforces. ``_run_evaluation``
is the deliberately separate, explicitly internal/fixture-only composition seam every WN-A1
test uses instead (it may accept an injected deterministic ``clock``, but ``run_canonical``
itself hardwires the real one); ``discover_event``/``acquire_market_snapshot``/
``acquire_weathernext_evidence``/``evaluate_candidate`` remain independently injectable and
independently testable, as before. Nothing here places, previews, or authorizes an order; no
execution or credential-signing module is imported anywhere in this package.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from services.market_universe import public_read
from services.market_universe.domain import Event, Market, Series, stable_hash
from services.market_universe.m27e_public_acceptance import validate_response_evidence

from .wn_a1_alert import (
    DEFAULT_POLICY,
    AlertDecision,
    AlertPolicy,
    decide_alert,
    render_primary_alert,
)
from .wn_a1_attempt_store import (
    WnA1Attempt,
    WnA1AttemptStore,
    WnA1RunStart,
    WnA1RunTerminal,
    open_default_attempt_store,
    weathernext_member_rows,
)
from .wn_a1_current_daily_high_authority import (
    POLICY_IDENTITY as CONTRACT_POLICY_IDENTITY,
)
from .wn_a1_current_daily_high_authority import (
    SERIES_TICKER,
    TIMEZONE,
    CurrentDailyHighContract,
    CurrentDailyHighRoute,
    CurrentDailyHighRouteState,
    route_current_daily_high,
)
from .wn_a1_domain import WnA1Error
from .wn_a1_evaluation_record import EvaluationRecord
from .wn_a1_market_economics import (
    Clock,
    KalshiMarketSnapshot,
    acquire_market_snapshot,
    conservative_taker_debit,
    is_fresh,
)
from .wn_a1_probability import (
    BoundaryRiskDiagnostic,
    MemberDailyHigh,
    RawProbabilityResult,
    boundary_risk,
    compute_member_daily_highs,
    compute_raw_probability,
)
from .wn_a1_weathernext_evidence import (
    CHICAGO_STATION_LATITUDE,
    CHICAGO_STATION_LONGITUDE,
    MODEL,
    UNIT,
    VARIABLE,
    EnsembleAcquisitionResult,
    EnsembleStatus,
    ZarrReader,
    build_ensemble_evidence,
    gcs_zarr_reader,
)

EventGetter = Callable[[str], tuple[dict[str, object], bytes]]
SeriesGetter = Callable[[str], dict[str, object]]
MarketGetter = Callable[[str], dict[str, object]]
OrderbookGetter = Callable[[str], tuple[dict[str, object], bytes]]


def _real_clock() -> datetime:
    """The one hardwired real clock ``run_canonical`` uses -- never caller-overridable
    there. ``_run_evaluation`` accepts ``clock`` as an injectable seam so tests/tools can
    supply a deterministic one instead."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class EventDiscovery:
    event_ticker: str
    routes: tuple[CurrentDailyHighRoute, ...]

    @property
    def supported(self) -> tuple[CurrentDailyHighRoute, ...]:
        return tuple(r for r in self.routes if r.state is CurrentDailyHighRouteState.SUPPORTED)

    @property
    def contracts_by_ticker(self) -> dict[str, CurrentDailyHighContract]:
        return {r.market_ticker: r.contract for r in self.supported if r.contract is not None}


def event_ticker_for(target_local_date: date) -> str:
    """Chicago KXHIGHCHI event-ticker naming convention observed live: KXHIGHCHI-<YY><Mon><DD>."""
    return f"{SERIES_TICKER}-{target_local_date.strftime('%y%b%d').upper()}"


def discover_event(
    *,
    event_ticker: str,
    get_event: EventGetter = public_read.get_event_with_body,
    get_series: SeriesGetter = public_read.get,
) -> EventDiscovery:
    """Discover and independently route every current market in one KXHIGHCHI event."""
    event_response, _body = get_event(event_ticker)
    event_payload, _observed = validate_response_evidence(event_response)
    event_raw = event_payload.get("event")
    markets_raw = event_payload.get("markets")
    if not isinstance(event_raw, dict) or not isinstance(markets_raw, list):
        raise WnA1Error("Kalshi event payload malformed or missing nested markets")
    event = Event.parse(event_raw)
    series_response = get_series(public_read.BASE + "/series/" + event.series_ticker)
    series_payload, _series_observed = validate_response_evidence(series_response)
    series_raw = series_payload.get("series")
    if not isinstance(series_raw, dict):
        raise WnA1Error("Kalshi series payload malformed")
    series = Series.parse(series_raw)
    routes = tuple(
        route_current_daily_high(Market.parse(row), event, series)
        for row in markets_raw
        if isinstance(row, dict)
    )
    return EventDiscovery(event_ticker=event.ticker, routes=routes)


def research_probability_window(target_local_date: date) -> tuple[datetime, datetime]:
    """A provisional civil-local-day window used ONLY to bound the WN-A1 research ensemble
    probability calculation while the real settlement window is unestablished.

    This is NOT a claim about TWC's actual settlement measurement window -- see
    ``wn_a1_current_daily_high_authority.MISSING_SETTLEMENT_WINDOW_EVIDENCE``. Every
    decision computed from members bounded by this window is still hard-capped below
    TAKE A LOOK by ``decide_alert``'s window gate whenever the authoritative
    ``WindowStatus`` is ``NOT_ESTABLISHED`` (true of every live contract in this milestone),
    regardless of how the raw probability came out.
    """
    tz = ZoneInfo(TIMEZONE)
    start = datetime(
        target_local_date.year, target_local_date.month, target_local_date.day, tzinfo=tz
    )
    return start, start + timedelta(days=1)


@dataclass(frozen=True, slots=True)
class WeatherNextRequest:
    source_object: str
    init_time: datetime
    latitude: Decimal
    longitude: Decimal


def acquire_weathernext_evidence(
    request: WeatherNextRequest,
    *,
    window_start: datetime,
    window_end: datetime,
    clock: Clock,
    reader: ZarrReader = gcs_zarr_reader,
) -> EnsembleAcquisitionResult:
    """Item E3: the WeatherNext acquisition boundary captures its own ``acquired_at``
    internally, immediately AFTER the real read succeeds -- never as a caller-supplied
    parameter. By the time ``clock()`` is called here the evidence is positively known to
    have been acquired; ``build_ensemble_evidence`` then fails closed (Item B) if that
    timestamp somehow precedes ``request.init_time``.
    """
    raw_rows, content_hash, selected_latitude, selected_longitude = reader(
        request.source_object,
        request.init_time,
        request.latitude,
        request.longitude,
        window_start,
        window_end,
    )
    acquired_at = clock()
    return build_ensemble_evidence(
        model=MODEL,
        source_object=request.source_object,
        init_time=request.init_time,
        acquired_at=acquired_at,
        variable=VARIABLE,
        requested_latitude=request.latitude,
        requested_longitude=request.longitude,
        selected_latitude=selected_latitude,
        selected_longitude=selected_longitude,
        unit=UNIT,
        raw_rows=raw_rows,
        source_content_hash=content_hash,
    )


@dataclass(frozen=True, slots=True)
class EvaluationOutcome:
    market_ticker: str
    decision: AlertDecision
    primary_alert_text: str
    boundary: BoundaryRiskDiagnostic | None
    record: EvaluationRecord


def evaluate_candidate(
    *,
    candidate_ticker: str,
    sibling_contracts: Mapping[str, CurrentDailyHighContract],
    members: tuple[MemberDailyHigh, ...] | None,
    weathernext_evidence_identity: str | None,
    ensemble_status: EnsembleStatus,
    snapshot: KalshiMarketSnapshot | None,
    evaluated_at: datetime,
    policy: AlertPolicy = DEFAULT_POLICY,
) -> EvaluationOutcome:
    """Evaluate exactly one candidate market; always returns a decision and a record.

    Every evaluated opportunity is preserved via the returned ``EvaluationRecord`` --
    callers must persist it even when the decision is SKIP or TOO UNCERTAIN, to avoid
    selection bias in later scoring.
    """
    contract = sibling_contracts.get(candidate_ticker)
    ensemble_complete = ensemble_status is EnsembleStatus.COMPLETE and members is not None
    market_fresh = snapshot is not None and is_fresh(snapshot, evaluated_at)
    yes_debit = (
        conservative_taker_debit(snapshot.yes_taker_cost, snapshot.quantity)
        if snapshot is not None
        else None
    )
    no_debit = (
        conservative_taker_debit(snapshot.no_taker_cost, snapshot.quantity)
        if snapshot is not None
        else None
    )

    raw: RawProbabilityResult | None = None
    boundary: BoundaryRiskDiagnostic | None = None
    if contract is not None and members is not None:
        raw = compute_raw_probability(members, contract)
        if len(sibling_contracts) >= 2:
            boundary = boundary_risk(members, sibling_contracts, candidate_ticker)

    decision = decide_alert(
        contract_supported=contract is not None,
        ensemble_complete=ensemble_complete,
        market_fresh=market_fresh,
        yes_conservative_debit=yes_debit,
        no_conservative_debit=no_debit,
        raw=raw,
        window_status=contract.window_status if contract is not None else None,
        boundary=boundary,
        policy=policy,
    )
    target_date = contract.local_date if contract is not None else evaluated_at.date()
    text = render_primary_alert(decision=decision, location="Chicago", target_date=target_date)
    record = EvaluationRecord.create(
        market_ticker=candidate_ticker,
        target_local_date=target_date,
        weathernext_evidence_identity=weathernext_evidence_identity,
        kalshi_snapshot_identity=snapshot.snapshot_identity if snapshot is not None else None,
        contract_policy_identity=CONTRACT_POLICY_IDENTITY,
        decision=decision,
        evaluated_at=evaluated_at,
    )
    return EvaluationOutcome(candidate_ticker, decision, text, boundary, record)


def select_candidate(
    sibling_contracts: Mapping[str, CurrentDailyHighContract],
    members: tuple[MemberDailyHigh, ...],
    debits_by_ticker: Mapping[str, tuple[Decimal | None, Decimal | None]],
) -> str | None:
    """Pick the sibling market with the largest SUPPORTED, side-aware buffered gap.

    Never uses absolute |model - price| disagreement: a ticker only becomes a candidate
    when ``decide_alert`` finds at least one side (YES or NO) whose own buffered gap clears
    the frozen research thresholds. Only ``decision.side``/``selected_buffered_gap_pp`` are
    read -- both are computed before ``decide_alert``'s window/boundary ceiling gate, so
    this ranking is unaffected by whether the window happens to be established; it is a
    heuristic over which contract to feature, not itself a TAKE A LOOK claim --
    ``evaluate_candidate`` applies every real gate afterward for the actual decision.
    """
    best_ticker: str | None = None
    best_buffered: Decimal | None = None
    for ticker, contract in sibling_contracts.items():
        yes_debit, no_debit = debits_by_ticker.get(ticker, (None, None))
        raw = compute_raw_probability(members, contract)
        decision = decide_alert(
            contract_supported=True,
            ensemble_complete=True,
            market_fresh=True,
            yes_conservative_debit=yes_debit,
            no_conservative_debit=no_debit,
            raw=raw,
            window_status=contract.window_status,
            boundary=None,
        )
        if decision.side is None or decision.selected_buffered_gap_pp is None:
            continue
        if best_buffered is None or decision.selected_buffered_gap_pp > best_buffered:
            best_buffered = decision.selected_buffered_gap_pp
            best_ticker = ticker
    return best_ticker


def _build_attempt(
    *,
    run_id: str,
    outcome: EvaluationOutcome,
    contract: CurrentDailyHighContract,
    ensemble_result: EnsembleAcquisitionResult,
    snapshot: KalshiMarketSnapshot | None,
    window_start: datetime,
    window_end: datetime,
) -> WnA1Attempt:
    decision = outcome.decision
    evidence = ensemble_result.evidence
    boundary = outcome.boundary
    return WnA1Attempt(
        attempt_id=outcome.record.record_id,
        run_id=run_id,
        target_local_date=contract.local_date.isoformat(),
        market_ticker=contract.market_ticker,
        event_ticker=contract.event_ticker,
        series_ticker=contract.series_ticker,
        contract_policy_identity=CONTRACT_POLICY_IDENTITY,
        contract_policy_version=outcome.record.contract_policy_version,
        contract_comparator=contract.comparator,
        contract_lower=contract.lower,
        contract_upper=contract.upper,
        research_window_start=window_start,
        research_window_end=window_end,
        window_status=contract.window_status.value,
        weathernext_evidence_identity=evidence.evidence_identity if evidence else None,
        weathernext_model=evidence.model if evidence else None,
        weathernext_source_object=evidence.source_object if evidence else None,
        weathernext_init_time=evidence.init_time if evidence else None,
        weathernext_acquired_at=evidence.acquired_at if evidence else None,
        weathernext_requested_latitude=evidence.requested_latitude if evidence else None,
        weathernext_requested_longitude=evidence.requested_longitude if evidence else None,
        weathernext_selected_latitude=evidence.selected_latitude if evidence else None,
        weathernext_selected_longitude=evidence.selected_longitude if evidence else None,
        weathernext_source_content_hash=evidence.source_content_hash if evidence else None,
        weathernext_members=weathernext_member_rows(evidence) if evidence else (),
        kalshi_snapshot_identity=snapshot.snapshot_identity if snapshot is not None else None,
        kalshi_orderbook_observed_at=(
            snapshot.orderbook_observed_at if snapshot is not None else None
        ),
        alert_policy_version=decision.policy_version,
        decision_state=decision.state.value,
        decision_gate_failures=tuple(f.value for f in decision.gate_failures),
        side=decision.side.value if decision.side is not None else None,
        model_probability_yes=decision.model_probability_yes,
        yes_conservative_debit=decision.yes_conservative_debit,
        no_conservative_debit=decision.no_conservative_debit,
        yes_gap_pp=decision.yes_gap_pp,
        yes_buffered_gap_pp=decision.yes_buffered_gap_pp,
        no_gap_pp=decision.no_gap_pp,
        no_buffered_gap_pp=decision.no_buffered_gap_pp,
        yes_fee_quality=(
            snapshot.yes_taker_cost.fee_quality.value
            if snapshot is not None and snapshot.yes_taker_cost is not None
            else None
        ),
        no_fee_quality=(
            snapshot.no_taker_cost.fee_quality.value
            if snapshot is not None and snapshot.no_taker_cost is not None
            else None
        ),
        boundary_members_within_one_f=(
            boundary.members_within_one_f_of_boundary if boundary is not None else None
        ),
        boundary_baseline_ticker=boundary.baseline_preferred_ticker if boundary else None,
        boundary_plus_one_ticker=boundary.plus_one_preferred_ticker if boundary else None,
        boundary_minus_one_ticker=boundary.minus_one_preferred_ticker if boundary else None,
        evaluated_at=outcome.record.evaluated_at,
    )


def _run_invocation_id(
    *,
    event_ticker: str,
    target_local_date: date,
    weathernext_init_time: datetime,
    weathernext_source_object: str,
    weathernext_requested_latitude: Decimal,
    weathernext_requested_longitude: Decimal,
    policy_version: str,
) -> str:
    """Content-derived run identity (Items D/F): the same LOGICAL inputs always produce the
    same ``run_id`` -- deliberately excluding any wall-clock/caller-supplied timestamp, so
    "duplicate exact run identity" means "the same requested evaluation", not "the same
    evaluation attempted at the same instant". This lets ``register_run_start`` reject a
    genuinely duplicate run before any external getter/reader is ever called (Item F2),
    matching ``WnA1Attempt``'s own content-derived ``attempt_id`` convention.
    """
    return stable_hash(
        (
            "wn-a1-run-invocation-v2-no-wall-clock",
            event_ticker,
            target_local_date.isoformat(),
            weathernext_init_time.astimezone(UTC).isoformat(),
            weathernext_source_object,
            str(weathernext_requested_latitude),
            str(weathernext_requested_longitude),
            policy_version,
        )
    )


def _run_evaluation(
    *,
    target_local_date: date,
    weathernext_init_time: datetime,
    weathernext_source_object: str,
    weathernext_requested_latitude: Decimal,
    weathernext_requested_longitude: Decimal,
    clock: Clock,
    store: WnA1AttemptStore,
    policy: AlertPolicy,
    get_event: EventGetter,
    get_series: SeriesGetter,
    get_market: MarketGetter,
    get_orderbook: OrderbookGetter,
    weathernext_reader: ZarrReader,
) -> tuple[EvaluationOutcome, ...]:
    """The internal, fully-injectable WN-A1 composition seam. Every WN-A1 test uses this
    (directly, or via the lower-level functions it calls) -- never ``run_canonical``, which
    accepts no transport or clock overrides at all. See the module docstring.

    Item E: no timestamp is a caller parameter here. ``clock`` is called to capture, and
    distinguish, three separate instants -- ``pipeline_started_at`` (this function's own
    entry), the WeatherNext acquisition timestamp (captured by ``acquire_weathernext_
    evidence`` immediately after its real read succeeds), and each market's own
    ``decision_at`` (captured only after that market's Kalshi orderbook acquisition has been
    attempted). Every per-market decision fails closed if that market's ``decision_at``
    would precede the WeatherNext acquisition or Kalshi response evidence it is based on
    (see the chronology check below) -- market freshness (Item A) is evaluated against this
    same ``decision_at``, never a caller-supplied instant.

    Item F: exactly one immutable ``WnA1RunStart`` is registered BEFORE any Kalshi
    discovery, WeatherNext acquisition, or market/orderbook acquisition is attempted --
    ``register_run_start`` fails closed on a duplicate exact run identity before this
    function ever calls an external getter/reader. Exactly one ``WnA1RunTerminal``
    (``COMPLETED`` or ``FAILED``) is appended once this invocation actually finishes,
    regardless of whether Kalshi event discovery fails, zero markets are supported, every
    route abstains, or WeatherNext acquisition fails -- so a canonical invocation can never
    return zero per-market records and leave no durable trace that it was attempted at all.
    A run whose process dies between registration and its terminal record is durably
    visible as STARTED/interrupted (``WnA1AttemptStore.run_status``), never silently
    disappeared.
    """
    event_ticker = event_ticker_for(target_local_date)
    run_id = _run_invocation_id(
        event_ticker=event_ticker,
        target_local_date=target_local_date,
        weathernext_init_time=weathernext_init_time,
        weathernext_source_object=weathernext_source_object,
        weathernext_requested_latitude=weathernext_requested_latitude,
        weathernext_requested_longitude=weathernext_requested_longitude,
        policy_version=policy.version,
    )
    pipeline_started_at = clock()

    # Item F1/F2: registered BEFORE any external getter/reader is ever called. A genuinely
    # duplicate run identity is rejected right here.
    store.register_run_start(
        WnA1RunStart(
            run_id=run_id,
            target_local_date=target_local_date.isoformat(),
            event_ticker=event_ticker,
            weathernext_source_object=weathernext_source_object,
            weathernext_init_time=weathernext_init_time,
            weathernext_requested_latitude=weathernext_requested_latitude,
            weathernext_requested_longitude=weathernext_requested_longitude,
            alert_policy_version=policy.version,
            pipeline_started_at=pipeline_started_at,
        )
    )

    discovery: EventDiscovery | None = None
    discovery_state = "OK"
    discovery_reason: str | None = None
    weathernext_state = "COMPLETE"
    weathernext_reason: str | None = None
    weathernext_acquired_at: datetime | None = None
    route_outcomes: list[tuple[str, str, str | None]] = []
    attempt_ids: list[str] = []
    outcomes: list[EvaluationOutcome] = []
    status = "FAILED"
    failure_reason: str | None = None
    try:
        try:
            discovery = discover_event(
                event_ticker=event_ticker, get_event=get_event, get_series=get_series
            )
        except WnA1Error as exc:
            discovery_state = "DISCOVERY_FAILED"
            discovery_reason = str(exc)

        window_start, window_end = research_probability_window(target_local_date)
        request = WeatherNextRequest(
            source_object=weathernext_source_object,
            init_time=weathernext_init_time,
            latitude=weathernext_requested_latitude,
            longitude=weathernext_requested_longitude,
        )
        try:
            ensemble_result = acquire_weathernext_evidence(
                request,
                window_start=window_start,
                window_end=window_end,
                clock=clock,
                reader=weathernext_reader,
            )
        except WnA1Error as exc:
            ensemble_result = EnsembleAcquisitionResult(EnsembleStatus.INCOMPLETE, None, {})
            weathernext_state = "ACQUISITION_FAILED"
            weathernext_reason = str(exc)
        if ensemble_result.evidence is not None:
            weathernext_acquired_at = ensemble_result.evidence.acquired_at
        if ensemble_result.status is not EnsembleStatus.COMPLETE and weathernext_reason is None:
            weathernext_state = "INCOMPLETE"
            weathernext_reason = (
                f"missing WeatherNext members for {len(ensemble_result.missing_samples_by_hour)} "
                "valid hour(s)"
                if ensemble_result.missing_samples_by_hour
                else "WeatherNext evidence unavailable"
            )

        # A window-bounding failure (e.g. no member values fall inside the research window)
        # leaves the successfully-acquired evidence itself intact and persistable -- only
        # the decision's ensemble_complete gate degrades to DATA NOT READY.
        members: tuple[MemberDailyHigh, ...] | None = None
        if (
            ensemble_result.status is EnsembleStatus.COMPLETE
            and ensemble_result.evidence is not None
        ):
            try:
                members = compute_member_daily_highs(
                    ensemble_result.evidence, window_start, window_end
                )
            except WnA1Error:
                members = None

        contracts_by_ticker = discovery.contracts_by_ticker if discovery is not None else {}
        for route in discovery.routes if discovery is not None else ():
            if route.state is not CurrentDailyHighRouteState.SUPPORTED:
                route_outcomes.append(
                    (
                        route.market_ticker,
                        route.state.value,
                        route.reason.value if route.reason is not None else None,
                    )
                )
                continue
            contract = route.contract
            assert contract is not None  # noqa: S101 -- guaranteed by ROUTESTATE.SUPPORTED
            try:
                snapshot: KalshiMarketSnapshot | None = acquire_market_snapshot(
                    market_ticker=route.market_ticker,
                    get_market=get_market,
                    get_event=get_event,
                    get_series=get_series,
                    get_orderbook=get_orderbook,
                )
            except WnA1Error:
                snapshot = None

            # Item E4: decision_at is captured only AFTER all evidence required for this
            # market's decision exists, including the Kalshi orderbook acquisition attempt.
            decision_at = clock()

            # Item E5: require chronology -- fail closed (abort the run) rather than
            # silently persisting a decision that claims to postdate evidence it could not
            # actually have seen yet.
            if (
                ensemble_result.evidence is not None
                and ensemble_result.evidence.acquired_at > decision_at
            ):
                raise WnA1Error(
                    "WN-A1 chronology violated: WeatherNext acquired_at is after this "
                    "market's decision_at"
                )
            if snapshot is not None and (
                snapshot.market_observed_at > decision_at
                or snapshot.orderbook_observed_at > decision_at
            ):
                raise WnA1Error(
                    "WN-A1 chronology violated: a Kalshi response observed_at is after "
                    "this market's decision_at"
                )

            outcome = evaluate_candidate(
                candidate_ticker=route.market_ticker,
                sibling_contracts=contracts_by_ticker,
                members=members,
                weathernext_evidence_identity=(
                    ensemble_result.evidence.evidence_identity
                    if ensemble_result.evidence is not None
                    else None
                ),
                ensemble_status=ensemble_result.status,
                snapshot=snapshot,
                evaluated_at=decision_at,
                policy=policy,
            )
            attempt = _build_attempt(
                run_id=run_id,
                outcome=outcome,
                contract=contract,
                ensemble_result=ensemble_result,
                snapshot=snapshot,
                window_start=window_start,
                window_end=window_end,
            )
            store.append(attempt)
            outcomes.append(outcome)
            attempt_ids.append(outcome.record.record_id)
            route_outcomes.append((route.market_ticker, "EVALUATED", outcome.decision.state.value))

        status = "COMPLETED"
    except Exception as exc:
        failure_reason = str(exc)
        raise
    finally:
        store.append_run_terminal(
            WnA1RunTerminal(
                run_id=run_id,
                status=status,
                pipeline_started_at=pipeline_started_at,
                terminal_at=clock(),
                target_local_date=target_local_date.isoformat(),
                event_ticker=event_ticker,
                weathernext_source_object=weathernext_source_object,
                weathernext_init_time=weathernext_init_time,
                weathernext_requested_latitude=weathernext_requested_latitude,
                weathernext_requested_longitude=weathernext_requested_longitude,
                discovery_state=discovery_state,
                discovery_reason=discovery_reason,
                weathernext_state=weathernext_state,
                weathernext_reason=weathernext_reason,
                weathernext_acquired_at=weathernext_acquired_at,
                route_outcomes=tuple(route_outcomes),
                attempt_ids=tuple(attempt_ids),
                alert_policy_version=policy.version,
                failure_reason=failure_reason,
            )
        )

    return tuple(outcomes)


def run_canonical(
    *,
    target_local_date: date,
    weathernext_init_time: datetime,
    weathernext_source_object: str,
    weathernext_requested_latitude: Decimal = CHICAGO_STATION_LATITUDE,
    weathernext_requested_longitude: Decimal = CHICAGO_STATION_LONGITUDE,
) -> tuple[EvaluationOutcome, ...]:
    """The ONE canonical, research-only, real-live-acquisition WN-A1 entrypoint.

    live Kalshi event/contract acquisition -> reviewed WeatherNext GCS acquisition ->
    probability calculation -> current orderbook/taker economics -> side-aware candidate
    selection -> plain-English alert -> durable attempt persistence. Every dependency is
    hardwired to the real, reviewed transport and configuration: ``DEFAULT_POLICY``,
    ``open_default_attempt_store()``, ``services.market_universe.public_read``,
    ``gcs_zarr_reader``, and the real UTC clock (``_real_clock``). This function accepts no
    store/policy/getter/reader/clock override parameters at all -- only the target-date and
    WeatherNext-run identity inputs needed to identify the requested evaluation -- so no
    caller can substitute an isolated persistence destination, a caller-selected policy, a
    fake transport, or a backdated/forward-dated clock and still call this the canonical
    live entrypoint. ``pipeline_started_at``, the WeatherNext acquisition timestamp, and
    each market's ``decision_at`` are all captured internally, never accepted as parameters
    -- see ``test_run_canonical_signature_is_exactly_the_evaluation_identity_inputs`` for
    the exact signature regression this guarantees. Persists every evaluated attempt,
    including SKIP/TOO UNCERTAIN/DATA NOT READY, to the fixed-location durable store, bound
    to a pre-registered run, and always leaves a durable run-level terminal record even when
    zero markets are supported -- see ``_run_evaluation``. Places, previews, or authorizes
    no order anywhere. Tests/tools that need injected dependencies or a deterministic clock
    use ``_run_evaluation`` directly instead.
    """
    return _run_evaluation(
        target_local_date=target_local_date,
        weathernext_init_time=weathernext_init_time,
        weathernext_source_object=weathernext_source_object,
        weathernext_requested_latitude=weathernext_requested_latitude,
        weathernext_requested_longitude=weathernext_requested_longitude,
        clock=_real_clock,
        store=open_default_attempt_store(),
        policy=DEFAULT_POLICY,
        get_event=public_read.get_event_with_body,
        get_series=public_read.get,
        get_market=public_read.get_market,
        get_orderbook=public_read.get_orderbook_with_body,
        weathernext_reader=gcs_zarr_reader,
    )
