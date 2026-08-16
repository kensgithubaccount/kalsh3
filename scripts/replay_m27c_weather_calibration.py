"""Replay captured M27C weather calibration evidence without network or writes."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.forecasting.weather_calibration import (
    CalibrationMeasurement,
    build_residuals,
    parse_ghcnd_daily,
    parse_ndfd_descriptor,
    parse_ndfd_point_csv,
)
from services.forecasting.weather_source_authority import (
    PHYSICAL_WEATHER_SOURCES,
    parse_nws_station,
)


def replay(args: argparse.Namespace) -> dict[str, object]:
    now = datetime.now(UTC)
    source = PHYSICAL_WEATHER_SOURCES.get(args.source)
    if source is None:
        raise ValueError("source is not a reviewed physical weather source")
    measurement = CalibrationMeasurement(args.measurement)
    station = parse_nws_station(source, json.loads(args.nws_station.read_text()), now)
    descriptor = parse_ndfd_descriptor(args.descriptor.read_bytes(), source, measurement, now)
    point = parse_ndfd_point_csv(args.point_csv.read_bytes(), descriptor, station, now)
    outcome = parse_ghcnd_daily(args.ghcnd_dly.read_bytes(), source, now)
    residuals = build_residuals(source, descriptor, point, outcome, station, source, now)
    target_dates = {
        row.valid_time_coordinate.astimezone(ZoneInfo(source.timezone)).date() for row in point.rows
    }
    usable_dates = {
        row.local_date
        for row in outcome.observations
        if row.measurement is measurement and row.usable
    }
    excluded = sorted(str(value) for value in target_dates - usable_dates)
    return {
        "source": source.settlement_product_id,
        "nws_station_id": source.nws_station_id,
        "ghcnd_station_id": source.ghcnd_station_id,
        "authority_identity": source.authority_identity,
        "descriptor_hash": descriptor.source_hash,
        "csv_hash": point.source_hash,
        "ghcnd_hash": outcome.source_hash,
        "forecast_reference_time": descriptor.forecast_reference_time,
        "valid_time_rows": [
            {
                "coordinate": row.valid_time_coordinate,
                "target_local_date": row.valid_time_coordinate.astimezone(
                    ZoneInfo(source.timezone)
                ).date(),
                "forecast_kelvin": row.forecast_kelvin,
                "forecast_degF": row.forecast_deg_f,
            }
            for row in point.rows
        ],
        "residual_rows": [
            {
                "target_local_date": row.local_target_date,
                "observed_degF": row.observed_deg_f,
                "residual_degF": row.residual_deg_f,
                "lead_seconds": row.lead_to_valid_coordinate_seconds,
            }
            for row in residuals
        ],
        "excluded_or_missing_outcomes": excluded,
        "replay_fidelity": "FORECAST_VINTAGED_CURRENT_OUTCOME_SNAPSHOT",
        "research_only": True,
        "production_influence": "0",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline M27C forecast-vintaged weather replay")
    parser.add_argument("--source", required=True)
    parser.add_argument(
        "--measurement", required=True, choices=[item.value for item in CalibrationMeasurement]
    )
    parser.add_argument("--descriptor", type=Path, required=True)
    parser.add_argument("--point-csv", type=Path, required=True)
    parser.add_argument("--nws-station", type=Path, required=True)
    parser.add_argument("--ghcnd-dly", type=Path, required=True)
    print(json.dumps(replay(parser.parse_args()), indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
