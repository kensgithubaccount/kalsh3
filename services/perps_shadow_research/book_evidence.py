"""Canonical, immutable order-book evidence for offline research only."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from services.real_time_market_data.orderbook import BookState, PriceMode

from .domain import ShadowResearchError


class BookUpdateKind(StrEnum):
    SNAPSHOT = "SNAPSHOT"
    DELTA = "DELTA"


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ShadowResearchError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _non_negative_int(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ShadowResearchError(f"{field} must be a non-negative integer")
    return value


def _optional_decimal(value: Any, field: str) -> Decimal | None:
    if value is not None and not isinstance(value, Decimal):
        raise ShadowResearchError(f"{field} must be a Decimal or None")
    return value


@dataclass(frozen=True, slots=True)
class BookEvidenceObservation:
    evidence_id: str
    update_kind: BookUpdateKind
    ticker: str
    market_id: str
    exchange_index: int
    connection_epoch: UUID
    sid: int
    sequence: int
    book_state: BookState
    best_yes_bid: Decimal | None
    best_yes_ask: Decimal | None
    best_bid_size: Decimal | None
    best_ask_size: Decimal | None
    exchange_at: datetime | None
    received_at: datetime
    available_at: datetime
    price_mode: PriceMode
    structure_hash: str
    production_influence: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if not isinstance(self.update_kind, BookUpdateKind):
            raise ShadowResearchError("update_kind must be a BookUpdateKind")
        if not self.ticker or not self.market_id or not self.structure_hash:
            raise ShadowResearchError("ticker, market_id, and structure_hash are required")
        _non_negative_int(self.exchange_index, "exchange_index")
        _non_negative_int(self.sid, "sid")
        _non_negative_int(self.sequence, "sequence")
        if not isinstance(self.connection_epoch, UUID) or self.connection_epoch.int == 0:
            raise ShadowResearchError("connection_epoch must be a non-zero UUID")
        if self.book_state is not BookState.CURRENT:
            raise ShadowResearchError("only CURRENT books are usable evidence")
        if not isinstance(self.price_mode, PriceMode):
            raise ShadowResearchError("price_mode must be a PriceMode")
        received_at = _utc(self.received_at, "received_at")
        available_at = _utc(self.available_at, "available_at")
        exchange_at = None if self.exchange_at is None else _utc(self.exchange_at, "exchange_at")
        object.__setattr__(self, "received_at", received_at)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "exchange_at", exchange_at)
        if received_at > available_at:
            raise ShadowResearchError("received_at must not exceed available_at")
        bid = _optional_decimal(self.best_yes_bid, "best_yes_bid")
        ask = _optional_decimal(self.best_yes_ask, "best_yes_ask")
        bid_size = _optional_decimal(self.best_bid_size, "best_bid_size")
        ask_size = _optional_decimal(self.best_ask_size, "best_ask_size")
        if bid is None and ask is None:
            raise ShadowResearchError("at least one usable book side is required")
        for price, size, side in ((bid, bid_size, "bid"), (ask, ask_size, "ask")):
            if (price is None) != (size is None):
                raise ShadowResearchError(f"{side} price and size must be present together")
            if price is not None and (price <= 0 or price >= 1 or size is None or size <= 0):
                raise ShadowResearchError(f"{side} price and size are invalid")
        if not isinstance(
            self.production_influence, Decimal
        ) or self.production_influence != Decimal("0"):
            raise ShadowResearchError("book evidence cannot have production influence")
        expected = self.canonical_evidence_id()
        if self.evidence_id != expected:
            raise ShadowResearchError("evidence_id does not match canonical payload")

    @classmethod
    def create(cls, **values: Any) -> BookEvidenceObservation:
        for timestamp in ("received_at", "available_at", "exchange_at"):
            value = values.get(timestamp)
            if value is not None:
                values[timestamp] = _utc(value, timestamp)
        values.setdefault("production_influence", Decimal("0"))
        values["evidence_id"] = cls._canonical_id(values)
        return cls(**values)

    def canonical_evidence_id(self) -> str:
        return self._canonical_id({field.name: getattr(self, field.name) for field in fields(self)})

    @staticmethod
    def _canonical_id(values: dict[str, Any]) -> str:
        def encoded(value: Any) -> Any:
            if isinstance(value, Decimal):
                return str(value)
            if isinstance(value, datetime):
                return value.astimezone(UTC).isoformat(timespec="microseconds")
            if isinstance(value, UUID):
                return str(value)
            if isinstance(value, StrEnum):
                return value.value
            return value

        payload = {key: encoded(value) for key, value in values.items() if key != "evidence_id"}
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(canonical.encode()).hexdigest()
