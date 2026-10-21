"""Operator CLI for research-only standalone P12 Kalshi CPI inventory capture.

Never invokes BLS, Reuters or P11 preflight. Not the release-day price collector.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from services.forecasting.cpi_p12_kalshi_inventory import (
    P12KalshiCaptureError,
    capture_kalshi_cpi_inventory,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-ticker", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--research-only",
        action="store_true",
        help="Acknowledge that no event is frozen and this does not grant scientific authority",
    )
    return parser


def _write_create_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(fd)


def main() -> int:
    args = _parser().parse_args()
    if not args.research_only:
        raise SystemExit("BLOCKER: --research-only acknowledgement is required")
    if args.output.exists():
        raise SystemExit("BLOCKER: refusing to overwrite existing P12 capture")

    try:
        record = capture_kalshi_cpi_inventory(args.event_ticker)
    except P12KalshiCaptureError:
        print('{"status":"FAILED_CLOSED","stage":"KALSHI_CPI_INVENTORY_RAW"}')
        return 4

    payload = (json.dumps(record, sort_keys=True, indent=2) + "\n").encode("utf-8")
    try:
        _write_create_once(args.output, payload)
    except FileExistsError:
        raise SystemExit("BLOCKER: another process already created the receipt") from None

    print(
        json.dumps(
            {
                "status": "CAPTURED_RESEARCH_ONLY",
                "event_ticker": record["requested_event_ticker"],
                "market_row_count": record["market_row_count"],
                "output": str(args.output),
                "scientific_evidence_authorized": False,
                "p12_frozen": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
