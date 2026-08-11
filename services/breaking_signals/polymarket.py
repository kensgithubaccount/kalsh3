"""Public-only Polymarket universe, WebSocket protocol and exact market events."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol

from services.market_universe.domain import UniverseValidationError, exact, parse_time

GAMMA = "https://gamma-api.polymarket.com"
DATA = "https://data-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
MARKET_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


class SyncState(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class StreamState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    HEALTHY = "HEALTHY"
    STALE = "STALE"
    BACKPRESSURED = "BACKPRESSURED"
    RECONNECTING = "RECONNECTING"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class PolymarketMarket:
    market_id: str
    condition_id: str
    question: str
    description: str | None
    event_id: str | None
    asset_ids: tuple[str, ...]
    outcomes: tuple[str, ...]
    end_time: datetime | None
    resolved: bool
    orderbook_enabled: bool
    tick_size: Decimal
    minimum_size: Decimal
    negative_risk: bool
    multi_outcome: bool
    updated_at: datetime | None
    raw: dict[str, Any]

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> PolymarketMarket:
        for key in (
            "id",
            "conditionId",
            "question",
            "tokens",
            "outcomes",
            "tickSize",
            "minOrderSize",
        ):
            if key not in raw:
                raise UniverseValidationError(f"missing Polymarket {key}")
        tokens = raw["tokens"]
        outcomes = raw["outcomes"]
        if not isinstance(tokens, list) or not isinstance(outcomes, list):
            raise UniverseValidationError("Polymarket assets malformed")
        return cls(
            str(raw["id"]),
            str(raw["conditionId"]),
            str(raw["question"]),
            raw.get("description"),
            None if raw.get("eventId") is None else str(raw["eventId"]),
            tuple(str(x) for x in tokens),
            tuple(str(x) for x in outcomes),
            parse_time(raw.get("endDate"), optional=True),
            bool(raw.get("resolved", False)),
            bool(raw.get("enableOrderBook", False)),
            exact(str(raw["tickSize"]), "tickSize"),
            exact(str(raw["minOrderSize"]), "minOrderSize"),
            bool(raw.get("negRisk", False)),
            len(outcomes) != 2,
            (bool(raw.get("updatedAt")) and parse_time(raw.get("updatedAt"), optional=True))
            or None,
            raw,
        )


class PublicTransport(Protocol):
    def get(self, url: str, *, timeout_seconds: float) -> dict[str, Any]: ...


@dataclass(slots=True)
class DiscoveryRun:
    state: SyncState = SyncState.FAILED
    pages: int = 0
    records: int = 0
    last_cursor: str | None = None
    failure: str | None = None


class PolymarketDiscovery:
    def __init__(self, transport: PublicTransport, timeout: float = 10) -> None:
        self.transport = transport
        self.timeout = timeout

    def markets(self) -> tuple[list[PolymarketMarket], DiscoveryRun]:
        cursor = ""
        seen = set()
        result: list[PolymarketMarket] = []
        run = DiscoveryRun()
        while True:
            try:
                payload = self.transport.get(
                    f"{GAMMA}/markets?active=true&closed=false&next_cursor={cursor}",
                    timeout_seconds=self.timeout,
                )
            except Exception as exc:
                run.failure = type(exc).__name__
                run.state = SyncState.PARTIAL if run.pages else SyncState.FAILED
                return result, run
            page = payload.get("data")
            run.pages += 1
            if not isinstance(page, list):
                run.failure = "malformed_page"
                run.state = SyncState.PARTIAL
                return result, run
            try:
                result.extend(
                    PolymarketMarket.parse(item) for item in page if isinstance(item, dict)
                )
            except UniverseValidationError:
                run.failure = "malformed_market"
                run.state = SyncState.PARTIAL
                return result, run
            run.records += len(page)
            next_cursor = payload.get("next_cursor")
            if next_cursor in (None, ""):
                run.state = SyncState.COMPLETE
                return result, run
            if not isinstance(next_cursor, str) or next_cursor in seen:
                run.failure = "cursor_loop"
                run.state = SyncState.PARTIAL
                return result, run
            seen.add(next_cursor)
            cursor = next_cursor
            run.last_cursor = cursor


@dataclass(frozen=True, slots=True)
class MarketEvent:
    event_type: str
    asset_id: str | None
    market_id: str | None
    best_bid: Decimal | None
    best_ask: Decimal | None
    price: Decimal | None
    size: Decimal | None
    tick_size: Decimal | None
    timestamp: datetime | None
    raw: dict[str, Any]

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> MarketEvent:
        kind = raw.get("event_type")
        if kind not in {
            "book",
            "price_change",
            "last_trade_price",
            "tick_size_change",
            "best_bid_ask",
            "new_market",
            "market_resolved",
        }:
            raise UniverseValidationError("unknown Polymarket market event")

        def optional(key: str) -> Decimal | None:
            return None if raw.get(key) is None else exact(str(raw[key]), key)

        return cls(
            kind,
            None if raw.get("asset_id") is None else str(raw["asset_id"]),
            None if raw.get("market") is None else str(raw["market"]),
            optional("best_bid"),
            optional("best_ask"),
            optional("price"),
            optional("size"),
            optional("new_tick_size"),
            parse_time(raw.get("timestamp"), optional=True),
            raw,
        )


@dataclass(slots=True)
class PolymarketStream:
    queue_limit: int = 10000
    state: StreamState = StreamState.DISCONNECTED
    assets: set[str] = field(default_factory=set)
    queue: list[dict[str, Any]] = field(default_factory=list)
    last_message: datetime | None = None
    reconnects: int = 0
    price_ladder_invalid: set[str] = field(default_factory=set)
    gaps: int = 0

    def subscribe(self, assets: set[str]) -> dict[str, Any]:
        self.assets |= assets
        return {
            "assets_ids": sorted(assets),
            "type": "market",
            "custom_feature_enabled": True,
            "operation": "subscribe",
        }

    def unsubscribe(self, assets: set[str]) -> dict[str, Any]:
        self.assets -= assets
        return {"assets_ids": sorted(assets), "type": "market", "operation": "unsubscribe"}

    def enqueue(self, raw: dict[str, Any], now: datetime) -> bool:
        self.last_message = now
        if len(self.queue) >= self.queue_limit:
            self.state = StreamState.BACKPRESSURED
            self.gaps += 1
            return False
        self.queue.append(raw)
        self.state = StreamState.HEALTHY
        return True

    def parse_next(self) -> MarketEvent:
        event = MarketEvent.parse(self.queue.pop(0))
        if event.event_type == "tick_size_change" and event.asset_id:
            self.price_ladder_invalid.add(event.asset_id)
        return event

    def stale(self, now: datetime, timeout: timedelta = timedelta(seconds=30)) -> bool:
        stale = self.last_message is None or now - self.last_message > timeout
        if stale:
            self.state = StreamState.STALE
        return stale

    def reconnect_delay(self) -> float:
        self.reconnects += 1
        self.state = StreamState.RECONNECTING
        return float(min(30, 0.5 * 2 ** min(self.reconnects, 6)))


@dataclass(frozen=True, slots=True)
class CommentEvent:
    event_type: str
    comment_id: str
    market_id: str | None
    author_id: str | None
    body: str | None
    observed_at: datetime

    @classmethod
    def parse(cls, raw: dict[str, Any], now: datetime) -> CommentEvent:
        kind = raw.get("event_type")
        if kind not in {
            "comment_created",
            "comment_removed",
            "reaction_created",
            "reaction_removed",
        }:
            raise UniverseValidationError("unknown Polymarket comment event")
        if not isinstance(raw.get("comment_id"), str):
            raise UniverseValidationError("comment identity missing")
        return cls(
            kind, raw["comment_id"], raw.get("market_id"), raw.get("user_id"), raw.get("body"), now
        )


@dataclass(slots=True)
class CommentStream:
    """RTDS comment discovery is unverified social input, never independent confirmation."""

    last_ping: datetime | None = None
    last_message: datetime | None = None

    def ping(self, now: datetime) -> str:
        self.last_ping = now
        return "PING"

    def heartbeat_due(self, now: datetime) -> bool:
        return self.last_ping is None or now - self.last_ping >= timedelta(seconds=5)
