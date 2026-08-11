"""Deterministic non-active M17 bounded-autonomy governance artifacts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum


class AutonomyState(StrEnum):
    OFF = "OFF"


class EvidenceState(StrEnum):
    VERIFIED = "VERIFIED"
    NOT_VERIFIED = "NOT_VERIFIED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class BoundedAutonomyPolicy:
    """Research governance ceiling, not an activation or capital authorization."""

    policy_id: str = "m17-offline-policy"
    version: str = "1"
    maximum_order_quantity: Decimal = Decimal("1.00")
    maximum_concurrent_orders: int = 1
    maximum_markets: int = 1
    human_approval_required: bool = True
    automatic_scaling_allowed: bool = False
    production_activation_available: bool = False
    production_state: AutonomyState = AutonomyState.OFF

    def __post_init__(self) -> None:
        if self.maximum_order_quantity != Decimal("1.00"):
            raise ValueError("M17 research ceiling cannot exceed one contract")
        if self.maximum_concurrent_orders != 1 or self.maximum_markets != 1:
            raise ValueError("M17 cannot broaden concurrent production authority")
        if not self.human_approval_required or self.automatic_scaling_allowed:
            raise ValueError("human approval is mandatory and scaling is forbidden")
        if self.production_activation_available or self.production_state != AutonomyState.OFF:
            raise ValueError("M17 autonomy must remain OFF")


@dataclass(frozen=True, slots=True)
class AutonomyEvidence:
    supervised_canary_live_verified: bool
    canary_operationally_complete: bool
    canary_reconciled: bool
    no_unknown_orders: bool
    no_unknown_positions: bool
    real_reconciliation_verified: bool
    strategy_evidence_sufficient: bool
    drawdown_acceptable: bool
    concentration_acceptable: bool
    m13_limits_current: bool
    m14_demo_current: bool
    signer_runtime_verified: bool
    postgres_concurrency_verified: bool
    api_compatibility_current: bool
    production_read_current: bool
    compliance_clear: bool
    global_halt_clear: bool
    kills_clear: bool
    write_credential_installed: bool
    human_governance_approved: bool

    def state(self) -> AutonomyState:
        """Evidence can inform governance but cannot activate autonomy in M17."""
        return AutonomyState.OFF

    def missing(self) -> tuple[str, ...]:
        return tuple(name for name in self.__dataclass_fields__ if getattr(self, name) is not True)

    def classifications(self) -> dict[str, EvidenceState]:
        return {
            name: EvidenceState.VERIFIED
            if getattr(self, name) is True
            else EvidenceState.NOT_VERIFIED
            for name in self.__dataclass_fields__
        }


@dataclass(frozen=True, slots=True)
class AutonomyReadinessSnapshot:
    snapshot_id: str
    observed_at: datetime
    evidence: AutonomyEvidence
    policy_version: str
    code_sha: str
    content_hash: str = ""

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("readiness timestamp must be timezone aware")
        payload = json.dumps(
            {
                "snapshot_id": self.snapshot_id,
                "observed_at": self.observed_at.astimezone(UTC).isoformat(),
                "evidence": self.evidence.classifications(),
                "policy_version": self.policy_version,
                "code_sha": self.code_sha,
                "state": AutonomyState.OFF,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
        object.__setattr__(self, "content_hash", hashlib.sha256(payload).hexdigest())


@dataclass(frozen=True, slots=True)
class BoundedAutonomyProposal:
    """Human-review proposal only; it has no execution or activation authority."""

    proposal_id: str
    readiness_hash: str
    created_at: datetime
    expires_at: datetime
    rationale: str
    requested_state: AutonomyState = AutonomyState.OFF
    production_influence: str = "NONE"
    content_hash: str = ""

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("proposal timestamps must be timezone aware")
        if not self.rationale.strip():
            raise ValueError("proposal rationale is required")
        if self.requested_state != AutonomyState.OFF or self.production_influence != "NONE":
            raise ValueError("M17 proposals cannot activate production")
        if self.expires_at <= self.created_at or self.expires_at - self.created_at > timedelta(
            days=7
        ):
            raise ValueError("proposal lifetime must be positive and at most seven days")
        payload = json.dumps(
            [
                self.proposal_id,
                self.readiness_hash,
                self.created_at.isoformat(),
                self.expires_at.isoformat(),
                self.rationale,
                self.requested_state,
                self.production_influence,
            ],
            separators=(",", ":"),
        ).encode()
        object.__setattr__(self, "content_hash", hashlib.sha256(payload).hexdigest())


@dataclass(frozen=True, slots=True)
class AutonomyDecision:
    state: AutonomyState
    missing_gates: tuple[str, ...]
    production_state: str = "DISARMED"
    production_write_credential: str = "NONE"
    real_money_execution_authorized: bool = False


def evaluate_autonomy(
    snapshot: AutonomyReadinessSnapshot, policy: BoundedAutonomyPolicy | None = None
) -> AutonomyDecision:
    policy = policy or BoundedAutonomyPolicy()
    if policy.production_state != AutonomyState.OFF:
        raise ValueError("invalid M17 policy state")
    return AutonomyDecision(AutonomyState.OFF, snapshot.evidence.missing())
