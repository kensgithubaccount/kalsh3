"""M27N.1 -- OFFLINE evidence adapter tests.

Focused on the adapter's own charter: turning persisted JSON evidence into the exact fixture
types ``services.supervised_canary.m27n_weather_rehearsal.build_rehearsal`` requires, failing
closed (raising :class:`AdapterError`) on anything tampered, malformed, mismatched, or stale.
Does not attempt a full end-to-end ``REHEARSAL_READY`` run: composing a complete
``candidate_inputs`` triple is the module's own disclosed out-of-scope gap (see
``m27n1_evidence_adapter``'s module docstring), not something these tests should paper over by
hand-building a parallel path around it.
"""

from __future__ import annotations

import ast
import dataclasses
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

import pytest

from services.market_universe.domain import Market
from services.risk_engine.authorization import AuthorizationState, RiskAuthorization
from services.risk_engine.domain import (
    EconomicAction,
    PortfolioRiskSnapshot,
    ReconciliationStatus,
    RiskDecision,
    RiskDecisionState,
    RiskIntent,
)
from services.risk_engine.domain import content_hash as risk_content_hash
from services.supervised_canary.m27n1_evidence_adapter import (
    AdapterError,
    build_account_snapshot_fixture,
    build_candidate_exposure_fixture,
    build_m13_fixture,
    build_rules_identity_fixture,
    build_submission_budget_fixture,
    run_m27n1_rehearsal,
)
from tests.test_m27i_live_weather_preflight import (
    _exposure,
    _m27f_payload,
    _m27h_payload,
    _raw_market,
    _snapshot_payload,
)

NOW = datetime(2026, 8, 19, 3, 0, 0, tzinfo=UTC)


def _value_to_json(value: object) -> object:
    if isinstance(value, bool):
        return value
    if value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, tuple):
        return [_value_to_json(v) for v in value]
    raise TypeError(f"unsupported type for test serialization: {type(value)!r}")


def _dataclass_to_json(obj: object) -> dict[str, object]:
    return {f.name: _value_to_json(getattr(obj, f.name)) for f in dataclasses.fields(obj)}


# ---------------------------------------------------------------------------
# Rules identity
# ---------------------------------------------------------------------------


def test_rules_identity_fixture_happy_path() -> None:
    raw_market = _raw_market("M", "E")
    expected_rules_hash = Market.parse(raw_market).rules_hash
    payload = _snapshot_payload(NOW, ticker="M", raw_market=raw_market, observed_at=NOW)

    fixture = build_rules_identity_fixture(
        payload,
        expected_market_ticker="M",
        expected_event_ticker="E",
        expected_rules_hash=expected_rules_hash,
        now=NOW,
    )

    assert fixture.market_ticker == "M"
    assert fixture.event_ticker == "E"
    assert fixture.current_rules_hash == expected_rules_hash
    assert fixture.expected_rules_hash == expected_rules_hash
    assert fixture.observed_at == NOW


def test_rules_identity_fixture_rejects_hash_mismatch() -> None:
    raw_market = _raw_market("M", "E")
    payload = _snapshot_payload(NOW, ticker="M", raw_market=raw_market, observed_at=NOW)

    with pytest.raises(AdapterError, match="did not validate"):
        build_rules_identity_fixture(
            payload,
            expected_market_ticker="M",
            expected_event_ticker="E",
            expected_rules_hash="not-the-real-hash",
            now=NOW,
        )


def test_rules_identity_fixture_rejects_stale_evidence() -> None:
    raw_market = _raw_market("M", "E")
    expected_rules_hash = Market.parse(raw_market).rules_hash
    observed_at = NOW - timedelta(minutes=5)
    payload = _snapshot_payload(NOW, ticker="M", raw_market=raw_market, observed_at=observed_at)

    with pytest.raises(AdapterError, match="did not validate"):
        build_rules_identity_fixture(
            payload,
            expected_market_ticker="M",
            expected_event_ticker="E",
            expected_rules_hash=expected_rules_hash,
            now=NOW,
        )


def test_rules_identity_fixture_rejects_wrong_ticker_binding() -> None:
    raw_market = _raw_market("M", "E")
    expected_rules_hash = Market.parse(raw_market).rules_hash
    payload = _snapshot_payload(NOW, ticker="M", raw_market=raw_market, observed_at=NOW)

    with pytest.raises(AdapterError, match="did not validate"):
        build_rules_identity_fixture(
            payload,
            expected_market_ticker="OTHER-TICKER",
            expected_event_ticker="E",
            expected_rules_hash=expected_rules_hash,
            now=NOW,
        )


def test_rules_identity_fixture_rejects_tampered_raw_body() -> None:
    raw_market = _raw_market("M", "E")
    expected_rules_hash = Market.parse(raw_market).rules_hash
    payload = dict(_snapshot_payload(NOW, ticker="M", raw_market=raw_market, observed_at=NOW))
    payload["rules_hash"] = expected_rules_hash  # keep the stamped field consistent
    payload["body_sha256"] = "0" * 64  # but corrupt the hash binding to the raw body

    with pytest.raises(AdapterError, match="did not validate"):
        build_rules_identity_fixture(
            payload,
            expected_market_ticker="M",
            expected_event_ticker="E",
            expected_rules_hash=expected_rules_hash,
            now=NOW,
        )


# ---------------------------------------------------------------------------
# Account snapshot / M27F / M27H
# ---------------------------------------------------------------------------


def test_account_snapshot_fixture_happy_path(tmp_path: Path) -> None:
    m27f_payload = _m27f_payload(NOW)
    m27h_payload = _m27h_payload(NOW)
    exposure_payload = _exposure("M", NOW).to_json()

    live_read_path = tmp_path / "m27f.json"
    write_credential_path = tmp_path / "m27h.json"
    live_read_path.write_text(json.dumps(m27f_payload))
    write_credential_path.write_text(json.dumps(m27h_payload))

    fixture = build_account_snapshot_fixture(
        live_read_evidence_path=live_read_path,
        write_credential_evidence_path=write_credential_path,
        candidate_exposure_payload=exposure_payload,
        expected_market_ticker="M",
        now=NOW,
    )

    assert fixture.production_reads_verified is True
    assert fixture.reconciled is True
    assert fixture.write_credential_evidence_verified is True
    assert fixture.signer_runtime_evidence_verified is True
    assert fixture.account_snapshot_version
    assert fixture.reconciliation_version
    # observed_at is the OLDER of the two artifacts' completed_at -- never the newer.
    assert fixture.observed_at == min(
        datetime.fromisoformat(m27f_payload["completed_at"]),
        datetime.fromisoformat(m27h_payload["completed_at"]),
    )


def test_account_snapshot_fixture_fails_closed_on_one_bad_read(tmp_path: Path) -> None:
    m27f_payload = _m27f_payload(NOW)
    m27f_payload["reads"][0]["classification"] = "HTTP_OR_NETWORK_FAILURE"
    m27f_payload["reconciliation"]["classification"] = "BLOCKED"
    m27h_payload = _m27h_payload(NOW)
    exposure_payload = _exposure("M", NOW).to_json()

    live_read_path = tmp_path / "m27f.json"
    write_credential_path = tmp_path / "m27h.json"
    live_read_path.write_text(json.dumps(m27f_payload))
    write_credential_path.write_text(json.dumps(m27h_payload))

    fixture = build_account_snapshot_fixture(
        live_read_evidence_path=live_read_path,
        write_credential_evidence_path=write_credential_path,
        candidate_exposure_payload=exposure_payload,
        expected_market_ticker="M",
        now=NOW,
    )

    assert fixture.production_reads_verified is False
    assert fixture.reconciled is False
    # M27H evidence was untouched -- its two gates must remain independently True.
    assert fixture.write_credential_evidence_verified is True
    assert fixture.signer_runtime_evidence_verified is True


def test_account_snapshot_fixture_reconciliation_version_binds_candidate_exposure(
    tmp_path: Path,
) -> None:
    """Same M27F/M27H evidence, different candidate exposure -> different identity."""
    m27f_payload = _m27f_payload(NOW)
    m27h_payload = _m27h_payload(NOW)
    live_read_path = tmp_path / "m27f.json"
    write_credential_path = tmp_path / "m27h.json"
    live_read_path.write_text(json.dumps(m27f_payload))
    write_credential_path.write_text(json.dumps(m27h_payload))

    no_position = build_account_snapshot_fixture(
        live_read_evidence_path=live_read_path,
        write_credential_evidence_path=write_credential_path,
        candidate_exposure_payload=_exposure("M", NOW, position_nonzero=False).to_json(),
        expected_market_ticker="M",
        now=NOW,
    )
    with_position = build_account_snapshot_fixture(
        live_read_evidence_path=live_read_path,
        write_credential_evidence_path=write_credential_path,
        candidate_exposure_payload=_exposure("M", NOW, position_nonzero=True).to_json(),
        expected_market_ticker="M",
        now=NOW,
    )
    assert no_position.reconciliation_version != with_position.reconciliation_version
    assert no_position.account_snapshot_version == with_position.account_snapshot_version


# ---------------------------------------------------------------------------
# Candidate exposure
# ---------------------------------------------------------------------------


def test_candidate_exposure_fixture_happy_path() -> None:
    evidence = _exposure("M", NOW, open_orders=0, position_nonzero=False)
    fixture = build_candidate_exposure_fixture(evidence.to_json(), expected_market_ticker="M")
    assert fixture.succeeded is True
    assert fixture.open_order_count == 0
    assert fixture.position_nonzero is False
    assert fixture.market_ticker == "M"


def test_candidate_exposure_fixture_blocked_never_succeeds() -> None:
    payload = {
        "schema": "kalsh3.m27i.candidate-exposure.v1",
        "software_version": "x",
        "market_ticker": "M",
        "started_at": NOW.isoformat(),
        "completed_at": NOW.isoformat(),
        "orders_classification": "SUCCESS",
        "positions_classification": "AccountGatewayError",
        "open_order_count": None,
        "position_nonzero": None,
        "classification": "BLOCKED",
        "reason": "positions read did not complete",
    }
    fixture = build_candidate_exposure_fixture(payload, expected_market_ticker="M")
    assert fixture.succeeded is False
    # Fail-closed sentinels, not the caller's None -- exposure gates must never treat these as OK.
    assert fixture.position_nonzero is True


def test_candidate_exposure_fixture_rejects_wrong_market() -> None:
    evidence = _exposure("M", NOW)
    with pytest.raises(AdapterError, match="different market ticker"):
        build_candidate_exposure_fixture(evidence.to_json(), expected_market_ticker="OTHER")


def test_candidate_exposure_fixture_rejects_extra_field() -> None:
    payload = dict(_exposure("M", NOW).to_json())
    payload["succeeded"] = True  # not part of this schema -- must never be trusted if injected
    with pytest.raises(AdapterError, match="unexpected or missing fields"):
        build_candidate_exposure_fixture(payload, expected_market_ticker="M")


def test_candidate_exposure_fixture_rejects_missing_field() -> None:
    payload = dict(_exposure("M", NOW).to_json())
    del payload["reason"]
    with pytest.raises(AdapterError, match="unexpected or missing fields"):
        build_candidate_exposure_fixture(payload, expected_market_ticker="M")


# ---------------------------------------------------------------------------
# M13 risk quadruple
# ---------------------------------------------------------------------------


def _valid_intent(**overrides: object) -> RiskIntent:
    values: dict[str, object] = {
        "intent_id": "intent-1",
        "created_at": NOW,
        "market_ticker": "M",
        "event_id": "E",
        "correlation_cluster_id": "E",
        "rules_version": "v1",
        "rules_hash": "rules",
        "contract_interpretation_version": "v1",
        "candidate_id": "cand-1",
        "forecast_id": "forecast-1",
        "economic_action": EconomicAction.BUY_YES_OUTCOME,
        "outcome_side": "yes",
        "book_side": "ASK",
        "price": Decimal("0.30"),
        "quantity": Decimal("1.00"),
        "maximum_expected_fee": Decimal("0.02"),
        "maximum_expected_cash_commitment": Decimal("0.30"),
        "maximum_loss_if_filled": Decimal("0.30"),
        "order_style": "LIMIT",
        "time_in_force_policy": "GTC",
        "expires_at": NOW + timedelta(seconds=30),
        "post_only": False,
        "cancel_order_on_pause": True,
        "reduce_only": False,
        "self_trade_prevention": "CANCEL_NEWEST",
        "required_order_group_policy": "NONE",
        "client_order_id": "kalsh3-v1-abcdefgh",
        "account": "acct",
        "subaccount": 0,
    }
    values.update(overrides)
    return RiskIntent.freeze(**values)


def _valid_snapshot(**overrides: object) -> PortfolioRiskSnapshot:
    zero = Decimal("0")
    values: dict[str, object] = {
        "observed_at": NOW,
        "account_snapshot_version": "asv-1",
        "reconciliation_version": "rv-1",
        "cash": Decimal("1000"),
        "portfolio_value": Decimal("1000"),
        "account_equity": Decimal("1000"),
        "protected_reserve": zero,
        "active_capital_available": Decimal("1000"),
        "current_market_risk": zero,
        "current_event_risk": zero,
        "current_aggregate_risk": zero,
        "resting_order_potential_risk": zero,
        "projected_market_risk": zero,
        "projected_event_risk": zero,
        "projected_aggregate_risk": zero,
        "realized_daily_pnl": zero,
        "realized_weekly_pnl": zero,
        "realized_monthly_pnl": zero,
        "experiment_equity": zero,
        "experiment_high_water_mark": zero,
        "experiment_drawdown": zero,
        "external_positions": 0,
        "external_orders": 0,
        "unknown_orders": 0,
        "account_fresh": True,
        "reconciliation_status": ReconciliationStatus.RECONCILED,
        "exchange_market_exposure": zero,
        "exchange_event_exposure": zero,
        "independently_calculated_market_exposure": zero,
        "independently_calculated_event_exposure": zero,
    }
    values.update(overrides)
    return PortfolioRiskSnapshot.freeze(**values)


def _valid_decision(
    intent: RiskIntent, snapshot: PortfolioRiskSnapshot, **overrides: object
) -> RiskDecision:
    values: dict[str, object] = {
        "state": RiskDecisionState.PASS_NEXT_GATE,
        "intent_hash": intent.content_hash,
        "risk_policy_version": "1",
        "portfolio_state_hash": snapshot.content_hash,
        "reconciliation_version": snapshot.reconciliation_version,
        "rules_version": intent.rules_version,
        "market_data_version": "v1",
        "loss_state_version": "initial",
        "compliance_state_version": "v1",
        "kill_state_version": "v1",
        "decided_at": NOW,
        "expires_at": NOW + timedelta(seconds=5),
        "reasons": (),
        "display_result": "PASS",
        "production_write_authorized": False,
    }
    values.update(overrides)
    return RiskDecision.freeze(**values)


def _valid_authorization(
    decision: RiskDecision, intent: RiskIntent, **overrides: object
) -> RiskAuthorization:
    created_at = NOW
    safety_state_hash = "safety-1"
    authorization_id = risk_content_hash(
        (decision.decision_id, intent.content_hash, safety_state_hash, created_at.isoformat())
    )
    values: dict[str, object] = {
        "authorization_id": authorization_id,
        "risk_decision_id": decision.decision_id,
        "intent_hash": intent.content_hash,
        "portfolio_state_hash": decision.portfolio_state_hash,
        "policy_version": decision.risk_policy_version,
        "rules_version": decision.rules_version,
        "safety_state_hash": safety_state_hash,
        "created_at": created_at,
        "expires_at": NOW + timedelta(seconds=5),
        "state": AuthorizationState.ISSUED,
        "production_execution_authorized": False,
    }
    values.update(overrides)
    return RiskAuthorization(**values)


def test_m13_fixture_happy_path_round_trips_typed_values() -> None:
    intent = _valid_intent()
    snapshot = _valid_snapshot()
    decision = _valid_decision(intent, snapshot)
    authorization = _valid_authorization(decision, intent)

    fixture = build_m13_fixture(
        risk_intent_payload=_dataclass_to_json(intent),
        risk_snapshot_payload=_dataclass_to_json(snapshot),
        risk_decision_payload=_dataclass_to_json(decision),
        risk_authorization_payload=_dataclass_to_json(authorization),
        global_halt_clear=True,
        compliance_clear=True,
        kills_clear=True,
    )

    assert fixture.risk_intent.price == Decimal("0.30")
    assert fixture.risk_intent.economic_action is EconomicAction.BUY_YES_OUTCOME
    assert fixture.risk_intent.content_hash == intent.content_hash
    assert fixture.risk_decision.state is RiskDecisionState.PASS_NEXT_GATE
    assert fixture.risk_decision.decision_id == decision.decision_id
    assert fixture.risk_snapshot.reconciliation_status is ReconciliationStatus.RECONCILED
    assert fixture.authorization.state is AuthorizationState.ISSUED
    assert fixture.authorization.authorization_id == authorization.authorization_id
    assert fixture.global_halt_clear is True


def test_m13_fixture_rejects_negative_money() -> None:
    intent = _valid_intent()
    snapshot = _valid_snapshot()
    decision = _valid_decision(intent, snapshot)
    authorization = _valid_authorization(decision, intent)

    tampered_intent_payload = _dataclass_to_json(intent)
    tampered_intent_payload["price"] = "-0.30"

    with pytest.raises(AdapterError, match="failed domain validation"):
        build_m13_fixture(
            risk_intent_payload=tampered_intent_payload,
            risk_snapshot_payload=_dataclass_to_json(snapshot),
            risk_decision_payload=_dataclass_to_json(decision),
            risk_authorization_payload=_dataclass_to_json(authorization),
            global_halt_clear=True,
            compliance_clear=True,
            kills_clear=True,
        )


def test_m13_fixture_rejects_invalid_enum_value() -> None:
    intent = _valid_intent()
    snapshot = _valid_snapshot()
    decision = _valid_decision(intent, snapshot)
    authorization = _valid_authorization(decision, intent)

    tampered_decision_payload = _dataclass_to_json(decision)
    tampered_decision_payload["state"] = "NOT_A_REAL_STATE"

    with pytest.raises(AdapterError, match="not a valid RiskDecisionState"):
        build_m13_fixture(
            risk_intent_payload=_dataclass_to_json(intent),
            risk_snapshot_payload=_dataclass_to_json(snapshot),
            risk_decision_payload=tampered_decision_payload,
            risk_authorization_payload=_dataclass_to_json(authorization),
            global_halt_clear=True,
            compliance_clear=True,
            kills_clear=True,
        )


def test_m13_fixture_rejects_missing_field() -> None:
    intent = _valid_intent()
    snapshot = _valid_snapshot()
    decision = _valid_decision(intent, snapshot)
    authorization = _valid_authorization(decision, intent)

    tampered_snapshot_payload = _dataclass_to_json(snapshot)
    del tampered_snapshot_payload["cash"]

    with pytest.raises(AdapterError, match="missing field cash"):
        build_m13_fixture(
            risk_intent_payload=_dataclass_to_json(intent),
            risk_snapshot_payload=tampered_snapshot_payload,
            risk_decision_payload=_dataclass_to_json(decision),
            risk_authorization_payload=_dataclass_to_json(authorization),
            global_halt_clear=True,
            compliance_clear=True,
            kills_clear=True,
        )


def test_m13_fixture_rejects_non_bool_safety_flags() -> None:
    intent = _valid_intent()
    snapshot = _valid_snapshot()
    decision = _valid_decision(intent, snapshot)
    authorization = _valid_authorization(decision, intent)

    with pytest.raises(AdapterError, match="already-produced boolean"):
        build_m13_fixture(
            risk_intent_payload=_dataclass_to_json(intent),
            risk_snapshot_payload=_dataclass_to_json(snapshot),
            risk_decision_payload=_dataclass_to_json(decision),
            risk_authorization_payload=_dataclass_to_json(authorization),
            global_halt_clear="true",  # not an actual bool -- must be rejected, never coerced
            compliance_clear=True,
            kills_clear=True,
        )


# ---------------------------------------------------------------------------
# Submission budget
# ---------------------------------------------------------------------------


def test_submission_budget_fixture_happy_path() -> None:
    fixture = build_submission_budget_fixture(
        write_budget_used=False, unresolved_canary_present=False
    )
    assert fixture.write_budget_used is False
    assert fixture.unresolved_canary_present is False


def test_submission_budget_fixture_rejects_non_bool() -> None:
    with pytest.raises(AdapterError, match="already-produced boolean"):
        build_submission_budget_fixture(write_budget_used=0, unresolved_canary_present=False)


# ---------------------------------------------------------------------------
# Orchestrator wiring -- delegates to the existing, unmodified build_rehearsal.
# ---------------------------------------------------------------------------


def test_run_m27n1_rehearsal_delegates_to_build_rehearsal_and_abstains_on_no_candidates() -> None:
    result = run_m27n1_rehearsal(now=NOW, candidate_inputs=())
    assert result.artifact.state == "ABSTAIN"
    assert result.artifact.abstain_reason == "ABSTAIN_NO_QUALIFYING_CANDIDATE"


# ---------------------------------------------------------------------------
# No network / credential / store call sites.
# ---------------------------------------------------------------------------

_FORBIDDEN_CALL_NAMES = {
    "acquire_market_snapshot",
    "acquire_current_market_rules",
    "run_live_read_acceptance",
    "verify_installed_write_credential",
    "check_candidate_market_exposure",
    "KalshiAccountClient",
    "AuthorizationStore",
    "CanaryStore",
    "get_market_with_body",
}


def test_adapter_module_never_calls_network_credential_or_store_functions() -> None:
    source = Path("services/supervised_canary/m27n1_evidence_adapter.py").read_text()
    tree = ast.parse(source)
    called_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                called_names.add(func.id)
            elif isinstance(func, ast.Attribute):
                called_names.add(func.attr)
    forbidden_hits = called_names & _FORBIDDEN_CALL_NAMES
    assert not forbidden_hits, f"adapter calls forbidden network/store names: {forbidden_hits}"
