"""Offline tests for M28 production weather strategy contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from services.production_weather_strategy.contracts import (
    DeploymentTier,
    ModelArtifact,
    ModelState,
    PredictionRecord,
    ProductionLearningPolicy,
    ProductionPromotion,
    ProductionStrategyError,
    SettlementLabel,
    SettlementLabelManifest,
    TemporalSplit,
    TrainingDatasetManifest,
)

NOW = datetime(2026, 8, 23, 3, 30, tzinfo=UTC)


def split() -> TemporalSplit:
    return TemporalSplit(
        train_start=datetime(2024, 1, 1, tzinfo=UTC),
        train_end=datetime(2025, 1, 1, tzinfo=UTC),
        validation_start=datetime(2025, 1, 1, tzinfo=UTC),
        validation_end=datetime(2026, 1, 1, tzinfo=UTC),
        test_start=datetime(2026, 1, 1, tzinfo=UTC),
        test_end=datetime(2026, 8, 1, tzinfo=UTC),
    )


def labels() -> SettlementLabelManifest:
    return SettlementLabelManifest.build(
        settlement_mapping_id="weather-company-kxhighchi-v1",
        authority="Kalshi settlement record + contract settlement source",
        labels=(
            SettlementLabel.build(
                event_id="event-a",
                market_ticker="market-a",
                resolved_outcome=True,
                resolved_at=NOW - timedelta(days=1),
                settlement_evidence_id="settlement-a",
            ),
            SettlementLabel.build(
                event_id="event-b",
                market_ticker="market-b",
                resolved_outcome=False,
                resolved_at=NOW - timedelta(days=1),
                settlement_evidence_id="settlement-b",
            ),
        ),
    )


def dataset() -> TrainingDatasetManifest:
    return TrainingDatasetManifest.build(
        family="KXHIGHCHI",
        feature_schema_hash="feature-schema-v1",
        feature_artifact_ids=("features-a", "features-b"),
        settlement_labels=labels(),
        temporal_split=split(),
        prediction_cutoff_rule="only evidence published before decision timestamp",
        created_at=NOW,
    )


def model() -> ModelArtifact:
    return ModelArtifact.build(
        family="KXHIGHCHI",
        algorithm="calibrated-gradient-boosted-ensemble",
        hyperparameters=(("max_depth", "3"), ("learning_rate", "0.03")),
        feature_schema_hash="feature-schema-v1",
        training_manifest=dataset(),
        calibration_method="isotonic-on-validation-window",
        parent_model_id=None,
        trained_at=NOW + timedelta(minutes=1),
        state=ModelState.CHALLENGER,
    )


def policy(*, bounded: bool = False) -> ProductionLearningPolicy:
    return ProductionLearningPolicy.build(
        family="KXHIGHCHI",
        automated_retraining=True,
        automated_challenger_creation=True,
        bounded_policy_promotion=bounded,
        minimum_unique_settled_events=2,
        maximum_weekly_parameter_change=Decimal("0.05"),
        maximum_single_trade_loss=Decimal("1.00"),
        maximum_daily_loss=Decimal("5.00"),
        maximum_open_market_exposure=Decimal("2.00"),
        minimum_calibration_score=Decimal("0.70"),
        minimum_market_relative_skill=Decimal("0.01"),
    )


def test_temporal_split_rejects_overlap() -> None:
    with pytest.raises(ProductionStrategyError, match="overlap"):
        TemporalSplit(
            train_start=datetime(2024, 1, 1, tzinfo=UTC),
            train_end=datetime(2025, 2, 1, tzinfo=UTC),
            validation_start=datetime(2025, 1, 1, tzinfo=UTC),
            validation_end=datetime(2026, 1, 1, tzinfo=UTC),
            test_start=datetime(2026, 1, 1, tzinfo=UTC),
            test_end=datetime(2026, 8, 1, tzinfo=UTC),
        )


def test_training_dataset_requires_authoritative_settlement_mapping() -> None:
    with pytest.raises(ProductionStrategyError, match="settlement mapping"):
        SettlementLabelManifest.build(
            settlement_mapping_id="",
            authority="source",
            labels=(
                SettlementLabel.build(
                    event_id="event-a",
                    market_ticker="market-a",
                    resolved_outcome=True,
                    resolved_at=NOW,
                    settlement_evidence_id="settlement-a",
                ),
            ),
        )


def test_settlement_label_rejects_incomplete_or_naive_records() -> None:
    with pytest.raises(ProductionStrategyError, match="identity"):
        SettlementLabel.build(
            event_id="",
            market_ticker="market-a",
            resolved_outcome=True,
            resolved_at=NOW,
            settlement_evidence_id="settlement-a",
        )


def test_settlement_label_direct_constructor_derives_identity() -> None:
    first = SettlementLabel("event-a", "market-a", True, NOW, "settlement-a")
    second = SettlementLabel("event-a", "market-a", True, NOW, "settlement-a")
    assert first.content_hash == second.content_hash
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        SettlementLabel(  # type: ignore[call-arg]
            "event-a", "market-a", True, NOW, "settlement-a", content_hash="forged"
        )


def test_settlement_label_identity_changes_for_each_semantic_field() -> None:
    baseline = SettlementLabel("event-a", "market-a", True, NOW, "settlement-a")
    variants = (
        SettlementLabel("event-a", "market-a", False, NOW, "settlement-a"),
        SettlementLabel("event-a", "market-a", True, NOW + timedelta(seconds=1), "settlement-a"),
        SettlementLabel("event-a", "market-a", True, NOW, "settlement-b"),
        SettlementLabel("event-b", "market-a", True, NOW, "settlement-a"),
        SettlementLabel("event-a", "market-b", True, NOW, "settlement-a"),
    )
    assert all(label.content_hash != baseline.content_hash for label in variants)
    with pytest.raises(ProductionStrategyError, match="timezone-aware"):
        SettlementLabel.build(
            event_id="event-a",
            market_ticker="market-a",
            resolved_outcome=True,
            resolved_at=datetime(2026, 8, 22),
            settlement_evidence_id="settlement-a",
        )


def test_settlement_manifest_rejects_duplicate_market_labels() -> None:
    label = SettlementLabel.build(
        event_id="event-a",
        market_ticker="market-a",
        resolved_outcome=True,
        resolved_at=NOW,
        settlement_evidence_id="settlement-a",
    )
    with pytest.raises(ProductionStrategyError, match="market labels must be unique"):
        SettlementLabelManifest.build(
            settlement_mapping_id="mapping-v1",
            authority="source",
            labels=(label, label),
        )


def test_settlement_manifest_direct_constructor_derives_identity() -> None:
    label = SettlementLabel(
        event_id="event-a",
        market_ticker="market-a",
        resolved_outcome=True,
        resolved_at=NOW,
        settlement_evidence_id="settlement-a",
    )
    manifest = SettlementLabelManifest(
        settlement_mapping_id="mapping-v1",
        authority="source",
        labels=(label,),
    )
    assert manifest.manifest_id == manifest.content_hash
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        SettlementLabelManifest(  # type: ignore[call-arg]
            settlement_mapping_id="mapping-v1",
            authority="source",
            labels=(label,),
            manifest_id="forged",
            content_hash="forged",
        )


def test_training_dataset_rejects_future_labels() -> None:
    future = SettlementLabelManifest.build(
        settlement_mapping_id="mapping-v1",
        authority="source",
        labels=(
            SettlementLabel.build(
                event_id="event-a",
                market_ticker="market-a",
                resolved_outcome=True,
                resolved_at=NOW + timedelta(hours=1),
                settlement_evidence_id="settlement-a",
            ),
        ),
    )
    with pytest.raises(ProductionStrategyError, match="future labels"):
        TrainingDatasetManifest.build(
            family="KXHIGHCHI",
            feature_schema_hash="feature-schema-v1",
            feature_artifact_ids=("features-a",),
            settlement_labels=future,
            temporal_split=split(),
            prediction_cutoff_rule="evidence before cutoff",
            created_at=NOW,
        )


def test_model_is_bound_to_exact_dataset_and_feature_schema() -> None:
    artifact = model()
    assert artifact.training_manifest_id == dataset().manifest_id
    assert artifact.feature_schema_hash == "feature-schema-v1"
    assert artifact.content_hash == artifact.model_id

    with pytest.raises(ProductionStrategyError, match="feature schema"):
        ModelArtifact.build(
            family="KXHIGHCHI",
            algorithm="ensemble",
            hyperparameters=(),
            feature_schema_hash="different-schema",
            training_manifest=dataset(),
            calibration_method="isotonic",
            parent_model_id=None,
            trained_at=NOW + timedelta(minutes=1),
        )


def test_automated_retraining_does_not_itself_authorize_bounded_production() -> None:
    challenger = model()
    learning_policy = policy(bounded=False)
    assert learning_policy.automated_retraining is True
    assert learning_policy.automated_challenger_creation is True

    with pytest.raises(ProductionStrategyError, match="not enabled"):
        ProductionPromotion.build(
            model=challenger,
            previous_model_id=None,
            policy=learning_policy,
            deployment_tier=DeploymentTier.BOUNDED_PRODUCTION,
            unique_settled_events=10,
            calibration_score=Decimal("0.80"),
            market_relative_skill=Decimal("0.05"),
            evaluation_manifest_id="evaluation-v1",
            authorized_at=NOW + timedelta(minutes=2),
            authorized_by="bounded-policy:m28-test",
        )


def test_predeclared_bounded_policy_can_authorize_future_autonomous_promotion() -> None:
    promotion = ProductionPromotion.build(
        model=model(),
        previous_model_id=None,
        policy=policy(bounded=True),
        deployment_tier=DeploymentTier.BOUNDED_PRODUCTION,
        unique_settled_events=10,
        calibration_score=Decimal("0.80"),
        market_relative_skill=Decimal("0.05"),
        evaluation_manifest_id="evaluation-v1",
        authorized_at=NOW + timedelta(minutes=2),
        authorized_by="bounded-policy:m28-test",
    )
    assert promotion.deployment_tier is DeploymentTier.BOUNDED_PRODUCTION
    assert promotion.model_state is ModelState.CHALLENGER


@pytest.mark.parametrize("state", [ModelState.QUARANTINED, ModelState.RETIRED])
@pytest.mark.parametrize(
    "tier",
    [DeploymentTier.SHADOW, DeploymentTier.ONE_CONTRACT_CANARY, DeploymentTier.BOUNDED_PRODUCTION],
)
def test_quarantined_or_retired_models_cannot_be_promoted(
    state: ModelState, tier: DeploymentTier
) -> None:
    with pytest.raises(ProductionStrategyError, match="cannot be promoted"):
        ProductionPromotion.build(
            model=ModelArtifact.build(
                family="KXHIGHCHI",
                algorithm="ensemble",
                hyperparameters=(),
                feature_schema_hash="feature-schema-v1",
                training_manifest=dataset(),
                calibration_method="isotonic",
                parent_model_id=None,
                trained_at=NOW + timedelta(minutes=1),
                state=state,
            ),
            previous_model_id=None,
            policy=policy(bounded=True),
            deployment_tier=tier,
            unique_settled_events=10,
            calibration_score=Decimal("0.80"),
            market_relative_skill=Decimal("0.05"),
            evaluation_manifest_id="evaluation-v1",
            authorized_at=NOW + timedelta(minutes=2),
            authorized_by="bounded-policy:m28-test",
        )


def test_settlement_manifest_and_dataset_bind_exact_outcomes_and_pairing() -> None:
    first = labels()
    market_b = next(label for label in first.labels if label.market_ticker == "market-b")
    changed_outcome = SettlementLabelManifest.build(
        settlement_mapping_id=first.settlement_mapping_id,
        authority=first.authority,
        labels=(
            SettlementLabel.build(
                event_id="event-a",
                market_ticker="market-a",
                resolved_outcome=False,
                resolved_at=NOW - timedelta(days=1),
                settlement_evidence_id="settlement-a",
            ),
            market_b,
        ),
    )
    changed_pairing = SettlementLabelManifest.build(
        settlement_mapping_id=first.settlement_mapping_id,
        authority=first.authority,
        labels=(
            SettlementLabel.build(
                event_id="event-b",
                market_ticker="market-a",
                resolved_outcome=True,
                resolved_at=NOW - timedelta(days=1),
                settlement_evidence_id="settlement-a",
            ),
            SettlementLabel.build(
                event_id="event-a",
                market_ticker="market-b",
                resolved_outcome=False,
                resolved_at=NOW - timedelta(days=1),
                settlement_evidence_id="settlement-b",
            ),
        ),
    )
    assert changed_outcome.manifest_id != first.manifest_id
    assert changed_pairing.manifest_id != first.manifest_id
    assert (
        TrainingDatasetManifest.build(
            family="KXHIGHCHI",
            feature_schema_hash="feature-schema-v1",
            feature_artifact_ids=("features-a", "features-b"),
            settlement_labels=changed_outcome,
            temporal_split=split(),
            prediction_cutoff_rule="only evidence published before decision timestamp",
            created_at=NOW,
        ).manifest_id
        != dataset().manifest_id
    )


def test_settlement_evidence_and_resolution_time_change_downstream_identity() -> None:
    baseline = labels()
    baseline_dataset = dataset()
    changed_evidence = SettlementLabel.build(
        event_id="event-a",
        market_ticker="market-a",
        resolved_outcome=True,
        resolved_at=NOW - timedelta(days=1),
        settlement_evidence_id="settlement-other",
    )
    changed_time = SettlementLabel.build(
        event_id="event-a",
        market_ticker="market-a",
        resolved_outcome=True,
        resolved_at=NOW - timedelta(days=1, seconds=1),
        settlement_evidence_id="settlement-a",
    )

    def manifest_for(label: SettlementLabel) -> SettlementLabelManifest:
        other = next(item for item in baseline.labels if item.market_ticker == "market-b")
        return SettlementLabelManifest.build(
            settlement_mapping_id=baseline.settlement_mapping_id,
            authority=baseline.authority,
            labels=(label, other),
        )

    for changed in (changed_evidence, changed_time):
        changed_manifest = manifest_for(changed)
        changed_dataset = TrainingDatasetManifest.build(
            family="KXHIGHCHI",
            feature_schema_hash="feature-schema-v1",
            feature_artifact_ids=("features-a", "features-b"),
            settlement_labels=changed_manifest,
            temporal_split=split(),
            prediction_cutoff_rule="only evidence published before decision timestamp",
            created_at=NOW,
        )
        assert changed_manifest.manifest_id != baseline.manifest_id
        assert changed_dataset.manifest_id != baseline_dataset.manifest_id


def test_prediction_is_prospective_content_addressed_and_model_bound() -> None:
    artifact = model()
    prediction = PredictionRecord.build(
        model=artifact,
        event_ticker="KXHIGHCHI-26AUG24",
        market_ticker="KXHIGHCHI-26AUG24-B76.5",
        side_probability=Decimal("0.63"),
        evidence_ids=("noaa-03z", "kalshi-book"),
        feature_schema_hash=artifact.feature_schema_hash,
        prediction_cutoff=NOW + timedelta(minutes=5),
        created_at=NOW + timedelta(minutes=4),
    )
    assert prediction.model_id == artifact.model_id
    assert prediction.content_hash == prediction.prediction_id


def test_prediction_rejects_post_cutoff_creation() -> None:
    artifact = model()
    with pytest.raises(ProductionStrategyError, match="after its decision cutoff"):
        PredictionRecord.build(
            model=artifact,
            event_ticker="event",
            market_ticker="market",
            side_probability=Decimal("0.50"),
            evidence_ids=("evidence",),
            feature_schema_hash=artifact.feature_schema_hash,
            prediction_cutoff=NOW,
            created_at=NOW + timedelta(seconds=1),
        )


def test_new_package_does_not_import_research_learning_configuration_or_execution() -> None:
    source = Path("services/production_weather_strategy/contracts.py").read_text()
    assert "services.learning.configuration" not in source
    assert "services.production_execution" not in source
    assert "services.risk_engine.authorization" not in source
    assert "services.supervised_canary" not in source
