"""Immutable Perps book and market-state research evidence."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from .domain import ShadowResearchError
from .perps_events import PerpsBookDeltaEvent, PerpsBookSnapshotEvent, PerpsTickerEvent
from .perps_metadata import PerpsMarketMetadata, TimestampedPrice, canonical_hash
from .perps_orderbook import PerpsBookState, PerpsBookView


class PerpsUpdateKind(StrEnum):
    SNAPSHOT = "SNAPSHOT"
    DELTA = "DELTA"


def _utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ShadowResearchError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _identity(values: dict[str, Any]) -> str:
    return canonical_hash({key: value for key, value in values.items() if key != "evidence_id"})


def perps_book_fingerprint(event: PerpsBookSnapshotEvent | PerpsBookDeltaEvent) -> str:
    common: dict[str, Any] = {"ticker": event.ticker, "sid": event.sid, "sequence": event.sequence}
    if isinstance(event, PerpsBookSnapshotEvent):
        return canonical_hash(
            {**common, "kind": "SNAPSHOT", "bids": event.bids, "asks": event.asks}
        )
    return canonical_hash(
        {
            **common,
            "kind": "DELTA",
            "price": event.price,
            "delta": event.delta,
            "side": event.side.value,
            "exchange_at": None if event.exchange_at is None else event.exchange_at.isoformat(),
            "last_update_reason": None
            if event.last_update_reason is None
            else event.last_update_reason.value,
        }
    )


def perps_ticker_fingerprint(event: PerpsTickerEvent) -> str:
    return canonical_hash({field.name: getattr(event, field.name) for field in fields(event)})


@dataclass(frozen=True, slots=True)
class PerpsBookEvidenceObservation:
    evidence_id: str
    update_kind: PerpsUpdateKind
    ticker: str
    exchange_index: int
    connection_epoch: UUID
    sid: int
    sequence: int
    source_event_fingerprint: str
    book_state: PerpsBookState
    best_bid: Decimal | None
    best_ask: Decimal | None
    best_bid_size: Decimal | None
    best_ask_size: Decimal | None
    exchange_at: datetime | None
    received_at: datetime
    available_at: datetime
    tick_size: Decimal
    contract_size: Decimal
    fractional_trading_enabled: bool
    perps_contract_hash: str
    market_metadata_hash: str
    full_book_hash: str
    production_influence: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if (
            type(self.exchange_index) is not int
            or self.exchange_index < 0
            or type(self.sid) is not int
            or self.sid < 1
            or type(self.sequence) is not int
            or self.sequence < 1
        ):
            raise ShadowResearchError("invalid Perps evidence identity")
        if not isinstance(self.connection_epoch, UUID) or self.connection_epoch.int == 0:
            raise ShadowResearchError("non-zero connection epoch required")
        if self.book_state is not PerpsBookState.CURRENT:
            raise ShadowResearchError("only current, uncrossed Perps books are evidence")
        received, available = (
            _utc(self.received_at, "received_at"),
            _utc(self.available_at, "available_at"),
        )
        exchange = None if self.exchange_at is None else _utc(self.exchange_at, "exchange_at")
        if received > available:
            raise ShadowResearchError("received_at must not exceed available_at")
        object.__setattr__(self, "received_at", received)
        object.__setattr__(self, "available_at", available)
        object.__setattr__(self, "exchange_at", exchange)
        if self.best_bid is None and self.best_ask is None:
            raise ShadowResearchError("at least one Perps book side is required")
        for price, size in (
            (self.best_bid, self.best_bid_size),
            (self.best_ask, self.best_ask_size),
        ):
            if (price is None) != (size is None) or (
                price is not None and (price <= 0 or size is None or size <= 0)
            ):
                raise ShadowResearchError("invalid Perps top of book")
        if self.production_influence != Decimal("0"):
            raise ShadowResearchError("Perps evidence production influence must be zero")
        for digest in (
            self.source_event_fingerprint,
            self.perps_contract_hash,
            self.market_metadata_hash,
            self.full_book_hash,
        ):
            if len(digest) != 64:
                raise ShadowResearchError("evidence hashes must be SHA-256")
        if self.evidence_id != _identity(
            {field.name: getattr(self, field.name) for field in fields(self)}
        ):
            raise ShadowResearchError("evidence_id does not match canonical payload")

    @classmethod
    def create(
        cls,
        *,
        event: PerpsBookSnapshotEvent | PerpsBookDeltaEvent,
        market: PerpsMarketMetadata,
        epoch: UUID,
        view: PerpsBookView,
        received_at: datetime,
        available_at: datetime,
    ) -> PerpsBookEvidenceObservation:
        values: dict[str, Any] = dict(
            update_kind=PerpsUpdateKind.SNAPSHOT
            if isinstance(event, PerpsBookSnapshotEvent)
            else PerpsUpdateKind.DELTA,
            ticker=event.ticker,
            exchange_index=market.exchange_index,
            connection_epoch=epoch,
            sid=event.sid,
            sequence=event.sequence,
            source_event_fingerprint=perps_book_fingerprint(event),
            book_state=view.state,
            best_bid=view.best_bid,
            best_ask=view.best_ask,
            best_bid_size=view.best_bid_size,
            best_ask_size=view.best_ask_size,
            exchange_at=event.exchange_at if isinstance(event, PerpsBookDeltaEvent) else None,
            received_at=received_at,
            available_at=available_at,
            tick_size=market.tick_size,
            contract_size=market.contract_size,
            fractional_trading_enabled=market.fractional_trading_enabled,
            perps_contract_hash=market.perps_contract_hash,
            market_metadata_hash=market.market_metadata_hash,
            full_book_hash=view.full_book_hash,
            production_influence=Decimal("0"),
        )
        values["evidence_id"] = _identity(values)
        return cls(**values)


@dataclass(frozen=True, slots=True)
class PerpsMarketStateObservation:
    evidence_id: str
    ticker: str
    exchange_index: int
    connection_epoch: UUID
    sid: int
    source_fingerprint: str
    ticker_ts_ms: int
    received_at: datetime
    available_at: datetime
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
    funding_rate: Decimal | None
    funding_observed_ts_ms: int | None
    next_funding_time_ms: int | None
    market_metadata_hash: str
    production_influence: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if (
            type(self.sid) is not int
            or self.sid < 1
            or type(self.ticker_ts_ms) is not int
            or self.ticker_ts_ms < 0
        ):
            raise ShadowResearchError("invalid ticker identity")
        if not isinstance(self.connection_epoch, UUID) or self.connection_epoch.int == 0:
            raise ShadowResearchError("non-zero ticker epoch required")
        received, available = (
            _utc(self.received_at, "received_at"),
            _utc(self.available_at, "available_at"),
        )
        if received > available:
            raise ShadowResearchError("received_at must not exceed available_at")
        object.__setattr__(self, "received_at", received)
        object.__setattr__(self, "available_at", available)
        if self.production_influence != Decimal("0"):
            raise ShadowResearchError("Perps market state production influence must be zero")
        if self.evidence_id != _identity(
            {field.name: getattr(self, field.name) for field in fields(self)}
        ):
            raise ShadowResearchError("evidence_id does not match canonical payload")

    @classmethod
    def create(
        cls,
        event: PerpsTickerEvent,
        market: PerpsMarketMetadata,
        epoch: UUID,
        received_at: datetime,
        available_at: datetime,
    ) -> PerpsMarketStateObservation:
        funding = event.funding_rate
        values: dict[str, Any] = dict(
            ticker=event.ticker,
            exchange_index=market.exchange_index,
            connection_epoch=epoch,
            sid=event.sid,
            source_fingerprint=perps_ticker_fingerprint(event),
            ticker_ts_ms=event.ts_ms,
            received_at=received_at,
            available_at=available_at,
            price=event.price,
            bid=event.bid,
            ask=event.ask,
            bid_size=event.bid_size,
            ask_size=event.ask_size,
            last_trade_size=event.last_trade_size,
            volume=event.volume,
            volume_notional_value_dollars=event.volume_notional_value_dollars,
            volume_24h=event.volume_24h,
            volume_24h_notional_value_dollars=event.volume_24h_notional_value_dollars,
            open_interest=event.open_interest,
            open_interest_notional_value_dollars=event.open_interest_notional_value_dollars,
            reference_price=event.reference_price,
            settlement_mark_price=event.settlement_mark_price,
            liquidation_mark_price=event.liquidation_mark_price,
            funding_rate=None if funding is None else funding.rate,
            funding_observed_ts_ms=None if funding is None else funding.ts_ms,
            next_funding_time_ms=None if funding is None else funding.next_funding_time_ms,
            market_metadata_hash=market.market_metadata_hash,
            production_influence=Decimal("0"),
        )
        values["evidence_id"] = _identity(values)
        return cls(**values)
