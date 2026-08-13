"""Immutable research contract for shadow perps data."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class ShadowResearchError(ValueError):
    pass


class Direction(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ShadowResearchError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _freeze(value: Any, active: set[int] | None = None) -> Any:
    if type(value) in (str, int, float, bool, Decimal) or value is None:
        return value
    if not isinstance(value, Mapping | list | tuple | set | frozenset):
        raise ShadowResearchError(f"unsupported raw payload value type: {type(value).__name__}")

    active = set() if active is None else active
    identity = id(value)
    if identity in active:
        raise ShadowResearchError("raw payload must not contain cycles")
    active.add(identity)
    try:
        if isinstance(value, Mapping):
            frozen: dict[str, Any] = {}
            for key, item in value.items():
                if type(key) is not str:
                    raise ShadowResearchError("raw payload mapping keys must be strings")
                frozen[key] = _freeze(item, active)
            return MappingProxyType(frozen)
        if isinstance(value, list | tuple):
            return tuple(_freeze(item, active) for item in value)
        return frozenset(_freeze(item, active) for item in value)
    finally:
        active.remove(identity)


def _immutable_raw(raw: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ShadowResearchError("raw payload must be a mapping")
    frozen = _freeze(raw)
    if not isinstance(frozen, Mapping):  # pragma: no cover - guaranteed above
        raise ShadowResearchError("raw payload must be a mapping")
    return frozen


def _edge(signal_value: Decimal, observed_value: Decimal, direction: Direction) -> Decimal:
    if not isinstance(direction, Direction):
        raise ShadowResearchError("direction must be a Direction")
    if direction is Direction.LONG:
        return signal_value - observed_value
    if direction is Direction.SHORT:
        return observed_value - signal_value
    raise ShadowResearchError("unsupported direction")


def _milliseconds(start: datetime, end: datetime) -> int:
    delta = end - start
    total_microseconds = (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds
    milliseconds, remainder = divmod(total_microseconds, 1_000)
    if total_microseconds < 0:
        raise ShadowResearchError("edge-decay timestamps must be monotonic")
    if remainder:
        raise ShadowResearchError("edge-decay timestamps must have exact millisecond precision")
    return milliseconds


@dataclass(frozen=True, slots=True)
class LeverageEstimate:
    notional: Decimal
    leverage: Decimal


@dataclass(frozen=True, slots=True)
class MarginMarketObservation:
    """One immutable market-metadata observation.

    Directional leverage estimates remain separate by construction.  The raw
    payload is retained so schema evolution can be replayed without silently
    inventing missing values.
    """

    ticker: str
    exchange_index: int
    observed_at: datetime
    long_leverage_estimates: tuple[LeverageEstimate, ...] = ()
    short_leverage_estimates: tuple[LeverageEstimate, ...] = ()
    symmetric_leverage_estimates: tuple[LeverageEstimate, ...] = ()
    funding_rate: Decimal | None = None
    mark_price: Decimal | None = None
    reference_price: Decimal | None = None
    raw: Mapping[str, Any] | None = None
    production_influence: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))
        object.__setattr__(self, "raw", _immutable_raw(self.raw))
        if not self.ticker:
            raise ShadowResearchError("ticker is required")
        if self.exchange_index < 0:
            raise ShadowResearchError("exchange_index must be non-negative")
        if self.production_influence != 0:
            raise ShadowResearchError("shadow perps data cannot have production influence")


@dataclass(frozen=True, slots=True)
class PortfolioMarginObservation:
    """Portfolio-level margin is canonical; position-level values stay nullable."""

    subaccount: int
    exchange_index: int
    observed_at: datetime
    available_balance: Decimal | None = None
    portfolio_value: Decimal | None = None
    margin_used: Decimal | None = None
    maintenance_margin: Decimal | None = None
    raw: Mapping[str, Any] | None = None
    production_influence: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))
        object.__setattr__(self, "raw", _immutable_raw(self.raw))
        if self.subaccount < 0:
            raise ShadowResearchError("subaccount must be non-negative")
        if self.exchange_index < 0:
            raise ShadowResearchError("exchange_index must be non-negative")
        if self.production_influence != 0:
            raise ShadowResearchError("shadow margin data cannot have production influence")


@dataclass(frozen=True, slots=True)
class QuoteObservation:
    observed_at: datetime
    value: Decimal
    source: str
    exchange_index: int
    subaccount: int | None = None
    production_influence: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))
        if self.exchange_index < 0:
            raise ShadowResearchError("exchange_index must be non-negative")
        if self.subaccount is not None and self.subaccount < 0:
            raise ShadowResearchError("subaccount must be non-negative when present")
        if self.production_influence != 0:
            raise ShadowResearchError("shadow quote data cannot have production influence")


@dataclass(frozen=True, slots=True)
class EdgeDecayObservation:
    """Economic latency measurement with no order-placement semantics."""

    artifact_id: str
    ticker: str
    direction: Direction
    exchange_index: int
    signal_created_at: datetime
    signal_available_at: datetime
    decision_at: datetime
    hypothetical_send_at: datetime
    signal_value: Decimal
    value_at_creation: Decimal
    value_at_available: Decimal
    value_at_decision: Decimal
    value_at_hypothetical_send: Decimal
    initial_edge: Decimal
    available_edge: Decimal
    decision_edge: Decimal
    send_edge: Decimal
    publication_to_available_ms: int
    available_to_decision_ms: int
    decision_to_send_ms: int
    production_influence: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if not isinstance(self.direction, Direction):
            raise ShadowResearchError("direction must be a Direction")
        for field in (
            "signal_created_at",
            "signal_available_at",
            "decision_at",
            "hypothetical_send_at",
        ):
            object.__setattr__(self, field, _utc(getattr(self, field), field))
        if self.exchange_index < 0:
            raise ShadowResearchError("exchange_index must be non-negative")
        if not (
            self.signal_created_at
            <= self.signal_available_at
            <= self.decision_at
            <= self.hypothetical_send_at
        ):
            raise ShadowResearchError("edge-decay timestamps must be monotonic")
        expected_latencies = (
            _milliseconds(self.signal_created_at, self.signal_available_at),
            _milliseconds(self.signal_available_at, self.decision_at),
            _milliseconds(self.decision_at, self.hypothetical_send_at),
        )
        stored_latencies = (
            self.publication_to_available_ms,
            self.available_to_decision_ms,
            self.decision_to_send_ms,
        )
        if stored_latencies != expected_latencies:
            raise ShadowResearchError("stored latencies contradict edge-decay timestamps")
        expected_edges = (
            _edge(self.signal_value, self.value_at_creation, self.direction),
            _edge(self.signal_value, self.value_at_available, self.direction),
            _edge(self.signal_value, self.value_at_decision, self.direction),
            _edge(self.signal_value, self.value_at_hypothetical_send, self.direction),
        )
        stored_edges = (self.initial_edge, self.available_edge, self.decision_edge, self.send_edge)
        if stored_edges != expected_edges:
            raise ShadowResearchError("stored edges contradict observed values or direction")
        if self.production_influence != 0:
            raise ShadowResearchError("edge-decay research cannot have production influence")
