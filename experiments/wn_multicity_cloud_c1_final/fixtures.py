"""Offline fakes and fixtures shared by the C1 tests and non-scientific canaries.

Nothing here contacts Earth Engine, Kalshi, or GCS. Weather fixtures come from the
frozen D1 non-current 2026-09-27 point fixture; market fixtures come from the
frozen D1 synthetic ladder in test_policy.py.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

import cloud_collector as cc

HISTORICAL = date(2026, 9, 27)
D1_ROW_KEYS = (
    "city_id",
    "station_icao",
    "grid_lonlat",
    "init_time_utc",
    "valid_time_utc",
    "forecast_hour",
    "ingestion_time_utc",
    cc.PRIMARY_BAND,
)


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class MemoryStore:
    """Create-only object map with the same exists/create contract as GcsCreateOnly."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def exists(self, name: str) -> bool:
        return name in self.objects

    def create(self, name: str, payload: bytes) -> None:
        if name in self.objects:
            raise FileExistsError(name)
        self.objects[name] = payload


def historical_rows(city_id: str) -> list[dict[str, Any]]:
    fixture = json.loads((cc.FROZEN / "historical_point_fixture_20260927.json").read_text())
    rows: list[dict[str, Any]] = fixture["cities"][city_id]
    return rows


def shifted_rows(city_id: str, target: date) -> list[dict[str, Any]]:
    """SYNTHETIC: real 2026-09-27 p50 values re-dated to an allowlist date for offline replay.

    Diagnostic bands are synthetic offsets of p50. Never written to GCS as evidence.
    """
    delta = datetime(target.year, target.month, target.day, tzinfo=UTC) - datetime(
        HISTORICAL.year, HISTORICAL.month, HISTORICAL.day, tzinfo=UTC
    )

    def shift(value: str) -> str:
        moved = datetime.fromisoformat(value.replace("Z", "+00:00")) + delta
        return moved.isoformat().replace("+00:00", "Z")

    rows = []
    for row in historical_rows(city_id):
        p50 = row[cc.PRIMARY_BAND]
        rows.append(
            {
                **row,
                "init_time_utc": shift(row["init_time_utc"]),
                "valid_time_utc": shift(row["valid_time_utc"]),
                "ingestion_time_utc": shift(row["ingestion_time_utc"]),
                "station_head_temperature_2m_mean": p50 + 0.25,
                "station_head_temperature_2m_p10": p50 - 1.5,
                "station_head_temperature_2m_p90": p50 + 1.5,
            }
        )
    return rows


def features_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Inverse of cloud_collector.rows_from_features: an Earth Engine-shaped FeatureCollection."""
    features = []
    for row in rows:
        ingestion = datetime.fromisoformat(row["ingestion_time_utc"].replace("Z", "+00:00"))
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": row["grid_lonlat"]},
                "properties": {
                    "start_time": row["init_time_utc"],
                    "end_time": row["valid_time_utc"],
                    "forecast_hour": row["forecast_hour"],
                    "ingestion_time_utc": ingestion.timestamp(),
                    **{band: row[band] for band in cc.BANDS},
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def d1_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: row[key] for key in D1_ROW_KEYS} for row in rows]


def fake_kalshi(event: dict[str, Any], calls: list[str] | None = None) -> Any:
    """Serve a D1-shaped assembled event as the raw public endpoint responses."""
    markets = {row["ticker"]: row for row in event["markets"] if isinstance(row.get("ticker"), str)}

    def bare(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "event_ticker": event.get("event_ticker"),
            **{k: v for k, v in row.items() if k not in ("orderbook_fp", "retrieved_at_utc")},
        }

    def get(path: str) -> dict[str, Any]:
        if calls is not None:
            calls.append(path)
        tail = path.removeprefix("/trade-api/v2/")
        if tail.startswith("events/"):
            payload: dict[str, Any] = {
                "event": {k: v for k, v in event.items() if k != "markets"},
                "markets": [bare(row) for row in event["markets"]],
            }
        elif tail.startswith("series/"):
            payload = {
                "series": {
                    "ticker": event.get("series_ticker"),
                    "settlement_sources": cc.SETTLEMENT_SOURCES,
                }
            }
        elif tail.startswith("markets/orderbooks?tickers="):
            ticker = tail.split("=", 1)[1]
            payload = {
                "orderbooks": [{"ticker": ticker, "orderbook_fp": markets[ticker]["orderbook_fp"]}]
            }
        elif tail.startswith("markets/"):
            payload = {"market": bare(markets[tail.split("/", 1)[1]])}
        else:
            raise RuntimeError("UNEXPECTED_FAKE_KALSHI_PATH")
        body = json.dumps(payload, sort_keys=True).encode()
        return {
            "path": path,
            "observed_at": "FAKE",
            "status": 200,
            "body_sha256": cc.sha(body),
            "raw_body_b64": base64.b64encode(body).decode("ascii"),
            "payload": payload,
        }

    return get


def run_fake_city(
    policy: Any,
    store: Any,
    city_id: str,
    target: date,
    now: datetime,
    rows: list[dict[str, Any]],
    event: dict[str, Any],
    *,
    calls: list[str] | None = None,
    weather_calls: list[str] | None = None,
    delayed_stall_seconds: float = 0.0,
) -> tuple[str, int]:
    """Drive the real run_city code with a fake clock, fake weather, and fake Kalshi."""
    clock = FakeClock(now)
    base_get = fake_kalshi(event, calls)
    delayed_seen = [0]

    def get(path: str) -> dict[str, Any]:
        if delayed_stall_seconds and clock.now >= now + cc.DELAYED_OFFSET:
            delayed_seen[0] += 1
            if delayed_seen[0] == 2:
                clock.sleep(delayed_stall_seconds)
        return base_get(path)

    def open_weather() -> Any:
        def fetch() -> dict[str, Any]:
            if weather_calls is not None:
                weather_calls.append(city_id)
            return features_from_rows(rows)

        return fetch, {"credential_class": "FAKE", "scope_names": [cc.READONLY_SCOPE]}

    return cc.run_city(
        policy,
        store,
        city_id,
        target,
        wrapper_started_at=now,
        clock=clock,
        sleep=clock.sleep,
        open_weather=open_weather,
        get=get,
        context={"task_index": cc.TASK_INDEX_TO_CITY.index(city_id), "fixture": True},
    )
