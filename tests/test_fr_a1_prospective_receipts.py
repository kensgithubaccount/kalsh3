from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

import services.forward_reality.prospective_receipts as receipt_module
from services.forecasting.domain import ForecastError, ModelFamily
from services.forecasting.models import (
    FeatureProvenance,
    FeatureSnapshot,
    FeatureValue,
    Forecast,
    ForecastKind,
    MarketReference,
)
from services.forward_reality.prospective_receipts import (
    ProspectivePredictionReceipt,
    ProspectiveReceiptError,
    ProspectiveReceiptStore,
)

NOW = datetime(2026, 8, 28, 12, tzinfo=UTC)


def make_forecast(**changes: object) -> Forecast:
    feature = FeatureSnapshot.build(
        NOW,
        "feature-v1",
        (
            FeatureValue(
                "x",
                Decimal("1"),
                ("source-1",),
                NOW,
                FeatureProvenance.FUNDAMENTAL_STRUCTURED,
                False,
                None,
            ),
        ),
    )
    values: dict[str, object] = {
        "market_ticker": "KXCPI-26SEP-T3.0",
        "event_id": "event-cpi-2026-09",
        "series_id": "KXCPI",
        "market_family": ModelFamily.SCHEDULED_ECONOMIC_RELEASE,
        "rules_version": "rules-v1",
        "rules_hash": "rules-hash",
        "forecast_kind": ForecastKind.INDEPENDENT_FUNDAMENTAL,
        "issued_at": NOW,
        "replay_time": None,
        "target_resolution_time": NOW + timedelta(hours=24),
        "horizon_seconds": 86400,
        "model_id": "model-cpi-1",
        "model_version": "model-cpi-1.0",
        "feature_schema_version": feature.schema_version,
        "model_artifact_hash": "artifact-hash",
        "calibration_id": "calibration-cpi-1",
        "calibration_version": "calibration-cpi-1.0",
        "feature_snapshot_id": feature.snapshot_id,
        "evidence_bundle_id": "evidence-1",
        "source_snapshot_id": "source-1",
        "raw_probability": Decimal(".60"),
        "calibrated_probability": Decimal(".58"),
        "lower_probability": Decimal(".50"),
        "upper_probability": Decimal(".66"),
        "interval_level": Decimal(".90"),
        "uncertainty_method": "fixture-interval",
        "uncertainty_quality": "RESEARCH",
        "market_reference": MarketReference.midpoint(
            forecast_at=NOW,
            snapshot_time=NOW,
            yes_bid=Decimal(".40"),
            yes_ask=Decimal(".44"),
            no_bid=Decimal(".56"),
            no_ask=Decimal(".60"),
            max_age_ms=1000,
            max_spread=Decimal(".10"),
        ),
        "abstention_reason": None,
        "research_status": "RESEARCH",
        "production_influence": Decimal("0"),
        "code_git_sha": "code-sha",
        "created_at": NOW,
    }
    values.update(changes)
    return Forecast.freeze(**values)


def make_receipt(**forecast_changes: object) -> ProspectivePredictionReceipt:
    return ProspectivePredictionReceipt.from_forecast(
        make_forecast(**forecast_changes),
        candidate_id="candidate-cpi-1",
        calibrator_id="calibrator-cpi-1",
        feature_schema_id="feature-schema-v1",
        code_identity="code-sha",
        event_ticker="KXCPI-26SEP",
        underlying_event_id="cpi-release-2026-09",
        evidence_ids=("evidence-1",),
        source_ids=("source-1",),
        market_snapshot_id="market-snapshot-1",
    )


def test_receipt_is_content_addressed_and_create_only(tmp_path) -> None:
    receipt = make_receipt()
    store = ProspectiveReceiptStore(tmp_path)
    store.publish(receipt)
    store.publish(receipt)
    assert (tmp_path / f"{receipt.receipt_id}.json").read_bytes() == receipt.to_bytes()
    path = tmp_path / f"{receipt.receipt_id}.json"
    path.write_bytes(receipt.to_bytes().replace(b'"0.58"', b'"0.59"', 1))
    with pytest.raises(ProspectiveReceiptError, match="conflicting"):
        store.publish(receipt)


def test_changed_forecast_timestamp_model_or_evidence_is_not_equivalent() -> None:
    original = make_receipt()
    assert make_receipt(calibrated_probability=Decimal(".59")).receipt_id != original.receipt_id
    changed_time = make_receipt(
        issued_at=NOW + timedelta(minutes=1),
        created_at=NOW + timedelta(minutes=1),
        target_resolution_time=NOW + timedelta(hours=24, minutes=1),
    )
    assert changed_time.receipt_id != original.receipt_id
    assert make_receipt(model_version="model-cpi-2").receipt_id != original.receipt_id
    assert make_receipt(feature_snapshot_id="different-feature").receipt_id != original.receipt_id
    assert make_receipt(evidence_bundle_id="different-evidence").receipt_id != original.receipt_id
    assert make_receipt(market_ticker="KXCPI-26SEP-T4.0").receipt_id != original.receipt_id


def test_historical_replay_cannot_be_relabelled_as_prospective() -> None:
    with pytest.raises(ProspectiveReceiptError, match="historical replay"):
        make_receipt(replay_time=NOW)


def test_outcome_boundary_requires_published_receipt_and_later_outcome(tmp_path) -> None:
    receipt = make_receipt()
    store = ProspectiveReceiptStore(tmp_path)
    with pytest.raises(ProspectiveReceiptError, match="unpublished"):
        store.require_frozen(receipt, NOW + timedelta(days=1))
    store.publish(receipt)
    with pytest.raises(ProspectiveReceiptError, match="trusted publication"):
        store.require_frozen(receipt, receipt.forecast_created_at)
    store.require_frozen(receipt, NOW + timedelta(days=1))


def test_receipt_published_after_known_outcome_cannot_be_called_prospective(
    tmp_path, monkeypatch
) -> None:
    outcome_available_at = NOW + timedelta(days=1)
    monkeypatch.setattr(
        receipt_module, "_utc_now", lambda: outcome_available_at + timedelta(seconds=1)
    )
    receipt = make_receipt(
        issued_at=NOW - timedelta(days=2),
        created_at=NOW - timedelta(days=2),
        target_resolution_time=NOW - timedelta(days=1),
    )
    store = ProspectiveReceiptStore(tmp_path)
    store.publish(receipt)
    with pytest.raises(ProspectiveReceiptError, match="trusted publication"):
        store.require_frozen(receipt, outcome_available_at)


def test_backdated_forecast_cannot_hide_post_outcome_publication(tmp_path, monkeypatch) -> None:
    outcome_available_at = NOW + timedelta(days=1)
    monkeypatch.setattr(
        receipt_module, "_utc_now", lambda: outcome_available_at + timedelta(seconds=1)
    )
    receipt = make_receipt(
        issued_at=NOW - timedelta(days=2),
        created_at=NOW - timedelta(days=2),
        target_resolution_time=NOW - timedelta(days=1),
    )
    store = ProspectiveReceiptStore(tmp_path)
    store.publish(receipt)
    with pytest.raises(ProspectiveReceiptError, match="trusted publication"):
        store.require_frozen(receipt, outcome_available_at)


def test_caller_cannot_supply_publication_timestamp(tmp_path) -> None:
    receipt = make_receipt()
    store = ProspectiveReceiptStore(tmp_path)
    with pytest.raises(TypeError):
        store.publish(receipt, published_at=NOW)  # type: ignore[call-arg]


def test_production_influence_and_foreign_runtime_types_are_rejected() -> None:
    with pytest.raises(ForecastError):
        make_receipt(production_influence=Decimal(".1"))
    with pytest.raises(ProspectiveReceiptError, match="canonical Forecast type"):
        ProspectivePredictionReceipt.from_forecast(
            type("ForeignForecast", (), {})(),
            candidate_id="candidate",
            calibrator_id="calibrator",
            feature_schema_id="schema",
            code_identity="code",
            event_ticker="event",
            underlying_event_id="underlying",
            evidence_ids=("evidence",),
            source_ids=("source",),
            market_snapshot_id=None,
        )


def test_receipt_package_has_no_execution_or_capital_authority() -> None:
    source = "\n".join(path.read_text() for path in Path("services/forward_reality").glob("*.py"))
    assert "production_execution" not in source
    assert "risk_engine" not in source
    assert "submit_order" not in source


def test_sibling_markets_share_one_underlying_dependency_identity() -> None:
    first = make_receipt()
    second = ProspectivePredictionReceipt.from_forecast(
        make_forecast(market_ticker="KXCPI-26SEP-T4.0"),
        candidate_id="candidate-cpi-1",
        calibrator_id="calibrator-cpi-1",
        feature_schema_id="feature-schema-v1",
        code_identity="code-sha",
        event_ticker="KXCPI-26SEP",
        underlying_event_id=first.underlying_event_id,
        evidence_ids=("evidence-1",),
        source_ids=("source-1",),
        market_snapshot_id="market-snapshot-2",
    )
    assert first.underlying_event_id == second.underlying_event_id
    assert first.receipt_id != second.receipt_id
