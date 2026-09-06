"""Launch the canonical A0.2 prospective collection composition."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path

# Direct script execution places ``scripts/`` first on sys.path; bind imports to this checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_m27_current_weather_evidence import compose as compose_weather
from services.prospective_shadow.kernel import ShadowObservationStore, validate_start_receipt
from services.prospective_shadow.runner import DEFAULT_CADENCE_SECONDS, run_forever, run_once


def _weather_acquirer(day: date) -> Mapping[str, object] | None:
    result = compose_weather(day, transport=lambda url: _weather_transport(url))
    if result.get("classification") != "SUCCESS":
        return None
    records = result.get("records")
    if not isinstance(records, list):
        return None
    return next(
        (
            record
            for record in records
            if isinstance(record, dict) and record.get("target_date") == day.isoformat()
        ),
        None,
    )


def _weather_transport(url: str) -> bytes:
    from scripts.run_m27_current_weather_evidence import _public_transport

    return _public_transport(url)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=DEFAULT_CADENCE_SECONDS)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument(
        "--start-receipt", type=Path, default=Path("artifacts/a01/prospective_start.json")
    )
    args = parser.parse_args()
    receipt = json.loads(args.start_receipt.read_text())
    validate_start_receipt(receipt)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    store = ShadowObservationStore(args.store, receipt)
    kwargs = {
        "archive": args.archive,
        "store": store,
        "start_receipt": receipt,
        "weather_acquirer": _weather_acquirer,
    }
    if args.once:
        result = run_once(cycle_id=datetime.now(UTC).isoformat(), **kwargs)
        payload = {
            "refresh_complete": result.refresh_complete,
            "summary": {"weather": len(result.weather), "structural": len(result.structural)},
        }
        print(json.dumps(payload, sort_keys=True))
        return 0
    run_forever(interval_seconds=args.interval_seconds, **kwargs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
