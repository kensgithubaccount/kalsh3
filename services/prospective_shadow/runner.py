"""Bounded A0.2 composition around the canonical A0.1 shadow kernel."""

from __future__ import annotations

import gc
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from services.cycle_deadline import CycleDeadline, CycleDeadlineExceeded
from services.forecasting.daily_temperature import route_daily_temperature
from services.market_universe.event_snapshot import (
    AuthoritativeEventSnapshot,
    acquire_event_snapshot,
)
from services.market_universe.market_snapshot import acquire_market_snapshot
from services.market_universe.orderbook_snapshot import acquire_orderbook_snapshot
from services.opportunity_engine.structural_measurement_runner import (
    refresh_universe,
    run_discovery,
)
from services.prospective_shadow.kernel import (
    ShadowObservationStore,
    capture_structural_observation,
    capture_weather_observation,
    hydrate_market_authority,
    validate_start_receipt,
)
from services.real_time_market_data.orderbook import BookState, BookView, SequencedBook

DEFAULT_CADENCE_SECONDS = 900
MAX_CANDIDATE_EVENTS = 200
MAX_STRUCTURAL_LEADS = 500
MAX_MARKET_HYDRATIONS = 1000
MAX_CYCLE_SECONDS = 300.0
_PUBLIC_SOURCE = "external-api.kalshi.com"


@dataclass(frozen=True, slots=True)
class WeatherAcquisitionResult:
    evidence: Mapping[str, Any] | None
    operational_failure: bool = False
    reason: str | None = None


@dataclass(slots=True)
class CycleDiagnostics:
    cycle_started_at: str = ""
    cycle_deadline_seconds: float = MAX_CYCLE_SECONDS
    markets_discovered: int = 0
    events_discovered: int = 0
    candidate_events: int = 0
    candidate_leads: int = 0
    exact_market_attempted: int = 0
    exact_market_succeeded: int = 0
    exact_market_failed: int = 0
    exact_event_attempted: int = 0
    exact_event_succeeded: int = 0
    exact_event_failed: int = 0
    books_attempted: int = 0
    structural_cohorts: int = 0
    structural_leads: int = 0
    structural_observe: int = 0
    structural_abstain: int = 0
    weather_operational_failures: int = 0
    cycle_duration_seconds: float = 0.0
    operational_failures: list[str] = field(default_factory=list)
    discovery_elapsed_seconds: float = 0.0
    timeout_stage: str | None = None
    market_pages: int = 0
    event_pages: int = 0
    exact_reconciliation_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["operational_failures"] = list(self.operational_failures)
        return payload


@dataclass(frozen=True, slots=True)
class CycleResult:
    cycle_id: str
    weather: tuple[dict[str, Any], ...]
    structural: tuple[dict[str, Any], ...]
    complete: bool
    diagnostics: dict[str, Any]

    @property
    def observations(self) -> tuple[dict[str, Any], ...]:
        return self.weather + self.structural


def _empty_book(ticker: str, now: datetime) -> BookView:
    return SequencedBook(ticker).view(now)


def _book(
    ticker: str,
    now: datetime,
    diagnostics: CycleDiagnostics,
    deadline: CycleDeadline | None = None,
) -> BookView:
    diagnostics.books_attempted += 1
    if deadline is not None:
        timeout = deadline.timeout_seconds(10, "CYCLE_DEADLINE_BOOK_ACQUISITION")
        snapshot = acquire_orderbook_snapshot(ticker, clock=lambda: now, timeout_seconds=timeout)
    else:
        snapshot = acquire_orderbook_snapshot(ticker, clock=lambda: now)
    if not snapshot.succeeded:
        return _empty_book(ticker, now)
    book = SequencedBook(ticker)
    book.snapshot(
        0,
        [list(level) for level in snapshot.yes_levels],
        [list(level) for level in snapshot.no_levels],
        snapshot.observed_at,
        snapshot.orderbook_identity or "",
        ingested_at=snapshot.observed_at,
    )
    return book.view(now)


def _event_snapshot(
    event_ticker: str,
    now: datetime,
    cache: dict[str, AuthoritativeEventSnapshot | None],
    diagnostics: CycleDiagnostics,
    deadline: CycleDeadline | None = None,
) -> AuthoritativeEventSnapshot | None:
    if event_ticker in cache:
        return cache[event_ticker]
    diagnostics.exact_event_attempted += 1
    if deadline is not None:
        timeout = deadline.timeout_seconds(10, "CYCLE_DEADLINE_EVENT_HYDRATION")
        snapshot = acquire_event_snapshot(event_ticker, clock=lambda: now, timeout_seconds=timeout)
    else:
        snapshot = acquire_event_snapshot(event_ticker, clock=lambda: now)
    cache[event_ticker] = snapshot if snapshot.succeeded else None
    if snapshot.succeeded:
        diagnostics.exact_event_succeeded += 1
    else:
        diagnostics.exact_event_failed += 1
    return cache[event_ticker]


def _authority(
    market: Any,
    event_snapshot: AuthoritativeEventSnapshot | None,
    now: datetime,
    diagnostics: CycleDiagnostics,
    deadline: CycleDeadline | None = None,
) -> Any:
    diagnostics.exact_market_attempted += 1
    if deadline is not None:
        timeout = deadline.timeout_seconds(10, "CYCLE_DEADLINE_MARKET_HYDRATION")
        snapshot = acquire_market_snapshot(
            market.ticker, clock=lambda: now, timeout_seconds=timeout
        )
    else:
        snapshot = acquire_market_snapshot(market.ticker, clock=lambda: now)
    if not snapshot.succeeded or event_snapshot is None:
        diagnostics.exact_market_failed += 1
        return None
    diagnostics.exact_market_succeeded += 1
    return hydrate_market_authority(
        {"ticker": market.ticker, "event_ticker": market.event_ticker},
        market_snapshot=snapshot,
        event_snapshot=event_snapshot,
    )


def _record_failure(diagnostics: CycleDiagnostics, reason: str) -> None:
    diagnostics.operational_failures.append(reason)


def _weather_cycle(
    *,
    repo: Any,
    store: ShadowObservationStore,
    start_receipt: Mapping[str, Any],
    acquired_at: datetime,
    weather_acquirer: Callable[[date], WeatherAcquisitionResult] | None,
    clock: Callable[[], datetime],
    diagnostics: CycleDiagnostics,
    deadline: CycleDeadline | None = None,
) -> list[dict[str, Any]]:
    if weather_acquirer is None:
        diagnostics.weather_operational_failures += 1
        _record_failure(diagnostics, "WEATHER_ACQUISITION_RUNTIME_DEPENDENCY_MISSING")
        return []
    forecast_cache: dict[date, WeatherAcquisitionResult] = {}
    event_cache: dict[str, AuthoritativeEventSnapshot | None] = {}
    rows: list[dict[str, Any]] = []
    for market in repo.markets.values():
        if deadline is not None:
            deadline.check("CYCLE_DEADLINE_WEATHER_ADAPTER")
        event = repo.events.get(market.event_ticker)
        if event is None:
            continue
        route = route_daily_temperature(market, event)
        if route.contract is None:
            continue
        target_date = route.contract.local_date
        if target_date not in forecast_cache:
            try:
                forecast_cache[target_date] = weather_acquirer(target_date)
                if deadline is not None:
                    deadline.check("CYCLE_DEADLINE_WEATHER_ADAPTER")
            except Exception as exc:
                forecast_cache[target_date] = WeatherAcquisitionResult(
                    None, True, f"weather acquisition failed: {type(exc).__name__}"
                )
        result = forecast_cache[target_date]
        if result.operational_failure:
            diagnostics.weather_operational_failures += 1
            _record_failure(diagnostics, result.reason or "WEATHER_ACQUISITION_FAILURE")
            return []
        now = clock()
        event_snapshot = _event_snapshot(event.ticker, now, event_cache, diagnostics, deadline)
        authority = _authority(market, event_snapshot, now, diagnostics, deadline)
        row = capture_weather_observation(
            market=authority.market if authority is not None else market,
            event=authority.event if authority is not None else event,
            forecast=result.evidence or {},
            book=_book(market.ticker, now, diagnostics, deadline),
            acquired_at=acquired_at,
            decision_at=now,
            authority=authority,
            start_receipt_digest=str(start_receipt["receipt_digest"]),
        )
        rows.append(row)
    return rows


def _structural_cycle(
    *,
    repo: Any,
    scan: Any,
    store: ShadowObservationStore,
    start_receipt: Mapping[str, Any],
    acquired_at: datetime,
    clock: Callable[[], datetime],
    diagnostics: CycleDiagnostics,
    deadline: CycleDeadline | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    leads = tuple(scan.leads)
    diagnostics.candidate_leads = len(leads)
    diagnostics.candidate_events = len({lead.event_ticker for lead in leads})
    diagnostics.structural_cohorts = scan.manifest.structural_cohorts
    diagnostics.structural_leads = len(leads)
    if len(leads) > MAX_STRUCTURAL_LEADS or diagnostics.candidate_events > MAX_CANDIDATE_EVENTS:
        _record_failure(diagnostics, "STRUCTURAL_CANDIDATE_BOUND_EXCEEDED")
        return [], False

    event_cache: dict[str, AuthoritativeEventSnapshot | None] = {}
    rows: list[dict[str, Any]] = []
    for lead in leads:
        if deadline is not None:
            deadline.check("CYCLE_DEADLINE_STRUCTURAL_SCAN")
        if diagnostics.exact_market_attempted + 2 > MAX_MARKET_HYDRATIONS:
            _record_failure(diagnostics, "STRUCTURAL_MARKET_HYDRATION_BOUND_EXCEEDED")
            return rows, False
        now = clock()
        event_snapshot = _event_snapshot(lead.event_ticker, now, event_cache, diagnostics, deadline)
        broad = repo.markets.get(lead.broad_market_ticker)
        narrow = repo.markets.get(lead.narrow_market_ticker)
        broad_authority = (
            None if broad is None else _authority(broad, event_snapshot, now, diagnostics, deadline)
        )
        narrow_authority = (
            None
            if narrow is None
            else _authority(narrow, event_snapshot, now, diagnostics, deadline)
        )
        if event_snapshot is None or broad_authority is None or narrow_authority is None:
            _record_failure(diagnostics, "STRUCTURAL_EXACT_AUTHORITY_UNAVAILABLE")
            return rows, False
        authorities = {
            lead.broad_market_ticker: broad_authority,
            lead.narrow_market_ticker: narrow_authority,
        }
        broad_book = _book(lead.broad_market_ticker, now, diagnostics, deadline)
        narrow_book = _book(lead.narrow_market_ticker, now, diagnostics, deadline)
        if broad_book.state is not BookState.CURRENT or narrow_book.state is not BookState.CURRENT:
            _record_failure(diagnostics, "STRUCTURAL_ORDERBOOK_ACQUISITION_FAILURE")
            return rows, False
        row = capture_structural_observation(
            lead=lead,
            broad_book=broad_book,
            narrow_book=narrow_book,
            acquired_at=acquired_at,
            decision_at=now,
            leg_authority=authorities,
            start_receipt_digest=str(start_receipt["receipt_digest"]),
        )
        rows.append(row)
    return rows, True


def run_once(
    *,
    archive: str | Path,
    store: ShadowObservationStore,
    start_receipt: Mapping[str, Any],
    cycle_id: str,
    weather_acquirer: Callable[[date], WeatherAcquisitionResult] | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    monotonic: Callable[[], float] = time.monotonic,
) -> CycleResult:
    validate_start_receipt(start_receipt)
    deadline = CycleDeadline(MAX_CYCLE_SECONDS, monotonic=monotonic)
    acquired_at = clock()
    diagnostics = CycleDiagnostics()
    diagnostics.cycle_started_at = acquired_at.isoformat()
    try:
        refresh = refresh_universe(
            str(archive), compressed_evidence=True, clock=clock, deadline=deadline
        )
    except CycleDeadlineExceeded as exc:
        diagnostics.timeout_stage = exc.stage
        _record_failure(diagnostics, exc.stage)
        diagnostics.discovery_elapsed_seconds = round(deadline.elapsed_seconds)
        diagnostics.cycle_duration_seconds = diagnostics.discovery_elapsed_seconds
        return CycleResult(cycle_id, (), (), False, diagnostics.as_dict())
    except Exception as exc:
        _record_failure(diagnostics, f"UNIVERSE_DISCOVERY_FAILURE:{type(exc).__name__}")
        diagnostics.cycle_duration_seconds = deadline.elapsed_seconds
        return CycleResult(cycle_id, (), (), False, diagnostics.as_dict())
    gc.collect()
    diagnostics.markets_discovered = len(refresh.repo.markets)
    diagnostics.events_discovered = len(refresh.repo.events)
    diagnostics.market_pages = getattr(refresh, "market_pages", 0)
    diagnostics.event_pages = getattr(refresh, "event_pages", 0)
    diagnostics.exact_reconciliation_count = getattr(refresh, "exact_reconciliation_count", 0)
    diagnostics.discovery_elapsed_seconds = round(deadline.elapsed_seconds)
    if not refresh.complete:
        reason = refresh.failure or "UNIVERSE_DISCOVERY_INCOMPLETE"
        diagnostics.timeout_stage = reason if reason.startswith("CYCLE_DEADLINE_") else None
        _record_failure(diagnostics, reason)
        diagnostics.cycle_duration_seconds = deadline.elapsed_seconds
        return CycleResult(cycle_id, (), (), False, diagnostics.as_dict())
    try:
        deadline.check("CYCLE_DEADLINE_STRUCTURAL_SCAN")
    except CycleDeadlineExceeded as exc:
        diagnostics.timeout_stage = exc.stage
        _record_failure(diagnostics, exc.stage)
        diagnostics.cycle_duration_seconds = deadline.elapsed_seconds
        return CycleResult(cycle_id, (), (), False, diagnostics.as_dict())

    try:
        scan = run_discovery(
            refresh.repo,
            source_authority=_PUBLIC_SOURCE,
            deadline_check=lambda: deadline.check("CYCLE_DEADLINE_STRUCTURAL_SCAN"),
        )
        deadline.check("CYCLE_DEADLINE_STRUCTURAL_SCAN")
        structural_rows, structural_complete = _structural_cycle(
            repo=refresh.repo,
            scan=scan,
            store=store,
            start_receipt=start_receipt,
            acquired_at=acquired_at,
            clock=clock,
            diagnostics=diagnostics,
            deadline=deadline,
        )
        weather_rows = _weather_cycle(
            repo=refresh.repo,
            store=store,
            start_receipt=start_receipt,
            acquired_at=acquired_at,
            weather_acquirer=weather_acquirer,
            clock=clock,
            diagnostics=diagnostics,
            deadline=deadline,
        )
        deadline.check("CYCLE_DEADLINE_FINAL_PERSISTENCE")
    except CycleDeadlineExceeded as exc:
        diagnostics.timeout_stage = exc.stage
        _record_failure(diagnostics, exc.stage)
        diagnostics.cycle_duration_seconds = deadline.elapsed_seconds
        return CycleResult(cycle_id, (), (), False, diagnostics.as_dict())
    diagnostics.structural_observe = sum(row["decision"] == "OBSERVE" for row in structural_rows)
    diagnostics.structural_abstain = sum(row["decision"] == "ABSTAIN" for row in structural_rows)
    if structural_complete:
        for row in structural_rows:
            store.append(row, now=clock())
        for row in weather_rows:
            store.append(row, now=clock())
    diagnostics.cycle_duration_seconds = deadline.elapsed_seconds
    return CycleResult(
        cycle_id,
        tuple(weather_rows),
        tuple(structural_rows),
        structural_complete,
        diagnostics.as_dict(),
    )


def run_forever(*, interval_seconds: float = DEFAULT_CADENCE_SECONDS, **kwargs: Any) -> None:
    if interval_seconds < 0:
        raise ValueError("interval_seconds must be non-negative")
    index = 0
    while True:
        result = run_once(cycle_id=f"cycle-{index}", **kwargs)
        if not result.complete:
            raise RuntimeError("A0.2 cycle was incomplete; continuous collection stopped")
        index += 1
        time.sleep(interval_seconds)


def summarize(cycles: list[CycleResult]) -> dict[str, Any]:
    rows = [row for cycle in cycles for row in cycle.observations]
    return {
        adapter: {
            "observe": sum(
                row["decision"] == "OBSERVE" for row in rows if row["strategy_family"] == adapter
            ),
            "abstain": sum(
                row["decision"] == "ABSTAIN" for row in rows if row["strategy_family"] == adapter
            ),
            "reasons": dict(
                Counter(
                    row["reason_code"]
                    for row in rows
                    if row["strategy_family"] == adapter and row["reason_code"]
                )
            ),
        }
        for adapter in ("daily_weather", "structural_threshold")
    }
