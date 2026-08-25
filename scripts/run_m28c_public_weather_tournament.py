#!/usr/bin/env python3
"""Dormant M28C public research runner with an explicit strict-NOAA fail-closed gate.

This file intentionally has no automatic or CI entrypoint beyond manual invocation. Current
NOAA/GHCN snapshots are replay-only under the canonical climate-evidence contract because the
repository has no reviewed issuer for historical source-vintage authority. Therefore manual
invocation stops before any public request or canonical tournament execution.

The offline helper below adapts market-checkpoint reconstruction to the current singular,
explicitly bounded ``HistoricalClient.candles`` interface for future separately reviewed use.
It never authenticates, signs, mutates state, or sends orders.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from services.historical_replay.archive import stable_hash
from services.historical_replay.client import HistoricalClient
from services.production_weather_strategy.model_tournament import MarketCheckpoint
from services.production_weather_strategy.settlement_dataset import ResolvedTemperatureContract

STRICT_NOAA_BLOCK_REASON = (
    "canonical strict historical climate evidence is unavailable: current NOAA/GHCN "
    "snapshots are REPLAY_ONLY without independently reviewed historical source-vintage evidence"
)


class RequestEvidenceTransport(Protocol):
    requests: list[dict[str, object]]


def _market_checkpoint(
    client: HistoricalClient,
    transport: RequestEvidenceTransport,
    contract: ResolvedTemperatureContract,
    checkpoint_at: datetime,
) -> MarketCheckpoint | None:
    """Build one checkpoint from the current bounded historical-candle client interface."""

    cutoff = checkpoint_at
    end_ts = int(cutoff.timestamp())
    start_ts = end_ts - 24 * 60 * 60
    before = len(transport.requests)
    candles = client.candles(
        contract.market_ticker,
        60,
        start_ts=start_ts,
        end_ts=end_ts,
    )
    if len(transport.requests) != before + 1:
        raise RuntimeError(
            "historical candle request did not produce one transport evidence record"
        )
    request = transport.requests[-1]
    path = request.get("path")
    response_sha = request.get("sha256")
    if not isinstance(path, str) or not isinstance(response_sha, str):
        raise RuntimeError("historical candle transport evidence is incomplete")
    response_evidence_id = stable_hash(("m28c-candle-response-v1", path, response_sha))
    return MarketCheckpoint.from_candles(
        market_ticker=contract.market_ticker,
        checkpoint_at=cutoff,
        candles=tuple(_as_mapping(row) for row in candles),
        response_evidence_id=response_evidence_id,
    )


def _as_mapping(row: Mapping[str, Any]) -> Mapping[str, object]:
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.parse_args()

    print(f"M28C_CLASSIFICATION=BLOCKED ({STRICT_NOAA_BLOCK_REASON})", file=sys.stderr)
    print("HISTORICAL_RESULT_CLASSIFICATION=HISTORICAL_REFERENCE")
    print("STRICT_HISTORICAL_NOAA_VINTAGE_EVIDENCE=UNAVAILABLE")
    print("PUBLIC_REQUESTS_EXECUTED=0")
    print("CREDENTIAL_ACCESS=NONE")
    print("ACCOUNT_GETS=NONE")
    print("SIGNER_INVOCATION=NONE")
    print("PRODUCTION_STATE_MUTATION=NONE")
    print("RISK_AUTHORIZATION=NONE")
    print("APPROVAL=NONE")
    print("EXECUTION_AUTHORIZATION=NONE")
    print("ARM=NONE")
    print("BURN=NONE")
    print("FINAL_ACKNOWLEDGEMENT=NONE")
    print("ORDER_SENT=NO")
    return 21


if __name__ == "__main__":
    raise SystemExit(main())
