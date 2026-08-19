"""Bounded unauthenticated Kalshi production read acceptance for M27E.

Thin CLI/operator wrapper only: the reusable bounded public GET transport itself lives in
:mod:`services.market_universe.public_read` (fixed origin, bounded size, no redirects, GET-only)
and is shared with M27J's authoritative market-snapshot acquisition -- this script owns only the
M27E-specific business logic (exchange status / series / paginated open-markets acceptance) and
the CLI entrypoint that writes the evidence file. Dependency direction is ``scripts -> services``
only; no ``services`` module ever imports from ``scripts``.

Only fixed public GET paths are reachable.  The output is an immutable, secret-free
JSON evidence bundle suitable for the M27D shadow input review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

from services.market_universe.public_read import (
    BASE,
    HOST,
    MAX_RESPONSE_BYTES,
    PublicReadFailure,
    get,
    get_market,
    get_market_with_body,
)

__all__ = [
    "BASE",
    "HOST",
    "MAX_RESPONSE_BYTES",
    "PublicReadFailure",
    "get",
    "get_market",
    "get_market_with_body",
    "main",
    "paged_markets",
]


def paged_markets() -> dict[str, object]:
    pages: list[dict[str, object]] = []
    cursor = ""
    seen: set[str] = set()
    while True:
        query = {"series_ticker": "CLIMDW", "status": "open", "limit": "1000"}
        if cursor:
            query["cursor"] = cursor
        page = get(BASE + "/markets?" + urlencode(query))
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
            for page_item in pages:
                page_payload = page_item.get("payload")
                page_markets = (
                    page_payload.get("markets") if isinstance(page_payload, dict) else None
                )
                if isinstance(page_markets, list):
                    market_count += len(page_markets)
            return {
                "classification": "SUCCESS",
                "pages": pages,
                "pagination_complete": True,
                "market_count": market_count,
            }
        if not isinstance(next_cursor, str) or next_cursor in seen:
            raise PublicReadFailure("pagination incomplete: missing or repeated cursor")
        seen.add(next_cursor)
        cursor = next_cursor


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        evidence = {
            "schema": "kalsh3.m27e.public-read.v1",
            "host": "https://" + HOST,
            "started_at": datetime.now(UTC).isoformat(),
            "exchange_status": get(BASE + "/exchange/status"),
            "series": get(BASE + "/series/CLIMDW"),
            "markets": paged_markets(),
        }
    except PublicReadFailure as exc:
        print(f"PUBLIC_READ_FAILED: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, sort_keys=True, indent=2) + "\n")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
