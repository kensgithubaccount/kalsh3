"""Completely offline M25A read-only evidence collection runtime."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from uuid import UUID

from services.real_time_market_data.events import BookDeltaEvent, BookSnapshotEvent
from services.real_time_market_data.manager import SubscriptionManager
from services.real_time_market_data.orderbook import BookState, PriceMode
from services.real_time_market_data.protocol import ProtocolError, Subscription
from services.real_time_market_data.transport import ReceivedFrame

from .book_evidence import BookEvidenceObservation
from .domain import ShadowResearchError
from .pipeline import ReadOnlyBookEvidencePipeline
from .store import BookEvidenceStore


class RuntimeState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    SUBSCRIBING = "SUBSCRIBING"
    HEALTHY = "HEALTHY"
    QUARANTINED = "QUARANTINED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"


@dataclass(frozen=True, slots=True)
class MarketSpec:
    ticker: str
    exchange_index: int
    structure_hash: str

    def __post_init__(self) -> None:
        if (
            not self.ticker.strip()
            or type(self.exchange_index) is not int
            or self.exchange_index < 0
        ):
            raise ShadowResearchError("market spec identity is missing or invalid")
        if not self.structure_hash.strip():
            raise ShadowResearchError("market structure_hash is required")


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    state: RuntimeState
    epoch: UUID | None
    subscribed_ticker_count: int
    last_raw_frame_received_at: datetime | None
    last_accepted_snapshot: datetime | None
    last_accepted_delta: datetime | None
    rows_written_this_run: int
    replay_count: int
    collision_count: int
    gap_count: int
    stale_count: int
    malformed_count: int
    reconnect_count: int
    resnapshot_count: int
    persistence_failure_count: int
    processing_lag_ns: int | None


class ScriptedTransport:
    """Offline-only deterministic transport; it has no URL or networking capability."""

    def __init__(self, frames: Iterable[ReceivedFrame] = ()) -> None:
        self._frames = iter(frames)
        self.sent: list[Mapping[str, Any]] = []
        self.connected = False

    def connect(self, *, succeeds: bool = True) -> bool:
        self.connected = succeeds
        return succeeds

    def send(self, command: Mapping[str, Any]) -> None:
        if not self.connected:
            raise RuntimeError("scripted transport is disconnected")
        self.sent.append(MappingProxyType(dict(command)))

    def receive(self) -> ReceivedFrame:
        if not self.connected:
            raise RuntimeError("scripted transport is disconnected")
        return next(self._frames)

    def close(self) -> None:
        self.connected = False


class OfflineReadOnlyEvidenceRuntime:
    """Single-consumer composition from raw scripted frames to SQLite evidence."""

    def __init__(
        self,
        market_specs: Iterable[MarketSpec],
        store: BookEvidenceStore,
        transport: ScriptedTransport,
        utc_clock: Callable[[], datetime],
        monotonic_ns: Callable[[], int],
        *,
        max_depth_markets: int = 250,
        enabled: bool = False,
    ) -> None:
        specs = tuple(market_specs)
        if not specs:
            raise ShadowResearchError("at least one market spec is required")
        tickers = [spec.ticker for spec in specs]
        if len(set(tickers)) != len(tickers):
            raise ShadowResearchError("duplicate market ticker")
        if len(specs) > max_depth_markets:
            raise ShadowResearchError("market specs exceed max_depth_markets")
        self._specs = MappingProxyType({spec.ticker: spec for spec in specs})
        self._manager = SubscriptionManager(max_depth_markets=max_depth_markets)
        self._store = store
        self._transport = transport
        self._clock = utc_clock
        self._monotonic_ns = monotonic_ns
        self._enabled = enabled
        self._pipeline = ReadOnlyBookEvidencePipeline(
            self._manager,
            store,
            MappingProxyType({spec.ticker: spec.exchange_index for spec in specs}),
            utc_clock,
        )
        self._state = RuntimeState.DISCONNECTED
        self._connection_epoch: UUID | None = None
        self._orderbook_sid: int | None = None
        self._recovery_pending: dict[int, set[str]] = {}
        self._last_raw: datetime | None = None
        self._last_snapshot: datetime | None = None
        self._last_delta: datetime | None = None
        self._rows = self._replays = self._collisions = self._gaps = 0
        self._stale = self._malformed = self._reconnects = self._resnapshots = 0
        self._persistence_failures = 0
        self._lag_ns: int | None = None

    @property
    def manager(self) -> SubscriptionManager:
        return self._manager

    @property
    def price_mode(self) -> PriceMode:
        return PriceMode.UNIFIED_YES

    def connect(self, *, succeeds: bool = True) -> UUID | None:
        if not self._enabled:
            self._state = RuntimeState.STOPPED
            return None
        self._state = RuntimeState.CONNECTING
        if not self._transport.connect(succeeds=succeeds):
            self._state = RuntimeState.FAILED
            return None
        epoch = self._manager.new_epoch()
        self._connection_epoch = epoch
        self._orderbook_sid = None
        self._recovery_pending.clear()
        self._manager.set_depth_watch(set(self._specs))
        self._state = RuntimeState.SUBSCRIBING
        for command in self._manager.depth_commands(None):
            self._transport.send(command)
        return epoch

    def disconnect(self) -> None:
        if self._connection_epoch is not None:
            self._manager.disconnected()
            self._reconnects += 1
        self._transport.close()
        self._connection_epoch = None
        self._orderbook_sid = None
        self._recovery_pending.clear()
        self._state = RuntimeState.DISCONNECTED

    def receive_one(self) -> BookEvidenceObservation | None:
        return self.process(self._transport.receive(), connection_epoch=self._connection_epoch)

    def process(
        self, frame: ReceivedFrame, *, connection_epoch: UUID | None
    ) -> BookEvidenceObservation | None:
        if self._state in {RuntimeState.QUARANTINED, RuntimeState.FAILED, RuntimeState.STOPPED}:
            return None
        if connection_epoch is None or connection_epoch != self._connection_epoch:
            return None
        self._last_raw = frame.received_at
        now_ns = self._monotonic_ns()
        self._lag_ns = max(0, now_ns - frame.received_monotonic_ns)
        try:
            raw = json.loads(
                frame.raw,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"invalid JSON constant: {value}")
                ),
            )
            if not isinstance(raw, dict) or not isinstance(raw.get("type"), str):
                raise ValueError("frame must decode to an object with a type")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
            self._malformed += 1
            self._state = RuntimeState.QUARANTINED
            return None
        kind = raw["type"]
        try:
            if kind in {"subscribed", "ok", "error"}:
                subscription = (
                    self._manager.protocol.response(raw) if self._manager.protocol else None
                )
                if kind == "error":
                    raise ProtocolError("subscription command rejected")
                if isinstance(subscription, Subscription):
                    if subscription.market_tickers != tuple(sorted(self._specs)):
                        raise ProtocolError("subscription ticker set mismatch")
                    self._orderbook_sid = subscription.sid
                    self._manager.sid_markets[subscription.sid] = set(self._specs)
                    self._manager.subscribed_depth = set(self._specs)
                    self._state = RuntimeState.HEALTHY
                return None
            if kind not in {"orderbook_snapshot", "orderbook_delta"}:
                raise ProtocolError("unknown unapproved event type")
            return self._process_book(raw, frame)
        except ProtocolError:
            self._malformed += 1
            self._state = RuntimeState.QUARANTINED
            return None

    def _process_book(
        self, raw: dict[str, Any], frame: ReceivedFrame
    ) -> BookEvidenceObservation | None:
        try:
            event: BookSnapshotEvent | BookDeltaEvent
            if raw["type"] == "orderbook_snapshot":
                event = BookSnapshotEvent.parse(raw, self.price_mode)
            else:
                event = BookDeltaEvent.parse(raw)
            if event.ticker not in self._specs or event.sid != self._orderbook_sid:
                raise ShadowResearchError("unexpected ticker or subscription")
            before = self._store.count()
            if isinstance(event, BookSnapshotEvent):
                result = self._pipeline.snapshot(
                    event,
                    received_at=frame.received_at,
                    structure_hash=self._specs[event.ticker].structure_hash,
                )
                if result is None:
                    self._stale += 1
                    self._request_snapshot_once(event.sid)
                    self._state = RuntimeState.SUBSCRIBING
                    return None
                book = self._manager.books.get(event.ticker)
                if book is None or book.view(result.available_at).state is not BookState.CURRENT:
                    self._gaps += 1
                    self._request_snapshot_once(event.sid)
                    self._state = RuntimeState.SUBSCRIBING
                    return None
                remaining = self._recovery_pending.get(event.sid)
                if remaining is not None:
                    remaining.discard(event.ticker)
                    if not remaining:
                        del self._recovery_pending[event.sid]
                self._last_snapshot = frame.received_at
            else:
                book = self._manager.books.get(event.ticker)
                if book is not None and book.view(frame.received_at).state is BookState.STALE:
                    self._stale += 1
                    self._state = RuntimeState.QUARANTINED
                    return None
                result = self._pipeline.delta(event, received_at=frame.received_at)
                if result is None:
                    self._gaps += 1
                    self._request_snapshot_once(event.sid)
                    self._state = RuntimeState.SUBSCRIBING
                    return None
                self._last_delta = frame.received_at
            after = self._store.count()
            if after > before:
                self._rows += after - before
            elif result is not None:
                self._replays += 1
            self._state = (
                RuntimeState.SUBSCRIBING if self._recovery_pending else RuntimeState.HEALTHY
            )
            return result
        except ShadowResearchError as exc:
            message = str(exc)
            if "collision" in message:
                self._collisions += 1
            elif "persistence" in message or "database" in message:
                self._persistence_failures += 1
            else:
                self._malformed += 1
            self._state = RuntimeState.QUARANTINED
            return None
        except (KeyError, TypeError, ValueError):
            self._malformed += 1
            self._state = RuntimeState.QUARANTINED
            return None

    def _request_snapshot_once(self, sid: int) -> None:
        if sid in self._recovery_pending:
            return
        self._mark_recovery_required(sid)
        self._transport.send(self._manager.recovery_command(sid))
        self._resnapshots += 1

    def _mark_recovery_required(self, sid: int) -> None:
        self._recovery_pending.setdefault(
            sid,
            set(self._manager.sid_markets.get(sid, set())).intersection(self._specs),
        )

    def check_freshness(self) -> bool:
        now = self._clock().astimezone(UTC)
        stale = any(
            book.view(now).state is not BookState.CURRENT for book in self._manager.books.values()
        )
        if stale:
            self._stale += 1
            self._state = RuntimeState.QUARANTINED
        return not stale

    def stop(self) -> None:
        self._transport.close()
        self._state = RuntimeState.STOPPED

    def health(self) -> HealthSnapshot:
        return HealthSnapshot(
            self._state,
            self._connection_epoch,
            len(self._manager.subscribed_depth),
            self._last_raw,
            self._last_snapshot,
            self._last_delta,
            self._rows,
            self._replays,
            self._collisions,
            self._gaps,
            self._stale,
            self._malformed,
            self._reconnects,
            self._resnapshots,
            self._persistence_failures,
            self._lag_ns,
        )
