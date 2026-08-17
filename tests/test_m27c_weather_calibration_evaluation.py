from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction

import pytest

from services.forecasting.domain import ForecastError
from services.forecasting.weather_calibration_coverage import LeadBucket
from services.forecasting.weather_calibration_evaluation import (
    ADJUSTMENT_METHOD,
    COVERAGE_GATE_COUNT,
    EXACT_TAIL_CONVENTION,
    FAMILY_WISE_ALPHA,
    HOLDOUT_START,
    PER_TEST_ALPHA,
    TWO_SIDED_TAIL_ALPHA,
    EvaluationRow,
    build_split_manifest,
    evaluate_frozen_horizon,
    evaluate_validation_walk_forward,
    exact_two_sided_binomial_region,
    validate_walk_forward_training,
)
from services.forecasting.weather_probability import EmpiricalResidualDistribution


def row(identifier: str, day: date, bucket: LeadBucket, lead: int) -> EvaluationRow:
    return EvaluationRow(identifier, day, bucket, lead, Decimal("70"), Decimal("71"))


def split_rows() -> tuple[EvaluationRow, ...]:
    values = []
    for bucket, lead in (
        (LeadBucket.ZERO_TO_24H, 54_000),
        (LeadBucket.TWENTY_FOUR_TO_48H, 140_400),
        (LeadBucket.FORTY_EIGHT_TO_72H, 226_800),
    ):
        values.extend(
            row(f"{lead}-{index}", HOLDOUT_START + timedelta(days=index), bucket, lead)
            for index in range(10)
        )
    values.append(row("train", date(2024, 1, 1), LeadBucket.ZERO_TO_24H, 54_000))
    values.append(row("validation", date(2025, 7, 1), LeadBucket.ZERO_TO_24H, 54_000))
    return tuple(values)


@pytest.mark.parametrize(
    ("trials", "level", "expected"),
    [
        (10, Decimal("0.50"), (1, 9)),
        (10, Decimal("0.80"), (4, 10)),
        (10, Decimal("0.90"), (6, 10)),
    ],
)
def test_exact_equal_tail_binomial_known_fixtures(
    trials: int, level: Decimal, expected: tuple[int, int]
) -> None:
    region = exact_two_sided_binomial_region(trials, level)
    assert (region.minimum_count, region.maximum_count) == expected


def test_split_manifest_freezes_all_nine_pre_holdout_regions() -> None:
    first = build_split_manifest(split_rows())
    second = build_split_manifest(tuple(reversed(split_rows())))
    assert first.identity == second.identity
    assert len(first.acceptance_regions) == 9
    assert first.train_ids == ("train",)
    assert first.validation_ids == ("validation",)
    assert len(first.holdout_ids) == 30


def test_split_manifest_binds_frozen_bonferroni_policy() -> None:
    manifest = build_split_manifest(split_rows())
    assert (
        Fraction(1, 20),
        9,
        Fraction(1, 180),
    ) == (FAMILY_WISE_ALPHA, COVERAGE_GATE_COUNT, PER_TEST_ALPHA)
    assert Fraction(1, 360) == TWO_SIDED_TAIL_ALPHA
    assert ADJUSTMENT_METHOD == "BONFERRONI"
    assert "P(K<=k)" in EXACT_TAIL_CONVENTION
    assert "P(K>=k)" in EXACT_TAIL_CONVENTION
    assert len(manifest.acceptance_regions) == COVERAGE_GATE_COUNT


def test_manifest_identity_changes_with_fixed_holdout_count() -> None:
    rows = split_rows()
    first = build_split_manifest(rows)
    changed = build_split_manifest(
        (*rows, row("extra", date(2026, 2, 1), LeadBucket.ZERO_TO_24H, 54_000))
    )
    assert first.identity != changed.identity


def test_walk_forward_rejects_same_date_future_and_cross_horizon() -> None:
    target = row("target", date(2025, 7, 2), LeadBucket.ZERO_TO_24H, 54_000)
    validate_walk_forward_training(
        (row("past", date(2025, 7, 1), LeadBucket.ZERO_TO_24H, 54_000),), target
    )
    with pytest.raises(ForecastError, match="leakage"):
        validate_walk_forward_training((replace_day(target, "same"),), target)
    with pytest.raises(ForecastError, match="crosses horizon"):
        validate_walk_forward_training(
            (row("other", date(2025, 7, 1), LeadBucket.TWENTY_FOUR_TO_48H, 140_400),),
            target,
        )


def replace_day(value: EvaluationRow, identifier: str) -> EvaluationRow:
    return EvaluationRow(
        identifier,
        value.local_target_date,
        value.lead_bucket,
        value.exact_midpoint_seconds,
        value.forecast_deg_f,
        value.observed_deg_f,
    )


def test_frozen_holdout_crps_baseline_intervals_and_gate_are_exact() -> None:
    rows = split_rows()
    manifest = build_split_manifest(rows)
    distribution = EmpiricalResidualDistribution((Decimal("0"), Decimal("2")))
    result = evaluate_frozen_horizon(
        residuals=distribution,
        rows=rows,
        manifest=manifest,
        lead_bucket=LeadBucket.ZERO_TO_24H,
        exact_midpoint_seconds=54_000,
    )
    assert result.mean_model_crps == Decimal("0.5")
    assert result.mean_raw_ndfd_crps == Decimal("1")
    assert result.raw_ndfd_mae == Decimal("1")
    assert result.bias_corrected_mae == Decimal("0")
    assert result.model_beats_baseline
    assert result.interval_coverage_counts == (
        (Decimal("0.50"), 10),
        (Decimal("0.80"), 10),
        (Decimal("0.90"), 10),
    )


def test_holdout_rows_cannot_be_smuggled_into_validation_walk_forward() -> None:
    rows = (
        *(
            row(
                f"train-{index}",
                date(2024, 1, 1) + timedelta(days=index),
                LeadBucket.ZERO_TO_24H,
                54_000,
            )
            for index in range(365)
        ),
        row("validation", date(2025, 7, 1), LeadBucket.ZERO_TO_24H, 54_000),
        row("holdout", date(2026, 1, 1), LeadBucket.ZERO_TO_24H, 54_000),
    )
    outputs = evaluate_validation_walk_forward(rows=rows)
    assert len(outputs) == 1
    assert outputs[0].evaluated_ids == ("validation",)


def test_evaluation_module_has_no_io_market_risk_or_execution_dependency() -> None:
    import inspect

    import services.forecasting.weather_calibration_evaluation as module

    source = inspect.getsource(module)
    forbidden = (
        "production_execution",
        "risk_engine",
        "MarketReference",
        "Forecast(",
        "open(",
        "subprocess",
        "requests",
        "httpx",
    )
    assert all(value not in source for value in forbidden)
