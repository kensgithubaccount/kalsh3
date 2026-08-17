from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction

import pytest

from services.forecasting.domain import ForecastError
from services.forecasting.weather_calibration import CalibrationMeasurement, ReplayFidelity
from services.forecasting.weather_calibration_coverage import LeadBucket
from services.forecasting.weather_calibration_evaluation import (
    ADJUSTMENT_METHOD,
    COVERAGE_GATE_COUNT,
    EXACT_TAIL_CONVENTION,
    FAMILY_WISE_ALPHA,
    HOLDOUT_START,
    PER_TEST_ALPHA,
    TRAIN_START,
    TWO_SIDED_TAIL_ALPHA,
    VALIDATION_END,
    EvaluationRow,
    build_split_manifest,
    evaluate_frozen_horizon,
    evaluate_validation_walk_forward,
    exact_two_sided_binomial_region,
    validate_walk_forward_training,
)
from services.forecasting.weather_probability import (
    TypedResidual,
    WeatherCalibrationModelIdentity,
    WeatherResidualPopulation,
)


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
    values.append(
        EvaluationRow(
            "train", date(2024, 1, 1), LeadBucket.ZERO_TO_24H, 54_000, Decimal("70"), Decimal("70")
        )
    )
    values.append(
        EvaluationRow(
            "validation",
            date(2025, 7, 1),
            LeadBucket.ZERO_TO_24H,
            54_000,
            Decimal("70"),
            Decimal("72"),
        )
    )
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


def population_for_evaluation_rows(
    values: tuple[EvaluationRow, ...],
    *,
    training_end: date = VALIDATION_END,
    lead_bucket: LeadBucket = LeadBucket.ZERO_TO_24H,
    midpoint: int = 54_000,
) -> WeatherResidualPopulation:
    selected = tuple(value for value in values if value.lead_bucket is lead_bucket)
    typed = tuple(
        TypedResidual(
            value.residual_id,
            "CLIMDW",
            "KMDW",
            "USW00014819",
            CalibrationMeasurement.DAILY_MAX,
            datetime(2025, 1, 1, 3, tzinfo=UTC),
            datetime(2025, 1, 1, 18, tzinfo=UTC),
            value.local_target_date,
            midpoint,
            value.forecast_deg_f,
            value.observed_deg_f,
            value.observed_deg_f - value.forecast_deg_f,
            "M27C_TEST_AUTHORITY",
            ReplayFidelity.FORECAST_VINTAGED_CURRENT_OUTCOME_SNAPSHOT,
            True,
            Decimal("0"),
        )
        for value in selected
    )
    identity = WeatherCalibrationModelIdentity(
        "test-model",
        "test-policy",
        "test-kind",
        "test-claim",
        "test-mapping",
        "M27C_TEST_AUTHORITY",
        lead_bucket,
        midpoint,
        TRAIN_START,
        training_end,
        "test-coverage",
        "test-population",
        len(typed),
        365,
        "V1_OPERATING_SAFETY_FLOOR",
    )
    return WeatherResidualPopulation(
        identity,
        typed,
        tuple(value.residual_id for value in typed),
        tuple(value.residual_deg_f for value in typed),
        ReplayFidelity.FORECAST_VINTAGED_CURRENT_OUTCOME_SNAPSHOT,
    )


def incident_rows() -> tuple[EvaluationRow, ...]:
    values: list[EvaluationRow] = []
    for bucket, midpoint in (
        (LeadBucket.ZERO_TO_24H, 54_000),
        (LeadBucket.TWENTY_FOUR_TO_48H, 140_400),
        (LeadBucket.FORTY_EIGHT_TO_72H, 226_800),
    ):
        values.extend(
            row(
                f"{midpoint}-pre-{index}",
                date(2024, 1, 1) + timedelta(days=index),
                bucket,
                midpoint,
            )
            for index in range(725)
        )
        values.extend(
            row(
                f"{midpoint}-holdout-{index}",
                HOLDOUT_START + timedelta(days=index),
                bucket,
                midpoint,
            )
            for index in range(10)
        )
    return tuple(values)


def incident_population(
    values: tuple[EvaluationRow, ...],
    *,
    bucket: LeadBucket = LeadBucket.ZERO_TO_24H,
    midpoint: int = 54_000,
) -> WeatherResidualPopulation:
    return population_for_evaluation_rows(
        tuple(value for value in values if value.local_target_date < HOLDOUT_START),
        lead_bucket=bucket,
        midpoint=midpoint,
    )


def test_frozen_holdout_crps_baseline_intervals_and_gate_are_exact() -> None:
    rows = split_rows()
    manifest = build_split_manifest(rows)
    population = population_for_evaluation_rows(
        tuple(row for row in rows if row.local_target_date < HOLDOUT_START)
    )
    result = evaluate_frozen_horizon(
        population=population,
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


def test_final_holdout_accepts_exact_train_plus_validation_population() -> None:
    rows = incident_rows()
    manifest = build_split_manifest(rows)
    population = incident_population(rows)
    assert population.identity.sample_count == 725
    result = evaluate_frozen_horizon(
        population=population,
        rows=rows,
        manifest=manifest,
        lead_bucket=LeadBucket.ZERO_TO_24H,
        exact_midpoint_seconds=54_000,
    )
    assert len(result.evaluated_ids) == 10
    assert population.production_influence == Decimal("0")


@pytest.mark.parametrize(
    "case",
    [
        "train-only-542",
        "omit-validation",
        "holdout-id",
        "2026-row",
        "wrong-midpoint",
        "cross-horizon",
        "count-mismatch",
        "production-influence",
    ],
)
def test_final_holdout_rejects_unprovenance_population(case: str) -> None:
    rows = incident_rows()
    manifest = build_split_manifest(rows)
    population = incident_population(rows)
    if case == "train-only-542":
        train = tuple(
            value
            for value in rows
            if value.lead_bucket is LeadBucket.ZERO_TO_24H
            and TRAIN_START <= value.local_target_date <= date(2025, 6, 30)
        )
        train = train[:542]
        population = population_for_evaluation_rows(train, training_end=date(2025, 6, 30))
        assert population.identity.sample_count == 542
    elif case == "omit-validation":
        population = incident_population(rows)
        population = replace(
            population,
            rows=population.rows[:-1],
            residual_ids=population.residual_ids[:-1],
            residuals=population.residuals[:-1],
            identity=replace(population.identity, sample_count=724),
        )
    elif case == "holdout-id":
        population = population_for_evaluation_rows(
            tuple(value for value in rows if value.lead_bucket is LeadBucket.ZERO_TO_24H)[:726]
        )
    elif case == "2026-row":
        zero_rows = tuple(value for value in rows if value.lead_bucket is LeadBucket.ZERO_TO_24H)
        population = population_for_evaluation_rows(
            (
                *zero_rows[:725],
                row("2026", date(2026, 2, 1), LeadBucket.ZERO_TO_24H, 54_000),
            )
        )
    elif case == "wrong-midpoint":
        population = replace(
            population,
            identity=replace(population.identity, exact_midpoint_seconds=140_400),
            rows=tuple(
                replace(value, lead_to_valid_coordinate_seconds=140_400)
                for value in population.rows
            ),
        )
    elif case == "cross-horizon":
        population = incident_population(
            rows, bucket=LeadBucket.TWENTY_FOUR_TO_48H, midpoint=140_400
        )
    elif case == "count-mismatch":
        population = replace(population, identity=replace(population.identity, sample_count=724))
    elif case == "production-influence":
        population = replace(population, production_influence=Decimal("0.01"))
    with pytest.raises(ForecastError):
        evaluate_frozen_horizon(
            population=population,
            rows=rows,
            manifest=manifest,
            lead_bucket=LeadBucket.ZERO_TO_24H,
            exact_midpoint_seconds=54_000,
        )


def test_final_holdout_population_order_is_not_semantic() -> None:
    rows = incident_rows()
    manifest = build_split_manifest(rows)
    population = incident_population(rows)
    reversed_population = incident_population(tuple(reversed(rows)))
    assert population.residual_ids != reversed_population.residual_ids
    assert evaluate_frozen_horizon(
        population=reversed_population,
        rows=rows,
        manifest=manifest,
        lead_bucket=LeadBucket.ZERO_TO_24H,
        exact_midpoint_seconds=54_000,
    ).evaluated_ids


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
