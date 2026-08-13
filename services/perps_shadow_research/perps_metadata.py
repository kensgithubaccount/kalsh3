"""Strict, immutable Perps market metadata derived from the margin OpenAPI."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from uuid import UUID

from .domain import ShadowResearchError

PERPS_OPENAPI_URL = "https://docs.kalshi.com/perps_openapi.yaml"
PERPS_ASYNCAPI_URL = "https://docs.kalshi.com/perps_asyncapi.yaml"
PARSER_VERSION = "m25b1-v1"
OFFICIAL_OPENAPI_PROVENANCE: SourceContractProvenance


def decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ShadowResearchError(f"{field} must be an exact decimal value")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ShadowResearchError(f"{field} must be an exact decimal value") from exc
    if not parsed.is_finite():
        raise ShadowResearchError(f"{field} must be finite")
    return parsed


def _canonical_decimal(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def canonical_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return {"$decimal": _canonical_decimal(value)}
    if isinstance(value, Mapping):
        return {key: canonical_value(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [canonical_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return {"$timestamp": value.isoformat()}
    if isinstance(value, UUID):
        return {"$uuid": str(value)}
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return canonical_value(asdict(value))
    if value is None or type(value) in {str, int, bool}:
        return value
    raise ShadowResearchError(f"unsupported metadata value type: {type(value).__name__}")


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(canonical_value(value), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _freeze(value: Any) -> Any:
    normalized = canonical_value(value)
    if isinstance(normalized, dict):
        return MappingProxyType({key: _freeze(item) for key, item in normalized.items()})
    if isinstance(normalized, list):
        return tuple(_freeze(item) for item in normalized)
    return normalized


@dataclass(frozen=True, slots=True)
class SourceContractProvenance:
    source_url: str
    retrieved_on: date
    sha256: str
    parser_version: str = PARSER_VERSION

    def __post_init__(self) -> None:
        if self.source_url not in {PERPS_OPENAPI_URL, PERPS_ASYNCAPI_URL}:
            raise ShadowResearchError("unsupported Perps contract source")
        if len(self.sha256) != 64 or any(char not in "0123456789abcdef" for char in self.sha256):
            raise ShadowResearchError("contract checksum must be SHA-256")
        if not self.parser_version:
            raise ShadowResearchError("parser version is required")


OFFICIAL_OPENAPI_PROVENANCE = SourceContractProvenance(
    PERPS_OPENAPI_URL,
    date(2026, 8, 13),
    "af990c0e11353da0f06eaa017738646f70460784f3068a6c2460d51a0f21434b",
)


@dataclass(frozen=True, slots=True)
class PerpsSchedule:
    is_open: bool
    next_open_ts: int | None
    next_close_ts: int | None

    def __post_init__(self) -> None:
        if type(self.is_open) is not bool:
            raise ShadowResearchError("schedule.is_open must be boolean")
        for name in ("next_open_ts", "next_close_ts"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ShadowResearchError(f"schedule.{name} must be a non-negative integer or null")


@dataclass(frozen=True, slots=True)
class TimestampedPrice:
    price: Decimal
    ts_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.price, Decimal) or not self.price.is_finite():
            raise ShadowResearchError("timestamped price must be finite Decimal")
        if type(self.ts_ms) is not int or self.ts_ms < 0:
            raise ShadowResearchError("timestamped price ts_ms must be non-negative integer")


@dataclass(frozen=True, slots=True)
class PerpsMarketMetadata:
    ticker: str
    status: str
    title: str
    exchange_index: int
    contract_size: Decimal
    tick_size: Decimal
    fractional_trading_enabled: bool
    schedule: PerpsSchedule | None
    leverage_estimate: Decimal | None
    leverage_estimates: tuple[tuple[Decimal, Decimal], ...]
    long_leverage_estimates: tuple[tuple[Decimal, Decimal], ...]
    short_leverage_estimates: tuple[tuple[Decimal, Decimal], ...]
    bid: Decimal | None
    ask: Decimal | None
    price: Decimal | None
    settlement_mark_price: TimestampedPrice | None
    liquidation_mark_price: TimestampedPrice | None
    reference_price: TimestampedPrice | None
    volume: Decimal | None
    open_interest: Decimal | None
    normalized_snapshot: Mapping[str, Any]
    observed_at: datetime
    perps_contract_hash: str
    market_metadata_hash: str
    source_provenance: SourceContractProvenance

    def __post_init__(self) -> None:
        if not self.ticker or self.status not in {"inactive", "active", "closed"} or not self.title:
            raise ShadowResearchError("invalid Perps market identity or status")
        # Local fail-closed policy: exchange shards are required to be non-negative.
        if type(self.exchange_index) is not int or self.exchange_index < 0:
            raise ShadowResearchError("exchange_index must be an exact non-negative integer")
        if self.contract_size <= 0 or self.tick_size <= 0:
            raise ShadowResearchError("contract_size and tick_size must be positive")
        if type(self.fractional_trading_enabled) is not bool:
            raise ShadowResearchError("fractional_trading_enabled must be boolean")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ShadowResearchError("observed_at must be timezone-aware")
        object.__setattr__(self, "observed_at", self.observed_at.astimezone(UTC))

    def is_open(self) -> bool:
        return self.status == "active" and (self.schedule is None or self.schedule.is_open)

    def quantity_valid(self, quantity: Decimal) -> bool:
        """Validate the documented fixed-point Perps book quantity contract."""
        if not quantity.is_finite() or quantity < 0:
            return False
        return quantity % Decimal("0.01") == 0

    def price_on_tick(self, price: Decimal) -> bool:
        return price > 0 and price % self.tick_size == 0


def _required(raw: Mapping[str, Any], field: str) -> Any:
    if field not in raw:
        raise ShadowResearchError(f"required Perps market field missing: {field}")
    return raw[field]


def _optional_decimal(raw: Mapping[str, Any], field: str) -> Decimal | None:
    value = raw.get(field)
    return None if value is None else decimal(value, field)


def _estimates(raw: Mapping[str, Any], field: str) -> tuple[tuple[Decimal, Decimal], ...]:
    value = raw.get(field)
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise ShadowResearchError(f"{field} must be a mapping or null")
    rows = [
        (decimal(key, f"{field}.notional"), decimal(item, field)) for key, item in value.items()
    ]
    return tuple(sorted(rows))


def _timestamped(raw: Mapping[str, Any], field: str) -> TimestampedPrice | None:
    value = raw.get(field)
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"price", "ts_ms"}:
        raise ShadowResearchError(f"{field} must contain exactly price and ts_ms")
    return TimestampedPrice(decimal(value["price"], f"{field}.price"), value["ts_ms"])


def parse_perps_market(
    raw: Mapping[str, Any],
    *,
    observed_at: datetime,
    source_provenance: SourceContractProvenance = OFFICIAL_OPENAPI_PROVENANCE,
) -> PerpsMarketMetadata:
    if not isinstance(raw, Mapping):
        raise ShadowResearchError("Perps market payload must be an object")
    ticker, status, title = (_required(raw, name) for name in ("ticker", "status", "title"))
    exchange_index = _required(raw, "exchange_index")
    fractional = _required(raw, "fractional_trading_enabled")
    if not all(isinstance(value, str) for value in (ticker, status, title)):
        raise ShadowResearchError("ticker, status, and title must be strings")
    if type(exchange_index) is not int:
        raise ShadowResearchError("exchange_index must be an exact integer")
    if type(fractional) is not bool:
        raise ShadowResearchError("fractional_trading_enabled must be boolean")
    schedule_raw = _required(raw, "schedule")
    schedule = None
    if schedule_raw is not None:
        if not isinstance(schedule_raw, Mapping):
            raise ShadowResearchError("schedule must be an object or null")
        schedule = PerpsSchedule(
            _required(schedule_raw, "is_open"),
            _required(schedule_raw, "next_open_ts"),
            _required(schedule_raw, "next_close_ts"),
        )
    contract_size = decimal(_required(raw, "contract_size"), "contract_size")
    tick_size = decimal(_required(raw, "tick_size"), "tick_size")
    normalized = canonical_value(raw)
    contract = {
        "ticker": ticker,
        "exchange_index": exchange_index,
        "contract_size": contract_size,
        "tick_size": tick_size,
        "fractional_trading_enabled": fractional,
    }
    return PerpsMarketMetadata(
        ticker=ticker,
        status=status,
        title=title,
        exchange_index=exchange_index,
        contract_size=contract_size,
        tick_size=tick_size,
        fractional_trading_enabled=fractional,
        schedule=schedule,
        leverage_estimate=_optional_decimal(raw, "leverage_estimate"),
        leverage_estimates=_estimates(raw, "leverage_estimates"),
        long_leverage_estimates=_estimates(raw, "long_leverage_estimates"),
        short_leverage_estimates=_estimates(raw, "short_leverage_estimates"),
        bid=_optional_decimal(raw, "bid"),
        ask=_optional_decimal(raw, "ask"),
        price=_optional_decimal(raw, "price"),
        settlement_mark_price=_timestamped(raw, "settlement_mark_price"),
        liquidation_mark_price=_timestamped(raw, "liquidation_mark_price"),
        reference_price=_timestamped(raw, "reference_price"),
        volume=_optional_decimal(raw, "volume"),
        open_interest=_optional_decimal(raw, "open_interest"),
        normalized_snapshot=_freeze(normalized),
        observed_at=observed_at,
        perps_contract_hash=canonical_hash(contract),
        market_metadata_hash=canonical_hash(normalized),
        source_provenance=source_provenance,
    )
