"""Pure locked evaluation policy for M27C Part 2B2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from fractions import Fraction
from math import comb

from services.market_universe.domain import stable_hash

from .domain import ForecastError
from .weather_calibration_coverage import LeadBucket
from .weather_probability import (
    INTERVAL_LEVELS,
    ZERO,
    EmpiricalResidualDistribution,
    WeatherResidualPopulation,
)

POLICY_VERSION = "m27c-part2b2-weather-evaluation-v2"
TRAIN_START = date(2024, 1, 1)
TRAIN_END = date(2025, 6, 30)
VALIDATION_START = date(2025, 7, 1)
VALIDATION_END = date(2025, 12, 31)
HOLDOUT_START = date(2026, 1, 1)
HOLDOUT_END = date(2026, 7, 31)
FAMILY_WISE_ALPHA = Fraction(1, 20)
COVERAGE_GATE_COUNT = 9
PER_TEST_ALPHA = Fraction(1, 180)
TWO_SIDED_TAIL_ALPHA = Fraction(1, 360)
ADJUSTMENT_METHOD = "BONFERRONI"
EXACT_TAIL_CONVENTION = (
    "REJECT_LOW_IF_P(K<=k)<=TWO_SIDED_TAIL_ALPHA;"
    "REJECT_HIGH_IF_P(K>=k)<=TWO_SIDED_TAIL_ALPHA;"
    "EQUALITY_REJECTED"
)


@dataclass(frozen=True, slots=True)
class EvaluationRow:
    residual_id: str
    local_target_date: date
    lead_bucket: LeadBucket
    exact_midpoint_seconds: int
    forecast_deg_f: Decimal
    observed_deg_f: Decimal


@dataclass(frozen=True, slots=True)
class BinomialAcceptanceRegion:
    trials: int
    nominal_coverage: Decimal
    minimum_count: int
    maximum_count: int


@dataclass(frozen=True, slots=True)
class WeatherEvaluationSplitManifest:
    identity: str
    train_ids: tuple[str, ...]
    validation_ids: tuple[str, ...]
    holdout_ids: tuple[str, ...]
    holdout_counts: tuple[tuple[LeadBucket, int], ...]
    acceptance_regions: tuple[tuple[LeadBucket, BinomialAcceptanceRegion], ...]


@dataclass(frozen=True, slots=True)
class HorizonEvaluation:
    identity: str
    lead_bucket: LeadBucket
    exact_midpoint_seconds: int
    evaluated_ids: tuple[str, ...]
    mean_model_crps: Decimal
    mean_raw_ndfd_crps: Decimal
    raw_ndfd_mae: Decimal
    bias_corrected_mae: Decimal
    interval_coverage_counts: tuple[tuple[Decimal, int], ...]
    mean_interval_widths: tuple[tuple[Decimal, Decimal], ...]
    residual_mean: Decimal
    residual_median: Decimal
    boundary_probability_count: int
    model_beats_baseline: bool
    coverage_gates_pass: bool


def exact_two_sided_binomial_region(
    trials: int, nominal_coverage: Decimal
) -> BinomialAcceptanceRegion:
    """Equal-tail exact Bonferroni-adjusted acceptance region.

    There are nine fixed gates: three horizons by three interval levels.
    Reject low counts when P(K <= k) <= 1/360 and high counts when
    P(K >= k) <= 1/360. Equality is rejected, making the retained integer
    interval the smallest deterministic equal-tail region under this convention.
    """
    if trials <= 0 or nominal_coverage not in INTERVAL_LEVELS:
        raise ForecastError("unsupported exact-binomial gate")
    probability = Fraction(nominal_coverage)
    masses = tuple(
        Fraction(comb(trials, count)) * probability**count * (1 - probability) ** (trials - count)
        for count in range(trials + 1)
    )
    lower = 0
    cumulative = Fraction(0)
    for count, mass in enumerate(masses):
        cumulative += mass
        if cumulative <= TWO_SIDED_TAIL_ALPHA:
            lower = count + 1
        else:
            break
    upper = trials
    cumulative = Fraction(0)
    for count in range(trials, -1, -1):
        cumulative += masses[count]
        if cumulative <= TWO_SIDED_TAIL_ALPHA:
            upper = count - 1
        else:
            break
    return BinomialAcceptanceRegion(trials, nominal_coverage, lower, upper)


def build_split_manifest(rows: tuple[EvaluationRow, ...]) -> WeatherEvaluationSplitManifest:
    if len({row.residual_id for row in rows}) != len(rows):
        raise ForecastError("evaluation rows contain duplicate identities")
    ordered = tuple(sorted(rows, key=lambda row: (row.local_target_date, row.residual_id)))
    train = tuple(
        row.residual_id for row in ordered if TRAIN_START <= row.local_target_date <= TRAIN_END
    )
    validation = tuple(
        row.residual_id
        for row in ordered
        if VALIDATION_START <= row.local_target_date <= VALIDATION_END
    )
    holdout_rows = tuple(
        row for row in ordered if HOLDOUT_START <= row.local_target_date <= HOLDOUT_END
    )
    holdout = tuple(row.residual_id for row in holdout_rows)
    counts = tuple(
        (bucket, sum(row.lead_bucket is bucket for row in holdout_rows)) for bucket in LeadBucket
    )
    if any(count <= 0 for _, count in counts):
        raise ForecastError("every horizon requires a fixed nonempty holdout")
    regions = tuple(
        (bucket, exact_two_sided_binomial_region(count, level))
        for bucket, count in counts
        for level in INTERVAL_LEVELS
    )
    material = (
        POLICY_VERSION,
        TRAIN_START.isoformat(),
        TRAIN_END.isoformat(),
        VALIDATION_START.isoformat(),
        VALIDATION_END.isoformat(),
        HOLDOUT_START.isoformat(),
        HOLDOUT_END.isoformat(),
        train,
        validation,
        holdout,
        tuple((bucket.value, count) for bucket, count in counts),
        tuple(
            (
                bucket.value,
                str(region.nominal_coverage),
                region.trials,
                region.minimum_count,
                region.maximum_count,
            )
            for bucket, region in regions
        ),
        str(FAMILY_WISE_ALPHA),
        COVERAGE_GATE_COUNT,
        str(PER_TEST_ALPHA),
        str(TWO_SIDED_TAIL_ALPHA),
        ADJUSTMENT_METHOD,
        EXACT_TAIL_CONVENTION,
    )
    return WeatherEvaluationSplitManifest(
        stable_hash(material), train, validation, holdout, counts, regions
    )


def validate_walk_forward_training(
    training_rows: tuple[EvaluationRow, ...], target: EvaluationRow
) -> None:
    if any(row.local_target_date >= target.local_target_date for row in training_rows):
        raise ForecastError("walk-forward training contains target-date or future leakage")
    if any(
        row.lead_bucket is not target.lead_bucket
        or row.exact_midpoint_seconds != target.exact_midpoint_seconds
        for row in training_rows
    ):
        raise ForecastError("walk-forward training crosses horizon")


def evaluate_frozen_horizon(
    *,
    population: WeatherResidualPopulation,
    rows: tuple[EvaluationRow, ...],
    manifest: WeatherEvaluationSplitManifest,
    lead_bucket: LeadBucket,
    exact_midpoint_seconds: int,
) -> HorizonEvaluation:
    """Evaluate the frozen holdout only from the frozen TRAIN + VALIDATION population."""
    _validate_final_holdout_population(
        population=population,
        rows=rows,
        manifest=manifest,
        lead_bucket=lead_bucket,
        exact_midpoint_seconds=exact_midpoint_seconds,
    )
    return _evaluate_frozen_horizon(
        residuals=EmpiricalResidualDistribution(population.residuals),
        rows=rows,
        manifest=manifest,
        lead_bucket=lead_bucket,
        exact_midpoint_seconds=exact_midpoint_seconds,
    )


def _validate_final_holdout_population(
    *,
    population: WeatherResidualPopulation,
    rows: tuple[EvaluationRow, ...],
    manifest: WeatherEvaluationSplitManifest,
    lead_bucket: LeadBucket,
    exact_midpoint_seconds: int,
) -> None:
    identity = population.identity
    if identity.training_start != TRAIN_START or identity.training_end != VALIDATION_END:
        raise ForecastError("final holdout population is not frozen TRAIN + VALIDATION")
    if identity.lead_bucket is not lead_bucket:
        raise ForecastError("final holdout population crosses horizon")
    if identity.exact_midpoint_seconds != exact_midpoint_seconds:
        raise ForecastError("final holdout population midpoint conflicts")
    if (
        not isinstance(population.production_influence, Decimal)
        or population.production_influence != ZERO
    ):
        raise ForecastError("final holdout population has production influence")
    if population.research_only is not True:
        raise ForecastError("final holdout population is not research-only")
    if not (
        len(population.rows)
        == len(population.residual_ids)
        == len(population.residuals)
        == identity.sample_count
    ):
        raise ForecastError("final holdout population identity/count mismatch")
    row_ids = tuple(row.residual_id for row in population.rows)
    if set(row_ids) != set(population.residual_ids) or len(set(row_ids)) != len(row_ids):
        raise ForecastError("final holdout population row IDs mismatch")
    residual_by_id = dict(zip(population.residual_ids, population.residuals, strict=True))
    if residual_by_id != {row.residual_id: row.residual_deg_f for row in population.rows}:
        raise ForecastError("final holdout population residuals mismatch")
    if any(
        row.lead_to_valid_coordinate_seconds != exact_midpoint_seconds
        or row.local_target_date >= HOLDOUT_START
        or row.research_only is not True
        or not isinstance(row.production_influence, Decimal)
        or row.production_influence != ZERO
        for row in population.rows
    ):
        raise ForecastError("final holdout population contains invalid row provenance")

    train_or_validation = set(manifest.train_ids) | set(manifest.validation_ids)
    expected_ids = {
        row.residual_id
        for row in rows
        if row.residual_id in train_or_validation
        and row.lead_bucket is lead_bucket
        and row.exact_midpoint_seconds == exact_midpoint_seconds
    }
    if set(population.residual_ids) != expected_ids:
        raise ForecastError("final holdout population does not match TRAIN + VALIDATION manifest")
    if set(population.residual_ids) & set(manifest.holdout_ids):
        raise ForecastError("final holdout population contains holdout identity")


def _evaluate_frozen_horizon(
    *,
    residuals: EmpiricalResidualDistribution,
    rows: tuple[EvaluationRow, ...],
    manifest: WeatherEvaluationSplitManifest,
    lead_bucket: LeadBucket,
    exact_midpoint_seconds: int,
) -> HorizonEvaluation:
    selected = tuple(
        row
        for row in rows
        if row.residual_id in manifest.holdout_ids
        and row.lead_bucket is lead_bucket
        and row.exact_midpoint_seconds == exact_midpoint_seconds
    )
    if not selected:
        raise ForecastError("frozen horizon evaluation is empty")
    if any(
        row.local_target_date < HOLDOUT_START or row.local_target_date > HOLDOUT_END
        for row in selected
    ):
        raise ForecastError("frozen horizon evaluation escaped holdout")
    model_crps: list[Decimal] = []
    raw_errors: list[Decimal] = []
    corrected_errors: list[Decimal] = []
    coverage = {level: 0 for level in INTERVAL_LEVELS}
    widths = {level: ZERO for level in INTERVAL_LEVELS}
    boundary = 0
    for row in selected:
        predictive = residuals.shifted(row.forecast_deg_f)
        model_crps.append(predictive.crps(row.observed_deg_f))
        raw_errors.append(abs(row.forecast_deg_f - row.observed_deg_f))
        corrected_errors.append(abs(row.forecast_deg_f + residuals.median - row.observed_deg_f))
        for level in INTERVAL_LEVELS:
            low, high = predictive.interval(level)
            coverage[level] += low <= row.observed_deg_f <= high
            widths[level] += high - low
        # A deterministic diagnostic threshold at the observation records whether
        # finite empirical support places all mass on one side of that value.
        count = predictive.predicate_count("GT", row.observed_deg_f)
        boundary += count in {0, predictive.count}
    count_decimal = Decimal(len(selected))
    regions = {
        region.nominal_coverage: region
        for bucket, region in manifest.acceptance_regions
        if bucket is lead_bucket
    }
    coverage_pass = all(
        regions[level].minimum_count <= coverage[level] <= regions[level].maximum_count
        for level in INTERVAL_LEVELS
    )
    mean_model = sum(model_crps, ZERO) / count_decimal
    mean_raw = sum(raw_errors, ZERO) / count_decimal
    material = (
        POLICY_VERSION,
        manifest.identity,
        lead_bucket.value,
        exact_midpoint_seconds,
        tuple(row.residual_id for row in selected),
        tuple(map(str, residuals.values)),
        str(mean_model),
        str(mean_raw),
        tuple((str(level), coverage[level]) for level in INTERVAL_LEVELS),
        coverage_pass,
    )
    return HorizonEvaluation(
        stable_hash(material),
        lead_bucket,
        exact_midpoint_seconds,
        tuple(row.residual_id for row in selected),
        mean_model,
        mean_raw,
        mean_raw,
        sum(corrected_errors, ZERO) / count_decimal,
        tuple((level, coverage[level]) for level in INTERVAL_LEVELS),
        tuple((level, widths[level] / count_decimal) for level in INTERVAL_LEVELS),
        residuals.mean,
        residuals.median,
        boundary,
        mean_model < mean_raw,
        coverage_pass,
    )


def evaluate_validation_walk_forward(
    *,
    rows: tuple[EvaluationRow, ...],
    minimum_samples: int = 365,
) -> tuple[HorizonEvaluation, ...]:
    """Evaluate validation sequentially; each target uses only strictly earlier rows."""
    outputs: list[HorizonEvaluation] = []
    for target in sorted(rows, key=lambda row: (row.local_target_date, row.residual_id)):
        if not VALIDATION_START <= target.local_target_date <= VALIDATION_END:
            continue
        training = tuple(
            row
            for row in rows
            if row.lead_bucket is target.lead_bucket
            and row.exact_midpoint_seconds == target.exact_midpoint_seconds
            and row.local_target_date < target.local_target_date
        )
        validate_walk_forward_training(training, target)
        if len(training) < minimum_samples:
            continue
        residual_values = tuple(row.observed_deg_f - row.forecast_deg_f for row in training)
        distribution = EmpiricalResidualDistribution(residual_values)
        predictive = distribution.shifted(target.forecast_deg_f)
        model = predictive.crps(target.observed_deg_f)
        raw = abs(target.forecast_deg_f - target.observed_deg_f)
        coverage = tuple(
            (
                level,
                int(
                    predictive.interval(level)[0]
                    <= target.observed_deg_f
                    <= predictive.interval(level)[1]
                ),
            )
            for level in INTERVAL_LEVELS
        )
        widths = tuple(
            (level, predictive.interval(level)[1] - predictive.interval(level)[0])
            for level in INTERVAL_LEVELS
        )
        identity = stable_hash(
            (
                POLICY_VERSION,
                "VALIDATION_WALK_FORWARD",
                target.residual_id,
                tuple(row.residual_id for row in training),
                str(model),
                str(raw),
            )
        )
        outputs.append(
            HorizonEvaluation(
                identity,
                target.lead_bucket,
                target.exact_midpoint_seconds,
                (target.residual_id,),
                model,
                raw,
                raw,
                abs(target.forecast_deg_f + distribution.median - target.observed_deg_f),
                coverage,
                widths,
                distribution.mean,
                distribution.median,
                int(
                    predictive.predicate_count("GT", target.observed_deg_f) in {0, predictive.count}
                ),
                model < raw,
                True,
            )
        )
    return tuple(outputs)
