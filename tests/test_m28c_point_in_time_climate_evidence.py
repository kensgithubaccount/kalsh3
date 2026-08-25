from __future__ import annotations

import inspect
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

import services.production_weather_strategy.climate_evidence as climate_module
from services.forecasting.weather_calibration import GhcndDailySnapshotEvidence, parse_ghcnd_daily
from services.forecasting.weather_source_authority import PHYSICAL_WEATHER_SOURCES
from services.production_weather_strategy.climate_evidence import (
    CLIMATE_LOOKBACK_YEARS,
    CLIMATE_SEASONAL_WINDOW_DAYS,
    ClimateEvidenceClassification,
    ClimateEvidenceError,
    ClimateFeatureEvidence,
    ClimateHistory,
    ClimateObservation,
    ClimateReplayReason,
    ClimateSourceArtifact,
    HistoricalClimateVintageEvidence,
    build_climate_feature_evidence,
    build_ghcnd_climate_observations,
    build_point_in_time_climate_feature_evidence,
    seasonal_distance_days,
)

ROOT = Path(__file__).parent / "fixtures" / "m27c"
RAW = (ROOT / "USW00014819-201806.dly").read_bytes()
SOURCE = PHYSICAL_WEATHER_SOURCES["CLIMDW"]
STATION = "USW00014819"
ACQUIRED = datetime(2026, 8, 24, 12, tzinfo=UTC)
TARGET = date(2024, 6, 10)
CUTOFF = datetime(2024, 6, 10, 3, tzinfo=UTC)


def snapshot(raw: bytes = RAW) -> GhcndDailySnapshotEvidence:
    return parse_ghcnd_daily(raw, SOURCE, ACQUIRED)


def source(*, vintage: datetime | None = None, raw: bytes = RAW) -> ClimateSourceArtifact:
    parsed = snapshot(raw)
    plain = ClimateSourceArtifact(
        provider="NOAA/NCEI",
        source_identity="ghcnd-daily-reviewed",
        station_id=STATION,
        raw_artifact=raw,
        acquired_at=ACQUIRED,
        parser_version="parse_ghcnd_daily",
    )
    proof = None
    if vintage is not None:
        proof = HistoricalClimateVintageEvidence(
            _capability=climate_module._CLIMATE_AUTHORITY_CAPABILITY,
            provider=plain.provider,
            source_identity=plain.source_identity,
            station_id=plain.station_id,
            capture_id=plain._capture_id,
            source_vintage_at=vintage,
            evidence_id="synthetic-reviewed-archive-proof",
        )
    return ClimateSourceArtifact._from_reviewed_ghcnd(
        provider=plain.provider,
        source_identity=plain.source_identity,
        station_id=plain.station_id,
        raw_artifact=raw,
        acquired_at=ACQUIRED,
        parser_version="parse_ghcnd_daily",
        snapshot=parsed,
        vintage_evidence=proof,
        _capability=climate_module._CLIMATE_AUTHORITY_CAPABILITY,
    )


def usable_row(
    source_artifact: ClimateSourceArtifact, *, measurement: str = "DAILY_MAX"
) -> ClimateObservation:
    return next(
        row
        for row in build_ghcnd_climate_observations(
            source_artifact=source_artifact,
            snapshot=snapshot(source_artifact._raw_artifact),
        )
        if row.measurement == measurement and row.local_date == date(2018, 6, 10)
    )


def history(
    rows: Sequence[ClimateObservation],
    artifacts: Sequence[ClimateSourceArtifact],
) -> ClimateHistory:
    return ClimateHistory.build(
        station_id=STATION, observations=tuple(rows), source_artifacts=tuple(artifacts)
    )


def evidence(
    climate_history: ClimateHistory,
    *,
    cutoff: datetime = CUTOFF,
    target: date = TARGET,
) -> ClimateFeatureEvidence:
    return build_climate_feature_evidence(
        station_id=STATION,
        measurement="DAILY_MAX",
        target_local_date=target,
        decision_cutoff_at=cutoff,
        history=climate_history,
    )


def test_direct_vintage_claims_cannot_mint_authority() -> None:
    with pytest.raises(TypeError):
        ClimateSourceArtifact(  # type: ignore[call-arg]
            provider="NOAA",
            source_identity="x",
            station_id=STATION,
            raw_artifact=RAW,
            acquired_at=ACQUIRED,
            parser_version="p",
            source_vintage_at=datetime(2023, 1, 1, tzinfo=UTC),
            source_vintage_evidence_id="caller-invented",
        )
    artifact = source()
    row = usable_row(artifact)
    result = evidence(history([row], [artifact]))
    assert result.classification is ClimateEvidenceClassification.REPLAY_ONLY
    assert result.replay_reasons == (ClimateReplayReason.UNKNOWN_SOURCE_VINTAGE,)


def test_current_snapshot_is_replay_only_even_when_parser_is_valid() -> None:
    artifact = source()
    with pytest.raises(ClimateEvidenceError, match="UNKNOWN_SOURCE_VINTAGE"):
        build_point_in_time_climate_feature_evidence(
            station_id=STATION,
            measurement="DAILY_MAX",
            target_local_date=TARGET,
            decision_cutoff_at=CUTOFF,
            history=history([usable_row(artifact)], [artifact]),
        )


def test_internal_pre_cutoff_vintage_is_strictly_usable() -> None:
    artifact = source(vintage=datetime(2023, 12, 31, tzinfo=UTC))
    row = usable_row(artifact)
    result = build_point_in_time_climate_feature_evidence(
        station_id=STATION,
        measurement="DAILY_MAX",
        target_local_date=TARGET,
        decision_cutoff_at=CUTOFF,
        history=history([row], [artifact]),
    )
    assert result.classification is ClimateEvidenceClassification.HISTORICAL_POINT_IN_TIME
    assert row.semantic_authority and row.record_slot_id


def test_post_cutoff_vintage_is_replay_only() -> None:
    artifact = source(vintage=datetime(2025, 1, 1, tzinfo=UTC))
    assert evidence(history([usable_row(artifact)], [artifact])).replay_reasons == (
        ClimateReplayReason.SOURCE_VINTAGE_AFTER_CUTOFF,
    )


def test_arbitrary_record_and_semantics_are_never_strict_authority() -> None:
    artifact = source()
    fake = ClimateObservation(
        station_id=STATION,
        measurement="DAILY_MAX",
        local_date=date(2018, 6, 10),
        temperature_deg_f=Decimal("72"),
        source_artifact=artifact,
        source_record=b"not present in the GHCN capture",
    )
    assert not fake.semantic_authority
    assert (
        ClimateReplayReason.UNVALIDATED_SOURCE_RECORD
        in evidence(history([fake], [artifact])).replay_reasons
    )


def test_forged_semantics_remain_replay_only_even_with_valid_vintage() -> None:
    artifact = source(vintage=datetime(2023, 12, 31, tzinfo=UTC))
    fake = ClimateObservation(
        station_id=STATION,
        measurement="DAILY_MAX",
        local_date=date(2018, 6, 10),
        temperature_deg_f=Decimal("999"),
        source_artifact=artifact,
        source_record=RAW.splitlines()[0],
    )
    result = evidence(history([fake], [artifact]))
    assert result.classification is ClimateEvidenceClassification.REPLAY_ONLY
    assert ClimateReplayReason.UNVALIDATED_SOURCE_RECORD in result.replay_reasons


def test_authoritative_observation_requires_internal_capability() -> None:
    with pytest.raises(ClimateEvidenceError):
        ClimateObservation._from_ghcnd_observation(
            source_artifact=source(), parsed=snapshot().observations[0]
        )


def test_vintage_proof_is_bound_to_exact_capture_and_station() -> None:
    artifact = source()
    proof = HistoricalClimateVintageEvidence(
        _capability=climate_module._CLIMATE_AUTHORITY_CAPABILITY,
        provider=artifact.provider,
        source_identity=artifact.source_identity,
        station_id=artifact.station_id,
        capture_id=artifact._capture_id,
        source_vintage_at=datetime(2023, 1, 1, tzinfo=UTC),
        evidence_id="proof",
    )
    with pytest.raises(ClimateEvidenceError, match="does not bind"):
        ClimateSourceArtifact._from_reviewed_ghcnd(
            provider=artifact.provider,
            source_identity=artifact.source_identity,
            station_id=artifact.station_id,
            raw_artifact=RAW + b"x",
            acquired_at=ACQUIRED,
            parser_version="parse_ghcnd_daily",
            snapshot=snapshot(),
            vintage_evidence=proof,
            _capability=climate_module._CLIMATE_AUTHORITY_CAPABILITY,
        )
    other = HistoricalClimateVintageEvidence(
        _capability=climate_module._CLIMATE_AUTHORITY_CAPABILITY,
        provider=artifact.provider,
        source_identity=artifact.source_identity,
        station_id="OTHER",
        capture_id=artifact._capture_id,
        source_vintage_at=datetime(2023, 1, 1, tzinfo=UTC),
        evidence_id="proof",
    )
    assert proof.content_hash != other.content_hash


def test_parser_values_and_exact_record_slot_are_derived() -> None:
    artifact = source()
    parsed = snapshot()
    row = usable_row(artifact)
    expected = next(
        item
        for item in parsed.observations
        if item.measurement.value == "DAILY_MAX" and item.local_date == row.local_date
    )
    assert row.temperature_deg_f == expected.observed_deg_f
    assert row.source_record_sha256 and row.record_slot_id


def test_daily_min_and_unusable_rows_follow_parser_semantics() -> None:
    minimum_raw = RAW.replace(b"TMAX", b"TMIN", 1)
    artifact = source(raw=minimum_raw)
    rows = build_ghcnd_climate_observations(
        source_artifact=artifact, snapshot=snapshot(minimum_raw)
    )
    assert all(row.measurement == "DAILY_MIN" for row in rows)
    assert all(row.semantic_authority for row in rows)
    assert all(row.temperature_deg_f.is_finite() for row in rows)


def test_record_absence_and_altered_slot_fail_closed() -> None:
    artifact = source()
    parsed = next(
        item
        for item in snapshot().observations
        if item.measurement.value == "DAILY_MAX" and item.usable
    )
    altered = bytearray(RAW)
    element_start = altered.find(b"TMAX")
    assert element_start >= 0
    line_start = element_start - 17
    slot_start = line_start + 21 + (parsed.local_date.day - 1) * 8
    altered[slot_start : slot_start + 5] = b"99999"
    altered_parsed = replace(parsed, raw_tenths_c=99999)
    with pytest.raises(ClimateEvidenceError):
        ClimateObservation._from_ghcnd_observation(
            source_artifact=artifact,
            parsed=altered_parsed,
            _capability=climate_module._CLIMATE_AUTHORITY_CAPABILITY,
        )


def test_used_record_mutation_changes_identity_but_unused_capture_does_not() -> None:
    base = source()
    base_row = usable_row(base)
    changed_source = source(raw=RAW + b"UNRELATED-CAPTURE\n")
    changed_row = usable_row(changed_source)
    assert base_row.observation_id == changed_row.observation_id
    assert base_row.record_slot_id == changed_row.record_slot_id
    assert (
        evidence(history([base_row], [base])).feature_evidence_id
        == evidence(history([changed_row], [changed_source])).feature_evidence_id
    )

    used_changed = bytearray(RAW)
    element_start = used_changed.find(b"TMAX")
    assert element_start >= 0
    line_start = element_start - 17
    used_slot_start = line_start + 21 + (10 - 1) * 8
    used_changed[used_slot_start : used_slot_start + 5] = b" 9999"
    used_source = source(raw=bytes(used_changed))
    used_row = usable_row(used_source)
    assert base_row.observation_id != used_row.observation_id


def test_duplicate_semantic_key_fails_closed() -> None:
    artifact = source()
    row = usable_row(artifact)
    with pytest.raises(ClimateEvidenceError, match="duplicate climate observation key"):
        history([row, row], [artifact])


def test_policy_and_identity_rules_remain_stable() -> None:
    assert CLIMATE_LOOKBACK_YEARS == 10
    assert CLIMATE_SEASONAL_WINDOW_DAYS == 15
    assert seasonal_distance_days(date(2020, 2, 29), date(2024, 2, 29)) == 0
    assert seasonal_distance_days(date(2023, 3, 1), date(2024, 2, 29)) == 1
    assert seasonal_distance_days(date(2023, 3, 20), date(2024, 2, 29)) > 15
    artifact = source()
    row = usable_row(artifact)
    first = evidence(history([row], [artifact]))
    second = evidence(history([row], [artifact]), cutoff=datetime(2024, 6, 10, 4, tzinfo=UTC))
    assert first.feature_evidence_id != second.feature_evidence_id


def test_derived_ids_are_not_constructor_fields() -> None:
    for cls, names in (
        (
            ClimateSourceArtifact,
            ("raw_artifact_sha256", "vintage_status", "provenance_id", "artifact_id"),
        ),
        (ClimateObservation, ("source_record_sha256", "source_provenance_id", "observation_id")),
    ):
        params = inspect.signature(cls).parameters
        for name in names:
            assert name not in params
