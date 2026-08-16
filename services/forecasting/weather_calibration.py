"""Offline, forecast-vintaged weather calibration evidence.

This module parses captured NDFD descriptor/point evidence and a current
GHCN-Daily ``.dly`` label snapshot.  It deliberately stops at residual data:
it does not forecast, calibrate probabilities, or reach a trading path.
"""

from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from zoneinfo import ZoneInfo

from services.market_universe.domain import stable_hash

from .domain import ForecastError
from .weather_source_authority import (
    PHYSICAL_WEATHER_SOURCES,
    GhcndStationEvidence,
    NwsStationEvidence,
    PhysicalWeatherSource,
)

ZERO = Decimal("0")
TWELVE = Decimal("12")
_CAPABILITY = object()
_ORIGIN = re.compile(r"\AHour since (?P<origin>[^ ]+)\Z")
_VAR_ID = re.compile(r"\AVAR_0-0-(?P<parameter>[45])_L1_I12_Hour_S(?P<stat>[23])\Z")
_REQUESTED = re.compile(
    r"\AGridPointRequestedAt\[(?P<lat>[+-]?\d+\.\d{3})N_(?P<lon>[+-]?\d+\.\d{3})W\]\Z"
)


class CalibrationMeasurement(StrEnum):
    DAILY_MAX = "DAILY_MAX"
    DAILY_MIN = "DAILY_MIN"


class ReplayFidelity(StrEnum):
    FORECAST_VINTAGED_CURRENT_OUTCOME_SNAPSHOT = "FORECAST_VINTAGED_CURRENT_OUTCOME_SNAPSHOT"


@dataclass(frozen=True, slots=True, init=False)
class NdfdDescriptorEvidence:
    source: PhysicalWeatherSource
    measurement: CalibrationMeasurement
    forecast_reference_time: datetime
    time_coordinate_origin: datetime
    valid_time_coordinates: tuple[datetime, ...]
    statistical_period_hours: Decimal
    variable_name: str
    grid_identity: str
    units: str
    grib_parameter: tuple[int, int, int]
    grib_category: str
    grib_parameter_name: str
    generating_process: str
    statistical_process: str
    source_hash: str
    acquired_at: datetime
    authority_identity: str
    evidence_identity: str
    research_only: bool
    production_influence: Decimal

    def __init__(self, *, _capability: object | None = None, **values: object) -> None:
        if _capability is not _CAPABILITY:
            raise ForecastError("NDFD descriptor evidence is not caller-constructible")
        for name, value in values.items():
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class NdfdPointRow:
    valid_time_coordinate: datetime
    requested_coordinate_display: str
    returned_latitude: Decimal
    returned_longitude: Decimal
    forecast_kelvin: Decimal
    forecast_deg_f: Decimal


@dataclass(frozen=True, slots=True, init=False)
class NdfdPointEvidence:
    descriptor: NdfdDescriptorEvidence
    station_evidence_identity: str
    rows: tuple[NdfdPointRow, ...]
    source_hash: str
    acquired_at: datetime
    authority_identity: str
    evidence_identity: str
    research_only: bool
    production_influence: Decimal

    def __init__(self, *, _capability: object | None = None, **values: object) -> None:
        if _capability is not _CAPABILITY:
            raise ForecastError("NDFD point evidence is not caller-constructible")
        for name, value in values.items():
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class GhcndObservation:
    station_id: str
    measurement: CalibrationMeasurement
    local_date: date
    raw_tenths_c: int
    mflag: str
    qflag: str
    sflag: str
    observed_deg_c: Decimal | None
    observed_deg_f: Decimal | None
    usable: bool


@dataclass(frozen=True, slots=True, init=False)
class GhcndDailySnapshotEvidence:
    station_evidence_identity: str
    station_id: str
    observations: tuple[GhcndObservation, ...]
    source_hash: str
    acquired_at: datetime
    authority_identity: str
    evidence_identity: str
    research_only: bool
    production_influence: Decimal

    def __init__(self, *, _capability: object | None = None, **values: object) -> None:
        if _capability is not _CAPABILITY:
            raise ForecastError("GHCN-Daily snapshot evidence is not caller-constructible")
        for name, value in values.items():
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class WeatherCalibrationResidual:
    settlement_product_id: str
    nws_station_id: str
    ghcnd_station_id: str
    measurement: CalibrationMeasurement
    forecast_reference_time: datetime
    valid_time_coordinate: datetime
    local_target_date: date
    lead_to_valid_coordinate_seconds: int
    forecast_kelvin: Decimal
    forecast_deg_f: Decimal
    observed_tenths_c: int
    observed_deg_f: Decimal
    residual_deg_f: Decimal
    ndfd_descriptor_hash: str
    ndfd_csv_hash: str
    ghcnd_snapshot_hash: str
    ndfd_evidence_identity: str
    ghcnd_evidence_identity: str
    authority_identity: str
    created_at: datetime
    descriptor_acquired_at: datetime
    point_acquired_at: datetime
    outcome_acquired_at: datetime
    replay_fidelity: ReplayFidelity
    research_only: bool
    production_influence: Decimal


def parse_ndfd_descriptor(
    payload: str | bytes,
    source: PhysicalWeatherSource,
    measurement: CalibrationMeasurement | str,
    acquired_at: datetime,
) -> NdfdDescriptorEvidence:
    """Parse one captured NDFD XML descriptor without any transport."""
    _trusted_source(source)
    _aware(acquired_at, "descriptor acquisition timestamp")
    raw = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    if not isinstance(raw, str) or not raw.strip() or re.search(r"<!DOCTYPE|<!ENTITY", raw, re.I):
        raise ForecastError("unsafe or empty NDFD XML")
    try:
        root = ET.fromstring(raw)  # noqa: S314 — DTD/ENTITY input is rejected above.
    except ET.ParseError as exc:
        raise ForecastError("malformed NDFD XML") from exc
    if _local(root.tag) != "gridDataset":
        raise ForecastError("NDFD XML must contain one gridDataset root")
    grid_sets = [node for node in root if _local(node.tag) == "gridSet"]
    if len(grid_sets) != 1:
        raise ForecastError("NDFD descriptor gridSet is missing or ambiguous")
    grids = [node for node in grid_sets[0] if _local(node.tag) == "grid"]
    if len(grids) != 1:
        raise ForecastError("NDFD supported grid is missing or ambiguous")
    grid = grids[0]
    axes = {node.attrib.get("name"): node for node in root if _local(node.tag) == "axis"}
    reftime = axes.get("reftime")
    time_axis = axes.get("time")
    if reftime is None or time_axis is None:
        raise ForecastError("NDFD reftime/time axes are required")
    ref_origin = _axis_origin(reftime, "reftime")
    time_origin = _axis_origin(time_axis, "time")
    if ref_origin != time_origin:
        raise ForecastError("NDFD reftime and time origins conflict")
    if _attribute(reftime, "standard_name") != "forecast_reference_time":
        raise ForecastError("NDFD reftime is not forecast_reference_time")
    if _attribute(reftime, "long_name") != "GRIB reference time":
        raise ForecastError("NDFD reftime semantics are unsupported")
    axis_type = _attribute(time_axis, "axisType") or _attribute(time_axis, "_CoordinateAxisType")
    if axis_type != "Time" or _attribute(time_axis, "units") != "Hour":
        raise ForecastError("NDFD time axis semantics are unsupported")
    values = next((node for node in time_axis if _local(node.tag) == "values"), None)
    if values is None or values.attrib.get("spacing") != "discontiguousInterval":
        raise ForecastError("NDFD time axis interval semantics are unsupported")
    coordinates = _decimal_list(values.text, "NDFD time coordinates")
    npts = _positive_int(values.attrib.get("npts"), "NDFD time npts")
    if len(coordinates) != npts or any(
        coordinates[i] >= coordinates[i + 1] for i in range(len(coordinates) - 1)
    ):
        raise ForecastError("NDFD time coordinates must be unique and ordered")
    start, end, resolution = (_decimal_attr(values, key) for key in ("start", "end", "resolution"))
    if start > coordinates[0] or end < coordinates[-1] or resolution != Decimal("24.0"):
        raise ForecastError("NDFD interval metadata is inconsistent")
    variable = _semantic_grid(grid, measurement)
    reference = ref_origin
    valid = tuple(reference + _hours(value) for value in coordinates)
    variable_name = _required_attr(grid, "name")
    grid_identity = stable_hash(
        (grid_sets[0].attrib.get("name", ""), variable_name, root.attrib.get("location", ""))
    )
    source_hash = stable_hash(raw)
    identity = stable_hash(("m27c-ndfd-descriptor-v1", source.authority_identity, source_hash))
    return NdfdDescriptorEvidence(
        _capability=_CAPABILITY,
        source=source,
        measurement=_measurement(measurement),
        forecast_reference_time=reference,
        time_coordinate_origin=time_origin,
        valid_time_coordinates=valid,
        statistical_period_hours=TWELVE,
        variable_name=variable_name,
        grid_identity=grid_identity,
        units="K",
        grib_parameter=variable[0],
        grib_category=variable[1],
        grib_parameter_name=variable[2],
        generating_process="Forecast",
        statistical_process=variable[3],
        source_hash=source_hash,
        acquired_at=acquired_at,
        authority_identity=source.authority_identity,
        evidence_identity=identity,
        research_only=True,
        production_influence=ZERO,
    )


def parse_ndfd_point_csv(
    payload: str | bytes,
    descriptor: NdfdDescriptorEvidence,
    station: NwsStationEvidence,
    acquired_at: datetime,
) -> NdfdPointEvidence:
    """Parse the exact NCSS point CSV shape emitted by the reviewed capture."""
    _aware(acquired_at, "point CSV acquisition timestamp")
    if (
        station.source is not descriptor.source
        or station.authority_identity != descriptor.authority_identity
    ):
        raise ForecastError("NDFD point station evidence does not bind descriptor authority")
    raw = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    rows = list(csv.reader(io.StringIO(raw, newline="")))
    if not rows:
        raise ForecastError("empty NDFD point CSV")
    expected_prefix = [
        "time",
        "station",
        'latitude[unit="degrees_north"]',
        'longitude[unit="degrees_east"]',
    ]
    header = rows[0]
    if len(header) != 5 or header[:4] != expected_prefix:
        raise ForecastError("unsupported NDFD point CSV schema")
    match = re.fullmatch(r'(.+)\[unit="([^"]+)"\]', header[4])
    if match is None or match.group(1) != descriptor.variable_name or match.group(2) != "K":
        raise ForecastError("NDFD point CSV variable or units conflict with descriptor")
    coordinates = set(descriptor.valid_time_coordinates)
    seen: set[datetime] = set()
    parsed: list[NdfdPointRow] = []
    for row in rows[1:]:
        if len(row) != 5:
            raise ForecastError("NDFD point CSV row shape is invalid")
        valid_time = _parse_datetime(row[0], "NDFD valid-time coordinate")
        if valid_time not in coordinates or valid_time in seen:
            raise ForecastError("NDFD point CSV time is missing, extra, or duplicated")
        seen.add(valid_time)
        requested = row[1]
        request_match = _REQUESTED.fullmatch(requested)
        if request_match is None:
            raise ForecastError("NDFD requested-coordinate display is unsupported")
        requested_lat = Decimal(request_match.group("lat"))
        requested_lon = -Decimal(request_match.group("lon"))
        latitude, longitude = (
            _finite_decimal(row[2], "NDFD returned latitude"),
            _finite_decimal(row[3], "NDFD returned longitude"),
        )
        if not (
            Decimal("-90") <= latitude <= Decimal("90")
            and Decimal("-180") <= longitude <= Decimal("180")
        ):
            raise ForecastError("NDFD returned coordinate is out of range")
        station_lat = station.latitude.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        station_lon = station.longitude.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        if (requested_lat, requested_lon) != (station_lat, station_lon):
            raise ForecastError("NDFD requested-coordinate display conflicts with station evidence")
        kelvin = _finite_decimal(row[4], "NDFD Kelvin value")
        fahrenheit = (kelvin - Decimal("273.15")) * Decimal(9) / Decimal(5) + Decimal(32)
        parsed.append(NdfdPointRow(valid_time, requested, latitude, longitude, kelvin, fahrenheit))
    if len(parsed) != len(descriptor.valid_time_coordinates):
        raise ForecastError("NDFD point CSV row count differs from descriptor")
    result = tuple(parsed)
    source_hash = stable_hash(raw)
    identity = stable_hash(
        ("m27c-ndfd-point-v1", descriptor.evidence_identity, station.evidence_identity, source_hash)
    )
    return NdfdPointEvidence(
        _capability=_CAPABILITY,
        descriptor=descriptor,
        station_evidence_identity=station.evidence_identity,
        rows=result,
        source_hash=source_hash,
        acquired_at=acquired_at,
        authority_identity=descriptor.authority_identity,
        evidence_identity=identity,
        research_only=True,
        production_influence=ZERO,
    )


def parse_ghcnd_daily(
    payload: str | bytes,
    station: GhcndStationEvidence | PhysicalWeatherSource,
    acquired_at: datetime,
) -> GhcndDailySnapshotEvidence:
    """Parse only the official fixed-width GHCN-Daily monthly ``.dly`` shape."""
    _aware(acquired_at, "GHCN-Daily acquisition timestamp")
    raw = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    if isinstance(station, GhcndStationEvidence):
        station_id, station_evidence_identity, authority_identity = (
            station.station_id,
            station.evidence_identity,
            station.authority_identity,
        )
    else:
        _trusted_source(station)
        station_id, station_evidence_identity, authority_identity = (
            station.ghcnd_station_id,
            station.authority_identity,
            station.authority_identity,
        )
    observations: list[GhcndObservation] = []
    monthly: set[tuple[int, int, CalibrationMeasurement]] = set()
    for line in raw.splitlines():
        if not line.strip():
            continue
        if len(line) < 21 + 31 * 8 or line[:11] != station_id:
            if line[:11] == station_id:
                raise ForecastError("malformed GHCN-Daily fixed-width row")
            continue
        try:
            year, month = int(line[11:15]), int(line[15:17])
        except ValueError as exc:
            raise ForecastError("invalid GHCN-Daily year/month") from exc
        element = line[17:21]
        if element not in {"TMAX", "TMIN"}:
            continue
        measurement = (
            CalibrationMeasurement.DAILY_MAX
            if element == "TMAX"
            else CalibrationMeasurement.DAILY_MIN
        )
        key = (year, month, measurement)
        if key in monthly:
            raise ForecastError("duplicate GHCN-Daily monthly element")
        monthly.add(key)
        for day in range(1, 32):
            offset = 21 + (day - 1) * 8
            raw_value = line[offset : offset + 5]
            mflag, qflag, sflag = (line[offset + index].strip() for index in (5, 6, 7))
            if raw_value == "-9999":
                try:
                    missing_date = _safe_date(year, month, day)
                except ForecastError:
                    continue
                observations.append(
                    GhcndObservation(
                        station_id,
                        measurement,
                        missing_date,
                        -9999,
                        mflag,
                        qflag,
                        sflag,
                        None,
                        None,
                        False,
                    )
                )
                continue
            local_date = _safe_date(year, month, day)
            if not re.fullmatch(r"-?\d{1,4}", raw_value.strip()):
                raise ForecastError("invalid GHCN-Daily integer value")
            raw_integer = int(raw_value)
            deg_c = Decimal(raw_integer) / Decimal(10)
            deg_f = deg_c * Decimal(9) / Decimal(5) + Decimal(32)
            observations.append(
                GhcndObservation(
                    station_id,
                    measurement,
                    local_date,
                    raw_integer,
                    mflag,
                    qflag,
                    sflag,
                    deg_c,
                    deg_f,
                    qflag == "",
                )
            )
    if not observations:
        raise ForecastError("GHCN-Daily snapshot has no TMAX/TMIN rows for reviewed station")
    source_hash = stable_hash(raw)
    identity = stable_hash(
        ("m27c-ghcnd-daily-v1", authority_identity, station_evidence_identity, source_hash)
    )
    return GhcndDailySnapshotEvidence(
        _capability=_CAPABILITY,
        station_evidence_identity=station_evidence_identity,
        station_id=station_id,
        observations=tuple(observations),
        source_hash=source_hash,
        acquired_at=acquired_at,
        authority_identity=authority_identity,
        evidence_identity=identity,
        research_only=True,
        production_influence=ZERO,
    )


def build_residuals(
    source: PhysicalWeatherSource,
    descriptor: NdfdDescriptorEvidence,
    point: NdfdPointEvidence,
    outcome: GhcndDailySnapshotEvidence,
    station: NwsStationEvidence,
    ghcnd_station: GhcndStationEvidence | PhysicalWeatherSource,
    created_at: datetime,
) -> tuple[WeatherCalibrationResidual, ...]:
    """Join trusted evidence into residuals; missing/flagged labels are excluded."""
    _trusted_source(source)
    _aware(created_at, "residual creation timestamp")
    if (
        any(e.source is not source for e in (descriptor, point.descriptor))
        or point.authority_identity != source.authority_identity
        or outcome.authority_identity != source.authority_identity
    ):
        raise ForecastError("calibration evidence source authority mismatch")
    ghcnd_source = (
        ghcnd_station.source if isinstance(ghcnd_station, GhcndStationEvidence) else ghcnd_station
    )
    if (
        station.source is not source
        or ghcnd_source is not source
        or outcome.station_id != source.ghcnd_station_id
    ):
        raise ForecastError("calibration evidence station authority mismatch")
    expected_element = (
        CalibrationMeasurement.DAILY_MAX
        if descriptor.measurement is CalibrationMeasurement.DAILY_MAX
        else CalibrationMeasurement.DAILY_MIN
    )
    if expected_element is not descriptor.measurement:
        raise ForecastError("calibration measurement mismatch")
    by_date = {(row.measurement, row.local_date): row for row in outcome.observations}
    result: list[WeatherCalibrationResidual] = []
    for row in point.rows:
        if descriptor.forecast_reference_time > row.valid_time_coordinate:
            raise ForecastError("forecast reference time is after valid-time coordinate")
        target = target_local_date(row.valid_time_coordinate, source.timezone)
        observed = by_date.get((descriptor.measurement, target))
        if observed is None or not observed.usable or observed.observed_deg_f is None:
            continue
        lead = int((row.valid_time_coordinate - descriptor.forecast_reference_time).total_seconds())
        residual = observed.observed_deg_f - row.forecast_deg_f
        result.append(
            WeatherCalibrationResidual(
                source.settlement_product_id,
                source.nws_station_id,
                source.ghcnd_station_id,
                descriptor.measurement,
                descriptor.forecast_reference_time,
                row.valid_time_coordinate,
                target,
                lead,
                row.forecast_kelvin,
                row.forecast_deg_f,
                observed.raw_tenths_c,
                observed.observed_deg_f,
                residual,
                descriptor.source_hash,
                point.source_hash,
                outcome.source_hash,
                point.evidence_identity,
                outcome.evidence_identity,
                source.authority_identity,
                created_at,
                descriptor.acquired_at,
                point.acquired_at,
                outcome.acquired_at,
                ReplayFidelity.FORECAST_VINTAGED_CURRENT_OUTCOME_SNAPSHOT,
                True,
                ZERO,
            )
        )
    return tuple(result)


def target_local_date(valid_time_coordinate: datetime, timezone: str) -> date:
    """Convert a UTC valid-time coordinate using the source's DST-aware zone."""
    _aware(valid_time_coordinate, "valid-time coordinate")
    try:
        return valid_time_coordinate.astimezone(ZoneInfo(timezone)).date()
    except (KeyError, ValueError) as exc:
        raise ForecastError("invalid target timezone") from exc


def _semantic_grid(
    grid: ET.Element, measurement: CalibrationMeasurement | str
) -> tuple[tuple[int, int, int], str, str, str]:
    wanted = _measurement(measurement)
    attrs = {key: value for key, value in grid.attrib.items()}
    attrs.update(
        {
            node.attrib["name"]: node.attrib.get("value", "")
            for node in grid
            if _local(node.tag) == "attribute" and "name" in node.attrib
        }
    )
    parameter = _required_attr(grid, "Grib2_Parameter").split()
    if len(parameter) != 3 or any(not item.isdigit() for item in parameter):
        raise ForecastError("NDFD GRIB parameter is malformed")
    grib = tuple(int(item) for item in parameter)
    expected = (
        (0, 0, 4, "Maximum", "Maximum temperature")
        if wanted is CalibrationMeasurement.DAILY_MAX
        else (0, 0, 5, "Minimum", "Minimum temperature")
    )
    if (
        grib != expected[:3]
        or attrs.get("Grib2_Parameter_Category") != "Temperature"
        or attrs.get("Grib2_Generating_Process_Type") != "Forecast"
        or attrs.get("Grib2_Statistical_Process_Type") != expected[3]
        or attrs.get("Grib2_Parameter_Name") != expected[4]
        or attrs.get("units") != "K"
    ):
        raise ForecastError("NDFD grid semantics conflict with requested measurement")
    long_name = attrs.get("long_name", _required_attr(grid, "desc"))
    if long_name != f"{expected[4]} (12_Hour {expected[3]}) @ Ground or water surface":
        raise ForecastError("NDFD grid statistical product is not the reviewed 12-hour product")
    var_id = attrs.get("Grib_Variable_Id")
    if var_id is not None and _VAR_ID.fullmatch(var_id) is None:
        raise ForecastError("NDFD GRIB variable identity is malformed")
    return grib, attrs["Grib2_Parameter_Category"], attrs["Grib2_Parameter_Name"], expected[3]


def _measurement(value: CalibrationMeasurement | str) -> CalibrationMeasurement:
    try:
        return value if isinstance(value, CalibrationMeasurement) else CalibrationMeasurement(value)
    except ValueError as exc:
        raise ForecastError("unsupported calibration measurement") from exc


def _axis_origin(axis: ET.Element, name: str) -> datetime:
    values = [
        node.attrib.get("value", "")
        for node in axis.iter()
        if _local(node.tag) == "attribute" and node.attrib.get("name") in {"units", "udunits"}
    ]
    match = next((_ORIGIN.fullmatch(value) for value in values if _ORIGIN.fullmatch(value)), None)
    if match is None:
        raise ForecastError(f"invalid NDFD {name} time origin")
    return _parse_datetime(match.group("origin"), f"NDFD {name} origin")


def _attribute(node: ET.Element, name: str) -> str | None:
    return next(
        (
            child.attrib.get("value")
            for child in node
            if _local(child.tag) == "attribute" and child.attrib.get("name") == name
        ),
        None,
    )


def _required_attr(node: ET.Element, name: str) -> str:
    value = node.attrib.get(name) or _attribute(node, name)
    if not value:
        raise ForecastError(f"missing NDFD attribute {name}")
    return value


def _decimal_list(value: str | None, field: str) -> tuple[Decimal, ...]:
    if not value:
        raise ForecastError(f"missing {field}")
    result = tuple(_finite_decimal(item, field) for item in value.split())
    if not result:
        raise ForecastError(f"missing {field}")
    return result


def _decimal_attr(node: ET.Element, name: str) -> Decimal:
    return _finite_decimal(node.attrib.get(name, ""), f"NDFD {name}")


def _positive_int(value: str | None, field: str) -> int:
    if value is None or not value.isdigit() or int(value) <= 0:
        raise ForecastError(f"invalid {field}")
    return int(value)


def _hours(value: Decimal) -> timedelta:
    return timedelta(seconds=int(value * Decimal(3600)))


def _finite_decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ForecastError(f"invalid {field}")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ForecastError(f"invalid {field}") from exc
    if not result.is_finite():
        raise ForecastError(f"invalid {field}")
    return result


def _parse_datetime(value: str, field: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ForecastError(f"invalid {field}") from exc
    _aware(result, field)
    return result.astimezone(UTC)


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ForecastError(f"{field} must be timezone-aware")


def _safe_date(year: int, month: int, day: int) -> date:
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise ForecastError("invalid GHCN-Daily calendar date") from exc


def _local(value: str) -> str:
    return value.rsplit("}", 1)[-1]


def _trusted_source(source: PhysicalWeatherSource) -> None:
    if PHYSICAL_WEATHER_SOURCES.get(source.settlement_product_id) is not source:
        raise ForecastError("untrusted physical weather-source authority")
