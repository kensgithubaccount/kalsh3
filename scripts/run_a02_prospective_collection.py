"""Launch the canonical A0.2 prospective collection composition."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

# Direct script execution places ``scripts/`` first on sys.path; bind imports to this checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.collect_m27c_weather_calibration_coverage import WGRIB2_VERSION, _resolve_wgrib2
from scripts.run_m27_current_weather_evidence import compose as compose_weather
from services.prospective_shadow.kernel import (
    ADAPTER_VERSIONS,
    KERNEL_VERSION,
    ShadowObservationStore,
    validate_start_receipt,
)
from services.prospective_shadow.runner import (
    DEFAULT_CADENCE_SECONDS,
    WeatherAcquisitionResult,
    run_forever,
    run_once,
)


def _weather_acquirer(day: date) -> WeatherAcquisitionResult:
    try:
        _resolve_wgrib2(None)
    except Exception:
        return WeatherAcquisitionResult(
            None,
            True,
            f"WEATHER_ACQUISITION_RUNTIME_DEPENDENCY_MISSING:wgrib2 {WGRIB2_VERSION} required",
        )
    result = compose_weather(day, transport=lambda url: _weather_transport(url))
    classification = result.get("classification")
    if classification == "EVALUATION_BLOCKED":
        return WeatherAcquisitionResult(
            None,
            True,
            f"WEATHER_ACQUISITION_EVALUATION_BLOCKED:{result.get('reason')}",
        )
    if classification != "SUCCESS":
        return WeatherAcquisitionResult(
            None, False, f"WEATHER_AUTHORITY_REJECTED:{classification}:{result.get('reason')}"
        )
    records = result.get("records")
    if not isinstance(records, list):
        return WeatherAcquisitionResult(None, False, "WEATHER_AUTHORITY_RECORDS_MISSING")
    evidence = next(
        (
            record
            for record in records
            if isinstance(record, dict) and record.get("target_date") == day.isoformat()
        ),
        None,
    )
    return WeatherAcquisitionResult(
        evidence, False, None if evidence is not None else "WEATHER_TARGET_DATE_UNAVAILABLE"
    )


def _weather_transport(url: str) -> bytes:
    from scripts.run_m27_current_weather_evidence import _public_transport

    return _public_transport(url)


def _runtime_identity() -> tuple[str, str]:
    root = Path(__file__).resolve().parents[1]
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git executable is required for runtime identity")
    sha = subprocess.run(  # noqa: S603 -- executable is shutil.which("git").
        [git, "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    tree = subprocess.run(  # noqa: S603 -- executable is shutil.which("git").
        [git, "rev-parse", "HEAD^{tree}"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return sha, tree


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
    runtime_sha, runtime_tree = _runtime_identity()
    kwargs = {
        "archive": args.archive,
        "store": store,
        "start_receipt": receipt,
        "weather_acquirer": _weather_acquirer,
    }
    if args.once:
        result = run_once(cycle_id=datetime.now(UTC).isoformat(), **kwargs)
        payload = {
            "schema": "kalshi.a02.prospective-cycle.v1",
            "runtime_main_sha": runtime_sha,
            "runtime_main_tree": runtime_tree,
            "kernel_version": KERNEL_VERSION,
            "adapter_versions": ADAPTER_VERSIONS,
            "start_receipt_digest": receipt["receipt_digest"],
            "cadence_seconds": args.interval_seconds,
            "run_dir": str(args.run_dir.resolve()),
            "store": str(args.store.resolve()),
            "source_identities": receipt["source_identities"],
            "research_only": True,
            "production_influence": "0",
            "complete": result.complete,
            "diagnostics": result.diagnostics,
            "summary": {"weather": len(result.weather), "structural": len(result.structural)},
        }
        (args.run_dir / f"cycle-{result.cycle_id}.json").write_text(
            json.dumps(payload, sort_keys=True, indent=2) + "\n"
        )
        print(json.dumps(payload, sort_keys=True))
        return 0 if result.complete else 2
    run_forever(interval_seconds=args.interval_seconds, **kwargs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
