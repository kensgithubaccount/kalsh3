"""Fetch and hash the current official Kalshi API pages used by M27E."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import ssl
from datetime import UTC, datetime
from pathlib import Path

HOST = "docs.kalshi.com"
PAGES = {
    "exchange_status": "/api-reference/exchange/get-exchange-status",
    "markets": "/api-reference/market/get-markets",
    "events": "/api-reference/events/get-event",
    "orderbook": "/api-reference/market/get-market-orderbook",
    "balance": "/api-reference/portfolio/get-balance",
    "orders": "/api-reference/orders/get-orders",
    "positions": "/api-reference/portfolio/get-positions",
    "fills": "/api-reference/portfolio/get-fills",
    "create_v2": "/api-reference/orders/create-order-v2",
    "cancel_v2": "/api-reference/orders/cancel-order-v2",
    "amend_v2": "/api-reference/orders/amend-order-v2",
    "decrease_v2": "/api-reference/orders/decrease-order-v2",
    "authenticated_signing": "/getting_started/quick_start_authenticated_requests",
}


def fetch(path: str) -> tuple[int, bytes]:
    connection = http.client.HTTPSConnection(HOST, timeout=15, context=ssl.create_default_context())
    try:
        connection.request("GET", path, headers={"Accept": "text/html"})
        response = connection.getresponse()
        return response.status, response.read(8_000_001)
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    pages: dict[str, object] = {}
    for name, path in PAGES.items():
        status, body = fetch(path)
        pages[name] = {
            "url": "https://" + HOST + path,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "status": status,
            "sha256": hashlib.sha256(body).hexdigest(),
            "bytes": len(body),
        }
    result = {
        "schema": "kalsh3.m27e.official-api-compatibility.v1",
        "production_base": "https://external-api.kalshi.com/trade-api/v2",
        "signing": "timestamp + HTTP_METHOD + full path without query string",
        "typed_operations": {
            "create": "POST /trade-api/v2/portfolio/events/orders",
            "cancel": "DELETE /trade-api/v2/portfolio/events/orders/{order_id}",
            "amend": "POST /trade-api/v2/portfolio/events/orders/{order_id}/amend",
            "decrease": "POST /trade-api/v2/portfolio/events/orders/{order_id}/decrease",
        },
        "pages": pages,
    }
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(hashlib.sha256(args.output.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
