from datetime import UTC, date

import pytest

from services.forecasting.domain import ForecastError
from services.forecasting.weather_calibration_grib import (
    POST2020_MINT_GRIB_FAMILY,
    mint_target_local_date,
    parse_wgrib2_max_t_evidence,
    parse_wgrib2_min_t_evidence,
)


def _mint_inventory() -> str:
    rows = (
        (1, "20240615040000", "20240615120000", "290.4"),
        (2, "20240616000000", "20240616120000", "294.3"),
        (3, "20240617000000", "20240617120000", "297.6"),
    )
    return "\n".join(
        (
            f"record {number}:\n"
            f"reference = 20240615040000\nvariable = TMIN\nlevel = 2 m above ground\n"
            f"generating_process = 2\nstatistical_process = 3\ntime_processing = 2\n"
            f"parameter = 0/0/5\nunit = Kelvin\nstart = {start}\nend = {end}\n"
            f"verification = {end}\nlat = 41.794091\nlon = 272.260017\nvalue = {value}\n"
            "grid_template = 30\nnx = 2145\nny = 1377\ndx = 2539.703\ndy = 2539.703\n"
        )
        for number, start, end, value in rows
    )


def test_real_shape_mint_inventory_and_exact_horizons() -> None:
    evidence = parse_wgrib2_min_t_evidence(_mint_inventory())
    assert evidence.family_identity == POST2020_MINT_GRIB_FAMILY
    assert [r.lead_to_midpoint_seconds // 3600 for r in evidence.records] == [4, 26, 50]
    assert [mint_target_local_date(r, "America/Chicago") for r in evidence.records] == [
        date(2024, 6, 15),
        date(2024, 6, 16),
        date(2024, 6, 17),
    ]


def test_mint_rejects_maxt_and_wrong_semantics() -> None:
    with pytest.raises(ForecastError):
        parse_wgrib2_min_t_evidence(_mint_inventory().replace("TMIN", "TMAX"))
    with pytest.raises(ForecastError):
        parse_wgrib2_min_t_evidence(
            _mint_inventory().replace("statistical_process = 3", "statistical_process = 2")
        )
    with pytest.raises(ForecastError):
        parse_wgrib2_min_t_evidence(
            _mint_inventory().replace("parameter = 0/0/5", "parameter = 0/0/4")
        )


def test_max_parser_rejects_real_shape_mint_inventory() -> None:
    with pytest.raises(ForecastError):
        parse_wgrib2_max_t_evidence(_mint_inventory())


@pytest.mark.parametrize(
    ("reference", "start", "end", "expected"),
    (
        ("20240310040000", "20240310040000", "20240310120000", date(2024, 3, 10)),
        ("20241103040000", "20241104000000", "20241104120000", date(2024, 11, 4)),
    ),
)
def test_mint_local_date_rule_handles_dst_boundaries(
    reference: str, start: str, end: str, expected: date
) -> None:
    from dataclasses import replace
    from datetime import datetime

    record = parse_wgrib2_min_t_evidence(_mint_inventory()).records[0]
    record = replace(
        record,
        reference_time=datetime.strptime(reference, "%Y%m%d%H%M%S").replace(tzinfo=UTC),
        interval_start=datetime.strptime(start, "%Y%m%d%H%M%S").replace(tzinfo=UTC),
        interval_end=datetime.strptime(end, "%Y%m%d%H%M%S").replace(tzinfo=UTC),
        verification_time=datetime.strptime(end, "%Y%m%d%H%M%S").replace(tzinfo=UTC),
    )
    assert mint_target_local_date(record, "America/Chicago") == expected
