"""Pure execution-learning evidence derived from completed production reconciliations.

M28 needs a separate execution model from its predictive model. This module turns already-
produced reconciliation facts into immutable labels for fill probability, price slippage,
fee error, and reconciliation latency. It never reads credentials, calls Kalshi, mutates
production state, authorizes risk, approves execution, burns authority, or sends orders.

Only unambiguous FILLED, FILLED_POLICY_VIOLATION, and NO_FILL reconciliations become
supervised execution labels. UNKNOWN remains preserved as an unresolved observation and is
never silently interpreted as an unfilled order.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from services.historical_replay.archive import stable_hash
from services.production_weather_strategy.historical_economics import TradeSide


class ExecutionLearningError(ValueError):
    """Execution-learning evidence violates an identity, timing, or numeric invariant."""


class ExecutionLabelState(StrEnum):
    FILLED = "FILLED"
    FILLED_POLICY_VIOLATION = "FILLED_POLICY_VIOLATION"
    NO_FILL = "NO_FILL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ExecutionExpectation:
    """What the strategy believed execution would cost immediately before submission."""

    expectation_id: str
    family: str
    event_ticker: str
    market_ticker: str
    side: TradeSide
    quantity: Decimal
    expected_taker_price: Decimal
    expected_fee: Decimal
    expected_all_in_cost: Decimal
    book_evidence_id: str
    economics_evidence_id: str
    observed_at: datetime
    content_hash: str

    @classmethod
    def build(
        cls,
        *,
        family: str,
        event_ticker: str,
        market_ticker: str,
        side: TradeSide,
        quantity: Decimal,
        expected_taker_price: Decimal,
        expected_fee: Decimal,
        book_evidence_id: str,
        economics_evidence_id: str,
        observed_at: datetime,
    ) -> ExecutionExpectation:
        for value, name in (
            (family, "family"),
            (event_ticker, "event ticker"),
            (market_ticker, "market ticker"),
            (book_evidence_id, "book evidence id"),
            (economics_evidence_id, "economics evidence id"),
        ):
            if not value.strip():
                raise ExecutionLearningError(f"{name} is required")
        if quantity <= 0 or not quantity.is_finite():
            raise ExecutionLearningError("execution quantity must be positive and finite")
        for value, name in (
            (expected_taker_price, "expected taker price"),
            (expected_fee, "expected fee"),
        ):
            if value < 0 or not value.is_finite():
                raise ExecutionLearningError(f"{name} is invalid")
        if not Decimal("0") < expected_taker_price < Decimal("1"):
            raise ExecutionLearningError("expected taker price must be inside (0,1)")
        expected_all_in = expected_taker_price * quantity + expected_fee
        if expected_all_in <= 0 or expected_all_in > quantity:
            raise ExecutionLearningError("expected all-in cost is outside payout bounds")
        observed = _utc(observed_at, "expectation observed_at")
        material = (
            "m28f-execution-expectation-v1",
            family,
            event_ticker,
            market_ticker,
            side.value,
            str(quantity),
            str(expected_taker_price),
            str(expected_fee),
            str(expected_all_in),
            book_evidence_id,
            economics_evidence_id,
            observed.isoformat(),
        )
        digest = stable_hash(material)
        return cls(
            expectation_id=digest,
            family=family,
            event_ticker=event_ticker,
            market_ticker=market_ticker,
            side=side,
            quantity=quantity,
            expected_taker_price=expected_taker_price,
            expected_fee=expected_fee,
            expected_all_in_cost=expected_all_in,
            book_evidence_id=book_evidence_id,
            economics_evidence_id=economics_evidence_id,
            observed_at=observed,
            content_hash=digest,
        )


@dataclass(frozen=True, slots=True)
class ExecutionLearningObservation:
    """One immutable fill/no-fill/unknown label tied to a pre-submit expectation."""

    observation_id: str
    expectation_id: str
    family: str
    event_ticker: str
    market_ticker: str
    execution_id: str
    client_order_id: str
    state: ExecutionLabelState
    fill_label: int | None
    actual_fill_price: Decimal | None
    actual_fee: Decimal | None
    price_slippage: Decimal | None
    fee_error: Decimal | None
    actual_all_in_cost: Decimal | None
    reconciliation_latency_seconds: Decimal
    reconciliation_content_hash: str
    terminal_state: str
    observed_at: datetime
    completed_at: datetime
    content_hash: str


def build_execution_learning_observation(
    expectation: ExecutionExpectation,
    reconciliation: Mapping[str, object],
) -> ExecutionLearningObservation:
    """Validate one secret-free M27O reconciliation payload and derive execution labels."""

    if reconciliation.get("schema") != "kalsh3.m27o.post-send-reconciliation.v1":
        raise ExecutionLearningError("unsupported reconciliation schema")
    _verify_reconciliation_hash(reconciliation)
    execution_id = _text(reconciliation.get("execution_id"), "execution id")
    client_order_id = _text(reconciliation.get("client_order_id"), "client order id")
    content_hash = _text(reconciliation.get("content_hash"), "reconciliation content hash")
    terminal_state = _text(reconciliation.get("terminal_state"), "terminal state")
    observed = _timestamp(reconciliation.get("observed_at"), "reconciliation observed_at")
    completed = _timestamp(reconciliation.get("completed_at"), "reconciliation completed_at")
    if completed < observed:
        raise ExecutionLearningError("reconciliation completed before observation")
    if observed < expectation.observed_at:
        raise ExecutionLearningError("reconciliation predates its execution expectation")
    latency = Decimal(str((completed - observed).total_seconds()))

    classification_raw = _text(reconciliation.get("classification"), "classification")
    try:
        state = ExecutionLabelState(classification_raw)
    except ValueError as exc:
        raise ExecutionLearningError("unsupported reconciliation classification") from exc

    reconciliation_required = reconciliation.get("reconciliation_required")
    if not isinstance(reconciliation_required, bool):
        raise ExecutionLearningError("reconciliation_required is malformed")
    if state is ExecutionLabelState.UNKNOWN:
        if not reconciliation_required:
            raise ExecutionLearningError(
                "UNKNOWN reconciliation must remain reconciliation-required"
            )
    elif reconciliation_required:
        raise ExecutionLearningError(
            "terminal reconciliation cannot remain reconciliation-required"
        )

    filled_quantity = _optional_decimal(reconciliation.get("filled_quantity"), "filled quantity")
    fill_price = _optional_decimal(reconciliation.get("maximum_fill_price"), "maximum fill price")
    actual_fee = _optional_decimal(reconciliation.get("total_fee"), "total fee")

    fill_label: int | None
    price_slippage: Decimal | None = None
    fee_error: Decimal | None = None
    actual_all_in: Decimal | None = None
    filled_state = state in {
        ExecutionLabelState.FILLED,
        ExecutionLabelState.FILLED_POLICY_VIOLATION,
    }
    if filled_state:
        fill_label = 1
        if filled_quantity != expectation.quantity:
            raise ExecutionLearningError("filled reconciliation quantity differs from expectation")
        if fill_price is None or actual_fee is None:
            raise ExecutionLearningError("filled reconciliation lacks price or fee")
        if not Decimal("0") < fill_price < Decimal("1"):
            raise ExecutionLearningError("actual fill price is outside (0,1)")
        if actual_fee < 0:
            raise ExecutionLearningError("actual fee is negative")
        price_slippage = fill_price - expectation.expected_taker_price
        fee_error = actual_fee - expectation.expected_fee
        actual_all_in = fill_price * expectation.quantity + actual_fee
        if actual_all_in <= 0 or actual_all_in > expectation.quantity:
            raise ExecutionLearningError("actual all-in cost is outside payout bounds")
        if state is ExecutionLabelState.FILLED and terminal_state != "CANARY_COMPLETE":
            raise ExecutionLearningError("FILLED reconciliation has unexpected terminal state")
        if (
            state is ExecutionLabelState.FILLED_POLICY_VIOLATION
            and terminal_state != "CANARY_FAILED"
        ):
            raise ExecutionLearningError("policy-violation fill has unexpected terminal state")
    elif state is ExecutionLabelState.NO_FILL:
        fill_label = 0
        if terminal_state != "CANARY_COMPLETE":
            raise ExecutionLearningError("NO_FILL reconciliation has unexpected terminal state")
        if filled_quantity not in {None, Decimal("0"), Decimal("0.00")}:
            raise ExecutionLearningError("NO_FILL reconciliation reports nonzero filled quantity")
        if fill_price is not None or (actual_fee is not None and actual_fee != 0):
            raise ExecutionLearningError("NO_FILL reconciliation reports execution economics")
    else:
        fill_label = None
        if terminal_state != "SUBMITTED_OR_UNKNOWN":
            raise ExecutionLearningError("UNKNOWN reconciliation has unexpected terminal state")
        # UNKNOWN evidence is deliberately preserved without learning a false no-fill label.

    material = (
        "m28f-execution-learning-observation-v1",
        expectation.expectation_id,
        execution_id,
        client_order_id,
        state.value,
        fill_label,
        None if fill_price is None else str(fill_price),
        None if actual_fee is None else str(actual_fee),
        None if price_slippage is None else str(price_slippage),
        None if fee_error is None else str(fee_error),
        None if actual_all_in is None else str(actual_all_in),
        str(latency),
        content_hash,
        terminal_state,
        observed.isoformat(),
        completed.isoformat(),
    )
    digest = stable_hash(material)
    return ExecutionLearningObservation(
        observation_id=digest,
        expectation_id=expectation.expectation_id,
        family=expectation.family,
        event_ticker=expectation.event_ticker,
        market_ticker=expectation.market_ticker,
        execution_id=execution_id,
        client_order_id=client_order_id,
        state=state,
        fill_label=fill_label,
        actual_fill_price=fill_price,
        actual_fee=actual_fee,
        price_slippage=price_slippage,
        fee_error=fee_error,
        actual_all_in_cost=actual_all_in,
        reconciliation_latency_seconds=latency,
        reconciliation_content_hash=content_hash,
        terminal_state=terminal_state,
        observed_at=observed,
        completed_at=completed,
        content_hash=digest,
    )


@dataclass(frozen=True, slots=True)
class ExecutionLearningSummary:
    summary_id: str
    total_observations: int
    supervised_observations: int
    unknown_observations: int
    filled_observations: int
    policy_violation_fill_observations: int
    no_fill_observations: int
    unique_events: int
    fill_rate: Decimal | None
    mean_price_slippage: Decimal | None
    mean_fee_error: Decimal | None
    mean_reconciliation_latency_seconds: Decimal
    content_hash: str


def summarize_execution_learning(
    observations: tuple[ExecutionLearningObservation, ...],
) -> ExecutionLearningSummary:
    """Summarize unambiguous execution labels without converting UNKNOWN to NO_FILL."""

    if not observations:
        raise ExecutionLearningError("execution learning summary cannot be empty")
    if len({row.observation_id for row in observations}) != len(observations):
        raise ExecutionLearningError("duplicate execution learning observation")
    supervised = [row for row in observations if row.fill_label is not None]
    unknown = [row for row in observations if row.state is ExecutionLabelState.UNKNOWN]
    filled = [
        row
        for row in observations
        if row.state in {ExecutionLabelState.FILLED, ExecutionLabelState.FILLED_POLICY_VIOLATION}
    ]
    policy_violations = [
        row for row in observations if row.state is ExecutionLabelState.FILLED_POLICY_VIOLATION
    ]
    no_fill = [row for row in observations if row.state is ExecutionLabelState.NO_FILL]
    fill_rate = (
        None
        if not supervised
        else Decimal(sum(row.fill_label or 0 for row in supervised)) / Decimal(len(supervised))
    )
    price_slippage = _mean_optional([row.price_slippage for row in filled])
    fee_error = _mean_optional([row.fee_error for row in filled])
    mean_latency = sum(
        (row.reconciliation_latency_seconds for row in observations), Decimal("0")
    ) / Decimal(len(observations))
    event_count = len({row.event_ticker for row in observations})
    ordered_ids = tuple(sorted(row.observation_id for row in observations))
    material = (
        "m28f-execution-learning-summary-v1",
        ordered_ids,
        len(supervised),
        len(unknown),
        len(filled),
        len(policy_violations),
        len(no_fill),
        event_count,
        None if fill_rate is None else str(fill_rate),
        None if price_slippage is None else str(price_slippage),
        None if fee_error is None else str(fee_error),
        str(mean_latency),
    )
    digest = stable_hash(material)
    return ExecutionLearningSummary(
        summary_id=digest,
        total_observations=len(observations),
        supervised_observations=len(supervised),
        unknown_observations=len(unknown),
        filled_observations=len(filled),
        policy_violation_fill_observations=len(policy_violations),
        no_fill_observations=len(no_fill),
        unique_events=event_count,
        fill_rate=fill_rate,
        mean_price_slippage=price_slippage,
        mean_fee_error=fee_error,
        mean_reconciliation_latency_seconds=mean_latency,
        content_hash=digest,
    )


def _verify_reconciliation_hash(reconciliation: Mapping[str, object]) -> None:
    fields = (
        "schema",
        "software_version",
        "observed_at",
        "completed_at",
        "classification",
        "reason",
        "execution_id",
        "session_id",
        "client_order_id",
        "order_id",
        "order_status",
        "filled_quantity",
        "maximum_fill_price",
        "total_fee",
        "orders_sha256",
        "fills_sha256",
        "positions_sha256",
        "terminal_state",
        "reconciliation_required",
    )
    if any(field not in reconciliation for field in fields):
        raise ExecutionLearningError("reconciliation hash material is incomplete")
    material = {field: reconciliation[field] for field in fields}
    encoded = json.dumps(
        material,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    expected = hashlib.sha256(encoded).hexdigest()
    actual = reconciliation.get("content_hash")
    if not isinstance(actual, str) or actual != expected:
        raise ExecutionLearningError("reconciliation content hash mismatch")


def _mean_optional(values: list[Decimal | None]) -> Decimal | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return sum(present, Decimal("0")) / Decimal(len(present))


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExecutionLearningError(f"{field} is missing or malformed")
    return value


def _optional_decimal(value: object, field: str) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, (str, int, float, Decimal)) or isinstance(value, bool):
        raise ExecutionLearningError(f"{field} is malformed")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ExecutionLearningError(f"{field} is malformed") from exc
    if not parsed.is_finite():
        raise ExecutionLearningError(f"{field} is malformed")
    return parsed


def _timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ExecutionLearningError(f"{field} is missing or malformed")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExecutionLearningError(f"{field} is missing or malformed") from exc
    return _utc(parsed, field)


def _utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ExecutionLearningError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)
