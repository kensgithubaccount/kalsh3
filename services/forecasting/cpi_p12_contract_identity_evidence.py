"""Bounded P12 Kalshi CPI contract-identity evidence; no scientific authority.

Independent first-party metadata reads, using reviewed fixed-origin raw GETs.
No Reuters, BLS, orderbooks, price selection, scheduling or artifact writes.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote, urlsplit

from services.forecasting.cpi_p12_kalshi_inventory import (
    CPI_SERIES,
    P12KalshiCaptureError,
    _get_and_bind,
)
from services.market_universe import public_read

RECORD_TYPE = "CPI-P12-KALSHI-CONTRACT-IDENTITY-RESEARCH-v1"
MAX_PAGES = 8
PAGE_LIMIT = 100
_EVENT_RE = re.compile(r"^KXCPI-[A-Z0-9][A-Z0-9_.-]{0,100}$")
_MARKET_RE = re.compile(r"^KXCPI-[A-Z0-9][A-Z0-9_.-]{0,160}$")
_CURSOR_RE = re.compile(r"^[A-Za-z0-9_+=/-]{1,512}$")


class P12ContractIdentityError(ValueError):
    """Reject structurally invalid first-party responses or missing pages."""


def _utc(value: datetime, label: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise P12ContractIdentityError(f"{label} must be aware")
    return value.astimezone(UTC)


def _has_text(value: object) -> bool:
    return type(value) is str and bool(value.strip())


def _has_time(value: object) -> bool:
    if not _has_text(value):
        return False
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return False
    return stamp.tzinfo is not None and stamp.utcoffset() is not None


def _has_strike(value: object) -> bool:
    if type(value) not in (int, float, str) or not str(value).strip():
        return False
    try:
        return Decimal(str(value)).is_finite()
    except InvalidOperation:
        return False


def _source_is_described(source: object) -> bool:
    if type(source) is not dict or not _has_text(source.get("name")):
        return False
    url = source.get("url")
    if not _has_text(url):
        return False
    try:
        parsed = urlsplit(str(url))
        return parsed.scheme == "https" and bool(parsed.hostname)
    except ValueError:
        return False


def _has_source(series: dict[str, object], event: dict[str, object]) -> bool:
    for parent in (event, series):
        sources = parent.get("settlement_sources")
        if type(sources) is list and sources and all(_source_is_described(x) for x in sources):
            return True
    return False


def _gaps(market: dict[str, object]) -> list[str]:
    missing: list[str] = []
    if not _has_text(market.get("rules_primary")):
        missing.append("primary_rule_unknown")
    if not _has_text(market.get("rules_secondary")):
        missing.append("secondary_rule_unknown")
    if not _has_time(market.get("close_time")):
        missing.append("close_time_unknown")
    comparator = market.get("strike_type")
    if comparator not in ("greater", "less", "between"):
        missing.append("comparator_unknown")
    elif comparator == "greater" and not _has_strike(market.get("floor_strike")):
        missing.append("floor_strike_unknown")
    elif comparator == "less" and not _has_strike(market.get("cap_strike")):
        missing.append("cap_strike_unknown")
    elif comparator == "between" and not (
        _has_strike(market.get("floor_strike"))
        and _has_strike(market.get("cap_strike"))
    ):
        missing.append("strike_bounds_unknown")
    return missing


def capture_p12_kalshi_contract_identity(
    event_ticker: str,
    *,
    getter: Callable[[str], dict[str, object]] | None = None,
    clock: Callable[[], datetime] | None = None,
    max_pages: int = MAX_PAGES,
) -> dict[str, Any]:
    """Bounded exploratory series/event/paginated-sibling observation only.

    A terminal cursor indicates only that these API pages were exhausted;
    it does not prove point-in-time cohort integrity or event-rule authority.
    """
    if type(event_ticker) is not str or _EVENT_RE.fullmatch(event_ticker) is None:
        raise P12ContractIdentityError("invalid KXCPI event identifier")
    if type(max_pages) is not int or not 1 <= max_pages <= MAX_PAGES:
        raise P12ContractIdentityError("page bound invalid")
    transport = public_read.get if getter is None else getter
    now = clock or (lambda: datetime.now(UTC))
    if not callable(transport) or not callable(now):
        raise P12ContractIdentityError("invalid collector dependency")
    observations: list[dict[str, object]] = []

    def read(path: str) -> dict[str, object]:
        before = _utc(now(), "pre-request clock")
        try:
            envelope, body = _get_and_bind(path, getter=transport)
        except P12KalshiCaptureError as exc:
            raise P12ContractIdentityError("Kalshi raw response invalid") from exc
        after = _utc(now(), "post-request clock")
        observed_text = envelope["observed_at_utc"]
        if type(observed_text) is not str:
            raise P12ContractIdentityError("missing observed time")
        observed = _utc(
            datetime.fromisoformat(observed_text.replace("Z", "+00:00")), "observed time"
        )
        if not before <= observed <= after:
            raise P12ContractIdentityError("observation outside source request interval")
        observations.append(envelope)
        return body

    series = read(f"{public_read.BASE}/series/{CPI_SERIES}").get("series")
    event = read(f"{public_read.BASE}/events/{event_ticker}").get("event")
    if type(series) is not dict or series.get("ticker") != CPI_SERIES:
        raise P12ContractIdentityError("wrong CPI series")
    if type(event) is not dict or event.get("series_ticker") != CPI_SERIES:
        raise P12ContractIdentityError("wrong CPI event/series binding")
    identities = [event[k] for k in ("ticker", "event_ticker") if k in event]
    if not identities or any(value != event_ticker for value in identities):
        raise P12ContractIdentityError("conflicting or absent CPI event identity")

    cursor: str | None = None
    seen_cursors: set[str] = set()
    markets: dict[str, dict[str, object]] = {}
    for _ in range(max_pages):
        path = f"{public_read.BASE}/markets?event_ticker={event_ticker}&limit={PAGE_LIMIT}"
        if cursor is not None:
            path += f"&cursor={quote(cursor, safe='')}"
        page = read(path)
        items = page.get("markets")
        if type(items) is not list or not 1 <= len(items) <= PAGE_LIMIT:
            raise P12ContractIdentityError("absent, empty or oversized market page")
        for item in items:
            if type(item) is not dict or item.get("event_ticker") != event_ticker:
                raise P12ContractIdentityError("market bound to wrong event")
            ticker = item.get("ticker")
            if (
                type(ticker) is not str
                or _MARKET_RE.fullmatch(ticker) is None
                or not ticker.startswith(event_ticker + "-")
                or ticker in markets
            ):
                raise P12ContractIdentityError("duplicate or invalid sibling ticker")
            markets[ticker] = item
        next_cursor = page.get("cursor")
        if next_cursor is None or next_cursor == "":
            break
        if (
            type(next_cursor) is not str
            or _CURSOR_RE.fullmatch(next_cursor) is None
            or next_cursor in seen_cursors
        ):
            raise P12ContractIdentityError("missing or repeated valid pagination cursor")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    else:
        raise P12ContractIdentityError("market inventory not exhausted within page bound")

    gaps = {t: missing for t, m in markets.items() if (missing := _gaps(m))}
    if not _has_source(series, event):
        gaps["__event__"] = ["settlement_sources_unverified"]
    finished = _utc(now(), "completion clock")
    return {
        "record_type": RECORD_TYPE,
        "stage": "KALSHI_CONTRACT_IDENTITY_RESEARCH_ONLY",
        "requested_event_ticker": event_ticker,
        "requested_series_ticker": CPI_SERIES,
        "source_origin": f"https://{public_read.HOST}",
        "observations": observations,
        "market_row_count": len(markets),
        "market_page_count": len(observations) - 2,
        "observed_sibling_tickers": sorted(markets),
        "pagination_terminal_observed": True,
        "metadata_gaps": gaps,
        "metadata_field_coverage": "UNVERIFIED_FIELDS_PRESENT" if not gaps else "UNKNOWN",
        "cohort_authority": "OBSERVED_PAGES_ONLY_NOT_FROZEN",
        "reference_month_authority": "UNKNOWN",
        "settlement_authority": "UNVERIFIED",
        "readiness": "UNKNOWN_NOT_READY",
        "observation_completed_at_utc": finished.isoformat().replace("+00:00", "Z"),
        "acquisition_mode": "PUBLIC_GET" if getter is None else "INJECTED_TEST_GETTER",
        "research_only": True,
        "scientific_evidence_authorized": False,
        "phase1_economic_test_authorized": False,
        "reuters_forecast_authorized": False,
        "promotion_authority": "NONE",
        "production_influence": "0",
        "p12_frozen": False,
    }
