"""Pure fee-aware evaluation for M28 weather tournament predictions.

This module connects the M28C model fit to M28D historical top-of-book economics without
adding any network, credential, account, production-state, authorization, approval, or order
boundary.  It deliberately evaluates at most one contract per independent weather event so
sibling binary contracts cannot be counted as independent trade opportunities.

Historical quote checkpoints must be supplied by a caller from exact pre-cutoff bid/ask
evidence.  Missing quotes fail closed by default; a midpoint or last trade is never accepted
as a substitute for executable top-of-book evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from services.historical_replay.archive import stable_hash
from services.opportunity_engine.fees import FeeType

from .historical_economics import (
    HistoricalOpportunity,
    HistoricalQuoteCheckpoint,
    TradeSide,
    evaluate_historical_opportunity,
    reconstruct_checkpoint_economics,
)
from .model_tournament import (
    EDGE_THRESHOLD,
    ModelTournamentError,
    ModelTournamentResult,
    TournamentFeatureDataset,
    TournamentFeatureRow,
    TournamentModel,
    TournamentPartition,
)


class TournamentEconomicsError(ValueError):
    """Fee-aware tournament evaluation violated an evidence or identity invariant."""


@dataclass(frozen=True, slots=True)
class TournamentRowPrediction:
    row_id: str
    event_id: str
    market_ticker: str
    model: TournamentModel
    partition: TournamentPartition
    model_yes_probability: Decimal
    model_evidence_id: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class FeeAwareEventSelection:
    event_id: str
    row_id: str
    market_ticker: str
    model: TournamentModel
    side: TradeSide
    model_side_probability: Decimal
    taker_all_in_cost: Decimal
    after_cost_edge: Decimal
    resolved_yes: int
    hypothetical_pnl: Decimal
    economics_id: str
    opportunity_id: str
    trade_eligible: bool
    content_hash: str


@dataclass(frozen=True, slots=True)
class FeeAwareTournamentEvaluation:
    evaluation_id: str
    feature_dataset_id: str
    tournament_id: str
    model: TournamentModel
    partition: TournamentPartition
    minimum_after_cost_edge: Decimal
    fee_type: FeeType
    fee_multiplier: Decimal
    contract_count: int
    independent_event_count: int
    quote_checkpoint_count: int
    selected_event_count: int
    trade_count: int
    winning_trade_count: int
    losing_trade_count: int
    hypothetical_total_pnl: Decimal
    hypothetical_average_pnl: Decimal
    exact_fill_truth: bool
    promotion_authority: str
    selections: tuple[FeeAwareEventSelection, ...]
    content_hash: str


def tournament_row_predictions(
    dataset: TournamentFeatureDataset,
    tournament: ModelTournamentResult,
    *,
    model: TournamentModel,
    partition: TournamentPartition,
) -> tuple[TournamentRowPrediction, ...]:
    """Reconstruct deterministic per-row probabilities from one frozen M28C fit."""

    if tournament.feature_dataset_id != dataset.dataset_id:
        raise TournamentEconomicsError("tournament result does not bind the feature dataset")
    rows = tuple(row for row in dataset.rows if row.partition is partition)
    if not rows:
        raise TournamentEconomicsError("requested tournament partition is empty")

    fit = tournament.fit
    city_biases = dict(fit.city_biases)
    predictions: list[TournamentRowPrediction] = []
    for row in rows:
        probability = _prediction(
            row,
            model=model,
            pooled_alpha=fit.pooled_alpha,
            pooled_bias=fit.pooled_bias,
            city_biases=city_biases,
            calibration_slope=fit.calibration_slope,
            calibration_offset=fit.calibration_offset,
            ensemble_weight=fit.ensemble_weight,
        )
        model_evidence_id = stable_hash(
            (
                "m28d-tournament-row-prediction-v1",
                tournament.tournament_id,
                tournament.fit.content_hash,
                row.row_id,
                model.value,
                str(probability),
            )
        )
        predictions.append(
            TournamentRowPrediction(
                row_id=row.row_id,
                event_id=row.event_id,
                market_ticker=row.market_ticker,
                model=model,
                partition=partition,
                model_yes_probability=probability,
                model_evidence_id=model_evidence_id,
                content_hash=model_evidence_id,
            )
        )
    return tuple(sorted(predictions, key=lambda item: (item.event_id, item.market_ticker)))


def evaluate_fee_aware_partition(
    dataset: TournamentFeatureDataset,
    tournament: ModelTournamentResult,
    *,
    model: TournamentModel,
    partition: TournamentPartition,
    quote_checkpoints: Mapping[str, HistoricalQuoteCheckpoint],
    fee_type: FeeType,
    fee_multiplier: Decimal,
    minimum_after_cost_edge: Decimal = EDGE_THRESHOLD,
) -> FeeAwareTournamentEvaluation:
    """Evaluate one model with executable pre-cutoff prices and reviewed historical fees.

    Every feature row in the requested partition must have exactly one ticker-bound quote at
    the same checkpoint timestamp.  All rows are evaluated, then exactly one strongest
    opportunity is retained per weather event.  Only event winners whose after-cost edge
    reaches ``minimum_after_cost_edge`` count as hypothetical trades.
    """

    if (
        not minimum_after_cost_edge.is_finite()
        or minimum_after_cost_edge < 0
        or minimum_after_cost_edge > 1
    ):
        raise TournamentEconomicsError("minimum after-cost edge is outside [0,1]")
    if not fee_multiplier.is_finite() or fee_multiplier < 0:
        raise TournamentEconomicsError("fee multiplier is invalid")

    rows = tuple(row for row in dataset.rows if row.partition is partition)
    if not rows:
        raise TournamentEconomicsError("requested tournament partition is empty")
    predictions = tournament_row_predictions(
        dataset,
        tournament,
        model=model,
        partition=partition,
    )
    prediction_by_row = {item.row_id: item for item in predictions}
    if len(prediction_by_row) != len(rows):
        raise TournamentEconomicsError("tournament prediction coverage is incomplete")

    expected_tickers = {row.market_ticker for row in rows}
    missing = sorted(expected_tickers - set(quote_checkpoints))
    extra = sorted(set(quote_checkpoints) - expected_tickers)
    if missing:
        raise TournamentEconomicsError(
            f"historical executable quote coverage is incomplete (missing={len(missing)})"
        )
    if extra:
        raise TournamentEconomicsError(
            f"historical quote map contains out-of-partition tickers (extra={len(extra)})"
        )

    opportunities_by_event: dict[str, list[tuple[TournamentFeatureRow, HistoricalOpportunity]]] = {}
    for row in rows:
        quote = quote_checkpoints[row.market_ticker]
        if quote.market_ticker != row.market_ticker:
            raise TournamentEconomicsError("quote ticker binding is invalid")
        if quote.checkpoint_at != row.checkpoint_at:
            raise TournamentEconomicsError("quote timestamp does not match feature checkpoint")
        economics = reconstruct_checkpoint_economics(
            quote,
            fee_type=fee_type,
            fee_multiplier=fee_multiplier,
        )
        prediction = prediction_by_row[row.row_id]
        opportunity = evaluate_historical_opportunity(
            economics,
            model_yes_probability=prediction.model_yes_probability,
            resolved_yes=row.realized_yes,
            model_evidence_id=prediction.model_evidence_id,
        )
        opportunities_by_event.setdefault(row.event_id, []).append((row, opportunity))

    selections: list[FeeAwareEventSelection] = []
    for event_id, values in sorted(opportunities_by_event.items()):
        # Highest after-cost edge wins.  The ticker provides deterministic tie-breaking.
        row, opportunity = min(
            values,
            key=lambda value: (-value[1].after_cost_edge, value[0].market_ticker),
        )
        trade_eligible = opportunity.after_cost_edge >= minimum_after_cost_edge
        selection_material = (
            "m28d-fee-aware-event-selection-v1",
            tournament.tournament_id,
            dataset.dataset_id,
            event_id,
            row.row_id,
            opportunity.content_hash,
            str(minimum_after_cost_edge),
            trade_eligible,
        )
        digest = stable_hash(selection_material)
        selections.append(
            FeeAwareEventSelection(
                event_id=event_id,
                row_id=row.row_id,
                market_ticker=row.market_ticker,
                model=model,
                side=opportunity.side,
                model_side_probability=opportunity.model_side_probability,
                taker_all_in_cost=opportunity.all_in_cost,
                after_cost_edge=opportunity.after_cost_edge,
                resolved_yes=row.realized_yes,
                hypothetical_pnl=opportunity.hypothetical_pnl,
                economics_id=opportunity.economics_id,
                opportunity_id=opportunity.opportunity_id,
                trade_eligible=trade_eligible,
                content_hash=digest,
            )
        )

    eligible = tuple(item for item in selections if item.trade_eligible)
    total_pnl = sum((item.hypothetical_pnl for item in eligible), Decimal("0"))
    trade_count = len(eligible)
    average_pnl = Decimal("0") if not eligible else total_pnl / Decimal(trade_count)
    wins = sum(1 for item in eligible if item.hypothetical_pnl > 0)
    losses = sum(1 for item in eligible if item.hypothetical_pnl < 0)
    evaluation_material = (
        "m28d-fee-aware-tournament-evaluation-v1",
        dataset.dataset_id,
        tournament.tournament_id,
        model.value,
        partition.value,
        str(minimum_after_cost_edge),
        fee_type.value,
        str(fee_multiplier),
        tuple(item.content_hash for item in selections),
        trade_count,
        wins,
        losses,
        str(total_pnl),
        str(average_pnl),
        False,
        "NONE",
    )
    digest = stable_hash(evaluation_material)
    return FeeAwareTournamentEvaluation(
        evaluation_id=digest,
        feature_dataset_id=dataset.dataset_id,
        tournament_id=tournament.tournament_id,
        model=model,
        partition=partition,
        minimum_after_cost_edge=minimum_after_cost_edge,
        fee_type=fee_type,
        fee_multiplier=fee_multiplier,
        contract_count=len(rows),
        independent_event_count=len(opportunities_by_event),
        quote_checkpoint_count=len(quote_checkpoints),
        selected_event_count=len(selections),
        trade_count=trade_count,
        winning_trade_count=wins,
        losing_trade_count=losses,
        hypothetical_total_pnl=total_pnl,
        hypothetical_average_pnl=average_pnl,
        exact_fill_truth=False,
        promotion_authority="NONE",
        selections=tuple(selections),
        content_hash=digest,
    )


def _prediction(
    row: TournamentFeatureRow,
    *,
    model: TournamentModel,
    pooled_alpha: Decimal,
    pooled_bias: Decimal,
    city_biases: Mapping[str, Decimal],
    calibration_slope: Decimal,
    calibration_offset: Decimal,
    ensemble_weight: Decimal,
) -> Decimal:
    market = row.market_probability
    climate = row.climate_probability
    pooled = _clip(market + pooled_alpha * (climate - market) + pooled_bias)
    city = _clip(pooled + city_biases.get(row.station_id, Decimal("0")))
    calibrated = _clip(
        Decimal("0.5") + calibration_slope * (city - Decimal("0.5")) + calibration_offset
    )
    if model is TournamentModel.MARKET:
        value = market
    elif model is TournamentModel.NOAA_CLIMATOLOGY:
        value = climate
    elif model is TournamentModel.POOLED_RESIDUAL:
        value = pooled
    elif model is TournamentModel.CITY_SHRUNK_RESIDUAL:
        value = city
    elif model is TournamentModel.CALIBRATED_ENSEMBLE:
        value = _clip(ensemble_weight * calibrated + (Decimal("1") - ensemble_weight) * market)
    else:
        raise ModelTournamentError("unsupported tournament model")
    return _clip(value)


def _clip(value: Decimal) -> Decimal:
    return max(Decimal("0"), min(Decimal("1"), value))
