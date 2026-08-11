import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from services.supervised_canary.domain import (
    CanaryState,
    HumanCanaryApproval,
    HumanCanaryPreview,
)
from services.supervised_canary.readiness import FinalCanaryState, ReadinessSnapshot
from services.supervised_canary.store import CanaryStore
from services.supervised_canary.workflow import evaluate_preview, final_revalidation
from services.web_dashboard.app import DashboardApp
from services.web_dashboard.security import SecretBox, hash_password
from services.web_dashboard.store import StateStore

NOW = datetime(2026, 8, 11, 12, tzinfo=UTC)


def preview(**changes: object) -> HumanCanaryPreview:
    values: dict[str, object] = dict(
        preview_id="preview-1",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=2),
        candidate_id="candidate-1",
        market_ticker="M",
        event_ticker="E",
        market_title="Will the event occur?",
        resolution_question="Resolves YES if source publishes X.",
        close_time=NOW + timedelta(days=1),
        rules_version="r1",
        rules_hash="rules",
        settlement_source="official source",
        forecast_version="f1",
        independent_forecast=Decimal(".60"),
        market_reference=Decimal(".50"),
        uncertainty=Decimal(".04"),
        selected_outcome="BUY YES",
        limit_price=Decimal(".4567"),
        quantity=Decimal("1.00"),
        maximum_fee=Decimal(".02"),
        maximum_commitment=Decimal(".4767"),
        maximum_loss=Decimal(".4767"),
        current_market_risk=Decimal("1"),
        projected_market_risk=Decimal("1.4767"),
        current_event_risk=Decimal("4"),
        projected_event_risk=Decimal("4.4767"),
        current_aggregate_risk=Decimal("20"),
        projected_aggregate_risk=Decimal("20.4767"),
        protected_reserve=Decimal("700"),
        after_cost_value=Decimal(".03"),
        execution_style="POST_ONLY_GTC",
        post_only=True,
        reduce_only=False,
        cancel_order_on_pause=True,
        stp_policy="cancel_newest",
        order_group_policy="required",
        client_order_id="kalsh3-v1-canary-1",
        reconciliation_version="rec1",
        market_data_version="book1",
        api_compatibility_version="NOT VERIFIED",
        evidence_mode="REAL REQUIRED",
        subaccount=0,
    )
    values.update(changes)
    return HumanCanaryPreview(**values)  # type: ignore[arg-type]


def readiness(all_live: bool = False, **changes: object) -> ReadinessSnapshot:
    values: dict[str, object] = {
        name: all_live
        for name in (
            "m13_verified",
            "m14_live_demo_verified",
            "m15_path_verified",
            "production_deployment_live_verified",
            "production_read_live_verified",
            "real_reconciliation_live_verified",
            "api_compatibility_live_verified",
            "postgres_concurrency_live_verified",
            "signer_runtime_live_verified",
            "model_eligible",
            "source_evidence_ready",
            "write_credential_installed",
            "compliance_clear",
            "global_halt_clear",
            "kills_clear",
            "exchange_active",
            "trading_active",
            "market_tradable",
            "rules_current",
            "candidate_current",
            "fee_verified",
            "account_reconciled",
            "no_unknown_orders",
            "no_unknown_positions",
            "clock_safe",
            "write_budget_safe",
            "read_budget_safe",
        )
    }
    values.update(user_data_timestamp=NOW if all_live else None, observed_at=NOW)
    values.update(changes)
    return ReadinessSnapshot(**values)  # type: ignore[arg-type]


def approval(p: HumanCanaryPreview, **changes: object) -> HumanCanaryApproval:
    values: dict[str, object] = dict(
        approval_id="approval-1",
        owner_identity="owner",
        preview_hash=p.content_hash,
        candidate_id=p.candidate_id,
        intent_hash="intent",
        exact_price=p.limit_price,
        exact_quantity=p.quantity,
        maximum_fee=p.maximum_fee,
        maximum_loss=p.maximum_loss,
        rules_hash=p.rules_hash,
        reconciliation_version=p.reconciliation_version,
        production_read_state="LIVE VERIFIED",
        approved_at=NOW,
        expires_at=NOW + timedelta(seconds=60),
        reason="first supervised canary",
        confirmation="APPROVE THIS ONE-CONTRACT CANARY",
        step_up_proof_reference="reauth-event-1",
    )
    values.update(changes)
    return HumanCanaryApproval(**values)  # type: ignore[arg-type]


def test_real_gates_fail_closed_and_user_data_freshness_is_30_seconds() -> None:
    decision = evaluate_preview(preview(), readiness(), NOW)
    assert decision.state == CanaryState.APPROVAL_UNAVAILABLE
    assert "m14_live_demo_verified" in decision.missing_gates
    assert "production_read_live_verified" in decision.missing_gates
    assert "user_data_fresh" in decision.missing_gates
    assert evaluate_preview(preview(), readiness(True), NOW).state == CanaryState.READY_FOR_APPROVAL
    stale = readiness(True, user_data_timestamp=NOW - timedelta(seconds=31))
    assert not stale.approvable(NOW)


def test_exactly_one_contract_and_two_minute_preview_are_immutable() -> None:
    with pytest.raises(ValueError, match=r"exactly 1\.00"):
        preview(quantity=Decimal("1.01"))
    with pytest.raises(ValueError, match="TTL"):
        preview(expires_at=NOW + timedelta(minutes=3))
    assert preview().limit_price == Decimal(".4567")


def test_step_up_auth_and_single_use_approval(tmp_path: Path) -> None:
    p, a, store = preview(), None, CanaryStore(tmp_path / "canary.db")
    store.add_preview(p)
    a = approval(p)
    with pytest.raises(PermissionError):
        store.issue_approval(
            a,
            preview=p,
            now=NOW,
            authenticated_session=True,
            recent_session=True,
            password_valid=False,
            totp_valid=True,
            csrf_valid=True,
            rate_limit_clear=True,
        )
    store.issue_approval(
        a,
        preview=p,
        now=NOW,
        authenticated_session=True,
        recent_session=True,
        password_valid=True,
        totp_valid=True,
        csrf_valid=True,
        rate_limit_clear=True,
    )
    assert store.consume_approval(
        a.approval_id, owner="owner", preview_hash=p.content_hash, now=NOW
    )
    assert not store.consume_approval(
        a.approval_id, owner="owner", preview_hash=p.content_hash, now=NOW
    )


def test_one_canary_concurrency_is_durable(tmp_path: Path) -> None:
    store = CanaryStore(tmp_path / "race.db")

    def open_one(index: int) -> bool:
        return store.open_session(
            session_id=f"s{index}",
            preview_id=f"p{index}",
            approval_id=f"a{index}",
            client_order_id=f"c{index}",
            now=NOW,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(open_one, (1, 2))) == 1


@pytest.mark.parametrize(
    "change",
    (
        "exact_price_unchanged",
        "exact_quantity_unchanged",
        "rules_hash_unchanged",
        "candidate_unchanged",
        "forecast_current",
        "fresh_m13_passed",
        "fresh_m13_authorization_issued",
        "m15_body_hash_matches",
    ),
)
def test_final_revalidation_cannot_be_overridden(change: str) -> None:
    p = preview()
    a = approval(p)
    final = FinalCanaryState(
        p.content_hash,
        a.content_hash,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        readiness(True),
    )
    result = final_revalidation(p, a, replace(final, **{change: False}), NOW)
    assert result.state == CanaryState.CANARY_FAILED and not result.send_permitted


def test_price_change_rules_change_and_expiry_invalidate_approval() -> None:
    p = preview()
    a = approval(p)
    final = FinalCanaryState(
        p.content_hash,
        a.content_hash,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        readiness(True),
    )
    assert (
        final_revalidation(p, replace(a, exact_price=Decimal(".45")), final, NOW).state
        == CanaryState.CANARY_FAILED
    )
    assert (
        final_revalidation(p, replace(a, rules_hash="changed"), final, NOW).state
        == CanaryState.CANARY_FAILED
    )
    assert (
        final_revalidation(p, replace(a, expires_at=NOW), final, NOW).state
        == CanaryState.CANARY_FAILED
    )


def test_partial_fill_counter_and_restart_disarm(tmp_path: Path) -> None:
    store = CanaryStore(tmp_path / "fills.db")
    assert store.open_session(
        session_id="s", preview_id="p", approval_id="a", client_order_id="c", now=NOW
    )
    store.record_fill("s", filled=Decimal(".40"), mode="REAL_PRODUCTION")
    store.record_fill("s", filled=Decimal(".70"), mode="DEMO")
    store.resolve("s", CanaryState.SUBMITTED_OR_UNKNOWN, NOW)
    assert store.recover() == ("s",)
    assert not store.open_session(
        session_id="s2", preview_id="p2", approval_id="a2", client_order_id="c2", now=NOW
    )


def test_canary_ui_is_unmistakably_real_but_unavailable(tmp_path: Path) -> None:
    state = StateStore(tmp_path / "state.db")
    state.set_config("owner", "owner")
    state.set_config("password_hash", hash_password("LongProduction9Password"))
    state.set_config("vault", "sealed")
    app = DashboardApp(state, SecretBox(b"x" * 32))
    token, _ = state.create_session(int(time.time()))
    environ = {
        "PATH_INFO": "/canary",
        "REQUEST_METHOD": "GET",
        "HTTP_COOKIE": f"session={token}",
        "QUERY_STRING": "",
        "wsgi.input": __import__("io").BytesIO(b""),
    }
    result: list[str] = []
    body = b"".join(app(environ, lambda status, headers: result.append(status)))
    assert result[0] == "200 OK"
    assert b"REAL PRODUCTION" in body and b"REAL MONEY" in body
    assert b"NOT AVAILABLE" in body and b"APPROVE THIS ONE-CONTRACT CANARY" in body
    assert b"NOT VERIFIED" in body and b"ARM" not in body
