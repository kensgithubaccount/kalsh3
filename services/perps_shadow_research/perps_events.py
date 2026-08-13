"""Strict Perps AsyncAPI book and ticker events; no Predictions semantics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from .domain import ShadowResearchError
from .perps_metadata import PerpsMarketMetadata, TimestampedPrice, decimal


def _integer(value: Any, field: str, *, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise ShadowResearchError(f"{field} must be an exact integer >= {minimum}")
    return value


def _envelope(raw: Any, kind: str, *, sequence: bool) -> tuple[int, int | None, dict[str, Any]]:
    if not isinstance(raw, dict) or raw.get("type") != kind or not isinstance(raw.get("msg"), dict):
        raise ShadowResearchError(f"malformed {kind}")
    sid = _integer(raw.get("sid"), "sid", minimum=1)
    seq = _integer(raw.get("seq"), "sequence", minimum=1) if sequence else None
    return sid, seq, raw["msg"]


class PerpsBookSide(StrEnum):
    BID = "bid"
    ASK = "ask"


class LastUpdateReason(StrEnum):
    UNSPECIFIED = ""
    DECREASE = "Decrease"
    AMEND = "Amend"
    MARGIN_CANCEL = "MarginCancel"
    SELF_TRADE_CANCEL = "SelfTradeCancel"
    EXPIRY_CANCEL = "ExpiryCancel"
    TRADE = "Trade"
    POST_ONLY_CROSS_CANCEL = "PostOnlyCrossCancel"


def _levels(
    value: Any, name: str, market: PerpsMarketMetadata
) -> tuple[tuple[Decimal, Decimal], ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ShadowResearchError(f"{name} must be an array")
    output: list[tuple[Decimal, Decimal]] = []
    seen: set[Decimal] = set()
    for level in value:
        if (
            not isinstance(level, list)
            or len(level) != 2
            or not all(isinstance(x, str) for x in level)
        ):
            raise ShadowResearchError(f"{name} level must be exactly two strings")
        price, quantity = decimal(level[0], "price"), decimal(level[1], "quantity")
        if not market.price_on_tick(price) or quantity <= 0 or not market.quantity_valid(quantity):
            raise ShadowResearchError(f"{name} level violates market contract")
        if price in seen:
            raise ShadowResearchError(f"duplicate {name} price")
        seen.add(price)
        output.append((price, quantity))
    return tuple(output)


@dataclass(frozen=True, slots=True)
class PerpsBookSnapshotEvent:
    sid: int
    sequence: int
    ticker: str
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]

    @classmethod
    def parse(cls, raw: dict[str, Any], market: PerpsMarketMetadata) -> PerpsBookSnapshotEvent:
        sid, sequence, msg = _envelope(raw, "orderbook_snapshot", sequence=True)
        ticker = msg.get("market_ticker")
        if not isinstance(ticker, str) or ticker != market.ticker:
            raise ShadowResearchError("snapshot ticker is missing or unexpected")
        return cls(
            sid,
            sequence or 0,
            ticker,
            _levels(msg.get("bid"), "bid", market),
            _levels(msg.get("ask"), "ask", market),
        )


@dataclass(frozen=True, slots=True)
class PerpsBookDeltaEvent:
    sid: int
    sequence: int
    ticker: str
    price: Decimal
    delta: Decimal
    side: PerpsBookSide
    exchange_at: datetime | None
    last_update_reason: LastUpdateReason | None
    has_client_order_id: bool
    has_subaccount: bool

    @classmethod
    def parse(cls, raw: dict[str, Any], market: PerpsMarketMetadata) -> PerpsBookDeltaEvent:
        sid, sequence, msg = _envelope(raw, "orderbook_delta", sequence=True)
        ticker = msg.get("market_ticker")
        if not isinstance(ticker, str) or ticker != market.ticker:
            raise ShadowResearchError("delta ticker is missing or unexpected")
        raw_side = msg.get("side")
        if not isinstance(raw_side, str):
            raise ShadowResearchError("delta side must be bid or ask")
        try:
            side = PerpsBookSide(raw_side)
        except ValueError as exc:
            raise ShadowResearchError("delta side must be bid or ask") from exc
        price = decimal(msg.get("price"), "price")
        quantity_delta = decimal(msg.get("delta"), "delta")
        if not market.price_on_tick(price):
            raise ShadowResearchError("delta price violates market tick")
        if not market.quantity_valid(abs(quantity_delta)):
            raise ShadowResearchError("delta quantity violates market contract")
        reason_raw = msg.get("last_update_reason")
        try:
            reason = None if reason_raw is None else LastUpdateReason(reason_raw)
        except ValueError as exc:
            raise ShadowResearchError("unknown last_update_reason") from exc
        ts_ms = msg.get("ts_ms")
        exchange_at = None
        if ts_ms is not None:
            _integer(ts_ms, "ts_ms", minimum=0)
            exchange_at = datetime.fromtimestamp(ts_ms / 1000, UTC)
        return cls(
            sid,
            sequence or 0,
            ticker,
            price,
            quantity_delta,
            side,
            exchange_at,
            reason,
            "client_order_id" in msg,
            "subaccount" in msg,
        )


@dataclass(frozen=True, slots=True)
class FundingRate:
    rate: Decimal
    next_funding_time_ms: int
    ts_ms: int


@dataclass(frozen=True, slots=True)
class PerpsTickerEvent:
    sid: int
    ticker: str
    ts_ms: int
    price: Decimal
    bid: Decimal
    ask: Decimal
    bid_size: Decimal
    ask_size: Decimal
    last_trade_size: Decimal
    volume: Decimal
    volume_notional_value_dollars: Decimal
    volume_24h: Decimal
    volume_24h_notional_value_dollars: Decimal
    open_interest: Decimal
    open_interest_notional_value_dollars: Decimal
    reference_price: TimestampedPrice | None
    settlement_mark_price: TimestampedPrice | None
    liquidation_mark_price: TimestampedPrice | None
    funding_rate: FundingRate | None

    @classmethod
    def parse(cls, raw: dict[str, Any], market: PerpsMarketMetadata) -> PerpsTickerEvent:
        sid, _, msg = _envelope(raw, "ticker", sequence=False)
        if msg.get("market_ticker") != market.ticker:
            raise ShadowResearchError("ticker identity is missing or unexpected")
        required = (
            "price",
            "bid",
            "ask",
            "bid_size_fp",
            "ask_size_fp",
            "last_trade_size_fp",
            "volume",
            "volume_notional_value_dollars",
            "volume_24h",
            "volume_24h_notional_value_dollars",
            "open_interest",
            "open_interest_notional_value_dollars",
        )
        values = {name: decimal(msg.get(name), name) for name in required}
        ts_ms = _integer(msg.get("ts_ms"), "ts_ms", minimum=0)

        def mark(name: str) -> TimestampedPrice | None:
            value = msg.get(name)
            if value is None:
                return None
            if not isinstance(value, dict):
                raise ShadowResearchError(f"{name} must be an object")
            return TimestampedPrice(
                decimal(value.get("price"), f"{name}.price"),
                _integer(value.get("ts_ms"), f"{name}.ts_ms", minimum=0),
            )

        funding_raw = msg.get("funding_rate")
        funding = None
        if funding_raw is not None:
            if not isinstance(funding_raw, dict):
                raise ShadowResearchError("funding_rate must be an object")
            funding = FundingRate(
                decimal(funding_raw.get("rate"), "funding_rate.rate"),
                _integer(
                    funding_raw.get("next_funding_time_ms"), "next_funding_time_ms", minimum=0
                ),
                _integer(funding_raw.get("ts_ms"), "funding_rate.ts_ms", minimum=0),
            )
        return cls(
            sid,
            market.ticker,
            ts_ms,
            values["price"],
            values["bid"],
            values["ask"],
            values["bid_size_fp"],
            values["ask_size_fp"],
            values["last_trade_size_fp"],
            values["volume"],
            values["volume_notional_value_dollars"],
            values["volume_24h"],
            values["volume_24h_notional_value_dollars"],
            values["open_interest"],
            values["open_interest_notional_value_dollars"],
            mark("reference_price"),
            mark("settlement_mark_price"),
            mark("liquidation_mark_price"),
            funding,
        )
