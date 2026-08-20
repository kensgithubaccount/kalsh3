"""Pure validation of captured operator-only wgrib2 temperature evidence.

The collector owns downloading GRIB and invoking wgrib2.  This module accepts
only captured extraction text and never performs I/O, subprocesses, or network
access.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from zoneinfo import ZoneInfo

from .domain import ForecastError
from .weather_calibration import (
    CalibrationMeasurement,
    GhcndDailySnapshotEvidence,
    ReplayFidelity,
    WeatherCalibrationResidual,
)
from .weather_source_authority import PhysicalWeatherSource

POST2020_GRIB_FAMILY = "POST2020_CHICAGO_MAXT_2P5KM_YGUZ98_03Z"
POST2020_MINT_GRIB_FAMILY = "POST2020_CHICAGO_MINT_2P5KM_YHUZ98_04Z"
EXPECTED_GRID_TEMPLATE = 30
EXPECTED_NX = 2145
EXPECTED_NY = 1377
EXPECTED_DX = Decimal("2539.703")
EXPECTED_DY = Decimal("2539.703")
EXPECTED_PARAMETER = (0, 0, 4)
EXPECTED_MINT_PARAMETER = (0, 0, 5)
_DECIMAL = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
_TIME = re.compile(r"\A\d{14}\Z")


@dataclass(frozen=True, slots=True)
class RawGribRecord:
    record_number: int
    reference_time: datetime
    variable: str
    level: str
    generating_process_code: int
    statistical_process_code: int
    time_processing_code: int
    parameter: tuple[int, int, int]
    unit: str
    interval_start: datetime
    interval_end: datetime
    verification_time: datetime
    latitude: Decimal
    raw_longitude: Decimal
    signed_longitude: Decimal
    kelvin: Decimal
    grid_template: int
    nx: int
    ny: int
    dx: Decimal
    dy: Decimal

    @property
    def midpoint(self) -> datetime:
        duration = self.interval_end - self.interval_start
        return self.interval_start + duration / 2

    @property
    def lead_to_interval_start_seconds(self) -> int:
        return int((self.interval_start - self.reference_time).total_seconds())

    @property
    def lead_to_midpoint_seconds(self) -> int:
        return int((self.midpoint - self.reference_time).total_seconds())

    @property
    def lead_to_interval_end_seconds(self) -> int:
        return int((self.interval_end - self.reference_time).total_seconds())


@dataclass(frozen=True, slots=True)
class RawGribEvidence:
    family_identity: str
    records: tuple[RawGribRecord, ...]
    extraction_policy_version: str
    wgrib2_version: str
    raw_grib_sha256: str = ""
    extraction_sha256: str = ""


def validate_raw_grib_max_t_evidence(evidence: RawGribEvidence) -> None:
    """Authoritatively re-derive the reviewed 03Z MaxT invariants from a
    `RawGribEvidence`, regardless of how it was constructed.

    `RawGribEvidence` is a public, directly-constructible dataclass, so a
    caller must never trust that a given instance actually passed through
    `parse_wgrib2_max_t_evidence`.  This function performs the exact same
    structural checks that function applies after parsing, operating only on
    the evidence object's own fields.
    """
    if evidence.family_identity != POST2020_GRIB_FAMILY:
        raise ForecastError("raw GRIB evidence has an unsupported family")
    if evidence.wgrib2_version != "3.8.0":
        raise ForecastError("wgrib2 version is not the reviewed version")
    records = evidence.records
    if len(records) != 3:
        raise ForecastError("raw GRIB must contain exactly three MaxT records")
    ordered = tuple(sorted(records, key=lambda record: record.record_number))
    if tuple(record.record_number for record in ordered) != (1, 2, 3):
        raise ForecastError("raw GRIB record numbering is not exactly 1,2,3")
    if records != ordered:
        raise ForecastError("raw GRIB records are not physically ordered 1,2,3")
    reference = ordered[0].reference_time
    _require_utc(reference, "reference time")
    if reference.hour != 3 or reference.minute or reference.second or reference.microsecond:
        raise ForecastError("raw GRIB reference time is not the reviewed 03Z cycle")
    expected_offsets = ((9, 21), (33, 45), (57, 69))
    for record, (start_hours, end_hours) in zip(ordered, expected_offsets, strict=True):
        _validate_record(record, reference, start_hours, end_hours)
    if any(left.interval_end > right.interval_start for left, right in pairwise(ordered)):
        raise ForecastError("raw GRIB intervals overlap")


def parse_wgrib2_max_t_evidence(
    text: str,
    *,
    family_identity: str = POST2020_GRIB_FAMILY,
    extraction_policy_version: str = "m27c-wgrib2-maxt-extraction-v1",
    wgrib2_version: str = "3.8.0",
    raw_grib_sha256: str = "",
    extraction_sha256: str = "",
) -> RawGribEvidence:
    """Parse and strictly validate one canonical three-record extraction."""
    if family_identity != POST2020_GRIB_FAMILY:
        raise ForecastError("raw GRIB evidence has an unsupported family")
    if wgrib2_version != "3.8.0":
        raise ForecastError("wgrib2 version is not the reviewed version")
    records = _parse_records(text)
    evidence = RawGribEvidence(
        family_identity,
        tuple(sorted(records, key=lambda record: record.record_number)),
        extraction_policy_version,
        wgrib2_version,
        raw_grib_sha256,
        extraction_sha256,
    )
    validate_raw_grib_max_t_evidence(evidence)
    return evidence


def parse_wgrib2_min_t_evidence(
    text: str,
    *,
    family_identity: str = POST2020_MINT_GRIB_FAMILY,
    extraction_policy_version: str = "m27c-wgrib2-mint-extraction-v1",
    wgrib2_version: str = "3.8.0",
    raw_grib_sha256: str = "",
    extraction_sha256: str = "",
) -> RawGribEvidence:
    """Parse the reviewed post-2020 Chicago MinT three-record extraction."""
    if family_identity != POST2020_MINT_GRIB_FAMILY:
        raise ForecastError("raw GRIB evidence has an unsupported MinT family")
    if wgrib2_version != "3.8.0":
        raise ForecastError("wgrib2 version is not the reviewed version")
    records = _parse_records(text)
    if len(records) != 3:
        raise ForecastError("raw GRIB must contain exactly three MinT records")
    ordered = tuple(sorted(records, key=lambda record: record.record_number))
    if tuple(record.record_number for record in ordered) != (1, 2, 3):
        raise ForecastError("raw GRIB record numbering is not exactly 1,2,3")
    reference = ordered[0].reference_time
    _require_utc(reference, "reference time")
    if reference.hour != 4 or reference.minute or reference.second or reference.microsecond:
        raise ForecastError("raw GRIB reference time is not the reviewed 04Z cycle")
    # The first reviewed MinT record is the 0-12 forecast product but its
    # statistical interval begins at the 04Z reference and therefore has
    # observed GRIB bounds 04Z-12Z (8 hours); later records are 12 hours.
    expected_offsets = ((0, 8), (20, 32), (44, 56))
    for record, (start_hours, end_hours) in zip(ordered, expected_offsets, strict=True):
        _validate_record_for_measurement(
            record, reference, start_hours, end_hours, "TMIN", EXPECTED_MINT_PARAMETER, "MinT"
        )
    if any(left.interval_end > right.interval_start for left, right in pairwise(ordered)):
        raise ForecastError("raw GRIB intervals overlap")
    return RawGribEvidence(
        family_identity,
        ordered,
        extraction_policy_version,
        wgrib2_version,
        raw_grib_sha256,
        extraction_sha256,
    )


def target_local_date(record: RawGribRecord, timezone: str) -> date:
    """Validate DAILY_MAX interval dates and return the local target date."""
    try:
        zone = ZoneInfo(timezone)
    except Exception as exc:
        raise ForecastError("invalid target timezone") from exc
    local_start = record.interval_start.astimezone(zone).date()
    local_midpoint = record.midpoint.astimezone(zone).date()
    local_before_end = (record.interval_end - timedelta(microseconds=1)).astimezone(zone).date()
    if not (local_start == local_midpoint == local_before_end):
        raise ForecastError("MaxT interval crosses multiple local target dates")
    return local_start


def mint_target_local_date(record: RawGribRecord, timezone: str) -> date:
    """Map reviewed MinT overnight intervals to their local ending date."""
    try:
        zone = ZoneInfo(timezone)
    except Exception as exc:
        raise ForecastError("invalid target timezone") from exc
    local_start = record.interval_start.astimezone(zone)
    local_end = record.interval_end.astimezone(zone)
    local_midpoint = record.midpoint.astimezone(zone)
    if local_end.date() < local_start.date():
        raise ForecastError("MinT interval has an invalid local date order")
    if local_end.date() != local_midpoint.date() and local_end.date() != local_start.date():
        raise ForecastError("MinT interval date mapping is ambiguous")
    return local_end.date()


def kelvin_to_fahrenheit(kelvin: Decimal) -> Decimal:
    if not kelvin.is_finite():
        raise ForecastError("raw GRIB Kelvin value is non-finite")
    celsius = kelvin - Decimal("273.15")
    return celsius * Decimal(9) / Decimal(5) + Decimal(32)


def validate_kmdw_points(
    evidence: RawGribEvidence, requested_latitude: Decimal, requested_longitude: Decimal
) -> None:
    """Bind nearest-point evidence to KMDW without requiring grid-point equality."""
    signed_requested = _longitude(requested_longitude)
    for record in evidence.records:
        if abs(record.latitude - requested_latitude) > Decimal("0.5"):
            raise ForecastError("raw GRIB point is not geographically near KMDW")
        if abs(record.signed_longitude - signed_requested) > Decimal("0.5"):
            raise ForecastError("raw GRIB point is not geographically near KMDW")


def build_grib_residuals(
    evidence: RawGribEvidence,
    source: PhysicalWeatherSource,
    outcome: GhcndDailySnapshotEvidence,
    acquired_at: datetime,
) -> tuple[WeatherCalibrationResidual, ...]:
    """Join validated MaxT GRIB records to the reviewed GHCN-Daily outcome."""
    return _build_grib_residuals(
        evidence, source, outcome, acquired_at, CalibrationMeasurement.DAILY_MAX, target_local_date
    )


def build_min_t_grib_residuals(
    evidence: RawGribEvidence,
    source: PhysicalWeatherSource,
    outcome: GhcndDailySnapshotEvidence,
    acquired_at: datetime,
) -> tuple[WeatherCalibrationResidual, ...]:
    """Join validated MinT GRIB records only to usable GHCN TMIN outcomes."""
    if evidence.family_identity != POST2020_MINT_GRIB_FAMILY:
        raise ForecastError("MinT residuals require the reviewed MinT family")
    return _build_grib_residuals(
        evidence,
        source,
        outcome,
        acquired_at,
        CalibrationMeasurement.DAILY_MIN,
        mint_target_local_date,
    )


def _build_grib_residuals(
    evidence: RawGribEvidence,
    source: PhysicalWeatherSource,
    outcome: GhcndDailySnapshotEvidence,
    acquired_at: datetime,
    measurement: CalibrationMeasurement,
    date_mapper: Callable[[RawGribRecord, str], date],
) -> tuple[WeatherCalibrationResidual, ...]:
    if outcome.authority_identity != source.authority_identity:
        raise ForecastError("GRIB outcome authority does not match source")
    if not evidence.records:
        raise ForecastError("GRIB evidence has no records")
    by_date = {
        (observation.measurement, observation.local_date): observation
        for observation in outcome.observations
    }
    result: list[WeatherCalibrationResidual] = []
    for record in evidence.records:
        local_date = date_mapper(record, source.timezone)
        observed = by_date.get((measurement, local_date))
        if observed is None or not observed.usable or observed.observed_deg_f is None:
            continue
        forecast_f = kelvin_to_fahrenheit(record.kelvin)
        result.append(
            WeatherCalibrationResidual(
                source.settlement_product_id,
                source.nws_station_id,
                source.ghcnd_station_id,
                measurement,
                record.reference_time,
                record.midpoint,
                local_date,
                record.lead_to_midpoint_seconds,
                record.kelvin,
                forecast_f,
                observed.raw_tenths_c,
                observed.observed_deg_f,
                observed.observed_deg_f - forecast_f,
                evidence.raw_grib_sha256,
                evidence.extraction_sha256,
                outcome.source_hash,
                evidence.family_identity,
                outcome.evidence_identity,
                source.authority_identity,
                acquired_at,
                acquired_at,
                acquired_at,
                outcome.acquired_at,
                ReplayFidelity.FORECAST_VINTAGED_CURRENT_OUTCOME_SNAPSHOT,
                True,
                Decimal("0"),
            )
        )
    return tuple(result)


def _parse_records(text: str) -> tuple[RawGribRecord, ...]:
    if not text.strip() or "NaN" in text or "Infinity" in text:
        raise ForecastError("raw GRIB extraction is empty or non-finite")
    sections = re.split(r"(?m)^\s*record\s+(\d+)\s*:\s*$", text, flags=re.IGNORECASE)
    if len(sections) < 7 or len(sections) % 2 == 0 or sections[0].strip():
        return _parse_inventory_records(text)
    records: list[RawGribRecord] = []
    for index in range(1, len(sections), 2):
        number = int(sections[index])
        values = _fields(sections[index + 1])
        records.append(
            RawGribRecord(
                number,
                _timestamp(values, "reference"),
                _required(values, "variable"),
                _required(values, "level"),
                _integer(values, "generating_process"),
                _integer(values, "statistical_process"),
                _integer(values, "time_processing"),
                _parameter(values),
                _required(values, "unit"),
                _timestamp(values, "start"),
                _timestamp(values, "end"),
                _timestamp(values, "verification"),
                _decimal(values, "lat"),
                _decimal(values, "lon"),
                _longitude(_decimal(values, "lon")),
                _decimal(values, "value"),
                _integer(values, "grid_template"),
                _integer(values, "nx"),
                _integer(values, "ny"),
                _decimal(values, "dx"),
                _decimal(values, "dy"),
            )
        )
    return tuple(records)


def _parse_inventory_records(text: str) -> tuple[RawGribRecord, ...]:
    chunks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if re.match(r"^\d+:\d+:d=", line):
            if current:
                chunks.append("\n".join(current))
            current = [line]
        elif current and line.strip():
            current.append(line)
    if current:
        chunks.append("\n".join(current))
    records: list[RawGribRecord] = []
    for chunk in chunks:
        match = re.match(
            r"(?P<number>\d+):\d+:d=(?P<date>\d{10}):(?P<variable>[^:]+):"
            r"(?P<level>[^:]+):.*?:D=(?P<reference>\d{14}):.*?"
            r"var(?P<discipline>\d+)_(?P<master>\d+)_(?P<local>\d+)_(?P<center>\d+)"
            r"_(?P<category>\d+)_(?P<parameter>\d+):"
            r"code table 4\.3=(?P<generating>\d+).*?"
            r"code table 4\.10=(?P<statistical>\d+).*?"
            r"code table 4\.11=(?P<processing>\d+).*?"
            r"start_FT=(?P<start>\d{14}):end_FT=(?P<end>\d{14}):"
            r"vt=(?P<verification>\d{14}):lon=(?P<lon>[^,]+),lat=(?P<lat>[^,]+),"
            r"val=(?P<value>[^:]+):grid_template=(?P<template>\d+):.*?"
            r"\((?P<nx>\d+) x (?P<ny>\d+)\).*?Dx (?P<dx>[^ ]+) m Dy (?P<dy>[^ ]+) m",
            chunk,
            flags=re.DOTALL,
        )
        if match is None:
            raise ForecastError("wgrib2 inventory output is malformed")
        values = match.groupdict()
        if values["discipline"] != "0" or values["category"] != "0":
            raise ForecastError("wgrib2 parameter category is not Temperature")
        records.append(
            RawGribRecord(
                int(values["number"]),
                _timestamp_value(values["reference"]),
                values["variable"],
                values["level"],
                int(values["generating"]),
                int(values["statistical"]),
                int(values["processing"]),
                (0, 0, int(values["parameter"])),
                "Kelvin",
                _timestamp_value(values["start"]),
                _timestamp_value(values["end"]),
                _timestamp_value(values["verification"]),
                _decimal_value(values["lat"], "latitude"),
                _decimal_value(values["lon"], "longitude"),
                _longitude(_decimal_value(values["lon"], "longitude")),
                _decimal_value(values["value"], "Kelvin value"),
                int(values["template"]),
                int(values["nx"]),
                int(values["ny"]),
                _decimal_value(values["dx"], "Dx"),
                _decimal_value(values["dy"], "Dy"),
            )
        )
    if not records:
        raise ForecastError("wgrib2 inventory output lacks records")
    return tuple(records)


def _fields(section: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in section.splitlines():
        match = re.match(r"\s*([A-Za-z0-9_]+)\s*=\s*(.*?)\s*$", line)
        if match:
            result[match.group(1).lower()] = match.group(2)
    return result


def _required(values: dict[str, str], key: str) -> str:
    value = values.get(key)
    if not value:
        raise ForecastError(f"raw GRIB field is missing: {key}")
    return value


def _integer(values: dict[str, str], key: str) -> int:
    try:
        return int(_required(values, key))
    except ValueError as exc:
        raise ForecastError(f"raw GRIB integer is malformed: {key}") from exc


def _decimal(values: dict[str, str], key: str) -> Decimal:
    raw = _required(values, key)
    if not re.fullmatch(_DECIMAL, raw):
        raise ForecastError(f"raw GRIB decimal is malformed: {key}")
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ForecastError(f"raw GRIB decimal is malformed: {key}") from exc
    if not value.is_finite():
        raise ForecastError(f"raw GRIB decimal is non-finite: {key}")
    return value


def _decimal_value(raw: str, key: str) -> Decimal:
    if not re.fullmatch(_DECIMAL, raw):
        raise ForecastError(f"raw GRIB decimal is malformed: {key}")
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ForecastError(f"raw GRIB decimal is malformed: {key}") from exc
    if not value.is_finite():
        raise ForecastError(f"raw GRIB decimal is non-finite: {key}")
    return value


def _timestamp(values: dict[str, str], key: str) -> datetime:
    raw = _required(values, key)
    if not _TIME.fullmatch(raw):
        raise ForecastError(f"raw GRIB timestamp is malformed: {key}")
    try:
        return datetime.strptime(raw, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except ValueError as exc:
        raise ForecastError(f"raw GRIB timestamp is malformed: {key}") from exc


def _timestamp_value(raw: str) -> datetime:
    return _timestamp({"value": raw}, "value")


def _parameter(values: dict[str, str]) -> tuple[int, int, int]:
    raw = _required(values, "parameter").split("/")
    if len(raw) != 3:
        raise ForecastError("raw GRIB parameter is malformed")
    try:
        result = tuple(int(item) for item in raw)
    except ValueError as exc:
        raise ForecastError("raw GRIB parameter is malformed") from exc
    return result  # type: ignore[return-value]


def _longitude(value: Decimal) -> Decimal:
    if value < Decimal("-180") or value > Decimal("360"):
        raise ForecastError("raw GRIB longitude is outside accepted range")
    return value - Decimal("360") if value > Decimal("180") else value


def _require_utc(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ForecastError(f"raw GRIB {label} is not UTC")


def _validate_record(record: RawGribRecord, reference: datetime, start: int, end: int) -> None:
    _validate_record_for_measurement(
        record, reference, start, end, "TMAX", EXPECTED_PARAMETER, "MaxT"
    )


def _validate_record_for_measurement(
    record: RawGribRecord,
    reference: datetime,
    start: int,
    end: int,
    variable: str,
    parameter: tuple[int, int, int],
    label: str,
) -> None:
    _require_utc(record.reference_time, "record reference time")
    _require_utc(record.interval_start, "interval start")
    _require_utc(record.interval_end, "interval end")
    _require_utc(record.verification_time, "verification time")
    if record.reference_time != reference:
        raise ForecastError("raw GRIB records have different reference times")
    if record.variable != variable or record.level != "2 m above ground":
        raise ForecastError(f"raw GRIB variable or level is not reviewed {label}")
    if record.generating_process_code != 2:
        raise ForecastError("raw GRIB generating process is not Forecast")
    expected_statistical = 3 if label == "MinT" else 2
    if record.statistical_process_code != expected_statistical or record.time_processing_code != 2:
        raise ForecastError(f"raw GRIB statistical semantics are not reviewed {label}")
    if record.parameter != parameter or record.unit != "Kelvin":
        raise ForecastError(f"raw GRIB parameter or unit is not reviewed {label}")
    if record.interval_end <= record.interval_start:
        raise ForecastError("raw GRIB interval is not increasing")
    if record.interval_end - record.interval_start != timedelta(hours=end - start):
        raise ForecastError("raw GRIB interval duration is not the reviewed duration")
    if record.verification_time != record.interval_end:
        raise ForecastError("raw GRIB verification time is not interval end")
    # Exact datetime equality (not integer-truncated total_seconds()) so a
    # fractional/subsecond offset cannot hide inside int(total_seconds())'s
    # truncation and still pass as an on-the-hour horizon.
    if record.interval_start != reference + timedelta(hours=start):
        raise ForecastError("raw GRIB interval start horizon is unexpected")
    if record.interval_end != reference + timedelta(hours=end):
        raise ForecastError("raw GRIB interval end horizon is unexpected")
    if (
        record.grid_template != EXPECTED_GRID_TEMPLATE
        or record.nx != EXPECTED_NX
        or record.ny != EXPECTED_NY
        or record.dx != EXPECTED_DX
        or record.dy != EXPECTED_DY
    ):
        raise ForecastError("raw GRIB grid signature is not the reviewed family")
    if not record.latitude.is_finite() or not record.signed_longitude.is_finite():
        raise ForecastError("raw GRIB point is non-finite")
    if record.latitude < Decimal("-90") or record.latitude > Decimal("90"):
        raise ForecastError("raw GRIB latitude is invalid")
    if not record.kelvin.is_finite():
        raise ForecastError("raw GRIB Kelvin value is non-finite")
