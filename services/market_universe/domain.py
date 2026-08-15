"""Fail-closed canonical market domain and semantic version hashes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, cast


class UniverseValidationError(ValueError):
    pass


class MarketStatus(StrEnum):
    INITIALIZED = "initialized"
    ACTIVE = "active"
    INACTIVE = "inactive"
    CLOSED = "closed"
    DETERMINED = "determined"
    DISPUTED = "disputed"
    AMENDED = "amended"
    FINALIZED = "finalized"


class PublicFilter(StrEnum):
    UNOPENED = "unopened"
    OPEN = "open"
    PAUSED = "paused"
    CLOSED = "closed"
    SETTLED = "settled"


def normalize_status(value: Any) -> MarketStatus:
    try:
        return MarketStatus(str(value).lower())
    except ValueError as exc:
        raise UniverseValidationError("unknown canonical market status") from exc


def exact(value: Any, field: str) -> Decimal:
    if not isinstance(value, str):
        raise UniverseValidationError(f"{field} must be fixed-point string")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise UniverseValidationError(f"invalid {field}") from exc
    if not result.is_finite():
        raise UniverseValidationError(f"invalid {field}")
    return result


def stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


RULE_FIELDS = (
    "rules_primary",
    "rules_secondary",
    "settlement_sources",
    "strike_type",
    "floor_strike",
    "cap_strike",
    "functional_strike",
    "custom_strike",
    "early_close_condition",
)
METADATA_FIELDS = (
    "ticker",
    "event_ticker",
    "title",
    "subtitle",
    "market_type",
    "status",
    "price_level_structure",
    "price_ranges",
    "fractional_trading_enabled",
    "expected_expiration_time",
    "expiration_time",
    "occurrence_datetime",
    "is_provisional",
    "mve_collection_ticker",
    "settlement_value_dollars",
)


def material_hashes(raw: dict[str, Any]) -> tuple[str, str]:
    return stable_hash({k: raw.get(k) for k in RULE_FIELDS}), stable_hash(
        {k: raw.get(k) for k in METADATA_FIELDS}
    )


@dataclass(frozen=True, slots=True)
class Series:
    ticker: str
    title: str
    category: str
    frequency: str
    tags: tuple[str, ...]
    settlement_sources: tuple[dict[str, Any], ...]
    fee_type: str | None
    fee_multiplier: Decimal | None
    volume: Decimal | None
    source_updated_at: datetime | None
    metadata_hash: str
    raw: dict[str, Any]

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> Series:
        for key in ("ticker", "title", "category", "frequency"):
            if not isinstance(raw.get(key), str) or not raw[key]:
                raise UniverseValidationError(f"missing series {key}")
        sources = raw.get("settlement_sources", [])
        if not isinstance(sources, list) or any(not isinstance(x, dict) for x in sources):
            raise UniverseValidationError("invalid settlement sources")
        tags = raw.get("tags", [])
        if not isinstance(tags, list) or any(not isinstance(x, str) for x in tags):
            raise UniverseValidationError("invalid tags")
        updated = parse_time(raw.get("last_updated_ts"), optional=True)
        fee = (
            exact(raw["fee_multiplier"], "fee_multiplier")
            if raw.get("fee_multiplier") is not None
            else None
        )
        volume = exact(raw["volume_fp"], "volume_fp") if raw.get("volume_fp") is not None else None
        material = {
            k: raw.get(k)
            for k in (
                "ticker",
                "title",
                "category",
                "frequency",
                "tags",
                "settlement_sources",
                "contract_url",
                "contract_terms_url",
                "fee_type",
                "fee_multiplier",
                "additional_prohibitions",
                "product_metadata",
            )
        }
        return cls(
            raw["ticker"],
            raw["title"],
            raw["category"],
            raw["frequency"],
            tuple(tags),
            tuple(sources),
            raw.get("fee_type"),
            fee,
            volume,
            updated,
            stable_hash(material),
            raw,
        )


@dataclass(frozen=True, slots=True)
class Event:
    ticker: str
    series_ticker: str
    title: str
    category: str | None
    status: str | None
    source_updated_at: datetime | None
    metadata_hash: str
    raw: dict[str, Any]

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> Event:
        for key in ("event_ticker", "series_ticker", "title"):
            if not isinstance(raw.get(key), str) or not raw[key]:
                raise UniverseValidationError(f"missing event {key}")
        material = {
            k: raw.get(k)
            for k in (
                "event_ticker",
                "series_ticker",
                "title",
                "category",
                "status",
                "strike_date",
                "strike_period",
            )
        }
        return cls(
            raw["event_ticker"],
            raw["series_ticker"],
            raw["title"],
            raw.get("category"),
            raw.get("status"),
            parse_time(raw.get("last_updated_ts"), optional=True),
            stable_hash(material),
            raw,
        )


@dataclass(frozen=True, slots=True)
class Market:
    ticker: str
    event_ticker: str
    title: str | None
    status: MarketStatus
    market_type: str
    provisional: bool
    multivariate: bool
    volume: Decimal
    open_interest: Decimal
    rules_hash: str
    metadata_hash: str
    source_updated_at: datetime | None
    raw: dict[str, Any]

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> Market:
        for key in (
            "ticker",
            "event_ticker",
            "market_type",
            "status",
            "rules_primary",
            "price_level_structure",
        ):
            if not isinstance(raw.get(key), str) or not raw[key]:
                raise UniverseValidationError(f"missing market {key}")
        title = raw.get("title")
        if title is not None and (not isinstance(title, str) or not title):
            raise UniverseValidationError("invalid market title")
        provisional = raw.get("is_provisional", False)
        if not isinstance(provisional, bool):
            raise UniverseValidationError("invalid market is_provisional")
        updated = _parse_market_updated_time(raw)
        rules, metadata = material_hashes(raw)
        return cls(
            raw["ticker"],
            raw["event_ticker"],
            title,
            normalize_status(raw["status"]),
            raw["market_type"],
            provisional,
            bool(raw.get("mve_collection_ticker") or raw.get("multivariate_contract")),
            exact(raw.get("volume_fp", "0"), "volume_fp"),
            exact(raw.get("open_interest_fp", "0"), "open_interest_fp"),
            rules,
            metadata,
            updated,
            raw,
        )


def _parse_market_updated_time(raw: dict[str, Any]) -> datetime | None:
    updated = parse_time(raw["updated_time"]) if "updated_time" in raw else None
    legacy = parse_time(raw["last_updated_ts"]) if "last_updated_ts" in raw else None
    if updated is not None and legacy is not None and updated != legacy:
        raise UniverseValidationError("conflicting market updated timestamps")
    return updated if updated is not None else legacy


def parse_time(value: Any, *, optional: bool = False) -> datetime | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise UniverseValidationError("timestamp must be ISO string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise UniverseValidationError("malformed timestamp") from exc
    if parsed.tzinfo is None:
        raise UniverseValidationError("timestamp lacks timezone")
    return parsed.astimezone(UTC)


def parse_quantity(value: Any) -> Decimal:
    """Parse current `_fp` contract quantities at the documented 0.01 granularity."""
    quantity = exact(value, "quantity")
    if quantity <= 0:
        raise UniverseValidationError("quantity unsupported")
    if -cast(int, quantity.as_tuple().exponent) > 2:
        raise UniverseValidationError("quantity precision unsupported")
    return quantity
