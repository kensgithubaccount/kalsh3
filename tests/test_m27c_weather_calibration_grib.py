from __future__ import annotations

from decimal import Decimal

import pytest

from services.forecasting.domain import ForecastError
from services.forecasting.weather_calibration_grib import (
    POST2020_GRIB_FAMILY,
    kelvin_to_fahrenheit,
    parse_wgrib2_max_t_evidence,
    target_local_date,
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
