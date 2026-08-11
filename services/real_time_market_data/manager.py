"""Connection-epoch, SID sequencing, liveness, tiered subscriptions and recovery."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID, uuid4

from .events import BookDeltaEvent, BookSnapshotEvent
from .orderbook import BookState, SequencedBook
from .protocol import Channel, ProtocolState


class RealtimeState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    AUTHENTICATING = "AUTHENTICATING"
    SUBSCRIBING = "SUBSCRIBING"
    SNAPSHOTTING = "SNAPSHOTTING"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    GAP_DETECTED = "GAP_DETECTED"
    STALE = "STALE"
    BACKPRESSURED = "BACKPRESSURED"
    RECONNECTING = "RECONNECTING"
    FAILED = "FAILED"


class AsyncTransport(Protocol):
    async def connect(self, url: str, headers: dict[str, str]) -> None: ...
    async def send_json(self, message: dict[str, Any]) -> None: ...
    async def receive_json(self) -> dict[str, Any]: ...
    async def close(self) -> None: ...

    # Reviewed client handles Ping("heartbeat")/Pong control frames automatically.


@dataclass(slots=True)
class Gap:
    epoch: UUID
    sid: int
    expected: int
    actual: int
    markets: tuple[str, ...]
    started_at: datetime
    recovery_method: str | None = None
    recovered_at: datetime | None = None


@dataclass(slots=True)
class Liveness:
    last_frame_at: datetime | None = None
    last_application_at: datetime | None = None

    def frame(self, now: datetime, *, application: bool) -> None:
        self.last_frame_at = now
        self.last_application_at = now if application else self.last_application_at

    def stalled(self, now: datetime, timeout: timedelta = timedelta(seconds=30)) -> bool:
        return self.last_frame_at is None or now - self.last_frame_at > timeout


@dataclass(slots=True)
class SubscriptionManager:
    max_depth_markets: int = 250
    desired_depth: set[str] = field(default_factory=set)
    subscribed_depth: set[str] = field(default_factory=set)
    pending_add: set[str] = field(default_factory=set)
    pending_remove: set[str] = field(default_factory=set)
    queue_limit: int = 10000
    queue: deque[dict[str, Any]] = field(default_factory=deque)
    state: RealtimeState = RealtimeState.DISCONNECTED
    epoch: UUID | None = None
    protocol: ProtocolState | None = None
    last_seq: dict[int, int] = field(default_factory=dict)
    sid_markets: dict[int, set[str]] = field(default_factory=dict)
    books: dict[str, SequencedBook] = field(default_factory=dict)
    gaps: list[Gap] = field(default_factory=list)
    reconnect_count: int = 0
    liveness: Liveness = field(default_factory=Liveness)
    processing_lag_ms: int = 0

    def new_epoch(self) -> UUID:
        self.epoch = uuid4()
        self.protocol = ProtocolState(self.epoch)
        self.last_seq.clear()
        self.sid_markets.clear()
        self.subscribed_depth.clear()
        self.state = RealtimeState.SUBSCRIBING
        for book in self.books.values():
            book.state = BookState.STALE
        return self.epoch

    def global_commands(self) -> list[dict[str, Any]]:
        if self.protocol is None:
            raise RuntimeError("no connection epoch")
        return [
            self.protocol.subscribe(Channel.TICKER),
            self.protocol.subscribe(Channel.TRADE),
            self.protocol.subscribe(Channel.LIFECYCLE),
            self.protocol.subscribe(Channel.MVE_LIFECYCLE),
        ]

    def set_depth_watch(self, tickers: set[str]) -> None:
        bounded = set(sorted(tickers)[: self.max_depth_markets])
        self.desired_depth = bounded
        self.pending_add = bounded - self.subscribed_depth
        self.pending_remove = self.subscribed_depth - bounded

    def depth_commands(self, sid: int | None) -> list[dict[str, Any]]:
        if self.protocol is None:
            raise RuntimeError("no connection epoch")
        commands = []
        if sid is None and self.desired_depth:
            commands.append(
                self.protocol.subscribe(Channel.ORDERBOOK, tuple(sorted(self.desired_depth)))
            )
        elif sid is not None:
            if self.pending_add:
                commands.append(
                    self.protocol.update(sid, "add_markets", tuple(sorted(self.pending_add)))
                )
            if self.pending_remove:
                commands.append(
                    self.protocol.update(sid, "delete_markets", tuple(sorted(self.pending_remove)))
                )
        return commands

    def enqueue(self, raw: dict[str, Any], now: datetime) -> bool:
        self.liveness.frame(now, application=True)
        if len(self.queue) >= self.queue_limit:
            self.state = RealtimeState.BACKPRESSURED
            self._gap_from_raw(raw, now)
            return False
        self.queue.append(raw)
        return True

    def sequence(self, sid: int, seq: int, now: datetime) -> bool:
        previous = self.last_seq.get(sid)
        if previous is None:
            self.last_seq[sid] = seq
            return True
        if seq != previous + 1:
            self.state = RealtimeState.GAP_DETECTED
            markets = tuple(sorted(self.sid_markets.get(sid, set())))
            self.gaps.append(Gap(self.epoch or UUID(int=0), sid, previous + 1, seq, markets, now))
            for market in markets:
                if market in self.books:
                    self.books[market].state = BookState.GAP
            return False
        self.last_seq[sid] = seq
        return True

    def install_snapshot(
        self, event: BookSnapshotEvent, now: datetime, structure_hash: str
    ) -> None:
        if not self.sequence(event.sid, event.seq, now) and event.sid in self.last_seq:
            # A verified fresh snapshot intentionally resets this SID after a gap.
            self.last_seq[event.sid] = event.seq
        self.sid_markets.setdefault(event.sid, set()).add(event.ticker)
        book = self.books.setdefault(event.ticker, SequencedBook(event.ticker))
        book.snapshot(
            event.seq,
            [[str(p), str(s)] for p, s in event.yes],
            [[str(p), str(s)] for p, s in event.no],
            now,
            structure_hash,
            ingested_at=now,
            price_mode=event.mode,
        )
        self.subscribed_depth.add(event.ticker)
        for gap in reversed(self.gaps):
            if gap.sid == event.sid and gap.recovered_at is None:
                gap.recovery_method = "websocket_get_snapshot"
                gap.recovered_at = now
                break
        self.state = RealtimeState.HEALTHY

    def apply_delta(self, event: BookDeltaEvent, now: datetime) -> bool:
        if not self.sequence(event.sid, event.seq, now):
            return False
        book = self.books.get(event.ticker)
        if book is None or book.state != BookState.CURRENT:
            self.state = RealtimeState.GAP_DETECTED
            return False
        # SID sequencing is authoritative; book sequence mirrors the last applied SID sequence.
        book.sequence = event.seq - 1
        ok = book.delta(
            event.seq, event.side, str(event.price), str(event.delta), event.exchange_at
        )
        if not ok:
            self.state = RealtimeState.GAP_DETECTED
        return ok

    def recovery_command(self, sid: int) -> dict[str, Any]:
        if self.protocol is None:
            raise RuntimeError("no connection epoch")
        tickers = tuple(sorted(self.sid_markets.get(sid, set())))
        if not tickers:
            raise RuntimeError("affected subscription is unknown")
        return self.protocol.update(sid, "get_snapshot", tickers)

    def disconnected(self) -> float:
        self.state = RealtimeState.RECONNECTING
        self.reconnect_count += 1
        for book in self.books.values():
            book.state = BookState.STALE
        return float(min(30, 0.5 * (2 ** min(self.reconnect_count, 6))))

    def _gap_from_raw(self, raw: dict[str, Any], now: datetime) -> None:
        raw_sid, raw_seq = raw.get("sid"), raw.get("seq")
        sid = raw_sid if isinstance(raw_sid, int) else -1
        seq = raw_seq if isinstance(raw_seq, int) else -1
        expected = self.last_seq.get(sid, -1) + 1
        self.gaps.append(
            Gap(
                self.epoch or UUID(int=0),
                sid,
                expected,
                seq,
                tuple(sorted(self.sid_markets.get(sid, set()))),
                now,
            )
        )
