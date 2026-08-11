from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from services.demo_execution.domain import (
    ExecutionEnvironment,
    PaperOrder,
    PaperOrderState,
    require_nonproduction_environment,
)

NOW = datetime(2026, 8, 10, 12, tzinfo=UTC)


def order() -> PaperOrder:
    return PaperOrder(
        paper_order_id="paper-test-1",
        client_order_id="kalsh3-v1-demo-test",
        environment=ExecutionEnvironment.PAPER,
        intent_hash="intent-hash",
        quantity=Decimal("2.5"),
        filled_quantity=Decimal("0"),
        state=PaperOrderState.CREATED,
        updated_at=NOW,
    )


def test_m14_boundary_rejects_production() -> None:
    assert require_nonproduction_environment("demo") is ExecutionEnvironment.DEMO
    with pytest.raises(ValueError, match="DEMO, MOCK, or PAPER"):
        require_nonproduction_environment("production")


def test_unknown_submission_requires_reconciliation() -> None:
    pending = order().transition(PaperOrderState.SUBMISSION_PENDING, at=NOW)
    unknown = pending.transition(PaperOrderState.SUBMISSION_UNKNOWN, at=NOW)
    reconciled = unknown.transition(PaperOrderState.RECONCILIATION_REQUIRED, at=NOW)
    assert reconciled.state is PaperOrderState.RECONCILIATION_REQUIRED
    with pytest.raises(ValueError):
        unknown.transition(PaperOrderState.FILLED, at=NOW, filled_quantity=Decimal("2.5"))


def test_partial_fill_and_cancel_race_use_exact_quantities() -> None:
    resting = (
        order()
        .transition(PaperOrderState.SUBMISSION_PENDING, at=NOW)
        .transition(PaperOrderState.RESTING, at=NOW)
    )
    partial = resting.transition(
        PaperOrderState.PARTIALLY_FILLED, at=NOW, filled_quantity=Decimal("0.75")
    )
    cancel_pending = partial.transition(PaperOrderState.CANCEL_PENDING, at=NOW)
    filled_during_cancel = cancel_pending.transition(
        PaperOrderState.FILLED,
        at=NOW + timedelta(milliseconds=1),
        filled_quantity=Decimal("2.5"),
    )
    assert filled_during_cancel.filled_quantity == Decimal("2.5")
    assert filled_during_cancel.production_influence == "NONE"


def test_lifecycle_rejects_clock_regression_and_real_ids() -> None:
    with pytest.raises(ValueError, match="synthetic"):
        PaperOrder(
            paper_order_id="exchange-id",
            client_order_id="id",
            environment=ExecutionEnvironment.MOCK,
            intent_hash="hash",
            quantity=Decimal("1"),
            filled_quantity=Decimal("0"),
            state=PaperOrderState.CREATED,
            updated_at=NOW,
        )
    with pytest.raises(ValueError, match="clock regression"):
        order().transition(PaperOrderState.SUBMISSION_PENDING, at=NOW - timedelta(seconds=1))
