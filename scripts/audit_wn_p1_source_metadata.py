"""Read-only, historical WeatherNext Earth Engine image-METADATA feasibility.

This probe never reads forecast pixels, raw ensemble GCS, Kalshi, or settlement data.
Retrospective ingestion properties are not point-in-time availability evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

ASSET = "projects/gcp-public-data-weathernext/assets/weathernext_3_0_0_0p05deg"
CITY_HOURS = {
    "boston": (5, 28),
    "miami": (5, 28),
    "denver": (7, 30),
    "los_angeles": (8, 31),
    "seattle": (8, 31),
}
REQUIRED_BANDS = frozenset(
    f"station_head_temperature_2m_{x}" for x in ("mean", "p10", "p25", "p50", "p75", "p90")
)
POLICY_ID = "wn-p1-historical-ee-metadata-feasibility-v1"


class SourceMetadataError(ValueError):
    """Fail closed, without granting any model/probability/paper authority."""


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise SourceMetadataError("timestamp must be a UTC Z-string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SourceMetadataError("invalid ISO timestamp") from exc
    if parsed.utcoffset() != timedelta(0):
        raise SourceMetadataError("timestamp must be UTC")
    return parsed.astimezone(UTC)


def validate(target: date, metadata: dict[str, object]) -> dict[str, object]:
    """Five-city 24-hour completeness; no daily-maximum distribution constructed."""
    if type(target) is not date or target < date(2026, 9, 1):
        raise SourceMetadataError("target outside WeatherNext-3 historical reference scope")
    fields = {"forecast_hours", "start_times", "end_times", "ingestion_times", "band_names"}
    if type(metadata) is not dict or set(metadata) != fields:
        raise SourceMetadataError("unrecognized metadata fields")
    bands = metadata["band_names"]
    if not isinstance(bands, list) or not all(type(b) is str for b in bands):
        raise SourceMetadataError("band names must be strings")
    if len(bands) != len(set(bands)) or not REQUIRED_BANDS <= set(bands):
        raise SourceMetadataError("all six marginal band names required")
    arrays = [metadata[key] for key in ("forecast_hours", "start_times", "end_times", "ingestion_times")]
    if not all(isinstance(array, list) for array in arrays):
        raise SourceMetadataError("metadata arrays required")
    hours, starts, ends, ingestions = cast(
        tuple[list[object], list[object], list[object], list[object]], tuple(arrays)
    )
    if not (len(hours) == len(starts) == len(ends) == len(ingestions)):
        raise SourceMetadataError("metadata array length mismatch")
    if any(type(h) is not int or not 1 <= h <= 31 for h in hours):
        raise SourceMetadataError("unrecognized forecast hour")
    if len(hours) != len(set(hours)):
        raise SourceMetadataError("duplicate forecast hour")

    init = datetime.combine(target, datetime.min.time(), tzinfo=UTC)
    cutoff = init + timedelta(hours=9)
    stamps: dict[int, datetime] = {}
    for hour, start, end, ingested in zip(hours, starts, ends, ingestions, strict=True):
        h = cast(int, hour)
        if _timestamp(start) != init or _timestamp(end) != init + timedelta(hours=h):
            raise SourceMetadataError("forecast initialization or valid-hour mismatch")
        if type(ingested) not in (int, float):
            raise SourceMetadataError("invalid ingestion epoch type")
        epoch = cast(float, ingested)
        if not 0 < epoch < 5_000_000_000:
            raise SourceMetadataError("invalid ingestion epoch value")
        try:
            observed = datetime.fromtimestamp(epoch, UTC)
        except (ValueError, OverflowError, OSError) as exc:
            raise SourceMetadataError("ingestion epoch unparseable") from exc
        if observed < init:
            raise SourceMetadataError("ingestion precedes initialization")
        stamps[h] = observed

    cities: dict[str, dict[str, object]] = {}
    for city, (first, last) in CITY_HOURS.items():
        expected = set(range(first, last + 1))
        missing = sorted(expected - stamps.keys())
        late = sorted(h for h in expected & stamps.keys() if stamps[h] >= cutoff)
        cities[city] = {
            "status": (
                "HISTORICAL_METADATA_PASS_NOT_SOURCE_PROOF"
                if not missing and not late
                else "ABSTAIN_INCOMPLETE_OR_LATE"
            ),
            "expected_hours": 24,
            "present_hours": len(expected & stamps.keys()),
            "missing_hours": missing,
            "late_hours": late,
            "latest_ingestion_utc": (
                max(stamps[h] for h in expected).isoformat().replace("+00:00", "Z")
                if not missing
                else None
            ),
        }
    return {
        "record_type": "WN-P1-HISTORICAL-EE-METADATA-REFERENCE-v1",
        "policy_id": POLICY_ID,
        "target_date_utc": target.isoformat(),
        "source_asset": ASSET,
        "metadata_sha256": hashlib.sha256(
            json.dumps(metadata, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest(),
        "cities": cities,
        "status": "HISTORICAL_METADATA_REFERENCE_ONLY",
        "retrospective_ingestion_does_not_prove_live_availability": True,
        "hourly_marginals_not_joint_daily_max_distribution": True,
        "probability_authorized": False,
        "paper_signal_authorized": False,
        "trading_authority": "NONE",
        "research_only": True,
        "production_influence": "0",
    }


def acquire_metadata(project: str, target: date) -> dict[str, object]:
    """Single bounded EE metadata RPC, never raster pixels or raw ensemble GCS."""
    if not project or any(not (ch.isalnum() or ch in "-_") for ch in project):
        raise SourceMetadataError("explicit valid EE project required")
    try:
        import ee  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SourceMetadataError("Earth Engine Python client not installed") from exc
    ee.Initialize(project=project)
    init = datetime.combine(target, datetime.min.time(), tzinfo=UTC)
    images = (
        ee.ImageCollection(ASSET)
        .filter(ee.Filter.eq("start_time", init.isoformat().replace("+00:00", "Z")))
        .filter(ee.Filter.gte("forecast_hour", 1))
        .filter(ee.Filter.lte("forecast_hour", 31))
        .sort("forecast_hour")
    )
    raw: Any = ee.Dictionary(
        {
            "forecast_hours": images.aggregate_array("forecast_hour"),
            "start_times": images.aggregate_array("start_time"),
            "end_times": images.aggregate_array("end_time"),
            "ingestion_times": images.aggregate_array("ingestion_time_utc"),
            "band_names": images.first().bandNames(),
        }
    ).getInfo()
    if not isinstance(raw, dict):
        raise SourceMetadataError("Earth Engine response malformed")
    return cast(dict[str, object], raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical-target", type=date.fromisoformat, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--project", help="Historical metadata read through authorized EE")
    group.add_argument("--offline-json", help="Untrusted local fixture, not source proof")
    args = parser.parse_args(argv)
    if args.historical_target >= datetime.now(UTC).date():
        parser.error("today/future is not a historical metadata canary")
    try:
        if args.offline_json:
            raw: Any = json.loads(Path(args.offline_json).read_text())
        else:
            raw = acquire_metadata(args.project, args.historical_target)
        report = validate(args.historical_target, raw)
    except (SourceMetadataError, ValueError, OSError, RuntimeError) as exc:
        print(f"FAIL_CLOSED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    return (
        0
        if all(
            item["status"] == "HISTORICAL_METADATA_PASS_NOT_SOURCE_PROOF"
            for item in cast(dict[str, dict[str, object]], report["cities"]).values()
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
