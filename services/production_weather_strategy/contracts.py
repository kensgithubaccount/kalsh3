"""M28 production weather strategy contracts.

This package is intentionally separate from ``services.learning``. The existing learning
package is research-only and structurally enforces zero production influence. M28 keeps the
same evidence, forecasting, evaluation, and machine-learning ideas while introducing a
separate, explicitly governed production lane.

The contracts here do not trade, access credentials, call Kalshi, mutate production state,
or promote a model by themselves. They make the production-learning boundary explicit:

* training data must use authoritative settlement labels;
* temporal splits must be strictly ordered to prevent future leakage;
* every model is content-addressed and bound to its exact training manifest;
* automated retraining may create challengers, but deployment requires a separate promotion
  record;
* bounded-policy promotion is supported for future autonomy, but only when the policy itself
  supplies explicit capital, loss, and evidence limits;
* every live prediction records the model, evidence, probability, and decision cutoff so
  outcomes can later train/evaluate without rewriting history.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from services.historical_replay.archive import stable_hash


class ProductionStrategyError(ValueError):
    """A production strategy invariant was violated."""


class ModelState(StrEnum):
    DEVELOPMENT = "DEVELOPMENT"
    SHADOW = "SHADOW"
    CHALLENGER = "CHALLENGER"
    LIMITED_PRODUCTION = "LIMITED_PRODUCTION"
    CHAMPION = "CHAMPION"
    QUARANTINED = "QUARANTINED"
    RETIRED = "RETIRED"


class DeploymentTier(StrEnum):
    SHADOW = "SHADOW"
    ONE_CONTRACT_CANARY = "ONE_CONTRACT_CANARY"
    BOUNDED_PRODUCTION = "BOUNDED_PRODUCTION"


@dataclass(frozen=True, slots=True)
class TemporalSplit:
    """Strictly ordered train/validation/test windows for leakage-resistant evaluation."""

    train_start: datetime
    train_end: datetime
    validation_start: datetime
    validation_end: datetime
    test_start: datetime
    test_end: datetime

    def __post_init__(self) -> None:
        points = (
            self.train_start,
            self.train_end,
            self.validation_start,
            self.validation_end,
            self.test_start,
            self.test_end,
        )
        if any(point.tzinfo is None for point in points):
            raise ProductionStrategyError("temporal split timestamps must be timezone-aware")
        if not (
            self.train_start
            < self.train_end
            <= self.validation_start
            < self.validation_end
            <= self.test_start
            < self.test_end
        ):
            raise ProductionStrategyError("training, validation, and test windows overlap")

    @property
    def content_hash(self) -> str:
        return stable_hash(
            (
                self.train_start.astimezone(UTC).isoformat(),
                self.train_end.astimezone(UTC).isoformat(),
                self.validation_start.astimezone(UTC).isoformat(),
                self.validation_end.astimezone(UTC).isoformat(),
                self.test_start.astimezone(UTC).isoformat(),
                self.test_end.astimezone(UTC).isoformat(),
            )
        )


@dataclass(frozen=True, slots=True)
class SettlementLabelManifest:
    """Authoritative resolved-outcome labels used for production training/evaluation."""

    manifest_id: str
    settlement_mapping_id: str
    authority: str
    event_ids: tuple[str, ...]
    market_tickers: tuple[str, ...]
    resolved_at: datetime
    content_hash: str

    @classmethod
    def build(
        cls,
        *,
        settlement_mapping_id: str,
        authority: str,
        event_ids: tuple[str, ...],
        market_tickers: tuple[str, ...],
        resolved_at: datetime,
    ) -> SettlementLabelManifest:
        if not settlement_mapping_id.strip():
            raise ProductionStrategyError("authoritative settlement mapping is required")
        if not authority.strip():
            raise ProductionStrategyError("settlement authority is required")
        if resolved_at.tzinfo is None:
            raise ProductionStrategyError("resolved_at must be timezone-aware")
        if not event_ids or not market_tickers:
            raise ProductionStrategyError("settlement label manifest cannot be empty")
        if len(set(event_ids)) != len(event_ids):
            raise ProductionStrategyError("settlement event ids must be unique")
        if len(set(market_tickers)) != len(market_tickers):
            raise ProductionStrategyError("settlement market tickers must be unique")
        values = (
            settlement_mapping_id,
            authority,
            tuple(sorted(event_ids)),
            tuple(sorted(market_tickers)),
            resolved_at.astimezone(UTC).isoformat(),
        )
        digest = stable_hash(values)
        return cls(
            manifest_id=digest,
            settlement_mapping_id=settlement_mapping_id,
            authority=authority,
            event_ids=tuple(sorted(event_ids)),
            market_tickers=tuple(sorted(market_tickers)),
            resolved_at=resolved_at.astimezone(UTC),
            content_hash=digest,
        )


@dataclass(frozen=True, slots=True)
class TrainingDatasetManifest:
    """Exact production training dataset identity and leakage boundary."""

    manifest_id: str
    family: str
    feature_schema_hash: str
    feature_artifact_ids: tuple[str, ...]
    settlement_labels_id: str
    temporal_split_hash: str
    prediction_cutoff_rule: str
    created_at: datetime
    content_hash: str

    @classmethod
    def build(
        cls,
        *,
        family: str,
        feature_schema_hash: str,
        feature_artifact_ids: tuple[str, ...],
        settlement_labels: SettlementLabelManifest,
        temporal_split: TemporalSplit,
        prediction_cutoff_rule: str,
        created_at: datetime,
    ) -> TrainingDatasetManifest:
        if not family.strip():
            raise ProductionStrategyError("market family is required")
        if not feature_schema_hash.strip() or not feature_artifact_ids:
            raise ProductionStrategyError("feature evidence is required")
        if len(set(feature_artifact_ids)) != len(feature_artifact_ids):
            raise ProductionStrategyError("feature artifact ids must be unique")
        if not prediction_cutoff_rule.strip():
            raise ProductionStrategyError("prediction cutoff rule is required")
        if created_at.tzinfo is None:
            raise ProductionStrategyError("created_at must be timezone-aware")
        if settlement_labels.resolved_at > created_at.astimezone(UTC):
            raise ProductionStrategyError("training dataset cannot use unresolved future labels")
        values = (
            family,
            feature_schema_hash,
            tuple(sorted(feature_artifact_ids)),
            settlement_labels.manifest_id,
            temporal_split.content_hash,
            prediction_cutoff_rule,
            created_at.astimezone(UTC).isoformat(),
        )
        digest = stable_hash(values)
        return cls(
            manifest_id=digest,
            family=family,
            feature_schema_hash=feature_schema_hash,
            feature_artifact_ids=tuple(sorted(feature_artifact_ids)),
            settlement_labels_id=settlement_labels.manifest_id,
            temporal_split_hash=temporal_split.content_hash,
            prediction_cutoff_rule=prediction_cutoff_rule,
            created_at=created_at.astimezone(UTC),
            content_hash=digest,
        )


@dataclass(frozen=True, slots=True)
class ModelArtifact:
    """Content-addressed trainable weather model or ensemble."""

    model_id: str
    family: str
    algorithm: str
    hyperparameters: tuple[tuple[str, str], ...]
    feature_schema_hash: str
    training_manifest_id: str
    calibration_method: str
    parent_model_id: str | None
    trained_at: datetime
    state: ModelState
    content_hash: str

    @classmethod
    def build(
        cls,
        *,
        family: str,
        algorithm: str,
        hyperparameters: tuple[tuple[str, str], ...],
        feature_schema_hash: str,
        training_manifest: TrainingDatasetManifest,
        calibration_method: str,
        parent_model_id: str | None,
        trained_at: datetime,
        state: ModelState = ModelState.DEVELOPMENT,
    ) -> ModelArtifact:
        if family != training_manifest.family:
            raise ProductionStrategyError("model family does not match training dataset")
        if feature_schema_hash != training_manifest.feature_schema_hash:
            raise ProductionStrategyError("model feature schema does not match training dataset")
        if not algorithm.strip() or not calibration_method.strip():
            raise ProductionStrategyError("algorithm and calibration method are required")
        if trained_at.tzinfo is None:
            raise ProductionStrategyError("trained_at must be timezone-aware")
        if trained_at.astimezone(UTC) < training_manifest.created_at:
            raise ProductionStrategyError("model cannot predate its training dataset")
        normalized_hyperparameters = tuple(sorted(hyperparameters))
        values = (
            family,
            algorithm,
            normalized_hyperparameters,
            feature_schema_hash,
            training_manifest.manifest_id,
            calibration_method,
            parent_model_id,
            trained_at.astimezone(UTC).isoformat(),
        )
        digest = stable_hash(values)
        return cls(
            model_id=digest,
            family=family,
            algorithm=algorithm,
            hyperparameters=normalized_hyperparameters,
            feature_schema_hash=feature_schema_hash,
            training_manifest_id=training_manifest.manifest_id,
            calibration_method=calibration_method,
            parent_model_id=parent_model_id,
            trained_at=trained_at.astimezone(UTC),
            state=state,
            content_hash=digest,
        )


@dataclass(frozen=True, slots=True)
class ProductionLearningPolicy:
    """Pre-declared bounds for automated retraining and eventual bounded promotion."""

    policy_id: str
    family: str
    automated_retraining: bool
    automated_challenger_creation: bool
    bounded_policy_promotion: bool
    minimum_unique_settled_events: int
    maximum_weekly_parameter_change: Decimal
    maximum_single_trade_loss: Decimal
    maximum_daily_loss: Decimal
    maximum_open_market_exposure: Decimal
    minimum_calibration_score: Decimal
    minimum_market_relative_skill: Decimal
    content_hash: str

    @classmethod
    def build(
        cls,
        *,
        family: str,
        automated_retraining: bool,
        automated_challenger_creation: bool,
        bounded_policy_promotion: bool,
        minimum_unique_settled_events: int,
        maximum_weekly_parameter_change: Decimal,
        maximum_single_trade_loss: Decimal,
        maximum_daily_loss: Decimal,
        maximum_open_market_exposure: Decimal,
        minimum_calibration_score: Decimal,
        minimum_market_relative_skill: Decimal,
    ) -> ProductionLearningPolicy:
        if not family.strip():
            raise ProductionStrategyError("policy family is required")
        if minimum_unique_settled_events < 1:
            raise ProductionStrategyError("settled-event threshold must be positive")
        if not Decimal("0") < maximum_weekly_parameter_change <= Decimal("0.10"):
            raise ProductionStrategyError("weekly model change must be within (0, 0.10]")
        for value, name in (
            (maximum_single_trade_loss, "single-trade loss"),
            (maximum_daily_loss, "daily loss"),
            (maximum_open_market_exposure, "open-market exposure"),
        ):
            if value <= 0:
                raise ProductionStrategyError(f"{name} bound must be positive")
        if maximum_single_trade_loss > maximum_open_market_exposure:
            raise ProductionStrategyError("single-trade loss exceeds market exposure bound")
        if maximum_single_trade_loss > maximum_daily_loss:
            raise ProductionStrategyError("single-trade loss exceeds daily loss bound")
        for value, name in (
            (minimum_calibration_score, "calibration"),
            (minimum_market_relative_skill, "market-relative skill"),
        ):
            if not Decimal("-1") <= value <= Decimal("1"):
                raise ProductionStrategyError(f"{name} threshold must be in [-1, 1]")
        values = (
            family,
            automated_retraining,
            automated_challenger_creation,
            bounded_policy_promotion,
            minimum_unique_settled_events,
            str(maximum_weekly_parameter_change),
            str(maximum_single_trade_loss),
            str(maximum_daily_loss),
            str(maximum_open_market_exposure),
            str(minimum_calibration_score),
            str(minimum_market_relative_skill),
        )
        digest = stable_hash(values)
        return cls(
            policy_id=digest,
            family=family,
            automated_retraining=automated_retraining,
            automated_challenger_creation=automated_challenger_creation,
            bounded_policy_promotion=bounded_policy_promotion,
            minimum_unique_settled_events=minimum_unique_settled_events,
            maximum_weekly_parameter_change=maximum_weekly_parameter_change,
            maximum_single_trade_loss=maximum_single_trade_loss,
            maximum_daily_loss=maximum_daily_loss,
            maximum_open_market_exposure=maximum_open_market_exposure,
            minimum_calibration_score=minimum_calibration_score,
            minimum_market_relative_skill=minimum_market_relative_skill,
            content_hash=digest,
        )


@dataclass(frozen=True, slots=True)
class ProductionPromotion:
    """Separate immutable authority to move a model toward production influence."""

    promotion_id: str
    model_id: str
    previous_model_id: str | None
    policy_id: str
    deployment_tier: DeploymentTier
    unique_settled_events: int
    calibration_score: Decimal
    market_relative_skill: Decimal
    evaluation_manifest_id: str
    authorized_at: datetime
    authorized_by: str
    content_hash: str

    @classmethod
    def build(
        cls,
        *,
        model: ModelArtifact,
        previous_model_id: str | None,
        policy: ProductionLearningPolicy,
        deployment_tier: DeploymentTier,
        unique_settled_events: int,
        calibration_score: Decimal,
        market_relative_skill: Decimal,
        evaluation_manifest_id: str,
        authorized_at: datetime,
        authorized_by: str,
    ) -> ProductionPromotion:
        if model.family != policy.family:
            raise ProductionStrategyError("model and promotion policy families differ")
        if not evaluation_manifest_id.strip() or not authorized_by.strip():
            raise ProductionStrategyError("promotion evidence and authority are required")
        if authorized_at.tzinfo is None:
            raise ProductionStrategyError("authorized_at must be timezone-aware")
        if unique_settled_events < policy.minimum_unique_settled_events:
            raise ProductionStrategyError("promotion settled-event threshold not met")
        if calibration_score < policy.minimum_calibration_score:
            raise ProductionStrategyError("promotion calibration threshold not met")
        if market_relative_skill < policy.minimum_market_relative_skill:
            raise ProductionStrategyError("promotion market-relative skill threshold not met")
        if (
            deployment_tier is DeploymentTier.BOUNDED_PRODUCTION
            and not policy.bounded_policy_promotion
        ):
            raise ProductionStrategyError("bounded autonomous promotion is not enabled")
        values = (
            model.model_id,
            previous_model_id,
            policy.policy_id,
            deployment_tier.value,
            unique_settled_events,
            str(calibration_score),
            str(market_relative_skill),
            evaluation_manifest_id,
            authorized_at.astimezone(UTC).isoformat(),
            authorized_by,
        )
        digest = stable_hash(values)
        return cls(
            promotion_id=digest,
            model_id=model.model_id,
            previous_model_id=previous_model_id,
            policy_id=policy.policy_id,
            deployment_tier=deployment_tier,
            unique_settled_events=unique_settled_events,
            calibration_score=calibration_score,
            market_relative_skill=market_relative_skill,
            evaluation_manifest_id=evaluation_manifest_id,
            authorized_at=authorized_at.astimezone(UTC),
            authorized_by=authorized_by,
            content_hash=digest,
        )


@dataclass(frozen=True, slots=True)
class PredictionRecord:
    """Immutable prospective prediction for later settlement-linked learning."""

    prediction_id: str
    model_id: str
    family: str
    event_ticker: str
    market_ticker: str
    side_probability: Decimal
    evidence_ids: tuple[str, ...]
    feature_schema_hash: str
    prediction_cutoff: datetime
    created_at: datetime
    content_hash: str

    @classmethod
    def build(
        cls,
        *,
        model: ModelArtifact,
        event_ticker: str,
        market_ticker: str,
        side_probability: Decimal,
        evidence_ids: tuple[str, ...],
        feature_schema_hash: str,
        prediction_cutoff: datetime,
        created_at: datetime,
    ) -> PredictionRecord:
        if not Decimal("0") <= side_probability <= Decimal("1"):
            raise ProductionStrategyError("prediction probability must be in [0, 1]")
        if not event_ticker.strip() or not market_ticker.strip() or not evidence_ids:
            raise ProductionStrategyError("prediction identity and evidence are required")
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ProductionStrategyError("prediction evidence ids must be unique")
        if feature_schema_hash != model.feature_schema_hash:
            raise ProductionStrategyError("prediction feature schema does not match model")
        if prediction_cutoff.tzinfo is None or created_at.tzinfo is None:
            raise ProductionStrategyError("prediction timestamps must be timezone-aware")
        if created_at.astimezone(UTC) > prediction_cutoff.astimezone(UTC):
            raise ProductionStrategyError("prediction was created after its decision cutoff")
        normalized_evidence = tuple(sorted(evidence_ids))
        values = (
            model.model_id,
            model.family,
            event_ticker,
            market_ticker,
            str(side_probability),
            normalized_evidence,
            feature_schema_hash,
            prediction_cutoff.astimezone(UTC).isoformat(),
            created_at.astimezone(UTC).isoformat(),
        )
        digest = stable_hash(values)
        return cls(
            prediction_id=digest,
            model_id=model.model_id,
            family=model.family,
            event_ticker=event_ticker,
            market_ticker=market_ticker,
            side_probability=side_probability,
            evidence_ids=normalized_evidence,
            feature_schema_hash=feature_schema_hash,
            prediction_cutoff=prediction_cutoff.astimezone(UTC),
            created_at=created_at.astimezone(UTC),
            content_hash=digest,
        )
