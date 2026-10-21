#!/usr/bin/env python3
"""Run only the Kalshi P12 research snapshot lane, independent of BLS/Reuters."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from services.forecasting.cpi_p12_decoupled_collection import (
    P12CollectorStage,
    P12ExpectedStageFailure,
    P12IsolationPaths,
    StageCollection,
    run_independent_stage,
)
from services.forecasting.cpi_p12_raw_market_snapshots import (
    P12SnapshotCaptureError,
    capture_kalshi_raw_market_snapshots,
)


def _timestamp(text: str) -> datetime:
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timestamp must be ISO-8601") from exc
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include timezone")
    return stamp.astimezone(UTC)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--event-ticker", required=True)
    parser.add_argument("--market-ticker", required=True, action="append")
    parser.add_argument("--window-start", required=True, type=_timestamp)
    parser.add_argument("--deadline", required=True, type=_timestamp)
    parser.add_argument("--research-only", action="store_true")
    return parser


def _write_create_once(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(fd)


def main() -> int:
    args = _parser().parse_args()
    if not args.research_only:
        raise SystemExit("BLOCKER: --research-only acknowledgement required")

    paths = P12IsolationPaths(args.root)
    output = args.root / "kalshi" / "raw_market_snapshots.json"

    def collect() -> StageCollection:
        try:
            capture = capture_kalshi_raw_market_snapshots(
                event_ticker=args.event_ticker,
                market_tickers=tuple(args.market_ticker),
                window_start=args.window_start,
                deadline=args.deadline,
            )
        except P12SnapshotCaptureError as exc:
            raise P12ExpectedStageFailure("Kalshi research snapshot failed") from exc
        payload = (json.dumps(capture, sort_keys=True, indent=2) + "\n").encode("utf-8")
        try:
            _write_create_once(output, payload)
        except FileExistsError as exc:
            raise P12ExpectedStageFailure("Kalshi snapshot already exists") from exc
        return StageCollection(artifacts=(output,))

    receipt = run_independent_stage(paths, P12CollectorStage.KALSHI_MARKET_CAPTURE, collect)
    print(
        json.dumps(
            {
                "stage": receipt["stage"],
                "status": receipt["status"],
                "stage_receipt": str(paths.stage_receipt(P12CollectorStage.KALSHI_MARKET_CAPTURE)),
                "scientific_evidence_authorized": False,
                "p12_frozen": False,
            },
            sort_keys=True,
        )
    )
    return 0 if receipt["status"] == "PASS" else 4


if __name__ == "__main__":
    raise SystemExit(main())
