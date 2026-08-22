"""M27O operator-owned orchestration for exactly one supervised real-money canary.

This module does not add a generic production executor.  It joins the already-reviewed M27O
A-D boundaries in their only permitted order after a second, explicit real-money human
authorization has been bound to the exact M16 preview and approval.

The human authorization deliberately exists *before* the five-second M13/M15 execution window:
a person can inspect the exact candidate/side/price/fee/loss terms for up to sixty seconds,
confirm the literal real-money phrase, and only then may the caller mint the short-lived risk
authorization/envelope and invoke this runner.  The runner independently re-binds those fresh
artifacts to that prior authorization before Phase B burns any durable token.

There is no sender, transport, URL, host, method, private key, or raw credential argument.
Production mutation remains confined to ``production_execution.m27o_live_canary`` and
post-send recovery remains confined to ``production_execution.m27o_reconciliation``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from services.production_execution.domain import ProductionRequestEnvelope
from services.production_execution.enrollment import ProtectedWriteCredentialStore
from services.production_execution.m27o_live_canary import (
    LiveCanaryOutcome,
    execute_one_contract_live_canary,
)
from services.production_execution.m27o_reconciliation import (
    PostSendReconciliation,
    reconcile_one_contract_live_canary,
)
from services.production_execution.store import ProductionJournal
from services.risk_engine.authorization import AuthorizationStore, RiskAuthorization

from .domain import HumanCanaryApproval, HumanCanaryPreview
from .m27o import (
    AtomicReleaseCommit,
    OneContractCanaryRelease,
    commit_atomic_release,
    prepare_one_contract_release,
)
from .store import CanaryStore

AUTHORIZATION_SCHEMA = "kalsh3.m27o.operator-real-money-authorization.v1"
AUTHORIZATION_TTL = timedelta(seconds=60)
EXACT_REAL_MONEY_CONFIRMATION = "EXECUTE THIS EXACT ONE-CONTRACT REAL-MONEY CANARY"
ONE_CONTRACT = Decimal("1.00")


class Clock(Protocol):
    def now(self) -> datetime: ...


class M27OOperatorError(PermissionError):
    """An operator-runner invariant failed before additional authority may be exercised."""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise M27OOperatorError("operator clock must be timezone-aware")
    return value.astimezone(UTC)


def _canonical_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class OperatorExecutionAuthorization:
    schema: str
    issued_at: datetime
    expires_at: datetime
    candidate_id: str
    market_ticker: str
    selected_side: str
    exact_price: Decimal
    exact_quantity: Decimal
    maximum_fee: Decimal
    maximum_loss: Decimal
    preview_hash: str
    approval_hash: str
    confirmation: str
    content_hash: str = ""

    def __post_init__(self) -> None:
        if self.schema != AUTHORIZATION_SCHEMA:
            raise ValueError("M27O operator authorization schema mismatch")
        if self.issued_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("M27O operator authorization timestamps must be timezone-aware")
        if (
            self.expires_at <= self.issued_at
            or self.expires_at - self.issued_at > AUTHORIZATION_TTL
        ):
            raise ValueError("M27O operator authorization TTL exceeds sixty seconds")
        if self.selected_side not in {"YES", "NO"} or self.exact_quantity != ONE_CONTRACT:
            raise ValueError("M27O operator authorization is not an exact one-contract BUY")
        if self.confirmation != EXACT_REAL_MONEY_CONFIRMATION:
            raise ValueError("exact M27O real-money confirmation required")
        object.__setattr__(self, "content_hash", _canonical_hash(self._material()))

    def _material(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "issued_at": self.issued_at.astimezone(UTC).isoformat(),
            "expires_at": self.expires_at.astimezone(UTC).isoformat(),
            "candidate_id": self.candidate_id,
            "market_ticker": self.market_ticker,
            "selected_side": self.selected_side,
            "exact_price": str(self.exact_price),
            "exact_quantity": str(self.exact_quantity),
            "maximum_fee": str(self.maximum_fee),
            "maximum_loss": str(self.maximum_loss),
            "preview_hash": self.preview_hash,
            "approval_hash": self.approval_hash,
            "confirmation": self.confirmation,
        }

    def to_json(self) -> dict[str, object]:
        return {**self._material(), "content_hash": self.content_hash}


@dataclass(frozen=True, slots=True)
class OperatorCanaryRun:
    authorization_hash: str
    release: OneContractCanaryRelease
    atomic_commit: AtomicReleaseCommit
    live_outcome: LiveCanaryOutcome
    reconciliation: PostSendReconciliation


def issue_operator_execution_authorization(
    *,
    preview: HumanCanaryPreview,
    approval: HumanCanaryApproval,
    confirmation: str,
    now: datetime,
) -> OperatorExecutionAuthorization:
    """Create the distinct, short-lived human authority for one exact real-money canary."""
    now = _utc(now)
    if not hmac.compare_digest(confirmation, EXACT_REAL_MONEY_CONFIRMATION):
        raise M27OOperatorError("exact real-money confirmation phrase did not match")
    if preview.quantity != ONE_CONTRACT or preview.subaccount != 0:
        raise M27OOperatorError("operator authorization requires exactly one subaccount-0 contract")
    if now >= preview.expires_at.astimezone(UTC) or now >= approval.expires_at.astimezone(UTC):
        raise M27OOperatorError("preview or human approval expired before real-money authorization")
    if approval.preview_hash != preview.content_hash or approval.candidate_id != preview.candidate_id:
        raise M27OOperatorError("human approval is not bound to the exact preview")
    side = preview.selected_outcome.removeprefix("BUY ")
    if side not in {"YES", "NO"}:
        raise M27OOperatorError("preview is not BUY YES/NO")
    if (
        approval.exact_price != preview.limit_price
        or approval.exact_quantity != ONE_CONTRACT
        or approval.maximum_fee != preview.maximum_fee
        or approval.maximum_loss != preview.maximum_loss
    ):
        raise M27OOperatorError("human approval economics changed before real-money authorization")
    expires_at = min(
        now + AUTHORIZATION_TTL,
        preview.expires_at.astimezone(UTC),
        approval.expires_at.astimezone(UTC),
    )
    if expires_at <= now:
        raise M27OOperatorError("no operator authorization window remains")
    return OperatorExecutionAuthorization(
        schema=AUTHORIZATION_SCHEMA,
        issued_at=now,
        expires_at=expires_at,
        candidate_id=preview.candidate_id,
        market_ticker=preview.market_ticker,
        selected_side=side,
        exact_price=preview.limit_price,
        exact_quantity=ONE_CONTRACT,
        maximum_fee=preview.maximum_fee,
        maximum_loss=preview.maximum_loss,
        preview_hash=preview.content_hash,
        approval_hash=approval.content_hash,
        confirmation=confirmation,
    )


def _validate_operator_authority(
    *,
    authorization: OperatorExecutionAuthorization,
    release: OneContractCanaryRelease,
    now: datetime,
) -> None:
    if authorization.content_hash != _canonical_hash(authorization._material()):
        raise M27OOperatorError("operator authorization content hash changed")
    if now >= authorization.expires_at.astimezone(UTC):
        raise M27OOperatorError("operator real-money authorization expired")
    if not hmac.compare_digest(authorization.confirmation, EXACT_REAL_MONEY_CONFIRMATION):
        raise M27OOperatorError("operator real-money confirmation changed")
    if (
        authorization.candidate_id != release.candidate_id
        or authorization.market_ticker != release.market_ticker
        or authorization.selected_side != release.selected_side
        or authorization.exact_price != release.exact_price
        or authorization.exact_quantity != release.exact_quantity
        or authorization.maximum_fee != release.maximum_fee
        or authorization.maximum_loss != release.maximum_loss
        or authorization.preview_hash != release.preview_hash
        or authorization.approval_hash != release.approval_hash
    ):
        raise M27OOperatorError("operator authorization does not match the exact M27O release")


def run_operator_canary(
    *,
    operator_authorization: OperatorExecutionAuthorization,
    preflight_payload: object,
    preview: HumanCanaryPreview,
    approval: HumanCanaryApproval,
    envelope: ProductionRequestEnvelope,
    risk_authorization: RiskAuthorization,
    canary_store: CanaryStore,
    authorization_store: AuthorizationStore,
    credential_store: ProtectedWriteCredentialStore,
    journal: ProductionJournal,
    clock: Clock,
) -> OperatorCanaryRun:
    """Execute the sole reviewed M27O A->B->C->D sequence for one exact canary.

    This function never retries Phase C.  If Phase C returns a possibly-sent outcome, Phase D
    is invoked exactly once immediately.  An UNKNOWN reconciliation remains unresolved and may
    later be re-run through the dedicated GET-only Phase-D recovery function; this runner can
    never issue a second opening order because Phase B has already burned the global v1 budget.
    """
    release_now = _utc(clock.now())
    release = prepare_one_contract_release(
        preflight_payload=preflight_payload,
        preview=preview,
        approval=approval,
        envelope=envelope,
        risk_authorization=risk_authorization,
        now=release_now,
    )
    _validate_operator_authority(
        authorization=operator_authorization,
        release=release,
        now=release_now,
    )

    commit = commit_atomic_release(
        release=release,
        canary_store=canary_store,
        authorization_store=authorization_store,
        now=_utc(clock.now()),
    )
    live_outcome = execute_one_contract_live_canary(
        release=release,
        atomic_commit=commit,
        preflight_payload=preflight_payload,
        envelope=envelope,
        m27h_payload=preflight_payload.get("m27h_payload")
        if isinstance(preflight_payload, dict)
        else None,
        shared_state_path=canary_store.path,
        credential_store=credential_store,
        journal=journal,
        clock=clock,
    )
    reconciliation = reconcile_one_contract_live_canary(
        release=release,
        atomic_commit=commit,
        execution_id=envelope.execution_id,
        shared_state_path=canary_store.path,
        credential_store=credential_store,
        journal=journal,
        clock=clock,
    )
    return OperatorCanaryRun(
        authorization_hash=operator_authorization.content_hash,
        release=release,
        atomic_commit=commit,
        live_outcome=live_outcome,
        reconciliation=reconciliation,
    )
