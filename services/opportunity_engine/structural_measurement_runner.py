"""M27B.2 operator: repeated, read-only, unauthenticated-public-only structural-lead measurement.

Composes, without reimplementing, exactly the following canonical reviewed pieces:

* universe refresh -- :class:`services.market_universe.sync.UniverseSynchronizer` and
  :class:`services.market_universe.collect.PublicUniverseTransport`, the same reviewed public-only
  path the M26H.3 acceptance used to build the 84,724-market archive M27B.1 replayed;
* canonical discovery -- :func:`services.opportunity_engine.structural.scan_structural_markets`;
* canonical exact confirmation --
  :func:`services.opportunity_engine.structural.confirm_structural_lead` via
  :func:`services.opportunity_engine.authoritative_economics.build_authoritative_market_economics`;
* canonical fee resolution --
  :func:`services.opportunity_engine.live_fees.resolve_current_fee_regime`;
* canonical contract-semantic parsing -- :mod:`services.contract_intelligence.specification`, the
  SAME generic parser :class:`services.market_universe.router.MarketUniverseRouter` uses for the
  whole-exchange census. This is not a family-specific parser: any market with a semantically
  ``VALID`` specification is eligible for exact confirmation, not only weather or CPI.

Every acquisition in this module is the SAME bounded, unauthenticated, fixed-origin PUBLIC GET
boundary (:mod:`services.market_universe.public_read`) every other public-read path in this
repository already uses. No credential, signer, risk, or execution import exists anywhere in this
module -- see ``tests/test_m27b2_architecture.py``.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from services.contract_intelligence.specification import (
    ContractSpecification,
    ContractSpecificationParser,
    SemanticsInputBundle,
    SemanticStatus,
)
from services.market_universe import public_read
from services.market_universe.archive import UniverseObservationArchive
from services.market_universe.collect import (
    MAX_EVENT_RECONCILIATION_REQUESTS,
    OPEN_NON_MVE_V2,
    PublicUniverseTransport,
)
from services.market_universe.domain import Event, Market, UniverseValidationError, stable_hash
from services.market_universe.market_snapshot import acquire_market_snapshot
from services.market_universe.orderbook_snapshot import (
    acquire_orderbook_snapshot,
    validate_orderbook_snapshot,
)
from services.market_universe.pricing import PriceLadder
from services.market_universe.sync import (
    Completeness,
    MemoryUniverseRepository,
    UniverseSynchronizer,
)

from .authoritative_economics import build_authoritative_market_economics
from .domain import OpportunityError
from .fees import current_event_formula_policy
from .live_economics import DiscoveryQuotes
from .live_fees import CurrentSeriesFeeObservation, EventFeeOverride, resolve_current_fee_regime
from .structural import (
    StructuralLead,
    StructuralScanResult,
    confirm_structural_lead,
    scan_structural_markets,
)
from .structural_measurement import (
    LeadObservation,
    MeasurementState,
    record_ambiguous,
    record_disappeared,
    record_discovery_only,
    record_exact_confirmation,
    relationship_id,
)
from .structural_measurement_store import StructuralMeasurementStore

# Conservative default cadence: this is an operational courtesy to the public API, never an
# implied statement that leads persist (or fail to persist) on any particular timescale -- the
# lifetime measurements in structural_measurement.py answer that question empirically.
DEFAULT_CADENCE_SECONDS = 900

_QUOTE_FIELDS = (
    "yes_bid_dollars",
    "yes_ask_dollars",
    "yes_bid_size_fp",
    "yes_ask_size_fp",
    "no_bid_dollars",
    "no_ask_dollars",
    "volume_fp",
    "volume_24h_fp",
    "open_interest_fp",
    "liquidity_dollars",
)

MarketReader = Callable[[str], tuple[dict[str, object], bytes]]
SeriesReader = Callable[[str], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class UniverseRefreshResult:
    repo: MemoryUniverseRepository
    complete: bool


def refresh_universe(
    archive_path: str,
    *,
    transport: object | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> UniverseRefreshResult:
    """Bounded public-only universe refresh.

    Reuses the exact reviewed :class:`UniverseSynchronizer` /
    :class:`PublicUniverseTransport` path -- never a second parser or transport. This duplicates
    only :func:`services.market_universe.collect.collect_evidence`'s few lines of orchestration
    glue, because that function does not return its :class:`MemoryUniverseRepository`, and the
    scanner needs the resulting Market/Event objects directly.
    """
    live_transport = transport if transport is not None else PublicUniverseTransport()
    archive = UniverseObservationArchive(archive_path)
    repo = MemoryUniverseRepository()
    synchronizer = UniverseSynchronizer(live_transport, repo, archive=archive, clock=clock)  # type: ignore[arg-type]
    market_run = synchronizer.sync("markets", parameters=dict(OPEN_NON_MVE_V2.markets_parameters))
    event_run = synchronizer.sync("events", parameters=dict(OPEN_NON_MVE_V2.events_parameters))
    market_events = {item.event_ticker for item in repo.markets.values()}
    missing = tuple(sorted(market_events - set(repo.events)))
    reconciliation_complete = True
    if missing:
        if (
            market_run.completeness is not Completeness.COMPLETE
            or event_run.completeness is not Completeness.COMPLETE
            or len(missing) > MAX_EVENT_RECONCILIATION_REQUESTS
        ):
            reconciliation_complete = False
        else:
            reconciliation_run = synchronizer.reconcile_events(missing)
            reconciliation_complete = reconciliation_run.completeness is Completeness.COMPLETE
    complete = (
        market_run.completeness is Completeness.COMPLETE
        and event_run.completeness is Completeness.COMPLETE
        and reconciliation_complete
    )
    return UniverseRefreshResult(repo, complete)


def _discovery_quote(raw: Mapping[str, Any]) -> DiscoveryQuotes | None:
    if not all(field_name in raw for field_name in _QUOTE_FIELDS):
        return None
    try:
        return DiscoveryQuotes.parse(dict(raw))
    except UniverseValidationError:
        return None


def run_discovery(repo: MemoryUniverseRepository, *, source_authority: str) -> StructuralScanResult:
    """Run canonical M27B discovery over the current refreshed universe. Reuses
    :func:`scan_structural_markets` verbatim -- this is not a second scanner."""
    quotes = {ticker: _discovery_quote(market.raw) for ticker, market in repo.markets.items()}
    return scan_structural_markets(
        repo.markets.values(),
        events=repo.events,
        discovery_quotes=quotes,
        source_authority=source_authority,
    )


def _build_specification(
    market: Market, event: Event, series_raw: Mapping[str, Any], *, now: datetime
) -> ContractSpecification:
    parser = ContractSpecificationParser()
    return parser.parse(
        SemanticsInputBundle.build(market.raw, event.raw, dict(series_raw)), now=now
    )


def _fetch_series_raw(ticker: str, series_read: SeriesReader) -> Mapping[str, Any]:
    payload = series_read(ticker)
    series = payload.get("series")
    if not isinstance(series, dict) or series.get("ticker") != ticker:
        raise OpportunityError(f"series response for {ticker!r} did not contain the exact series")
    return series


def _default_series_read(ticker: str) -> dict[str, Any]:
    evidence = public_read.get(f"{public_read.BASE}/series/{ticker}")
    if evidence.get("classification") != "SUCCESS":
        raise OpportunityError(f"series acquisition failed for {ticker!r}")
    payload = evidence.get("payload")
    if not isinstance(payload, dict):
        raise OpportunityError(f"series response for {ticker!r} is not an object")
    return payload


@dataclass(frozen=True, slots=True)
class _LegEvidence:
    market_ticker: str
    economics: Any
    specification: ContractSpecification


class _ConfirmationBlocked(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _acquire_leg(
    ticker: str,
    event: Event,
    series_raw: Mapping[str, Any],
    *,
    market: Market,
    fee_regime: Any,
    fee_policy: Any,
    series_fee_observation_id: str,
    event_fee_hash: str,
    requested_quantity: Decimal,
    economics_observed_at: datetime,
    market_read: MarketReader,
    orderbook_acquirer: Callable[..., Any],
    now: datetime,
) -> _LegEvidence:
    specification = _build_specification(market, event, series_raw, now=economics_observed_at)
    if (
        specification.semantic_status is not SemanticStatus.VALID
        or not specification.strategy_supported
    ):
        raise _ConfirmationBlocked(
            f"contract specification not strategy-supported for {ticker}: "
            f"status={specification.semantic_status.value}"
        )
    snapshot = acquire_market_snapshot(ticker, transport=market_read)
    if not snapshot.succeeded:
        raise _ConfirmationBlocked(
            f"market snapshot acquisition failed for {ticker}: {snapshot.classification}"
        )
    orderbook = orderbook_acquirer(ticker)
    if not orderbook.succeeded:
        raise _ConfirmationBlocked(
            f"orderbook snapshot acquisition failed for {ticker}: {orderbook.classification}"
        )
    validation = validate_orderbook_snapshot(orderbook.to_json(), expected_ticker=ticker, now=now)
    if not validation.succeeded:
        raise _ConfirmationBlocked(
            f"orderbook snapshot did not independently re-validate for {ticker}: "
            f"{validation.classification}"
        )
    try:
        ladder = PriceLadder.parse(
            market.raw.get("price_level_structure"), market.raw.get("price_ranges")
        )
    except UniverseValidationError as exc:
        raise _ConfirmationBlocked(f"unsupported price ladder for {ticker}: {exc}") from None
    try:
        economics, _binding = build_authoritative_market_economics(
            snapshot_payload=snapshot.to_json(),
            expected_market_ticker=ticker,
            expected_event_ticker=event.ticker,
            series_ticker=event.series_ticker or "",
            market_source_id=snapshot.body_sha256 or "",
            raw_orderbook=orderbook.raw_orderbook_for_economics(),
            ladder=ladder,
            orderbook_source_id=orderbook.orderbook_identity or "",
            orderbook_observed_at=orderbook.observed_at,
            series_fee_observation_id=series_fee_observation_id,
            resolved_fee_regime_id=fee_regime.regime_id,
            event_fee_hash=event_fee_hash,
            fee_policy=fee_policy,
            fee_regime=fee_regime,
            requested_quantity=requested_quantity,
            economics_observed_at=economics_observed_at,
        )
    except OpportunityError as exc:
        raise _ConfirmationBlocked(
            f"authoritative economics construction failed for {ticker}: {exc}"
        ) from None
    return _LegEvidence(ticker, economics, specification)


def attempt_exact_confirmation(
    lead: StructuralLead,
    *,
    relationship_id_value: str,
    scan_run_id: str,
    repo: MemoryUniverseRepository,
    requested_quantity: Decimal = Decimal(1),
    market_read: MarketReader = public_read.get_market_with_body,
    series_read: SeriesReader = _default_series_read,
    orderbook_acquirer: Callable[..., Any] = acquire_orderbook_snapshot,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> LeadObservation:
    """Attempt canonical exact confirmation for one discovered lead.

    Never weakens the confirmation requirement: any failure to acquire, validate, or resolve the
    evidence this needs produces a ``DISCOVERY_ONLY`` observation with an explicit
    ``blocker_reason``, never a fabricated or partial confirmation.
    """
    now = clock()
    event = repo.events.get(lead.event_ticker)
    broad_market = repo.markets.get(lead.broad_market_ticker)
    narrow_market = repo.markets.get(lead.narrow_market_ticker)
    if event is None or broad_market is None or narrow_market is None:
        return record_discovery_only(
            lead,
            relationship_id_value=relationship_id_value,
            scan_run_id=scan_run_id,
            observed_at=now,
            blocker_reason="event or market no longer present in the refreshed universe",
        )
    series_ticker = event.series_ticker
    if not series_ticker:
        return record_discovery_only(
            lead,
            relationship_id_value=relationship_id_value,
            scan_run_id=scan_run_id,
            observed_at=now,
            blocker_reason="event carries no series identity",
        )
    try:
        series_raw = _fetch_series_raw(series_ticker, series_read)
        series_observation = CurrentSeriesFeeObservation.parse(dict(series_raw), observed_at=now)
        event_override = EventFeeOverride.parse(event.raw)
        fee_regime = resolve_current_fee_regime(series_observation, event_override)
        fee_policy = current_event_formula_policy(
            fee_type=fee_regime.fee_type, fee_multiplier=fee_regime.fee_multiplier
        )
    except OpportunityError as exc:
        return record_discovery_only(
            lead,
            relationship_id_value=relationship_id_value,
            scan_run_id=scan_run_id,
            observed_at=now,
            blocker_reason=f"fee regime unresolved: {exc}",
        )

    try:
        broad = _acquire_leg(
            lead.broad_market_ticker,
            event,
            series_raw,
            market=broad_market,
            fee_regime=fee_regime,
            fee_policy=fee_policy,
            series_fee_observation_id=series_observation.observation_id,
            event_fee_hash=event_override.metadata_hash,
            requested_quantity=requested_quantity,
            economics_observed_at=now,
            market_read=market_read,
            orderbook_acquirer=orderbook_acquirer,
            now=now,
        )
        narrow = _acquire_leg(
            lead.narrow_market_ticker,
            event,
            series_raw,
            market=narrow_market,
            fee_regime=fee_regime,
            fee_policy=fee_policy,
            series_fee_observation_id=series_observation.observation_id,
            event_fee_hash=event_override.metadata_hash,
            requested_quantity=requested_quantity,
            economics_observed_at=now,
            market_read=market_read,
            orderbook_acquirer=orderbook_acquirer,
            now=now,
        )
    except _ConfirmationBlocked as exc:
        return record_discovery_only(
            lead,
            relationship_id_value=relationship_id_value,
            scan_run_id=scan_run_id,
            observed_at=now,
            blocker_reason=exc.reason,
        )

    try:
        confirmation = confirm_structural_lead(
            lead,
            broad.economics,
            narrow.economics,
            broad_specification=broad.specification,
            narrow_specification=narrow.specification,
        )
    except OpportunityError as exc:
        return record_discovery_only(
            lead,
            relationship_id_value=relationship_id_value,
            scan_run_id=scan_run_id,
            observed_at=now,
            blocker_reason=f"canonical exact confirmation rejected: {exc}",
        )
    return record_exact_confirmation(
        lead,
        confirmation,
        relationship_id_value=relationship_id_value,
        scan_run_id=scan_run_id,
        observed_at=now,
    )


def new_scan_run_id() -> str:
    return str(uuid4())


def sleep_between_scans(seconds: float, *, sleeper: Callable[[float], None] = time.sleep) -> None:
    """Isolated so tests can inject a non-blocking sleeper; never itself decides cadence policy."""
    if seconds < 0:
        raise OpportunityError("cadence seconds must be non-negative")
    sleeper(seconds)


@dataclass(frozen=True, slots=True)
class ScanCycleResult:
    scan_run_id: str
    independent_cohorts_observed: int
    discovery_leads: int
    observations: tuple[LeadObservation, ...]
    refresh_complete: bool = True


def _new_persistence_episode(relationship: str, scan_run_id: str) -> str:
    return stable_hash(("m27b2-persistence-episode-v1", relationship, scan_run_id))


def run_scan_cycle(
    *,
    archive_path: str,
    store: StructuralMeasurementStore,
    source_authority: str,
    universe_transport: object | None = None,
    requested_quantity: Decimal = Decimal(1),
    market_read: MarketReader = public_read.get_market_with_body,
    series_read: SeriesReader = _default_series_read,
    orderbook_acquirer: Callable[..., Any] = acquire_orderbook_snapshot,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ScanCycleResult:
    """One full read-only scan/measure/persist cycle. Never places an order, never authenticates,
    never mutates the canonical M27B discovery/confirmation implementation.

    Note on current scope (see ``docs/reviews/M27B2_CONTINUOUS_STRUCTURAL_MEASUREMENT.md``): a
    relationship whose fee regime cannot be resolved, or whose fresh confirmation evidence is
    stale, is currently recorded ``DISCOVERY_ONLY`` with a descriptive blocker rather than the
    more specific ``FEE_UNKNOWN``/``STALE`` states -- both constructors exist and are fully
    tested, but auto-triggering them from live acquisition is the smallest next increment, not
    yet wired here. Likewise, a relationship missing from the current scan is always recorded
    ``DISAPPEARED``; distinguishing a genuinely ambiguous cohort (``record_ambiguous``) is the
    same kind of already-built, not-yet-wired next increment.
    """
    scan_run_id = new_scan_run_id()
    refresh = refresh_universe(archive_path, transport=universe_transport, clock=clock)
    if not refresh.complete:
        # A successful prefix is not evidence that previously observed relationships disappeared.
        return ScanCycleResult(scan_run_id, 0, 0, (), refresh_complete=False)
    scan = run_discovery(refresh.repo, source_authority=source_authority)
    now = clock()

    current_by_relationship: dict[str, StructuralLead] = {
        relationship_id(lead): lead for lead in scan.leads
    }

    observations: list[LeadObservation] = []
    observed_relationships: set[str] = set()
    for rel_id, lead in current_by_relationship.items():
        previous_rows = store.for_relationship(rel_id)
        observation_relationship_id = rel_id
        if previous_rows and previous_rows[-1].state is MeasurementState.DISAPPEARED:
            observation_relationship_id = _new_persistence_episode(rel_id, scan_run_id)
        observation = attempt_exact_confirmation(
            lead,
            relationship_id_value=observation_relationship_id,
            scan_run_id=scan_run_id,
            repo=refresh.repo,
            requested_quantity=requested_quantity,
            market_read=market_read,
            series_read=series_read,
            orderbook_acquirer=orderbook_acquirer,
            clock=clock,
        )
        store.append(observation)
        observations.append(observation)
        observed_relationships.add(observation_relationship_id)

    ambiguous_routes = {
        route
        for route in scan.routes
        if any(
            reason.value in ("DUPLICATE_THRESHOLD", "MIXED_CUSTOM_STRIKE_PRESENCE")
            for reason in route.reasons
        )
    }
    known_relationships = set(store.relationship_ids())
    for rel_id in known_relationships - observed_relationships:
        previous_rows = store.for_relationship(rel_id)
        if not previous_rows:
            continue  # pragma: no cover - relationship_ids() only returns rows that exist
        previous = previous_rows[-1]
        if previous.state.value in ("DISAPPEARED",):
            continue  # a closed lifetime is never re-closed
        matching_ambiguous_routes = [
            route
            for route in ambiguous_routes
            if route.event_ticker == previous.event_ticker
            and route.market_ticker in {previous.broad_market_ticker, previous.narrow_market_ticker}
        ]
        if len({route.market_ticker for route in matching_ambiguous_routes}) >= 2:
            observation = record_ambiguous(
                previous,
                scan_run_id=scan_run_id,
                observed_at=now,
                blocker_reason="canonical structural cohort became ambiguous",
            )
        else:
            observation = record_disappeared(previous, scan_run_id=scan_run_id, observed_at=now)
        store.append(observation)
        observations.append(observation)

    return ScanCycleResult(
        scan_run_id, scan.manifest.structural_cohorts, len(scan.leads), tuple(observations)
    )


def run_forever(
    *,
    archive_path: str,
    store: StructuralMeasurementStore,
    source_authority: str,
    cadence_seconds: float = DEFAULT_CADENCE_SECONDS,
    max_iterations: int | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    **cycle_kwargs: Any,
) -> Iterator[ScanCycleResult]:
    """Repeated unattended read-only measurement. ``cadence_seconds`` is a conservative,
    configurable operational default (see ``DEFAULT_CADENCE_SECONDS``) -- it never encodes or
    implies any claim about lead persistence or profitability."""
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        yield run_scan_cycle(
            archive_path=archive_path,
            store=store,
            source_authority=source_authority,
            **cycle_kwargs,
        )
        iterations += 1
        if max_iterations is None or iterations < max_iterations:
            sleep_between_scans(cadence_seconds, sleeper=sleeper)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="M27B.2 repeated read-only structural-lead measurement (research only)"
    )
    parser.add_argument("--archive", required=True, type=Path, help="local universe archive path")
    parser.add_argument(
        "--evidence-db", required=True, type=Path, help="local append-only observation database"
    )
    parser.add_argument(
        "--live-public-read",
        action="store_true",
        help="explicitly permit unauthenticated public Kalshi GET requests",
    )
    parser.add_argument("--cadence-seconds", type=float, default=DEFAULT_CADENCE_SECONDS)
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="stop after this many scans instead of running unattended forever",
    )
    parser.add_argument("--source-authority", default="external-api.kalshi.com")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.live_public_read:
        print("Structural measurement: NOT STARTED")
        print("Reason: explicit --live-public-read permission is required")
        return 2
    store = StructuralMeasurementStore(args.evidence_db)
    print("Starting M27B.2 structural-lead measurement (research only, production_influence=0)")
    for result in run_forever(
        archive_path=str(args.archive),
        store=store,
        source_authority=args.source_authority,
        cadence_seconds=args.cadence_seconds,
        max_iterations=args.max_iterations,
    ):
        print(
            f"scan {result.scan_run_id}: {result.independent_cohorts_observed} cohorts, "
            f"{result.discovery_leads} leads, {len(result.observations)} observations recorded"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
