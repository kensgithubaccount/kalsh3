from __future__ import annotations

import hashlib
import json
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


def _rehash(payload: dict[str, object]) -> dict[str, object]:
    material = {key: value for key, value in payload.items() if key != "content_hash"}
    payload["content_hash"] = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    return payload


def _reconciliation(
    classification: str,
    *,
    fill_quantity: str | None = None,
    fill_price: str | None = None,
    fee: str | None = None,
) -> dict[str, object]:
    if classification == "FILLED_POLICY_VIOLATION":
        terminal_state = "CANARY_FAILED"
        reconciliation_required = False
    elif classification in {"FILLED", "NO_FILL"}:
        terminal_state = "CANARY_COMPLETE"
        reconciliation_required = False
    else:
        terminal_state = "SUBMITTED_OR_UNKNOWN"
        reconciliation_required = True
    payload: dict[str, object] = {
        "schema": "kalsh3.m27o.post-send-reconciliation.v1",
        "software_version": "kalsh3.m27o.post-send-reconciliation/1",
        "observed_at": "2026-08-23T03:13:47+00:00",
        "completed_at": "2026-08-23T03:13:48.500000+00:00",
        "classification": classification,
        "reason": (
            "authenticated fill exceeded release-bound price or fee ceiling"
            if classification == "FILLED_POLICY_VIOLATION"
            else None
        ),
        "execution_id": f"exec-{classification}",
        "session_id": "session-1",
        "client_order_id": f"client-{classification}",
        "order_id": None if classification == "UNKNOWN" else "order-1",
        "order_status": (
            "executed"
            if classification in {"FILLED", "FILLED_POLICY_VIOLATION"}
            else "canceled" if classification == "NO_FILL" else None
        ),
        "filled_quantity": fill_quantity,
        "maximum_fill_price": fill_price,
        "total_fee": fee,
        "orders_sha256": "orders-hash",
        "fills_sha256": "fills-hash",
        "positions_sha256": "positions-hash",
        "terminal_state": terminal_state,
        "reconciliation_required": reconciliation_required,
    }
    return _rehash(payload)


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


def test_policy_violation_fill_is_preserved_as_supervised_adverse_execution() -> None:
    row = build_execution_learning_observation(
        _expectation(),
        _reconciliation(
            "FILLED_POLICY_VIOLATION",
            fill_quantity="1.00",
            fill_price="0.6000",
            fee="0.0200",
        ),
    )
    assert row.state is ExecutionLabelState.FILLED_POLICY_VIOLATION
    assert row.fill_label == 1
    assert row.price_slippage == Decimal("0.0300")
    assert row.fee_error == Decimal("0.0028")
    assert row.terminal_state == "CANARY_FAILED"


def test_no_fill_is_a_zero_label_without_execution_economics() -> None:
    row = build_execution_learning_observation(
        _expectation(),
        _reconciliation("NO_FILL", fill_quantity="0.00", fee="0"),
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


def test_tampered_reconciliation_hash_fails_closed() -> None:
    payload = _reconciliation("UNKNOWN")
    payload["terminal_state"] = "CANARY_COMPLETE"
    with pytest.raises(ExecutionLearningError, match="content hash mismatch"):
        build_execution_learning_observation(_expectation(), payload)


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
            _reconciliation(
                "NO_FILL",
                fill_quantity="0.00",
                fill_price="0.57",
                fee="0",
            ),
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
    no_fill_payload = _reconciliation("NO_FILL", fill_quantity="0.00", fee="0")
    no_fill_payload["execution_id"] = "exec-no-fill-2"
    no_fill_payload["client_order_id"] = "client-no-fill-2"
    _rehash(no_fill_payload)
    no_fill = build_execution_learning_observation(expectation, no_fill_payload)
    unknown_payload = _reconciliation("UNKNOWN")
    unknown_payload["execution_id"] = "exec-unknown-2"
    unknown_payload["client_order_id"] = "client-unknown-2"
    _rehash(unknown_payload)
    unknown = build_execution_learning_observation(expectation, unknown_payload)

    summary = summarize_execution_learning((filled, no_fill, unknown))
    assert summary.supervised_observations == 2
    assert summary.unknown_observations == 1
    assert summary.fill_rate == Decimal("0.5")
    assert summary.mean_price_slippage == Decimal("0.0100")
    assert summary.mean_fee_error == Decimal("-0.0002")
    assert summary.policy_violation_fill_observations == 0


def test_reconciliation_cannot_predate_expectation() -> None:
    payload = _reconciliation("UNKNOWN")
    payload["observed_at"] = "2026-08-23T03:13:45+00:00"
    _rehash(payload)
    with pytest.raises(ExecutionLearningError, match="predates"):
        build_execution_learning_observation(_expectation(), payload)
