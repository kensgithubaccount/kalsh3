"""Moving-cutoff history, duplicate-safe seam routing, exact records, and account merge."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from services.market_universe.domain import UniverseValidationError, parse_time


class RecordKind(StrEnum):
    MARKET = "market"
    CANDLE = "candle"
    TRADE = "trade"
    FILL = "fill"
    ORDER = "order"


@dataclass(frozen=True, slots=True)
class CutoffObservation:
    observed_at: datetime
    market_settled_ts: datetime
    trades_created_ts: datetime
    orders_updated_ts: datetime
    spec_version: str
    backward_warning: bool = False

    @classmethod
    def parse(
        cls,
        payload: dict[str, Any],
        observed_at: datetime,
        spec_version: str,
        previous: CutoffObservation | None = None,
    ) -> CutoffObservation:
        parsed = [
            parse_time(payload.get(key))
            for key in ("market_settled_ts", "trades_created_ts", "orders_updated_ts")
        ]
        if any(value is None for value in parsed):
            raise UniverseValidationError("cutoff incomplete")
        values = tuple(value for value in parsed if value is not None)
        backward = previous is not None and any(
            values[index] < old
            for index, old in enumerate(
                (previous.market_settled_ts, previous.trades_created_ts, previous.orders_updated_ts)
            )
        )
        return cls(observed_at, values[0], values[1], values[2], spec_version, backward)

    def for_kind(self, kind: RecordKind) -> datetime:
        if kind in {RecordKind.MARKET, RecordKind.CANDLE}:
            return self.market_settled_ts
        if kind in {RecordKind.TRADE, RecordKind.FILL}:
            return self.trades_created_ts
        return self.orders_updated_ts


@dataclass(slots=True)
class CutoffHistory:
    observations: list[CutoffObservation] = field(default_factory=list)

    def add(
        self, payload: dict[str, Any], observed_at: datetime, spec_version: str
    ) -> CutoffObservation:
        observation = CutoffObservation.parse(
            payload, observed_at, spec_version, self.observations[-1] if self.observations else None
        )
        self.observations.append(observation)
        return observation


@dataclass(frozen=True, slots=True)
class MergeResult:
    records: tuple[dict[str, Any], ...]
    duplicates_removed: int
    seam_ambiguity: bool


class HistoricalRouter:
    """At exact cutoff, query safe overlap and dedupe rather than risk a silent gap."""

    def merge(
        self,
        kind: RecordKind,
        cutoff: CutoffObservation,
        historical: list[dict[str, Any]],
        live: list[dict[str, Any]],
        *,
        id_field: str,
        time_field: str,
    ) -> MergeResult:
        boundary = cutoff.for_kind(kind)
        combined = []
        seen = set()
        duplicates = 0
        seam = False
        for source, rows in (("historical", historical), ("live", live)):
            for row in rows:
                identity = row.get(id_field)
                timestamp = parse_time(row.get(time_field))
                if not isinstance(identity, str) or timestamp is None:
                    raise UniverseValidationError("routed record malformed")
                if timestamp == boundary:
                    seam = True
                if identity in seen:
                    duplicates += 1
                    continue
                seen.add(identity)
                combined.append(dict(row) | {"partition_source": source})
        combined.sort(key=lambda row: (str(row[time_field]), str(row[id_field])))
        return MergeResult(tuple(combined), duplicates, seam)


@dataclass(frozen=True, slots=True)
class MarketTrade:
    trade_id: str
    market_ticker: str
    yes_price: Decimal
    no_price: Decimal
    count: Decimal
    exchange_at: datetime
    archive_manifest_id: str


@dataclass(frozen=True, slots=True)
class UserFill:
    fill_id: str
    order_id: str
    market_ticker: str
    price: Decimal
    count: Decimal
    filled_at: datetime
    archive_manifest_id: str


@dataclass(frozen=True, slots=True)
class UserOrder:
    order_id: str
    market_ticker: str
    status: str
    count: Decimal
    updated_at: datetime
    resting: bool
    archive_manifest_id: str


@dataclass(frozen=True, slots=True)
class Candle:
    market_ticker: str
    start_at: datetime
    interval_minutes: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    assumed_delay: timedelta

    @property
    def available_at(self) -> datetime:
        return self.start_at + timedelta(minutes=self.interval_minutes) + self.assumed_delay

    def visible(self, replay_at: datetime) -> bool:
        return replay_at >= self.available_at

    def intrabar_path(self) -> None:
        raise UniverseValidationError("OHLC candles do not establish an intrabar path")


class AccountHistory:
    @staticmethod
    def merge(
        historical_fills: list[UserFill],
        live_fills: list[UserFill],
        historical_orders: list[UserOrder],
        live_orders: list[UserOrder],
    ) -> tuple[tuple[UserFill, ...], tuple[UserOrder, ...]]:
        fills = {item.fill_id: item for item in (*historical_fills, *live_fills)}
        orders = {item.order_id: item for item in (*historical_orders, *live_orders)}
        # Resting live orders remain regardless of age. Terminal records dedupe by order ID.
        return tuple(sorted(fills.values(), key=lambda x: (x.filled_at, x.fill_id))), tuple(
            sorted(orders.values(), key=lambda x: (x.updated_at, x.order_id))
        )
