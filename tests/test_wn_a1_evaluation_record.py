from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from services.forecasting.wn_a1_alert import AlertDecision
from services.forecasting.wn_a1_domain import AlertState, WnA1Error
from services.forecasting.wn_a1_evaluation_record import EvaluationRecord, replay_record_id


def decision(state: AlertState = AlertState.TAKE_A_LOOK) -> AlertDecision:
    return AlertDecision(
        state=state,
        gate_failures=(),
        raw_probability=Decimal("0.42"),
        market_yes_probability=Decimal("0.27"),
        gap_pp=Decimal("15"),
        buffered_gap_pp=Decimal("10"),
        policy_version="wn-a1-alert-policy-v1",
    )


def test_record_is_deterministic_across_fresh_construction() -> None:
    at = datetime(2026, 9, 15, 2, 0, tzinfo=UTC)
    first = EvaluationRecord.create(
        market_ticker="KXHIGHCHI-26SEP15-T87",
        target_local_date=date(2026, 9, 15),
        weathernext_evidence_identity="wn-hash",
        kalshi_snapshot_identity="kalshi-hash",
        contract_policy_identity="contract-policy-hash",
        decision=decision(),
        evaluated_at=at,
    )
    second = EvaluationRecord.create(
        market_ticker="KXHIGHCHI-26SEP15-T87",
        target_local_date=date(2026, 9, 15),
        weathernext_evidence_identity="wn-hash",
        kalshi_snapshot_identity="kalshi-hash",
        contract_policy_identity="contract-policy-hash",
        decision=decision(),
        evaluated_at=at,
    )
    assert first.record_id == second.record_id


def test_fresh_process_replay_reproduces_record_id() -> None:
    """Simulates a fresh process: rebuild `record_id` purely from persisted fields."""
    record = EvaluationRecord.create(
        market_ticker="KXHIGHCHI-26SEP15-T87",
        target_local_date=date(2026, 9, 15),
        weathernext_evidence_identity="wn-hash",
        kalshi_snapshot_identity="kalshi-hash",
        contract_policy_identity="contract-policy-hash",
        decision=decision(),
        evaluated_at=datetime(2026, 9, 15, 2, 0, tzinfo=UTC),
    )
    # Round-trip through a plain dict, as a persistence layer would.
    persisted = {
        "market_ticker": record.market_ticker,
        "target_local_date": record.target_local_date,
        "weathernext_evidence_identity": record.weathernext_evidence_identity,
        "kalshi_snapshot_identity": record.kalshi_snapshot_identity,
        "contract_policy_identity": record.contract_policy_identity,
        "contract_policy_version": record.contract_policy_version,
        "alert_policy_version": record.alert_policy_version,
        "decision_state": record.decision_state,
        "decision_gate_failures": record.decision_gate_failures,
        "raw_probability": record.raw_probability,
        "market_yes_probability": record.market_yes_probability,
        "gap_pp": record.gap_pp,
        "buffered_gap_pp": record.buffered_gap_pp,
        "evaluated_at": record.evaluated_at,
    }
    rehydrated = EvaluationRecord(record_id=record.record_id, **persisted)
    assert replay_record_id(rehydrated) == record.record_id


def test_every_evaluated_outcome_is_recorded_including_skip_and_too_uncertain() -> None:
    skip_record = EvaluationRecord.create(
        market_ticker="KXHIGHCHI-26SEP15-T80",
        target_local_date=date(2026, 9, 15),
        weathernext_evidence_identity="wn-hash",
        kalshi_snapshot_identity="kalshi-hash",
        contract_policy_identity="contract-policy-hash",
        decision=decision(AlertState.SKIP),
        evaluated_at=datetime(2026, 9, 15, 2, 0, tzinfo=UTC),
    )
    too_uncertain_record = EvaluationRecord.create(
        market_ticker="KXHIGHCHI-26SEP15-B84.5",
        target_local_date=date(2026, 9, 15),
        weathernext_evidence_identity="wn-hash",
        kalshi_snapshot_identity="kalshi-hash",
        contract_policy_identity="contract-policy-hash",
        decision=decision(AlertState.TOO_UNCERTAIN),
        evaluated_at=datetime(2026, 9, 15, 2, 0, tzinfo=UTC),
    )
    assert skip_record.decision_state == "SKIP"
    assert too_uncertain_record.decision_state == "TOO UNCERTAIN"
    assert skip_record.record_id != too_uncertain_record.record_id


def test_naive_timestamp_rejected() -> None:
    with pytest.raises(WnA1Error):
        EvaluationRecord.create(
            market_ticker="KXHIGHCHI-26SEP15-T87",
            target_local_date=date(2026, 9, 15),
            weathernext_evidence_identity=None,
            kalshi_snapshot_identity=None,
            contract_policy_identity="contract-policy-hash",
            decision=decision(),
            evaluated_at=datetime(2026, 9, 15, 2, 0),  # naive
        )


def test_zero_production_influence_and_research_only() -> None:
    record = EvaluationRecord.create(
        market_ticker="KXHIGHCHI-26SEP15-T87",
        target_local_date=date(2026, 9, 15),
        weathernext_evidence_identity=None,
        kalshi_snapshot_identity=None,
        contract_policy_identity="contract-policy-hash",
        decision=decision(),
        evaluated_at=datetime(2026, 9, 15, 2, 0, tzinfo=UTC),
    )
    assert record.production_influence == Decimal(0)
    assert record.research_only is True
