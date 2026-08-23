"""Reusable M27E bounded unauthenticated production-read acceptance producer.

This is the service-layer owner of the exact evidence schema historically emitted by
``scripts/m27e_public_read_acceptance.py``. The script remains a thin operator CLI; M27R may now
reuse the same producer without violating the repository's ``services`` -> ``scripts`` dependency
boundary.

Only the shared :mod:`services.market_universe.public_read` GET transport is reachable: fixed
production origin, no credentials, no Authorization header, bounded response, no redirects, and
no mutation method.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from .public_read import BASE, HOST, PublicReadFailure, get

SCHEMA = "kalsh3.m27e.public-read.v1"
SERIES_TICKER = "KXHIGHCHI"
ACTIVE_MARKET_STATUS = "active"

PublicGetter = Callable[[str], dict[str, object]]
Clock = Callable[[], datetime]


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


def active_market_payloads(evidence: object) -> tuple[dict[str, Any], ...]:
    """Extract active market payloads only from a complete, exact M27E acquisition bundle.

    This is a discovery helper, not a substitute for M27I's independent consumption-time gate.
    M27I still receives the persisted bundle and independently validates exchange/trading/market
    currentness. Here we merely refuse to build M27R candidates from a partial or malformed
    discovery run.
    """

    if not isinstance(evidence, dict):
        raise PublicReadFailure("M27E evidence is not an object")
    if evidence.get("schema") != SCHEMA or evidence.get("host") != "https://" + HOST:
        raise PublicReadFailure("M27E evidence schema or host mismatch")
    markets = evidence.get("markets")
    if (
        not isinstance(markets, dict)
        or markets.get("classification") != "SUCCESS"
        or markets.get("pagination_complete") is not True
        or not isinstance(markets.get("pages"), list)
    ):
        raise PublicReadFailure("M27E market discovery is incomplete")

    active: dict[str, dict[str, Any]] = {}
    for page in markets["pages"]:
        if not isinstance(page, dict) or page.get("classification") != "SUCCESS":
            raise PublicReadFailure("M27E market page did not succeed")
        payload = page.get("payload")
        rows = payload.get("markets") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise PublicReadFailure("M27E market page is malformed")
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
