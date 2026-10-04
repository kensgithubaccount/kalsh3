"""Read historical WeatherNext image properties only; never sample forecast pixels."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import ee  # type: ignore[import-not-found]

ROOT = Path(__file__).resolve().parent
PROTOCOL = json.loads((ROOT / "protocol.json").read_text())
DATES = tuple(date(2026, 9, 24) + timedelta(days=i) for i in range(7))
DECISION_MINUTES = 8 * 60 + 30
ASSET = PROTOCOL["weather_asset"]


def main() -> None:
    ee.Initialize(project="total-market-138523")
    dates = []
    for target in DATES:
        init = datetime(target.year, target.month, target.day, tzinfo=UTC)
        images = (
            ee.ImageCollection(ASSET)
            .filter(ee.Filter.eq("start_time", init.isoformat().replace("+00:00", "Z")))
            .sort("forecast_hour")
        )
        rows = images.aggregate_array("forecast_hour").getInfo()
        lags = images.aggregate_array("ingestion_time_utc").getInfo()
        starts = images.aggregate_array("start_time").getInfo()
        ends = images.aggregate_array("end_time").getInfo()
        if not (len(rows) == len(lags) == len(starts) == len(ends)):
            raise RuntimeError("METADATA_LENGTH_MISMATCH")
        stamps = {}
        for hour, stamp, start_time, end_time in zip(rows, lags, starts, ends, strict=True):
            hour = int(hour)
            if hour in stamps:
                raise RuntimeError("DUPLICATE_FORECAST_HOUR")
            expected_valid = init + timedelta(hours=hour)
            if (
                datetime.fromisoformat(start_time.replace("Z", "+00:00")) != init
                or datetime.fromisoformat(end_time.replace("Z", "+00:00")) != expected_valid
            ):
                raise RuntimeError("INIT_OR_VALID_TIME_MISMATCH")
            stamps[hour] = datetime.fromtimestamp(float(stamp), UTC)
        dates.append((target, init, stamps))
    cities: dict[str, list[dict[str, Any]]] = {}
    for city in PROTOCOL["cities"]:
        start, end = city["required_forecast_hours_inclusive"]
        expected = list(range(start, end + 1))
        records = []
        for target, init, stamps in dates:
            missing = sorted(set(expected) - set(stamps))
            if missing:
                records.append(
                    {"date": target.isoformat(), "complete": False, "missing_hours": missing}
                )
                continue
            latest = max(stamps[hour] for hour in expected)
            lag = (latest - init).total_seconds() / 60
            records.append(
                {
                    "date": target.isoformat(),
                    "complete": True,
                    "latest_required_ingestion_utc": latest.isoformat().replace("+00:00", "Z"),
                    "max_lag_minutes": round(lag, 3),
                    "margin_to_0830_minutes": round(DECISION_MINUTES - lag, 3),
                }
            )
        cities[city["city_id"]] = records
    result = {
        "record_type": "WN-MULTICITY-D1-WEATHER-METADATA-PREFLIGHT-v1",
        "query_scope": (
            "historical image properties only; no forecast bands, pixels, or current-date images"
        ),
        "observational_unit": "CITY_DAY",
        "city_days_independent": False,
        "correlation_groups_by_city": {
            city["city_id"]: {
                "geographic_group": city["geographic_group"],
                "weather_regime_group": city["weather_regime_group"],
            }
            for city in PROTOCOL["cities"]
        },
        "dates": [day.isoformat() for day in DATES],
        "cities": cities,
    }
    (ROOT / "weather_ingestion_preflight.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "dates_checked": len(DATES),
                "latest_lag_minutes_by_city": {
                    city: max(float(row["max_lag_minutes"]) for row in rows if row["complete"])
                    for city, rows in cities.items()
                },
                "minimum_0830_margin_minutes": min(
                    float(row["margin_to_0830_minutes"])
                    for rows in cities.values()
                    for row in rows
                    if row["complete"]
                ),
            }
        )
    )


if __name__ == "__main__":
    main()
