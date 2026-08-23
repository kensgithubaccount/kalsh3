from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from services.opportunity_engine.fees import FeeType
from services.production_weather_strategy.historical_economics import (
    HistoricalQuoteCheckpoint,
)
from services.production_weather_strategy.model_tournament import (
    ModelScorecard,
    ModelTournamentResult,
    TournamentFeatureDataset,
    TournamentFeatureRow,
    TournamentFit,
    TournamentModel,
    TournamentPartition,
)
from services.production_weather_strategy.tournament_economics import (
    TournamentEconomicsError,
    evaluate_fee_aware_partition,
    tournament_row_predictions,
)


CHECKPOINT = datetime(2026, 8, 23, 3, tzinfo=UTC)


def _row(
    ticker: str,
    *,
    row_id: str,
    event_id: str,
    market: str,
    climate: str,
    resolved_yes: int,
    station: str = "CLIMDW",
) -> TournamentFeatureRow:
    return TournamentFeatureRow(
        row_id=row_id,
        event_id=event_id,
        contract_id=f"contract-{row_id}",
        market_ticker=ticker,
        station_id=station,
        measurement="DAILY_MAX",
        local_date=date(2026, 8, 23),
        realized_yes=resolved_yes,
        checkpoint_at=CHECKPOINT,
        market_probability=Decimal(market),
        climate_probability=Decimal(climate),
        climate_sample_count=50,
        market_evidence_id=f"market-{row_id}",
        climate_evidence_id=f"climate-{row_id}",
        partition=TournamentPartition.TEST,
        content_hash=f"hash-{row_id}",
    )


def _dataset() -> TournamentFeatureDataset:
    rows = (
        _row(
            "KXHIGHCHI-26AUG23-B74.5",
            row_id="r1",
            event_id="event-a",
            market="0.25",
            climate="0.70",
            resolved_yes=1,
        ),
        _row(
            "KXHIGHCHI-26AUG23-B76.5",
            row_id="r2",
            event_id="event-a",
            market="0.40",
            climate="0.55",
            resolved_yes=0,
        ),
        _row(
            "KXHIGHNY-26AUG23-B80.5",
            row_id="r3",
            event_id="event-b",
            market="0.55",
            climate="0.20",
            resolved_yes=0,
            station="CLINYC",
        ),
    )
    return TournamentFeatureDataset(
        dataset_id="dataset",
        settlement_dataset_id="settlements",
        feature_schema_hash="schema",
        rows=rows,
        train_event_ids=(),
        validation_event_ids=(),
        test_event_ids=("event-a", "event-b"),
        missing_market_contracts=0,
        missing_climate_contracts=0,
        content_hash="dataset",
    )


def _score(model: TournamentModel) -> ModelScorecard:
    return ModelScorecard(
        model=model,
        partition=TournamentPartition.TEST,
        contract_count=3,
        unique_event_count=2,
        event_weighted_brier=Decimal("0.1"),
        event_weighted_log_loss=Decimal("0.2"),
        calibration_gap=Decimal("0"),
        market_relative_skill=Decimal("0"),
        hypothetical_trade_count=0,
        hypothetical_total_pnl=Decimal("0"),
        hypothetical_average_pnl=Decimal("0"),
    )


def _tournament(*, ensemble_weight: str = "0.5") -> ModelTournamentResult:
    fit = TournamentFit(
        pooled_alpha=Decimal("0.50"),
        pooled_bias=Decimal("0.01"),
        city_biases=(("CLIMDW", Decimal("0.02")), ("CLINYC", Decimal("-0.01"))),
        calibration_slope=Decimal("0.9"),
        calibration_offset=Decimal("0.01"),
        ensemble_weight=Decimal(ensemble_weight),
        validation_selected_model=TournamentModel.CALIBRATED_ENSEMBLE,
        content_hash="fit",
    )
    selected = _score(TournamentModel.CALIBRATED_ENSEMBLE)
    market = _score(TournamentModel.MARKET)
    return ModelTournamentResult(
        tournament_id="tournament",
        feature_dataset_id="dataset",
        fit=fit,
        scorecards=(market, selected),
        selected_test_scorecard=selected,
        test_market_scorecard=market,
        test_edge_classification="NO_TEST_EDGE",
        promotion_authority="NONE",
        content_hash="tournament",
    )


def _quotes() -> dict[str, HistoricalQuoteCheckpoint]:
    return {
        "KXHIGHCHI-26AUG23-B74.5": HistoricalQuoteCheckpoint.build(
            market_ticker="KXHIGHCHI-26AUG23-B74.5",
            checkpoint_at=CHECKPOINT,
            yes_bid=Decimal("0.24"),
            yes_ask=Decimal("0.26"),
            quote_evidence_id="q1",
        ),
        "KXHIGHCHI-26AUG23-B76.5": HistoricalQuoteCheckpoint.build(
            market_ticker="KXHIGHCHI-26AUG23-B76.5",
            checkpoint_at=CHECKPOINT,
            yes_bid=Decimal("0.39"),
            yes_ask=Decimal("0.41"),
            quote_evidence_id="q2",
        ),
        "KXHIGHNY-26AUG23-B80.5": HistoricalQuoteCheckpoint.build(
            market_ticker="KXHIGHNY-26AUG23-B80.5",
            checkpoint_at=CHECKPOINT,
            yes_bid=Decimal("0.54"),
            yes_ask=Decimal("0.56"),
            quote_evidence_id="q3",
        ),
    }


def test_selected_model_predictions_reconstruct_from_frozen_fit() -> None:
    predictions = tournament_row_predictions(
        _dataset(),
        _tournament(ensemble_weight="0.5"),
        model=TournamentModel.CALIBRATED_ENSEMBLE,
        partition=TournamentPartition.TEST,
    )
    by_row = {item.row_id: item.model_yes_probability for item in predictions}
    # r1 pooled=.485, city=.505, calibrated=.5145, ensemble=(.5145+.25)/2.
    assert by_row["r1"] == Decimal("0.38225")
    assert len(predictions) == 3


def test_zero_ensemble_weight_reproduces_market_exactly() -> None:
    predictions = tournament_row_predictions(
        _dataset(),
        _tournament(ensemble_weight="0"),
        model=TournamentModel.CALIBRATED_ENSEMBLE,
        partition=TournamentPartition.TEST,
    )
    market = {row.row_id: row.market_probability for row in _dataset().rows}
    assert {item.row_id: item.model_yes_probability for item in predictions} == market


def test_fee_aware_evaluation_selects_only_one_sibling_contract_per_event() -> None:
    evaluation = evaluate_fee_aware_partition(
        _dataset(),
        _tournament(),
        model=TournamentModel.NOAA_CLIMATOLOGY,
        partition=TournamentPartition.TEST,
        quote_checkpoints=_quotes(),
        fee_type=FeeType.QUADRATIC,
        fee_multiplier=Decimal("1"),
        minimum_after_cost_edge=Decimal("0.03"),
    )
    assert evaluation.contract_count == 3
    assert evaluation.independent_event_count == 2
    assert evaluation.selected_event_count == 2
    assert len({item.event_id for item in evaluation.selections}) == 2
    assert evaluation.trade_count <= 2
    assert evaluation.exact_fill_truth is False
    assert evaluation.promotion_authority == "NONE"


def test_fee_aware_evaluation_fails_closed_on_missing_or_extra_quote() -> None:
    quotes = _quotes()
    quotes.pop("KXHIGHCHI-26AUG23-B76.5")
    with pytest.raises(TournamentEconomicsError, match="coverage is incomplete"):
        evaluate_fee_aware_partition(
            _dataset(),
            _tournament(),
            model=TournamentModel.CALIBRATED_ENSEMBLE,
            partition=TournamentPartition.TEST,
            quote_checkpoints=quotes,
            fee_type=FeeType.QUADRATIC,
            fee_multiplier=Decimal("1"),
        )

    quotes = _quotes()
    quotes["EXTRA"] = HistoricalQuoteCheckpoint.build(
        market_ticker="EXTRA",
        checkpoint_at=CHECKPOINT,
        yes_bid=Decimal("0.20"),
        yes_ask=Decimal("0.21"),
        quote_evidence_id="extra",
    )
    with pytest.raises(TournamentEconomicsError, match="out-of-partition"):
        evaluate_fee_aware_partition(
            _dataset(),
            _tournament(),
            model=TournamentModel.CALIBRATED_ENSEMBLE,
            partition=TournamentPartition.TEST,
            quote_checkpoints=quotes,
            fee_type=FeeType.QUADRATIC,
            fee_multiplier=Decimal("1"),
        )


def test_fee_aware_evaluation_rejects_stale_quote_timestamp() -> None:
    quotes = _quotes()
    quotes["KXHIGHCHI-26AUG23-B74.5"] = HistoricalQuoteCheckpoint.build(
        market_ticker="KXHIGHCHI-26AUG23-B74.5",
        checkpoint_at=datetime(2026, 8, 23, 2, tzinfo=UTC),
        yes_bid=Decimal("0.24"),
        yes_ask=Decimal("0.26"),
        quote_evidence_id="stale",
    )
    with pytest.raises(TournamentEconomicsError, match="timestamp"):
        evaluate_fee_aware_partition(
            _dataset(),
            _tournament(),
            model=TournamentModel.CALIBRATED_ENSEMBLE,
            partition=TournamentPartition.TEST,
            quote_checkpoints=quotes,
            fee_type=FeeType.QUADRATIC,
            fee_multiplier=Decimal("1"),
        )


def test_integration_module_has_no_network_credential_execution_or_mutation_boundary() -> None:
    source = Path("services/production_weather_strategy/tournament_economics.py").read_text()
    forbidden = (
        "urllib",
        "requests",
        "httpx",
        "services.production_execution",
        "services.kalshi_account_gateway",
        "services.risk_engine.authorization",
        "AuthorizationStore",
        "submit_order",
        "private_key",
    )
    assert all(term not in source for term in forbidden)
