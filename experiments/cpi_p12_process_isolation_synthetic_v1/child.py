"""Invented-only, separate-process P12 stage isolation rehearsal.

Never a scheduler, live collector, source locator, scientific validator, or
production runner. This module accepts no URLs, event identifiers, or forecasts.
Only run inside a directory explicitly named p12-process-synthetic-only.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from services.forecasting.cpi_p12_decoupled_collection import (
    P12CollectorStage,
    P12ExpectedStageFailure,
    P12IsolationPaths,
    StageCollection,
    run_independent_stage,
    write_bytes_durable_exclusive,
)

SYNTHETIC_ROOT_NAME = "p12-process-synthetic-only"
SYNTHETIC_RECORD_TYPE = "CPI-P12-INVENTED-SEPARATE-PROCESS-SENTINEL-v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--stage", required=True, choices=tuple(s.value for s in P12CollectorStage))
    parser.add_argument(
        "--mode", required=True, choices=("success", "expected-failure", "fatal-exit")
    )
    parser.add_argument("--synthetic-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    root: Path = args.root
    if (
        not args.synthetic_only
        or root.name != SYNTHETIC_ROOT_NAME
        or root.resolve().name != SYNTHETIC_ROOT_NAME
    ):
        parser.error("explicit synthetic-only acknowledgement and isolated root required")

    stage = P12CollectorStage(args.stage)
    mode: str = args.mode
    paths = P12IsolationPaths(root)

    def collect() -> StageCollection:
        if mode == "fatal-exit":
            # A process-level unhandled error must not prevent sibling processes.
            raise RuntimeError("invented fatal child-process failure")
        if mode == "expected-failure":
            raise P12ExpectedStageFailure("invented stage-local acquisition failure")
        artifact = root / "invented-stage-artifacts" / f"{stage.value}.json"
        payload = {
            "record_type": SYNTHETIC_RECORD_TYPE,
            "stage": stage.value,
            "synthetic_only": True,
            "child_pid": os.getpid(),
            "contains_target_data": False,
            "scientific_evidence_authorized": False,
            "phase1_economic_test_authorized": False,
            "p12_frozen": False,
            "production_influence": "0",
            "promotion_authority": "NONE",
            "trading_authority": "NONE",
        }
        write_bytes_durable_exclusive(
            artifact,
            (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8"),
        )
        return StageCollection(artifacts=(artifact,))

    receipt = run_independent_stage(paths, stage, collect)
    print(
        json.dumps(
            {
                "mode": "INVENTED_PROCESS_REHEARSAL_ONLY",
                "stage": stage.value,
                "status": receipt["status"],
                "synthetic_only": True,
                "scientific_evidence_authorized": False,
                "phase1_economic_test_authorized": False,
                "p12_frozen": False,
                "production_influence": "0",
                "promotion_authority": "NONE",
                "trading_authority": "NONE",
            },
            sort_keys=True,
        )
    )
    return 0 if receipt["status"] == "PASS" else 4


if __name__ == "__main__":
    raise SystemExit(main())
