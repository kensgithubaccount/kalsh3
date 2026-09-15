from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from services.forecasting.wn_a1_domain import WnA1Error
from services.forecasting.wn_a1_weathernext_evidence import (
    BILLING_PROJECT_ENV,
    GRID_DEGREES,
    MEMBER_COUNT,
    MODEL,
    UNIT,
    VARIABLE,
    EnsembleStatus,
    SelectedGridCoordinate,
    build_ensemble_evidence,
    select_nearest_grid_coordinate,
    to_0_360,
    validate_source_object,
)

INIT_TIME = datetime(2026, 9, 14, 0, 0, tzinfo=UTC)
SOURCE_OBJECT = "gs://weathernext3_spatial/weathernext_3_0_0/zarr/2026_to_present/20260914_00hr_XX_preds/predictions.zarr/"
LAT, LON = Decimal("41.80"), Decimal("-87.75")


def rows_for(
    sample_ids: range | list[int] = range(MEMBER_COUNT), hours: tuple[int, ...] = (0, 1)
) -> list[dict]:
    out = []
    for h in hours:
        valid = INIT_TIME + timedelta(hours=h)
        for sample in sample_ids:
            out.append(
                {
                    "sample": sample,
                    "lead_time_hours": h,
                    "lead_subtime_minutes": 0,
                    "valid_time": valid,
                    "value_kelvin": Decimal("293.15") + Decimal(sample) / Decimal(10),
                }
            )
    return out


def build(**overrides):
    kwargs = dict(
        model=MODEL,
        source_object=SOURCE_OBJECT,
        init_time=INIT_TIME,
        acquired_at=datetime(2026, 9, 14, 1, 0, tzinfo=UTC),
        variable=VARIABLE,
        requested_latitude=LAT,
        requested_longitude=LON,
        selected_latitude=LAT,
        selected_longitude=LON,
        unit=UNIT,
        raw_rows=rows_for(),
        source_content_hash="a" * 64,
    )
    kwargs.update(overrides)
    return build_ensemble_evidence(**kwargs)


def test_exact_64_member_ingestion_complete() -> None:
    result = build()
    assert result.status is EnsembleStatus.COMPLETE
    assert result.evidence is not None
    assert len(result.evidence.members) == MEMBER_COUNT * 2  # 2 retained hours
    assert {m.sample for m in result.evidence.members if m.lead_time_hours == 0} == set(
        range(MEMBER_COUNT)
    )
    assert result.evidence.research_only is True
    assert result.evidence.production_influence == Decimal(0)


def test_missing_member_is_incomplete_not_an_error() -> None:
    rows = rows_for(sample_ids=range(MEMBER_COUNT - 1))  # drop sample 63
    result = build(raw_rows=rows)
    assert result.status is EnsembleStatus.INCOMPLETE
    assert result.evidence is None
    assert 63 in next(iter(result.missing_samples_by_hour.values()))


def test_duplicate_member_within_one_hour_rejected() -> None:
    rows = rows_for()
    rows.append(dict(rows[0]))  # duplicate sample 0, hour 0
    with pytest.raises(WnA1Error, match="duplicate"):
        build(raw_rows=rows)


def test_wrong_variable_rejected() -> None:
    with pytest.raises(WnA1Error, match="variable"):
        build(variable="temperature_2m")


def test_wrong_model_version_rejected() -> None:
    with pytest.raises(WnA1Error, match="model"):
        build(model="weathernext_2_0_0")


def test_mismatched_valid_time_reconstruction_rejected() -> None:
    rows = rows_for(hours=(0,))
    rows[0] = dict(rows[0])
    rows[0]["valid_time"] = INIT_TIME + timedelta(hours=5)  # does not match lead_time_hours=0
    with pytest.raises(WnA1Error, match="valid_time"):
        build(raw_rows=rows)


def test_invalid_unit_rejected() -> None:
    with pytest.raises(WnA1Error, match="unit"):
        build(unit="F")


def test_out_of_range_sample_rejected() -> None:
    rows = rows_for(hours=(0,))
    rows[0] = dict(rows[0])
    rows[0]["sample"] = 64
    with pytest.raises(WnA1Error, match="sample"):
        build(raw_rows=rows)


def test_negative_lead_time_rejected() -> None:
    rows = rows_for(hours=(0,))
    rows[0] = dict(rows[0])
    rows[0]["lead_time_hours"] = -1
    with pytest.raises(WnA1Error, match="lead_time"):
        build(raw_rows=rows)


def test_naive_valid_time_rejected() -> None:
    rows = rows_for(hours=(0,))
    rows[0] = dict(rows[0])
    rows[0]["valid_time"] = datetime(2026, 9, 14, 0, 0)  # naive
    with pytest.raises(WnA1Error):
        build(raw_rows=rows)


def test_source_object_mismatched_init_time_rejected() -> None:
    with pytest.raises(WnA1Error, match="source object"):
        build(init_time=datetime(2026, 9, 15, 0, 0, tzinfo=UTC))


def test_source_object_wrong_layout_rejected() -> None:
    with pytest.raises(WnA1Error):
        validate_source_object("gs://weathernext3_spatial/wrong/layout", INIT_TIME)


def test_evidence_identity_is_deterministic_and_content_sensitive() -> None:
    first = build().evidence
    second = build().evidence
    assert first is not None and second is not None
    assert first.evidence_identity == second.evidence_identity
    rows = rows_for()
    rows[0] = dict(rows[0])
    rows[0]["value_kelvin"] = rows[0]["value_kelvin"] + Decimal("1")
    changed = build(raw_rows=rows).evidence
    assert changed is not None
    assert changed.evidence_identity != first.evidence_identity


def test_evidence_retains_both_requested_and_selected_coordinates() -> None:
    evidence = build(
        requested_latitude=LAT,
        requested_longitude=LON,
        selected_latitude=Decimal("41.80"),
        selected_longitude=Decimal("-87.75"),
    ).evidence
    assert evidence is not None
    assert evidence.requested_latitude == LAT
    assert evidence.requested_longitude == LON
    assert evidence.selected_latitude == Decimal("41.80")
    assert evidence.selected_longitude == Decimal("-87.75")


def test_evidence_identity_is_sensitive_to_selected_coordinate() -> None:
    """Item 2: the selected grid coordinate must be bound into evidence identity."""
    same_selection = build().evidence
    different_selection = build(
        selected_latitude=Decimal("41.85"), selected_longitude=Decimal("-87.75")
    ).evidence
    assert same_selection is not None and different_selection is not None
    assert same_selection.evidence_identity != different_selection.evidence_identity


def test_unexpectedly_distant_selected_coordinate_rejected() -> None:
    with pytest.raises(WnA1Error, match="unexpectedly distant"):
        build(selected_latitude=Decimal("50.00"), selected_longitude=LON)


# --- Item 2: coordinate selection by value, never raw index arithmetic ------------------


def test_longitude_conversion_to_0_360() -> None:
    assert to_0_360(Decimal("-87.75")) == Decimal("272.25")
    assert to_0_360(Decimal("10")) == Decimal("10")


def test_coordinate_selection_by_value_not_raw_index_arithmetic() -> None:
    """The old code computed ``round(latitude / 0.05)`` as a raw array index -- for
    Chicago's 41.80 latitude that is ``round(41.80 / 0.05) == 836``. A real global
    0.05-degree grid is not guaranteed to start at 0 and run upward; a latitude axis
    running from 90 down to -90 (a common global-grid convention) places 41.80 at index
    964, and index 836 actually holds 48.2 degrees -- a materially different cell. This
    proves selection is done by actual coordinate VALUE lookup, not index arithmetic.
    """
    lat_0p05 = [Decimal(90) - Decimal(i) * GRID_DEGREES for i in range(3601)]  # 90 -> -90
    lon_0p05 = [Decimal(i) * GRID_DEGREES for i in range(7200)]  # 0 -> 359.95, 0..360 convention

    assert lat_0p05[836] == Decimal("48.20")  # the OLD raw-index result -- wrong cell
    assert lat_0p05[836] != Decimal("41.80")

    selection = select_nearest_grid_coordinate(
        lat_0p05=lat_0p05,
        lon_0p05=lon_0p05,
        requested_latitude=Decimal("41.80"),
        requested_longitude=Decimal("-87.75"),
    )
    assert selection.lat_index == 964
    assert selection.lat_index != 836
    assert selection.selected_latitude == Decimal("41.80")
    assert selection.selected_longitude == Decimal("-87.75")


def test_coordinate_selection_picks_actual_nearest_value_when_off_grid() -> None:
    lat_0p05 = [Decimal(90) - Decimal(i) * GRID_DEGREES for i in range(3601)]
    lon_0p05 = [Decimal(i) * GRID_DEGREES for i in range(7200)]
    selection = select_nearest_grid_coordinate(
        lat_0p05=lat_0p05,
        lon_0p05=lon_0p05,
        requested_latitude=Decimal("41.79"),  # off-grid; nearest is still 41.80
        requested_longitude=Decimal("-87.77"),  # off-grid; nearest is still 272.25 -> -87.75
    )
    assert selection.selected_latitude == Decimal("41.80")
    assert selection.selected_longitude == Decimal("-87.75")


def test_coordinate_selection_rejects_empty_arrays() -> None:
    with pytest.raises(WnA1Error, match="missing or empty"):
        select_nearest_grid_coordinate(
            lat_0p05=[], lon_0p05=[Decimal(0)], requested_latitude=LAT, requested_longitude=LON
        )


def test_selected_grid_coordinate_is_a_plain_dataclass() -> None:
    coord = SelectedGridCoordinate(0, 0, Decimal("41.80"), Decimal("-87.75"))
    assert coord.lat_index == 0


# --- Item 1: Requester Pays / billing project -------------------------------------------


def test_billing_project_env_var_name_is_documented_and_non_secret() -> None:
    assert BILLING_PROJECT_ENV == "WN_A1_WEATHERNEXT_BILLING_PROJECT"


def test_gcs_reader_fails_clearly_without_billing_project(monkeypatch) -> None:
    monkeypatch.delenv(BILLING_PROJECT_ENV, raising=False)
    from services.forecasting.wn_a1_weathernext_evidence import gcs_zarr_reader

    try:
        import gcsfs  # noqa: F401
        import zarr  # noqa: F401
    except ImportError:
        pytest.skip("optional weathernext dependency group not installed in this environment")
    with pytest.raises(WnA1Error, match="Requester Pays billing project"):
        gcs_zarr_reader(
            SOURCE_OBJECT,
            INIT_TIME,
            LAT,
            LON,
            INIT_TIME,
            INIT_TIME + timedelta(hours=1),
        )


def test_gcs_reader_fails_clearly_without_optional_dependencies(monkeypatch) -> None:
    import builtins

    monkeypatch.setenv(BILLING_PROJECT_ENV, "some-billing-project")
    real_import = builtins.__import__

    def _blocked_import(name, *args, **kwargs):
        if name in ("gcsfs", "zarr"):
            raise ImportError(f"{name} not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked_import)
    from services.forecasting.wn_a1_weathernext_evidence import gcs_zarr_reader

    with pytest.raises(WnA1Error, match="optional 'weathernext' dependency group"):
        gcs_zarr_reader(
            SOURCE_OBJECT,
            INIT_TIME,
            LAT,
            LON,
            INIT_TIME,
            INIT_TIME + timedelta(hours=1),
        )
