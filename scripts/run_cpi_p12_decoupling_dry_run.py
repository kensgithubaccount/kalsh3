#!/usr/bin/env python3
"""Exercise P12 collector isolation without inspecting any target Reuters forecast.

This is a pre-freeze operational dry-run. Kalshi and Reuters artifacts are synthetic
sentinels used only to prove orchestration/failure isolation. BLS schedule authority,
when not fault-injected, is verified from an already sealed receipt with no network I/O.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from services.forecasting.cpi_p12_decoupled_collection import (
    P12CollectorStage,
    P12ExpectedStageFailure,
    P12IsolationPaths,
    SealedBLSScheduleExpectation,
    StageCollection,
    evaluate_preclose_completeness,
    run_all_independent_stages,
    verify_presealed_bls_schedule_receipt,
)


def _parse_release(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("release time must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("release time must include a timezone")
    return parsed.astimezone(UTC)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the P12 pre-freeze collector-isolation dry-run"
    )
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--schedule-receipt", required=True, type=Path)
    parser.add_argument("--reference-month", required=True)
    parser.add_argument("--release-at", required=True, type=_parse_release)
    parser.add_argument("--schedule-source-sha256", required=True)
    parser.add_argument("--schedule-receipt-sha256", required=True)
    parser.add_argument(
        "--fail-stage",
        choices=("none", "bls", "kalshi", "reuters"),
        default="none",
    )
    return parser


def _write_sentinel(root: Path, name: str) -> StageCollection:
    path = root / "dry_run_sentinels" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise RuntimeError(f"dry-run sentinel already exists: {path}")
    payload = {
        "record_type": "CPI-P12-DECOUPLING-DRY-RUN-SENTINEL-v1",
        "stage": name,
        "synthetic": True,
        "contains_target_forecast": False,
        "scientific_evidence_authorized": False,
        "p12_frozen": False,
        "production_influence": "0",
    }
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")
    return StageCollection(artifacts=(path,))


def main() -> int:
    args = _parser().parse_args()
    paths = P12IsolationPaths(args.root)
    expectation = SealedBLSScheduleExpectation(
        reference_month=args.reference_month,
        release_at_utc=args.release_at,
        source_sha256=args.schedule_source_sha256,
        receipt_sha256=args.schedule_receipt_sha256,
    )

    def bls() -> StageCollection:
        if args.fail_stage == "bls":
            raise P12ExpectedStageFailure("fault injection: BLS schedule stage")
        return verify_presealed_bls_schedule_receipt(args.schedule_receipt, expectation)

    def kalshi() -> StageCollection:
        if args.fail_stage == "kalshi":
            raise P12ExpectedStageFailure("fault injection: Kalshi stage")
        return _write_sentinel(paths.root, "kalshi_market_capture")

    def reuters() -> StageCollection:
        if args.fail_stage == "reuters":
            raise P12ExpectedStageFailure("fault injection: Reuters stage")
        return _write_sentinel(paths.root, "reuters_acquisition")

    outcomes = run_all_independent_stages(
        paths,
        {
            P12CollectorStage.BLS_SCHEDULE_AUTHORITY: bls,
            P12CollectorStage.KALSHI_MARKET_CAPTURE: kalshi,
            P12CollectorStage.REUTERS_ACQUISITION: reuters,
        },
    )
    gate = evaluate_preclose_completeness(paths)
    print(
        json.dumps(
            {
                "policy": "P12_PRE_FREEZE_DECOUPLING_DRY_RUN",
                "fail_stage": args.fail_stage,
                "stage_status": {
                    name: receipt["status"] for name, receipt in outcomes.items()
                },
                "gate": gate["decision"],
                "scientific_evidence_authorized": False,
                "p12_frozen": False,
                "production_influence": "0",
            },
            sort_keys=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
