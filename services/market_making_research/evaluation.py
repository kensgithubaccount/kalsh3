"""Bind MM-A1 quote proposals to canonical M11 simulation and markout evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum

from services.execution_simulation.analytics import SettlementResult
from services.execution_simulation.domain import (
    MarkoutObservation,
    OrderState,
    SimulatedOrder,
    SimulationCase,
    StrategyType,
)
from services.historical_replay.archive import stable_hash

from .domain import MarketMakingError, ShadowQuotePlan, ShadowQuoteState

REQUIRED_MARKOUT_HORIZONS = (timedelta(seconds=1), timedelta(seconds=30), timedelta(minutes=5))


class AttemptEvidenceState(StrEnum):
    NO_FILL_COMPLETE = "NO_FILL_COMPLETE"
    MARKOUT_COMPLETE_UNSETTLED = "MARKOUT_COMPLETE_UNSETTLED"
    SETTLED_COMPLETE = "SETTLED_COMPLETE"
    EXECUTION_UNKNOWN = "EXECUTION_UNKNOWN"


@dataclass(frozen=True, slots=True)
class HorizonMarkout:
    horizon: timedelta
    quantity_weighted_markout: Decimal


@dataclass(frozen=True, slots=True)
class ShadowAttemptReceipt:
    receipt_id: str
    plan_id: str
    quote_id: str
    simulated_order_id: str
    market_ticker: str
    event_id: str
    scenario: SimulationCase
    evidence_state: AttemptEvidenceState
    requested_quantity: Decimal
    filled_quantity: Decimal
    maker_fees: Decimal
    predicted_filled_edge: Decimal
    markouts: tuple[HorizonMarkout, ...]
    settlement_net_pnl: Decimal | None
    profitability_claim: str = "NOT_ESTABLISHED"
    research_only: bool = True
    production_influence: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if (
            not self.research_only
            or self.production_influence != 0
            or self.profitability_claim != "NOT_ESTABLISHED"
        ):
            raise MarketMakingError("attempt receipt cannot assert profitability or influence")


def _weighted_markouts(
    order: SimulatedOrder, observations: tuple[MarkoutObservation, ...]
) -> tuple[HorizonMarkout, ...]:
    fills = {fill.simulated_fill_id: fill for fill in order.fills}
    expected = {
        (fill.simulated_fill_id, horizon)
        for fill in order.fills
        for horizon in REQUIRED_MARKOUT_HORIZONS
    }
    actual = {(row.simulated_fill_id, row.horizon) for row in observations}
    if actual != expected or len(actual) != len(observations):
        raise MarketMakingError("markouts must cover every fill at each reviewed horizon exactly")
    output = []
    total_quantity = sum((fill.quantity for fill in order.fills), Decimal(0))
    for horizon in REQUIRED_MARKOUT_HORIZONS:
        weighted = sum(
            (
                row.normalized_markout * fills[row.simulated_fill_id].quantity
                for row in observations
                if row.horizon == horizon
            ),
            Decimal(0),
        )
        output.append(HorizonMarkout(horizon, weighted / total_quantity))
    return tuple(output)


def build_attempt_receipt(
    *,
    plan: ShadowQuotePlan,
    quote_id: str,
    order: SimulatedOrder,
    markouts: tuple[MarkoutObservation, ...],
    settlement: SettlementResult | None,
) -> ShadowAttemptReceipt:
    """Create one immutable evaluation receipt without upgrading simulated evidence."""
    if plan.state is ShadowQuoteState.ABSTAIN:
        raise MarketMakingError("abstaining plan cannot be evaluated as an attempted quote")
    selected = tuple(quote for quote in plan.quotes if quote.quote_id == quote_id)
    if len(selected) != 1:
        raise MarketMakingError("quote is not uniquely bound to plan")
    quote = selected[0]
    if order.frozen_candidate_id != quote.quote_id or order.strategy is StrategyType.TAKER_NOW:
        raise MarketMakingError("canonical maker simulation is not bound to shadow quote")
    if order.production_influence != 0 or any(not fill.maker for fill in order.fills):
        raise MarketMakingError("attempt contains non-maker or production-influencing evidence")
    if any(
        fill.outcome_side != quote.outcome_side.value
        or fill.price != quote.price
        or fill.scenario is not order.scenario
        for fill in order.fills
    ):
        raise MarketMakingError("fill direction, price, or scenario does not match quote")
    if settlement is not None and settlement.simulated_order_id != order.simulated_order_id:
        raise MarketMakingError("settlement does not belong to simulated order")

    filled = sum((fill.quantity for fill in order.fills), Decimal(0))
    fees = sum((fill.fee for fill in order.fills), Decimal(0))
    if order.state is OrderState.EXECUTION_OUTCOME_UNKNOWN:
        if settlement is not None or markouts:
            raise MarketMakingError("unknown execution cannot carry conclusive economics")
        state = AttemptEvidenceState.EXECUTION_UNKNOWN
        horizon_rows: tuple[HorizonMarkout, ...] = ()
    elif filled == 0:
        if settlement is not None or markouts:
            raise MarketMakingError("no-fill attempt cannot carry fill economics")
        state = AttemptEvidenceState.NO_FILL_COMPLETE
        horizon_rows = ()
    else:
        horizon_rows = _weighted_markouts(order, markouts)
        state = (
            AttemptEvidenceState.SETTLED_COMPLETE
            if settlement is not None
            else AttemptEvidenceState.MARKOUT_COMPLETE_UNSETTLED
        )
    predicted_edge = quote.net_edge_per_contract * filled
    settlement_net = None if settlement is None else settlement.net_pnl
    material = (
        "mm-a1-shadow-attempt-v1",
        plan.plan_id,
        quote.quote_id,
        order.simulated_order_id,
        order.scenario,
        state,
        str(order.initial_quantity),
        str(filled),
        str(fees),
        str(predicted_edge),
        horizon_rows,
        str(settlement_net),
    )
    return ShadowAttemptReceipt(
        stable_hash(material),
        plan.plan_id,
        quote.quote_id,
        order.simulated_order_id,
        plan.market_ticker,
        plan.event_id,
        order.scenario,
        state,
        order.initial_quantity,
        filled,
        fees,
        predicted_edge,
        horizon_rows,
        settlement_net,
    )


@dataclass(frozen=True, slots=True)
class MarketMakingEvidenceSummary:
    summary_id: str
    attempts: int
    unique_plans: int
    unique_markets: int
    unique_events: int
    filled_attempts: int
    execution_unknown_attempts: int
    filled_quantity: Decimal
    maker_fees: Decimal
    predicted_filled_edge: Decimal
    mean_markouts: tuple[HorizonMarkout, ...]
    settled_attempts: int
    settled_net_pnl: Decimal
    profitability_claim: str = "NOT_ESTABLISHED"
    production_eligible: bool = False
    production_influence: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if (
            self.profitability_claim != "NOT_ESTABLISHED"
            or self.production_eligible
            or self.production_influence != 0
        ):
            raise MarketMakingError("MM-A1 summary cannot establish production profitability")


def summarize_attempts(
    receipts: tuple[ShadowAttemptReceipt, ...],
) -> MarketMakingEvidenceSummary:
    if not receipts:
        raise MarketMakingError("at least one attempt receipt is required")
    if len({receipt.receipt_id for receipt in receipts}) != len(receipts):
        raise MarketMakingError("duplicate attempt receipt")
    filled_rows = tuple(receipt for receipt in receipts if receipt.filled_quantity > 0)
    complete_markout_rows = tuple(
        receipt
        for receipt in filled_rows
        if receipt.evidence_state
        in {AttemptEvidenceState.MARKOUT_COMPLETE_UNSETTLED, AttemptEvidenceState.SETTLED_COMPLETE}
    )
    total_filled = sum((receipt.filled_quantity for receipt in filled_rows), Decimal(0))
    mean_rows: list[HorizonMarkout] = []
    if complete_markout_rows and len(complete_markout_rows) == len(filled_rows):
        markout_quantity = sum(
            (receipt.filled_quantity for receipt in complete_markout_rows), Decimal(0)
        )
        for horizon in REQUIRED_MARKOUT_HORIZONS:
            weighted = sum(
                (
                    next(
                        row for row in receipt.markouts if row.horizon == horizon
                    ).quantity_weighted_markout
                    * receipt.filled_quantity
                    for receipt in complete_markout_rows
                ),
                Decimal(0),
            )
            mean_rows.append(HorizonMarkout(horizon, weighted / markout_quantity))
    settled = tuple(
        receipt
        for receipt in receipts
        if receipt.evidence_state is AttemptEvidenceState.SETTLED_COMPLETE
    )
    material = (
        "mm-a1-evidence-summary-v1",
        tuple(sorted(receipt.receipt_id for receipt in receipts)),
        tuple(mean_rows),
    )
    return MarketMakingEvidenceSummary(
        stable_hash(material),
        len(receipts),
        len({receipt.plan_id for receipt in receipts}),
        len({receipt.market_ticker for receipt in receipts}),
        len({receipt.event_id for receipt in receipts}),
        len(filled_rows),
        sum(
            receipt.evidence_state is AttemptEvidenceState.EXECUTION_UNKNOWN for receipt in receipts
        ),
        total_filled,
        sum((receipt.maker_fees for receipt in receipts), Decimal(0)),
        sum((receipt.predicted_filled_edge for receipt in receipts), Decimal(0)),
        tuple(mean_rows),
        len(settled),
        sum(
            (
                receipt.settlement_net_pnl
                for receipt in settled
                if receipt.settlement_net_pnl is not None
            ),
            Decimal(0),
        ),
    )
