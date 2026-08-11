"""Separate baseline and overlapped incremental universe synchronization."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol, cast
from uuid import uuid4

from .domain import Event, Market, Series, UniverseValidationError, parse_time


class Completeness(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


@dataclass(slots=True)
class SyncRun:
    run_id: str
    endpoint: str
    mode: str
    started_at: datetime
    finished_at: datetime | None = None
    pages: int = 0
    records_received: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    malformed: int = 0
    last_cursor: str | None = None
    completeness: Completeness = Completeness.FAILED
    failure: str | None = None
    previous_watermark: datetime | None = None
    requested_watermark: datetime | None = None
    confirmed_watermark: datetime | None = None


class PublicTransport(Protocol):
    def get(self, path: str, *, timeout_seconds: float) -> dict[str, Any]: ...


@dataclass
class MemoryUniverseRepository:
    series: dict[str, Series] = field(default_factory=dict)
    events: dict[str, Event] = field(default_factory=dict)
    markets: dict[str, Market] = field(default_factory=dict)
    metadata_versions: dict[str, list[str]] = field(default_factory=dict)
    rules_versions: dict[str, list[str]] = field(default_factory=dict)
    runs: list[SyncRun] = field(default_factory=list)
    watermarks: dict[str, datetime] = field(default_factory=dict)
    cutoff: HistoricalCutoff | None = None

    def upsert(self, entity: Series | Event | Market) -> str:
        ticker = entity.ticker
        old_metadata: Series | Event | Market | None
        if isinstance(entity, Series):
            old_metadata = self.series.get(ticker)
            self.series[ticker] = entity
        elif isinstance(entity, Event):
            old_metadata = self.events.get(ticker)
            self.events[ticker] = entity
        else:
            old_metadata = self.markets.get(ticker)
            self.markets[ticker] = entity
        old_hash = None if old_metadata is None else old_metadata.metadata_hash
        versions = self.metadata_versions.setdefault(ticker, [])
        if entity.metadata_hash not in versions:
            versions.append(entity.metadata_hash)
        if isinstance(entity, Market):
            rules = self.rules_versions.setdefault(ticker, [])
            if entity.rules_hash not in rules:
                rules.append(entity.rules_hash)
        if old_metadata is None:
            return "inserted"
        if old_hash != entity.metadata_hash:
            return "updated"
        if (
            isinstance(entity, Market)
            and isinstance(old_metadata, Market)
            and old_metadata.rules_hash != entity.rules_hash
        ):
            return "updated"
        return "unchanged"


@dataclass(frozen=True, slots=True)
class HistoricalCutoff:
    market_settled_ts: datetime
    trades_created_ts: datetime
    orders_updated_ts: datetime
    observed_at: datetime

    @classmethod
    def parse(cls, payload: dict[str, Any], now: datetime) -> HistoricalCutoff:
        values = [
            parse_time(payload.get(k))
            for k in ("market_settled_ts", "trades_created_ts", "orders_updated_ts")
        ]
        if any(x is None for x in values):
            raise UniverseValidationError("cutoff incomplete")
        return cls(values[0], values[1], values[2], now)  # type: ignore[arg-type]


class UniverseSynchronizer:
    def __init__(
        self,
        transport: PublicTransport,
        repo: MemoryUniverseRepository,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        timeout: float = 15,
    ) -> None:
        self.transport = transport
        self.repo = repo
        self.clock = clock
        self.timeout = timeout

    def _pages(
        self, endpoint: str, field: str, run: SyncRun, params: str = ""
    ) -> list[dict[str, Any]]:
        cursor = None
        seen = set()
        records = []
        while True:
            target = (
                f"/trade-api/v2/{endpoint}?{params}" if params else f"/trade-api/v2/{endpoint}?"
            )
            if cursor:
                target += ("&" if params else "") + f"cursor={cursor}"
            try:
                payload = self.transport.get(target, timeout_seconds=self.timeout)
            except Exception as exc:
                run.failure = type(exc).__name__
                run.completeness = Completeness.PARTIAL if run.pages else Completeness.FAILED
                raise
            page = payload.get(field)
            run.pages += 1
            if not isinstance(page, list):
                run.failure = "malformed_page"
                run.completeness = Completeness.PARTIAL
                raise UniverseValidationError("page collection malformed")
            records.extend(page)
            run.records_received += len(page)
            next_cursor = payload.get("cursor")
            if next_cursor in (None, ""):
                return records
            if not isinstance(next_cursor, str) or next_cursor in seen:
                run.failure = "invalid_cursor"
                run.completeness = Completeness.PARTIAL
                raise UniverseValidationError("cursor invalid or repeated")
            seen.add(next_cursor)
            cursor = next_cursor
            run.last_cursor = cursor

    def sync(
        self, kind: str, *, incremental: bool = False, overlap: timedelta = timedelta(seconds=60)
    ) -> SyncRun:
        parsers = {
            "series": (Series.parse, "series"),
            "events": (Event.parse, "events"),
            "markets": (Market.parse, "markets"),
        }
        parser, field = parsers[kind]
        now = self.clock()
        run = SyncRun(str(uuid4()), kind, "incremental" if incremental else "baseline", now)
        self.repo.runs.append(run)
        params = ""
        if incremental:
            previous = self.repo.watermarks.get(kind)
            run.previous_watermark = previous
            requested = (previous - overlap) if previous else datetime.fromtimestamp(0, UTC)
            run.requested_watermark = requested
            params = f"min_updated_ts={int(requested.timestamp())}" + (
                "&mve_filter=exclude" if kind == "markets" else ""
            )
        try:
            records = self._pages(kind, field, run, params)
            maximum = run.previous_watermark
            for raw in records:
                try:
                    if not isinstance(raw, dict):
                        raise UniverseValidationError("record not object")
                    entity = cast(Series | Event | Market, parser(raw))
                    result = self.repo.upsert(entity)
                    setattr(run, result, getattr(run, result) + 1)
                    updated = entity.source_updated_at
                    if updated is not None and (maximum is None or updated > maximum):
                        maximum = updated
                except UniverseValidationError:
                    run.malformed += 1
            run.completeness = Completeness.PARTIAL if run.malformed else Completeness.COMPLETE
            if incremental and run.completeness == Completeness.COMPLETE and maximum is not None:
                self.repo.watermarks[kind] = maximum
                run.confirmed_watermark = maximum
        except Exception as exc:
            if run.failure is None:
                run.failure = type(exc).__name__
        run.finished_at = self.clock()
        return run

    def sync_historical_cutoff(self) -> HistoricalCutoff:
        result = HistoricalCutoff.parse(
            self.transport.get("/trade-api/v2/historical/cutoff", timeout_seconds=self.timeout),
            self.clock(),
        )
        self.repo.cutoff = result
        return result

    def fetch_orderbooks(self, tickers: list[str]) -> list[dict[str, Any]]:
        output = []
        for start in range(0, len(tickers), 100):
            batch = tickers[start : start + 100]
            payload = self.transport.get(
                "/trade-api/v2/markets/orderbooks?tickers=" + ",".join(batch),
                timeout_seconds=self.timeout,
            )
            books = payload.get("orderbooks")
            if not isinstance(books, list) or len(books) > 100:
                raise UniverseValidationError("orderbook batch malformed")
            output.extend(books)
        return output
