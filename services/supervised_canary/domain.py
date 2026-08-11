"""Human-review canary previews with no arming or execution capability."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum


class CanaryState(StrEnum):
    DRAFT = "DRAFT"
    APPROVAL_UNAVAILABLE = "APPROVAL_UNAVAILABLE"
    EXPIRED = "EXPIRED"
    READY_FOR_APPROVAL = "READY_FOR_APPROVAL"
    AWAITING_REAUTH = "AWAITING_REAUTH"
    HUMAN_APPROVED = "HUMAN_APPROVED"
    FINAL_REVALIDATION = "FINAL_REVALIDATION"
    CANARY_AUTHORIZED = "CANARY_AUTHORIZED"
    SUBMISSION_PENDING = "SUBMISSION_PENDING"
    SUBMITTED_OR_UNKNOWN = "SUBMITTED_OR_UNKNOWN"
    RECONCILING = "RECONCILING"
    CANARY_COMPLETE = "CANARY_COMPLETE"
    CANARY_FAILED = "CANARY_FAILED"
    REVOKED = "REVOKED"


@dataclass(frozen=True, slots=True)
class CanaryPreview:
    preview_id: str
    candidate_id: str
    risk_decision_id: str
    intent_hash: str
    market: str
    outcome_side: str
    price: Decimal
    quantity: Decimal
    maximum_loss: Decimal
    created_at: datetime
    expires_at: datetime
    state: CanaryState = CanaryState.DRAFT
    content_hash: str = ""

    def __post_init__(self) -> None:
        if self.quantity > Decimal(1) or self.quantity <= 0:
            raise ValueError("supervised canary is limited to one contract")
        if self.expires_at <= self.created_at or self.expires_at - self.created_at > timedelta(
            minutes=2
        ):
            raise ValueError("canary preview expiry must be two minutes or less")
        payload = json.dumps(
            [
                self.preview_id,
                self.candidate_id,
                self.risk_decision_id,
                self.intent_hash,
                self.market,
                self.outcome_side,
                str(self.price),
                str(self.quantity),
                str(self.maximum_loss),
                self.created_at.isoformat(),
                self.expires_at.isoformat(),
            ],
            separators=(",", ":"),
        ).encode()
        object.__setattr__(self, "content_hash", hashlib.sha256(payload).hexdigest())


@dataclass(frozen=True, slots=True)
class CanaryReadiness:
    deployed: bool
    production_read_verified: bool
    production_write_credential_installed: bool
    reconciled: bool
    strategy_evidence_passed: bool
    safety_review_passed: bool
    owner_password_reauthenticated: bool
    owner_totp_reauthenticated: bool
    explicit_first_trade_approval: bool

    def evaluate(self) -> CanaryState:
        # M16 initial boundary intentionally cannot return an armed/supervised state.
        return CanaryState.APPROVAL_UNAVAILABLE


class ApprovalState(StrEnum):
    ISSUED = "ISSUED"
    CONSUMED = "CONSUMED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


@dataclass(frozen=True, slots=True)
class HumanCanaryPreview:
    preview_id: str
    created_at: datetime
    expires_at: datetime
    candidate_id: str
    market_ticker: str
    event_ticker: str
    market_title: str
    resolution_question: str
    close_time: datetime
    rules_version: str
    rules_hash: str
    settlement_source: str
    forecast_version: str
    independent_forecast: Decimal
    market_reference: Decimal
    uncertainty: Decimal
    selected_outcome: str
    limit_price: Decimal
    quantity: Decimal
    maximum_fee: Decimal
    maximum_commitment: Decimal
    maximum_loss: Decimal
    current_market_risk: Decimal
    projected_market_risk: Decimal
    current_event_risk: Decimal
    projected_event_risk: Decimal
    current_aggregate_risk: Decimal
    projected_aggregate_risk: Decimal
    protected_reserve: Decimal
    after_cost_value: Decimal
    execution_style: str
    post_only: bool
    reduce_only: bool
    cancel_order_on_pause: bool
    stp_policy: str
    order_group_policy: str
    client_order_id: str
    reconciliation_version: str
    market_data_version: str
    api_compatibility_version: str
    evidence_mode: str
    subaccount: int = 0
    content_hash: str = ""

    def __post_init__(self) -> None:
        money = (
            self.limit_price,
            self.quantity,
            self.maximum_fee,
            self.maximum_commitment,
            self.maximum_loss,
            self.current_market_risk,
            self.projected_market_risk,
            self.current_event_risk,
            self.projected_event_risk,
            self.current_aggregate_risk,
            self.projected_aggregate_risk,
            self.protected_reserve,
            self.after_cost_value,
        )
        if not all(isinstance(value, Decimal) and value.is_finite() for value in money):
            raise TypeError("all canary financial values require finite Decimal")
        if self.quantity != Decimal("1.00") or self.subaccount != 0:
            raise ValueError("canary opening quantity must be exactly 1.00 on subaccount 0")
        if self.expires_at <= self.created_at or self.expires_at - self.created_at > timedelta(
            minutes=2
        ):
            raise ValueError("preview TTL exceeds two minutes")
        payload = json.dumps(
            {
                name: str(getattr(self, name))
                for name in self.__dataclass_fields__
                if name != "content_hash"
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        object.__setattr__(self, "content_hash", hashlib.sha256(payload).hexdigest())


@dataclass(frozen=True, slots=True)
class HumanCanaryApproval:
    approval_id: str
    owner_identity: str
    preview_hash: str
    candidate_id: str
    intent_hash: str
    exact_price: Decimal
    exact_quantity: Decimal
    maximum_fee: Decimal
    maximum_loss: Decimal
    rules_hash: str
    reconciliation_version: str
    production_read_state: str
    approved_at: datetime
    expires_at: datetime
    reason: str
    confirmation: str
    step_up_proof_reference: str
    state: ApprovalState = ApprovalState.ISSUED
    content_hash: str = ""

    def __post_init__(self) -> None:
        if self.exact_quantity != Decimal("1.00") or self.expires_at - self.approved_at > timedelta(
            seconds=60
        ):
            raise ValueError("approval must bind one contract for no more than 60 seconds")
        if self.confirmation != "APPROVE THIS ONE-CONTRACT CANARY":
            raise ValueError("explicit canary confirmation required")
        payload = json.dumps(
            [
                str(getattr(self, name))
                for name in self.__dataclass_fields__
                if name != "content_hash"
            ],
            separators=(",", ":"),
        ).encode()
        object.__setattr__(self, "content_hash", hashlib.sha256(payload).hexdigest())
