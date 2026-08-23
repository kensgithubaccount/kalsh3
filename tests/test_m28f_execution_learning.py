from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from services.production_weather_strategy.execution_learning import (
    ExecutionExpectation,
    ExecutionLabelState,
    ExecutionLearningError,
    build_execution_learning_observation,
    summarize_execution_learning,
)
from services.production_weather_strategy.historical_economics import TradeSide


def _expectation() -> ExecutionExpectation:
    return ExecutionExpectation.build(
        family="DAILY_TEMPERATURE",
        event_ticker="KXHIGHCHI-26AUG23",
        market_ticker="KXHIGHCHI-26AUG23-B76.5",
        side=TradeSide.NO,
        quantity=Decimal("1.00"),
        expected_taker_price=Decimal("0.5700"),
        expected_fee=Decimal("0.0172"),
        book_evidence_id="book-hash",
        economics_evidence_id="economics-hash",
        observed_at=datetime(2026, 8, 23, 3, 13, 46, tzinfo=UTC),
    )


def _reconciliation(
    classification: str,
    *,
    fill_quantity: str | None = None,
    fill_price: str | None = None,
    fee: str | None = None,
) -> dict[str, object]:
    return {
        "schema": "kalsh3.m27o.post-send-reconciliation.v1",
        "execution_id": f"exec-{classification}",
        "client_order_id": f"client-{classification}",
        "classification": classification,
        "filled_quantity": fill_quantity,
        "maximum_fill_price": fill_price,
        "total_fee": fee,
        "terminal_state": "CANARY_COMPLETE" if classification != "UNKNOWN" else "RECONCILING",
        "observed_at": "2026-08-23T03:13:47+00:00",
        "completed_at": "2026-08-23T03:13:48.500000+00:00",
        "content_hash": f"recon-{classification}",
    }


def test_filled_reconciliation_derives_slippage_and_fee_error() -> None:
    row = build_execution_learning_observation(
        _expectation(),
        _reconciliation(
            "FILLED",
            fill_quantity="1.00",
            fill_price="0.5800",
            fee="0.0170",
        ),
    )
    assert row.state is ExecutionLabelState.FILLED
    assert row.fill_label == 1
    assert row.price_slippage == Decimal("0.0100")
    assert row.fee_error == Decimal("-0.0002")
    assert row.actual_all_in_cost == Decimal("0.597000")
    assert row.reconciliation_latency_seconds == Decimal("1.5")


def test_no_fill_is_a_zero_label_without_execution_economics() -> None:
    row = build_execution_learning_observation(
        _expectation(),
        _reconciliation("NO_FILL", fill_quantity="0.00"),
    )
    assert row.fill_label == 0
    assert row.actual_fill_price is None
    assert row.price_slippage is None


def test_unknown_is_preserved_and_never_labeled_no_fill() -> None:
    row = build_execution_learning_observation(
        _expectation(),
        _reconciliation("UNKNOWN"),
    )
    assert row.state is ExecutionLabelState.UNKNOWN
    assert row.fill_label is None


def test_filled_quantity_mismatch_fails_closed() -> None:
    with pytest.raises(ExecutionLearningError, match="quantity differs"):
        build_execution_learning_observation(
            _expectation(),
            _reconciliation(
                "FILLED",
                fill_quantity="0.50",
                fill_price="0.5800",
                fee="0.0100",
            ),
        )


def test_no_fill_with_execution_price_fails_closed() -> None:
    with pytest.raises(ExecutionLearningError, match="execution economics"):
        build_execution_learning_observation(
            _expectation(),
            _reconciliation("NO_FILL", fill_quantity="0.00", fill_price="0.57"),
        )


def test_summary_excludes_unknown_from_supervised_fill_rate() -> None:
    expectation = _expectation()
    filled = build_execution_learning_observation(
        expectation,
        _reconciliation(
            "FILLED",
            fill_quantity="1.00",
            fill_price="0.5800",
            fee="0.0170",
        ),
    )
    no_fill_payload = _reconciliation("NO_FILL", fill_quantity="0.00")
    no_fill_payload["execution_id"] = "exec-no-fill-2"
    no_fill_payload["client_order_id"] = "client-no-fill-2"
    no_fill_payload["content_hash"] = "recon-no-fill-2"
    no_fill = build_execution_learning_observation(expectation, no_fill_payload)
    unknown_payload = _reconciliation("UNKNOWN")
    unknown_payload["execution_id"] = "exec-unknown-2"
    unknown_payload["client_order_id"] = "client-unknown-2"
    unknown_payload["content_hash"] = "recon-unknown-2"
    unknown = build_execution_learning_observation(expectation, unknown_payload)

    summary = summarize_execution_learning((filled, no_fill, unknown))
    assert summary.supervised_observations == 2
    assert summary.unknown_observations == 1
    assert summary.fill_rate == Decimal("0.5")
    assert summary.mean_price_slippage == Decimal("0.0100")
    assert summary.mean_fee_error == Decimal("-0.0002")


def test_reconciliation_cannot_predate_expectation() -> None:
    payload = _reconciliation("UNKNOWN")
    payload["observed_at"] = "2026-08-23T03:13:45+00:00"
    with pytest.raises(ExecutionLearningError, match="predates"):
        build_execution_learning_observation(_expectation(), payload)
