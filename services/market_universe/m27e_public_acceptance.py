"""Reusable M27E bounded unauthenticated production-read acceptance producer.

M27E uses only the shared fixed-origin GET transport. Every successful response retains its exact
bounded raw body through :mod:`services.market_universe.public_read`; this module independently
re-hashes and re-parses those bytes before any M27R/M27I consumer may treat the payload or its
observation timestamp as evidence.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from .public_read import BASE, HOST, MAX_RESPONSE_BYTES, PublicReadFailure, get

SCHEMA = "kalsh3.m27e.public-read.v1"
SERIES_TICKER = "KXHIGHCHI"
ACTIVE_MARKET_STATUS = "active"

PublicGetter = Callable[[str], dict[str, object]]
Clock = Callable[[], datetime]


def _parse_time(value: object, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise PublicReadFailure(f"{field} timestamp missing")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PublicReadFailure(f"{field} timestamp malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PublicReadFailure(f"{field} timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def validate_response_evidence(
    response: object,
    *,
    expected_path: str | None = None,
    market_page: bool = False,
) -> tuple[dict[str, Any], datetime]:
    """Re-hash and re-parse one exact retained public response envelope."""

    if not isinstance(response, dict):
        raise PublicReadFailure("public response evidence is not an object")
    path = response.get("path")
    if not isinstance(path, str):
        raise PublicReadFailure("public response path missing")
    if expected_path is not None and path != expected_path:
        raise PublicReadFailure("public response path mismatch")
    if market_page:
        split = urlsplit(path)
        if split.path != BASE + "/markets":
            raise PublicReadFailure("M27E market page path mismatch")
        query = parse_qs(split.query, keep_blank_values=True)
        if query.get("series_ticker") != [SERIES_TICKER] or query.get("limit") != ["1000"]:
            raise PublicReadFailure("M27E market page query scope mismatch")
        if set(query) - {"series_ticker", "limit", "cursor"}:
            raise PublicReadFailure("M27E market page query contains unexpected parameters")

    if response.get("classification") != "SUCCESS" or response.get("status") != 200:
        raise PublicReadFailure("public response did not succeed")
    observed_at = _parse_time(response.get("observed_at"), field="public response")
    body_hash = response.get("body_sha256")
    raw_body_b64 = response.get("raw_body_b64")
    byte_count = response.get("bytes")
    if not isinstance(body_hash, str) or len(body_hash) != 64:
        raise PublicReadFailure("public response body hash missing or malformed")
    if not isinstance(raw_body_b64, str):
        raise PublicReadFailure("public response retained raw body missing")
    if isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count < 0:
        raise PublicReadFailure("public response byte count malformed")
    try:
        body = base64.b64decode(raw_body_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise PublicReadFailure("public response retained raw body is malformed") from exc
    if len(body) != byte_count or len(body) > MAX_RESPONSE_BYTES:
        raise PublicReadFailure("public response retained body length mismatch")
    if hashlib.sha256(body).hexdigest() != body_hash:
        raise PublicReadFailure("public response retained body hash mismatch")
    try:
        reparsed = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublicReadFailure("public response retained body is not valid JSON") from exc
    payload = response.get("payload")
    if not isinstance(reparsed, dict) or not isinstance(payload, dict) or reparsed != payload:
        raise PublicReadFailure("public response payload does not match retained raw body")
    return payload, observed_at


def paged_markets(*, getter: PublicGetter = get) -> dict[str, object]:
    """Read the complete KXHIGHCHI market set and filter active status only after pagination."""

    pages: list[dict[str, object]] = []
    cursor = ""
    seen: set[str] = set()
    while True:
        query = {"series_ticker": SERIES_TICKER, "limit": "1000"}
        if cursor:
            query["cursor"] = cursor
        page = getter(BASE + "/markets?" + urlencode(query))
        pages.append(page)
        if page.get("classification") != "SUCCESS":
            return {
                "classification": page.get("classification"),
                "pages": pages,
                "pagination_complete": False,
            }
        payload = page.get("payload")
        if not isinstance(payload, dict) or not isinstance(payload.get("markets"), list):
            raise PublicReadFailure("schema failure: markets array missing")
        next_cursor = payload.get("cursor")
        if next_cursor in (None, ""):
            market_count = 0
            total_returned = 0
            for page_item in pages:
                page_payload = page_item.get("payload")
                page_markets = (
                    page_payload.get("markets") if isinstance(page_payload, dict) else None
                )
                if isinstance(page_markets, list):
                    total_returned += len(page_markets)
                    market_count += sum(
                        1
                        for market in page_markets
                        if isinstance(market, dict) and market.get("status") == ACTIVE_MARKET_STATUS
                    )
            return {
                "classification": "SUCCESS",
                "pages": pages,
                "pagination_complete": True,
                "market_count": market_count,
                "total_returned": total_returned,
            }
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen:
            raise PublicReadFailure("pagination incomplete: missing or repeated cursor")
        seen.add(next_cursor)
        cursor = next_cursor


def acquire_public_acceptance(
    *,
    getter: PublicGetter = get,
    clock: Clock = lambda: datetime.now(UTC),
) -> dict[str, object]:
    """Acquire one exact M27E evidence bundle with no credentials or mutation capability."""

    started_at = clock()
    if started_at.tzinfo is None or started_at.utcoffset() is None:
        raise PublicReadFailure("M27E acquisition clock must be timezone-aware")
    return {
        "schema": SCHEMA,
        "host": "https://" + HOST,
        "started_at": started_at.isoformat(),
        "exchange_status": getter(BASE + "/exchange/status"),
        "series": getter(BASE + "/series/" + SERIES_TICKER),
        "markets": paged_markets(getter=getter),
    }


def validate_public_acceptance(evidence: object) -> dict[str, object]:
    """Independently validate the complete persisted M27E acquisition bundle."""

    if not isinstance(evidence, dict):
        raise PublicReadFailure("M27E evidence is not an object")
    if evidence.get("schema") != SCHEMA or evidence.get("host") != "https://" + HOST:
        raise PublicReadFailure("M27E evidence schema or host mismatch")
    _parse_time(evidence.get("started_at"), field="M27E acquisition")
    validate_response_evidence(
        evidence.get("exchange_status"), expected_path=BASE + "/exchange/status"
    )
    series_payload, _series_observed = validate_response_evidence(
        evidence.get("series"), expected_path=BASE + "/series/" + SERIES_TICKER
    )
    series = series_payload.get("series")
    if not isinstance(series, dict) or series.get("ticker") != SERIES_TICKER:
        raise PublicReadFailure("M27E series identity mismatch")

    markets = evidence.get("markets")
    if (
        not isinstance(markets, dict)
        or markets.get("classification") != "SUCCESS"
        or markets.get("pagination_complete") is not True
        or not isinstance(markets.get("pages"), list)
    ):
        raise PublicReadFailure("M27E market discovery is incomplete")
    pages = markets["pages"]
    if not pages:
        raise PublicReadFailure("M27E market discovery has no retained page")
    for page in pages:
        payload, _observed = validate_response_evidence(page, market_page=True)
        if not isinstance(payload.get("markets"), list):
            raise PublicReadFailure("M27E market page is malformed")
    return evidence


def series_payload_and_observed_at(evidence: object) -> tuple[dict[str, Any], datetime]:
    validated = validate_public_acceptance(evidence)
    series_response = validated["series"]
    payload, observed_at = validate_response_evidence(
        series_response, expected_path=BASE + "/series/" + SERIES_TICKER
    )
    raw = payload.get("series")
    if not isinstance(raw, dict):
        raise PublicReadFailure("M27E series payload is malformed")
    return raw, observed_at


def active_market_payloads(evidence: object) -> tuple[dict[str, Any], ...]:
    """Extract active markets only from a complete independently validated M27E bundle."""

    validated = validate_public_acceptance(evidence)
    markets = validated["markets"]
    assert isinstance(markets, dict)
    pages = markets["pages"]
    assert isinstance(pages, list)

    active: dict[str, dict[str, Any]] = {}
    for page in pages:
        payload, _observed = validate_response_evidence(page, market_page=True)
        rows = payload.get("markets")
        assert isinstance(rows, list)
        for raw in rows:
            if not isinstance(raw, dict) or raw.get("status") != ACTIVE_MARKET_STATUS:
                continue
            ticker = raw.get("ticker")
            if not isinstance(ticker, str) or not ticker:
                raise PublicReadFailure("active M27E market is missing ticker")
            if ticker in active:
                raise PublicReadFailure("active M27E market ticker appeared more than once")
            active[ticker] = raw
    return tuple(active[ticker] for ticker in sorted(active))
