#!/usr/bin/env python3
"""Dormant M28B fixed-origin public acquisition runner.

The runner is public GET-only software. It is not executed by tests or CI and grants no
standing authorization to contact Kalshi. When separately authorized by an operator, it
requires one reviewed series scope and acquires both archived and recent-settled partitions.
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

from services.historical_replay.archive import stable_hash
from services.historical_replay.client import HistoricalClient, HistoricalError
from services.production_weather_strategy.settlement_dataset import (
    _PAGE_EVIDENCE_CAPABILITY,
    ACQUISITION_SCHEMA,
    PUBLIC_KALSHI_ORIGIN,
    AcquisitionBoundMarketRow,
    HistoricalWeatherDatasetError,
    PublicPageEvidence,
    WeatherSettlementDataset,
    build_evidence_bound_weather_dataset,
)

ORIGIN = PUBLIC_KALSHI_ORIGIN
ALLOWED_PREFIXES = (
    "/trade-api/v2/historical/",
    "/trade-api/v2/markets?",
)
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
SCHEMA = ACQUISITION_SCHEMA


class PublicHistoricalTransport:
    """Fixed-origin unauthenticated JSON GET transport with per-page evidence."""

    def __init__(self, *, series_ticker: str) -> None:
        if not series_ticker.strip():
            raise ValueError("reviewed series scope is required")
        self.series_ticker = series_ticker.strip()
        self.requests: list[dict[str, object]] = []
        self._market_pages: dict[tuple[str, str], PublicPageEvidence] = {}

    def _issue_page_evidence(self, path: str, body: bytes) -> PublicPageEvidence:
        """Issue evidence only while processing a response observed by this transport."""
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("M28B public response was not JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("markets"), list):
            raise RuntimeError("M28B public response did not contain a market page")
        raw_markets = payload["markets"]
        if any(not isinstance(row, dict) for row in raw_markets):
            raise RuntimeError("M28B public response market row was malformed")
        return PublicPageEvidence(
            request_path=path,
            response_sha256=hashlib.sha256(body).hexdigest(),
            page_number=len(self.requests),
            scope_series_ticker=self.series_ticker,
            market_row_hashes=tuple(stable_hash(row) for row in raw_markets),
            _capability=_PAGE_EVIDENCE_CAPABILITY,
        )

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
            target, method="GET", headers={"Accept": "application/json"}
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
        request_record = {
            "path": path,
            "status": status,
            "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
        }
        self.requests.append(request_record)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("M28B public response was not JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("M28B public response root was not an object")
        if status == 200 and isinstance(payload.get("markets"), list):
            page = self._issue_page_evidence(path, body)
            for row in payload["markets"]:
                if isinstance(row, dict) and isinstance(row.get("ticker"), str):
                    key = (row["ticker"], stable_hash(row))
                    if key in self._market_pages:
                        raise RuntimeError(
                            "M28B public acquisition repeated identical market evidence"
                        )
                    self._market_pages[key] = page
        return status, payload

    def bind(self, row: Mapping[str, Any]) -> AcquisitionBoundMarketRow:
        ticker = row.get("ticker")
        if not isinstance(ticker, str):
            raise RuntimeError("M28B acquired row ticker is missing")
        page = self._market_pages.get((ticker, stable_hash(row)))
        if page is None:
            raise RuntimeError("M28B market row lacks exact acquired-page evidence")
        return AcquisitionBoundMarketRow.from_page(row, page)


def _jsonable(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()  # type: ignore[union-attr]
    if hasattr(value, "value"):
        return value.value  # type: ignore[union-attr]
    return str(value)


def _dataset_payload(dataset: WeatherSettlementDataset) -> dict[str, object]:
    return asdict(dataset)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--series-ticker", required=True)
    args = parser.parse_args()

    output = args.output.expanduser().resolve()
    if output.exists():
        print("BLOCKER: output already exists; M28B evidence is create-only", file=sys.stderr)
        return 20
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    transport = PublicHistoricalTransport(series_ticker=args.series_ticker)
    client = HistoricalClient(transport, signer=None, timeout=20)
    acquired_at = datetime.now(UTC)
    archive_rows: list[dict[str, Any]] = []
    recent_rows: list[dict[str, Any]] = []
    try:
        archive_rows = client.markets(series_ticker=args.series_ticker)
        recent_rows = client.recent_settled_markets(series_ticker=args.series_ticker)
        rows = archive_rows + recent_rows
        bound_rows = tuple(transport.bind(row) for row in rows)
        dataset = build_evidence_bound_weather_dataset(bound_rows)
    except (HistoricalError, HistoricalWeatherDatasetError, RuntimeError, ValueError) as exc:
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
        "classification": "SUCCESS_SERIES_SCOPED_EVIDENCE",
        "acquired_at": acquired_at.isoformat(),
        "api_origin": ORIGIN,
        "request_type": "PUBLIC_GET_ONLY",
        "series_scope": args.series_ticker,
        "coverage_claim": dataset.coverage_claim,
        "complete_total_count_proven": False,
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
    print("M28B_CLASSIFICATION=SUCCESS_SERIES_SCOPED_EVIDENCE")
    print(f"OUTPUT={output}")
    print(f"OUTPUT_SHA256={evidence_hash}")
    print(f"SERIES_SCOPE={args.series_ticker}")
    print(f"ARCHIVED_MARKETS={len(archive_rows)}")
    print(f"RECENT_SETTLED_MARKETS={len(recent_rows)}")
    print(f"DATASET_ID={dataset.dataset_id}")
    print("COMPLETE_TOTAL_COUNT_PROVEN=NO")
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
