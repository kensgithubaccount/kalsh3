"""Pure edge-decay measurement for shadow research."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from .domain import (
    Direction,
    EdgeDecayObservation,
    ShadowResearchError,
    _edge,
    _milliseconds,
    _utc,
)


def measure_edge_decay(
    *,
    ticker: str,
    direction: Direction,
    exchange_index: int,
    signal_created_at: datetime,
    signal_available_at: datetime,
    decision_at: datetime,
    hypothetical_send_at: datetime,
    signal_value: Decimal,
    value_at_creation: Decimal,
    value_at_available: Decimal,
    value_at_decision: Decimal,
    value_at_hypothetical_send: Decimal,
    artifact_id: str | None = None,
) -> EdgeDecayObservation:
    """Measure how much apparent edge survives each latency stage.

    `signal_value` is the research model's contemporaneous fair/reference value.
    The other values are observed executable/reference values captured by the
    caller. This function never creates an order or chooses a trade size.
    """
    if not ticker:
        raise ShadowResearchError("ticker is required")
    if not isinstance(direction, Direction):
        raise ShadowResearchError("direction must be a Direction")

    signal_created_at = _utc(signal_created_at, "signal_created_at")
    signal_available_at = _utc(signal_available_at, "signal_available_at")
    decision_at = _utc(decision_at, "decision_at")
    hypothetical_send_at = _utc(hypothetical_send_at, "hypothetical_send_at")

    return EdgeDecayObservation(
        artifact_id=artifact_id or f"edge-decay-{uuid4()}",
        ticker=ticker,
        direction=direction,
        exchange_index=exchange_index,
        signal_created_at=signal_created_at,
        signal_available_at=signal_available_at,
        decision_at=decision_at,
        hypothetical_send_at=hypothetical_send_at,
        signal_value=signal_value,
        value_at_creation=value_at_creation,
        value_at_available=value_at_available,
        value_at_decision=value_at_decision,
        value_at_hypothetical_send=value_at_hypothetical_send,
        initial_edge=_edge(signal_value, value_at_creation, direction),
        available_edge=_edge(signal_value, value_at_available, direction),
        decision_edge=_edge(signal_value, value_at_decision, direction),
        send_edge=_edge(signal_value, value_at_hypothetical_send, direction),
        publication_to_available_ms=_milliseconds(signal_created_at, signal_available_at),
        available_to_decision_ms=_milliseconds(signal_available_at, decision_at),
        decision_to_send_ms=_milliseconds(decision_at, hypothetical_send_at),
    )
