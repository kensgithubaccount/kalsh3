from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from services.forecasting.daily_temperature import SETTLEMENT_LOCATIONS
from services.forecasting.domain import ForecastError
from services.forecasting.weather import WeatherContract
from services.forecasting.weather_source_authority import (
    AUTHORITY_IDENTITY,
    PHYSICAL_WEATHER_SOURCES,
    PhysicalWeatherSource,
    authority_identity,
    parse_ghcnd_metadata,
    parse_nws_points,
    parse_nws_station,
    resolve_physical_weather_source,
)

NOW = datetime(2026, 8, 16, tzinfo=UTC)


def contract(**changes: Any) -> WeatherContract:
    values: dict[str, Any] = dict(
        station_id="CLIATL",
        location="Atlanta",
        measurement="DAILY_MAX",
        local_date=date(2026, 8, 16),
        timezone="America/New_York",
        lower=Decimal("90"),
        upper=None,
        comparator="GT",
        unit="degF",
        rounding=None,
        settlement_authority="Kalshi daily-temperature rules / The Weather Company",
        settlement_source="The Weather Company",
        revision_policy="reviewed rule",
    )
    values.update(changes)
    return WeatherContract(**values)


def station_payload(**changes: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "properties": {"stationIdentifier": "KATL", "name": "Atlanta Airport"},
        "geometry": {"type": "Point", "coordinates": [-84.42694, 33.64028]},
    }
    payload.update(changes)
    return payload


def grid_payload(**properties: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "timeZone": "America/New_York",
        "gridId": "FFC",
        "gridX": 50,
        "gridY": 82,
        "forecastGridData": "https://api.weather.gov/gridpoints/FFC/50,82",
    }
    values.update(properties)
    return {
        "properties": values,
        "geometry": {"type": "Point", "coordinates": [-84.42694, 33.64028]},
    }


def fixed_width() -> tuple[str, str]:
    station = (
        f"{'USW00013874':11} {33.6297:8.4f} {-84.4422:9.4f} "
        f"{'308.2':6} {'GA':2} {'ATLANTA HARTSFIELD-JACKSON INT':30}"
    )
    inventory = "\n".join(
        f"{'USW00013874':11} {33.6297:8.4f} {-84.4422:9.4f} {element:4} {first:4d} {last:4d}"
        for element, first, last in (("TMAX", 1930, 2026), ("TMIN", 1930, 2026))
    )
    return station, inventory


def test_authority_exact_immutable_and_part1_composed() -> None:
    assert len(PHYSICAL_WEATHER_SOURCES) == 20
    assert set(PHYSICAL_WEATHER_SOURCES) == set(SETTLEMENT_LOCATIONS)
    for key, value in PHYSICAL_WEATHER_SOURCES.items():
        assert (value.canonical_location, value.timezone) == (
            SETTLEMENT_LOCATIONS[key].location,
            SETTLEMENT_LOCATIONS[key].timezone,
        )
        assert value.research_only and value.production_influence == 0
    with pytest.raises(TypeError):
        PHYSICAL_WEATHER_SOURCES["FORGED"] = PHYSICAL_WEATHER_SOURCES["CLIATL"]  # type: ignore[index]
    with pytest.raises(ForecastError):
        PhysicalWeatherSource(settlement_product_id="CLIATL")  # type: ignore[call-arg]


def test_complete_identity_is_deterministic_and_material() -> None:
    rows = tuple(
        (
            v.settlement_product_id,
            v.canonical_location,
            v.timezone,
            v.nws_station_id,
            v.ghcnd_station_id,
        )
        for v in PHYSICAL_WEATHER_SOURCES.values()
    )
    assert authority_identity(rows) == AUTHORITY_IDENTITY == authority_identity(rows)
    changed = list(rows)
    changed[0] = (*changed[0][:-1], "USW00013875")
    assert authority_identity(tuple(changed)) != AUTHORITY_IDENTITY


@pytest.mark.parametrize(
    "changes",
    [
        {"station_id": "CLIXXX"},
        {"location": "Elsewhere"},
        {"timezone": "America/Chicago"},
        {"measurement": "HOURLY_MAX"},
        {"settlement_source": "AccuWeather"},
        {"settlement_authority": "unreviewed authority"},
        {"unit": "degC"},
    ],
)
def test_contract_resolution_fails_closed(changes: dict[str, str]) -> None:
    with pytest.raises(ForecastError):
        resolve_physical_weather_source(contract(**changes))


def test_correct_reviewed_contract_resolves() -> None:
    assert resolve_physical_weather_source(contract()).settlement_product_id == "CLIATL"


def test_nws_station_and_grid_evidence_are_content_bound_and_zero_influence() -> None:
    authority = resolve_physical_weather_source(contract())
    station = parse_nws_station(authority, station_payload(), NOW)
    grid = parse_nws_points(authority, station, grid_payload(), NOW)
    changed = parse_nws_station(authority, station_payload(extra="changed"), NOW)
    later = parse_nws_station(authority, station_payload(), NOW + timedelta(hours=1))
    dynamic = parse_nws_points(
        authority,
        station,
        grid_payload(
            gridId="NEW",
            gridX=1,
            gridY=2,
            forecastGridData="https://api.weather.gov/gridpoints/NEW/1,2",
        ),
        NOW,
    )
    assert changed.source_hash != station.source_hash
    assert (
        later.source_hash == station.source_hash
        and later.evidence_identity == station.evidence_identity
    )
    assert dynamic.grid_id != grid.grid_id and dynamic.authority_identity == grid.authority_identity
    assert station.research_only and grid.research_only
    assert station.production_influence == grid.production_influence == 0


@pytest.mark.parametrize(
    "payload",
    [
        station_payload(properties={"stationIdentifier": "KBOS", "name": "wrong"}),
        station_payload(geometry=None),
        station_payload(geometry={"type": "LineString", "coordinates": [0, 0]}),
        station_payload(geometry={"type": "Point", "coordinates": [0]}),
        station_payload(geometry={"type": "Point", "coordinates": [float("nan"), 0]}),
        station_payload(geometry={"type": "Point", "coordinates": [181, 0]}),
        station_payload(geometry={"type": "Point", "coordinates": [0, 91]}),
    ],
)
def test_nws_station_adversarial(payload: dict[str, Any]) -> None:
    with pytest.raises(ForecastError):
        parse_nws_station(PHYSICAL_WEATHER_SOURCES["CLIATL"], payload, NOW)


@pytest.mark.parametrize(
    "changes",
    [
        {"timeZone": "America/Chicago"},
        {"gridId": ""},
        {"gridX": "50"},
        {"gridY": 2.0},
        {"forecastGridData": "http://api.weather.gov/gridpoints/FFC/50,82"},
        {"forecastGridData": "https://evil.example/gridpoints/FFC/50,82"},
        {"forecastGridData": "https://api.weather.gov/gridpoints/OTHER/50,82"},
    ],
)
def test_nws_grid_adversarial(changes: dict[str, object]) -> None:
    authority = PHYSICAL_WEATHER_SOURCES["CLIATL"]
    station = parse_nws_station(authority, station_payload(), NOW)
    with pytest.raises(ForecastError):
        parse_nws_points(authority, station, grid_payload(**changes), NOW)
    wrong_coordinates = grid_payload()
    wrong_coordinates["geometry"]["coordinates"] = [-84, 33]  # type: ignore[index]
    with pytest.raises(ForecastError):
        parse_nws_points(authority, station, wrong_coordinates, NOW)


def test_ghcnd_metadata_is_vintaged_content_bound_and_zero_influence() -> None:
    station_text, inventory_text = fixed_width()
    authority = PHYSICAL_WEATHER_SOURCES["CLIATL"]
    first = parse_ghcnd_metadata(authority, station_text, inventory_text, NOW)
    later = parse_ghcnd_metadata(authority, station_text, inventory_text, NOW + timedelta(days=1))
    changed = parse_ghcnd_metadata(authority, station_text + "\n", inventory_text, NOW)
    assert first.station_id == "USW00013874"
    assert (first.tmax_last_year, first.tmin_last_year) == (2026, 2026)
    assert (
        first.source_hash == later.source_hash
        and first.evidence_identity == later.evidence_identity
    )
    assert changed.source_hash != first.source_hash
    assert first.research_only and first.production_influence == 0


@pytest.mark.parametrize(
    "station_change,inventory_change",
    [
        (lambda value: value.replace("USW00013874", "USW00013875"), lambda value: value),
        (
            lambda value: value,
            lambda value: "\n".join(x for x in value.splitlines() if "TMAX" not in x),
        ),
        (
            lambda value: value,
            lambda value: "\n".join(x for x in value.splitlines() if "TMIN" not in x),
        ),
        (lambda value: value, lambda value: value.replace("1930 2026", "2027 2026")),
    ],
)
def test_ghcnd_metadata_adversarial(station_change: Any, inventory_change: Any) -> None:
    station_text, inventory_text = fixed_width()
    with pytest.raises(ForecastError):
        parse_ghcnd_metadata(
            PHYSICAL_WEATHER_SOURCES["CLIATL"],
            station_change(station_text),
            inventory_change(inventory_text),
            NOW,
        )


def test_module_has_no_network_execution_or_forecast_path() -> None:
    import inspect

    import services.forecasting.weather_source_authority as module

    source = inspect.getsource(module)
    forbidden = (
        "services.production_execution",
        "requests",
        "httpx",
        "urllib.request",
        "forecast_weather",
        "WeatherSourceRecord",
        "FINAL_OFFICIAL_SETTLEMENT_SOURCE",
        "TradeCandidate",
        "DecisionReceipt",
        "RiskIntent",
    )
    assert all(value not in source for value in forbidden)
