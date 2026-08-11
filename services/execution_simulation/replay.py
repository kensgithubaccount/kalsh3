"""Sequence-aware taker and conservative aggregate-queue maker replay."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from services.opportunity_engine.books import NormalizedBook, OutcomeSide, walk_depth
from services.opportunity_engine.fees import FeePolicy, calculate_fee

from .domain import (
    ExecutionAssumptionPolicy,
    FillState,
    OrderState,
    QueueQuality,
    ReplayFidelity,
    SimulatedFill,
    SimulatedOrder,
    SimulationError,
    StrategyType,
)


@dataclass(frozen=True, slots=True)
class ReplayBook:
    timestamp: datetime
    sequence: int
    book: NormalizedBook
    fidelity: ReplayFidelity
    lineage: str
    active: bool = True


class FlowKind(StrEnum):
    TRADE = "TRADE"
    LEVEL_REDUCTION = "LEVEL_REDUCTION"
    GAP = "GAP"
    PAUSE = "PAUSE"
    RULE_CHANGE = "RULE_CHANGE"
    SOURCE_FAILURE = "SOURCE_FAILURE"


@dataclass(frozen=True, slots=True)
class ReplayFlow:
    event_id: str
    timestamp: datetime
    sequence: int
    kind: FlowKind
    outcome_side: OutcomeSide | None
    price: Decimal | None
    quantity: Decimal
    book_lineage: str


def _asks(book: NormalizedBook, side: OutcomeSide) -> tuple[object, ...]:
    return book.yes_asks if side == OutcomeSide.YES else book.no_asks


def _fill_state(filled: Decimal, requested: Decimal) -> FillState:
    if filled == 0:
        return FillState.NO_FILL
    return FillState.FULL_FILL if filled == requested else FillState.PARTIAL_FILL


def simulate_taker(
    *,
    simulated_order_id: str,
    candidate_id: str,
    side: OutcomeSide,
    candidate_time: datetime,
    decision_time: datetime,
    quantity: Decimal,
    policy: ExecutionAssumptionPolicy,
    books: tuple[ReplayBook, ...],
    fee_policy: FeePolicy,
    max_book_age: timedelta,
) -> SimulatedOrder:
    submit = decision_time + policy.latency.scheduling
    arrival = decision_time + policy.latency.decision_to_arrival
    available = [row for row in books if row.timestamp <= arrival]
    if not available:
        raise SimulationError("no arrival-time replay book")
    snapshot = max(available, key=lambda row: (row.timestamp, row.sequence))
    if arrival - snapshot.timestamp > max_book_age or not snapshot.active:
        raise SimulationError("arrival-time book stale or inactive")
    if snapshot.fidelity not in {
        ReplayFidelity.SEQUENCE_BOOK_AND_TRADES,
        ReplayFidelity.HIGH_RESOLUTION_BOOK,
    }:
        raise SimulationError("taker replay fidelity insufficient")
    levels = snapshot.book.yes_asks if side == OutcomeSide.YES else snapshot.book.no_asks
    walk = walk_depth(levels, quantity)  # exact relative to arrival snapshot
    fee = (
        Decimal(0)
        if walk.filled == 0 or walk.average_price is None
        else calculate_fee(fee_policy, walk.average_price, walk.filled).total_fee
    )
    fills: tuple[SimulatedFill, ...] = ()
    if walk.filled and walk.average_price is not None:
        fills = (
            SimulatedFill(
                f"sim-fill-{simulated_order_id}-1",
                simulated_order_id,
                arrival,
                side,
                "ASK",
                walk.average_price,
                walk.filled,
                False,
                None,
                f"arrival-book-{snapshot.sequence}",
                fee,
                walk.filled,
                walk.unfilled,
                snapshot.lineage,
                snapshot.sequence,
                policy.scenario,
            ),
        )
    state = {
        FillState.NO_FILL: OrderState.NO_FILL_SIMULATION,
        FillState.PARTIAL_FILL: OrderState.PARTIALLY_FILLED_SIMULATION,
        FillState.FULL_FILL: OrderState.FILLED_SIMULATION,
    }[_fill_state(walk.filled, quantity)]
    return SimulatedOrder(
        simulated_order_id,
        candidate_id,
        StrategyType.TAKER_NOW,
        policy.scenario,
        candidate_time,
        decision_time,
        submit,
        arrival,
        quantity,
        walk.unfilled,
        None,
        state,
        QueueQuality.UNKNOWN,
        None,
        fills,
    )


def simulate_maker(
    *,
    simulated_order_id: str,
    candidate_id: str,
    strategy: StrategyType,
    side: OutcomeSide,
    price: Decimal,
    quantity: Decimal,
    displayed_ahead: Decimal,
    candidate_time: datetime,
    decision_time: datetime,
    policy: ExecutionAssumptionPolicy,
    flows: tuple[ReplayFlow, ...],
    fee_policy: FeePolicy,
) -> SimulatedOrder:
    if strategy == StrategyType.TAKER_NOW:
        raise SimulationError("maker simulator requires maker strategy")
    if policy.required_fidelity != ReplayFidelity.SEQUENCE_BOOK_AND_TRADES:
        raise SimulationError("maker policy must require sequence book and trades")
    submit = decision_time + policy.latency.scheduling
    arrival = decision_time + policy.latency.decision_to_arrival
    expiry = arrival + policy.max_rest
    queue = displayed_ahead + policy.competing_fill_reserve * displayed_ahead
    remaining, cumulative = quantity, Decimal(0)
    fills: list[SimulatedFill] = []
    cancel_requested: datetime | None = None
    cancel_effective: datetime | None = None
    invalidation: str | None = None
    seen_trade_keys: set[tuple[int, Decimal]] = set()
    for event in sorted(flows, key=lambda row: (row.timestamp, row.sequence)):
        if event.timestamp < arrival:
            continue
        if event.timestamp > expiry:
            break
        if event.kind in {
            FlowKind.GAP,
            FlowKind.PAUSE,
            FlowKind.RULE_CHANGE,
            FlowKind.SOURCE_FAILURE,
        }:
            cancel_requested = event.timestamp
            cancel_effective = event.timestamp + policy.latency.cancellation
            invalidation = event.kind
            if event.kind == FlowKind.GAP:
                return SimulatedOrder(
                    simulated_order_id,
                    candidate_id,
                    strategy,
                    policy.scenario,
                    candidate_time,
                    decision_time,
                    submit,
                    arrival,
                    quantity,
                    remaining,
                    price,
                    OrderState.EXECUTION_OUTCOME_UNKNOWN,
                    QueueQuality.CONSERVATIVE_QUEUE_ASSUMPTION,
                    queue,
                    tuple(fills),
                    cancel_requested,
                    cancel_effective,
                    "unresolved replay gap",
                )
            continue
        if cancel_effective is not None and event.timestamp >= cancel_effective:
            break
        if event.outcome_side != side or event.price != price:
            continue
        if event.kind == FlowKind.LEVEL_REDUCTION:
            # Aggregated reductions are ambiguous; only scenario-policy cancellation credit applies.
            queue = max(Decimal(0), queue - event.quantity * policy.cancellation_credit)
            continue
        key = (event.sequence, event.quantity)
        if event.kind != FlowKind.TRADE or key in seen_trade_keys:
            continue
        seen_trade_keys.add(key)
        flow = event.quantity
        queue_depletion = min(queue, flow)
        queue -= queue_depletion
        executable = flow - queue_depletion
        fill_quantity = min(remaining, executable)
        if fill_quantity <= 0:
            continue
        cumulative += fill_quantity
        remaining -= fill_quantity
        fee = calculate_fee(fee_policy, price, cumulative, maker=True).total_fee - sum(
            (fill.fee for fill in fills), Decimal(0)
        )
        fills.append(
            SimulatedFill(
                f"sim-fill-{simulated_order_id}-{len(fills) + 1}",
                simulated_order_id,
                event.timestamp,
                side,
                "BID",
                price,
                fill_quantity,
                True,
                queue + queue_depletion,
                event.event_id,
                fee,
                cumulative,
                remaining,
                event.book_lineage,
                event.sequence,
                policy.scenario,
            )
        )
        if remaining == 0:
            break
    if remaining == 0:
        state = OrderState.FILLED_SIMULATION
    elif fills:
        state = OrderState.PARTIALLY_FILLED_SIMULATION
    elif cancel_requested:
        state = OrderState.CANCELED_SIMULATION
    else:
        state = OrderState.NO_FILL_SIMULATION
    return SimulatedOrder(
        simulated_order_id,
        candidate_id,
        strategy,
        policy.scenario,
        candidate_time,
        decision_time,
        submit,
        arrival,
        quantity,
        remaining,
        price,
        state,
        QueueQuality.CONSERVATIVE_QUEUE_ASSUMPTION,
        queue,
        tuple(fills),
        cancel_requested,
        cancel_effective,
        invalidation,
    )


def expire_partial(order: SimulatedOrder) -> SimulatedOrder:
    """End a partial attempt without fabricating fills for its remainder."""
    if order.state != OrderState.PARTIALLY_FILLED_SIMULATION:
        return order
    return replace(order, state=OrderState.EXPIRED_SIMULATION)
