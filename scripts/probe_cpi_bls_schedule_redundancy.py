#!/usr/bin/env python3
"""Run the research-only CPI BLS schedule path-redundancy probe."""

from __future__ import annotations

import argparse
import json
import re
import socket
from datetime import datetime
from pathlib import Path

from services.forecasting.cpi_bls_schedule_redundancy import (
    BLSScheduleTarget,
    probe_bls_schedule_redundancy,
)

_REFERENCE_RE = re.compile(r"^(?P<year>\d{4})-(?P<month>\d{2})$")


def _parse_reference_month(value: str) -> tuple[int, int]:
    match = _REFERENCE_RE.fullmatch(value)
    if match is None:
        raise argparse.ArgumentTypeError("reference month must be YYYY-MM")
    year = int(match.group("year"))
    month = int(match.group("month"))
    if month < 1 or month > 12:
        raise argparse.ArgumentTypeError("reference month must be YYYY-MM")
    return year, month


def _parse_release_at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("release time must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("release time must include a timezone")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe redundant first-party BLS CPI schedule paths (diagnostic only)"
    )
    parser.add_argument("--reference-month", required=True, type=_parse_reference_month)
    parser.add_argument("--release-at", required=True, type=_parse_release_at)
    parser.add_argument("--collector-label", default=socket.gethostname())
    parser.add_argument("--require-valid-paths", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    year, month = args.reference_month
    target = BLSScheduleTarget(
        reference_year=year,
        reference_month=month,
        release_at_utc=args.release_at,
    )
    report = probe_bls_schedule_redundancy(
        target,
        collector_label=args.collector_label,
        required_valid_paths=args.require_valid_paths,
        timeout_seconds=args.timeout_seconds,
    )
    payload = json.dumps(report.as_dict(), sort_keys=True, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report.path_redundancy_ready else 4


if __name__ == "__main__":
    raise SystemExit(main())
