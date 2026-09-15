"""WN-A1 top-level orchestration: WeatherNext -> current Kalshi daily-high -> plain alert.

Every dependency (Kalshi transport, WeatherNext reader, clock) is injectable so tests never
require real network access or real Google Cloud credentials. Nothing here places, previews,
or authorizes an order; no execution or credential-signing module is imported anywhere in
this package.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from services.market_universe import public_read
from services.market_universe.domain import Event, Market, Series
from services.market_universe.m27e_public_acceptance import validate_response_evidence

from .wn_a1_alert import (
    DEFAULT_POLICY,
    AlertDecision,
    AlertPolicy,
    decide_alert,
    render_primary_alert,
)
from .wn_a1_current_daily_high_authority import (
    POLICY_IDENTITY as CONTRACT_POLICY_IDENTITY,
)
from .wn_a1_current_daily_high_authority import (
    SERIES_TICKER,
    CurrentDailyHighContract,
    CurrentDailyHighRoute,
    CurrentDailyHighRouteState,
    route_current_daily_high,
)
from .wn_a1_domain import WnA1Error
from .wn_a1_evaluation_record import EvaluationRecord
from .wn_a1_market_economics import KalshiMarketSnapshot, is_fresh
from .wn_a1_probability import (
    BoundaryRiskDiagnostic,
    MemberDailyHigh,
    RawProbabilityResult,
    boundary_risk,
    compute_raw_probability,
)
from .wn_a1_weathernext_evidence import (
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
    acquired_at: datetime,
    reader: ZarrReader = gcs_zarr_reader,
) -> EnsembleAcquisitionResult:
    raw_rows, content_hash = reader(
        request.source_object,
        request.init_time,
        request.latitude,
        request.longitude,
        window_start,
        window_end,
    )
    return build_ensemble_evidence(
        model=MODEL,
        source_object=request.source_object,
        init_time=request.init_time,
        acquired_at=acquired_at,
        variable=VARIABLE,
        latitude=request.latitude,
        longitude=request.longitude,
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
    market_yes_probability = snapshot.yes_best_ask if snapshot is not None else None

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
        market_yes_probability=market_yes_probability,
        raw=raw,
        window_status=contract.window_status if contract is not None else None,
        boundary=boundary,
        policy=policy,
    )
    hotter_or_colder: str | None = None
    if decision.raw_probability is not None and decision.market_yes_probability is not None:
        hotter_or_colder = (
            "hotter" if decision.raw_probability > decision.market_yes_probability else "colder"
        )
    target_date = contract.local_date if contract is not None else evaluated_at.date()
    text = render_primary_alert(
        decision=decision,
        location="Chicago",
        target_date=target_date,
        hotter_or_colder=hotter_or_colder,
    )
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
    market_yes_probability_by_ticker: Mapping[str, Decimal | None],
) -> str | None:
    """Pick the sibling market with the largest raw |WeatherNext - market| gap."""
    best_ticker: str | None = None
    best_gap = Decimal(-1)
    for ticker, contract in sibling_contracts.items():
        market_prob = market_yes_probability_by_ticker.get(ticker)
        if market_prob is None:
            continue
        raw = compute_raw_probability(members, contract)
        gap = abs(raw.probability - market_prob)
        if gap > best_gap:
            best_gap = gap
            best_ticker = ticker
    return best_ticker
