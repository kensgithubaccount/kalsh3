from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from services.production_execution.requests import create_envelope
from services.risk_engine.authorization import AuthorizationState, RiskAuthorization
from services.supervised_canary.domain import HumanCanaryApproval, HumanCanaryPreview
from services.supervised_canary.m27i import (
    GATE_NAMES,
    GateResult,
    PreflightArtifact,
    PreflightGates,
)
from services.supervised_canary.m27o import M27OReleaseError, prepare_one_contract_release

NOW = datetime(2026, 8, 22, 3, 20, tzinfo=UTC)


def preview(**changes: object) -> HumanCanaryPreview:
    values: dict[str, object] = dict(
        preview_id="preview-m27o",
        created_at=NOW - timedelta(seconds=5),
        expires_at=NOW + timedelta(seconds=55),
        candidate_id="candidate-m27o",
        market_ticker="KXHIGHCHI-26AUG22-B80.5",
        event_ticker="KXHIGHCHI-26AUG22",
        market_title="Chicago high temperature 80 to 81",
        resolution_question="Will the reported high be 80 to 81 F?",
        close_time=NOW + timedelta(hours=12),
        rules_version="rules-v1",
        rules_hash="rules-hash-v1",
        settlement_source="Kalshi contract rules",
        forecast_version="forecast-v1",
        independent_forecast=Decimal("0.7807"),
        market_reference=Decimal("0.5574"),
        uncertainty=Decimal("0.04"),
        selected_outcome="BUY NO",
        limit_price=Decimal("0.5400"),
        quantity=Decimal("1.00"),
        maximum_fee=Decimal("0.0174"),
        maximum_commitment=Decimal("0.5574"),
        maximum_loss=Decimal("0.5574"),
        current_market_risk=Decimal("0"),
        projected_market_risk=Decimal("0.5574"),
        current_event_risk=Decimal("0"),
        projected_event_risk=Decimal("0.5574"),
        current_aggregate_risk=Decimal("0"),
        projected_aggregate_risk=Decimal("0.5574"),
        protected_reserve=Decimal("700"),
        after_cost_value=Decimal("0.2233"),
        execution_style="FILL_OR_KILL",
        post_only=False,
        reduce_only=False,
        cancel_order_on_pause=True,
        stp_policy="cancel_newest",
        order_group_policy="required",
        client_order_id="kalsh3-m27o-one-contract",
        reconciliation_version="reconciliation-v1",
        market_data_version="book-v1",
        api_compatibility_version="v2",
        evidence_mode="REAL REQUIRED",
        subaccount=0,
    )
    values.update(changes)
    return HumanCanaryPreview(**values)  # type: ignore[arg-type]


def approval(p: HumanCanaryPreview, **changes: object) -> HumanCanaryApproval:
    values: dict[str, object] = dict(
        approval_id="approval-m27o",
        owner_identity="owner",
        preview_hash=p.content_hash,
        candidate_id=p.candidate_id,
        intent_hash="intent-m27o",
        exact_price=p.limit_price,
        exact_quantity=p.quantity,
        maximum_fee=p.maximum_fee,
        maximum_loss=p.maximum_loss,
        rules_hash=p.rules_hash,
        reconciliation_version=p.reconciliation_version,
        production_read_state="LIVE VERIFIED",
        approved_at=NOW - timedelta(seconds=2),
        expires_at=NOW + timedelta(seconds=58),
        reason="first real one-contract canary",
        confirmation="APPROVE THIS ONE-CONTRACT CANARY",
        step_up_proof_reference="step-up-proof",
    )
    values.update(changes)
    return HumanCanaryApproval(**values)  # type: ignore[arg-type]


def risk_authorization(**changes: object) -> RiskAuthorization:
    values: dict[str, object] = dict(
        authorization_id="risk-auth-m27o",
        risk_decision_id="risk-decision-m27o",
        intent_hash="intent-m27o",
        portfolio_state_hash="portfolio-v1",
        policy_version="risk-policy-v1",
        rules_version="rules-v1",
        safety_state_hash="safety-v1",
        created_at=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(seconds=4),
        state=AuthorizationState.ISSUED,
    )
    values.update(changes)
    return RiskAuthorization(**values)  # type: ignore[arg-type]


def envelope(p: HumanCanaryPreview, r: RiskAuthorization, **changes: object):
    values: dict[str, object] = dict(
        execution_id="execution-m27o",
        authorization_id=r.authorization_id,
        decision_id=r.risk_decision_id,
        intent_hash=r.intent_hash,
        ticker=p.market_ticker,
        outcome_side="NO",
        price=p.limit_price,
        quantity=Decimal("1.00"),
        tif="fill_or_kill",
        expiration=None,
        post_only=False,
        reduce_only=False,
        cancel_on_pause=True,
        stp="cancel_newest",
        order_group_id=None,
        client_order_id=p.client_order_id,
        rules_version=p.rules_version,
        candidate_version=p.candidate_id,
        portfolio_hash=r.portfolio_state_hash,
        reconciliation_hash=p.reconciliation_version,
        created_at=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(seconds=4),
    )
    values.update(changes)
    return create_envelope(**values)  # type: ignore[arg-type]


def preflight(p: HumanCanaryPreview, **changes: object) -> dict[str, object]:
    values: dict[str, object] = dict(
        schema="kalsh3.m27i.live-weather-preflight.v1",
        software_version="kalsh3.m27i.live-weather-preflight/1",
        created_at=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(seconds=4),
        state="PREFLIGHT_READY",
        abstain_reason=None,
        candidate_id=p.candidate_id,
        market_ticker=p.market_ticker,
        event_ticker=p.event_ticker,
        target_date="2026-08-22",
        selected_side="NO",
        executable_price=str(p.limit_price),
        maximum_fee=str(p.maximum_fee),
        maximum_commitment=str(p.maximum_commitment),
        maximum_loss=str(p.maximum_loss),
        proxy_probability="0.7806896551724137931034482759",
        research_probability_discrepancy="0.2232896551724137931034482759",
        model_identity="model-v1",
        forecast_evidence_identity="forecast-evidence-v1",
        economics_evidence_identity="economics-evidence-v1",
        gates=PreflightGates({name: GateResult(True) for name in GATE_NAMES}),
        missing_gates=(),
        warning="research-only physical-temperature proxy",
    )
    values.update(changes)
    return PreflightArtifact(**values).to_json()  # type: ignore[arg-type]


def valid_release():
    p = preview()
    a = approval(p)
    r = risk_authorization()
    e = envelope(p, r)
    return prepare_one_contract_release(
        preflight_payload=preflight(p),
        preview=p,
        approval=a,
        envelope=e,
        risk_authorization=r,
        now=NOW,
    )


def test_valid_artifacts_bind_into_short_lived_one_contract_release() -> None:
    release = valid_release()
    assert release.exact_quantity == Decimal("1.00")
    assert release.selected_side == "NO"
    assert release.exact_price == Decimal("0.5400")
    assert release.maximum_fee == Decimal("0.0174")
    assert release.expires_at == NOW + timedelta(seconds=4)
    assert release.content_hash


def test_preflight_must_be_fresh_ready_and_hash_intact() -> None:
    p = preview()
    a = approval(p)
    r = risk_authorization()
    e = envelope(p, r)
    stale = preflight(
        p,
        created_at=NOW - timedelta(seconds=40),
        expires_at=NOW - timedelta(seconds=10),
    )
    with pytest.raises(M27OReleaseError, match="preflight rejected"):
        prepare_one_contract_release(
            preflight_payload=stale,
            preview=p,
            approval=a,
            envelope=e,
            risk_authorization=r,
            now=NOW,
        )

    tampered = preflight(p)
    tampered["executable_price"] = "0.01"
    with pytest.raises(M27OReleaseError, match="preflight rejected"):
        prepare_one_contract_release(
            preflight_payload=tampered,
            preview=p,
            approval=a,
            envelope=e,
            risk_authorization=r,
            now=NOW,
        )


def test_price_side_and_candidate_drift_fail_closed() -> None:
    p = preview()
    a = approval(p)
    r = risk_authorization()
    e = envelope(p, r)
    wrong_price = preflight(p, executable_price="0.5500")
    with pytest.raises(M27OReleaseError, match="price changed"):
        prepare_one_contract_release(
            preflight_payload=wrong_price,
            preview=p,
            approval=a,
            envelope=e,
            risk_authorization=r,
            now=NOW,
        )

    wrong_side = preflight(p, selected_side="YES")
    with pytest.raises(M27OReleaseError, match="side changed"):
        prepare_one_contract_release(
            preflight_payload=wrong_side,
            preview=p,
            approval=a,
            envelope=e,
            risk_authorization=r,
            now=NOW,
        )


def test_human_approval_is_exact_and_unexpired() -> None:
    p = preview()
    r = risk_authorization()
    e = envelope(p, r)
    with pytest.raises(M27OReleaseError, match="price or quantity changed"):
        prepare_one_contract_release(
            preflight_payload=preflight(p),
            preview=p,
            approval=replace(approval(p), exact_price=Decimal("0.53")),
            envelope=e,
            risk_authorization=r,
            now=NOW,
        )

    with pytest.raises(M27OReleaseError, match="approval expired"):
        prepare_one_contract_release(
            preflight_payload=preflight(p),
            preview=p,
            approval=replace(approval(p), expires_at=NOW),
            envelope=e,
            risk_authorization=r,
            now=NOW,
        )


def test_envelope_must_be_exact_one_contract_create_bound_to_approval() -> None:
    p = preview()
    a = approval(p)
    r = risk_authorization()
    two = envelope(p, r, quantity=Decimal("2.00"))
    with pytest.raises(M27OReleaseError, match="exactly one contract"):
        prepare_one_contract_release(
            preflight_payload=preflight(p),
            preview=p,
            approval=a,
            envelope=two,
            risk_authorization=r,
            now=NOW,
        )


def test_m13_authorization_must_be_current_and_exactly_bound() -> None:
    p = preview()
    a = approval(p)
    expired = risk_authorization(expires_at=NOW)
    e = envelope(p, expired, expires_at=NOW + timedelta(seconds=4))
    with pytest.raises(M27OReleaseError, match="M13 authorization expired"):
        prepare_one_contract_release(
            preflight_payload=preflight(p),
            preview=p,
            approval=a,
            envelope=e,
            risk_authorization=expired,
            now=NOW,
        )

    wrong_intent = risk_authorization(intent_hash="changed-intent")
    e2 = envelope(p, wrong_intent)
    with pytest.raises(M27OReleaseError, match="intent binding changed"):
        prepare_one_contract_release(
            preflight_payload=preflight(p),
            preview=p,
            approval=a,
            envelope=e2,
            risk_authorization=wrong_intent,
            now=NOW,
        )


def test_m27o_release_module_has_no_network_or_credential_surface() -> None:
    source = Path("services/supervised_canary/m27o.py").read_text()
    forbidden = (
        "urllib",
        "http.client",
        "requests.",
        "FixedKalshiProductionTransport",
        "ProductionWriteCredential",
        "_decode_committed_credential",
        "send_exact",
        "private_key",
    )
    for token in forbidden:
        assert token not in source
