"""M27M operator-only prospective capture archival CLI.

Given the path to an already-produced M27L prospective bundle JSON file
(the exact output of `scripts/capture_m27l_prospective_forecast.py`), this
script archives it immutably: `bundles/<sha256>.json` holds the exact
validated bundle bytes, `receipts/<receipt_id>.json` holds canonical
metadata derived only after the frozen M27L `deserialize_prospective_bundle`
succeeds, and `cycles/<reference-cycle>.json` is the create-only COMMIT
POINT published last -- exactly one accepted capture per forecast reference
cycle, ever.

Exact re-registration of byte-identical bundle bytes is idempotent. A
different valid bundle for a reference cycle that already has a published
cycle file fails closed, and never overwrites or deletes anything already
archived. This script never performs network I/O, never reads GHCN-Daily
result data, and never computes a residual, evaluation metric, or
market/price value -- all archival logic lives in
`services.forecasting.weather_prospective_operations`, which this script
only calls.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.forecasting.domain import ForecastError
from services.forecasting.weather_prospective_operations import register_prospective_capture


def register(args: argparse.Namespace) -> dict[str, Any]:
    raw_bytes = args.bundle.read_bytes()
    result = register_prospective_capture(raw_bytes, args.archive_root)
    return result.to_dict()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Operator-only M27M prospective capture archival. Archives an "
            "already-validated M27L bundle file into a local immutable "
            "archive. No network, no GHCN results, no evaluation, no "
            "market/risk/execution access, no access to Kalshi account "
            "secrets."
        )
    )
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = register(args)
    except ForecastError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(payload, sort_keys=True, indent=2, default=str))


if __name__ == "__main__":
    main()
