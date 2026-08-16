"""M27C Part 2A physical weather-source authority and vintaged evidence parsing.

This module performs no I/O.  CLI identifiers remain Kalshi climate-product
identities; NWS and GHCN-Daily identifiers have separate, narrower meanings.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from services.market_universe.domain import stable_hash

from .daily_temperature import SETTLEMENT_AUTHORITY, SETTLEMENT_LOCATIONS, SETTLEMENT_SOURCE
from .domain import ForecastError
from .weather import WeatherContract

POLICY_VERSION = "m27c-part2a-physical-weather-source-authority-v1"
NWS_API_HOST = "api.weather.gov"
ZERO = Decimal("0")
_CLI = re.compile(r"CLI[A-Z]{3}\Z")
_NWS = re.compile(r"K[A-Z]{3}\Z")
_GHCND = re.compile(r"US[CW]0[0-9]{7}\Z")
_AUTHORITY_CAPABILITY = object()
_EVIDENCE_CAPABILITY = object()


@dataclass(frozen=True, slots=True, init=False)
class PhysicalWeatherSource:
    settlement_product_id: str
    canonical_location: str
    timezone: str
    nws_station_id: str
    ghcnd_station_id: str
    authority_identity: str
    research_only: bool
    production_influence: Decimal

    def __init__(self, *, _capability: object | None = None, **values: object) -> None:
        if _capability is not _AUTHORITY_CAPABILITY:
            raise ForecastError("physical weather-source authority is not caller-constructible")
        for name, value in values.items():
            object.__setattr__(self, name, value)


_SOURCE_IDS = (
    ("CLIATL", "KATL", "USW00013874"),
    ("CLIAUS", "KAUS", "USW00013904"),
    ("CLIBOS", "KBOS", "USW00014739"),
    ("CLIDCA", "KDCA", "USW00013743"),
    ("CLIDEN", "KDEN", "USW00003017"),
    ("CLIDFW", "KDFW", "USW00003927"),
    ("CLIHOU", "KHOU", "USW00012918"),
    ("CLILAS", "KLAS", "USW00023169"),
    ("CLILAX", "KLAX", "USW00023174"),
    ("CLIMDW", "KMDW", "USW00014819"),
    ("CLIMIA", "KMIA", "USW00012839"),
    ("CLIMSP", "KMSP", "USW00014922"),
    ("CLIMSY", "KMSY", "USW00012916"),
    ("CLINYC", "KNYC", "USW00094728"),
    ("CLIOKC", "KOKC", "USW00013967"),
    ("CLIPHL", "KPHL", "USW00013739"),
    ("CLIPHX", "KPHX", "USW00023183"),
    ("CLISAT", "KSAT", "USW00012921"),
    ("CLISEA", "KSEA", "USW00024233"),
    ("CLISFO", "KSFO", "USW00023234"),
)


def authority_identity(rows: tuple[tuple[str, str, str, str, str], ...]) -> str:
    """Return the complete policy identity for validated material mapping rows."""
    _validate_rows(rows)
    return stable_hash((POLICY_VERSION, rows))


def _validate_rows(rows: tuple[tuple[str, str, str, str, str], ...]) -> None:
    if len(rows) != 20:
        raise ForecastError("physical weather-source authority must contain exactly 20 rows")
    cli_ids, nws_ids, ghcnd_ids = set(), set(), set()
    for cli_id, location, timezone, nws_id, ghcnd_id in rows:
        if (
            not _CLI.fullmatch(cli_id)
            or not _NWS.fullmatch(nws_id)
            or not _GHCND.fullmatch(ghcnd_id)
        ):
            raise ForecastError("malformed physical weather-source identifier")
        try:
            ZoneInfo(timezone)
        except (KeyError, ValueError) as exc:
            raise ForecastError("invalid physical weather-source timezone") from exc
        reviewed = SETTLEMENT_LOCATIONS.get(cli_id)
        if reviewed is None or (location, timezone) != (reviewed.location, reviewed.timezone):
            raise ForecastError("physical authority conflicts with settlement-location authority")
        cli_ids.add(cli_id)
        nws_ids.add(nws_id)
        ghcnd_ids.add(ghcnd_id)
    if cli_ids != set(SETTLEMENT_LOCATIONS) or len(nws_ids) != 20 or len(ghcnd_ids) != 20:
        raise ForecastError("physical weather-source authority coverage or uniqueness failure")


def _build_authority() -> tuple[Mapping[str, PhysicalWeatherSource], str]:
    rows = tuple(
        (
            cli_id,
            SETTLEMENT_LOCATIONS[cli_id].location,
            SETTLEMENT_LOCATIONS[cli_id].timezone,
            nws,
            ghcnd,
        )
        for cli_id, nws, ghcnd in _SOURCE_IDS
    )
    identity = authority_identity(rows)
    values = {
        cli_id: PhysicalWeatherSource(
            _capability=_AUTHORITY_CAPABILITY,
            settlement_product_id=cli_id,
            canonical_location=location,
            timezone=timezone,
            nws_station_id=nws,
            ghcnd_station_id=ghcnd,
            authority_identity=identity,
            research_only=True,
            production_influence=ZERO,
        )
        for cli_id, location, timezone, nws, ghcnd in rows
    }
    return MappingProxyType(values), identity


PHYSICAL_WEATHER_SOURCES, AUTHORITY_IDENTITY = _build_authority()


def resolve_physical_weather_source(contract: WeatherContract) -> PhysicalWeatherSource:
    """Resolve a validated Part 1 contract, failing closed on every semantic mismatch."""
    contract.validate()
    if (
        contract.settlement_source != SETTLEMENT_SOURCE
        or contract.settlement_authority != SETTLEMENT_AUTHORITY
        or contract.unit != "degF"
    ):
        raise ForecastError("weather contract conflicts with reviewed M27C settlement semantics")
    reviewed = PHYSICAL_WEATHER_SOURCES.get(contract.station_id)
    if reviewed is None:
        raise ForecastError("unreviewed settlement/climate-product identifier")
    if contract.measurement not in {"DAILY_MAX", "DAILY_MIN"}:
        raise ForecastError("unsupported physical weather-source measurement")
    if (contract.location, contract.timezone) != (
        reviewed.canonical_location,
        reviewed.timezone,
    ):
        raise ForecastError("weather contract conflicts with physical source authority")
    return reviewed


@dataclass(frozen=True, slots=True, init=False)
class NwsStationEvidence:
    source: PhysicalWeatherSource
    station_identifier: str
    station_name: str
    latitude: Decimal
    longitude: Decimal
    acquired_at: datetime
    source_hash: str
    evidence_identity: str
    authority_identity: str
    research_only: bool
    production_influence: Decimal

    def __init__(self, *, _capability: object | None = None, **values: object) -> None:
        if _capability is not _EVIDENCE_CAPABILITY:
            raise ForecastError("NWS station evidence is not caller-constructible")
        for name, value in values.items():
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True, init=False)
class NwsGridEvidence:
    source: PhysicalWeatherSource
    station_evidence_identity: str
    latitude: Decimal
    longitude: Decimal
    timezone: str
    grid_id: str
    grid_x: int
    grid_y: int
    forecast_grid_data_url: str
    acquired_at: datetime
    source_hash: str
    evidence_identity: str
    authority_identity: str
    research_only: bool
    production_influence: Decimal

    def __init__(self, *, _capability: object | None = None, **values: object) -> None:
        if _capability is not _EVIDENCE_CAPABILITY:
            raise ForecastError("NWS grid evidence is not caller-constructible")
        for name, value in values.items():
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True, init=False)
class GhcndStationEvidence:
    source: PhysicalWeatherSource
    station_id: str
    station_name: str
    latitude: Decimal
    longitude: Decimal
    tmax_first_year: int
    tmax_last_year: int
    tmin_first_year: int
    tmin_last_year: int
    acquired_at: datetime
    stations_source_hash: str
    inventory_source_hash: str
    source_hash: str
    evidence_identity: str
    authority_identity: str
    research_only: bool
    production_influence: Decimal

    def __init__(self, *, _capability: object | None = None, **values: object) -> None:
        if _capability is not _EVIDENCE_CAPABILITY:
            raise ForecastError("GHCN-Daily evidence is not caller-constructible")
        for name, value in values.items():
            object.__setattr__(self, name, value)


def parse_nws_station(
    source: PhysicalWeatherSource, payload: Mapping[str, Any], acquired_at: datetime
) -> NwsStationEvidence:
    _trusted_source(source)
    _aware(acquired_at)
    properties = _mapping(payload.get("properties"), "NWS station properties")
    station_id = _text(properties.get("stationIdentifier"), "NWS stationIdentifier")
    if station_id != source.nws_station_id:
        raise ForecastError("NWS stationIdentifier conflicts with reviewed authority")
    name = _text(properties.get("name"), "NWS station name")
    latitude, longitude = _point(payload.get("geometry"))
    source_hash = stable_hash(payload)
    identity = stable_hash(("nws-station-evidence-v1", source.authority_identity, source_hash))
    return NwsStationEvidence(
        _capability=_EVIDENCE_CAPABILITY,
        source=source,
        station_identifier=station_id,
        station_name=name,
        latitude=latitude,
        longitude=longitude,
        acquired_at=acquired_at,
        source_hash=source_hash,
        evidence_identity=identity,
        authority_identity=source.authority_identity,
        research_only=True,
        production_influence=ZERO,
    )


def parse_nws_points(
    source: PhysicalWeatherSource,
    station: NwsStationEvidence,
    payload: Mapping[str, Any],
    acquired_at: datetime,
) -> NwsGridEvidence:
    _trusted_source(source)
    if station.source is not source or station.authority_identity != source.authority_identity:
        raise ForecastError("NWS station evidence does not bind the requested authority")
    _aware(acquired_at)
    latitude, longitude = _point(payload.get("geometry"))
    if (latitude, longitude) != (station.latitude, station.longitude):
        raise ForecastError(
            "NWS points evidence coordinates differ from station lookup coordinates"
        )
    properties = _mapping(payload.get("properties"), "NWS points properties")
    timezone = _text(properties.get("timeZone"), "NWS points timeZone")
    if timezone != source.timezone:
        raise ForecastError("NWS points timezone conflicts with reviewed authority")
    grid_id = _text(properties.get("gridId"), "NWS gridId")
    grid_x = _exact_int(properties.get("gridX"), "NWS gridX")
    grid_y = _exact_int(properties.get("gridY"), "NWS gridY")
    grid_url = _text(properties.get("forecastGridData"), "NWS forecastGridData")
    parsed = urlsplit(grid_url)
    expected_path = f"/gridpoints/{grid_id}/{grid_x},{grid_y}"
    if parsed.scheme != "https" or parsed.hostname != NWS_API_HOST or parsed.port is not None:
        raise ForecastError("NWS forecastGridData origin is not allowlisted")
    if parsed.path != expected_path or parsed.query or parsed.fragment or parsed.username:
        raise ForecastError("NWS forecastGridData path is not the exact reviewed shape")
    source_hash = stable_hash(payload)
    identity = stable_hash(
        ("nws-grid-evidence-v1", source.authority_identity, station.evidence_identity, source_hash)
    )
    return NwsGridEvidence(
        _capability=_EVIDENCE_CAPABILITY,
        source=source,
        station_evidence_identity=station.evidence_identity,
        latitude=latitude,
        longitude=longitude,
        timezone=timezone,
        grid_id=grid_id,
        grid_x=grid_x,
        grid_y=grid_y,
        forecast_grid_data_url=grid_url,
        acquired_at=acquired_at,
        source_hash=source_hash,
        evidence_identity=identity,
        authority_identity=source.authority_identity,
        research_only=True,
        production_influence=ZERO,
    )


def parse_ghcnd_metadata(
    source: PhysicalWeatherSource,
    stations_snapshot: str,
    inventory_snapshot: str,
    acquired_at: datetime,
) -> GhcndStationEvidence:
    """Parse official fixed-width station and inventory snapshots for one reviewed station."""
    _trusted_source(source)
    _aware(acquired_at)
    station_rows = [
        line for line in stations_snapshot.splitlines() if line[:11] == source.ghcnd_station_id
    ]
    if len(station_rows) != 1 or len(station_rows[0]) < 71:
        raise ForecastError("GHCN-Daily station metadata missing or ambiguous")
    row = station_rows[0]
    latitude = _coordinate(row[12:20].strip(), -90, 90, "GHCN-Daily latitude")
    longitude = _coordinate(row[21:30].strip(), -180, 180, "GHCN-Daily longitude")
    name = _text(row[41:71].strip(), "GHCN-Daily station name")
    inventories: dict[str, tuple[int, int]] = {}
    for line in inventory_snapshot.splitlines():
        if line[:11] != source.ghcnd_station_id or len(line) < 45:
            continue
        if line[31:35] in {"TMAX", "TMIN"}:
            element = line[31:35]
            if element in inventories:
                raise ForecastError(f"duplicate GHCN-Daily {element} inventory")
            first = _year(line[36:40], f"GHCN-Daily {element} first year")
            last = _year(line[41:45], f"GHCN-Daily {element} last year")
            if first > last:
                raise ForecastError(f"invalid GHCN-Daily {element} inventory years")
            inventories[element] = (first, last)
    if set(inventories) != {"TMAX", "TMIN"}:
        raise ForecastError("GHCN-Daily TMAX and TMIN inventory are both required")
    stations_hash, inventory_hash = stable_hash(stations_snapshot), stable_hash(inventory_snapshot)
    source_hash = stable_hash((stations_hash, inventory_hash))
    identity = stable_hash(("ghcnd-station-evidence-v1", source.authority_identity, source_hash))
    return GhcndStationEvidence(
        _capability=_EVIDENCE_CAPABILITY,
        source=source,
        station_id=source.ghcnd_station_id,
        station_name=name,
        latitude=latitude,
        longitude=longitude,
        tmax_first_year=inventories["TMAX"][0],
        tmax_last_year=inventories["TMAX"][1],
        tmin_first_year=inventories["TMIN"][0],
        tmin_last_year=inventories["TMIN"][1],
        acquired_at=acquired_at,
        stations_source_hash=stations_hash,
        inventory_source_hash=inventory_hash,
        source_hash=source_hash,
        evidence_identity=identity,
        authority_identity=source.authority_identity,
        research_only=True,
        production_influence=ZERO,
    )


def _trusted_source(source: PhysicalWeatherSource) -> None:
    if PHYSICAL_WEATHER_SOURCES.get(source.settlement_product_id) is not source:
        raise ForecastError("untrusted physical weather-source authority")


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ForecastError("evidence acquisition timestamp must be timezone-aware")


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ForecastError(f"missing or malformed {field}")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ForecastError(f"missing or malformed {field}")
    return value.strip()


def _coordinate(value: object, lower: int, upper: int, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ForecastError(f"invalid {field}")
    if isinstance(value, float) and not math.isfinite(value):
        raise ForecastError(f"invalid {field}")
    try:
        coordinate = Decimal(str(value))
    except Exception as exc:
        raise ForecastError(f"invalid {field}") from exc
    if not coordinate.is_finite() or not Decimal(lower) <= coordinate <= Decimal(upper):
        raise ForecastError(f"invalid {field}")
    return coordinate


def _point(value: object) -> tuple[Decimal, Decimal]:
    geometry = _mapping(value, "NWS Point geometry")
    coordinates = geometry.get("coordinates")
    if (
        geometry.get("type") != "Point"
        or not isinstance(coordinates, (list, tuple))
        or len(coordinates) != 2
    ):
        raise ForecastError("NWS geometry must be an exact Point coordinate pair")
    longitude = _coordinate(coordinates[0], -180, 180, "NWS longitude")
    latitude = _coordinate(coordinates[1], -90, 90, "NWS latitude")
    return latitude, longitude


def _exact_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ForecastError(f"{field} must have exact integer semantics")
    return value


def _year(value: str, field: str) -> int:
    if not re.fullmatch(r"[0-9]{4}", value):
        raise ForecastError(f"invalid {field}")
    year = int(value)
    if not 1700 <= year <= 9999:
        raise ForecastError(f"invalid {field}")
    return year
