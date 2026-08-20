from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from services.forecasting.domain import ForecastError
from services.forecasting.weather_calibration_grib import (
    POST2020_GRIB_FAMILY,
    RawGribEvidence,
    RawGribRecord,
    kelvin_to_fahrenheit,
    parse_wgrib2_max_t_evidence,
    target_local_date,
    validate_raw_grib_max_t_evidence,
)


def _extraction() -> str:
    sections = []
    for number, start, end, value, lon in (
        (1, "20240615120000", "20240616000000", "302", "272.260017"),
        (2, "20240616120000", "20240617000000", "307", "272.260017"),
        (3, "20240617120000", "20240618000000", "309.3", "272.260017"),
    ):
        sections.append(
            f"""record {number}:
reference = 20240615030000
variable = TMAX
level = 2 m above ground
generating_process = 2
statistical_process = 2
time_processing = 2
parameter = 0/0/4
unit = Kelvin
start = {start}
end = {end}
verification = {end}
lat = 41.794091
lon = {lon}
value = {value}
grid_template = 30
nx = 2145
ny = 1377
dx = 2539.703
dy = 2539.703
"""
        )
    return "\n".join(sections)


def test_raw_grib_fixture_accepts_three_horizons_and_normalizes_longitude() -> None:
    evidence = parse_wgrib2_max_t_evidence(_extraction())
    assert evidence.family_identity == POST2020_GRIB_FAMILY
    assert [record.lead_to_midpoint_seconds // 3600 for record in evidence.records] == [15, 39, 63]
    assert evidence.records[0].raw_longitude == Decimal("272.260017")
    assert evidence.records[0].signed_longitude == Decimal("-87.739983")
    assert target_local_date(evidence.records[0], "America/Chicago").isoformat() == "2024-06-15"


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("reference", "20240615020000"),
        ("variable", "TMP"),
        ("level", "surface"),
        ("generating_process", "0"),
        ("statistical_process", "3"),
        ("time_processing", "1"),
        ("end", "20240615235959"),
        ("grid_template", "31"),
        ("nx", "2144"),
        ("dx", "2539.704"),
    ),
)
def test_raw_grib_semantics_fail_closed(field: str, replacement: str) -> None:
    text = _extraction().replace(
        f"{field} = {dict(_fields())[field]}", f"{field} = {replacement}", 1
    )
    with pytest.raises(ForecastError):
        parse_wgrib2_max_t_evidence(text)


def _fields() -> tuple[tuple[str, str], ...]:
    return (
        ("reference", "20240615030000"),
        ("variable", "TMAX"),
        ("level", "2 m above ground"),
        ("generating_process", "2"),
        ("statistical_process", "2"),
        ("time_processing", "2"),
        ("end", "20240616000000"),
        ("grid_template", "30"),
        ("nx", "2145"),
        ("dx", "2539.703"),
    )


def test_raw_grib_rejects_extra_record_and_invalid_longitude() -> None:
    with pytest.raises(ForecastError, match="exactly three"):
        parse_wgrib2_max_t_evidence(_extraction() + _extraction().split("record 2:", 1)[1])
    with pytest.raises(ForecastError, match="longitude"):
        parse_wgrib2_max_t_evidence(_extraction().replace("272.260017", "361"))


def test_kelvin_conversion_is_exact_decimal() -> None:
    assert kelvin_to_fahrenheit(Decimal("302")) == Decimal("83.93")
    with pytest.raises(ForecastError):
        kelvin_to_fahrenheit(Decimal("NaN"))


def test_wgrib2_version_mismatch_fails_closed() -> None:
    with pytest.raises(ForecastError, match="version"):
        parse_wgrib2_max_t_evidence(_extraction(), wgrib2_version="3.7.0")


def test_parser_calls_the_authoritative_validator() -> None:
    """`parse_wgrib2_max_t_evidence` must call `validate_raw_grib_max_t_evidence`
    itself rather than duplicating its checks, so a directly-constructed
    `RawGribEvidence` claiming to have already passed the parser is subject
    to the exact same authority."""
    evidence = parse_wgrib2_max_t_evidence(_extraction())
    validate_raw_grib_max_t_evidence(evidence)  # already-valid evidence passes silently
    tampered = RawGribEvidence(
        "LEGACY_CHICAGO_MAXT_5KM_YGFZ98",
        evidence.records,
        evidence.extraction_policy_version,
        evidence.wgrib2_version,
        evidence.raw_grib_sha256,
        evidence.extraction_sha256,
    )
    with pytest.raises(ForecastError, match="family"):
        validate_raw_grib_max_t_evidence(tampered)
    fewer_records = RawGribEvidence(
        evidence.family_identity,
        evidence.records[:2],
        evidence.extraction_policy_version,
        evidence.wgrib2_version,
        evidence.raw_grib_sha256,
        evidence.extraction_sha256,
    )
    with pytest.raises(ForecastError, match="exactly three"):
        validate_raw_grib_max_t_evidence(fewer_records)


_REFERENCE = datetime(2026, 9, 1, 3, 0, 0, tzinfo=UTC)
_GRID = {
    "grid_template": 30,
    "nx": 2145,
    "ny": 1377,
    "dx": Decimal("2539.703"),
    "dy": Decimal("2539.703"),
}


def _record(
    number: int, start_hours: int, end_hours: int, *, reference: datetime = _REFERENCE
) -> RawGribRecord:
    start = reference + timedelta(hours=start_hours)
    end = reference + timedelta(hours=end_hours)
    return RawGribRecord(
        record_number=number,
        reference_time=reference,
        variable="TMAX",
        level="2 m above ground",
        generating_process_code=2,
        statistical_process_code=2,
        time_processing_code=2,
        parameter=(0, 0, 4),
        unit="Kelvin",
        interval_start=start,
        interval_end=end,
        verification_time=end,
        latitude=Decimal("41.794091"),
        raw_longitude=Decimal("272.260017"),
        signed_longitude=Decimal("-87.739983"),
        kelvin=Decimal("302"),
        **_GRID,  # type: ignore[arg-type]
    )


def _valid_evidence() -> RawGribEvidence:
    records = (_record(1, 9, 21), _record(2, 33, 45), _record(3, 57, 69))
    return RawGribEvidence(POST2020_GRIB_FAMILY, records, "policy-v1", "3.8.0", "raw", "extract")


def test_non_utc_reference_time_fails_closed() -> None:
    chicago_reference = _REFERENCE.astimezone(ZoneInfo("America/Chicago"))
    records = tuple(
        replace(record, reference_time=chicago_reference)
        for record in (_record(1, 9, 21), _record(2, 33, 45), _record(3, 57, 69))
    )
    evidence = RawGribEvidence(
        POST2020_GRIB_FAMILY, records, "policy-v1", "3.8.0", "raw", "extract"
    )
    with pytest.raises(ForecastError, match="UTC"):
        validate_raw_grib_max_t_evidence(evidence)


def test_naive_reference_time_fails_closed() -> None:
    naive_reference = _REFERENCE.replace(tzinfo=None)
    records = tuple(
        replace(record, reference_time=naive_reference)
        for record in (_record(1, 9, 21), _record(2, 33, 45), _record(3, 57, 69))
    )
    evidence = RawGribEvidence(
        POST2020_GRIB_FAMILY, records, "policy-v1", "3.8.0", "raw", "extract"
    )
    with pytest.raises(ForecastError, match="UTC"):
        validate_raw_grib_max_t_evidence(evidence)


def test_non_utc_interval_timestamp_fails_closed() -> None:
    evidence = _valid_evidence()
    tampered_first = replace(
        evidence.records[0],
        interval_start=evidence.records[0].interval_start.astimezone(ZoneInfo("America/Chicago")),
    )
    tampered = RawGribEvidence(
        evidence.family_identity,
        (tampered_first, evidence.records[1], evidence.records[2]),
        evidence.extraction_policy_version,
        evidence.wgrib2_version,
        evidence.raw_grib_sha256,
        evidence.extraction_sha256,
    )
    with pytest.raises(ForecastError, match="UTC"):
        validate_raw_grib_max_t_evidence(tampered)


def test_fractional_second_offset_trick_fails_closed() -> None:
    """int(total_seconds()) truncates a sub-second offset away; exact
    datetime equality must still catch a record whose interval_start is a
    fraction of a second off the reviewed on-the-hour horizon."""
    evidence = _valid_evidence()
    shift = timedelta(microseconds=500_000)
    off_by_a_fraction = replace(
        evidence.records[0],
        interval_start=evidence.records[0].interval_start + shift,
        interval_end=evidence.records[0].interval_end + shift,
        verification_time=evidence.records[0].verification_time + shift,
    )
    tampered = RawGribEvidence(
        evidence.family_identity,
        (off_by_a_fraction, evidence.records[1], evidence.records[2]),
        evidence.extraction_policy_version,
        evidence.wgrib2_version,
        evidence.raw_grib_sha256,
        evidence.extraction_sha256,
    )
    with pytest.raises(ForecastError, match="horizon"):
        validate_raw_grib_max_t_evidence(tampered)


def test_records_not_physically_ordered_1_2_3_fails_closed() -> None:
    r1, r2, r3 = _valid_evidence().records
    reordered = RawGribEvidence(
        POST2020_GRIB_FAMILY, (r2, r1, r3), "policy-v1", "3.8.0", "raw", "extract"
    )
    with pytest.raises(ForecastError, match="physically ordered"):
        validate_raw_grib_max_t_evidence(reordered)
