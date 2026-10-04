"""Read only the 2026-09-27 historical 00Z p50 point rows for offline validation."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import ee  # type: ignore[import-not-found]

ROOT = Path(__file__).resolve().parent
PROTOCOL = json.loads((ROOT / "protocol.json").read_text())
TARGET = date(2026, 9, 27)
INIT = datetime(2026, 9, 27, tzinfo=UTC)
BAND = PROTOCOL["weather_variable"]


def make_sampler(station_point: Any, projection: Any) -> Callable[[Any], Any]:
    def sample(image: Any) -> Any:
        point = (
            image.select(BAND)
            .sample(region=station_point, projection=projection, numPixels=1, geometries=True)
            .first()
        )
        return ee.Feature(
            point.geometry(),
            image.toDictionary(
                ["start_time", "end_time", "forecast_hour", "ingestion_time_utc"]
            ).combine(point.toDictionary(), overwrite=True),
        )

    return sample


def main() -> None:
    ee.Initialize(project="total-market-138523")
    fixtures = {}
    for city in PROTOCOL["cities"]:
        first, last = city["required_forecast_hours_inclusive"]
        images = (
            ee.ImageCollection(PROTOCOL["weather_asset"])
            .filter(ee.Filter.eq("start_time", "2026-09-27T00:00:00Z"))
            .filter(ee.Filter.gte("forecast_hour", first))
            .filter(ee.Filter.lte("forecast_hour", last))
            .sort("forecast_hour")
        )
        station_point = ee.Geometry.Point(city["station_lonlat"])
        projection = ee.Image(images.first()).select(BAND).projection()

        features = (
            ee.FeatureCollection(images.map(make_sampler(station_point, projection)))
            .sort("forecast_hour")
            .getInfo()["features"]
        )
        rows = []
        for feature in features:
            props = feature["properties"]
            rows.append(
                {
                    "city_id": city["city_id"],
                    "station_icao": city["station_icao"],
                    "grid_lonlat": feature["geometry"]["coordinates"],
                    "init_time_utc": props["start_time"],
                    "valid_time_utc": props["end_time"],
                    "forecast_hour": int(props["forecast_hour"]),
                    "ingestion_time_utc": datetime.fromtimestamp(
                        float(props["ingestion_time_utc"]), UTC
                    )
                    .isoformat()
                    .replace("+00:00", "Z"),
                    BAND: props[BAND],
                }
            )
        fixtures[city["city_id"]] = rows
    result = {
        "record_type": "WN-MULTICITY-D1-HISTORICAL-POINT-FIXTURE-v1",
        "target_date": TARGET.isoformat(),
        "observational_unit": "CITY_DAY",
        "city_days_independent": False,
        "same_date_cluster": TARGET.isoformat(),
        "correlation_groups_by_city": {
            city["city_id"]: {
                "geographic_group": city["geographic_group"],
                "weather_regime_group": city["weather_regime_group"],
            }
            for city in PROTOCOL["cities"]
        },
        "query_scope": (
            "non-current 2026-09-27 00Z point values only; no current forecast or market data"
        ),
        "cities": fixtures,
    }
    (ROOT / "historical_point_fixture_20260927.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                city: {
                    "rows": len(rows),
                    "grid_points": sorted({tuple(row["grid_lonlat"]) for row in rows}),
                }
                for city, rows in fixtures.items()
            }
        )
    )


if __name__ == "__main__":
    main()
