"""M27O -- fail-closed release binding for one supervised real-money canary.

This module is deliberately non-networked and non-mutating.  It does not load a write
credential, sign a request, arm production, or send an order.  Its only job is to bind the
already-reviewed M16/M27I/M13/M15 artifacts into one short-lived release packet that a
separate production-execution boundary may consume.

The release packet is not sufficient by itself to send.  The eventual live boundary must
also atomically consume the durable M16 approval, reserve the one-and-only real submission
attempt, consume the M13 authorization, load the credential only inside production_execution,
and reconcile every possibly-sent outcome.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from services.production_execution.domain import Operation, ProductionRequestEnvelope, digest
from services.risk_engine.authorization import AuthorizationState, RiskAuthorization

from .domain import ApprovalState, HumanCanaryApproval, HumanCanaryPreview
from .m27i import validate_preflight_artifact

SCHEMA = "kalsh3.m27o.one-contract-release.v1"
SOFTWARE_VERSION = "kalsh3.m27o.one-contract-release/1"
ONE_CONTRACT = Decimal("1.00")
ORDER_PATH = "/trade-api/v2/portfolio/events/orders"


class M27OReleaseError(PermissionError):
    """A release-binding invariant failed.  Never contains secrets."""


def _decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, str):
        raise M27OReleaseError(f"preflight {field} is missing or malformed")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise M27OReleaseError(f"preflight {field} is missing or malformed") from exc
    if not parsed.is_finite():
        raise M27OReleaseError(f"preflight {field} is missing or malformed")
    return parsed


def _canonical_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class OneContractCanaryRelease:
    schema: str
    software_version: str
    created_at: datetime
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
    preflight_hash: str
    envelope_hash: str
    body_hash: str
    risk_authorization_id: str
    risk_decision_id: str
    intent_hash: str
    client_order_id: str
    portfolio_state_hash: str
    reconciliation_state_hash: str
    content_hash: str = ""

    def __post_init__(self) -> None:
        if self.schema != SCHEMA or self.software_version != SOFTWARE_VERSION:
            raise ValueError("M27O release schema/version mismatch")
        if self.created_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("M27O release timestamps must be timezone-aware")
        if self.expires_at <= self.created_at:
            raise ValueError("M27O release must expire after creation")
        if self.exact_quantity != ONE_CONTRACT:
            raise ValueError("M27O release quantity must be exactly one contract")
        if self.selected_side not in {"YES", "NO"}:
            raise ValueError("M27O release side must be YES or NO")
        material = self._material()
        object.__setattr__(self, "content_hash", _canonical_hash(material))

    def _material(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "software_version": self.software_version,
            "created_at": self.created_at.astimezone(UTC).isoformat(),
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
            "preflight_hash": self.preflight_hash,
            "envelope_hash": self.envelope_hash,
            "body_hash": self.body_hash,
            "risk_authorization_id": self.risk_authorization_id,
            "risk_decision_id": self.risk_decision_id,
            "intent_hash": self.intent_hash,
            "client_order_id": self.client_order_id,
            "portfolio_state_hash": self.portfolio_state_hash,
            "reconciliation_state_hash": self.reconciliation_state_hash,
        }

    def to_json(self) -> dict[str, object]:
        return {**self._material(), "content_hash": self.content_hash}


def prepare_one_contract_release(
    *,
    preflight_payload: object,
    preview: HumanCanaryPreview,
    approval: HumanCanaryApproval,
    envelope: ProductionRequestEnvelope,
    risk_authorization: RiskAuthorization,
    now: datetime,
) -> OneContractCanaryRelease:
    """Bind all final artifacts without consuming or mutating any of them.

    This function deliberately performs no store mutation.  Approval consumption, M13
    authorization consumption, the durable one-submission burn, credential access, signing,
    sending, and reconciliation remain responsibilities of the eventual M27O execution
    transaction.  Splitting pure binding from mutation makes the release contract reviewable
    and lets every mismatch fail before a real-money boundary is reachable.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise M27OReleaseError("current time must be timezone-aware")
    now = now.astimezone(UTC)
    if not isinstance(preflight_payload, dict):
        raise M27OReleaseError("M27I preflight payload must be an object")

    validation = validate_preflight_artifact(
        preflight_payload,
        expected_candidate_id=preview.candidate_id,
        now=now,
    )
    if not validation.valid:
        raise M27OReleaseError(f"M27I preflight rejected: {validation.reason}")
    if preflight_payload.get("state") != "PREFLIGHT_READY":
        raise M27OReleaseError("M27I preflight is not PREFLIGHT_READY")
    missing = preflight_payload.get("missing_gates")
    if missing not in ([], ()):  # serialized artifacts use a list
        raise M27OReleaseError("M27I preflight still has missing gates")

    if preview.quantity != ONE_CONTRACT or preview.subaccount != 0:
        raise M27OReleaseError("preview is not the exact one-contract subaccount-0 canary")
    if now >= preview.expires_at.astimezone(UTC):
        raise M27OReleaseError("preview expired before release")

    if approval.state != ApprovalState.ISSUED:
        raise M27OReleaseError("human approval is not ISSUED")
    if now >= approval.expires_at.astimezone(UTC):
        raise M27OReleaseError("human approval expired before release")
    if approval.preview_hash != preview.content_hash:
        raise M27OReleaseError("human approval is bound to a different preview")
    if approval.candidate_id != preview.candidate_id:
        raise M27OReleaseError("human approval candidate changed")
    if approval.exact_price != preview.limit_price or approval.exact_quantity != ONE_CONTRACT:
        raise M27OReleaseError("human approval price or quantity changed")
    if approval.maximum_fee != preview.maximum_fee or approval.maximum_loss != preview.maximum_loss:
        raise M27OReleaseError("human approval fee or loss ceiling changed")
    if approval.rules_hash != preview.rules_hash:
        raise M27OReleaseError("human approval rules hash changed")
    if approval.reconciliation_version != preview.reconciliation_version:
        raise M27OReleaseError("human approval reconciliation binding changed")
    if approval.production_read_state != "LIVE VERIFIED":
        raise M27OReleaseError("human approval lacks live production-read binding")

    expected_side = preview.selected_outcome.removeprefix("BUY ")
    if expected_side not in {"YES", "NO"}:
        raise M27OReleaseError("preview selected outcome is not a BUY YES/NO canary")
    if preflight_payload.get("market_ticker") != preview.market_ticker:
        raise M27OReleaseError("M27I preflight market changed")
    if preflight_payload.get("selected_side") != expected_side:
        raise M27OReleaseError("M27I preflight side changed")
    if _decimal(preflight_payload.get("executable_price"), "executable_price") != preview.limit_price:
        raise M27OReleaseError("M27I preflight executable price changed")
    if _decimal(preflight_payload.get("maximum_fee"), "maximum_fee") != preview.maximum_fee:
        raise M27OReleaseError("M27I preflight fee ceiling changed")

    if envelope.operation != Operation.CREATE or envelope.method != "POST" or envelope.path != ORDER_PATH:
        raise M27OReleaseError("envelope is not the exact create-order operation")
    if envelope.quantity != ONE_CONTRACT or envelope.subaccount != 0:
        raise M27OReleaseError("envelope is not exactly one contract on subaccount 0")
    if envelope.market_ticker != preview.market_ticker or envelope.outcome_side != expected_side:
        raise M27OReleaseError("envelope market or side changed")
    if envelope.price != preview.limit_price:
        raise M27OReleaseError("envelope price changed")
    if envelope.client_order_id != preview.client_order_id:
        raise M27OReleaseError("envelope client_order_id changed")
    if envelope.rules_version != preview.rules_version:
        raise M27OReleaseError("envelope rules version changed")
    if envelope.candidate_version != preview.candidate_id:
        raise M27OReleaseError("envelope candidate binding changed")
    if digest(envelope.canonical_body) != envelope.body_hash:
        raise M27OReleaseError("envelope body hash no longer matches exact bytes")
    if now >= envelope.expires_at.astimezone(UTC):
        raise M27OReleaseError("production envelope expired before release")

    if risk_authorization.state != AuthorizationState.ISSUED:
        raise M27OReleaseError("M13 authorization is not ISSUED")
    if now >= risk_authorization.expires_at.astimezone(UTC):
        raise M27OReleaseError("M13 authorization expired before release")
    if risk_authorization.authorization_id != envelope.risk_authorization_id:
        raise M27OReleaseError("M13 authorization id changed")
    if risk_authorization.risk_decision_id != envelope.risk_decision_id:
        raise M27OReleaseError("M13 decision id changed")
    if risk_authorization.intent_hash != approval.intent_hash or risk_authorization.intent_hash != envelope.intent_hash:
        raise M27OReleaseError("M13 intent binding changed")
    if risk_authorization.portfolio_state_hash != envelope.portfolio_state_hash:
        raise M27OReleaseError("M13 portfolio binding changed")

    expires_at = min(
        datetime.fromisoformat(str(preflight_payload["expires_at"])).astimezone(UTC),
        preview.expires_at.astimezone(UTC),
        approval.expires_at.astimezone(UTC),
        envelope.expires_at.astimezone(UTC),
        risk_authorization.expires_at.astimezone(UTC),
    )
    if expires_at <= now:
        raise M27OReleaseError("no live release window remains")

    preflight_hash = preflight_payload.get("content_hash")
    if not isinstance(preflight_hash, str) or not preflight_hash:
        raise M27OReleaseError("M27I preflight hash missing")

    return OneContractCanaryRelease(
        schema=SCHEMA,
        software_version=SOFTWARE_VERSION,
        created_at=now,
        expires_at=expires_at,
        candidate_id=preview.candidate_id,
        market_ticker=preview.market_ticker,
        selected_side=expected_side,
        exact_price=preview.limit_price,
        exact_quantity=ONE_CONTRACT,
        maximum_fee=preview.maximum_fee,
        maximum_loss=preview.maximum_loss,
        preview_hash=preview.content_hash,
        approval_hash=approval.content_hash,
        preflight_hash=preflight_hash,
        envelope_hash=envelope.content_hash,
        body_hash=envelope.body_hash,
        risk_authorization_id=risk_authorization.authorization_id,
        risk_decision_id=risk_authorization.risk_decision_id,
        intent_hash=risk_authorization.intent_hash,
        client_order_id=envelope.client_order_id,
        portfolio_state_hash=envelope.portfolio_state_hash,
        reconciliation_state_hash=envelope.reconciliation_state_hash,
    )
