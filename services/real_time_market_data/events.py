"""Strict fixed-point parsers for current ticker, trade, book, and lifecycle messages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from services.market_universe.domain import UniverseValidationError, exact

from .orderbook import PriceMode


def event_time(ts_ms: Any) -> datetime:
    if not isinstance(ts_ms, int) or ts_ms < 0:
        raise UniverseValidationError("ts_ms missing or invalid")
    return datetime.fromtimestamp(ts_ms / 1000, UTC)


def envelope(raw: Any, expected: str) -> tuple[int, int, dict[str, Any]]:
    if (
        not isinstance(raw, dict)
        or raw.get("type") != expected
        or not isinstance(raw.get("sid"), int)
        or not isinstance(raw.get("seq"), int)
        or not isinstance(raw.get("msg"), dict)
    ):
        raise UniverseValidationError(f"malformed {expected}")
    return raw["sid"], raw["seq"], raw["msg"]


@dataclass(frozen=True, slots=True)
class EventContext:
    epoch: UUID
    received_at: datetime
    received_monotonic_ns: int
    persisted_at: datetime


@dataclass(frozen=True, slots=True)
class BookSnapshotEvent:
    sid: int
    seq: int
    ticker: str
    market_id: str
    yes: tuple[tuple[Decimal, Decimal], ...]
    no: tuple[tuple[Decimal, Decimal], ...]
    mode: PriceMode
    raw: dict[str, Any]

    @classmethod
    def parse(cls, raw: dict[str, Any], mode: PriceMode) -> BookSnapshotEvent:
        sid, seq, msg = envelope(raw, "orderbook_snapshot")

        def levels(name: str) -> tuple[tuple[Decimal, Decimal], ...]:
            value = msg.get(name)
            if not isinstance(value, list):
                raise UniverseValidationError("book side missing")
            output = []
            for level in value:
                if not isinstance(level, list) or len(level) != 2:
                    raise UniverseValidationError("book level malformed")
                output.append((exact(level[0], "price"), exact(level[1], "size")))
            return tuple(output)

        if not isinstance(msg.get("market_ticker"), str) or not isinstance(
            msg.get("market_id"), str
        ):
            raise UniverseValidationError("book identity missing")
        return cls(
            sid,
            seq,
            msg["market_ticker"],
            msg["market_id"],
            levels("yes_dollars_fp"),
            levels("no_dollars_fp"),
            mode,
            raw,
        )


@dataclass(frozen=True, slots=True)
class BookDeltaEvent:
    sid: int
    seq: int
    ticker: str
    market_id: str
    price: Decimal
    delta: Decimal
    side: str
    exchange_at: datetime
    raw: dict[str, Any]

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> BookDeltaEvent:
        sid, seq, msg = envelope(raw, "orderbook_delta")
        side = msg.get("side")
        if (
            side not in {"yes", "no"}
            or not isinstance(msg.get("market_ticker"), str)
            or not isinstance(msg.get("market_id"), str)
        ):
            raise UniverseValidationError("delta identity or side invalid")
        return cls(
            sid,
            seq,
            msg["market_ticker"],
            msg["market_id"],
            exact(msg.get("price_dollars"), "price_dollars"),
            exact(msg.get("delta_fp"), "delta_fp"),
            side,
            event_time(msg.get("ts_ms")),
            raw,
        )


@dataclass(frozen=True, slots=True)
class TickerEvent:
    sid: int
    seq: int
    ticker: str
    price: Decimal | None
    yes_bid: Decimal | None
    yes_ask: Decimal | None
    volume: Decimal
    open_interest: Decimal
    yes_bid_size: Decimal | None
    yes_ask_size: Decimal | None
    last_trade_size: Decimal | None
    exchange_at: datetime
    raw: dict[str, Any]

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> TickerEvent:
        sid, seq, msg = envelope(raw, "ticker")
        if not isinstance(msg.get("market_ticker"), str):
            raise UniverseValidationError("ticker identity missing")

        def optional(key: str) -> Decimal | None:
            return None if msg.get(key) is None else exact(msg[key], key)

        return cls(
            sid,
            seq,
            msg["market_ticker"],
            optional("price_dollars"),
            optional("yes_bid_dollars"),
            optional("yes_ask_dollars"),
            exact(msg.get("volume_fp"), "volume_fp"),
            exact(msg.get("open_interest_fp"), "open_interest_fp"),
            optional("yes_bid_size_fp"),
            optional("yes_ask_size_fp"),
            optional("last_trade_size_fp"),
            event_time(msg.get("ts_ms")),
            raw,
        )


class OutcomeSide(StrEnum):
    YES = "yes"
    NO = "no"


class BookSide(StrEnum):
    BID = "bid"
    ASK = "ask"


@dataclass(frozen=True, slots=True)
class TradeEvent:
    sid: int
    seq: int
    trade_id: str
    ticker: str
    yes_price: Decimal
    no_price: Decimal
    count: Decimal
    taker_outcome_side: OutcomeSide
    taker_book_side: BookSide
    exchange_at: datetime
    raw: dict[str, Any]

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> TradeEvent:
        sid, seq, msg = envelope(raw, "trade")
        try:
            outcome = OutcomeSide(str(msg.get("taker_outcome_side")))
            book = BookSide(str(msg.get("taker_book_side")))
        except ValueError as exc:
            raise UniverseValidationError("canonical taker direction missing") from exc
        if not isinstance(msg.get("trade_id"), str) or not isinstance(
            msg.get("market_ticker"), str
        ):
            raise UniverseValidationError("trade identity missing")
        yes, no = (
            exact(msg.get("yes_price_dollars"), "yes_price_dollars"),
            exact(msg.get("no_price_dollars"), "no_price_dollars"),
        )
        if yes + no != Decimal(1):
            raise UniverseValidationError("trade prices do not complement")
        return cls(
            sid,
            seq,
            msg["trade_id"],
            msg["market_ticker"],
            yes,
            no,
            exact(msg.get("count_fp"), "count_fp"),
            outcome,
            book,
            event_time(msg.get("ts_ms")),
            raw,
        )


class LifecycleKind(StrEnum):
    CREATED = "created"
    ACTIVATED = "activated"
    DEACTIVATED = "deactivated"
    CLOSE_DATE_UPDATED = "close_date_updated"
    DETERMINED = "determined"
    SETTLED = "settled"
    METADATA_UPDATED = "metadata_updated"
    PRICE_STRUCTURE_UPDATED = "price_level_structure_updated"


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    sid: int
    seq: int
    channel: str
    ticker: str
    kind: LifecycleKind
    exchange_at: datetime
    result: str | None
    settlement_value: Decimal | None
    metadata_refresh_required: bool
    rest_status_hint: str | None
    is_mve: bool
    raw: dict[str, Any]

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> LifecycleEvent:
        message_type = raw.get("type")
        if message_type not in {"market_lifecycle_v2", "multivariate_market_lifecycle"}:
            raise UniverseValidationError("unsupported lifecycle channel")
        sid, seq, msg = envelope(raw, message_type)
        try:
            kind = LifecycleKind(str(msg.get("event_type")))
        except ValueError as exc:
            raise UniverseValidationError("unknown lifecycle event") from exc
        if not isinstance(msg.get("market_ticker"), str):
            raise UniverseValidationError("lifecycle ticker missing")
        settlement = (
            None
            if msg.get("settlement_value") is None
            else exact(msg["settlement_value"], "settlement_value")
        )
        refresh = kind in {
            LifecycleKind.CREATED,
            LifecycleKind.ACTIVATED,
            LifecycleKind.CLOSE_DATE_UPDATED,
            LifecycleKind.METADATA_UPDATED,
            LifecycleKind.PRICE_STRUCTURE_UPDATED,
        }
        return cls(
            sid,
            seq,
            message_type,
            msg["market_ticker"],
            kind,
            event_time(msg.get("ts_ms")),
            msg.get("result"),
            settlement,
            refresh,
            "finalized" if kind == LifecycleKind.SETTLED else None,
            message_type == "multivariate_market_lifecycle",
            raw,
        )
