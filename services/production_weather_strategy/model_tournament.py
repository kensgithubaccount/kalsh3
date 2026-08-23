"""M28C leakage-safe weather feature joining and model tournament.

This module is deliberately pure. It accepts already-validated M28B settlement evidence,
pre-cutoff public market probabilities, and pre-cutoff-safe NOAA climate history. It does
not perform network I/O, access credentials, mutate production state, issue risk authority,
approve execution, promote a model, or send orders.

The tournament keeps one independent weather event as the effective unit of evaluation:
sibling binary contracts are averaged inside each event before Brier/log-loss aggregation.
Model selection uses train/validation only. The test partition is untouched until the final
scorecard is produced.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from enum import StrEnum

from services.historical_replay.archive import stable_hash

from .settlement_dataset import (
    AuthoritativeWeatherDataset,
    ResolvedTemperatureContract,
)

FEATURE_SCHEMA_VERSION = "m28c-weather-features-v1"
TOURNAMENT_VERSION = "m28c-weather-model-tournament-v1"
PREDICTION_CUTOFF_HOUR_UTC = 3
CLIMATE_LOOKBACK_YEARS = 10
CLIMATE_SEASONAL_WINDOW_DAYS = 15
MIN_CLIMATE_SAMPLES = 30
CITY_SHRINKAGE_PRIOR = Decimal("25")
EDGE_THRESHOLD = Decimal("0.10")
HYPOTHETICAL_FRICTION = Decimal("0.02")
PROBABILITY_EPSILON = Decimal("0.001")


class ModelTournamentError(ValueError):
    """M28C evidence violates a leakage, identity, or tournament invariant."""


class TournamentPartition(StrEnum):
    TRAIN = "TRAIN"
    VALIDATION = "VALIDATION"
    TEST = "TEST"


class TournamentModel(StrEnum):
    MARKET = "MARKET"
    NOAA_CLIMATOLOGY = "NOAA_CLIMATOLOGY"
    POOLED_RESIDUAL = "POOLED_RESIDUAL"
    CITY_SHRUNK_RESIDUAL = "CITY_SHRUNK_RESIDUAL"
    CALIBRATED_ENSEMBLE = "CALIBRATED_ENSEMBLE"


@dataclass(frozen=True, slots=True)
class ClimateObservation:
    station_id: str
    measurement: str
    local_date: date
    temperature_deg_f: Decimal
    source_evidence_id: str

    def __post_init__(self) -> None:
        if not self.station_id.strip() or not self.source_evidence_id.strip():
            raise ModelTournamentError("climate observation identity is incomplete")
        if self.measurement not in {"DAILY_MAX", "DAILY_MIN"}:
            raise ModelTournamentError("climate observation measurement is unsupported")
        if not self.temperature_deg_f.is_finite():
            raise ModelTournamentError("climate observation temperature is non-finite")


@dataclass(frozen=True, slots=True)
class ClimateHistory:
    station_id: str
    observations: tuple[ClimateObservation, ...]
    source_evidence_ids: tuple[str, ...]
    content_hash: str

    @classmethod
    def build(
        cls,
        station_id: str,
        observations: Sequence[ClimateObservation],
    ) -> ClimateHistory:
        if not station_id.strip():
            raise ModelTournamentError("climate history station is required")
        rows = tuple(
            sorted(
                observations,
                key=lambda row: (row.local_date, row.measurement, row.temperature_deg_f),
            )
        )
        if not rows or any(row.station_id != station_id for row in rows):
            raise ModelTournamentError("climate history station binding is invalid")
        identities = tuple(sorted({row.source_evidence_id for row in rows}))
        material = (
            FEATURE_SCHEMA_VERSION,
            station_id,
            tuple(
                (
                    row.measurement,
                    row.local_date.isoformat(),
                    str(row.temperature_deg_f),
                    row.source_evidence_id,
                )
                for row in rows
            ),
        )
        digest = stable_hash(material)
        return cls(station_id, rows, identities, digest)


@dataclass(frozen=True, slots=True)
class MarketCheckpoint:
    market_ticker: str
    checkpoint_at: datetime
    yes_probability: Decimal
    evidence_id: str

    def __post_init__(self) -> None:
        if not self.market_ticker.strip() or not self.evidence_id.strip():
            raise ModelTournamentError("market checkpoint identity is incomplete")
        if self.checkpoint_at.tzinfo is None:
            raise ModelTournamentError("market checkpoint must be timezone-aware")
        if not Decimal("0") <= self.yes_probability <= Decimal("1"):
            raise ModelTournamentError("market checkpoint probability is outside [0,1]")


@dataclass(frozen=True, slots=True)
class TournamentFeatureRow:
    row_id: str
    event_id: str
    contract_id: str
    market_ticker: str
    station_id: str
    measurement: str
    local_date: date
    realized_yes: int
    checkpoint_at: datetime
    market_probability: Decimal
    climate_probability: Decimal
    climate_sample_count: int
    market_evidence_id: str
    climate_evidence_id: str
    partition: TournamentPartition
    content_hash: str


@dataclass(frozen=True, slots=True)
class TournamentFeatureDataset:
    dataset_id: str
    settlement_dataset_id: str
    feature_schema_hash: str
    rows: tuple[TournamentFeatureRow, ...]
    train_event_ids: tuple[str, ...]
    validation_event_ids: tuple[str, ...]
    test_event_ids: tuple[str, ...]
    missing_market_contracts: int
    missing_climate_contracts: int
    content_hash: str

    @property
    def unique_event_count(self) -> int:
        return len({row.event_id for row in self.rows})


@dataclass(frozen=True, slots=True)
class ModelScorecard:
    model: TournamentModel
    partition: TournamentPartition
    contract_count: int
    unique_event_count: int
    event_weighted_brier: Decimal
    event_weighted_log_loss: Decimal
    calibration_gap: Decimal
    market_relative_skill: Decimal
    hypothetical_trade_count: int
    hypothetical_total_pnl: Decimal
    hypothetical_average_pnl: Decimal


@dataclass(frozen=True, slots=True)
class TournamentFit:
    pooled_alpha: Decimal
    pooled_bias: Decimal
    city_biases: tuple[tuple[str, Decimal], ...]
    calibration_slope: Decimal
    calibration_offset: Decimal
    ensemble_weight: Decimal
    validation_selected_model: TournamentModel
    content_hash: str


@dataclass(frozen=True, slots=True)
class ModelTournamentResult:
    tournament_id: str
    feature_dataset_id: str
    fit: TournamentFit
    scorecards: tuple[ModelScorecard, ...]
    selected_test_scorecard: ModelScorecard
    test_market_scorecard: ModelScorecard
    test_edge_classification: str
    promotion_authority: str
    content_hash: str


def build_feature_dataset(
    settlement_dataset: AuthoritativeWeatherDataset,
    *,
    market_checkpoints: Mapping[str, MarketCheckpoint],
    climate_histories: Mapping[str, ClimateHistory],
) -> TournamentFeatureDataset:
    """Join M28B labels to strictly pre-settlement market and NOAA climate evidence."""

    if settlement_dataset.event_count < 1 or settlement_dataset.contract_count < 1:
        raise ModelTournamentError("settlement dataset is empty")
    event_by_contract: dict[str, str] = {}
    event_date: dict[str, date] = {}
    for event in settlement_dataset.events:
        if event.event_id in event_date:
            raise ModelTournamentError("duplicate settlement event id")
        event_date[event.event_id] = event.local_date
        for contract_id in event.contract_ids:
            if contract_id in event_by_contract:
                raise ModelTournamentError("contract appears in multiple settlement events")
            event_by_contract[contract_id] = event.event_id

    partitions = _temporal_partitions(event_date)
    rows: list[TournamentFeatureRow] = []
    missing_market = 0
    missing_climate = 0
    for contract in settlement_dataset.contracts:
        event_id = event_by_contract.get(contract.contract_id)
        if event_id is None:
            raise ModelTournamentError("settlement contract is not bound to an event")
        checkpoint = market_checkpoints.get(contract.market_ticker)
        if checkpoint is None:
            missing_market += 1
            continue
        _validate_checkpoint(contract, checkpoint)
        history = climate_histories.get(contract.station_id)
        if history is None:
            missing_climate += 1
            continue
        climate = _climate_probability(contract, history)
        if climate is None:
            missing_climate += 1
            continue
        climate_probability, sample_count, climate_evidence_id = climate
        partition = partitions[event_id]
        material = (
            FEATURE_SCHEMA_VERSION,
            settlement_dataset.dataset_id,
            event_id,
            contract.contract_id,
            checkpoint.evidence_id,
            climate_evidence_id,
            str(checkpoint.yes_probability),
            str(climate_probability),
            sample_count,
            partition.value,
        )
        digest = stable_hash(material)
        rows.append(
            TournamentFeatureRow(
                row_id=digest,
                event_id=event_id,
                contract_id=contract.contract_id,
                market_ticker=contract.market_ticker,
                station_id=contract.station_id,
                measurement=contract.measurement,
                local_date=contract.local_date,
                realized_yes=contract.realized_yes,
                checkpoint_at=checkpoint.checkpoint_at.astimezone(UTC),
                market_probability=checkpoint.yes_probability,
                climate_probability=climate_probability,
                climate_sample_count=sample_count,
                market_evidence_id=checkpoint.evidence_id,
                climate_evidence_id=climate_evidence_id,
                partition=partition,
                content_hash=digest,
            )
        )

    ordered = tuple(sorted(rows, key=lambda row: (row.local_date, row.market_ticker)))
    if not ordered:
        raise ModelTournamentError("no complete M28C feature rows were produced")
    represented = {row.partition for row in ordered}
    if represented != set(TournamentPartition):
        raise ModelTournamentError("feature coverage must reach train, validation, and test")
    feature_schema_hash = stable_hash(
        (
            FEATURE_SCHEMA_VERSION,
            PREDICTION_CUTOFF_HOUR_UTC,
            CLIMATE_LOOKBACK_YEARS,
            CLIMATE_SEASONAL_WINDOW_DAYS,
            MIN_CLIMATE_SAMPLES,
        )
    )
    train_ids = tuple(
        sorted({row.event_id for row in ordered if row.partition is TournamentPartition.TRAIN})
    )
    validation_ids = tuple(
        sorted({row.event_id for row in ordered if row.partition is TournamentPartition.VALIDATION})
    )
    test_ids = tuple(
        sorted({row.event_id for row in ordered if row.partition is TournamentPartition.TEST})
    )
    material = (
        FEATURE_SCHEMA_VERSION,
        settlement_dataset.dataset_id,
        feature_schema_hash,
        tuple(row.content_hash for row in ordered),
        train_ids,
        validation_ids,
        test_ids,
        missing_market,
        missing_climate,
    )
    digest = stable_hash(material)
    return TournamentFeatureDataset(
        dataset_id=digest,
        settlement_dataset_id=settlement_dataset.dataset_id,
        feature_schema_hash=feature_schema_hash,
        rows=ordered,
        train_event_ids=train_ids,
        validation_event_ids=validation_ids,
        test_event_ids=test_ids,
        missing_market_contracts=missing_market,
        missing_climate_contracts=missing_climate,
        content_hash=digest,
    )


def run_model_tournament(dataset: TournamentFeatureDataset) -> ModelTournamentResult:
    """Fit challengers on train/validation and reveal the untouched test only afterward."""

    train = _partition_rows(dataset, TournamentPartition.TRAIN)
    validation = _partition_rows(dataset, TournamentPartition.VALIDATION)
    test = _partition_rows(dataset, TournamentPartition.TEST)
    if len({row.event_id for row in train}) < 2:
        raise ModelTournamentError("training partition needs at least two independent events")
    if not validation or not test:
        raise ModelTournamentError("validation and test partitions cannot be empty")

    alpha, bias = _fit_pooled_residual(train)
    city_biases = _fit_city_biases(train, alpha, bias)
    slope, offset = _fit_calibration(train, alpha, bias, city_biases)
    ensemble_weight = _fit_ensemble_weight(
        validation,
        alpha,
        bias,
        city_biases,
        slope,
        offset,
    )
    selected_model = TournamentModel.CALIBRATED_ENSEMBLE
    fit_material = (
        TOURNAMENT_VERSION,
        dataset.dataset_id,
        str(alpha),
        str(bias),
        tuple((station, str(value)) for station, value in city_biases),
        str(slope),
        str(offset),
        str(ensemble_weight),
        selected_model.value,
    )
    fit_hash = stable_hash(fit_material)
    fit = TournamentFit(
        pooled_alpha=alpha,
        pooled_bias=bias,
        city_biases=city_biases,
        calibration_slope=slope,
        calibration_offset=offset,
        ensemble_weight=ensemble_weight,
        validation_selected_model=selected_model,
        content_hash=fit_hash,
    )

    scorecards: list[ModelScorecard] = []
    for partition, rows in (
        (TournamentPartition.TRAIN, train),
        (TournamentPartition.VALIDATION, validation),
        (TournamentPartition.TEST, test),
    ):
        market_score = _score(
            rows,
            TournamentModel.MARKET,
            partition,
            predictions={row.row_id: row.market_probability for row in rows},
            market_brier=None,
        )
        scorecards.append(market_score)
        market_brier = market_score.event_weighted_brier
        for model in (
            TournamentModel.NOAA_CLIMATOLOGY,
            TournamentModel.POOLED_RESIDUAL,
            TournamentModel.CITY_SHRUNK_RESIDUAL,
            TournamentModel.CALIBRATED_ENSEMBLE,
        ):
            predictions = _predictions(
                rows,
                model,
                alpha,
                bias,
                city_biases,
                slope,
                offset,
                ensemble_weight,
            )
            scorecards.append(
                _score(
                    rows,
                    model,
                    partition,
                    predictions=predictions,
                    market_brier=market_brier,
                )
            )

    ordered_scores = tuple(
        sorted(scorecards, key=lambda score: (score.partition.value, score.model.value))
    )
    selected_test = next(
        score
        for score in ordered_scores
        if score.partition is TournamentPartition.TEST and score.model is selected_model
    )
    market_test = next(
        score
        for score in ordered_scores
        if score.partition is TournamentPartition.TEST and score.model is TournamentModel.MARKET
    )
    edge = (
        "BEATS_MARKET_ON_UNTOUCHED_TEST"
        if selected_test.market_relative_skill > 0
        else "NO_TEST_EDGE"
    )
    material = (
        TOURNAMENT_VERSION,
        dataset.dataset_id,
        fit.content_hash,
        tuple(_score_material(score) for score in ordered_scores),
        selected_test.model.value,
        edge,
        "NONE",
    )
    digest = stable_hash(material)
    return ModelTournamentResult(
        tournament_id=digest,
        feature_dataset_id=dataset.dataset_id,
        fit=fit,
        scorecards=ordered_scores,
        selected_test_scorecard=selected_test,
        test_market_scorecard=market_test,
        test_edge_classification=edge,
        promotion_authority="NONE",
        content_hash=digest,
    )


def _temporal_partitions(event_dates: Mapping[str, date]) -> dict[str, TournamentPartition]:
    unique_dates = sorted(set(event_dates.values()))
    if len(unique_dates) < 5:
        raise ModelTournamentError("M28C requires at least five distinct settlement dates")
    train_end = max(1, len(unique_dates) * 3 // 5)
    validation_end = max(train_end + 1, len(unique_dates) * 4 // 5)
    validation_end = min(validation_end, len(unique_dates) - 1)
    train_dates = set(unique_dates[:train_end])
    validation_dates = set(unique_dates[train_end:validation_end])
    test_dates = set(unique_dates[validation_end:])
    if not train_dates or not validation_dates or not test_dates:
        raise ModelTournamentError("temporal split could not create three non-empty windows")
    result: dict[str, TournamentPartition] = {}
    for event_id, local_date in event_dates.items():
        if local_date in train_dates:
            result[event_id] = TournamentPartition.TRAIN
        elif local_date in validation_dates:
            result[event_id] = TournamentPartition.VALIDATION
        elif local_date in test_dates:
            result[event_id] = TournamentPartition.TEST
        else:
            raise ModelTournamentError("event date was not assigned to a temporal partition")
    return result


def _validate_checkpoint(
    contract: ResolvedTemperatureContract,
    checkpoint: MarketCheckpoint,
) -> None:
    if checkpoint.market_ticker != contract.market_ticker:
        raise ModelTournamentError("market checkpoint ticker binding is invalid")
    expected = datetime.combine(
        contract.local_date,
        time(PREDICTION_CUTOFF_HOUR_UTC, tzinfo=UTC),
    )
    actual = checkpoint.checkpoint_at.astimezone(UTC)
    if actual != expected:
        raise ModelTournamentError("market checkpoint is not the exact reviewed 03Z cutoff")
    if actual >= contract.settlement_at.astimezone(UTC):
        raise ModelTournamentError("market checkpoint is not pre-settlement")


def _climate_probability(
    contract: ResolvedTemperatureContract,
    history: ClimateHistory,
) -> tuple[Decimal, int, str] | None:
    if history.station_id != contract.station_id:
        raise ModelTournamentError("climate history does not bind the contract station")
    start_year = contract.local_date.year - CLIMATE_LOOKBACK_YEARS
    values: list[ClimateObservation] = []
    for observation in history.observations:
        if observation.measurement != contract.measurement:
            continue
        if not start_year <= observation.local_date.year < contract.local_date.year:
            continue
        if (
            _seasonal_distance(observation.local_date, contract.local_date)
            > CLIMATE_SEASONAL_WINDOW_DAYS
        ):
            continue
        values.append(observation)
    if len(values) < MIN_CLIMATE_SAMPLES:
        return None
    yes_count = sum(contract.predicate(row.temperature_deg_f) for row in values)
    probability = (Decimal(yes_count) + Decimal(1)) / (Decimal(len(values)) + Decimal(2))
    material = (
        FEATURE_SCHEMA_VERSION,
        history.content_hash,
        contract.contract_id,
        tuple(
            (row.local_date.isoformat(), str(row.temperature_deg_f), row.source_evidence_id)
            for row in values
        ),
    )
    return probability, len(values), stable_hash(material)


def _seasonal_distance(observed: date, target: date) -> int:
    observed_anchor = date(2000, observed.month, observed.day)
    target_anchor = date(2000, target.month, target.day)
    direct = abs((observed_anchor - target_anchor).days)
    return min(direct, 366 - direct)


def _partition_rows(
    dataset: TournamentFeatureDataset,
    partition: TournamentPartition,
) -> tuple[TournamentFeatureRow, ...]:
    return tuple(row for row in dataset.rows if row.partition is partition)


def _fit_pooled_residual(rows: Sequence[TournamentFeatureRow]) -> tuple[Decimal, Decimal]:
    best: tuple[Decimal, Decimal, Decimal] | None = None
    for alpha_step in range(31):
        alpha = Decimal(alpha_step) / Decimal(20)
        for bias_step in range(-10, 11):
            bias = Decimal(bias_step) / Decimal(100)
            predictions = {
                row.row_id: _clip(
                    row.market_probability
                    + alpha * (row.climate_probability - row.market_probability)
                    + bias
                )
                for row in rows
            }
            brier = _event_weighted_brier(rows, predictions)
            candidate = (brier, alpha, bias)
            if best is None or candidate < best:
                best = candidate
    if best is None:
        raise ModelTournamentError("pooled residual fit produced no candidate")
    return best[1], best[2]


def _fit_city_biases(
    rows: Sequence[TournamentFeatureRow],
    alpha: Decimal,
    bias: Decimal,
) -> tuple[tuple[str, Decimal], ...]:
    grouped: dict[str, list[Decimal]] = defaultdict(list)
    for row in rows:
        pooled = _pooled_prediction(row, alpha, bias)
        grouped[row.station_id].append(Decimal(row.realized_yes) - pooled)
    values: list[tuple[str, Decimal]] = []
    for station, residuals in sorted(grouped.items()):
        count = Decimal(len(residuals))
        mean = sum(residuals, Decimal("0")) / count
        shrink = count / (count + CITY_SHRINKAGE_PRIOR)
        values.append((station, _bounded_bias(mean * shrink)))
    return tuple(values)


def _fit_calibration(
    rows: Sequence[TournamentFeatureRow],
    alpha: Decimal,
    bias: Decimal,
    city_biases: tuple[tuple[str, Decimal], ...],
) -> tuple[Decimal, Decimal]:
    city_map = dict(city_biases)
    best: tuple[Decimal, Decimal, Decimal] | None = None
    for slope_step in range(16, 25):
        slope = Decimal(slope_step) / Decimal(20)
        for offset_step in range(-5, 6):
            offset = Decimal(offset_step) / Decimal(100)
            predictions = {
                row.row_id: _clip(
                    Decimal("0.5")
                    + slope * (_city_prediction(row, alpha, bias, city_map) - Decimal("0.5"))
                    + offset
                )
                for row in rows
            }
            brier = _event_weighted_brier(rows, predictions)
            candidate = (brier, slope, offset)
            if best is None or candidate < best:
                best = candidate
    if best is None:
        raise ModelTournamentError("calibration fit produced no candidate")
    return best[1], best[2]


def _fit_ensemble_weight(
    rows: Sequence[TournamentFeatureRow],
    alpha: Decimal,
    bias: Decimal,
    city_biases: tuple[tuple[str, Decimal], ...],
    slope: Decimal,
    offset: Decimal,
) -> Decimal:
    city_map = dict(city_biases)
    best: tuple[Decimal, Decimal] | None = None
    for step in range(21):
        weight = Decimal(step) / Decimal(20)
        predictions = {
            row.row_id: _clip(
                weight
                * _calibrated_city_prediction(
                    row,
                    alpha,
                    bias,
                    city_map,
                    slope,
                    offset,
                )
                + (Decimal("1") - weight) * row.market_probability
            )
            for row in rows
        }
        brier = _event_weighted_brier(rows, predictions)
        candidate = (brier, weight)
        if best is None or candidate < best:
            best = candidate
    if best is None:
        raise ModelTournamentError("ensemble fit produced no candidate")
    return best[1]


def _predictions(
    rows: Sequence[TournamentFeatureRow],
    model: TournamentModel,
    alpha: Decimal,
    bias: Decimal,
    city_biases: tuple[tuple[str, Decimal], ...],
    slope: Decimal,
    offset: Decimal,
    ensemble_weight: Decimal,
) -> dict[str, Decimal]:
    city_map = dict(city_biases)
    result: dict[str, Decimal] = {}
    for row in rows:
        if model is TournamentModel.NOAA_CLIMATOLOGY:
            value = row.climate_probability
        elif model is TournamentModel.POOLED_RESIDUAL:
            value = _pooled_prediction(row, alpha, bias)
        elif model is TournamentModel.CITY_SHRUNK_RESIDUAL:
            value = _city_prediction(row, alpha, bias, city_map)
        elif model is TournamentModel.CALIBRATED_ENSEMBLE:
            calibrated = _calibrated_city_prediction(
                row,
                alpha,
                bias,
                city_map,
                slope,
                offset,
            )
            value = _clip(
                ensemble_weight * calibrated
                + (Decimal("1") - ensemble_weight) * row.market_probability
            )
        else:
            raise ModelTournamentError("unsupported challenger model")
        result[row.row_id] = value
    return result


def _pooled_prediction(
    row: TournamentFeatureRow,
    alpha: Decimal,
    bias: Decimal,
) -> Decimal:
    return _clip(
        row.market_probability + alpha * (row.climate_probability - row.market_probability) + bias
    )


def _city_prediction(
    row: TournamentFeatureRow,
    alpha: Decimal,
    bias: Decimal,
    city_biases: Mapping[str, Decimal],
) -> Decimal:
    return _clip(
        _pooled_prediction(row, alpha, bias) + city_biases.get(row.station_id, Decimal("0"))
    )


def _calibrated_city_prediction(
    row: TournamentFeatureRow,
    alpha: Decimal,
    bias: Decimal,
    city_biases: Mapping[str, Decimal],
    slope: Decimal,
    offset: Decimal,
) -> Decimal:
    city = _city_prediction(row, alpha, bias, city_biases)
    return _clip(Decimal("0.5") + slope * (city - Decimal("0.5")) + offset)


def _score(
    rows: Sequence[TournamentFeatureRow],
    model: TournamentModel,
    partition: TournamentPartition,
    *,
    predictions: Mapping[str, Decimal],
    market_brier: Decimal | None,
) -> ModelScorecard:
    if not rows:
        raise ModelTournamentError("cannot score an empty partition")
    brier = _event_weighted_brier(rows, predictions)
    log_loss = _event_weighted_log_loss(rows, predictions)
    predicted_mean = sum((predictions[row.row_id] for row in rows), Decimal("0")) / Decimal(
        len(rows)
    )
    outcome_mean = sum((Decimal(row.realized_yes) for row in rows), Decimal("0")) / Decimal(
        len(rows)
    )
    calibration_gap = abs(predicted_mean - outcome_mean)
    if market_brier is None or market_brier == 0:
        skill = Decimal("0")
    else:
        skill = (market_brier - brier) / market_brier
    trades, total_pnl = _hypothetical_pnl(rows, predictions)
    average_pnl = Decimal("0") if trades == 0 else total_pnl / Decimal(trades)
    return ModelScorecard(
        model=model,
        partition=partition,
        contract_count=len(rows),
        unique_event_count=len({row.event_id for row in rows}),
        event_weighted_brier=brier,
        event_weighted_log_loss=log_loss,
        calibration_gap=calibration_gap,
        market_relative_skill=skill,
        hypothetical_trade_count=trades,
        hypothetical_total_pnl=total_pnl,
        hypothetical_average_pnl=average_pnl,
    )


def _event_weighted_brier(
    rows: Sequence[TournamentFeatureRow],
    predictions: Mapping[str, Decimal],
) -> Decimal:
    grouped: dict[str, list[Decimal]] = defaultdict(list)
    for row in rows:
        probability = predictions[row.row_id]
        loss = (probability - Decimal(row.realized_yes)) ** 2
        grouped[row.event_id].append(loss)
    event_losses = [sum(values, Decimal("0")) / Decimal(len(values)) for values in grouped.values()]
    if not event_losses:
        raise ModelTournamentError("Brier score has no independent events")
    return sum(event_losses, Decimal("0")) / Decimal(len(event_losses))


def _event_weighted_log_loss(
    rows: Sequence[TournamentFeatureRow],
    predictions: Mapping[str, Decimal],
) -> Decimal:
    grouped: dict[str, list[Decimal]] = defaultdict(list)
    for row in rows:
        probability = _clip_for_log(predictions[row.row_id])
        p = float(probability)
        loss = -math.log(p if row.realized_yes else 1.0 - p)
        grouped[row.event_id].append(Decimal(str(loss)))
    event_losses = [sum(values, Decimal("0")) / Decimal(len(values)) for values in grouped.values()]
    if not event_losses:
        raise ModelTournamentError("log loss has no independent events")
    return sum(event_losses, Decimal("0")) / Decimal(len(event_losses))


def _hypothetical_pnl(
    rows: Sequence[TournamentFeatureRow],
    predictions: Mapping[str, Decimal],
) -> tuple[int, Decimal]:
    trades = 0
    total = Decimal("0")
    for row in rows:
        model_probability = predictions[row.row_id]
        market_probability = row.market_probability
        delta = model_probability - market_probability
        if delta >= EDGE_THRESHOLD:
            entry = min(Decimal("1"), market_probability + HYPOTHETICAL_FRICTION)
            total += Decimal(row.realized_yes) - entry
            trades += 1
        elif delta <= -EDGE_THRESHOLD:
            no_market = Decimal("1") - market_probability
            entry = min(Decimal("1"), no_market + HYPOTHETICAL_FRICTION)
            total += Decimal(1 - row.realized_yes) - entry
            trades += 1
    return trades, total


def _bounded_bias(value: Decimal) -> Decimal:
    return max(Decimal("-0.15"), min(Decimal("0.15"), value))


def _clip(value: Decimal) -> Decimal:
    return max(Decimal("0"), min(Decimal("1"), value))


def _clip_for_log(value: Decimal) -> Decimal:
    return max(PROBABILITY_EPSILON, min(Decimal("1") - PROBABILITY_EPSILON, value))


def _score_material(score: ModelScorecard) -> tuple[object, ...]:
    return (
        score.model.value,
        score.partition.value,
        score.contract_count,
        score.unique_event_count,
        str(score.event_weighted_brier),
        str(score.event_weighted_log_loss),
        str(score.calibration_gap),
        str(score.market_relative_skill),
        score.hypothetical_trade_count,
        str(score.hypothetical_total_pnl),
        str(score.hypothetical_average_pnl),
    )
