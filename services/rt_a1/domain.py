"""Fail-closed KXRT universe parsing from public Kalshi responses."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

from services.market_universe.public_read import BASE, get


class DiscoveryError(ValueError):
    """The public response cannot establish a supported active KXRT contract."""


def _timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise DiscoveryError(f"{field} is missing")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DiscoveryError(f"{field} is malformed") from exc
    if result.tzinfo is None:
        raise DiscoveryError(f"{field} lacks timezone")
    return result.astimezone(UTC)


def _strike(raw: dict[str, Any]) -> Decimal:
    value = raw.get("floor_strike")
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise DiscoveryError("threshold strike is missing or malformed")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise DiscoveryError("threshold strike is malformed") from exc
    if not result.is_finite():
        raise DiscoveryError("threshold strike is malformed")
    return result


def _comparator(primary: object, secondary: object) -> str:
    text = f"{primary or ''} {secondary or ''}".lower()
    if re.search(r"at least|greater than or equal|>=", text):
        return ">="
    if re.search(r"at most|less than or equal|<=", text):
        return "<="
    if re.search(r"greater than|more than|above|>", text):
        return ">"
    if re.search(r"less than|below|<", text):
        return "<"
    raise DiscoveryError("unsupported threshold comparator")


def _rt_url(raw: dict[str, Any]) -> str:
    candidates: list[object] = [
        raw.get("settlement_sources"),
        raw.get("rules_primary"),
        raw.get("rules_secondary"),
    ]
    for candidate in candidates:
        values = candidate if isinstance(candidate, list) else [candidate]
        for item in values:
            text = str(item) if item is not None else ""
            match = re.search(r"https?://[^\s)\]\\\"']+", text)
            if match:
                url = match.group(0).rstrip(".,")
                host = (urlparse(url).hostname or "").lower()
                if host == "rottentomatoes.com" or host.endswith(".rottentomatoes.com"):
                    return url
    raise DiscoveryError("Rotten Tomatoes settlement URL is missing")


@dataclass(frozen=True, slots=True)
class ActiveKxrtMarket:
    event_ticker: str
    market_ticker: str
    film_title: str
    rt_url: str
    rules_primary: str
    rules_secondary: str
    comparator: str
    strike: Decimal
    open_time: datetime
    determination_time: datetime
    status: str
    settlement_source: str
    raw_body_sha256: str
    raw_body_b64: str
    observed_at: datetime
    fee_type: str | None = None
    fee_multiplier: str | None = None


def parse_active_market(
    raw: dict[str, Any],
    *,
    event_ticker: str,
    body_sha256: str,
    raw_body_b64: str,
    observed_at: datetime,
) -> ActiveKxrtMarket:
    if raw.get("status") != "active":
        raise DiscoveryError("market is not ACTIVE")
    if not isinstance(raw.get("ticker"), str) or not raw["ticker"]:
        raise DiscoveryError("market ticker is missing")
    series = str(raw.get("series_ticker", event_ticker.split("-")[0]))
    if not series.startswith("KXRT"):
        raise DiscoveryError("market is outside KXRT")
    primary, secondary = raw.get("rules_primary"), raw.get("rules_secondary", "")
    if not isinstance(primary, str) or not isinstance(secondary, str):
        raise DiscoveryError("rules are missing")
    determination = raw.get("expiration_time", raw.get("expected_expiration_time"))
    return ActiveKxrtMarket(
        event_ticker=event_ticker,
        market_ticker=raw["ticker"],
        film_title=str(raw.get("title") or raw.get("subtitle") or event_ticker),
        rt_url=_rt_url(raw),
        rules_primary=primary,
        rules_secondary=secondary,
        comparator=_comparator(primary, secondary),
        strike=_strike(raw),
        open_time=_timestamp(raw.get("open_time"), "open_time"),
        determination_time=_timestamp(determination, "determination timestamp"),
        status="active",
        settlement_source="Rotten Tomatoes",
        raw_body_sha256=body_sha256,
        raw_body_b64=raw_body_b64,
        observed_at=observed_at,
        fee_type=raw.get("fee_type") if isinstance(raw.get("fee_type"), str) else None,
        fee_multiplier=(
            str(raw["fee_multiplier"]) if raw.get("fee_multiplier") is not None else None
        ),
    )


def discover_active_kxrt(
    *,
    transport: Callable[[str], dict[str, object]] = get,
) -> tuple[ActiveKxrtMarket, ...]:
    """Discover current active KXRT markets via public GET-only endpoints.

    The list response identifies candidate events; each event's embedded market
    records are validated independently. Any malformed candidate fails closed.
    """
    response = transport(f"{BASE}/events?status=open&series_ticker=KXRT&limit=1000")
    payload = response.get("payload")
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise DiscoveryError("events response shape is unsupported")
    result: list[ActiveKxrtMarket] = []
    for event in payload["events"]:
        if not isinstance(event, dict) or not isinstance(event.get("event_ticker"), str):
            raise DiscoveryError("event identity is malformed")
        event_ticker = event["event_ticker"]
        exact = transport(f"{BASE}/events/{event_ticker}")
        exact_payload = exact.get("payload")
        if not isinstance(exact_payload, dict) or not isinstance(
            exact_payload.get("markets"), list
        ):
            raise DiscoveryError("event response has no markets")
        body_hash = exact.get("body_sha256")
        body_b64 = exact.get("raw_body_b64")
        observed = _timestamp(exact.get("observed_at"), "event observed_at")
        if not isinstance(body_hash, str) or not isinstance(body_b64, str):
            raise DiscoveryError("event raw evidence is missing")
        for market in exact_payload["markets"]:
            if not isinstance(market, dict):
                raise DiscoveryError("market record is malformed")
            if market.get("status") == "active":
                result.append(
                    parse_active_market(
                        market,
                        event_ticker=event_ticker,
                        body_sha256=body_hash,
                        raw_body_b64=body_b64,
                        observed_at=observed,
                    )
                )
    if len({m.market_ticker for m in result}) != len(result):
        raise DiscoveryError("duplicate active market ticker")
    return tuple(result)
