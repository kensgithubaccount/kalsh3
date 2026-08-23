#!/usr/bin/env python3
"""Acquire public Kalshi settled markets and build M28B settlement evidence.

PUBLIC READ ONLY. Kalshi partitions settled markets between the historical archive and the
recent live market tier. This runner reads both partitions when a series is supplied, then
builds one settlement-authoritative dataset from the union.

It sends unauthenticated GET requests only to the fixed Kalshi public API origin. It never
loads credentials, reads account state, mutates production state, creates risk authority,
approves execution, or sends an order.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from services.historical_replay.client import HistoricalClient, HistoricalError
from services.production_weather_strategy.settlement_dataset import (
    AuthoritativeWeatherDataset,
    HistoricalWeatherDatasetError,
    build_authoritative_weather_dataset,
)

ORIGIN = "https://external-api.kalshi.com"
ALLOWED_PREFIXES = (
    "/trade-api/v2/historical/",
    "/trade-api/v2/markets?",
)
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
SCHEMA = "kalsh3.m28b.public-settled-weather-evidence.v2"


class PublicHistoricalTransport:
    """Fixed-origin unauthenticated JSON GET transport with response-hash evidence."""

    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def get(
        self, path: str, headers: Mapping[str, str], *, timeout_seconds: float
    ) -> tuple[int, dict[str, Any]]:
        if headers:
            raise RuntimeError("M28B public transport forbids authorization headers")
        if not any(path.startswith(prefix) for prefix in ALLOWED_PREFIXES):
            raise RuntimeError("M28B public transport path is outside reviewed prefixes")
        target = urljoin(ORIGIN, path)
        parsed = urlparse(target)
        expected = urlparse(ORIGIN)
        if (parsed.scheme, parsed.netloc) != (expected.scheme, expected.netloc):
            raise RuntimeError("M28B public transport escaped the fixed Kalshi origin")
        request = Request(  # noqa: S310
            target,
            method="GET",
            headers={"Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
                final = urlparse(response.geturl())
                if (final.scheme, final.netloc) != (expected.scheme, expected.netloc):
                    raise RuntimeError("M28B public redirect escaped fixed origin")
                status = int(response.status)
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            status = int(exc.code)
            body = exc.read(MAX_RESPONSE_BYTES + 1)
        except URLError as exc:
            raise RuntimeError("M28B public GET failed") from exc
        if len(body) > MAX_RESPONSE_BYTES:
            raise RuntimeError("M28B public response exceeded size bound")
        self.requests.append(
            {
                "path": path,
                "status": status,
                "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        )
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("M28B public response was not JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("M28B public response root was not an object")
        return status, payload


def _jsonable(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()  # type: ignore[union-attr]
    if hasattr(value, "value"):
        return value.value  # type: ignore[union-attr]
    return str(value)


def _dataset_payload(dataset: AuthoritativeWeatherDataset) -> dict[str, object]:
    return {
        "dataset_id": dataset.dataset_id,
        "parser_version": dataset.parser_version,
        "label_authority": dataset.label_authority,
        "event_count": dataset.event_count,
        "contract_count": dataset.contract_count,
        "skipped_unsupported_count": dataset.skipped_unsupported_count,
        "temporal_split_hash": dataset.temporal_split_hash,
        "train_event_ids": list(dataset.train_event_ids),
        "validation_event_ids": list(dataset.validation_event_ids),
        "test_event_ids": list(dataset.test_event_ids),
        "events": [asdict(event) for event in dataset.events],
        "contracts": [asdict(contract) for contract in dataset.contracts],
        "content_hash": dataset.content_hash,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--series-ticker")
    args = parser.parse_args()

    output = args.output.expanduser().resolve()
    if output.exists():
        print("BLOCKER: output already exists; M28B evidence is create-only", file=sys.stderr)
        return 20
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    transport = PublicHistoricalTransport()
    client = HistoricalClient(transport, signer=None, timeout=20)
    acquired_at = datetime.now(UTC)
    archive_rows: list[dict[str, Any]] = []
    recent_rows: list[dict[str, Any]] = []
    try:
        archive_rows = client.markets(series_ticker=args.series_ticker)
        if args.series_ticker is not None:
            recent_rows = client.recent_settled_markets(series_ticker=args.series_ticker)
        rows = archive_rows + recent_rows
        dataset = build_authoritative_weather_dataset(rows)
    except (HistoricalError, HistoricalWeatherDatasetError, RuntimeError) as exc:
        print(f"M28B_CLASSIFICATION=BLOCKED ({type(exc).__name__}: {exc})", file=sys.stderr)
        print(f"ARCHIVED_MARKETS={len(archive_rows)}")
        print(f"RECENT_SETTLED_MARKETS={len(recent_rows)}")
        print(f"NETWORK_REQUESTS={len(transport.requests)}")
        print("CREDENTIAL_ACCESS=NONE")
        print("ACCOUNT_GETS=NONE")
        print("MUTATION=NONE")
        print("ORDER_SENT=NO")
        return 21

    payload = {
        "schema": SCHEMA,
        "classification": "SUCCESS",
        "acquired_at": acquired_at.isoformat(),
        "api_origin": ORIGIN,
        "request_type": "PUBLIC_GET_ONLY",
        "series_filter": args.series_ticker,
        "archived_market_count": len(archive_rows),
        "recent_settled_market_count": len(recent_rows),
        "raw_market_count": len(rows),
        "network_request_count": len(transport.requests),
        "network_requests": transport.requests,
        "dataset": _dataset_payload(dataset),
        "credential_access": "NONE",
        "account_gets": "NONE",
        "production_state_mutation": "NONE",
        "risk_authorization": "NONE",
        "approval": "NONE",
        "execution_authorization": "NONE",
        "burn": "NONE",
        "order_sent": "NO",
    }
    encoded = json.dumps(payload, default=_jsonable, indent=2, sort_keys=True).encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(output, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.write(b"\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        with suppress(OSError):
            output.unlink()
        raise

    evidence_hash = hashlib.sha256(encoded + b"\n").hexdigest()
    print("M28B_CLASSIFICATION=SUCCESS")
    print(f"OUTPUT={output}")
    print(f"OUTPUT_SHA256={evidence_hash}")
    print(f"ARCHIVED_MARKETS={len(archive_rows)}")
    print(f"RECENT_SETTLED_MARKETS={len(recent_rows)}")
    print(f"RAW_MARKETS={len(rows)}")
    print(f"SUPPORTED_WEATHER_EVENTS={dataset.event_count}")
    print(f"SUPPORTED_WEATHER_CONTRACTS={dataset.contract_count}")
    print(f"SKIPPED_UNSUPPORTED={dataset.skipped_unsupported_count}")
    print(f"DATASET_ID={dataset.dataset_id}")
    print(f"NETWORK_REQUESTS={len(transport.requests)}")
    print("REQUEST_TYPE=PUBLIC_GET_ONLY")
    print("CREDENTIAL_ACCESS=NONE")
    print("ACCOUNT_GETS=NONE")
    print("PRODUCTION_STATE_MUTATION=NONE")
    print("RISK_AUTHORIZATION=NONE")
    print("APPROVAL=NONE")
    print("EXECUTION_AUTHORIZATION=NONE")
    print("BURN=NONE")
    print("ORDER_SENT=NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
