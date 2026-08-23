"""Offline tests for M28C leakage-safe weather model tournament."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest

from services.production_weather_strategy.model_tournament import (
    ClimateHistory,
    ClimateObservation,
    MarketCheckpoint,
    ModelTournamentError,
    TournamentModel,
    TournamentPartition,
    build_feature_dataset,
    run_model_tournament,
)
from services.production_weather_strategy.settlement_dataset import (
    AuthoritativeWeatherDataset,
    ResolvedTemperatureContract,
    ResolvedWeatherEvent,
)


def _contract(
    index: int,
    *,
    station: str,
    local_date: date,
    realized_yes: int,
) -> ResolvedTemperatureContract:
    event_ticker = f"KXHIGH{station}-{local_date:%y%b%d}".upper()
    market_ticker = f"{event_ticker}-B70.5"
    contract_id = f"contract-{index}"
    return ResolvedTemperatureContract(
        contract_id=contract_id,
        market_ticker=market_ticker,
        event_ticker=event_ticker,
        series_ticker=f"KXHIGH{station}",
        station_id=station,
        location=station,
        timezone="UTC",
        measurement="DAILY_MAX",
        local_date=local_date,
        comparator="RANGE",
        lower=Decimal("70"),
        upper=Decimal("80"),
        result="yes" if realized_yes else "no",
        realized_yes=realized_yes,
        settlement_value_dollars=Decimal(realized_yes),
        settlement_at=datetime.combine(local_date + timedelta(days=1), time(12), tzinfo=UTC),
        rules_primary="test rule",
        rules_hash=f"rules-{index}",
        source_row_hash=f"row-{index}",
        content_hash=contract_id,
    )


def _dataset() -> AuthoritativeWeatherDataset:
    contracts: list[ResolvedTemperatureContract] = []
    events: list[ResolvedWeatherEvent] = []
    start = date(2025, 1, 1)
    for index in range(10):
        local_date = start + timedelta(days=index)
        station = "CLIAAA" if index % 2 == 0 else "CLIBBB"
        realized_yes = 1 if index in {1, 2, 4, 5, 7, 8} else 0
        contract = _contract(
            index, station=station, local_date=local_date, realized_yes=realized_yes
        )
        contracts.append(contract)
        event_id = f"event-{index}"
        events.append(
            ResolvedWeatherEvent(
                event_id=event_id,
                event_ticker=contract.event_ticker,
                series_ticker=contract.series_ticker,
                station_id=station,
                location=station,
                timezone="UTC",
                measurement="DAILY_MAX",
                local_date=local_date,
                contract_ids=(contract.contract_id,),
                market_tickers=(contract.market_ticker,),
                feasible_witness_deg_f=Decimal("75"),
                content_hash=event_id,
            )
        )
    return AuthoritativeWeatherDataset(
        dataset_id="settlement-dataset",
        parser_version="test",
        label_authority="test",
        event_count=len(events),
        contract_count=len(contracts),
        skipped_unsupported_count=0,
        events=tuple(events),
        contracts=tuple(contracts),
        train_event_ids=(),
        validation_event_ids=(),
        test_event_ids=(),
        temporal_split_hash=None,
        content_hash="settlement-dataset",
    )


def _histories() -> dict[str, ClimateHistory]:
    result: dict[str, ClimateHistory] = {}
    for station in ("CLIAAA", "CLIBBB"):
        observations: list[ClimateObservation] = []
        for year in range(2015, 2025):
            for day_offset in range(-3, 4):
                local_date = date(year, 1, 5) + timedelta(days=day_offset)
                temperature = Decimal("75") if (year + day_offset) % 3 else Decimal("85")
                observations.append(
                    ClimateObservation(
                        station_id=station,
                        measurement="DAILY_MAX",
                        local_date=local_date,
                        temperature_deg_f=temperature,
                        source_evidence_id=f"ghcn-{station}",
                    )
                )
        result[station] = ClimateHistory.build(station, observations)
    return result


def _checkpoints(dataset: AuthoritativeWeatherDataset) -> dict[str, MarketCheckpoint]:
    result: dict[str, MarketCheckpoint] = {}
    for index, contract in enumerate(dataset.contracts):
        probability = Decimal("0.35") if index % 3 else Decimal("0.65")
        result[contract.market_ticker] = MarketCheckpoint(
            market_ticker=contract.market_ticker,
            checkpoint_at=datetime.combine(contract.local_date, time(3), tzinfo=UTC),
            yes_probability=probability,
            evidence_id=f"market-{index}",
        )
    return result


def test_feature_join_is_temporal_and_reaches_all_three_partitions() -> None:
    settlements = _dataset()
    features = build_feature_dataset(
        settlements,
        market_checkpoints=_checkpoints(settlements),
        climate_histories=_histories(),
    )
    assert len(features.rows) == 10
    assert features.unique_event_count == 10
    assert {row.partition for row in features.rows} == set(TournamentPartition)
    assert len(features.train_event_ids) == 6
    assert len(features.validation_event_ids) == 2
    assert len(features.test_event_ids) == 2
    assert all(row.climate_sample_count >= 30 for row in features.rows)


def test_checkpoint_must_be_exact_03z_and_pre_settlement() -> None:
    settlements = _dataset()
    checkpoints = _checkpoints(settlements)
    first = settlements.contracts[0]
    checkpoints[first.market_ticker] = MarketCheckpoint(
        market_ticker=first.market_ticker,
        checkpoint_at=datetime.combine(first.local_date, time(4), tzinfo=UTC),
        yes_probability=Decimal("0.5"),
        evidence_id="wrong-hour",
    )
    with pytest.raises(ModelTournamentError, match="03Z"):
        build_feature_dataset(
            settlements,
            market_checkpoints=checkpoints,
            climate_histories=_histories(),
        )


def test_future_climate_rows_cannot_create_feature_evidence() -> None:
    settlements = _dataset()
    future_only: dict[str, ClimateHistory] = {}
    for station in ("CLIAAA", "CLIBBB"):
        observations = [
            ClimateObservation(
                station_id=station,
                measurement="DAILY_MAX",
                local_date=date(2026, 1, 5) + timedelta(days=index),
                temperature_deg_f=Decimal("75"),
                source_evidence_id=f"future-{station}",
            )
            for index in range(40)
        ]
        future_only[station] = ClimateHistory.build(station, observations)
    with pytest.raises(ModelTournamentError, match="no complete"):
        build_feature_dataset(
            settlements,
            market_checkpoints=_checkpoints(settlements),
            climate_histories=future_only,
        )


def test_tournament_keeps_test_untouched_for_final_scorecard_and_never_promotes() -> None:
    settlements = _dataset()
    features = build_feature_dataset(
        settlements,
        market_checkpoints=_checkpoints(settlements),
        climate_histories=_histories(),
    )
    result = run_model_tournament(features)
    assert result.fit.validation_selected_model is TournamentModel.CALIBRATED_ENSEMBLE
    assert result.selected_test_scorecard.partition is TournamentPartition.TEST
    assert result.selected_test_scorecard.model is TournamentModel.CALIBRATED_ENSEMBLE
    assert result.test_market_scorecard.model is TournamentModel.MARKET
    assert result.promotion_authority == "NONE"
    assert result.test_edge_classification in {
        "BEATS_MARKET_ON_UNTOUCHED_TEST",
        "NO_TEST_EDGE",
    }
    assert len(result.scorecards) == 15


def test_climate_history_is_order_independent_and_content_addressed() -> None:
    rows = [
        ClimateObservation(
            station_id="CLIAAA",
            measurement="DAILY_MAX",
            local_date=date(2020, 1, 1) + timedelta(days=index),
            temperature_deg_f=Decimal(70 + index),
            source_evidence_id="ghcn",
        )
        for index in range(3)
    ]
    first = ClimateHistory.build("CLIAAA", rows)
    second = ClimateHistory.build("CLIAAA", list(reversed(rows)))
    assert first.content_hash == second.content_hash
