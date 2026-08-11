from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from services.operations.api_compatibility import ApiContractEvidence
from services.operations.backup import BackupArtifact, BackupManifest, RestoreDrill
from services.operations.domain import (
    DependencyObservation,
    DependencyState,
    OperationalBudget,
    OperationalSnapshot,
    ProductionSafetyState,
    ReadinessState,
    restart_recovery_decision,
)
from services.operations.observability import (
    MetricRegistry,
    TraceContext,
    safe_health_payload,
    structured_event,
)
from services.operations.queues import QueuePolicy
from services.reporting_service.support_snapshot import redact

NOW = datetime(2026, 8, 11, 12, tzinfo=UTC)


def dependencies(
    state: DependencyState = DependencyState.HEALTHY,
) -> tuple[DependencyObservation, ...]:
    return tuple(
        DependencyObservation(name, state, NOW, timedelta(seconds=30))
        for name in ("postgres", "redis", "nats", "object_storage", "market_data", "account_read")
    )


def operational_snapshot(**changes: object) -> OperationalSnapshot:
    values: dict[str, object] = {
        "snapshot_id": "ops-1",
        "observed_at": NOW,
        "dependencies": dependencies(),
        "reconciliation_current": True,
        "no_unknown_orders": True,
        "no_unknown_positions": True,
        "global_halt_clear": True,
        "compliance_clear": True,
        "kill_states_clear": True,
        "clock_safe": True,
        "disk_safe": True,
        "backup_current": True,
        "monitoring_current": True,
    }
    values.update(changes)
    return OperationalSnapshot(**values)  # type: ignore[arg-type]


def test_operational_readiness_is_content_addressed_and_fail_closed() -> None:
    snapshot = operational_snapshot()
    assert snapshot.readiness(NOW + timedelta(seconds=10)) is ReadinessState.READY_READ_ONLY
    assert snapshot.blockers(NOW + timedelta(seconds=10)) == ()
    assert len(snapshot.content_hash) == 64
    stale = replace(snapshot, snapshot_id="ops-2", observed_at=NOW + timedelta(minutes=1))
    assert stale.readiness(NOW + timedelta(minutes=1)) is ReadinessState.NOT_READY
    assert "DEPENDENCY_POSTGRES_STALE" in stale.blockers(NOW + timedelta(minutes=1))


@pytest.mark.parametrize(
    ("field", "blocker"),
    (
        ("reconciliation_current", "RECONCILIATION_STALE"),
        ("no_unknown_orders", "UNKNOWN_ORDERS"),
        ("no_unknown_positions", "UNKNOWN_POSITIONS"),
        ("global_halt_clear", "GLOBAL_HALT"),
        ("compliance_clear", "COMPLIANCE_BLOCK"),
        ("kill_states_clear", "KILL_STATE"),
        ("clock_safe", "CLOCK_UNSAFE"),
        ("disk_safe", "DISK_UNSAFE"),
        ("backup_current", "BACKUP_STALE"),
        ("monitoring_current", "MONITORING_STALE"),
    ),
)
def test_each_safety_failure_blocks_readiness(field: str, blocker: str) -> None:
    snapshot = operational_snapshot(**{field: False})
    assert snapshot.readiness(NOW) is ReadinessState.NOT_READY
    assert blocker in snapshot.blockers(NOW)


def test_dependency_failure_unknown_and_clock_regression_fail_closed() -> None:
    for state in (DependencyState.DEGRADED, DependencyState.FAILED, DependencyState.UNKNOWN):
        snapshot = operational_snapshot(dependencies=dependencies(state))
        assert snapshot.readiness(NOW) is ReadinessState.NOT_READY
    assert operational_snapshot().readiness(NOW - timedelta(seconds=1)) is ReadinessState.NOT_READY


def test_restart_never_arms_retries_or_enables_autonomy(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ARM", "LIVE", "AUTONOMY", "PRODUCTION_TRADING"):
        monkeypatch.setenv(name, "true")
    decision = restart_recovery_decision()
    assert decision.production_state is ProductionSafetyState.DISARMED
    assert decision.autonomy_state == "OFF"
    assert decision.write_credential_state == "NONE"
    assert decision.permit_new_risk is False
    assert decision.resume_unknown_mutations is False
    assert decision.reconcile_execution_journals is True
    assert decision.recover_risk_reservations is True
    assert "PRODUCTION_WRITE_KEY" not in os.environ


def test_operational_budget_uses_decimal_and_hard_cap() -> None:
    assert OperationalBudget().state == "NOT_VERIFIED"
    assert OperationalBudget(observed_monthly_usd=Decimal("25.00")).state == "WITHIN_TARGET"
    assert OperationalBudget(observed_monthly_usd=Decimal("25.01")).state == "ABOVE_TARGET"
    assert OperationalBudget(observed_monthly_usd=Decimal("50.00")).state == "HARD_CAP_REACHED"
    with pytest.raises(ValueError):
        OperationalBudget(hard_cap_monthly_usd=Decimal("51.00"))


def test_structured_logs_metrics_and_health_are_secret_safe() -> None:
    trace = TraceContext("a" * 32, "b" * 16)
    event = json.loads(
        structured_event(
            "DEPENDENCY_FAILED",
            {
                "dependency": "postgres",
                "password": "should-not-leak",
                "signature": "should-not-leak",
                "account_id": "should-not-leak",
            },
            trace,
        )
    )
    assert event["fields"]["dependency"] == "postgres"
    assert set(event["fields"].values()) == {"postgres", "[REDACTED]"}
    assert event["trace_id"] == "a" * 32 and event["span_id"] == "b" * 16
    with pytest.raises(ValueError):
        TraceContext("not-safe", "also-not-safe")
    registry = MetricRegistry()
    registry.set("queue_depth", 7)
    registry.set("monthly_cost_usd", Decimal("24.50"))
    metrics = registry.prometheus()
    assert "kpv3_queue_depth 7" in metrics
    assert "kpv3_monthly_cost_usd 24.50" in metrics
    with pytest.raises(ValueError):
        registry.set("api_key_id", 1)
    health = safe_health_payload(process_healthy=True, ready=False, blockers=("DB",), version="m18")
    assert (
        "DISARMED" in health
        and '"autonomy":"OFF"' in health
        and '"production_write_credential":"NONE"' in health
    )


def test_support_redaction_covers_headers_pem_and_identifiers() -> None:
    raw = {
        "account_id": "acct",
        "client_order_id": "order",
        "authorization": "Bearer secret",
        "nested": {"safe": "KALSHI-ACCESS-SIGNATURE"},
        "pem_value": "-----BEGIN PRIVATE KEY-----",
    }
    cleaned = redact(raw)
    assert isinstance(cleaned, dict)
    assert cleaned["account_id"] == "[REDACTED]"
    assert cleaned["client_order_id"] == "[REDACTED]"
    encoded = json.dumps(cleaned)
    for secret in ("Bearer secret", "KALSHI-ACCESS-SIGNATURE", "PRIVATE KEY"):
        assert secret not in encoded


def test_queue_backpressure_and_poison_message_policy() -> None:
    policy = QueuePolicy()
    assert policy.action(0) == "ACCEPT"
    assert policy.action(5_000) == "BACKPRESSURE_RESEARCH"
    assert policy.action(10_000) == "HALT_NEW_RISK"
    assert policy.action(1, attempts=5) == "DEAD_LETTER_AND_ALERT"
    with pytest.raises(ValueError):
        policy.action(-1)


def test_api_contract_drift_and_missing_evidence_block() -> None:
    digest = "a" * 64
    current = ApiContractEvidence(digest, digest, digest, digest, digest, digest)
    assert current.compatible() is True and current.blockers() == ()
    missing = replace(current, openapi_sha256=None)
    assert missing.compatible() is False and missing.blockers() == ("OPENAPI_NOT_VERIFIED",)
    drifted = replace(current, changelog_sha256="b" * 64)
    assert drifted.compatible() is False and drifted.blockers() == ("CHANGELOG_DRIFT",)


def test_encrypted_backup_manifest_and_isolated_restore_evidence() -> None:
    artifact = BackupArtifact("postgres", "postgres.dump.age", 123, "a" * 64)
    manifest = BackupManifest("backup-1", NOW, "0017", (artifact,), encrypted=True)
    assert len(manifest.content_hash) == 64
    with pytest.raises(ValueError):
        replace(manifest, encrypted=False)
    drill = RestoreDrill("drill-1", manifest.content_hash, NOW, True, True, True, True, True, True)
    assert drill.passed is True
    assert replace(drill, production_network_blocked=False).passed is False
    with pytest.raises(ValueError):
        replace(drill, performed_at=datetime(2026, 8, 11))


def test_operations_migration_is_disarmed_and_off() -> None:
    sql = Path("migrations/0017_operations_hardening.sql").read_text()
    assert "CHECK(production_state='DISARMED')" in sql
    assert "CHECK(autonomy_state='OFF')" in sql
    assert "production_write_credential" not in sql
    with sqlite3.connect(":memory:") as db:
        assert db.execute("SELECT 1").fetchone() == (1,)


def test_oracle_topology_and_recovery_scripts_are_hardened() -> None:
    compose = Path("docker-compose.yml").read_text()
    assert 'max-size: "10m"' in compose
    assert compose.count("restart: unless-stopped") >= 7
    assert "cap_drop: [ALL]" in compose
    assert "signer_internal" in compose and "internal: true" in compose
    assert "networks: [worker_egress, data, events]" in compose
    assert "PRODUCTION_STATE=DISARMED" in compose
    assert "PRODUCTION_TRADING" not in compose
    backup = Path("deploy/oracle/backup.sh").read_text()
    restore = Path("deploy/oracle/restore-drill.sh").read_text()
    assert "age -R" in backup and "pg_dump" in backup and "sha256sum" in backup
    assert "--network none" in restore and "pg_restore" in restore and "--exit-on-error" in restore
    assert "curl" not in backup + restore


def test_owner_system_status_is_truthful() -> None:
    source = Path("services/web_dashboard/app.py").read_text()
    assert "Monthly operations target / hard cap" in source
    assert "$25.00 / $50.00" in source
    assert "LIVE OPERATIONS NOT VERIFIED" in source
    assert 'production_state":"DISARMED' in source
    assert 'production_write_credential":"NONE' in source


def test_operations_package_has_no_execution_signer_or_network_client() -> None:
    source = "\n".join(path.read_text() for path in Path("services/operations").glob("*.py"))
    forbidden = (
        "production_execution",
        "sign(",
        "private_key",
        "httpx",
        "requests.",
        "urllib.request",
    )
    assert all(term not in source for term in forbidden)
