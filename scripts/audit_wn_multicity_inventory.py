#!/usr/bin/env python3
"""Read-only WN multicity prospective inventory audit.

Example:
    gcloud storage ls --recursive "$BASE" |
        python scripts/audit_wn_multicity_inventory.py --as-of 2026-10-03

The script reads object names from stdin only. It performs no network calls and
does not acquire outcomes, mutate evidence, backfill, retry, or score P&L.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from services.forecasting.wn_multicity_evaluation_spec import audit_inventory


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", required=True, type=_parse_date)
    args = parser.parse_args()
    report = audit_inventory(sys.stdin, as_of=args.as_of)
    print(json.dumps(report.to_dict(), sort_keys=True, indent=2))
    return 0 if report.operationally_clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
