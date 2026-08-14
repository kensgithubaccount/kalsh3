"""Completely offline scripted runtime for Perps evidence collection."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid4

from services.real_time_market_data.transport import ReceivedFrame

from .domain import ShadowResearchError
from .margin_protocol import MarginChannel, MarginProtocolState
from .perps_events import PerpsBookDeltaEvent, PerpsBookSnapshotEvent, PerpsTickerEvent
from .perps_evidence import (
    PerpsBookEvidenceObservation,
    PerpsMarketStateObservation,
    perps_book_fingerprint,
    perps_ticker_fingerprint,
)
from .perps_metadata import PerpsMarketMetadata
from .perps_orderbook import PerpsBookState, PerpsSequencedBook
from .perps_store import PerpsEvidenceStore


class PerpsRuntimeState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    SUBSCRIBING = "SUBSCRIBING"
    HEALTHY = "HEALTHY"
    RECONNECT_REQUIRED = "RECONNECT_REQUIRED"
    QUARANTINED = "QUARANTINED"
    STOPPED = "STOPPED"


@dataclass(frozen=True, slots=True)
class PerpsHealthSnapshot:
    state: PerpsRuntimeState
    epoch: UUID | None
    orderbook_sid: int | None
    ticker_sid: int | None
    last_raw_frame_received_at: datetime | None
    last_snapshot_at: datetime | None
    last_delta_at: datetime | None
    last_ticker_at: datetime | None
    rows_written: int
    replay_count: int
    collision_count: int
    gap_count: int
    crossed_count: int
    stale_count: int
    malformed_count: int
    persistence_failure_count: int
    reconnect_count: int
    processing_lag_ns: int | None
    accepted_snapshot_count: int
    accepted_delta_count: int
    accepted_ticker_count: int


class ScriptedPerpsTransport:
    """In-memory deterministic transport with deliberately no networking surface."""

    def __init__(self, frames: Iterable[ReceivedFrame] = ()) -> None:
        self._frames = iter(frames)
        self.sent: list[Mapping[str, Any]] = []
        self.connected = False

    def connect(self) -> None:
        self.connected = True

    def send(self, command: Mapping[str, Any]) -> None:
        if not self.connected:
            raise ShadowResearchError("scripted Perps transport is disconnected")
        self.sent.append(MappingProxyType(dict(command)))

    def receive(self) -> ReceivedFrame:
        if not self.connected:
            raise ShadowResearchError("scripted Perps transport is disconnected")
        return next(self._frames)

    def close(self) -> None:
        self.connected = False


class OfflinePerpsEvidenceRuntime:
    def __init__(
        self,
        market: PerpsMarketMetadata,
        store: PerpsEvidenceStore,
        transport: ScriptedPerpsTransport,
        utc_clock: Callable[[], datetime],
        monotonic_ns: Callable[[], int],
        *,
        enabled: bool = False,
    ) -> None:
        self.market = market
        self.store = store
        self.transport = transport
        self.clock = utc_clock
        self.monotonic_ns = monotonic_ns
        self.enabled = enabled
        self.book = PerpsSequencedBook(market)
        self.protocol: MarginProtocolState | None = None
        self.epoch: UUID | None = None
        self.orderbook_sid: int | None = None
        self.ticker_sid: int | None = None
        self.state = PerpsRuntimeState.DISCONNECTED
        self._last_raw: datetime | None = None
        self._last_snapshot: datetime | None = None
        self._last_delta: datetime | None = None
        self._last_ticker: datetime | None = None
        self._rows = self._replays = self._collisions = self._gaps = self._malformed = 0
        self._crossed = self._stale = 0
        self._persistence_failures = self._reconnects = 0
        self._accepted_snapshots = self._accepted_deltas = self._accepted_tickers = 0
        self._lag_ns: int | None = None
        self.store.append_metadata(market)

    def connect(self) -> UUID | None:
        if not self.enabled:
            self.state = PerpsRuntimeState.STOPPED
            return None
        self.transport.connect()
        self.epoch = uuid4()
        self.protocol = MarginProtocolState(self.epoch)
        self.orderbook_sid = self.ticker_sid = None
        self.book.mark_stale()
        self.state = PerpsRuntimeState.SUBSCRIBING
        tickers = (self.market.ticker,)
        self.transport.send(self.protocol.subscribe(MarginChannel.ORDERBOOK, tickers))
        self.transport.send(self.protocol.subscribe(MarginChannel.TICKER, tickers))
        return self.epoch

    def bind_live_connection(self, epoch: UUID, protocol: MarginProtocolState) -> None:
        """Bind an externally-owned async connection without creating another epoch."""
        if not self.enabled or epoch.int == 0 or protocol.epoch != epoch:
            raise ShadowResearchError("invalid live Perps connection binding")
        self.epoch = epoch
        self.protocol = protocol
        self.orderbook_sid = self.ticker_sid = None
        self.book.mark_stale()
        self.state = PerpsRuntimeState.SUBSCRIBING

    def invalidate_live_connection(self, epoch: UUID) -> None:
        """Invalidate exactly the closing connection; old frames then fail epoch checks."""
        if self.epoch != epoch:
            return
        self._reconnects += 1
        self.book.mark_stale()
        self.epoch = None
        self.protocol = None
        self.orderbook_sid = self.ticker_sid = None
        self.state = PerpsRuntimeState.DISCONNECTED

    def disconnect(self) -> None:
        if self.epoch is not None:
            self._reconnects += 1
        self.book.mark_stale()
        self.transport.close()
        self.epoch = None
        self.protocol = None
        self.orderbook_sid = self.ticker_sid = None
        self.state = PerpsRuntimeState.DISCONNECTED

    def process(
        self, frame: ReceivedFrame, *, connection_epoch: UUID | None
    ) -> PerpsBookEvidenceObservation | PerpsMarketStateObservation | None:
        if (
            self.state
            in {
                PerpsRuntimeState.QUARANTINED,
                PerpsRuntimeState.RECONNECT_REQUIRED,
                PerpsRuntimeState.STOPPED,
            }
            or connection_epoch is None
            or connection_epoch != self.epoch
        ):
            return None
        self._last_raw = frame.received_at
        self._lag_ns = max(0, self.monotonic_ns() - frame.received_monotonic_ns)
        try:
            raw = json.loads(
                frame.raw,
                parse_float=Decimal,
                parse_int=int,
                parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
            )
            if not isinstance(raw, dict) or not isinstance(raw.get("type"), str):
                raise ShadowResearchError("Perps frame must be an object with type")
            if raw["type"] == "subscribed":
                if self.protocol is None:
                    raise ShadowResearchError("missing margin protocol")
                subscription = self.protocol.subscribed(raw)
                if subscription.tickers != (self.market.ticker,):
                    raise ShadowResearchError("subscription ticker mismatch")
                if subscription.channel is MarginChannel.ORDERBOOK:
                    self.orderbook_sid = subscription.sid
                else:
                    self.ticker_sid = subscription.sid
                return None
            if raw["type"] in {"unsubscribed", "ok"}:
                if self.protocol is None:
                    raise ShadowResearchError("missing margin protocol")
                self.protocol.command_acknowledged(raw)
                return None
            if raw["type"] == "orderbook_snapshot":
                return self._snapshot(PerpsBookSnapshotEvent.parse(raw, self.market), frame)
            if raw["type"] == "orderbook_delta":
                return self._delta(PerpsBookDeltaEvent.parse(raw, self.market), frame)
            if raw["type"] == "ticker":
                return self._ticker(PerpsTickerEvent.parse(raw, self.market), frame)
            raise ShadowResearchError("unapproved Perps frame type")
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValueError,
            TypeError,
            ShadowResearchError,
        ) as exc:
            if isinstance(exc, ShadowResearchError) and "collision" in str(exc):
                self._collisions += 1
            elif isinstance(exc, ShadowResearchError) and "persistence" in str(exc):
                self._persistence_failures += 1
            else:
                self._malformed += 1
            self.state = PerpsRuntimeState.QUARANTINED
            return None

    def _snapshot(
        self, event: PerpsBookSnapshotEvent, frame: ReceivedFrame
    ) -> PerpsBookEvidenceObservation | None:
        self._book_sid(event.sid)
        replay = self._preflight(event)
        if replay:
            self._replays += 1
            return None
        self.book.snapshot(event, frame.received_at)
        return self._book_evidence(event, frame)

    def _delta(
        self, event: PerpsBookDeltaEvent, frame: ReceivedFrame
    ) -> PerpsBookEvidenceObservation | None:
        self._book_sid(event.sid)
        replay = self._preflight(event)
        if replay:
            self._replays += 1
            return None
        if self.book.state not in {
            PerpsBookState.CURRENT,
            PerpsBookState.CROSSED,
        } or not self.book.delta(event, frame.received_at):
            self._gaps += 1
            self.book.state = PerpsBookState.GAP
            self.state = PerpsRuntimeState.RECONNECT_REQUIRED
            return None
        return self._book_evidence(event, frame)

    def _preflight(self, event: PerpsBookSnapshotEvent | PerpsBookDeltaEvent) -> bool:
        if self.epoch is None:
            raise ShadowResearchError("missing Perps connection epoch")
        row = self.store.get_book_by_source(self.epoch, event.sid, event.sequence, event.ticker)
        if row is None:
            return False
        if row["source_event_fingerprint"] != perps_book_fingerprint(event):
            raise ShadowResearchError("Perps book source-event logical identity collision")
        return True

    def _book_evidence(
        self, event: PerpsBookSnapshotEvent | PerpsBookDeltaEvent, frame: ReceivedFrame
    ) -> PerpsBookEvidenceObservation | None:
        available = self.clock().astimezone(UTC)
        view = self.book.view(available)
        if not view.usable:
            if view.state is PerpsBookState.CROSSED:
                self._crossed += 1
                self.state = PerpsRuntimeState.RECONNECT_REQUIRED
            elif view.state is PerpsBookState.STALE:
                self._stale += 1
                self.book.mark_stale()
                self.state = PerpsRuntimeState.RECONNECT_REQUIRED
            else:
                # A source-valid empty snapshot is waiting for a usable level.
                self.state = PerpsRuntimeState.SUBSCRIBING
            return None
        if self.epoch is None:
            raise ShadowResearchError("missing Perps connection epoch")
        item = PerpsBookEvidenceObservation.create(
            event=event,
            market=self.market,
            epoch=self.epoch,
            view=view,
            received_at=frame.received_at,
            available_at=available,
        )
        if self.store.append_book(item):
            self._rows += 1
        if isinstance(event, PerpsBookSnapshotEvent):
            self._last_snapshot = frame.received_at
            self._accepted_snapshots += 1
        else:
            self._last_delta = frame.received_at
            self._accepted_deltas += 1
        if self.orderbook_sid is not None and self.ticker_sid is not None:
            self.state = PerpsRuntimeState.HEALTHY
        return item

    def _ticker(
        self, event: PerpsTickerEvent, frame: ReceivedFrame
    ) -> PerpsMarketStateObservation | None:
        if event.sid != self.ticker_sid:
            raise ShadowResearchError("ticker SID mismatch")
        if self.epoch is None:
            raise ShadowResearchError("missing Perps connection epoch")
        fingerprint = perps_ticker_fingerprint(event)
        if (
            self.store.get_market_state_by_source(
                self.epoch, event.sid, event.ticker, event.ts_ms, fingerprint
            )
            is not None
        ):
            # Replays are observed but are not newly accepted ticker evidence.
            self._replays += 1
            return None
        item = PerpsMarketStateObservation.create(
            event, self.market, self.epoch, frame.received_at, self.clock().astimezone(UTC)
        )
        if self.store.append_market_state(item):
            self._rows += 1
        self._last_ticker = frame.received_at
        self._accepted_tickers += 1
        return item

    def _book_sid(self, sid: int) -> None:
        if sid != self.orderbook_sid:
            raise ShadowResearchError("orderbook SID mismatch")

    def update_metadata(self, market: PerpsMarketMetadata) -> bool:
        self.store.append_metadata(market)
        changed = self.book.invalidate_for_metadata(market)
        self.market = market
        if changed:
            self.state = PerpsRuntimeState.RECONNECT_REQUIRED
        return changed

    def health(self) -> PerpsHealthSnapshot:
        return PerpsHealthSnapshot(
            self.state,
            self.epoch,
            self.orderbook_sid,
            self.ticker_sid,
            self._last_raw,
            self._last_snapshot,
            self._last_delta,
            self._last_ticker,
            self._rows,
            self._replays,
            self._collisions,
            self._gaps,
            self._crossed,
            self._stale,
            self._malformed,
            self._persistence_failures,
            self._reconnects,
            self._lag_ns,
            self._accepted_snapshots,
            self._accepted_deltas,
            self._accepted_tickers,
        )
