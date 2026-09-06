"""A0.2 read-only composition runner for the canonical A0.1 shadow kernel.

This module deliberately contains orchestration only.  Market/event authority, weather
authority, structural discovery, book validation, and append-only persistence remain in their
reviewed modules.  A failed acquisition becomes an explicit kernel abstention; no caller can
turn a missing forecast, market, event, or current book into an observation.
"""

from __future__ import annotations

import gc
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from services.forecasting.daily_temperature import route_daily_temperature
from services.market_universe.event_snapshot import acquire_event_snapshot
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
from services.real_time_market_data.orderbook import SequencedBook

DEFAULT_CADENCE_SECONDS = 900
_PUBLIC_SOURCE = "external-api.kalshi.com"


@dataclass(frozen=True, slots=True)
class CycleResult:
    cycle_id: str
    weather: tuple[dict[str, Any], ...]
    structural: tuple[dict[str, Any], ...]
    cohorts: int
    leads: int
    refresh_complete: bool

    @property
    def observations(self) -> tuple[dict[str, Any], ...]:
        return self.weather + self.structural


def _empty_book(ticker: str, now: datetime) -> Any:
    return SequencedBook(ticker).view(now)


def _book(ticker: str, now: datetime) -> Any:
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


def _authority(summary: Mapping[str, Any], now: datetime) -> Any:
    market = acquire_market_snapshot(str(summary["ticker"]), clock=lambda: now)
    event = acquire_event_snapshot(str(summary["event_ticker"]), clock=lambda: now)
    if not market.succeeded or not event.succeeded:
        return None
    return hydrate_market_authority(summary, market_snapshot=market, event_snapshot=event)


def run_once(
    *,
    archive: str | Path,
    store: ShadowObservationStore,
    start_receipt: Mapping[str, Any],
    cycle_id: str,
    weather_acquirer: Callable[[date], Mapping[str, Any] | None] | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> CycleResult:
    """Run one public-read cycle and append only validated kernel payloads."""
    validate_start_receipt(start_receipt)
    acquired_at = clock()
    refresh = refresh_universe(str(archive), compressed_evidence=True, clock=clock)
    if not refresh.complete:
        return CycleResult(cycle_id, (), (), 0, 0, False)
    # The canonical refresh uses short-lived SQLite connections.  Explicitly finalize any
    # deferred connection objects before opening the independent A0.1 store, avoiding a
    # platform-specific descriptor/WAL race after a large live universe refresh.
    gc.collect()

    weather_rows: list[dict[str, Any]] = []
    forecast_cache: dict[date, Mapping[str, Any] | None] = {}
    for market in refresh.repo.markets.values():
        event = refresh.repo.events.get(market.event_ticker)
        if event is None:
            continue
        route = route_daily_temperature(market, event)
        if route.contract is None:
            continue
        now = clock()
        authority = _authority({"ticker": market.ticker, "event_ticker": event.ticker}, now)
        target_date = route.contract.local_date
        if target_date not in forecast_cache:
            forecast_cache[target_date] = (
                None if weather_acquirer is None else weather_acquirer(target_date)
            )
        forecast = forecast_cache[target_date]
        row = capture_weather_observation(
            market=authority.market if authority is not None else market,
            event=authority.event if authority is not None else event,
            forecast=forecast or {},
            book=_book(market.ticker, now),
            acquired_at=acquired_at,
            decision_at=now,
            authority=authority,
            start_receipt_digest=str(start_receipt["receipt_digest"]),
        )
        store.append(row, now=now)
        weather_rows.append(row)

    scan = run_discovery(refresh.repo, source_authority=_PUBLIC_SOURCE)
    structural_rows: list[dict[str, Any]] = []
    for lead in scan.leads:
        now = clock()
        broad_summary = {"ticker": lead.broad_market_ticker, "event_ticker": lead.event_ticker}
        narrow_summary = {"ticker": lead.narrow_market_ticker, "event_ticker": lead.event_ticker}
        broad_authority = _authority(broad_summary, now)
        narrow_authority = _authority(narrow_summary, now)
        authorities = None
        if broad_authority is not None and narrow_authority is not None:
            authorities = {
                lead.broad_market_ticker: broad_authority,
                lead.narrow_market_ticker: narrow_authority,
            }
        row = capture_structural_observation(
            lead=lead,
            broad_book=_book(lead.broad_market_ticker, now),
            narrow_book=_book(lead.narrow_market_ticker, now),
            acquired_at=acquired_at,
            decision_at=now,
            leg_authority=authorities,
            start_receipt_digest=str(start_receipt["receipt_digest"]),
        )
        store.append(row, now=now)
        structural_rows.append(row)
    return CycleResult(
        cycle_id,
        tuple(weather_rows),
        tuple(structural_rows),
        scan.manifest.structural_cohorts,
        len(scan.leads),
        True,
    )


def run_forever(*, interval_seconds: float = DEFAULT_CADENCE_SECONDS, **kwargs: Any) -> None:
    if interval_seconds < 0:
        raise ValueError("interval_seconds must be non-negative")
    index = 0
    while True:
        run_once(cycle_id=f"cycle-{index}", **kwargs)
        index += 1
        time.sleep(interval_seconds)


def summarize(cycles: list[CycleResult]) -> dict[str, Any]:
    rows = [row for cycle in cycles for row in cycle.observations]
    by_adapter: dict[str, dict[str, Any]] = {}
    for adapter in ("daily_weather", "structural_threshold"):
        selected = [row for row in rows if row["strategy_family"] == adapter]
        by_adapter[adapter] = {
            "observe": sum(row["decision"] == "OBSERVE" for row in selected),
            "abstain": sum(row["decision"] == "ABSTAIN" for row in selected),
            "reasons": dict(Counter(row["reason_code"] for row in selected if row["reason_code"])),
        }
    return by_adapter
