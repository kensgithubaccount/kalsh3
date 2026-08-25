from __future__ import annotations

import ast
import inspect
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

import services.production_weather_strategy.climate_evidence as climate_module
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
    ClimateSourceVintageStatus,
    build_climate_feature_evidence,
    build_point_in_time_climate_feature_evidence,
    seasonal_distance_days,
)

STATION = "USW00013904"
TARGET = date(2024, 6, 10)
CUTOFF = datetime(2024, 6, 10, 3, tzinfo=UTC)


def artifact(
    *,
    station: str = STATION,
    raw: bytes = b"capture",
    acquired: datetime = datetime(2026, 8, 24, 12, tzinfo=UTC),
    vintage: datetime | None = datetime(2023, 12, 31, 23, tzinfo=UTC),
    vintage_id: str | None = "synthetic-vintage-proof",
    source: str = "synthetic://noaa/ghcn/station.dly",
) -> ClimateSourceArtifact:
    return ClimateSourceArtifact(
        provider="NOAA/NCEI",
        source_identity=source,
        station_id=station,
        raw_artifact=raw,
        acquired_at=acquired,
        parser_version="synthetic-ghcn-v1",
        source_vintage_at=vintage,
        source_vintage_evidence_id=vintage_id,
    )


def obs(
    source: ClimateSourceArtifact,
    *,
    day: date = date(2020, 6, 10),
    measurement: str = "DAILY_MAX",
    temp: str = "72",
    record: bytes = b"row",
) -> ClimateObservation:
    return ClimateObservation(
        station_id=source.station_id,
        measurement=measurement,
        local_date=day,
        temperature_deg_f=Decimal(temp),
        source_artifact=source,
        source_record=record,
    )


def history(
    rows: list[ClimateObservation],
    sources: list[ClimateSourceArtifact],
    *,
    station: str = STATION,
) -> ClimateHistory:
    return ClimateHistory.build(
        station_id=station,
        observations=rows,
        source_artifacts=sources,
    )


def evidence(
    climate_history: ClimateHistory,
    *,
    target: date = TARGET,
    cutoff: datetime = CUTOFF,
    measurement: str = "DAILY_MAX",
) -> ClimateFeatureEvidence:
    return build_climate_feature_evidence(
        station_id=climate_history.station_id,
        measurement=measurement,
        target_local_date=target,
        decision_cutoff_at=cutoff,
        history=climate_history,
    )


def strict(climate_history: ClimateHistory) -> ClimateFeatureEvidence:
    return build_point_in_time_climate_feature_evidence(
        station_id=climate_history.station_id,
        measurement="DAILY_MAX",
        target_local_date=TARGET,
        decision_cutoff_at=CUTOFF,
        history=climate_history,
    )


def test_timezone_aware_timestamps_are_required() -> None:
    with pytest.raises(ClimateEvidenceError, match="acquired_at must be timezone-aware"):
        artifact(acquired=datetime(2026, 8, 24, 12))
    with pytest.raises(ClimateEvidenceError, match="source_vintage_at must be timezone-aware"):
        artifact(vintage=datetime(2023, 12, 31, 23))
    source = artifact()
    climate_history = history([obs(source)], [source])
    with pytest.raises(ClimateEvidenceError, match="decision_cutoff_at must be timezone-aware"):
        evidence(climate_history, cutoff=datetime(2024, 6, 10, 3))


def test_observation_date_is_not_availability_and_current_snapshot_is_replay_only() -> None:
    source = artifact(vintage=None, vintage_id=None)
    row = obs(source, day=date(2020, 6, 10))
    result = evidence(history([row], [source]))
    assert row.local_date < TARGET < source.acquired_at.date()
    assert source.vintage_status is ClimateSourceVintageStatus.UNPROVEN
    assert result.classification is ClimateEvidenceClassification.REPLAY_ONLY
    assert result.replay_reasons == (ClimateReplayReason.UNKNOWN_SOURCE_VINTAGE,)
    with pytest.raises(ClimateEvidenceError, match="UNKNOWN_SOURCE_VINTAGE"):
        strict(history([row], [source]))


def test_synthetic_pre_cutoff_vintage_passes_even_if_capture_is_later() -> None:
    source = artifact(vintage=datetime(2023, 12, 31, tzinfo=UTC))
    result = strict(history([obs(source)], [source]))
    assert source.acquired_at > CUTOFF
    assert source.source_vintage_at is not None and source.source_vintage_at < CUTOFF
    assert result.classification is ClimateEvidenceClassification.HISTORICAL_POINT_IN_TIME


def test_post_cutoff_vintage_is_replay_only_but_equality_is_allowed() -> None:
    late = artifact(vintage=datetime(2025, 1, 1, tzinfo=UTC))
    replay = evidence(history([obs(late)], [late]))
    assert replay.replay_reasons == (ClimateReplayReason.SOURCE_VINTAGE_AFTER_CUTOFF,)
    with pytest.raises(ClimateEvidenceError, match="SOURCE_VINTAGE_AFTER_CUTOFF"):
        strict(history([obs(late)], [late]))

    exact = artifact(vintage=CUTOFF)
    assert strict(history([obs(exact)], [exact])).classification is (
        ClimateEvidenceClassification.HISTORICAL_POINT_IN_TIME
    )


def test_vintage_claim_requires_timestamp_and_evidence_id_and_cannot_postdate_capture() -> None:
    with pytest.raises(ClimateEvidenceError, match="supplied together"):
        artifact(vintage=None, vintage_id="proof")
    with pytest.raises(ClimateEvidenceError, match="supplied together"):
        artifact(vintage=datetime(2023, 1, 1, tzinfo=UTC), vintage_id=None)
    with pytest.raises(ClimateEvidenceError, match="after artifact acquisition"):
        artifact(
            acquired=datetime(2023, 1, 1, tzinfo=UTC),
            vintage=datetime(2023, 1, 2, tzinfo=UTC),
        )


def test_station_measurement_and_temperature_validation() -> None:
    source = artifact(station="A")
    with pytest.raises(ClimateEvidenceError, match="station conflicts"):
        ClimateObservation(
            station_id="B",
            measurement="DAILY_MAX",
            local_date=date(2020, 6, 10),
            temperature_deg_f=Decimal("72"),
            source_artifact=source,
            source_record=b"row",
        )
    with pytest.raises(ClimateEvidenceError, match="measurement is unsupported"):
        obs(source, measurement="TAVG")
    for value in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(ClimateEvidenceError, match="temperature must be finite"):
            obs(source, temp=value)


@pytest.mark.parametrize("measurement", ["DAILY_MAX", "DAILY_MIN"])
def test_daily_max_and_min_are_explicit(measurement: str) -> None:
    source = artifact()
    row = obs(source, measurement=measurement)
    result = evidence(history([row], [source]), measurement=measurement)
    assert result.used_observations == (row,)


@pytest.mark.parametrize("kind", ["identical", "temperature", "provenance"])
def test_duplicate_semantic_key_fails_closed(kind: str) -> None:
    source_a = artifact(source="synthetic://a")
    first = obs(source_a, temp="72", record=b"a")
    sources = [source_a]
    if kind == "identical":
        second = first
    elif kind == "temperature":
        second = obs(source_a, temp="73", record=b"b")
    else:
        source_b = artifact(source="synthetic://b", raw=b"b")
        sources.append(source_b)
        second = obs(source_b, temp="72", record=b"b")
    with pytest.raises(ClimateEvidenceError, match="duplicate climate observation key"):
        history([first, second], sources)


def test_prior_calendar_year_and_ten_year_lookback_are_preserved() -> None:
    source = artifact()
    rows = [
        obs(source, day=date(2013, 6, 10), record=b"2013"),
        obs(source, day=date(2014, 6, 10), record=b"2014"),
        obs(source, day=date(2023, 6, 10), record=b"2023"),
        obs(source, day=date(2024, 6, 10), record=b"2024"),
        obs(source, day=date(2025, 6, 10), record=b"2025"),
    ]
    result = evidence(history(rows, [source]))
    assert CLIMATE_LOOKBACK_YEARS == 10
    assert [row.local_date.year for row in result.used_observations] == [2014, 2023]


def test_seasonal_wrap_cannot_bypass_target_year_gate() -> None:
    source = artifact(vintage=datetime(2024, 1, 1, tzinfo=UTC))
    prior = obs(source, day=date(2023, 12, 31), record=b"prior")
    target_year = obs(source, day=date(2024, 1, 1), record=b"target")
    far = obs(source, day=date(2023, 12, 1), record=b"far")
    result = evidence(
        history([target_year, far, prior], [source]),
        target=date(2024, 1, 5),
        cutoff=datetime(2024, 1, 5, 3, tzinfo=UTC),
    )
    assert CLIMATE_SEASONAL_WINDOW_DAYS == 15
    assert seasonal_distance_days(prior.local_date, date(2024, 1, 5)) == 5
    assert result.used_observations == (prior,)


def test_leap_day_distance_matches_historical_year_2000_anchor() -> None:
    source = artifact()
    leap = obs(source, day=date(2020, 2, 29), record=b"leap")
    march = obs(source, day=date(2023, 3, 1), record=b"march")
    far = obs(source, day=date(2023, 3, 20), record=b"far")
    result = evidence(
        history([far, march, leap], [source]),
        target=date(2024, 2, 29),
        cutoff=datetime(2024, 2, 29, 3, tzinfo=UTC),
    )
    assert seasonal_distance_days(leap.local_date, date(2024, 2, 29)) == 0
    assert seasonal_distance_days(march.local_date, date(2024, 2, 29)) == 1
    assert result.used_observations == (leap, march)


def test_used_temperature_provenance_and_vintage_mutations_change_subset_id() -> None:
    base = artifact(source="synthetic://base", vintage=datetime(2023, 12, 30, tzinfo=UTC))
    temp_change = artifact(source="synthetic://base", vintage=datetime(2023, 12, 30, tzinfo=UTC))
    provenance_change = artifact(
        source="synthetic://other",
        vintage=datetime(2023, 12, 30, tzinfo=UTC),
    )
    vintage_change = artifact(source="synthetic://base", vintage=datetime(2023, 12, 31, tzinfo=UTC))

    ids = {
        evidence(history([obs(base, temp="72")], [base])).feature_evidence_id,
        evidence(history([obs(temp_change, temp="73")], [temp_change])).feature_evidence_id,
        evidence(
            history([obs(provenance_change, temp="72")], [provenance_change])
        ).feature_evidence_id,
        evidence(history([obs(vintage_change, temp="72")], [vintage_change])).feature_evidence_id,
    }
    assert len(ids) == 4


def test_unused_capture_mutation_changes_history_but_not_used_subset_id() -> None:
    source_a = artifact(raw=b"used\nunused=1")
    used_a = obs(source_a, day=date(2020, 6, 10), record=b"used")
    unused_a = obs(source_a, day=date(2020, 9, 1), temp="90", record=b"unused=1")
    history_a = history([unused_a, used_a], [source_a])

    source_b = artifact(raw=b"used\nunused=2")
    used_b = obs(source_b, day=date(2020, 6, 10), record=b"used")
    unused_b = obs(source_b, day=date(2020, 9, 1), temp="91", record=b"unused=2")
    history_b = history([used_b, unused_b], [source_b])

    assert source_a.artifact_id != source_b.artifact_id
    assert source_a.provenance_id == source_b.provenance_id
    assert history_a.history_id != history_b.history_id
    assert used_a.observation_id == used_b.observation_id
    assert evidence(history_a).feature_evidence_id == evidence(history_b).feature_evidence_id


def test_input_order_is_canonical() -> None:
    source = artifact()
    rows = [
        obs(source, day=date(2020, 6, 10), record=b"2020"),
        obs(source, day=date(2021, 6, 11), record=b"2021"),
        obs(source, day=date(2022, 6, 12), record=b"2022"),
    ]
    forward = history(rows, [source])
    reverse = history(list(reversed(rows)), [source])
    assert forward.history_id == reverse.history_id
    assert evidence(forward).feature_evidence_id == evidence(reverse).feature_evidence_id


def test_revision_after_cutoff_cannot_rewrite_pre_cutoff_feature() -> None:
    old = artifact(
        source="synthetic://revision-a",
        raw=b"72",
        vintage=datetime(2023, 12, 31, tzinfo=UTC),
    )
    new = artifact(
        source="synthetic://revision-b",
        raw=b"73",
        vintage=datetime(2025, 1, 1, tzinfo=UTC),
    )
    old_row = obs(old, temp="72", record=b"72")
    new_row = obs(new, temp="73", record=b"73")
    old_history = history([old_row], [old])
    old_id = strict(old_history).feature_evidence_id

    assert (
        evidence(history([new_row], [new])).classification
        is ClimateEvidenceClassification.REPLAY_ONLY
    )
    assert strict(old_history).feature_evidence_id == old_id
    with pytest.raises(ClimateEvidenceError, match="duplicate climate observation key"):
        history([old_row, new_row], [old, new])


def test_derived_ids_hashes_subset_and_classification_are_not_caller_injectable() -> None:
    artifact_args = inspect.signature(ClimateSourceArtifact).parameters
    observation_args = inspect.signature(ClimateObservation).parameters
    history_args = inspect.signature(ClimateHistory).parameters
    feature_args = inspect.signature(ClimateFeatureEvidence).parameters
    for name in ("raw_artifact_sha256", "vintage_status", "provenance_id", "artifact_id"):
        assert name not in artifact_args
    for name in ("source_record_sha256", "source_provenance_id", "observation_id"):
        assert name not in observation_args
    assert "history_id" not in history_args
    for name in ("used_observations", "classification", "feature_evidence_id"):
        assert name not in feature_args


def test_raw_capture_and_exact_record_hashes_are_derived_from_bytes() -> None:
    first = artifact(raw=b"capture-a")
    second = artifact(raw=b"capture-b")
    assert first.raw_artifact_sha256 != second.raw_artifact_sha256
    row_a = obs(first, record=b"record-a")
    row_b = obs(first, record=b"record-b")
    assert row_a.source_record_sha256 != row_b.source_record_sha256
    assert row_a.observation_id != row_b.observation_id


def test_policy_identity_and_exact_station_binding_are_enforced() -> None:
    source = artifact()
    rows = [
        obs(source, day=date(2020, 6, 10), record=b"2020"),
        obs(source, day=date(2023, 6, 20), record=b"2023"),
    ]
    climate_history = history(rows, [source])
    default = evidence(climate_history)
    wider = build_climate_feature_evidence(
        station_id=STATION,
        measurement="DAILY_MAX",
        target_local_date=TARGET,
        decision_cutoff_at=CUTOFF,
        history=climate_history,
        seasonal_window_days=20,
    )
    assert default.feature_evidence_id != wider.feature_evidence_id

    other = artifact(station="OTHER")
    other_history = history([obs(other)], [other], station="OTHER")
    with pytest.raises(ClimateEvidenceError, match="station does not match history"):
        build_climate_feature_evidence(
            station_id=STATION,
            measurement="DAILY_MAX",
            target_local_date=TARGET,
            decision_cutoff_at=CUTOFF,
            history=other_history,
        )


def test_module_has_no_network_client_or_network_import() -> None:
    tree = ast.parse(inspect.getsource(climate_module))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            roots.add(node.module.split(".", 1)[0])
    assert roots.isdisjoint({"requests", "httpx", "urllib", "socket", "aiohttp"})
    assert not any(
        name.endswith(("Client", "Downloader", "Transport")) for name in vars(climate_module)
    )
