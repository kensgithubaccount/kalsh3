#!/usr/bin/env python3
"""D1-G1 manual research capture: one Atlanta Fed GDPNow acquisition + receipt.

Research-only. This script never runs automatically (no CI job, no cron, no
scheduler references it) and it never calls Kalshi in any way. It performs
exactly one bounded HTTPS GET against the reviewed Atlanta Fed GDPNow
commentary page via `acquire_gdpnow_commentary_page`, deterministically parses
the newest entry via `parse_gdpnow_commentary`, and durably persists the exact
raw bytes plus an immutable, content-addressed receipt.

This produces GDPNow acquisition provenance only. It does not start any
scientific clock, evaluation boundary, or prospective confirmation, and it
establishes no trading authority. An operator runs this manually and reviews
the printed receipt before treating it as anything more than a raw capture.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from services.forecasting.gdpnow_parsing import (
    build_gdpnow_capture_receipt,
    parse_gdpnow_commentary,
    write_gdpnow_capture_receipt,
)
from services.forecasting.gdpnow_source_acquisition import acquire_gdpnow_commentary_page


def capture(output: Path) -> Path:
    evidence = acquire_gdpnow_commentary_page()
    vintage = parse_gdpnow_commentary(evidence)
    receipt = build_gdpnow_capture_receipt(evidence, vintage)
    receipt_path = write_gdpnow_capture_receipt(evidence, vintage, directory=output)
    print(
        json.dumps(
            {
                "receipt_path": str(receipt_path),
                "receipt_id": receipt.receipt_id,
                "target_quarter": receipt.target_quarter,
                "gdpnow_value": receipt.gdpnow_value,
                "publisher_stated_date": receipt.publisher_stated_date,
                "acquired_at": receipt.acquired_at,
                "research_only": receipt.research_only,
                "production_influence": receipt.production_influence,
            },
            sort_keys=True,
        )
    )
    return receipt_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("state/d1_g1_gdpnow_research_captures"),
        help="Directory for content-addressed raw bytes and receipts.",
    )
    capture(parser.parse_args().output)
