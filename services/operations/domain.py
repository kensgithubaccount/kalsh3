"""Fail-closed operational readiness and budget policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum


class DependencyState(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class ReadinessState(StrEnum):
    READY_READ_ONLY = "READY_READ_ONLY"
    DEGRADED_READ_ONLY = "DEGRADED_READ_ONLY"
    NOT_READY = "NOT_READY"


class ProductionSafetyState(StrEnum):
    DISARMED = "DISARMED"


@dataclass(frozen=True, slots=True)
class DependencyObservation:
    name: str
    state: DependencyState
    observed_at: datetime
    maximum_age: timedelta
    required_for_new_risk: bool = True
    detail_code: str = "NONE"

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("dependency observation must be timezone aware")
        if self.maximum_age <= timedelta(0):
            raise ValueError("maximum age must be positive")
        if not self.name or not self.detail_code:
            raise ValueError("safe dependency name and detail code are required")

    def current(self, now: datetime) -> bool:
        return now >= self.observed_at and now - self.observed_at <= self.maximum_age


@dataclass(frozen=True, slots=True)
class OperationalSnapshot:
    snapshot_id: str
    observed_at: datetime
    dependencies: tuple[DependencyObservation, ...]
    reconciliation_current: bool
    no_unknown_orders: bool
    no_unknown_positions: bool
    global_halt_clear: bool
    compliance_clear: bool
    kill_states_clear: bool
    clock_safe: bool
    disk_safe: bool
    backup_current: bool
    monitoring_current: bool
    content_hash: str = ""

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("snapshot time must be timezone aware")
        names = [item.name for item in self.dependencies]
        if len(names) != len(set(names)):
            raise ValueError("dependency observations must be unique")
        payload = json.dumps(
            {
                "snapshot_id": self.snapshot_id,
                "observed_at": self.observed_at.astimezone(UTC).isoformat(),
                "dependencies": [
                    {
                        "name": item.name,
                        "state": item.state,
                        "observed_at": item.observed_at.astimezone(UTC).isoformat(),
                        "maximum_age_seconds": item.maximum_age.total_seconds(),
                        "required_for_new_risk": item.required_for_new_risk,
                        "detail_code": item.detail_code,
                    }
                    for item in self.dependencies
                ],
                "safety": [
                    self.reconciliation_current,
                    self.no_unknown_orders,
                    self.no_unknown_positions,
                    self.global_halt_clear,
                    self.compliance_clear,
                    self.kill_states_clear,
                    self.clock_safe,
                    self.disk_safe,
                    self.backup_current,
                    self.monitoring_current,
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        object.__setattr__(self, "content_hash", hashlib.sha256(payload).hexdigest())

    def blockers(self, now: datetime) -> tuple[str, ...]:
        blockers = [
            f"DEPENDENCY_{item.name.upper()}_{'STALE' if not item.current(now) else item.state}"
            for item in self.dependencies
            if item.required_for_new_risk
            and (not item.current(now) or item.state is not DependencyState.HEALTHY)
        ]
        gates = {
            "RECONCILIATION_STALE": self.reconciliation_current,
            "UNKNOWN_ORDERS": self.no_unknown_orders,
            "UNKNOWN_POSITIONS": self.no_unknown_positions,
            "GLOBAL_HALT": self.global_halt_clear,
            "COMPLIANCE_BLOCK": self.compliance_clear,
            "KILL_STATE": self.kill_states_clear,
            "CLOCK_UNSAFE": self.clock_safe,
            "DISK_UNSAFE": self.disk_safe,
            "BACKUP_STALE": self.backup_current,
            "MONITORING_STALE": self.monitoring_current,
        }
        blockers.extend(code for code, clear in gates.items() if not clear)
        return tuple(blockers)

    def readiness(self, now: datetime) -> ReadinessState:
        if self.blockers(now):
            return ReadinessState.NOT_READY
        if any(item.state is not DependencyState.HEALTHY for item in self.dependencies):
            return ReadinessState.DEGRADED_READ_ONLY
        return ReadinessState.READY_READ_ONLY


@dataclass(frozen=True, slots=True)
class OperationalBudget:
    target_monthly_usd: Decimal = Decimal("25.00")
    hard_cap_monthly_usd: Decimal = Decimal("50.00")
    observed_monthly_usd: Decimal | None = None

    def __post_init__(self) -> None:
        if self.target_monthly_usd != Decimal("25.00"):
            raise ValueError("M18 target must remain $25/month")
        if self.hard_cap_monthly_usd != Decimal("50.00"):
            raise ValueError("M18 hard cap must remain $50/month")
        if self.observed_monthly_usd is not None and self.observed_monthly_usd < 0:
            raise ValueError("observed cost cannot be negative")

    @property
    def state(self) -> str:
        if self.observed_monthly_usd is None:
            return "NOT_VERIFIED"
        if self.observed_monthly_usd >= self.hard_cap_monthly_usd:
            return "HARD_CAP_REACHED"
        if self.observed_monthly_usd > self.target_monthly_usd:
            return "ABOVE_TARGET"
        return "WITHIN_TARGET"


@dataclass(frozen=True, slots=True)
class RestartRecoveryDecision:
    production_state: ProductionSafetyState = ProductionSafetyState.DISARMED
    autonomy_state: str = "OFF"
    write_credential_state: str = "NONE"
    permit_new_risk: bool = False
    reconcile_execution_journals: bool = True
    recover_risk_reservations: bool = True
    resume_unknown_mutations: bool = False


def restart_recovery_decision() -> RestartRecoveryDecision:
    """Restart is never an activation event and never retries a mutation."""
    return RestartRecoveryDecision()
