"""Leakage-safe M28C/C.1 weather model tournament on canonical M28B/NOAA evidence.

The module is pure/offline. Canonical settlement labels must come from evidence-bound M28B
``WeatherSettlementDataset`` instances, and every climate feature must already be canonical
``HISTORICAL_POINT_IN_TIME`` evidence. The module never acquires network data, accesses
credentials/account state, mutates production state, promotes a model, or sends orders.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from types import MappingProxyType
from urllib.parse import quote

from services.forecasting.weather_source_authority import PHYSICAL_WEATHER_SOURCES
from services.historical_replay.archive import stable_hash
from services.production_weather_strategy.climate_evidence import (
    CLIMATE_LOOKBACK_YEARS,
    CLIMATE_SEASONAL_WINDOW_DAYS,
    ClimateEvidenceClassification,
    ClimateFeatureEvidence,
    seasonal_distance_days,
)
from services.production_weather_strategy.contracts import (
    ModelArtifact,
    ModelState,
    SettlementLabel,
    SettlementLabelManifest,
    TemporalSplit,
    TrainingDatasetManifest,
)
from services.production_weather_strategy.settlement_dataset import (
    SETTLEMENT_MAPPING_ID,
    EventPartition,
    ResolvedTemperatureContract,
    ResolvedWeatherEvent,
    WeatherSettlementDataset,
)

FEATURE_SCHEMA_VERSION = "m28c-weather-features-v2"
HISTORICAL_MARKET_RESPONSE_SCHEMA_VERSION = "m28c-historical-market-response-v1"
MARKET_CHECKPOINT_SCHEMA_VERSION = "m28c-market-checkpoint-v3"
TOURNAMENT_VERSION = "m28c-weather-model-tournament-v2"
FAMILY = "DAILY_TEMPERATURE"
PREDICTION_CUTOFF_HOUR_UTC = 3
MARKET_CANDLE_INTERVAL_MINUTES = 60
MARKET_CANDLE_LOOKBACK = timedelta(hours=24)
MIN_CLIMATE_SAMPLES = 30
CITY_SHRINKAGE_PRIOR = Decimal("25")
EDGE_THRESHOLD = Decimal("0.10")
HYPOTHETICAL_FRICTION = Decimal("0.02")
PROBABILITY_EPSILON = Decimal("0.001")
HYPOTHETICAL_PNL_CLASSIFICATION = "RESEARCH_ONLY_HYPOTHETICAL_PNL"
_HISTORICAL_MARKET_RESPONSE_CAPABILITY = object()
_MARKET_CHECKPOINT_CAPABILITY = object()
PREDICTION_CUTOFF_RULE = (
    "03:00:00Z on target local-date label; latest 60-minute historical Kalshi candle "
    "ending at-or-before cutoff; canonical NOAA climate evidence is strict historical "
    "point-in-time and uses prior calendar years only with a +/-15-day seasonal window"
)


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


@dataclass(frozen=True, slots=True, init=False)
class HistoricalMarketResponseEvidence:
    """Capability-gated identity of one exact reviewed historical candle response."""

    schema_version: str
    request_path: str
    market_ticker: str
    request_start_ts: int
    request_end_ts: int
    interval_minutes: int
    response_sha256: str
    candle_hashes: tuple[str, ...]
    evidence_id: str
    content_hash: str

    def __init__(
        self,
        *,
        request_path: str,
        market_ticker: str,
        request_start_ts: int,
        request_end_ts: int,
        interval_minutes: int,
        response_sha256: str,
        candle_hashes: Sequence[str],
        _capability: object | None = None,
    ) -> None:
        if _capability is not _HISTORICAL_MARKET_RESPONSE_CAPABILITY:
            raise ModelTournamentError(
                "historical market response evidence requires internal acquisition capability"
            )
        ticker = market_ticker.strip()
        if not ticker:
            raise ModelTournamentError("historical market response ticker is required")
        if request_end_ts <= request_start_ts:
            raise ModelTournamentError("historical market response bounds are invalid")
        if interval_minutes != MARKET_CANDLE_INTERVAL_MINUTES:
            raise ModelTournamentError("historical market response interval is not canonical")
        expected_path = _candle_request_path(
            ticker,
            start_ts=request_start_ts,
            end_ts=request_end_ts,
        )
        if request_path != expected_path:
            raise ModelTournamentError("historical market response request path is not canonical")
        response_hash = response_sha256.strip().lower()
        if len(response_hash) != 64 or any(
            character not in "0123456789abcdef" for character in response_hash
        ):
            raise ModelTournamentError("historical market response hash is malformed")
        exact_candle_hashes = tuple(candle_hashes)
        if any(not value.strip() for value in exact_candle_hashes):
            raise ModelTournamentError("historical market response candle identity is incomplete")
        if len(set(exact_candle_hashes)) != len(exact_candle_hashes):
            raise ModelTournamentError("historical market response candle identity is duplicated")
        digest = stable_hash(
            (
                HISTORICAL_MARKET_RESPONSE_SCHEMA_VERSION,
                request_path,
                ticker,
                request_start_ts,
                request_end_ts,
                interval_minutes,
                response_hash,
                exact_candle_hashes,
            )
        )
        object.__setattr__(self, "schema_version", HISTORICAL_MARKET_RESPONSE_SCHEMA_VERSION)
        object.__setattr__(self, "request_path", request_path)
        object.__setattr__(self, "market_ticker", ticker)
        object.__setattr__(self, "request_start_ts", request_start_ts)
        object.__setattr__(self, "request_end_ts", request_end_ts)
        object.__setattr__(self, "interval_minutes", interval_minutes)
        object.__setattr__(self, "response_sha256", response_hash)
        object.__setattr__(self, "candle_hashes", exact_candle_hashes)
        object.__setattr__(self, "evidence_id", digest)
        object.__setattr__(self, "content_hash", digest)


@dataclass(frozen=True, slots=True, init=False)
class MarketCheckpoint:
    """Exact internally-derived 03Z market evidence from bounded historical candles."""

    market_ticker: str
    checkpoint_at: datetime
    request_start_ts: int
    request_end_ts: int
    request_path: str
    response_evidence_id: str
    selected_candle_end_ts: int
    selected_candle_hash: str
    yes_probability: Decimal
    checkpoint_id: str
    content_hash: str

    def __init__(
        self,
        *,
        _capability: object | None = None,
        _values: Mapping[str, object] | None = None,
    ) -> None:
        if _capability is not _MARKET_CHECKPOINT_CAPABILITY or _values is None:
            raise ModelTournamentError("market checkpoint must be derived from candle evidence")
        for name in (
            "market_ticker",
            "checkpoint_at",
            "request_start_ts",
            "request_end_ts",
            "request_path",
            "response_evidence_id",
            "selected_candle_end_ts",
            "selected_candle_hash",
            "yes_probability",
            "checkpoint_id",
            "content_hash",
        ):
            object.__setattr__(self, name, _values[name])

    @classmethod
    def from_candles(
        cls,
        *,
        market_ticker: str,
        checkpoint_at: datetime,
        candles: Sequence[Mapping[str, object]],
        response_evidence: HistoricalMarketResponseEvidence | None = None,
        response_evidence_id: str | None = None,
    ) -> MarketCheckpoint | None:
        if response_evidence_id is not None:
            raise ModelTournamentError(
                "plain response evidence ids cannot mint strict market checkpoints"
            )
        if not isinstance(response_evidence, HistoricalMarketResponseEvidence):
            raise ModelTournamentError(
                "strict market checkpoint requires bound HistoricalMarketResponseEvidence"
            )
        ticker = market_ticker.strip()
        if not ticker:
            raise ModelTournamentError("market checkpoint ticker is required")
        cutoff = _reviewed_cutoff(checkpoint_at)
        end_ts = int(cutoff.timestamp())
        start_ts = int((cutoff - MARKET_CANDLE_LOOKBACK).timestamp())
        path = _candle_request_path(ticker, start_ts=start_ts, end_ts=end_ts)
        if response_evidence.market_ticker != ticker:
            raise ModelTournamentError("historical market response ticker binding is invalid")
        if (
            response_evidence.request_start_ts != start_ts
            or response_evidence.request_end_ts != end_ts
        ):
            raise ModelTournamentError("historical market response range binding is invalid")
        if response_evidence.interval_minutes != MARKET_CANDLE_INTERVAL_MINUTES:
            raise ModelTournamentError("historical market response interval binding is invalid")
        if response_evidence.request_path != path:
            raise ModelTournamentError("historical market response path binding is invalid")

        exact_candles = tuple(candles)
        candle_hashes = tuple(stable_hash(candle) for candle in exact_candles)
        if candle_hashes != response_evidence.candle_hashes:
            raise ModelTournamentError(
                "historical candles do not exactly match bound response evidence"
            )

        seen_periods: set[int] = set()
        for candle in exact_candles:
            period = candle.get("end_period_ts")
            if isinstance(period, bool) or not isinstance(period, int):
                raise ModelTournamentError("historical candlestick period is malformed")
            if period in seen_periods:
                raise ModelTournamentError("historical candlestick period is duplicated")
            seen_periods.add(period)
            if period > end_ts:
                raise ModelTournamentError("future candle contaminated market checkpoint")
            if period < start_ts:
                raise ModelTournamentError("historical candle is outside bound request range")
        if not exact_candles:
            return None
        selected = max(
            exact_candles,
            key=lambda candle: int(candle["end_period_ts"]),  # type: ignore[call-overload]
        )
        probability = _market_probability(selected)
        if probability is None:
            return None
        selected_end = int(selected["end_period_ts"])  # type: ignore[call-overload]
        selected_hash = stable_hash(selected)
        if selected_hash not in response_evidence.candle_hashes:
            raise ModelTournamentError("selected candle is absent from bound response evidence")
        digest = stable_hash(
            (
                MARKET_CHECKPOINT_SCHEMA_VERSION,
                ticker,
                cutoff.isoformat(),
                start_ts,
                end_ts,
                path,
                response_evidence.evidence_id,
                selected_end,
                selected_hash,
                str(probability),
            )
        )
        return cls(
            _capability=_MARKET_CHECKPOINT_CAPABILITY,
            _values=MappingProxyType(
                {
                    "market_ticker": ticker,
                    "checkpoint_at": cutoff,
                    "request_start_ts": start_ts,
                    "request_end_ts": end_ts,
                    "request_path": path,
                    "response_evidence_id": response_evidence.evidence_id,
                    "selected_candle_end_ts": selected_end,
                    "selected_candle_hash": selected_hash,
                    "yes_probability": probability,
                    "checkpoint_id": digest,
                    "content_hash": digest,
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class TournamentFeatureRow:
    row_id: str
    event_id: str
    contract_id: str
    market_ticker: str
    settlement_label_id: str
    station_id: str
    measurement: str
    local_date: date
    realized_yes: int
    checkpoint_at: datetime
    market_probability: Decimal
    climate_probability: Decimal
    climate_sample_count: int
    market_checkpoint_id: str
    climate_feature_evidence_id: str
    partition: TournamentPartition
    content_hash: str


@dataclass(frozen=True, slots=True)
class TournamentFeatureDataset:
    dataset_id: str
    settlement_bundle_id: str
    settlement_dataset_ids: tuple[str, ...]
    source_settlement_label_manifest_ids: tuple[str, ...]
    settlement_mapping_id: str
    settlement_labels: SettlementLabelManifest
    temporal_split_hash: str
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
    hypothetical_pnl_classification: str = field(default=HYPOTHETICAL_PNL_CLASSIFICATION)


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


def derive_temporal_split(events: Sequence[ResolvedWeatherEvent]) -> TemporalSplit:
    """Recover the reviewed roughly 60/20/20 local-date split as one canonical authority."""

    if not events:
        raise ModelTournamentError("cannot derive a temporal split from empty events")
    event_ids = [event.event_id for event in events]
    if len(set(event_ids)) != len(event_ids):
        raise ModelTournamentError("duplicate event identity while deriving temporal split")
    unique_dates = sorted({event.local_date for event in events})
    if len(unique_dates) < 5:
        raise ModelTournamentError("M28C requires at least five distinct settlement dates")
    train_end_index = max(1, len(unique_dates) * 3 // 5)
    validation_end_index = max(train_end_index + 1, len(unique_dates) * 4 // 5)
    validation_end_index = min(validation_end_index, len(unique_dates) - 1)
    train_dates = unique_dates[:train_end_index]
    validation_dates = unique_dates[train_end_index:validation_end_index]
    test_dates = unique_dates[validation_end_index:]
    if not train_dates or not validation_dates or not test_dates:
        raise ModelTournamentError("temporal split could not create three non-empty windows")
    train_start = datetime.combine(train_dates[0], time.min, tzinfo=UTC)
    validation_start = datetime.combine(validation_dates[0], time.min, tzinfo=UTC)
    test_start = datetime.combine(test_dates[0], time.min, tzinfo=UTC)
    test_end = datetime.combine(test_dates[-1] + timedelta(days=1), time.min, tzinfo=UTC)
    return TemporalSplit(
        train_start=train_start,
        train_end=validation_start,
        validation_start=validation_start,
        validation_end=test_start,
        test_start=test_start,
        test_end=test_end,
    )


def build_feature_dataset(
    settlement_datasets: Sequence[WeatherSettlementDataset],
    *,
    temporal_split: TemporalSplit,
    market_checkpoints: Mapping[str, MarketCheckpoint],
    climate_features: Mapping[str, ClimateFeatureEvidence],
) -> TournamentFeatureDataset:
    """Join exact M28B labels to strict pre-cutoff market and NOAA evidence."""

    datasets = tuple(settlement_datasets)
    if not datasets:
        raise ModelTournamentError("settlement dataset collection is empty")
    expected_split_hash = temporal_split.content_hash
    source_manifest_ids: list[str] = []
    source_dataset_ids: list[str] = []
    contracts: dict[str, ResolvedTemperatureContract] = {}
    event_partitions: dict[str, TournamentPartition] = {}
    labels_by_ticker: dict[str, SettlementLabel] = {}

    for dataset in datasets:
        if not isinstance(dataset, WeatherSettlementDataset):
            raise ModelTournamentError("canonical tournament requires WeatherSettlementDataset")
        if not dataset.evidence_bound or dataset.settlement_labels is None:
            raise ModelTournamentError("canonical tournament requires evidence-bound M28B labels")
        if dataset.settlement_mapping_id != SETTLEMENT_MAPPING_ID:
            raise ModelTournamentError("settlement mapping identity is not canonical M28B")
        if dataset.settlement_labels.settlement_mapping_id != dataset.settlement_mapping_id:
            raise ModelTournamentError("settlement label manifest mapping disagrees with dataset")
        if dataset.temporal_split_hash != expected_split_hash:
            raise ModelTournamentError("M28B dataset does not carry the required temporal split")
        _validate_m28b_partition_integrity(dataset)
        source_manifest_ids.append(dataset.settlement_labels.manifest_id)
        source_dataset_ids.append(dataset.dataset_id)

        label_map = {label.market_ticker: label for label in dataset.settlement_labels.labels}
        if len(label_map) != len(dataset.settlement_labels.labels):
            raise ModelTournamentError("settlement label ticker identity is duplicated")
        event_ids = {event.event_id for event in dataset.events}
        for contract in dataset.contracts:
            if contract.market_ticker in contracts:
                raise ModelTournamentError("market ticker appears in multiple M28B datasets")
            if contract.event_id not in event_ids:
                raise ModelTournamentError("M28B contract is not bound to a canonical event")
            label = label_map.get(contract.market_ticker)
            if label is None:
                raise ModelTournamentError("M28B contract lacks its canonical settlement label")
            if label.event_id != contract.event_id:
                raise ModelTournamentError("canonical settlement label event identity disagrees")
            if label.resolved_outcome != bool(contract.realized_yes):
                raise ModelTournamentError("canonical settlement label outcome disagrees")
            contracts[contract.market_ticker] = contract
            labels_by_ticker[contract.market_ticker] = label

        for event in dataset.events:
            event_partition = _partition_from_m28b(dataset.split_for_event(event.event_id))
            previous = event_partitions.setdefault(event.event_id, event_partition)
            if previous is not event_partition:
                raise ModelTournamentError("one event appears in contradictory temporal partitions")

    if len(set(source_dataset_ids)) != len(source_dataset_ids):
        raise ModelTournamentError("duplicate M28B settlement dataset identity")
    if len(set(source_manifest_ids)) != len(source_manifest_ids):
        raise ModelTournamentError("duplicate M28B settlement label manifest identity")

    rows: list[TournamentFeatureRow] = []
    used_labels: list[SettlementLabel] = []
    missing_market = 0
    missing_climate = 0
    for ticker in sorted(contracts):
        contract = contracts[ticker]
        checkpoint = market_checkpoints.get(ticker)
        if checkpoint is None:
            missing_market += 1
            continue
        _validate_checkpoint(contract, checkpoint)
        climate = climate_features.get(contract.event_id)
        if climate is None:
            missing_climate += 1
            continue
        _validate_climate_feature(contract, climate, checkpoint.checkpoint_at)
        if len(climate.used_observations) < MIN_CLIMATE_SAMPLES:
            missing_climate += 1
            continue
        yes_count = sum(
            contract.predicate(row.temperature_deg_f) for row in climate.used_observations
        )
        climate_probability = (Decimal(yes_count) + Decimal(1)) / (
            Decimal(len(climate.used_observations)) + Decimal(2)
        )
        partition = event_partitions.get(contract.event_id)
        if partition is None:
            raise ModelTournamentError("canonical event lacks temporal partition assignment")
        label = labels_by_ticker[ticker]
        row_material = (
            FEATURE_SCHEMA_VERSION,
            contract.event_id,
            contract.contract_id,
            ticker,
            label.content_hash,
            checkpoint.checkpoint_id,
            climate.feature_evidence_id,
            str(checkpoint.yes_probability),
            str(climate_probability),
            len(climate.used_observations),
            partition.value,
        )
        digest = stable_hash(row_material)
        rows.append(
            TournamentFeatureRow(
                row_id=digest,
                event_id=contract.event_id,
                contract_id=contract.contract_id,
                market_ticker=ticker,
                settlement_label_id=label.content_hash,
                station_id=contract.station_id,
                measurement=contract.measurement,
                local_date=contract.local_date,
                realized_yes=int(label.resolved_outcome),
                checkpoint_at=checkpoint.checkpoint_at,
                market_probability=checkpoint.yes_probability,
                climate_probability=climate_probability,
                climate_sample_count=len(climate.used_observations),
                market_checkpoint_id=checkpoint.checkpoint_id,
                climate_feature_evidence_id=climate.feature_evidence_id,
                partition=partition,
                content_hash=digest,
            )
        )
        used_labels.append(label)

    ordered = tuple(sorted(rows, key=lambda row: (row.local_date, row.event_id, row.market_ticker)))
    if not ordered:
        raise ModelTournamentError("no complete M28C feature rows were produced")
    represented = {row.partition for row in ordered}
    if represented != set(TournamentPartition):
        raise ModelTournamentError("feature coverage must reach train, validation, and test")
    _validate_event_partition_integrity(ordered)

    authorities = {
        dataset.settlement_labels.authority
        for dataset in datasets
        if dataset.settlement_labels is not None
    }
    if len(authorities) != 1:
        raise ModelTournamentError("M28B settlement label authorities disagree across series")
    subset_manifest = SettlementLabelManifest.build(
        settlement_mapping_id=SETTLEMENT_MAPPING_ID,
        authority=authorities.pop(),
        labels=tuple(used_labels),
    )
    feature_schema_hash = stable_hash(
        (
            FEATURE_SCHEMA_VERSION,
            PREDICTION_CUTOFF_HOUR_UTC,
            MARKET_CANDLE_INTERVAL_MINUTES,
            int(MARKET_CANDLE_LOOKBACK.total_seconds()),
            CLIMATE_LOOKBACK_YEARS,
            CLIMATE_SEASONAL_WINDOW_DAYS,
            MIN_CLIMATE_SAMPLES,
            str(CITY_SHRINKAGE_PRIOR),
            str(EDGE_THRESHOLD),
            str(HYPOTHETICAL_FRICTION),
        )
    )
    train_ids = _event_ids(ordered, TournamentPartition.TRAIN)
    validation_ids = _event_ids(ordered, TournamentPartition.VALIDATION)
    test_ids = _event_ids(ordered, TournamentPartition.TEST)
    dataset_ids = tuple(sorted(source_dataset_ids))
    manifest_ids = tuple(sorted(source_manifest_ids))
    settlement_bundle_id = stable_hash(
        (SETTLEMENT_MAPPING_ID, dataset_ids, manifest_ids, subset_manifest.manifest_id)
    )
    material = (
        FEATURE_SCHEMA_VERSION,
        settlement_bundle_id,
        dataset_ids,
        manifest_ids,
        subset_manifest.manifest_id,
        expected_split_hash,
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
        settlement_bundle_id=settlement_bundle_id,
        settlement_dataset_ids=dataset_ids,
        source_settlement_label_manifest_ids=manifest_ids,
        settlement_mapping_id=SETTLEMENT_MAPPING_ID,
        settlement_labels=subset_manifest,
        temporal_split_hash=expected_split_hash,
        feature_schema_hash=feature_schema_hash,
        rows=ordered,
        train_event_ids=train_ids,
        validation_event_ids=validation_ids,
        test_event_ids=test_ids,
        missing_market_contracts=missing_market,
        missing_climate_contracts=missing_climate,
        content_hash=digest,
    )


def build_training_manifest(
    dataset: TournamentFeatureDataset,
    *,
    temporal_split: TemporalSplit,
    created_at: datetime,
) -> TrainingDatasetManifest:
    """Bind the exact used feature rows and exact canonical label subset to M28A lineage."""

    if dataset.temporal_split_hash != temporal_split.content_hash:
        raise ModelTournamentError(
            "training manifest temporal split disagrees with feature dataset"
        )
    return TrainingDatasetManifest.build(
        family=FAMILY,
        feature_schema_hash=dataset.feature_schema_hash,
        feature_artifact_ids=tuple(row.content_hash for row in dataset.rows),
        settlement_labels=dataset.settlement_labels,
        temporal_split=temporal_split,
        prediction_cutoff_rule=PREDICTION_CUTOFF_RULE,
        created_at=created_at,
    )


def build_development_model_artifact(
    dataset: TournamentFeatureDataset,
    tournament: ModelTournamentResult,
    training_manifest: TrainingDatasetManifest,
    *,
    trained_at: datetime,
) -> ModelArtifact:
    """Create a DEVELOPMENT-only artifact; tournament results never grant promotion authority."""

    if tournament.feature_dataset_id != dataset.dataset_id:
        raise ModelTournamentError("tournament result does not bind this feature dataset")
    if training_manifest.family != FAMILY:
        raise ModelTournamentError("training manifest family does not bind this feature dataset")
    if training_manifest.feature_schema_hash != dataset.feature_schema_hash:
        raise ModelTournamentError(
            "training manifest feature schema does not bind this feature dataset"
        )
    expected_feature_artifact_ids = tuple(sorted(row.content_hash for row in dataset.rows))
    if training_manifest.feature_artifact_ids != expected_feature_artifact_ids:
        raise ModelTournamentError(
            "training manifest feature rows do not bind this feature dataset"
        )
    if training_manifest.settlement_labels_id != dataset.settlement_labels.manifest_id:
        raise ModelTournamentError(
            "training manifest settlement labels do not bind this feature dataset"
        )
    if training_manifest.temporal_split_hash != dataset.temporal_split_hash:
        raise ModelTournamentError(
            "training manifest temporal split does not bind this feature dataset"
        )
    if training_manifest.prediction_cutoff_rule != PREDICTION_CUTOFF_RULE:
        raise ModelTournamentError(
            "training manifest prediction cutoff does not bind this feature dataset"
        )
    if (
        training_manifest.created_at.tzinfo is None
        or training_manifest.created_at.utcoffset() is None
    ):
        raise ModelTournamentError("training manifest created_at must be timezone-aware")
    created_at_utc = training_manifest.created_at.astimezone(UTC)
    if any(label.resolved_at > created_at_utc for label in dataset.settlement_labels.labels):
        raise ModelTournamentError(
            "training manifest cannot use unresolved future settlement labels"
        )
    expected_manifest_digest = stable_hash(
        (
            training_manifest.family,
            training_manifest.feature_schema_hash,
            tuple(sorted(training_manifest.feature_artifact_ids)),
            training_manifest.settlement_labels_id,
            training_manifest.temporal_split_hash,
            training_manifest.prediction_cutoff_rule,
            training_manifest.created_at.astimezone(UTC).isoformat(),
        )
    )
    if (
        training_manifest.manifest_id != expected_manifest_digest
        or training_manifest.content_hash != expected_manifest_digest
    ):
        raise ModelTournamentError("training manifest content identity is invalid")
    fit = tournament.fit
    return ModelArtifact.build(
        family=FAMILY,
        algorithm=fit.validation_selected_model.value,
        hyperparameters=(
            ("pooled_alpha", str(fit.pooled_alpha)),
            ("pooled_bias", str(fit.pooled_bias)),
            ("city_bias_hash", stable_hash(fit.city_biases)),
            ("calibration_slope", str(fit.calibration_slope)),
            ("calibration_offset", str(fit.calibration_offset)),
            ("ensemble_weight", str(fit.ensemble_weight)),
        ),
        feature_schema_hash=dataset.feature_schema_hash,
        training_manifest=training_manifest,
        calibration_method="train-affine + validation-selected market ensemble",
        parent_model_id=None,
        trained_at=trained_at,
        state=ModelState.DEVELOPMENT,
    )


def run_model_tournament(dataset: TournamentFeatureDataset) -> ModelTournamentResult:
    """Fit on train, select ensemble weight on validation, then reveal test once."""

    _validate_feature_dataset(dataset)
    train = _partition_rows(dataset, TournamentPartition.TRAIN)
    validation = _partition_rows(dataset, TournamentPartition.VALIDATION)
    test = _partition_rows(dataset, TournamentPartition.TEST)
    if len({row.event_id for row in train}) < 2:
        raise ModelTournamentError("training partition needs at least two independent events")

    alpha, bias = _fit_pooled_residual(train)
    city_biases = _fit_city_biases(train, alpha, bias)
    slope, offset = _fit_calibration(train, alpha, bias, city_biases)
    ensemble_weight = _fit_ensemble_weight(validation, alpha, bias, city_biases, slope, offset)
    selected_model = TournamentModel.CALIBRATED_ENSEMBLE
    fit_hash = stable_hash(
        (
            TOURNAMENT_VERSION,
            tuple(row.content_hash for row in train),
            tuple(row.content_hash for row in validation),
            str(alpha),
            str(bias),
            tuple((station, str(value)) for station, value in city_biases),
            str(slope),
            str(offset),
            str(ensemble_weight),
            selected_model.value,
        )
    )
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
    for partition, partition_rows in (
        (TournamentPartition.TRAIN, train),
        (TournamentPartition.VALIDATION, validation),
        (TournamentPartition.TEST, test),
    ):
        market_score = _score(
            partition_rows,
            TournamentModel.MARKET,
            partition,
            predictions={row.row_id: row.market_probability for row in partition_rows},
            market_brier=None,
        )
        scorecards.append(market_score)
        for model in (
            TournamentModel.NOAA_CLIMATOLOGY,
            TournamentModel.POOLED_RESIDUAL,
            TournamentModel.CITY_SHRUNK_RESIDUAL,
            TournamentModel.CALIBRATED_ENSEMBLE,
        ):
            scorecards.append(
                _score(
                    partition_rows,
                    model,
                    partition,
                    predictions=_predictions(
                        partition_rows,
                        model,
                        alpha,
                        bias,
                        city_biases,
                        slope,
                        offset,
                        ensemble_weight,
                    ),
                    market_brier=market_score.event_weighted_brier,
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
    digest = stable_hash(
        (
            TOURNAMENT_VERSION,
            dataset.dataset_id,
            fit.content_hash,
            tuple(_score_material(score) for score in ordered_scores),
            selected_test.model.value,
            edge,
            "NONE",
        )
    )
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


def _validate_m28b_partition_integrity(dataset: WeatherSettlementDataset) -> None:
    train = set(dataset.train_event_ids)
    validation = set(dataset.validation_event_ids)
    test = set(dataset.test_event_ids)
    if train & validation or train & test or validation & test:
        raise ModelTournamentError("M28B event partitions overlap")
    event_ids = {event.event_id for event in dataset.events}
    if train | validation | test != event_ids:
        raise ModelTournamentError("M28B event partition coverage is incomplete")


def _partition_from_m28b(partition: EventPartition) -> TournamentPartition:
    try:
        return TournamentPartition(partition.value)
    except ValueError as exc:
        raise ModelTournamentError("unsupported canonical M28B temporal partition") from exc


def _event_ids(
    rows: Sequence[TournamentFeatureRow], partition: TournamentPartition
) -> tuple[str, ...]:
    return tuple(sorted({row.event_id for row in rows if row.partition is partition}))


def _validate_event_partition_integrity(rows: Sequence[TournamentFeatureRow]) -> None:
    assignment: dict[str, TournamentPartition] = {}
    for row in rows:
        previous = assignment.setdefault(row.event_id, row.partition)
        if previous is not row.partition:
            raise ModelTournamentError("one event's sibling contracts cross temporal partitions")


def _validate_feature_dataset(dataset: TournamentFeatureDataset) -> None:
    if not dataset.rows:
        raise ModelTournamentError("feature dataset is empty")
    _validate_event_partition_integrity(dataset.rows)
    for partition, expected in (
        (TournamentPartition.TRAIN, dataset.train_event_ids),
        (TournamentPartition.VALIDATION, dataset.validation_event_ids),
        (TournamentPartition.TEST, dataset.test_event_ids),
    ):
        actual = _event_ids(dataset.rows, partition)
        if actual != expected or not actual:
            raise ModelTournamentError(
                "feature dataset temporal partition identity is inconsistent"
            )
    if set(dataset.train_event_ids) & set(dataset.validation_event_ids):
        raise ModelTournamentError("train and validation event identities overlap")
    if set(dataset.train_event_ids) & set(dataset.test_event_ids):
        raise ModelTournamentError("train and test event identities overlap")
    if set(dataset.validation_event_ids) & set(dataset.test_event_ids):
        raise ModelTournamentError("validation and test event identities overlap")


def _validate_checkpoint(
    contract: ResolvedTemperatureContract, checkpoint: MarketCheckpoint
) -> None:
    if checkpoint.market_ticker != contract.market_ticker:
        raise ModelTournamentError("market checkpoint ticker binding is invalid")
    expected = datetime.combine(contract.local_date, time(PREDICTION_CUTOFF_HOUR_UTC), tzinfo=UTC)
    if checkpoint.checkpoint_at != expected:
        raise ModelTournamentError("market checkpoint is not the exact reviewed 03Z cutoff")
    if checkpoint.checkpoint_at >= contract.settlement_at.astimezone(UTC):
        raise ModelTournamentError("market checkpoint is not pre-settlement")
    if checkpoint.selected_candle_end_ts > int(checkpoint.checkpoint_at.timestamp()):
        raise ModelTournamentError("future candle contaminated market checkpoint")


def _validate_climate_feature(
    contract: ResolvedTemperatureContract,
    climate: ClimateFeatureEvidence,
    checkpoint_at: datetime,
) -> None:
    if climate.classification is not ClimateEvidenceClassification.HISTORICAL_POINT_IN_TIME:
        raise ModelTournamentError("replay-only climate evidence cannot enter strict tournament")
    source = PHYSICAL_WEATHER_SOURCES.get(contract.station_id)
    if source is None:
        raise ModelTournamentError("canonical settlement station lacks reviewed physical source")
    if climate.station_id != source.ghcnd_station_id:
        raise ModelTournamentError("climate feature station disagrees with canonical settlement")
    if climate.measurement != contract.measurement:
        raise ModelTournamentError(
            "climate feature measurement disagrees with canonical settlement"
        )
    if climate.target_local_date != contract.local_date:
        raise ModelTournamentError(
            "climate feature target date disagrees with canonical settlement"
        )
    if climate.decision_cutoff_at != checkpoint_at:
        raise ModelTournamentError("climate feature cutoff disagrees with market checkpoint")
    if climate.lookback_years != CLIMATE_LOOKBACK_YEARS:
        raise ModelTournamentError("climate feature lookback policy is not canonical")
    if climate.seasonal_window_days != CLIMATE_SEASONAL_WINDOW_DAYS:
        raise ModelTournamentError("climate feature seasonal policy is not canonical")
    start_year = contract.local_date.year - CLIMATE_LOOKBACK_YEARS
    for observation in climate.used_observations:
        if observation.station_id != climate.station_id:
            raise ModelTournamentError("climate used observation station is inconsistent")
        if observation.measurement != contract.measurement:
            raise ModelTournamentError("climate used observation measurement is inconsistent")
        if not start_year <= observation.local_date.year < contract.local_date.year:
            raise ModelTournamentError(
                "climate used observation violates prior-calendar-year policy"
            )
        if (
            seasonal_distance_days(observation.local_date, contract.local_date)
            > CLIMATE_SEASONAL_WINDOW_DAYS
        ):
            raise ModelTournamentError("climate used observation violates seasonal-window policy")


def _reviewed_cutoff(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ModelTournamentError("market checkpoint must be timezone-aware")
    cutoff = value.astimezone(UTC)
    if cutoff.time() != time(PREDICTION_CUTOFF_HOUR_UTC):
        raise ModelTournamentError("market checkpoint is not the exact reviewed 03Z cutoff")
    return cutoff


def _candle_request_path(ticker: str, *, start_ts: int, end_ts: int) -> str:
    target = quote(ticker, safe="")
    return (
        f"/trade-api/v2/historical/markets/{target}/candlesticks"
        f"?start_ts={start_ts}&end_ts={end_ts}&period_interval={MARKET_CANDLE_INTERVAL_MINUTES}"
    )


def _market_probability(candle: Mapping[str, object]) -> Decimal | None:
    bid = _nested_decimal(candle, "yes_bid", ("close_dollars", "close"))
    ask = _nested_decimal(candle, "yes_ask", ("close_dollars", "close"))
    if bid is not None and ask is not None and bid <= ask:
        return (bid + ask) / Decimal(2)
    close = _nested_decimal(candle, "price", ("close_dollars", "close"))
    if close is not None:
        return close
    return _nested_decimal(candle, "price", ("previous_dollars", "previous"))


def _nested_decimal(
    payload: Mapping[str, object], field_name: str, names: Sequence[str]
) -> Decimal | None:
    nested = payload.get(field_name)
    if not isinstance(nested, Mapping):
        return None
    value: object | None = None
    for name in names:
        candidate = nested.get(name)
        if candidate is not None:
            value = candidate
            break
    if (
        value is None
        or isinstance(value, bool)
        or not isinstance(value, (str, int, float, Decimal))
    ):
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    if not result.is_finite() or not Decimal("0") <= result <= Decimal("1"):
        return None
    return result


def _partition_rows(
    dataset: TournamentFeatureDataset, partition: TournamentPartition
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
            candidate = (_event_weighted_brier(rows, predictions), alpha, bias)
            if best is None or candidate < best:
                best = candidate
    if best is None:
        raise ModelTournamentError("pooled residual fit produced no candidate")
    return best[1], best[2]


def _fit_city_biases(
    rows: Sequence[TournamentFeatureRow], alpha: Decimal, bias: Decimal
) -> tuple[tuple[str, Decimal], ...]:
    grouped: dict[str, list[Decimal]] = defaultdict(list)
    for row in rows:
        grouped[row.station_id].append(
            Decimal(row.realized_yes) - _pooled_prediction(row, alpha, bias)
        )
    result: list[tuple[str, Decimal]] = []
    for station, residuals in sorted(grouped.items()):
        count = Decimal(len(residuals))
        mean = sum(residuals, Decimal("0")) / count
        shrink = count / (count + CITY_SHRINKAGE_PRIOR)
        result.append((station, _bounded_bias(mean * shrink)))
    return tuple(result)


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
            candidate = (_event_weighted_brier(rows, predictions), slope, offset)
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
                weight * _calibrated_city_prediction(row, alpha, bias, city_map, slope, offset)
                + (Decimal("1") - weight) * row.market_probability
            )
            for row in rows
        }
        candidate = (_event_weighted_brier(rows, predictions), weight)
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
            value = _clip(
                ensemble_weight
                * _calibrated_city_prediction(row, alpha, bias, city_map, slope, offset)
                + (Decimal("1") - ensemble_weight) * row.market_probability
            )
        else:
            raise ModelTournamentError("unsupported challenger model")
        result[row.row_id] = value
    return result


def _pooled_prediction(row: TournamentFeatureRow, alpha: Decimal, bias: Decimal) -> Decimal:
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
    calibration_gap = _event_equal_calibration_gap(rows, predictions)
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
    rows: Sequence[TournamentFeatureRow], predictions: Mapping[str, Decimal]
) -> Decimal:
    grouped: dict[str, list[Decimal]] = defaultdict(list)
    for row in rows:
        grouped[row.event_id].append((predictions[row.row_id] - Decimal(row.realized_yes)) ** 2)
    return _equal_event_mean(grouped, metric="Brier")


def _event_weighted_log_loss(
    rows: Sequence[TournamentFeatureRow], predictions: Mapping[str, Decimal]
) -> Decimal:
    grouped: dict[str, list[Decimal]] = defaultdict(list)
    for row in rows:
        probability = _clip_for_log(predictions[row.row_id])
        p = float(probability)
        loss = -math.log(p if row.realized_yes else 1.0 - p)
        grouped[row.event_id].append(Decimal(str(loss)))
    return _equal_event_mean(grouped, metric="log loss")


def _event_equal_calibration_gap(
    rows: Sequence[TournamentFeatureRow], predictions: Mapping[str, Decimal]
) -> Decimal:
    predicted: dict[str, list[Decimal]] = defaultdict(list)
    outcomes: dict[str, list[Decimal]] = defaultdict(list)
    for row in rows:
        predicted[row.event_id].append(predictions[row.row_id])
        outcomes[row.event_id].append(Decimal(row.realized_yes))
    if not predicted:
        raise ModelTournamentError("calibration gap has no independent events")
    event_ids = sorted(predicted)
    predicted_mean = sum(
        (
            sum(predicted[event_id], Decimal("0")) / Decimal(len(predicted[event_id]))
            for event_id in event_ids
        ),
        Decimal("0"),
    ) / Decimal(len(event_ids))
    outcome_mean = sum(
        (
            sum(outcomes[event_id], Decimal("0")) / Decimal(len(outcomes[event_id]))
            for event_id in event_ids
        ),
        Decimal("0"),
    ) / Decimal(len(event_ids))
    return abs(predicted_mean - outcome_mean)


def _equal_event_mean(grouped: Mapping[str, Sequence[Decimal]], *, metric: str) -> Decimal:
    event_values = [sum(values, Decimal("0")) / Decimal(len(values)) for values in grouped.values()]
    if not event_values:
        raise ModelTournamentError(f"{metric} has no independent events")
    return sum(event_values, Decimal("0")) / Decimal(len(event_values))


def _hypothetical_pnl(
    rows: Sequence[TournamentFeatureRow], predictions: Mapping[str, Decimal]
) -> tuple[int, Decimal]:
    trades = 0
    total = Decimal("0")
    for row in rows:
        model_probability = predictions[row.row_id]
        delta = model_probability - row.market_probability
        if delta >= EDGE_THRESHOLD:
            entry = min(Decimal("1"), row.market_probability + HYPOTHETICAL_FRICTION)
            total += Decimal(row.realized_yes) - entry
            trades += 1
        elif delta <= -EDGE_THRESHOLD:
            no_market = Decimal("1") - row.market_probability
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
        score.hypothetical_pnl_classification,
    )
