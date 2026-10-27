#!/usr/bin/env python3
"""Run ONLY the independent research-only Reuters raw acquisition P12 lane."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from services.forecasting.cpi_p12_decoupled_collection import (
    P12CollectorStage,
    P12ExpectedStageFailure,
    P12IsolationPaths,
    StageCollection,
    run_independent_stage,
    write_bytes_durable_exclusive,
)
from services.forecasting.cpi_p12_reuters_raw_acquisition import (
    P12ReutersRawAcquisitionError,
    capture_reuters_raw_sources,
)


def _timestamp(text: str) -> datetime:
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ISO-8601 timestamp required") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise argparse.ArgumentTypeError("timezone-aware timestamp required")
    return value.astimezone(UTC)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--source-url", action="append", required=True)
    p.add_argument("--window-start", type=_timestamp, required=True)
    p.add_argument("--deadline", type=_timestamp, required=True)
    p.add_argument("--research-only", action="store_true")
    return p


def _write_once(path: Path, content: bytes) -> None:
    """Publish Reuters bytes only after the shared P12 durability barrier."""
    write_bytes_durable_exclusive(path, content)


def main() -> int:
    args = _parser().parse_args()
    if not args.research_only:
        raise SystemExit("BLOCKER: --research-only acknowledgement required")
    paths = P12IsolationPaths(args.root)
    output = args.root / "reuters" / "raw_source_observations.json"

    def collect() -> StageCollection:
        if output.exists():
            raise P12ExpectedStageFailure("Reuters artifact exists without a confirmed stage receipt")
        try:
            evidence = capture_reuters_raw_sources(
                source_urls=tuple(args.source_url),
                window_start=args.window_start,
                deadline=args.deadline,
            )
        except P12ReutersRawAcquisitionError as exc:
            raise P12ExpectedStageFailure("Reuters raw acquisition failed") from exc
        content = (json.dumps(evidence, indent=2, sort_keys=True) + "\n").encode("utf-8")
        try:
            _write_once(output, content)
        except OSError as exc:
            raise P12ExpectedStageFailure("Reuters evidence persistence failed") from exc
        return StageCollection(artifacts=(output,))

    try:
        receipt = run_independent_stage(
            paths,
            P12CollectorStage.REUTERS_ACQUISITION,
            collect,
        )
    except OSError:
        # The filesystem cannot provide a durable stage receipt. Never claim
        # PASS or pretend that a FAILURE receipt was actually committed.
        print(
            json.dumps(
                {
                    "stage": P12CollectorStage.REUTERS_ACQUISITION.value,
                    "status": "PERSISTENCE_UNCONFIRMED",
                    "stage_receipt_durable": False,
                    "scientific_evidence_authorized": False,
                    "p12_frozen": False,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 4
    print(
        json.dumps(
            {
                "stage": receipt["stage"],
                "status": receipt["status"],
                "stage_receipt": str(paths.stage_receipt(P12CollectorStage.REUTERS_ACQUISITION)),
                "scientific_evidence_authorized": False,
                "p12_frozen": False,
            },
            sort_keys=True,
        )
    )
    return 0 if receipt["status"] == "PASS" else 4


if __name__ == "__main__":
    raise SystemExit(main())
