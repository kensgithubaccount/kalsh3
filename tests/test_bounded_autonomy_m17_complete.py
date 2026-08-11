from __future__ import annotations

import os
import sqlite3
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from services.bounded_autonomy.domain import (
    AutonomyEvidence,
    AutonomyReadinessSnapshot,
    AutonomyState,
    BoundedAutonomyPolicy,
    BoundedAutonomyProposal,
    EvidenceState,
    evaluate_autonomy,
)
from services.bounded_autonomy.store import AutonomyStore


def evidence(value: bool = False) -> AutonomyEvidence:
    return AutonomyEvidence(**{name: value for name in AutonomyEvidence.__dataclass_fields__})


def snapshot(value: bool = False) -> AutonomyReadinessSnapshot:
    return AutonomyReadinessSnapshot(
        snapshot_id="snapshot-1",
        observed_at=datetime(2026, 8, 11, tzinfo=UTC),
        evidence=evidence(value),
        policy_version="1",
        code_sha="synthetic-code-sha",
    )


def test_all_evidence_cannot_activate_autonomy() -> None:
    item = snapshot(True)
    decision = evaluate_autonomy(item)
    assert item.evidence.state() is AutonomyState.OFF
    assert decision.state is AutonomyState.OFF
    assert decision.production_state == "DISARMED"
    assert decision.production_write_credential == "NONE"
    assert decision.real_money_execution_authorized is False
    assert decision.missing_gates == ()
    assert tuple(AutonomyState) == (AutonomyState.OFF,)


def test_missing_evidence_is_explicit_and_fail_closed() -> None:
    item = evidence()
    assert set(item.missing()) == set(AutonomyEvidence.__dataclass_fields__)
    assert set(item.classifications().values()) == {EvidenceState.NOT_VERIFIED}
    improved = replace(item, m14_demo_current=True)
    assert improved.state() is AutonomyState.OFF
    assert improved.classifications()["m14_demo_current"] is EvidenceState.VERIFIED


def test_policy_cannot_broaden_or_activate_authority() -> None:
    policy = BoundedAutonomyPolicy()
    assert policy.maximum_order_quantity == Decimal("1.00")
    assert policy.production_state is AutonomyState.OFF
    with pytest.raises(FrozenInstanceError):
        policy.maximum_concurrent_orders = 2  # type: ignore[misc]
    unsafe = (
        {"maximum_order_quantity": Decimal("1.01")},
        {"maximum_concurrent_orders": 2},
        {"maximum_markets": 2},
        {"human_approval_required": False},
        {"automatic_scaling_allowed": True},
        {"production_activation_available": True},
    )
    for change in unsafe:
        with pytest.raises(ValueError):
            replace(policy, **change)  # type: ignore[arg-type]


def test_snapshot_and_proposal_are_content_addressed_and_off_only() -> None:
    item = snapshot()
    assert len(item.content_hash) == 64
    assert replace(item, code_sha="different").content_hash != item.content_hash
    proposal = BoundedAutonomyProposal(
        proposal_id="proposal-1",
        readiness_hash=item.content_hash,
        created_at=item.observed_at,
        expires_at=item.observed_at + timedelta(hours=1),
        rationale="Governance review of missing evidence; no activation.",
    )
    assert proposal.requested_state is AutonomyState.OFF
    assert proposal.production_influence == "NONE"
    with pytest.raises(ValueError):
        replace(proposal, production_influence="EXECUTION")
    with pytest.raises(ValueError):
        replace(proposal, expires_at=item.observed_at + timedelta(days=8))
    with pytest.raises(ValueError):
        replace(proposal, rationale="  ")
    with pytest.raises(ValueError):
        replace(proposal, created_at=datetime(2026, 8, 11))


def test_store_constraints_and_restart_force_off_disarmed(tmp_path: Path) -> None:
    store = AutonomyStore(tmp_path / "autonomy.sqlite3")
    item = snapshot()
    store.persist_snapshot(item)
    store.persist_proposal(
        BoundedAutonomyProposal(
            proposal_id="proposal-1",
            readiness_hash=item.content_hash,
            created_at=item.observed_at,
            expires_at=item.observed_at + timedelta(minutes=5),
            rationale="Offline governance only.",
        )
    )
    with sqlite3.connect(store.path) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE autonomy_runtime SET autonomy_state='ON' WHERE singleton=1")
    assert AutonomyStore(store.path).recover() == ("OFF", "DISARMED")


def test_malicious_environment_cannot_arm_or_enable_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("AUTONOMY", "ARM", "LIVE", "PRODUCTION_TRADING"):
        monkeypatch.setenv(name, "true")
    decision = evaluate_autonomy(snapshot(True))
    assert decision == replace(decision)
    assert decision.state is AutonomyState.OFF
    assert decision.production_state == "DISARMED"
    assert decision.real_money_execution_authorized is False
    assert not any(name in os.environ for name in ("PRODUCTION_WRITE_KEY", "KALSHI_WRITE_PEM"))


def test_m17_has_no_signer_transport_or_execution_dependency() -> None:
    root = Path("services/bounded_autonomy")
    source = "\n".join(path.read_text() for path in root.glob("*.py"))
    forbidden = (
        "production_execution",
        "demo_execution",
        "requests.post",
        "httpx",
        "urllib.request",
        "sign(",
        "private_key",
        "KALSHI-ACCESS-SIGNATURE",
    )
    assert all(term not in source for term in forbidden)


def test_migration_structurally_prevents_activation() -> None:
    sql = Path("migrations/0016_bounded_autonomy_off.sql").read_text()
    assert "CHECK(autonomy_state='OFF')" in sql
    assert "CHECK(production_state='DISARMED')" in sql
    assert "CHECK(production_write_credential='NONE')" in sql
    assert "CHECK(requested_state='OFF')" in sql
    assert "CHECK(production_influence='NONE')" in sql


def test_owner_surface_is_truthful_and_has_no_activation_control() -> None:
    from services.web_dashboard.app import DashboardApp

    page = DashboardApp._bounded_autonomy()
    assert "AUTONOMY OFF" in page
    assert "DISARMED" in page
    assert "Production write credential" in page and "NONE" in page
    assert "NOT VERIFIED" in page
    assert "cannot activate production" in page
    assert "<button" not in page
