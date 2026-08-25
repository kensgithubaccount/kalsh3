from __future__ import annotations

from copy import copy
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

import services.production_weather_strategy.forecast_vintage as forecast_module
from services.production_weather_strategy.forecast_vintage import (
    TARGET_LOCAL_DATE_TIME_SEMANTICS,
    ForecastEvidenceClassification,
    ForecastSourceArtifact,
    ForecastVintageError,
    ForecastVintageEvidence,
    HistoricalForecastPublicationEvidence,
    build_forecast_vintage_evidence,
    build_point_in_time_forecast_vintage_evidence,
)

CUTOFF = datetime(2026, 8, 23, 3, tzinfo=UTC)


def _artifact(
    *,
    raw: bytes = b"synthetic-forecast-v1",
    station: str = "CLIMDW",
    measurement: str = "DAILY_MAX",
    target: date = date(2026, 8, 23),
    reference: datetime = CUTOFF - timedelta(hours=6),
    value: str = "76.0",
) -> ForecastSourceArtifact:
    return ForecastSourceArtifact(
        provider="NOAA/NDFD",
        source_identity="synthetic-ndfd-archive",
        station_id=station,
        measurement=measurement,
        target_local_date=target,
        forecast_reference_time=reference,
        retrieved_at=CUTOFF + timedelta(days=2),
        parser_version="synthetic-parser-v1",
        forecast_deg_f=Decimal(value),
        raw_artifact=raw,
    )


def _proof(
    artifact: ForecastSourceArtifact,
    *,
    published: datetime = CUTOFF - timedelta(hours=5, minutes=30),
    proof_id: str = "reviewed-synthetic-proof",
) -> HistoricalForecastPublicationEvidence:
    return forecast_module._issue_historical_forecast_publication_evidence(
        artifact=artifact,
        source_published_at=published,
        evidence_id=proof_id,
        _capability=forecast_module._FORECAST_VINTAGE_AUTHORITY_CAPABILITY,
    )


def test_direct_forecast_vintage_construction_is_rejected() -> None:
    with pytest.raises(ForecastVintageError, match="must be issued by reviewed builder"):
        ForecastVintageEvidence()


def test_direct_forecast_vintage_construction_cannot_claim_strict_authority() -> None:
    with pytest.raises(TypeError):
        ForecastVintageEvidence(  # type: ignore[call-arg]
            classification=ForecastEvidenceClassification.HISTORICAL_POINT_IN_TIME,
        )


def test_dataclasses_replace_cannot_upgrade_replay_to_strict_authority() -> None:
    replay = build_forecast_vintage_evidence(_artifact(), decision_cutoff=CUTOFF)
    with pytest.raises(TypeError):
        replace(
            replay,
            classification=ForecastEvidenceClassification.HISTORICAL_POINT_IN_TIME,
        )


def test_caller_cannot_inject_forecast_publication_evidence_id() -> None:
    with pytest.raises(TypeError):
        ForecastVintageEvidence(  # type: ignore[call-arg]
            publication_evidence_id="caller-publication-proof",
        )


def test_caller_cannot_inject_forecast_final_identity_fields() -> None:
    with pytest.raises(TypeError):
        ForecastVintageEvidence(  # type: ignore[call-arg]
            evidence_id="caller-evidence",
            content_hash="caller-content",
        )


def test_ordinary_caller_forecast_evidence_remains_replay_only() -> None:
    evidence = build_forecast_vintage_evidence(_artifact(), decision_cutoff=CUTOFF)
    assert evidence.classification is ForecastEvidenceClassification.REPLAY_ONLY
    assert evidence.source_published_at is None
    assert evidence.publication_evidence_id is None


def test_fake_publication_timestamp_cannot_mint_strict_authority() -> None:
    artifact = _artifact()
    with pytest.raises(ForecastVintageError, match="internal reviewed capability"):
        HistoricalForecastPublicationEvidence(
            artifact=artifact,
            source_published_at=CUTOFF - timedelta(hours=1),
            evidence_id="caller-asserted",
        )


def test_arbitrary_source_hash_cannot_mint_strict_authority() -> None:
    artifact = _artifact()
    assert not hasattr(artifact, "trusted")
    assert artifact.raw_artifact_sha256 != "caller-controlled-hash"
    with pytest.raises(ForecastVintageError, match="bound publication proof"):
        build_point_in_time_forecast_vintage_evidence(
            artifact,
            decision_cutoff=CUTOFF,
            publication_evidence="caller-controlled-hash",  # type: ignore[arg-type]
        )


def test_strict_evidence_for_another_source_artifact_cannot_be_reused() -> None:
    first = _artifact(raw=b"artifact-one")
    second = _artifact(raw=b"artifact-two")
    with pytest.raises(ForecastVintageError, match="source artifact"):
        build_point_in_time_forecast_vintage_evidence(
            second,
            decision_cutoff=CUTOFF,
            publication_evidence=_proof(first),
        )


def test_publication_after_cutoff_fails() -> None:
    artifact = _artifact()
    proof = _proof(artifact, published=CUTOFF + timedelta(seconds=1))
    with pytest.raises(ForecastVintageError, match="published after"):
        build_point_in_time_forecast_vintage_evidence(
            artifact,
            decision_cutoff=CUTOFF,
            publication_evidence=proof,
        )


def test_reference_time_after_publication_fails() -> None:
    artifact = _artifact(reference=CUTOFF - timedelta(minutes=10))
    with pytest.raises(ForecastVintageError, match="reference time is after"):
        _proof(artifact, published=CUTOFF - timedelta(minutes=20))


def test_station_mismatch_fails() -> None:
    artifact = _artifact()
    proof = copy(_proof(artifact))
    object.__setattr__(proof, "station_id", "OTHER")
    with pytest.raises(ForecastVintageError, match="station"):
        build_point_in_time_forecast_vintage_evidence(
            artifact,
            decision_cutoff=CUTOFF,
            publication_evidence=proof,
        )


def test_measurement_mismatch_fails() -> None:
    artifact = _artifact()
    proof = copy(_proof(artifact))
    object.__setattr__(proof, "measurement", "DAILY_MIN")
    with pytest.raises(ForecastVintageError, match="measurement"):
        build_point_in_time_forecast_vintage_evidence(
            artifact,
            decision_cutoff=CUTOFF,
            publication_evidence=proof,
        )


def test_target_date_mismatch_fails() -> None:
    artifact = _artifact()
    proof = copy(_proof(artifact))
    object.__setattr__(proof, "target_local_date", date(2026, 8, 24))
    with pytest.raises(ForecastVintageError, match="target date"):
        build_point_in_time_forecast_vintage_evidence(
            artifact,
            decision_cutoff=CUTOFF,
            publication_evidence=proof,
        )


def test_exact_internally_issued_synthetic_historical_evidence_succeeds() -> None:
    artifact = _artifact()
    evidence = build_point_in_time_forecast_vintage_evidence(
        artifact,
        decision_cutoff=CUTOFF,
        publication_evidence=_proof(artifact),
    )
    assert evidence.classification is ForecastEvidenceClassification.HISTORICAL_POINT_IN_TIME
    assert evidence.source_artifact_id == artifact.artifact_id
    assert evidence.publication_evidence_id is not None


def test_used_forecast_evidence_identity_changes_with_exact_bound_source() -> None:
    first = _artifact(raw=b"source-one")
    second = _artifact(raw=b"source-two")
    first_evidence = build_point_in_time_forecast_vintage_evidence(
        first,
        decision_cutoff=CUTOFF,
        publication_evidence=_proof(first, proof_id="one"),
    )
    second_evidence = build_point_in_time_forecast_vintage_evidence(
        second,
        decision_cutoff=CUTOFF,
        publication_evidence=_proof(second, proof_id="two"),
    )
    assert first_evidence.content_hash != second_evidence.content_hash


def test_local_date_horizon_does_not_pretend_local_midnight_is_utc_midnight() -> None:
    evidence = build_forecast_vintage_evidence(_artifact(), decision_cutoff=CUTOFF)
    assert evidence.target_local_date_time_semantics == TARGET_LOCAL_DATE_TIME_SEMANTICS
    assert not hasattr(evidence, "horizon_seconds")
