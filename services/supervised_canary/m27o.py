"""M27O -- fail-closed release binding for one supervised real-money canary.

This module is deliberately non-networked and credential-free. It cannot sign or send an
order. Phase A binds the already-reviewed M16/M27I/M13/M15 artifacts into one short-lived
release packet. Phase B atomically consumes the M16 human approval, the M13 authorization,
and the one-real-submission budget in one shared SQLite database immediately before the
separately reviewed production execution boundary may become reachable.

True atomicity is intentionally restricted to one shared SQLite database. M27O refuses to
coordinate independent M16 and M13 database files because two WAL databases would not provide
the crash-atomic guarantee this boundary requires.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from services.production_execution.domain import Operation, ProductionRequestEnvelope, digest
from services.risk_engine.authorization import (
    AuthorizationState,
    AuthorizationStore,
    RiskAuthorization,
)

from .domain import ApprovalState, HumanCanaryApproval, HumanCanaryPreview
from .m27i import validate_preflight_artifact
from .store import CanaryStore, UNRESOLVED

SCHEMA = "kalsh3.m27o.one-contract-release.v1"
SOFTWARE_VERSION = "kalsh3.m27o.one-contract-release/2"
COMMIT_SCHEMA = "kalsh3.m27o.atomic-release-commit.v1"
ONE_CONTRACT = Decimal("1.00")
ORDER_PATH = "/trade-api/v2/portfolio/events/orders"
_REQUIRED_KILL_CATEGORIES = frozenset({"STRATEGY", "DATA", "PORTFOLIO", "CREDENTIAL"})


class M27OReleaseError(PermissionError):
    """A release invariant failed. Never contains secrets."""


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


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise M27OReleaseError("current time must be timezone-aware")
    return value.astimezone(UTC)


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
    preview_id: str
    preview_hash: str
    approval_id: str
    approval_hash: str
    preflight_hash: str
    envelope_hash: str
    body_hash: str
    risk_authorization_id: str
    risk_decision_id: str
    intent_hash: str
    client_order_id: str
    rules_version: str
    portfolio_state_hash: str
    safety_state_hash: str
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
        object.__setattr__(self, "content_hash", _canonical_hash(self._material()))

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
            "preview_id": self.preview_id,
            "preview_hash": self.preview_hash,
            "approval_id": self.approval_id,
            "approval_hash": self.approval_hash,
            "preflight_hash": self.preflight_hash,
            "envelope_hash": self.envelope_hash,
            "body_hash": self.body_hash,
            "risk_authorization_id": self.risk_authorization_id,
            "risk_decision_id": self.risk_decision_id,
            "intent_hash": self.intent_hash,
            "client_order_id": self.client_order_id,
            "rules_version": self.rules_version,
            "portfolio_state_hash": self.portfolio_state_hash,
            "safety_state_hash": self.safety_state_hash,
            "reconciliation_state_hash": self.reconciliation_state_hash,
        }

    def to_json(self) -> dict[str, object]:
        return {**self._material(), "content_hash": self.content_hash}


@dataclass(frozen=True, slots=True)
class AtomicReleaseCommit:
    schema: str
    committed_at: datetime
    session_id: str
    release_hash: str
    preview_id: str
    approval_id: str
    risk_authorization_id: str
    client_order_id: str
    state: str = "SUBMISSION_PENDING"
    possibly_submitted: bool = True
    content_hash: str = ""

    def __post_init__(self) -> None:
        if self.schema != COMMIT_SCHEMA:
            raise ValueError("M27O atomic commit schema mismatch")
        if self.committed_at.tzinfo is None:
            raise ValueError("M27O atomic commit timestamp must be timezone-aware")
        material = self._material()
        object.__setattr__(self, "content_hash", _canonical_hash(material))

    def _material(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "committed_at": self.committed_at.astimezone(UTC).isoformat(),
            "session_id": self.session_id,
            "release_hash": self.release_hash,
            "preview_id": self.preview_id,
            "approval_id": self.approval_id,
            "risk_authorization_id": self.risk_authorization_id,
            "client_order_id": self.client_order_id,
            "state": self.state,
            "possibly_submitted": self.possibly_submitted,
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
    """Bind all final artifacts without consuming or mutating any of them."""
    now = _utc(now)
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
    if (
        _decimal(preflight_payload.get("executable_price"), "executable_price")
        != preview.limit_price
    ):
        raise M27OReleaseError("M27I preflight executable price changed")
    if _decimal(preflight_payload.get("maximum_fee"), "maximum_fee") != preview.maximum_fee:
        raise M27OReleaseError("M27I preflight fee ceiling changed")

    if (
        envelope.operation != Operation.CREATE
        or envelope.method != "POST"
        or envelope.path != ORDER_PATH
    ):
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
    if (
        risk_authorization.intent_hash != approval.intent_hash
        or risk_authorization.intent_hash != envelope.intent_hash
    ):
        raise M27OReleaseError("M13 intent binding changed")
    if risk_authorization.portfolio_state_hash != envelope.portfolio_state_hash:
        raise M27OReleaseError("M13 portfolio binding changed")
    if risk_authorization.rules_version != preview.rules_version:
        raise M27OReleaseError("M13 rules binding changed")

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
        preview_id=preview.preview_id,
        preview_hash=preview.content_hash,
        approval_id=approval.approval_id,
        approval_hash=approval.content_hash,
        preflight_hash=preflight_hash,
        envelope_hash=envelope.content_hash,
        body_hash=envelope.body_hash,
        risk_authorization_id=risk_authorization.authorization_id,
        risk_decision_id=risk_authorization.risk_decision_id,
        intent_hash=risk_authorization.intent_hash,
        client_order_id=envelope.client_order_id,
        rules_version=envelope.rules_version,
        portfolio_state_hash=envelope.portfolio_state_hash,
        safety_state_hash=risk_authorization.safety_state_hash,
        reconciliation_state_hash=envelope.reconciliation_state_hash,
    )


def _shared_store_path(
    canary_store: CanaryStore, authorization_store: AuthorizationStore
) -> Path:
    canary_path = canary_store.path.resolve()
    authorization_path = authorization_store.path.resolve()
    if canary_path != authorization_path:
        raise M27OReleaseError(
            "M27O atomic release requires M16 and M13 to share one SQLite database"
        )
    return canary_path


def _session_id(release: OneContractCanaryRelease) -> str:
    return "m27o-" + hashlib.sha256(
        f"{release.content_hash}|submission-session".encode()
    ).hexdigest()[:32]


def commit_atomic_release(
    *,
    release: OneContractCanaryRelease,
    canary_store: CanaryStore,
    authorization_store: AuthorizationStore,
    now: datetime,
) -> AtomicReleaseCommit:
    """Atomically consume every one-shot durable token before a live send can be attempted.

    The shared transaction does all of the following or none of them:

    * consumes the exact M16 human approval;
    * consumes the exact M13 risk authorization and releases its reservation;
    * burns the global one-real-submission experimental budget;
    * opens one ``SUBMISSION_PENDING`` canary session marked ``possibly_submitted=1``.

    ``possibly_submitted=1`` is intentionally conservative. Once this commit succeeds, any
    process failure requires reconciliation even if the process happened to die before the
    network call. No retry may infer that an order was definitely not sent.
    """
    now = _utc(now)
    path = _shared_store_path(canary_store, authorization_store)
    if release.content_hash != _canonical_hash(release._material()):
        raise M27OReleaseError("M27O release content hash changed")
    if now >= release.expires_at.astimezone(UTC):
        raise M27OReleaseError("M27O release expired before atomic commit")

    session_id = _session_id(release)
    db = sqlite3.connect(path, timeout=10)
    db.execute("PRAGMA busy_timeout=10000")
    try:
        db.execute("BEGIN IMMEDIATE")

        preview = db.execute(
            "SELECT content_hash,candidate_id,client_order_id,price,quantity,expires_at "
            "FROM canary_previews WHERE preview_id=?",
            (release.preview_id,),
        ).fetchone()
        if preview is None:
            raise M27OReleaseError("durable M16 preview missing")
        if (
            preview[0] != release.preview_hash
            or preview[1] != release.candidate_id
            or preview[2] != release.client_order_id
            or Decimal(str(preview[3])) != release.exact_price
            or Decimal(str(preview[4])) != ONE_CONTRACT
            or datetime.fromisoformat(str(preview[5])).astimezone(UTC) <= now
        ):
            raise M27OReleaseError("durable M16 preview no longer matches release")

        approval = db.execute(
            "SELECT preview_hash,content_hash,expires_at,state FROM canary_approvals "
            "WHERE approval_id=?",
            (release.approval_id,),
        ).fetchone()
        if approval is None:
            raise M27OReleaseError("durable M16 approval missing")
        if (
            approval[0] != release.preview_hash
            or approval[1] != release.approval_hash
            or approval[3] != ApprovalState.ISSUED
            or datetime.fromisoformat(str(approval[2])).astimezone(UTC) <= now
        ):
            raise M27OReleaseError("durable M16 approval is stale, consumed, or changed")

        risk = db.execute(
            "SELECT risk_decision_id,intent_hash,client_order_id,market_ticker,"
            "portfolio_state_hash,rules_version,safety_state_hash,expires_at,state "
            "FROM risk_authorizations WHERE authorization_id=?",
            (release.risk_authorization_id,),
        ).fetchone()
        if risk is None:
            raise M27OReleaseError("durable M13 authorization missing")
        if (
            risk[0] != release.risk_decision_id
            or risk[1] != release.intent_hash
            or risk[2] != release.client_order_id
            or risk[3] != release.market_ticker
            or risk[4] != release.portfolio_state_hash
            or risk[5] != release.rules_version
            or risk[6] != release.safety_state_hash
            or datetime.fromisoformat(str(risk[7])).astimezone(UTC) <= now
            or risk[8] != AuthorizationState.ISSUED
        ):
            raise M27OReleaseError("durable M13 authorization is stale, consumed, or changed")

        reservation = db.execute(
            "SELECT active FROM risk_reservations WHERE authorization_id=?",
            (release.risk_authorization_id,),
        ).fetchone()
        if reservation is None or int(reservation[0]) != 1:
            raise M27OReleaseError("durable M13 risk reservation is not active")

        halt = db.execute(
            "SELECT active FROM global_halt_state WHERE singleton=1"
        ).fetchone()
        compliance = db.execute(
            "SELECT state FROM compliance_state WHERE singleton=1"
        ).fetchone()
        kills = db.execute(
            "SELECT category,level FROM durable_kill_states"
        ).fetchall()
        loss_holds = db.execute(
            "SELECT weekly_review_required,monthly_review_required,experiment_halt_required "
            "FROM durable_loss_holds WHERE singleton=1"
        ).fetchone()
        kill_map = {str(category): str(level) for category, level in kills}
        safety_clear = bool(
            halt
            and int(halt[0]) == 0
            and compliance
            and compliance[0] == "CLEAR"
            and set(kill_map) == _REQUIRED_KILL_CATEGORIES
            and all(level == "NORMAL" for level in kill_map.values())
            and loss_holds
            and not any(int(value) for value in loss_holds)
        )
        if not safety_clear:
            raise M27OReleaseError("durable safety state changed before atomic commit")

        unresolved_placeholders = ",".join("?" * len(UNRESOLVED))
        unresolved = db.execute(
            f"SELECT 1 FROM canary_sessions WHERE state IN ({unresolved_placeholders}) LIMIT 1",  # noqa: S608 -- fixed internal state tuple
            UNRESOLVED,
        ).fetchone()
        if unresolved is not None:
            raise M27OReleaseError("another unresolved canary already exists")

        budget = db.execute(
            "SELECT real_submission_count FROM production_submission_counter WHERE singleton=1"
        ).fetchone()
        if budget is None or int(budget[0]) != 0:
            raise M27OReleaseError("global one-real-submission canary budget is unavailable")

        approval_changed = db.execute(
            "UPDATE canary_approvals SET state='CONSUMED' "
            "WHERE approval_id=? AND state='ISSUED' AND expires_at>?",
            (release.approval_id, now.isoformat()),
        ).rowcount
        risk_changed = db.execute(
            "UPDATE risk_authorizations SET state='CONSUMED' "
            "WHERE authorization_id=? AND state='ISSUED' AND expires_at>?",
            (release.risk_authorization_id, now.isoformat()),
        ).rowcount
        reservation_changed = db.execute(
            "UPDATE risk_reservations SET active=0 "
            "WHERE authorization_id=? AND active=1",
            (release.risk_authorization_id,),
        ).rowcount
        budget_changed = db.execute(
            "UPDATE production_submission_counter SET real_submission_count=1 "
            "WHERE singleton=1 AND real_submission_count=0"
        ).rowcount
        if (approval_changed, risk_changed, reservation_changed, budget_changed) != (1, 1, 1, 1):
            raise M27OReleaseError("one-shot durable token consumption lost an atomic race")

        db.execute(
            "INSERT INTO canary_sessions("
            "session_id,preview_id,approval_id,client_order_id,state,possibly_submitted,created_at"
            ") VALUES(?,?,?,?,?,?,?)",
            (
                session_id,
                release.preview_id,
                release.approval_id,
                release.client_order_id,
                "SUBMISSION_PENDING",
                1,
                now.isoformat(),
            ),
        )
        db.execute(
            "INSERT INTO canary_events("
            "happened_at,event_type,reference_hash,actor"
            ") VALUES(?,?,?,?)",
            (now.isoformat(), "M27O_ATOMIC_RELEASE_COMMITTED", release.content_hash, "M27O"),
        )
        db.execute(
            "INSERT INTO risk_events("
            "event_type,actor,happened_at,reason,policy_version,state_hash"
            ") VALUES(?,?,?,?,?,?)",
            (
                "RISK_AUTHORIZATION_CONSUMED",
                "M27O_ATOMIC_RELEASE",
                now.isoformat(),
                release.risk_authorization_id,
                None,
                release.safety_state_hash,
            ),
        )
        db.commit()
    except M27OReleaseError:
        db.rollback()
        raise
    except (sqlite3.Error, ValueError, InvalidOperation) as exc:
        db.rollback()
        raise M27OReleaseError("M27O atomic release commit failed closed") from exc
    finally:
        db.close()

    return AtomicReleaseCommit(
        schema=COMMIT_SCHEMA,
        committed_at=now,
        session_id=session_id,
        release_hash=release.content_hash,
        preview_id=release.preview_id,
        approval_id=release.approval_id,
        risk_authorization_id=release.risk_authorization_id,
        client_order_id=release.client_order_id,
    )
