"""Auditable optimistic/base/adverse policy fixtures and fidelity gates."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from .domain import (
    ExecutionAssumptionPolicy,
    LatencyPolicy,
    ReplayFidelity,
    SimulationCase,
    StrategyType,
)


def default_policies(effective_at: datetime | None = None) -> tuple[ExecutionAssumptionPolicy, ...]:
    at = effective_at or datetime(2026, 1, 1, tzinfo=UTC)
    rows = (
        (SimulationCase.OPTIMISTIC, 70, Decimal(".50"), Decimal(".02"), Decimal(".002")),
        (SimulationCase.BASE, 300, Decimal(".15"), Decimal(".10"), Decimal(".008")),
        (SimulationCase.ADVERSE, 850, Decimal("0"), Decimal(".25"), Decimal(".02")),
    )
    output = []
    for case, arrival_ms, cancel_credit, competing, adverse in rows:
        latency = LatencyPolicy(
            f"latency-{case.lower()}-v1",
            "1",
            timedelta(milliseconds=250),
            timedelta(milliseconds=100),
            timedelta(milliseconds=20),
            timedelta(milliseconds=10),
            timedelta(milliseconds=arrival_ms - 30),
            timedelta(milliseconds=20),
            timedelta(milliseconds=max(100, arrival_ms)),
            "SIMULATION_ASSUMPTION",
            False,
        )
        output.append(
            ExecutionAssumptionPolicy.freeze(
                policy_id=f"execution-{case.lower()}-v1",
                version="1",
                scenario=case,
                effective_at=at,
                latency=latency,
                cancellation_credit=cancel_credit,
                competing_fill_reserve=competing,
                adverse_selection_reserve=adverse,
                max_rest=timedelta(seconds=30),
                required_fidelity=ReplayFidelity.SEQUENCE_BOOK_AND_TRADES,
                fee_policy_ids=("fixture-fee-v1",),
            )
        )
    return tuple(output)


def fidelity_allows(strategy: StrategyType, fidelity: ReplayFidelity) -> bool:
    if fidelity in {ReplayFidelity.GAP, ReplayFidelity.CANDLE_ONLY}:
        return False
    if strategy != StrategyType.TAKER_NOW:
        return fidelity == ReplayFidelity.SEQUENCE_BOOK_AND_TRADES
    return fidelity in {
        ReplayFidelity.SEQUENCE_BOOK_AND_TRADES,
        ReplayFidelity.HIGH_RESOLUTION_BOOK,
    }
