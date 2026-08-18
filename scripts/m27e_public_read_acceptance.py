"""Bounded unauthenticated Kalshi production read acceptance for M27E.

Only fixed public GET paths are reachable.  The output is an immutable, secret-free
JSON evidence bundle suitable for the M27D shadow input review.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import ssl
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

HOST = "external-api.kalshi.com"
BASE = "/trade-api/v2"
MAX_RESPONSE_BYTES = 8_000_000


class PublicReadFailure(RuntimeError):
    pass


def get(path: str) -> dict[str, object]:
    if not path.startswith(BASE + "/") or ".." in path.split("/") or "//" in path:
        raise PublicReadFailure("path is outside fixed public read authority")
    connection = http.client.HTTPSConnection(HOST, timeout=10, context=ssl.create_default_context())
    observed_at = datetime.now(UTC)
    try:
        connection.request("GET", path, headers={"Accept": "application/json"})
        response = connection.getresponse()
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise PublicReadFailure("response exceeded bounded size")
        evidence: dict[str, object] = {
            "path": path,
            "observed_at": observed_at.isoformat(),
            "status": response.status,
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "bytes": len(body),
        }
        if response.status != 200:
            evidence["classification"] = "HTTP_OR_NETWORK_FAILURE"
            evidence["body_preview"] = body[:200].decode("utf-8", errors="replace")
            return evidence
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PublicReadFailure(f"schema failure: invalid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise PublicReadFailure("schema failure: response is not an object")
        evidence["classification"] = "SUCCESS"
        evidence["payload"] = payload
        return evidence
    except (OSError, TimeoutError, http.client.HTTPException) as exc:
        raise PublicReadFailure(f"HTTP/network failure: {exc}") from exc
    finally:
        connection.close()


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
            return {
                "classification": "SUCCESS",
                "pages": pages,
                "pagination_complete": True,
                "market_count": sum(len(p["payload"]["markets"]) for p in pages),
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
