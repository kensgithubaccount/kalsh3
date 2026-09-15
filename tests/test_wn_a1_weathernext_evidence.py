from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from services.forecasting.wn_a1_domain import WnA1Error
from services.forecasting.wn_a1_weathernext_evidence import (
    MEMBER_COUNT,
    MODEL,
    UNIT,
    VARIABLE,
    EnsembleStatus,
    build_ensemble_evidence,
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
        latitude=LAT,
        longitude=LON,
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
