from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.supervised_canary import m27o_operator as operator
from services.supervised_canary.domain import HumanCanaryApproval, HumanCanaryPreview
from services.supervised_canary.m27o import (
    COMMIT_SCHEMA,
    AtomicReleaseCommit,
    OneContractCanaryRelease,
)

NOW = datetime(2026, 8, 22, 4, 0, tzinfo=UTC)
ONE = Decimal("1.00")


class Clock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


def preview(**changes: object) -> HumanCanaryPreview:
    values: dict[str, object] = dict(
        preview_id="preview-m27o-operator",
        created_at=NOW - timedelta(seconds=5),
        expires_at=NOW + timedelta(seconds=55),
        candidate_id="candidate-m27o-operator",
        market_ticker="KXHIGHCHI-26AUG22-B80.5",
        event_ticker="KXHIGHCHI-26AUG22",
        market_title="Chicago high temperature",
        resolution_question="Exact reviewed rule text",
        close_time=NOW + timedelta(hours=20),
        rules_version="rules-v1",
        rules_hash="rules-hash-v1",
        settlement_source="reviewed source",
        forecast_version="forecast-v1",
        independent_forecast=Decimal("0.7806"),
        market_reference=Decimal("0.54"),
        uncertainty=Decimal("0.04"),
        selected_outcome="BUY NO",
        limit_price=Decimal("0.5400"),
        quantity=ONE,
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
        after_cost_value=Decimal("0.2232"),
        execution_style="TAKER_NOW",
        post_only=False,
        reduce_only=False,
        cancel_order_on_pause=True,
        stp_policy="taker_at_cross",
        order_group_policy="required",
        client_order_id="kalsh3-m27o-operator-1",
        reconciliation_version="reconciliation-v1",
        market_data_version="book-v1",
        api_compatibility_version="m27e-v1",
        evidence_mode="REAL REQUIRED",
        subaccount=0,
    )
    values.update(changes)
    return HumanCanaryPreview(**values)  # type: ignore[arg-type]


def approval(p: HumanCanaryPreview, **changes: object) -> HumanCanaryApproval:
    values: dict[str, object] = dict(
        approval_id="approval-m27o-operator",
        owner_identity="owner",
        preview_hash=p.content_hash,
        candidate_id=p.candidate_id,
        intent_hash="intent-m27o-operator",
        exact_price=p.limit_price,
        exact_quantity=p.quantity,
        maximum_fee=p.maximum_fee,
        maximum_loss=p.maximum_loss,
        rules_hash=p.rules_hash,
        reconciliation_version=p.reconciliation_version,
        production_read_state="LIVE VERIFIED",
        approved_at=NOW - timedelta(seconds=2),
        expires_at=NOW + timedelta(seconds=50),
        reason="exact first supervised real-money canary",
        confirmation="APPROVE THIS ONE-CONTRACT CANARY",
        step_up_proof_reference="reauth-m27o-operator",
    )
    values.update(changes)
    return HumanCanaryApproval(**values)  # type: ignore[arg-type]


def release(p: HumanCanaryPreview, a: HumanCanaryApproval, **changes: object):
    values: dict[str, object] = dict(
        schema="kalsh3.m27o.one-contract-release.v1",
        software_version="kalsh3.m27o.one-contract-release/2",
        created_at=NOW,
        expires_at=NOW + timedelta(seconds=4),
        candidate_id=p.candidate_id,
        market_ticker=p.market_ticker,
        selected_side="NO",
        exact_price=p.limit_price,
        exact_quantity=ONE,
        maximum_fee=p.maximum_fee,
        maximum_loss=p.maximum_loss,
        preview_id=p.preview_id,
        preview_hash=p.content_hash,
        approval_id=a.approval_id,
        approval_hash=a.content_hash,
        preflight_hash="f" * 64,
        envelope_hash="e" * 64,
        body_hash="b" * 64,
        risk_authorization_id="risk-auth-m27o-operator",
        risk_decision_id="risk-decision-m27o-operator",
        intent_hash=a.intent_hash,
        client_order_id=p.client_order_id,
        rules_version=p.rules_version,
        portfolio_state_hash="portfolio-v1",
        safety_state_hash="safety-v1",
        reconciliation_state_hash=p.reconciliation_version,
    )
    values.update(changes)
    return OneContractCanaryRelease(**values)  # type: ignore[arg-type]


def test_operator_authorization_is_a_distinct_exact_real_money_gate() -> None:
    p = preview()
    a = approval(p)
    auth = operator.issue_operator_execution_authorization(
        preview=p,
        approval=a,
        confirmation=operator.EXACT_REAL_MONEY_CONFIRMATION,
        now=NOW,
    )
    assert auth.candidate_id == p.candidate_id
    assert auth.market_ticker == p.market_ticker
    assert auth.selected_side == "NO"
    assert auth.exact_price == Decimal("0.5400")
    assert auth.exact_quantity == ONE
    assert auth.maximum_fee == Decimal("0.0174")
    assert auth.maximum_loss == Decimal("0.5574")
    assert auth.preview_hash == p.content_hash
    assert auth.approval_hash == a.content_hash
    assert auth.expires_at == a.expires_at
    assert auth.content_hash


def test_wrong_confirmation_or_approval_drift_cannot_issue_operator_authority() -> None:
    p = preview()
    a = approval(p)
    with pytest.raises(operator.M27OOperatorError, match="confirmation"):
        operator.issue_operator_execution_authorization(
            preview=p,
            approval=a,
            confirmation="APPROVE THIS ONE-CONTRACT CANARY",
            now=NOW,
        )
    with pytest.raises(operator.M27OOperatorError, match="economics"):
        operator.issue_operator_execution_authorization(
            preview=p,
            approval=replace(a, exact_price=Decimal("0.53")),
            confirmation=operator.EXACT_REAL_MONEY_CONFIRMATION,
            now=NOW,
        )


def test_operator_authority_cannot_outlive_preview_or_approval() -> None:
    p = preview(expires_at=NOW + timedelta(seconds=20))
    a = approval(p, expires_at=NOW + timedelta(seconds=15))
    auth = operator.issue_operator_execution_authorization(
        preview=p,
        approval=a,
        confirmation=operator.EXACT_REAL_MONEY_CONFIRMATION,
        now=NOW,
    )
    assert auth.expires_at == NOW + timedelta(seconds=15)


def test_runner_rebinds_authority_before_atomic_burn(monkeypatch: pytest.MonkeyPatch) -> None:
    p = preview()
    a = approval(p)
    auth = operator.issue_operator_execution_authorization(
        preview=p,
        approval=a,
        confirmation=operator.EXACT_REAL_MONEY_CONFIRMATION,
        now=NOW,
    )
    r = release(p, a, exact_price=Decimal("0.5500"))
    calls: list[str] = []
    monkeypatch.setattr(operator, "prepare_one_contract_release", lambda **_kwargs: r)

    def forbidden_commit(**_kwargs):
        calls.append("commit")
        raise AssertionError("atomic burn must not occur")

    monkeypatch.setattr(operator, "commit_atomic_release", forbidden_commit)
    with pytest.raises(operator.M27OOperatorError, match="does not match"):
        operator.run_operator_canary(
            operator_authorization=auth,
            preflight_payload={},
            m27h_payload={},
            preview=p,
            approval=a,
            envelope=SimpleNamespace(execution_id="execution"),  # type: ignore[arg-type]
            risk_authorization=object(),  # type: ignore[arg-type]
            canary_store=SimpleNamespace(path=Path("state.db")),  # type: ignore[arg-type]
            authorization_store=object(),  # type: ignore[arg-type]
            credential_store=object(),  # type: ignore[arg-type]
            journal=object(),  # type: ignore[arg-type]
            clock=Clock(),
        )
    assert calls == []


def test_runner_executes_a_b_c_d_once_and_forwards_m27h_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = preview()
    a = approval(p)
    auth = operator.issue_operator_execution_authorization(
        preview=p,
        approval=a,
        confirmation=operator.EXACT_REAL_MONEY_CONFIRMATION,
        now=NOW,
    )
    r = release(p, a)
    c = AtomicReleaseCommit(
        schema=COMMIT_SCHEMA,
        committed_at=NOW + timedelta(milliseconds=10),
        session_id="m27o-operator-session",
        release_hash=r.content_hash,
        preview_id=r.preview_id,
        approval_id=r.approval_id,
        risk_authorization_id=r.risk_authorization_id,
        client_order_id=r.client_order_id,
    )
    live_outcome = object()
    reconciliation = object()
    m27h = {"schema": "exact-m27h"}
    calls: list[str] = []

    def prepare(**_kwargs):
        calls.append("A")
        return r

    def commit(**_kwargs):
        calls.append("B")
        return c

    def execute(**kwargs):
        calls.append("C")
        assert kwargs["m27h_payload"] is m27h
        assert kwargs["release"] is r
        assert kwargs["atomic_commit"] is c
        return live_outcome

    def reconcile(**kwargs):
        calls.append("D")
        assert kwargs["release"] is r
        assert kwargs["atomic_commit"] is c
        assert kwargs["execution_id"] == "execution-m27o-operator"
        return reconciliation

    monkeypatch.setattr(operator, "prepare_one_contract_release", prepare)
    monkeypatch.setattr(operator, "commit_atomic_release", commit)
    monkeypatch.setattr(operator, "execute_one_contract_live_canary", execute)
    monkeypatch.setattr(operator, "reconcile_one_contract_live_canary", reconcile)

    result = operator.run_operator_canary(
        operator_authorization=auth,
        preflight_payload={"preflight": "exact"},
        m27h_payload=m27h,
        preview=p,
        approval=a,
        envelope=SimpleNamespace(execution_id="execution-m27o-operator"),  # type: ignore[arg-type]
        risk_authorization=object(),  # type: ignore[arg-type]
        canary_store=SimpleNamespace(path=tmp_path / "state.db"),  # type: ignore[arg-type]
        authorization_store=object(),  # type: ignore[arg-type]
        credential_store=object(),  # type: ignore[arg-type]
        journal=object(),  # type: ignore[arg-type]
        clock=Clock(),
    )
    assert calls == ["A", "B", "C", "D"]
    assert result.authorization_hash == auth.content_hash
    assert result.release is r
    assert result.atomic_commit is c
    assert result.live_outcome is live_outcome
    assert result.reconciliation is reconciliation


def test_expired_operator_authority_fails_before_atomic_burn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p = preview()
    a = approval(p)
    auth = operator.issue_operator_execution_authorization(
        preview=p,
        approval=a,
        confirmation=operator.EXACT_REAL_MONEY_CONFIRMATION,
        now=NOW,
    )
    r = release(p, a)
    calls: list[str] = []
    monkeypatch.setattr(operator, "prepare_one_contract_release", lambda **_kwargs: r)
    monkeypatch.setattr(operator, "commit_atomic_release", lambda **_kwargs: calls.append("B"))
    with pytest.raises(operator.M27OOperatorError, match="expired"):
        operator.run_operator_canary(
            operator_authorization=auth,
            preflight_payload={},
            m27h_payload={},
            preview=p,
            approval=a,
            envelope=SimpleNamespace(execution_id="execution"),  # type: ignore[arg-type]
            risk_authorization=object(),  # type: ignore[arg-type]
            canary_store=SimpleNamespace(path=Path("state.db")),  # type: ignore[arg-type]
            authorization_store=object(),  # type: ignore[arg-type]
            credential_store=object(),  # type: ignore[arg-type]
            journal=object(),  # type: ignore[arg-type]
            clock=Clock(auth.expires_at),
        )
    assert calls == []


def test_operator_runner_exposes_no_generic_sender_or_secret_surface() -> None:
    parameters = inspect.signature(operator.run_operator_canary).parameters
    for forbidden in (
        "sender",
        "transport",
        "url",
        "origin",
        "method",
        "private_key",
        "credential",
    ):
        assert forbidden not in parameters
    source = inspect.getsource(operator.run_operator_canary)
    assert "send_exact" not in source
    assert "FixedKalshiProductionTransport" not in source
    assert "RequestSigner" not in source
    assert "_decode_committed_credential" not in source
