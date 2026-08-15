"""Separate baseline and overlapped incremental universe synchronization."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol, cast
from urllib.parse import quote, unquote, urlencode
from uuid import uuid4

from .archive import (
    EntityKind,
    UniverseObservationArchive,
    _acquisition_writer_for_synchronizer,
)
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
    requests: int = 0
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


@dataclass(frozen=True, slots=True)
class SyncProgress:
    """Cursor-free descriptive progress safe for operator presentation."""

    resource: str
    pages: int
    records_received: int


class PublicTransport(Protocol):
    def get(self, path: str, *, timeout_seconds: float) -> dict[str, Any]: ...


def _contains_control_character(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


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
        archive: UniverseObservationArchive | None = None,
        provider: str = "kalshi-public-api",
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        run_id_factory: Callable[[], str] = lambda: str(uuid4()),
        timeout: float = 15,
        max_pages: int | None = None,
        progress: Callable[[SyncProgress], None] | None = None,
    ) -> None:
        if max_pages is not None:
            if isinstance(max_pages, bool) or not isinstance(max_pages, int):
                raise TypeError("max_pages must be None or a positive int")
            if max_pages < 1:
                raise ValueError("max_pages must be positive")
        self.transport = transport
        self.repo = repo
        self.archive = archive
        self.__archive_writer = (
            None if archive is None else _acquisition_writer_for_synchronizer(archive)
        )
        self.provider = provider
        self.clock = clock
        self.run_id_factory = run_id_factory
        self.timeout = timeout
        self.max_pages = max_pages
        self.progress = progress

    def _pages(
        self,
        endpoint: str,
        field: str,
        run: SyncRun,
        parameters: Mapping[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        cursor = None
        seen = set()
        records = []
        fixed_parameters = dict(parameters or {})
        while True:
            request_parameters = dict(fixed_parameters)
            if cursor is not None:
                request_parameters["cursor"] = cursor
            target = f"/trade-api/v2/{endpoint}?{urlencode(request_parameters)}"
            try:
                payload = self.transport.get(target, timeout_seconds=self.timeout)
            except Exception as exc:
                run.failure = type(exc).__name__
                run.completeness = Completeness.PARTIAL if run.pages else Completeness.FAILED
                raise
            page = payload.get(field)
            run.pages += 1
            next_cursor = payload.get("cursor")
            if self.__archive_writer is not None:
                self.__archive_writer.append_page(
                    provider=self.provider,
                    endpoint=endpoint,
                    parameters=fixed_parameters,
                    acquired_at=self.clock(),
                    page_number=run.pages,
                    cursor_in=cursor,
                    cursor_out=next_cursor if isinstance(next_cursor, str) else None,
                    run_id=run.run_id,
                    kind=EntityKind(endpoint[:-1] if endpoint.endswith("s") else endpoint),
                    payload=payload,
                    succeeded=isinstance(page, list),
                    failure=None if isinstance(page, list) else "malformed_page",
                )
            if not isinstance(page, list):
                run.failure = "malformed_page"
                run.completeness = Completeness.PARTIAL
                raise UniverseValidationError("page collection malformed")
            records.extend(page)
            run.records_received += len(page)
            if self.progress is not None:
                self.progress(SyncProgress(endpoint, run.pages, run.records_received))
            if next_cursor in (None, ""):
                return records
            if self.max_pages is not None and run.pages >= self.max_pages:
                run.failure = "bounded_truncation"
                run.completeness = Completeness.PARTIAL
                raise UniverseValidationError("page collection exceeded safety bound")
            if (
                not isinstance(next_cursor, str)
                or next_cursor in seen
                or _contains_control_character(next_cursor)
                or _contains_control_character(unquote(next_cursor))
            ):
                run.failure = "invalid_cursor"
                run.completeness = Completeness.PARTIAL
                raise UniverseValidationError("cursor invalid or repeated")
            seen.add(next_cursor)
            cursor = next_cursor
            run.last_cursor = cursor

    def sync(
        self,
        kind: str,
        *,
        incremental: bool = False,
        overlap: timedelta = timedelta(seconds=60),
        parameters: Mapping[str, str] | None = None,
    ) -> SyncRun:
        parsers = {
            "series": (Series.parse, "series"),
            "events": (Event.parse, "events"),
            "markets": (Market.parse, "markets"),
        }
        parser, field = parsers[kind]
        now = self.clock()
        run = SyncRun(
            self.run_id_factory(), kind, "incremental" if incremental else "baseline", now
        )
        self.repo.runs.append(run)
        request_parameters = dict(parameters or {})
        if incremental:
            previous = self.repo.watermarks.get(kind)
            run.previous_watermark = previous
            requested = (previous - overlap) if previous else datetime.fromtimestamp(0, UTC)
            run.requested_watermark = requested
            request_parameters["min_updated_ts"] = str(int(requested.timestamp()))
            if kind == "markets":
                request_parameters["mve_filter"] = "exclude"
        try:
            records = self._pages(kind, field, run, request_parameters)
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
        if self.__archive_writer is not None:
            self.__archive_writer.record_run_result(
                run_id=run.run_id,
                completeness=run.completeness.value,
                pages=run.pages,
                records_received=run.records_received,
                malformed=run.malformed,
                failure=run.failure,
                finished_at=run.finished_at,
            )
        return run

    def reconcile_events(self, tickers: tuple[str, ...]) -> SyncRun:
        """Acquire exact Event parents in deterministic order through this authority."""
        now = self.clock()
        run = SyncRun(self.run_id_factory(), "events/reconciliation", "exact", now)
        self.repo.runs.append(run)
        failed = False
        for ticker in tickers:
            try:
                if not ticker or not all(
                    character.isascii() and (character.isalnum() or character in "-_.")
                    for character in ticker
                ):
                    raise UniverseValidationError("event ticker is not a canonical target")
                encoded = quote(ticker, safe="")
                endpoint = f"events/{encoded}"
                target = f"/trade-api/v2/{endpoint}"
                run.requests += 1
                payload = self.transport.get(target, timeout_seconds=self.timeout)
                raw = payload.get("event")
                valid = False
                entity: Event | None = None
                if isinstance(raw, dict):
                    try:
                        entity = Event.parse(raw)
                        valid = True
                    except UniverseValidationError:
                        pass
                if entity is not None and entity.ticker != ticker:
                    valid = False
                run.pages += 1
                run.records_received += int(isinstance(raw, dict))
                if self.__archive_writer is not None:
                    self.__archive_writer.append_page(
                        provider=self.provider,
                        endpoint=endpoint,
                        parameters={},
                        acquired_at=self.clock(),
                        page_number=run.pages,
                        cursor_in=None,
                        cursor_out=None,
                        run_id=run.run_id,
                        kind=EntityKind.EVENT,
                        payload=payload,
                        succeeded=valid,
                        failure=None if valid else "invalid_exact_event",
                    )
                if not valid or entity is None:
                    run.malformed += 1
                    failed = True
                    continue
                result = self.repo.upsert(entity)
                setattr(run, result, getattr(run, result) + 1)
            except Exception as exc:
                failed = True
                if run.failure is None:
                    run.failure = type(exc).__name__
        run.completeness = Completeness.PARTIAL if failed else Completeness.COMPLETE
        if failed and run.failure is None:
            run.failure = "invalid_exact_event"
        run.finished_at = self.clock()
        if self.__archive_writer is not None:
            self.__archive_writer.record_run_result(
                run_id=run.run_id,
                completeness=run.completeness.value,
                pages=run.pages,
                records_received=run.records_received,
                malformed=run.malformed,
                failure=run.failure,
                finished_at=run.finished_at,
            )
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
