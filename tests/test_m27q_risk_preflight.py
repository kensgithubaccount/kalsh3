from __future__ import annotations

import ast
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from services.kalshi_account_gateway.client import HttpResponse
from services.opportunity_engine.books import OutcomeSide
from services.risk_engine.domain import (
    ComplianceState,
    KillCategory,
    KillLevel,
    ReconciliationStatus,
    RequiredOrderGroupPolicy,
    RiskDecisionState,
    RiskReason,
)
from services.risk_engine.invariants import CANONICAL_POLICY, NewRiskReadiness
from services.risk_engine.states import (
    KillState,
    SafetyState,
    evaluate_loss_windows,
)
from services.supervised_canary import live_read_acceptance as m27f
from services.supervised_canary.candidate_exposure_check import (
    CandidateExposureEvidence,
)
from services.supervised_canary.m27d import (
    CANARY_STATUS,
    ExperimentalCanaryEligibility,
    ExperimentalCandidate,
)
from services.supervised_canary.m27q_risk_preflight import (
    FirstCanaryDurableState,
    M27QRiskError,
    RiskContextVersions,
    build_first_canary_risk_triple,
)
from tests.test_m27f_live_read_acceptance import (
    FakeAccountTransport,
    FakeSigner,
    build_attestation,
)

NOW = datetime(2026, 8, 22, 23, 30, tzinfo=UTC)


def candidate() -> ExperimentalCandidate:
    eligibility = ExperimentalCanaryEligibility(
        status=CANARY_STATUS,
        weather_result_identity="weather-result",
        model_identity="weather-model",
        claim_type="physical-proxy",
        settlement_mapping_status="UNVALIDATED",
        source_family="weather-family",
        forecast_evidence_identity="forecast-evidence",
        contract_identity="contract-identity",
        target_date=NOW.date(),
        exact_midpoint_seconds=54_000,
        market_evidence_identity="market-evidence",
        selection_policy_identity="selection-policy",
        created_at=NOW - timedelta(seconds=5),
        expires_at=NOW + timedelta(minutes=1),
        research_warning="research-only",
    )
    return ExperimentalCandidate(
        candidate_id="c" * 64,
        eligibility=eligibility,
        market_ticker="KXHIGHCHI-TEST",
        event_ticker="KXHIGHCHI-TEST-EVENT",
        series_ticker="KXHIGHCHI",
        predicate="contract-identity",
        selected_side=OutcomeSide.NO,
        executable_price=Decimal("0.55"),
        available_quantity=Decimal("5"),
        maximum_fee=Decimal("0.01"),
        maximum_commitment=Decimal("0.56"),
        maximum_loss=Decimal("0.56"),
        all_in_break_even_probability=Decimal("0.56"),
        research_probability_discrepancy=Decimal("0.25"),
        ranking=(Decimal("0.25"), 54_000, "KXHIGHCHI-TEST"),
        economics_evidence_identity="economics-evidence",
        truth_warning="research-only",
    )


def bundle(
    *,
    account_overrides: dict[str, list[HttpResponse | Exception]] | None = None,
) -> m27f.LiveReadAcceptanceBundle:
    return m27f.run_live_read_acceptance_bundle(
        key_id="candidate",
        private_key_pem=b"synthetic-pem-not-real",
        authority_attestation=build_attestation(),
        account_transport=FakeAccountTransport(account_overrides),
        signer_factory=FakeSigner,
        clock=lambda: NOW - timedelta(seconds=2),
        clock_ms=lambda: 123,
    )


def exposure(
    *,
    market_ticker: str = "KXHIGHCHI-TEST",
    open_orders: int = 0,
    position_nonzero: bool = False,
    classification: str = "PASS",
    completed_at: datetime | None = None,
) -> CandidateExposureEvidence:
    when = completed_at or (NOW - timedelta(seconds=1))
    return CandidateExposureEvidence(
        schema="kalsh3.m27i.candidate-exposure.v1",
        software_version="test",
        market_ticker=market_ticker,
        started_at=when,
        completed_at=when,
        orders_classification="SUCCESS",
        positions_classification="SUCCESS",
        open_order_count=open_orders,
        position_nonzero=position_nonzero,
        classification=classification,
        reason=None,
    )


def readiness(**changes: bool) -> NewRiskReadiness:
    values = {field.name: True for field in fields(NewRiskReadiness)}
    values.update(changes)
    return NewRiskReadiness(**values)


def safety(
    *,
    killed: KillCategory | None = None,
) -> SafetyState:
    losses = evaluate_loss_windows(
        now=NOW,
        realized_daily_pnl=Decimal(0),
        realized_weekly_pnl=Decimal(0),
        realized_monthly_pnl=Decimal(0),
        drawdown=Decimal(0),
        policy=CANONICAL_POLICY,
    )
    kills = tuple(
        KillState(
            category,
            KillLevel.KILLED if category is killed else KillLevel.NORMAL,
            "test",
            NOW,
        )
        for category in KillCategory
    )
    return SafetyState(
        global_halt=False,
        global_halt_reason=None,
        compliance=ComplianceState.CLEAR,
        reconciliation=ReconciliationStatus.RECONCILED,
        kills=kills,
        losses=losses,
    )


def versions(state: SafetyState) -> RiskContextVersions:
    return RiskContextVersions(
        rules_version="rules-v1",
        rules_hash="rules-hash-v1",
        contract_interpretation_version="contract-v1",
        market_data_version="market-data-v1",
        loss_state_version=state.losses.version,
        compliance_state_version="compliance-v1",
        kill_state_version="kills-v1",
    )


def durable(**changes: object) -> FirstCanaryDurableState:
    values: dict[str, object] = {
        "production_state": "DISARMED",
        "real_submission_count": 0,
        "real_fill_count": 0,
        "unresolved_canary_present": False,
    }
    values.update(changes)
    return FirstCanaryDurableState(**values)  # type: ignore[arg-type]


def build(**changes: object):
    state = safety()
    values: dict[str, object] = {
        "candidate": candidate(),
        "m27f_bundle": bundle(),
        "candidate_exposure": exposure(),
        "durable_state": durable(),
        "readiness": readiness(),
        "safety": state,
        "order_group": RequiredOrderGroupPolicy(
            "m27q-none",
            False,
            0,
            Decimal("1.00"),
            True,
            True,
        ),
        "versions": versions(state),
        "client_order_id_unique": True,
        "conflicting_bot_order": False,
        "authorization_service_available": True,
        "now": NOW,
    }
    values.update(changes)
    return build_first_canary_risk_triple(**values)  # type: ignore[arg-type]


def test_clean_first_canary_builds_real_m13_pass_next_gate_triple() -> None:
    result = build()

    assert result.clean_pass
    assert result.decision.state is RiskDecisionState.PASS_NEXT_GATE
    assert result.decision.reasons == ()
    assert result.decision.production_write_authorized is False

    assert result.intent.quantity == Decimal("1.00")
    assert result.intent.outcome_side == "NO"
    assert result.intent.price == Decimal("0.55")
    assert result.intent.maximum_expected_fee == Decimal("0.01")
    assert result.intent.maximum_expected_cash_commitment == Decimal("0.56")
    assert result.intent.maximum_loss_if_filled == Decimal("0.56")
    assert result.intent.client_order_id.startswith("kalsh3-v1-m27q-")

    assert result.snapshot.cash == Decimal("1000")
    assert result.snapshot.portfolio_value is None
    assert result.snapshot.account_equity == Decimal("1000")
    assert result.snapshot.protected_reserve == Decimal("700")
    assert result.snapshot.active_capital_available == Decimal("300")

    assert result.snapshot.current_market_risk == Decimal(0)
    assert result.snapshot.current_event_risk == Decimal(0)
    assert result.snapshot.current_aggregate_risk == Decimal(0)
    assert result.snapshot.projected_market_risk == Decimal("0.56")
    assert result.snapshot.projected_event_risk == Decimal("0.56")
    assert result.snapshot.projected_aggregate_risk == Decimal("0.56")

    assert result.snapshot.experiment_equity == Decimal("300")
    assert result.snapshot.experiment_high_water_mark == Decimal("300")
    assert result.snapshot.experiment_drawdown == Decimal(0)

    assert result.decision.intent_hash == result.intent.content_hash
    assert result.decision.portfolio_state_hash == result.snapshot.content_hash
    assert result.decision.reconciliation_version == result.snapshot.reconciliation_version
    assert result.decision.expires_at == NOW + timedelta(seconds=5)


@pytest.mark.parametrize(
    ("needle", "field"),
    [
        ("positions?", "market_positions"),
        ("orders?", "orders"),
        ("fills?", "fills"),
        ("settlements?", "settlements"),
    ],
)
def test_any_preexisting_account_activity_blocks_first_canary(
    needle: str,
    field: str,
) -> None:
    account_bundle = bundle(
        account_overrides={
            needle: [
                HttpResponse(
                    200,
                    {
                        field: [{"ticker": "EXISTING"}],
                        "cursor": "",
                    },
                )
            ]
        }
    )

    with pytest.raises(
        M27QRiskError,
        match="zero positions, orders, fills, and settlements",
    ):
        build(m27f_bundle=account_bundle)


@pytest.mark.parametrize(
    "state",
    [
        durable(production_state="ARMED"),
        durable(real_submission_count=1),
        durable(real_fill_count=1),
        durable(unresolved_canary_present=True),
    ],
)
def test_nonpristine_durable_state_blocks(state: FirstCanaryDurableState) -> None:
    with pytest.raises(M27QRiskError, match="durable first-canary state"):
        build(durable_state=state)


def test_candidate_specific_open_order_blocks() -> None:
    with pytest.raises(M27QRiskError, match="zero candidate open orders"):
        build(candidate_exposure=exposure(open_orders=1))


def test_candidate_specific_position_blocks() -> None:
    with pytest.raises(M27QRiskError, match="zero candidate position"):
        build(candidate_exposure=exposure(position_nonzero=True))


def test_stale_candidate_exposure_blocks() -> None:
    with pytest.raises(M27QRiskError, match="stale or future-dated"):
        build(candidate_exposure=exposure(completed_at=NOW - timedelta(seconds=31)))


def test_false_readiness_is_rejected_by_existing_m13_engine() -> None:
    result = build(readiness=readiness(model_valid=False))

    assert result.decision.state is RiskDecisionState.REJECT
    assert RiskReason.MODEL_NOT_ELIGIBLE in result.decision.reasons
    assert result.clean_pass is False


def test_kill_state_is_rejected_by_existing_m13_engine() -> None:
    state = safety(killed=KillCategory.DATA)

    result = build(
        safety=state,
        versions=versions(state),
    )

    assert result.decision.state is RiskDecisionState.PAUSE
    assert RiskReason.DATA_KILL in result.decision.reasons


def test_cash_below_protected_reserve_is_rejected_without_portfolio_value_assumption() -> None:
    low_balance = {
        "balance": 65000,
        "portfolio_value": 999999,
        "updated_ts": 1_700_000_000,
        "balance_breakdown": [],
    }
    account_bundle = bundle(account_overrides={"balance?": [HttpResponse(200, low_balance)]})

    result = build(m27f_bundle=account_bundle)

    assert result.snapshot.cash == Decimal("650")
    assert result.snapshot.portfolio_value is None
    assert result.snapshot.account_equity == Decimal("650")
    assert result.snapshot.active_capital_available == Decimal(0)
    assert result.decision.state is RiskDecisionState.REJECT
    assert RiskReason.RESERVE_VIOLATION in result.decision.reasons


def test_m27f_balance_hash_mismatch_blocks_before_risk_evaluation() -> None:
    account_bundle = bundle()
    assert account_bundle.account_facts is not None
    forged_facts = replace(
        account_bundle.account_facts,
        balance_payload_sha256="0" * 64,
    )
    forged_bundle = replace(
        account_bundle,
        account_facts=forged_facts,
    )

    with pytest.raises(M27QRiskError, match="cash facts do not bind"):
        build(m27f_bundle=forged_bundle)


def test_module_has_no_authorization_or_mutation_capability() -> None:
    path = Path("services/supervised_canary/m27q_risk_preflight.py")
    tree = ast.parse(path.read_text())

    imported: set[str] = set()
    calls: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                calls.add(node.func.attr)
            elif isinstance(node.func, ast.Name):
                calls.add(node.func.id)

    forbidden_import_fragments = {
        "sqlite3",
        "socket",
        "urllib",
        "requests",
        "httpx",
        "production_execution",
        "kalshi_account_gateway.auth",
        "kalshi_account_gateway.client",
        "risk_engine.authorization",
        "m27o_live_canary",
        "m27o_reconciliation",
    }

    assert not any(
        fragment in module for fragment in forbidden_import_fragments for module in imported
    )
    assert "issue" not in calls
    assert "consume" not in calls
    assert "record_submission_attempt" not in calls
    assert "record_fill" not in calls
