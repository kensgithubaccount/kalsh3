"""Offline M28C/C.1 reintegration tests over canonical M28B and NOAA contracts."""

from __future__ import annotations

import json
import sys
from copy import copy
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

import services.production_weather_strategy.climate_evidence as climate_module
from scripts import run_m28c_public_weather_tournament as runner
from services.forecasting.weather_calibration import parse_ghcnd_daily
from services.forecasting.weather_source_authority import PHYSICAL_WEATHER_SOURCES
from services.historical_replay.archive import stable_hash
from services.historical_replay.client import HistoricalClient
from services.production_weather_strategy.climate_evidence import (
    ClimateEvidenceClassification,
    ClimateFeatureEvidence,
    ClimateHistory,
    ClimateSourceArtifact,
    HistoricalClimateVintageEvidence,
    build_climate_feature_evidence,
    build_ghcnd_climate_observations,
    build_point_in_time_climate_feature_evidence,
)
from services.production_weather_strategy.contracts import ModelState
from services.production_weather_strategy.model_tournament import (
    EDGE_THRESHOLD,
    HYPOTHETICAL_PNL_CLASSIFICATION,
    MARKET_CANDLE_INTERVAL_MINUTES,
    MarketCheckpoint,
    ModelTournamentError,
    TournamentFeatureDataset,
    TournamentModel,
    TournamentPartition,
    _event_weighted_brier,
    _event_weighted_log_loss,
    build_development_model_artifact,
    build_feature_dataset,
    build_training_manifest,
    derive_temporal_split,
    run_model_tournament,
)
from services.production_weather_strategy.settlement_dataset import (
    _PAGE_EVIDENCE_CAPABILITY,
    SETTLEMENT_MAPPING_ID,
    AcquisitionBoundMarketRow,
    PublicPageEvidence,
    WeatherSettlementDataset,
    build_evidence_bound_weather_dataset,
    build_weather_settlement_dataset,
)

ROOT = Path(__file__).parent / "fixtures" / "m27c"
BASE_GHCND = (ROOT / "USW00014819-201806.dly").read_bytes()
ACQUIRED_AT = datetime(2030, 1, 1, tzinfo=UTC)
TARGET_DATES = tuple(date(2024, 6, 10) + timedelta(days=index) for index in range(6))
STATIONS = (
    ("CLIAUS", "Austin", "KXHIGHAUS"),
    ("CLIMDW", "Chicago", "KXHIGHCHI"),
)


def _rule(
    *,
    location: str,
    identifier: str,
    target: date,
    comparator: str,
) -> tuple[str, str, object, object]:
    date_text = target.strftime("%b %d, %Y").replace(" 0", " ")
    if comparator == "RANGE":
        phrase = "between 70-80"
        return (
            f"If the maximum temperature recorded at {location}({identifier}) for "
            f"{date_text}, is {phrase}° fahrenheit according to The Weather Company, then "
            "the market resolves to Yes.",
            "between",
            70,
            80,
        )
    if comparator == "GT":
        return (
            f"If the maximum temperature recorded at {location}({identifier}) for "
            f"{date_text}, is greater than 80° fahrenheit according to The Weather Company, "
            "then the market resolves to Yes.",
            "greater",
            80,
            None,
        )
    raise AssertionError("unsupported test comparator")


def _event_rows(
    *,
    station: str,
    location: str,
    series: str,
    target: date,
    range_yes: bool,
) -> list[dict[str, object]]:
    event = f"{series}-{target:%y%b%d}".upper()
    settlement = datetime.combine(target + timedelta(days=1), time(12), tzinfo=UTC)
    result_rows: list[dict[str, object]] = []
    for suffix, comparator, realized in (
        ("B70.5", "RANGE", range_yes),
        ("GT80", "GT", not range_yes),
    ):
        rule, strike_type, floor, cap = _rule(
            location=location,
            identifier=station,
            target=target,
            comparator=comparator,
        )
        result_rows.append(
            {
                "ticker": f"{event}-{suffix}",
                "event_ticker": event,
                "market_type": "binary",
                "status": "settled",
                "result": "yes" if realized else "no",
                "settlement_value_dollars": "1.0000" if realized else "0.0000",
                "settlement_ts": settlement.isoformat().replace("+00:00", "Z"),
                "rules_primary": rule,
                "rules_secondary": "Official value follows the named rule source.",
                "strike_type": strike_type,
                "floor_strike": floor,
                "cap_strike": cap,
            }
        )
    return result_rows


def _rows_by_series() -> dict[str, list[dict[str, object]]]:
    output: dict[str, list[dict[str, object]]] = {}
    for station, location, series in STATIONS:
        rows: list[dict[str, object]] = []
        for index, target in enumerate(TARGET_DATES):
            rows.extend(
                _event_rows(
                    station=station,
                    location=location,
                    series=series,
                    target=target,
                    range_yes=index % 2 == 0,
                )
            )
        output[series] = rows
    return output


def _page(rows: list[dict[str, object]], series: str, salt: str) -> PublicPageEvidence:
    path = f"/trade-api/v2/historical/markets?limit=1000&series_ticker={series}"
    body = json.dumps({"markets": rows, "cursor": ""}, sort_keys=True).encode()
    return PublicPageEvidence(
        request_path=path,
        response_sha256=stable_hash((salt, body.hex())),
        page_number=1,
        scope_series_ticker=series,
        market_row_hashes=tuple(stable_hash(row) for row in rows),
        _capability=_PAGE_EVIDENCE_CAPABILITY,
    )


def _settlement_fixture() -> tuple[tuple[WeatherSettlementDataset, ...], Any]:
    by_series = _rows_by_series()
    semantic_events = []
    for rows in by_series.values():
        semantic_events.extend(build_weather_settlement_dataset(rows).events)
    split = derive_temporal_split(semantic_events)
    datasets: list[WeatherSettlementDataset] = []
    for series, rows in sorted(by_series.items()):
        page = _page(rows, series, salt=series)
        bound = tuple(AcquisitionBoundMarketRow.from_page(row, page) for row in rows)
        datasets.append(build_evidence_bound_weather_dataset(bound, temporal_split=split))
    return tuple(datasets), split


def _rewrite_ghcnd(raw: bytes, *, ghcnd_station_id: str, year: int) -> bytes:
    lines: list[bytes] = []
    for line in raw.splitlines(keepends=True):
        newline = b"\n" if line.endswith(b"\n") else b""
        content = line.rstrip(b"\r\n")
        if len(content) < 21:
            continue
        content = ghcnd_station_id.encode("ascii") + f"{year:04d}".encode("ascii") + content[15:]
        lines.append(content + newline)
    return b"".join(lines)


def _climate_history(station: str, *, strict: bool, proof_suffix: str = "base") -> ClimateHistory:
    source = PHYSICAL_WEATHER_SOURCES[station]
    artifacts: list[ClimateSourceArtifact] = []
    observations = []
    for year in range(2014, 2024):
        raw = _rewrite_ghcnd(BASE_GHCND, ghcnd_station_id=source.ghcnd_station_id, year=year)
        snapshot = parse_ghcnd_daily(raw, source, ACQUIRED_AT)
        plain = ClimateSourceArtifact(
            provider="NOAA/NCEI",
            source_identity=(
                "ghcnd-daily-synthetic-fixture"
                if strict
                else f"ghcnd-daily-synthetic-fixture-replay-{year}"
            ),
            station_id=source.ghcnd_station_id,
            raw_artifact=raw,
            acquired_at=ACQUIRED_AT,
            parser_version="parse_ghcnd_daily",
        )
        vintage = None
        if strict:
            vintage = HistoricalClimateVintageEvidence(
                _capability=climate_module._CLIMATE_AUTHORITY_CAPABILITY,
                provider=plain.provider,
                source_identity=plain.source_identity,
                station_id=plain.station_id,
                capture_id=plain._capture_id,
                source_vintage_at=datetime(year, 12, 31, 23, 59, tzinfo=UTC),
                evidence_id=f"synthetic-{proof_suffix}-{station}-{year}",
            )
        artifact = ClimateSourceArtifact._from_reviewed_ghcnd(
            provider=plain.provider,
            source_identity=plain.source_identity,
            station_id=plain.station_id,
            raw_artifact=raw,
            acquired_at=ACQUIRED_AT,
            parser_version="parse_ghcnd_daily",
            snapshot=snapshot,
            vintage_evidence=vintage,
            _capability=climate_module._CLIMATE_AUTHORITY_CAPABILITY,
        )
        artifacts.append(artifact)
        observations.extend(
            build_ghcnd_climate_observations(source_artifact=artifact, snapshot=snapshot)
        )
    return ClimateHistory.build(
        station_id=source.ghcnd_station_id,
        observations=tuple(observations),
        source_artifacts=tuple(artifacts),
    )


def _strict_climate_features(
    datasets: tuple[WeatherSettlementDataset, ...], *, proof_suffix: str = "base"
) -> dict[str, ClimateFeatureEvidence]:
    histories = {
        station: _climate_history(station, strict=True, proof_suffix=proof_suffix)
        for station, _, _ in STATIONS
    }
    result: dict[str, ClimateFeatureEvidence] = {}
    for dataset in datasets:
        for event in dataset.events:
            history = histories[event.station_id]
            source_station = PHYSICAL_WEATHER_SOURCES[event.station_id].ghcnd_station_id
            result[event.event_id] = build_point_in_time_climate_feature_evidence(
                station_id=source_station,
                measurement=event.measurement,
                target_local_date=event.local_date,
                decision_cutoff_at=datetime.combine(event.local_date, time(3), tzinfo=UTC),
                history=history,
            )
    return result


def _checkpoint_for_contract(
    contract: Any, *, probability: Decimal | None = None
) -> MarketCheckpoint:
    cutoff = datetime.combine(contract.local_date, time(3), tzinfo=UTC)
    cutoff_ts = int(cutoff.timestamp())
    p = probability if probability is not None else Decimal("0.45")
    candle = {
        "end_period_ts": cutoff_ts - 60,
        "yes_bid": {"close_dollars": str(max(Decimal("0"), p - Decimal("0.02")))},
        "yes_ask": {"close_dollars": str(min(Decimal("1"), p + Decimal("0.02")))},
        "price": {"close_dollars": str(p)},
    }
    checkpoint = MarketCheckpoint.from_candles(
        market_ticker=contract.market_ticker,
        checkpoint_at=cutoff,
        candles=(candle,),
        response_evidence_id=f"fixture-response-{contract.market_ticker}",
    )
    assert checkpoint is not None
    return checkpoint


def _checkpoints(datasets: tuple[WeatherSettlementDataset, ...]) -> dict[str, MarketCheckpoint]:
    output: dict[str, MarketCheckpoint] = {}
    for dataset in datasets:
        for index, contract in enumerate(dataset.contracts):
            probability = Decimal("0.35") if index % 3 else Decimal("0.65")
            output[contract.market_ticker] = _checkpoint_for_contract(
                contract, probability=probability
            )
    return output


def _features() -> tuple[TournamentFeatureDataset, tuple[WeatherSettlementDataset, ...], Any]:
    datasets, split = _settlement_fixture()
    features = build_feature_dataset(
        datasets,
        temporal_split=split,
        market_checkpoints=_checkpoints(datasets),
        climate_features=_strict_climate_features(datasets),
    )
    return features, datasets, split


def test_canonical_feature_join_requires_evidence_bound_m28b_and_retains_lineage() -> None:
    features, datasets, split = _features()
    assert features.settlement_mapping_id == SETTLEMENT_MAPPING_ID
    assert features.temporal_split_hash == split.content_hash
    assert features.settlement_dataset_ids == tuple(sorted(item.dataset_id for item in datasets))
    assert features.source_settlement_label_manifest_ids == tuple(
        sorted(item.settlement_labels.manifest_id for item in datasets if item.settlement_labels)
    )
    source_label_ids = {
        label.content_hash
        for dataset in datasets
        for label in dataset.settlement_labels.labels  # type: ignore[union-attr]
    }
    assert {label.content_hash for label in features.settlement_labels.labels} == source_label_ids
    source_event_ids = {event.event_id for dataset in datasets for event in dataset.events}
    assert {row.event_id for row in features.rows} == source_event_ids
    assert all(row.climate_sample_count >= 30 for row in features.rows)


def test_semantic_replay_only_m28b_dataset_is_rejected() -> None:
    datasets, split = _settlement_fixture()
    semantic = build_weather_settlement_dataset(
        _rows_by_series()["KXHIGHAUS"], temporal_split=split
    )
    with pytest.raises(ModelTournamentError, match="evidence-bound"):
        build_feature_dataset(
            (semantic,),
            temporal_split=split,
            market_checkpoints=_checkpoints(datasets),
            climate_features=_strict_climate_features(datasets),
        )


def test_strict_climate_evidence_is_required_and_semantics_must_match() -> None:
    datasets, split = _settlement_fixture()
    strict = _strict_climate_features(datasets)
    checkpoints = _checkpoints(datasets)
    event = datasets[0].events[0]
    source_station = PHYSICAL_WEATHER_SOURCES[event.station_id].ghcnd_station_id
    replay_history = _climate_history(event.event_id if False else event.station_id, strict=False)
    replay = build_climate_feature_evidence(
        station_id=source_station,
        measurement=event.measurement,
        target_local_date=event.local_date,
        decision_cutoff_at=datetime.combine(event.local_date, time(3), tzinfo=UTC),
        history=replay_history,
    )
    assert replay.classification is ClimateEvidenceClassification.REPLAY_ONLY
    with pytest.raises(ModelTournamentError, match="replay-only"):
        build_feature_dataset(
            datasets,
            temporal_split=split,
            market_checkpoints=checkpoints,
            climate_features={**strict, event.event_id: replay},
        )

    other = next(
        value
        for value in strict.values()
        if value.station_id != strict[event.event_id].station_id
    )
    with pytest.raises(ModelTournamentError, match="station"):
        build_feature_dataset(
            datasets,
            temporal_split=split,
            market_checkpoints=checkpoints,
            climate_features={**strict, event.event_id: other},
        )

    wrong_measurement = copy(strict[event.event_id])
    object.__setattr__(wrong_measurement, "measurement", "DAILY_MIN")
    with pytest.raises(ModelTournamentError, match="measurement"):
        build_feature_dataset(
            datasets,
            temporal_split=split,
            market_checkpoints=checkpoints,
            climate_features={**strict, event.event_id: wrong_measurement},
        )

    wrong_cutoff = copy(strict[event.event_id])
    object.__setattr__(
        wrong_cutoff,
        "decision_cutoff_at",
        datetime.combine(event.local_date, time(4), tzinfo=UTC),
    )
    with pytest.raises(ModelTournamentError, match="cutoff"):
        build_feature_dataset(
            datasets,
            temporal_split=split,
            market_checkpoints=checkpoints,
            climate_features={**strict, event.event_id: wrong_cutoff},
        )


def test_target_year_noaa_observations_never_enter_used_feature_subset() -> None:
    datasets, split = _settlement_fixture()
    features = _strict_climate_features(datasets)
    assert features
    for event_id, evidence in features.items():
        assert event_id
        assert all(
            row.local_date.year < evidence.target_local_date.year
            for row in evidence.used_observations
        )

    event = datasets[0].events[0]
    corrupted_observation = copy(features[event.event_id].used_observations[0])
    object.__setattr__(corrupted_observation, "local_date", event.local_date)
    corrupted_feature = copy(features[event.event_id])
    object.__setattr__(
        corrupted_feature,
        "used_observations",
        (*corrupted_feature.used_observations, corrupted_observation),
    )
    with pytest.raises(ModelTournamentError, match="prior-calendar-year"):
        build_feature_dataset(
            datasets,
            temporal_split=split,
            market_checkpoints=_checkpoints(datasets),
            climate_features={**features, event.event_id: corrupted_feature},
        )


def test_used_climate_evidence_changes_dataset_identity_but_unused_mapping_does_not() -> None:
    datasets, split = _settlement_fixture()
    checkpoints = _checkpoints(datasets)
    first_climate = _strict_climate_features(datasets, proof_suffix="one")
    second_climate = _strict_climate_features(datasets, proof_suffix="two")
    first = build_feature_dataset(
        datasets,
        temporal_split=split,
        market_checkpoints=checkpoints,
        climate_features=first_climate,
    )
    event_id = next(iter(first_climate))
    changed = build_feature_dataset(
        datasets,
        temporal_split=split,
        market_checkpoints=checkpoints,
        climate_features={**first_climate, event_id: second_climate[event_id]},
    )
    assert first.dataset_id != changed.dataset_id
    unused = build_feature_dataset(
        datasets,
        temporal_split=split,
        market_checkpoints={**checkpoints, "UNUSED": next(iter(checkpoints.values()))},
        climate_features={**first_climate, "UNUSED": next(iter(first_climate.values()))},
    )
    assert first.dataset_id == unused.dataset_id


def test_market_checkpoint_is_exact_03z_bounded_and_no_lookahead() -> None:
    datasets, _ = _settlement_fixture()
    contract = datasets[0].contracts[0]
    cutoff = datetime.combine(contract.local_date, time(3), tzinfo=UTC)
    cutoff_ts = int(cutoff.timestamp())
    candles = (
        {"end_period_ts": cutoff_ts - 3600, "price": {"close_dollars": "0.40"}},
        {"end_period_ts": cutoff_ts, "price": {"close_dollars": "0.55"}},
        {"end_period_ts": cutoff_ts + 60, "price": {"close_dollars": "0.99"}},
    )
    checkpoint = MarketCheckpoint.from_candles(
        market_ticker=contract.market_ticker,
        checkpoint_at=cutoff,
        candles=candles,
        response_evidence_id="response-one",
    )
    assert checkpoint is not None
    assert checkpoint.checkpoint_at == cutoff
    assert checkpoint.selected_candle_end_ts == cutoff_ts
    assert checkpoint.yes_probability == Decimal("0.55")
    assert checkpoint.request_end_ts == cutoff_ts
    assert checkpoint.request_start_ts == cutoff_ts - 24 * 60 * 60
    assert f"period_interval={MARKET_CANDLE_INTERVAL_MINUTES}" in checkpoint.request_path
    assert MarketCheckpoint.from_candles(
        market_ticker=contract.market_ticker,
        checkpoint_at=cutoff,
        candles=({"end_period_ts": cutoff_ts + 1, "price": {"close_dollars": "0.9"}},),
        response_evidence_id="future-only",
    ) is None
    with pytest.raises(ModelTournamentError, match="03Z"):
        MarketCheckpoint.from_candles(
            market_ticker=contract.market_ticker,
            checkpoint_at=cutoff + timedelta(hours=1),
            candles=candles,
            response_evidence_id="wrong-hour",
        )


def test_market_checkpoint_fallbacks_malformed_rows_and_identity_are_deterministic() -> None:
    datasets, _ = _settlement_fixture()
    contract = datasets[0].contracts[0]
    cutoff = datetime.combine(contract.local_date, time(3), tzinfo=UTC)
    end_ts = int(cutoff.timestamp()) - 60
    midpoint = {
        "end_period_ts": end_ts,
        "yes_bid": {"close_dollars": "0.40"},
        "yes_ask": {"close_dollars": "0.44"},
        "price": {"close_dollars": "0.90"},
    }
    first = MarketCheckpoint.from_candles(
        market_ticker=contract.market_ticker,
        checkpoint_at=cutoff,
        candles=(midpoint,),
        response_evidence_id="response-one",
    )
    assert first is not None and first.yes_probability == Decimal("0.42")
    second = MarketCheckpoint.from_candles(
        market_ticker=contract.market_ticker,
        checkpoint_at=cutoff,
        candles=({**midpoint, "yes_ask": {"close_dollars": "0.46"}},),
        response_evidence_id="response-one",
    )
    assert second is not None and first.checkpoint_id != second.checkpoint_id
    third = MarketCheckpoint.from_candles(
        market_ticker=contract.market_ticker,
        checkpoint_at=cutoff,
        candles=(midpoint,),
        response_evidence_id="response-two",
    )
    assert third is not None and first.checkpoint_id != third.checkpoint_id
    with pytest.raises(ModelTournamentError, match="malformed"):
        MarketCheckpoint.from_candles(
            market_ticker=contract.market_ticker,
            checkpoint_at=cutoff,
            candles=({"end_period_ts": "bad"},),
            response_evidence_id="bad",
        )
    with pytest.raises(ModelTournamentError, match="derived"):
        MarketCheckpoint()


def test_current_historical_client_candle_interface_is_used_offline() -> None:
    datasets, _ = _settlement_fixture()
    contract = datasets[0].contracts[0]
    cutoff = datetime.combine(contract.local_date, time(3), tzinfo=UTC)
    cutoff_ts = int(cutoff.timestamp())

    class FakeTransport:
        def __init__(self) -> None:
            self.requests: list[dict[str, object]] = []

        def get(
            self, path: str, headers: Any, *, timeout_seconds: float
        ) -> tuple[int, dict[str, Any]]:
            assert headers == {}
            assert timeout_seconds > 0
            self.requests.append({"path": path, "sha256": "a" * 64})
            return 200, {
                "candlesticks": [
                    {
                        "end_period_ts": cutoff_ts - 60,
                        "price": {"close_dollars": "0.50"},
                    }
                ]
            }

    transport = FakeTransport()
    client = HistoricalClient(transport, signer=None, timeout=1)
    checkpoint = runner._market_checkpoint(client, transport, contract, cutoff)
    assert checkpoint is not None and checkpoint.yes_probability == Decimal("0.50")
    assert transport.requests[0]["path"] == checkpoint.request_path


def test_temporal_split_is_single_authority_and_siblings_never_cross_partitions() -> None:
    features, datasets, split = _features()
    assert all(dataset.temporal_split_hash == split.content_hash for dataset in datasets)
    partition_by_event: dict[str, TournamentPartition] = {}
    for row in features.rows:
        previous = partition_by_event.setdefault(row.event_id, row.partition)
        assert previous is row.partition
    corrupted = replace(
        datasets[0],
        validation_event_ids=(
            datasets[0].train_event_ids[0],
            *datasets[0].validation_event_ids,
        ),
    )
    with pytest.raises(ModelTournamentError, match="overlap"):
        build_feature_dataset(
            (corrupted, *datasets[1:]),
            temporal_split=split,
            market_checkpoints=_checkpoints(datasets),
            climate_features=_strict_climate_features(datasets),
        )


def test_exact_model_set_and_fitting_discipline_are_deterministic() -> None:
    features, _, _ = _features()
    assert tuple(TournamentModel) == (
        TournamentModel.MARKET,
        TournamentModel.NOAA_CLIMATOLOGY,
        TournamentModel.POOLED_RESIDUAL,
        TournamentModel.CITY_SHRUNK_RESIDUAL,
        TournamentModel.CALIBRATED_ENSEMBLE,
    )
    first = run_model_tournament(features)
    second = run_model_tournament(features)
    assert first == second
    assert first.fit.validation_selected_model is TournamentModel.CALIBRATED_ENSEMBLE
    assert first.selected_test_scorecard.partition is TournamentPartition.TEST
    assert first.promotion_authority == "NONE"
    assert len(first.scorecards) == 15


def _mutate_outcomes(
    dataset: TournamentFeatureDataset, partitions: set[TournamentPartition]
) -> TournamentFeatureDataset:
    rows = tuple(
        replace(row, realized_yes=1 - row.realized_yes)
        if row.partition in partitions
        else row
        for row in dataset.rows
    )
    return replace(dataset, rows=rows)


def test_test_labels_cannot_influence_fit_city_calibration_ensemble_or_threshold() -> None:
    features, _, _ = _features()
    baseline = run_model_tournament(features)
    changed = run_model_tournament(_mutate_outcomes(features, {TournamentPartition.TEST}))
    assert changed.fit == baseline.fit
    assert changed.fit.content_hash == baseline.fit.content_hash
    assert Decimal("0.10") == EDGE_THRESHOLD


def test_validation_and_test_labels_cannot_influence_train_only_fit_or_calibration() -> None:
    features, _, _ = _features()
    baseline = run_model_tournament(features)
    changed = run_model_tournament(
        _mutate_outcomes(
            features,
            {TournamentPartition.VALIDATION, TournamentPartition.TEST},
        )
    )
    assert changed.fit.pooled_alpha == baseline.fit.pooled_alpha
    assert changed.fit.pooled_bias == baseline.fit.pooled_bias
    assert changed.fit.city_biases == baseline.fit.city_biases
    assert changed.fit.calibration_slope == baseline.fit.calibration_slope
    assert changed.fit.calibration_offset == baseline.fit.calibration_offset


def test_sibling_multiplicity_cannot_fake_independent_event_weight() -> None:
    features, _, _ = _features()
    rows = list(row for row in features.rows if row.partition is TournamentPartition.TEST)
    event_ids = sorted({row.event_id for row in rows})
    assert len(event_ids) >= 2
    selected = []
    for event_id in event_ids[:2]:
        selected.append(next(row for row in rows if row.event_id == event_id))
    predictions = {
        selected[0].row_id: Decimal(selected[0].realized_yes),
        selected[1].row_id: Decimal(1 - selected[1].realized_yes),
    }
    base_brier = _event_weighted_brier(selected, predictions)
    base_log = _event_weighted_log_loss(selected, predictions)
    duplicates = list(selected)
    duplicate_predictions = dict(predictions)
    for index in range(20):
        duplicate = replace(selected[0], row_id=f"duplicate-{index}")
        duplicates.append(duplicate)
        duplicate_predictions[duplicate.row_id] = predictions[selected[0].row_id]
    assert _event_weighted_brier(duplicates, duplicate_predictions) == base_brier
    assert _event_weighted_log_loss(duplicates, duplicate_predictions) == base_log


def test_training_manifest_and_development_model_bind_exact_feature_and_label_lineage() -> None:
    features, _, split = _features()
    tournament = run_model_tournament(features)
    created = datetime(2025, 1, 1, tzinfo=UTC)
    training = build_training_manifest(features, temporal_split=split, created_at=created)
    assert training.settlement_labels_id == features.settlement_labels.manifest_id
    assert training.temporal_split_hash == split.content_hash
    assert training.feature_artifact_ids == tuple(sorted(row.content_hash for row in features.rows))
    model = build_development_model_artifact(
        features,
        tournament,
        training,
        trained_at=created + timedelta(hours=1),
    )
    assert model.state is ModelState.DEVELOPMENT
    assert model.training_manifest_id == training.manifest_id
    assert tournament.promotion_authority == "NONE"


def test_scorecards_label_hypothetical_pnl_as_research_only() -> None:
    features, _, _ = _features()
    result = run_model_tournament(features)
    assert all(
        score.hypothetical_pnl_classification == HYPOTHETICAL_PNL_CLASSIFICATION
        for score in result.scorecards
    )


def test_dormant_runner_fails_closed_before_any_public_request(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    output = tmp_path / "m28c.json"
    monkeypatch.setattr(sys, "argv", ["run_m28c", "--output", str(output)])
    assert runner.main() == 21
    captured = capsys.readouterr()
    assert "STRICT_HISTORICAL_NOAA_VINTAGE_EVIDENCE=UNAVAILABLE" in captured.out
    assert "PUBLIC_REQUESTS_EXECUTED=0" in captured.out
    assert not output.exists()
