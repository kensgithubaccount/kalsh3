"""Execution research metrics with event-level accounting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from services.opportunity_engine.books import OutcomeSide

from .domain import MarkoutObservation, SimulatedFill, SimulatedOrder


def markout(
    fill: SimulatedFill,
    side: OutcomeSide,
    reference_time: datetime,
    market_reference_yes: Decimal,
    executable_unwind: Decimal | None,
) -> MarkoutObservation:
    outcome_reference = (
        market_reference_yes if side == OutcomeSide.YES else Decimal(1) - market_reference_yes
    )
    return MarkoutObservation(
        fill.simulated_fill_id,
        reference_time - fill.timestamp,
        reference_time,
        outcome_reference,
        executable_unwind,
        outcome_reference - fill.price,
    )


@dataclass(frozen=True, slots=True)
class SettlementResult:
    simulated_order_id: str
    settled_outcome: OutcomeSide
    gross_pnl: Decimal | None
    fees: Decimal
    net_pnl: Decimal | None
    committed_capital: Decimal
    capital_duration: timedelta | None


def settle(
    order: SimulatedOrder, settled_outcome: OutcomeSide, settled_at: datetime
) -> SettlementResult:
    if not order.fills:
        return SettlementResult(
            order.simulated_order_id, settled_outcome, None, Decimal(0), None, Decimal(0), None
        )
    cost = sum((fill.price * fill.quantity for fill in order.fills), Decimal(0))
    fees = sum((fill.fee for fill in order.fills), Decimal(0))
    payout = sum(
        (fill.quantity for fill in order.fills if fill.outcome_side == settled_outcome), Decimal(0)
    )
    gross = payout - cost
    return SettlementResult(
        order.simulated_order_id,
        settled_outcome,
        gross,
        fees,
        gross - fees,
        cost + fees,
        settled_at - order.fills[0].timestamp,
    )


@dataclass(frozen=True, slots=True)
class DrawdownPoint:
    timestamp: datetime
    cumulative_pnl: Decimal
    peak: Decimal
    drawdown: Decimal


def drawdown_series(rows: tuple[tuple[datetime, Decimal], ...]) -> tuple[DrawdownPoint, ...]:
    cumulative = Decimal(0)
    peak = Decimal(0)
    output = []
    for timestamp, pnl in sorted(rows):
        cumulative += pnl
        peak = max(peak, cumulative)
        output.append(DrawdownPoint(timestamp, cumulative, peak, peak - cumulative))
    return tuple(output)


@dataclass(frozen=True, slots=True)
class CapacityPoint:
    hypothetical_quantity: Decimal
    attempts: int
    fill_rate: Decimal
    average_slippage: Decimal | None
    average_attempt_value: Decimal | None


@dataclass(frozen=True, slots=True)
class EventAccounting:
    candidate_attempts: int
    simulated_orders: int
    fills: int
    unique_markets: int
    unique_events: int
    settled_events: int
    effective_sample_size: Decimal


def event_accounting(attempts: tuple[tuple[str, str, str, bool, int], ...]) -> EventAccounting:
    events = {event for _, _, event, _, _ in attempts}
    settled = {event for _, _, event, is_settled, _ in attempts if is_settled}
    return EventAccounting(
        len(attempts),
        len({order for order, _, _, _, _ in attempts}),
        sum(fill_count for _, _, _, _, fill_count in attempts),
        len({market for _, market, _, _, _ in attempts}),
        len(events),
        len(settled),
        Decimal(len(settled)),
    )


def concentration(event_pnl: dict[str, Decimal]) -> tuple[Decimal, Decimal, Decimal]:
    total = sum(event_pnl.values(), Decimal(0))
    positive = sorted((value for value in event_pnl.values() if value > 0), reverse=True)
    if total <= 0 or not positive:
        return Decimal(0), Decimal(0), total
    best = positive[0] / total
    top_five = sum(positive[:5], Decimal(0)) / total
    return best, top_five, total - positive[0]
