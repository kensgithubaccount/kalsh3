from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from services.forecasting.domain import ForecastError
from services.forecasting.weather_calibration import (
    CalibrationMeasurement,
    ReplayFidelity,
    build_residuals,
    parse_ghcnd_daily,
    parse_ndfd_descriptor,
    parse_ndfd_point_csv,
    target_local_date,
)
from services.forecasting.weather_source_authority import (
    PHYSICAL_WEATHER_SOURCES,
    parse_nws_station,
)

ROOT = Path(__file__).parent / "fixtures" / "m27c"
ACQUIRED = datetime(2026, 8, 16, 12, tzinfo=UTC)


def source_and_station():
    source = PHYSICAL_WEATHER_SOURCES["CLIMDW"]
    station = parse_nws_station(
        source, json.loads((ROOT / "kmdw-station.json").read_text()), ACQUIRED
    )
    return source, station


def evidence():
    source, station = source_and_station()
    descriptor = parse_ndfd_descriptor(
        (ROOT / "maxt-point-dataset.xml").read_bytes(),
        source,
        CalibrationMeasurement.DAILY_MAX,
        ACQUIRED,
    )
    point = parse_ndfd_point_csv(
        (ROOT / "maxt-chicago.csv").read_bytes(), descriptor, station, ACQUIRED
    )
    outcome = parse_ghcnd_daily((ROOT / "USW00014819-201806.dly").read_bytes(), source, ACQUIRED)
    return source, station, descriptor, point, outcome


def test_real_shape_binds_forecast_vintage_and_grid_point() -> None:
    _, _, descriptor, point, outcome = evidence()
    assert descriptor.forecast_reference_time == datetime(2018, 6, 20, 7, tzinfo=UTC)
    assert descriptor.valid_time_coordinates == tuple(
        datetime(2018, 6, day, 18, tzinfo=UTC) for day in (20, 21, 22)
    )
    assert point.rows[0].returned_latitude == Decimal("41.772")
    assert point.rows[0].returned_longitude == Decimal("-87.741")
    assert point.rows[0].requested_coordinate_display == "GridPointRequestedAt[41.784N_87.755W]"
    assert outcome.observations[0].mflag == ""


def test_minimum_temperature_semantics_use_grib_identity_not_variable_guess() -> None:
    source, _ = source_and_station()
    raw = (ROOT / "maxt-point-dataset.xml").read_text()
    for old, new in (
        ("Maximum_temperature_surface_12_Hour_Maximum", "Different_MinT_Name"),
        ("Maximum temperature", "Minimum temperature"),
        ("12_Hour Maximum", "12_Hour Minimum"),
        ("VAR_0-0-4_L1_I12_Hour_S2", "VAR_0-0-5_L1_I12_Hour_S3"),
        ('value="0 0 4"', 'value="0 0 5"'),
        ('value="Maximum"', 'value="Minimum"'),
    ):
        raw = raw.replace(old, new)
    descriptor = parse_ndfd_descriptor(raw, source, CalibrationMeasurement.DAILY_MIN, ACQUIRED)
    assert descriptor.grib_parameter == (0, 0, 5)
    assert descriptor.statistical_process == "Minimum"


def test_real_chicago_residuals_use_observed_minus_forecast() -> None:
    source, station, descriptor, point, outcome = evidence()
    rows = build_residuals(source, descriptor, point, outcome, station, source, ACQUIRED)
    assert [row.observed_tenths_c for row in rows] == [261, 200, 183]
    assert [row.forecast_deg_f for row in rows] == [
        Decimal("74.03"),
        Decimal("77.99"),
        Decimal("72.05"),
    ]
    assert [row.observed_deg_f for row in rows] == [
        Decimal("78.98"),
        Decimal("68"),
        Decimal("64.94"),
    ]
    assert [row.residual_deg_f for row in rows] == [
        Decimal("4.95"),
        Decimal("-9.99"),
        Decimal("-7.11"),
    ]
    assert all(
        row.replay_fidelity is ReplayFidelity.FORECAST_VINTAGED_CURRENT_OUTCOME_SNAPSHOT
        for row in rows
    )
    assert all(row.research_only and row.production_influence == Decimal("0") for row in rows)


@pytest.mark.parametrize(
    "field",
    [
        "Grib2_Parameter_Category",
        "Grib2_Generating_Process_Type",
        "Grib2_Statistical_Process_Type",
        "units",
    ],
)
def test_descriptor_semantic_conflicts_fail_closed(field: str) -> None:
    source, _ = source_and_station()
    raw = (ROOT / "maxt-point-dataset.xml").read_text()
    originals = {
        "Grib2_Parameter_Category": "Temperature",
        "Grib2_Generating_Process_Type": "Forecast",
        "Grib2_Statistical_Process_Type": "Maximum",
        "units": "K",
    }
    replacement = {
        "Grib2_Parameter_Category": "Observation",
        "Grib2_Generating_Process_Type": "Observation",
        "Grib2_Statistical_Process_Type": "Minimum",
        "units": "degC",
    }[field]
    raw = raw.replace(
        f'name="{field}" value="{originals[field]}"',
        f'name="{field}" value="{replacement}"',
    )
    with pytest.raises(ForecastError):
        parse_ndfd_descriptor(raw, source, CalibrationMeasurement.DAILY_MAX, ACQUIRED)


@pytest.mark.parametrize(
    "bad",
    [
        "<!DOCTYPE gridDataset>",
        'values spacing="regularPoint"',
        '<values spacing="discontiguousInterval" npts="3" start="5" end="65" '
        'resolution="24">35 11 59</values>',
    ],
)
def test_descriptor_unsafe_or_unsupported_time_fails_closed(bad: str) -> None:
    source, _ = source_and_station()
    raw = (ROOT / "maxt-point-dataset.xml").read_text()
    if bad.startswith("<!"):
        raw = raw.replace("<gridDataset", bad + "\n<gridDataset")
    elif bad.startswith("values"):
        raw = raw.replace('spacing="discontiguousInterval"', 'spacing="regularPoint"')
    else:
        raw = raw.replace(">11 35 59</values>", ">35 11 59</values>")
    with pytest.raises(ForecastError):
        parse_ndfd_descriptor(raw, source, CalibrationMeasurement.DAILY_MAX, ACQUIRED)


@pytest.mark.parametrize(
    "bad",
    [
        'Maximum_temperature_surface_12_Hour_Maximum[unit="degC"]',
        'Wrong_variable[unit="K"]',
        'time,station,latitude[unit="degrees_north"],longitude[unit="degrees_east"],Maximum_temperature_surface_12_Hour_Maximum[unit="K"]\n2018-06-20T18:00:00Z,GridPointRequestedAt[41.784N_87.755W],nan,-87.741,296.5\n2018-06-21T18:00:00Z,GridPointRequestedAt[41.784N_87.755W],41.772,-87.741,298.7\n2018-06-22T18:00:00Z,GridPointRequestedAt[41.784N_87.755W],41.772,-87.741,295.4',
    ],
)
def test_point_csv_conflicts_fail_closed(bad: str) -> None:
    source, station = source_and_station()
    descriptor = parse_ndfd_descriptor(
        (ROOT / "maxt-point-dataset.xml").read_bytes(),
        source,
        CalibrationMeasurement.DAILY_MAX,
        ACQUIRED,
    )
    raw = (ROOT / "maxt-chicago.csv").read_text()
    raw = (
        bad
        if bad.count("\n")
        else raw.replace('Maximum_temperature_surface_12_Hour_Maximum[unit="K"]', bad)
    )
    with pytest.raises(ForecastError):
        parse_ndfd_point_csv(raw, descriptor, station, ACQUIRED)


def test_requested_coordinate_binding_and_nearest_grid_are_distinct() -> None:
    _, station, descriptor, _, _ = evidence()
    raw = (ROOT / "maxt-chicago.csv").read_text().replace("41.784N_87.755W", "41.785N_87.755W")
    with pytest.raises(ForecastError):
        parse_ndfd_point_csv(raw, descriptor, station, ACQUIRED)


def test_ghcnd_exact_conversion_quality_and_flags() -> None:
    source, _, _, _, outcome = evidence()
    tmax = next(
        row
        for row in outcome.observations
        if row.local_date.day == 20 and row.measurement is CalibrationMeasurement.DAILY_MAX
    )
    assert tmax.raw_tenths_c == 261 and tmax.observed_deg_f == Decimal("78.98")
    assert tmax.sflag == "W" and tmax.qflag == "" and tmax.usable
    lines = (ROOT / "USW00014819-201806.dly").read_text().splitlines(True)
    tmax_line = list(lines[0])
    tmax_line[21 + (18 - 1) * 8 + 6] = "Q"
    lines[0] = "".join(tmax_line)
    bad = "".join(lines)
    flagged = parse_ghcnd_daily(bad, source, ACQUIRED)
    assert not next(
        row
        for row in flagged.observations
        if row.local_date.day == 18 and row.measurement is CalibrationMeasurement.DAILY_MAX
    ).usable


@pytest.mark.parametrize(
    ("zone", "utc", "expected"),
    [
        ("America/Chicago", datetime(2024, 3, 10, 6, tzinfo=UTC), "2024-03-10"),
        ("America/New_York", datetime(2024, 11, 3, 5, tzinfo=UTC), "2024-11-03"),
        ("America/Phoenix", datetime(2024, 7, 1, 7, tzinfo=UTC), "2024-07-01"),
        ("America/Los_Angeles", datetime(2024, 11, 3, 7, tzinfo=UTC), "2024-11-03"),
        ("America/Denver", datetime(2024, 3, 10, 7, tzinfo=UTC), "2024-03-10"),
    ],
)
def test_target_date_is_zoneinfo_aware(zone: str, utc: datetime, expected: str) -> None:
    assert str(target_local_date(utc, zone)) == expected
