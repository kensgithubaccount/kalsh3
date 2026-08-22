"""M27O Phase C -- one-contract-only real Kalshi canary send boundary.

This is the sole M27O path allowed to make a real mutating request. It is deliberately
narrower than the generic M15 transport surface: CREATE only, exactly one contract,
fill-or-kill only, subaccount/exchange-index zero, fixed production origin/path, no redirects,
and no caller-supplied sender. The generic ``SignAndSendBoundary.production_execute`` remains
permanently DISARMED.

The boundary is reachable only after Phase B has already atomically consumed the M16 human
approval, the M13 authorization/reservation, and the one-real-submission budget. It then
independently revalidates the still-fresh M27I preflight, the durable Phase-B commit, the
installed-credential M27H evidence, the exact M15 envelope bytes, and the current durable
safety state before signing. Any possibly-sent outcome requires reconciliation and is never
retried automatically.

Credential containment: the installed credential is decrypted only while holding the
``ProtectedWriteCredentialStore`` exclusive lock. It is used only to bind the exact fresh
M27H secret-free evidence and to create the request signature. It is never returned, logged,
serialized, or passed to caller-supplied code.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Protocol

from services.supervised_canary.m27i import validate_preflight_artifact
from services.supervised_canary.m27o import AtomicReleaseCommit, OneContractCanaryRelease

from .domain import PRODUCTION_ORIGIN, AuthorityClass, Operation, ProductionRequestEnvelope, digest
from .enrollment import ProtectedWriteCredentialStore, _candidate_fingerprint
from .installed_credential_verification import (
    validate_installed_credential_evidence_for_readiness,
)
from .security_boundary import _rsa_pss_sha256
from .store import ProductionJournal
from .transport import (
    MAX_RESPONSE_BYTES,
    TIMEOUT_SECONDS,
    FixedKalshiProductionTransport,
    ProductionTransportError,
)

ORDER_PATH = "/trade-api/v2/portfolio/events/orders"
ONE_CONTRACT = Decimal("1.00")
BOUNDARY_VERSION = "m27o-one-contract-live-send-v1"
_REQUIRED_KILL_CATEGORIES = frozenset({"STRATEGY", "DATA", "PORTFOLIO", "CREDENTIAL"})


class Clock(Protocol):
    def now(self) -> datetime: ...


class LiveCanaryExecutionError(PermissionError):
    """A fail-closed M27O live-send invariant failed. Never contains secrets."""


@dataclass(frozen=True, slots=True)
class LiveCanaryOutcome:
    state: str
    status: int | None
    reconciliation_required: bool
    execution_id: str
    session_id: str


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise LiveCanaryExecutionError("M27O live clock must be timezone-aware")
    return value.astimezone(UTC)


def _canonical_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _hash_intact(value: object) -> bool:
    to_json = getattr(value, "to_json", None)
    if not callable(to_json):
        return False
    payload = to_json()
    if not isinstance(payload, dict):
        return False
    claimed = payload.pop("content_hash", None)
    return isinstance(claimed, str) and claimed == _canonical_hash(payload)


def _decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, str):
        raise LiveCanaryExecutionError(f"M27I preflight {field} malformed")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise LiveCanaryExecutionError(f"M27I preflight {field} malformed") from exc
    if not parsed.is_finite():
        raise LiveCanaryExecutionError(f"M27I preflight {field} malformed")
    return parsed


def _validate_release_commit(
    *, release: OneContractCanaryRelease, commit: AtomicReleaseCommit, now: datetime
) -> None:
    if not _hash_intact(release):
        raise LiveCanaryExecutionError("M27O release content hash changed")
    if not _hash_intact(commit):
        raise LiveCanaryExecutionError("M27O atomic commit content hash changed")
    if not release.created_at.astimezone(UTC) <= commit.committed_at.astimezone(UTC) <= now:
        raise LiveCanaryExecutionError("M27O release/commit timestamp ordering changed")
    if now >= release.expires_at.astimezone(UTC):
        raise LiveCanaryExecutionError("M27O release expired before live boundary")
    if (
        commit.release_hash != release.content_hash
        or commit.preview_id != release.preview_id
        or commit.approval_id != release.approval_id
        or commit.risk_authorization_id != release.risk_authorization_id
        or commit.client_order_id != release.client_order_id
        or commit.state != "SUBMISSION_PENDING"
        or commit.possibly_submitted is not True
    ):
        raise LiveCanaryExecutionError("M27O atomic commit no longer matches release")


def _validate_preflight(
    *, payload: object, release: OneContractCanaryRelease, now: datetime
) -> None:
    if not isinstance(payload, dict):
        raise LiveCanaryExecutionError("M27I preflight payload malformed")
    if payload.get("content_hash") != release.preflight_hash:
        raise LiveCanaryExecutionError("M27I preflight hash changed after release")
    validation = validate_preflight_artifact(
        payload,
        expected_candidate_id=release.candidate_id,
        now=now,
    )
    if not validation.valid:
        raise LiveCanaryExecutionError(f"M27I preflight rejected: {validation.reason}")
    if payload.get("state") != "PREFLIGHT_READY" or payload.get("missing_gates") not in ([], ()):
        raise LiveCanaryExecutionError("M27I preflight is not fully ready")
    if payload.get("market_ticker") != release.market_ticker:
        raise LiveCanaryExecutionError("M27I market changed after release")
    if payload.get("selected_side") != release.selected_side:
        raise LiveCanaryExecutionError("M27I side changed after release")
    if _decimal(payload.get("executable_price"), "executable_price") != release.exact_price:
        raise LiveCanaryExecutionError("M27I price changed after release")
    if _decimal(payload.get("maximum_fee"), "maximum_fee") != release.maximum_fee:
        raise LiveCanaryExecutionError("M27I fee ceiling changed after release")
    if _decimal(payload.get("maximum_loss"), "maximum_loss") != release.maximum_loss:
        raise LiveCanaryExecutionError("M27I loss ceiling changed after release")


def _validate_envelope(
    *, envelope: ProductionRequestEnvelope, release: OneContractCanaryRelease, now: datetime
) -> None:
    if (
        envelope.operation != Operation.CREATE
        or envelope.authority_class != AuthorityClass.NEW_RISK
        or envelope.method != "POST"
        or envelope.path != ORDER_PATH
        or envelope.origin != PRODUCTION_ORIGIN
    ):
        raise LiveCanaryExecutionError("M27O envelope is outside exact create-order authority")
    if (
        envelope.quantity != ONE_CONTRACT
        or envelope.subaccount != 0
        or envelope.exchange_index != 0
    ):
        raise LiveCanaryExecutionError("M27O envelope is not exactly one subaccount-0 contract")
    if (
        envelope.time_in_force != "fill_or_kill"
        or envelope.expiration is not None
        or envelope.post_only
        or envelope.reduce_only
        or not envelope.cancel_order_on_pause
    ):
        raise LiveCanaryExecutionError("M27O live canary must be non-resting fill-or-kill")
    if envelope.self_trade_prevention_type != "taker_at_cross":
        raise LiveCanaryExecutionError("M27O self-trade-prevention binding changed")
    if not isinstance(envelope.order_group_id, str) or not envelope.order_group_id:
        raise LiveCanaryExecutionError("M27O live canary requires an order group")
    if envelope.query:
        raise LiveCanaryExecutionError("M27O live canary forbids query parameters")
    if not envelope.created_at.astimezone(UTC) <= now < envelope.expires_at.astimezone(UTC):
        raise LiveCanaryExecutionError("M15 envelope is outside its five-second validity window")
    if digest(envelope.canonical_body) != envelope.body_hash:
        raise LiveCanaryExecutionError("M15 exact body hash changed")
    if (
        envelope.content_hash != release.envelope_hash
        or envelope.body_hash != release.body_hash
        or envelope.risk_authorization_id != release.risk_authorization_id
        or envelope.risk_decision_id != release.risk_decision_id
        or envelope.intent_hash != release.intent_hash
        or envelope.market_ticker != release.market_ticker
        or envelope.outcome_side != release.selected_side
        or envelope.price != release.exact_price
        or envelope.quantity != release.exact_quantity
        or envelope.client_order_id != release.client_order_id
        or envelope.rules_version != release.rules_version
        or envelope.candidate_version != release.candidate_id
        or envelope.portfolio_state_hash != release.portfolio_state_hash
        or envelope.reconciliation_state_hash != release.reconciliation_state_hash
    ):
        raise LiveCanaryExecutionError("M15 envelope changed after M27O release")


def _validate_durable_phase_b(
    *, path: Path, release: OneContractCanaryRelease, commit: AtomicReleaseCommit
) -> None:
    try:
        with sqlite3.connect(path, timeout=10) as db:
            db.execute("PRAGMA busy_timeout=10000")
            session = db.execute(
                "SELECT preview_id,approval_id,client_order_id,state,possibly_submitted "
                "FROM canary_sessions WHERE session_id=?",
                (commit.session_id,),
            ).fetchone()
            approval = db.execute(
                "SELECT state FROM canary_approvals WHERE approval_id=?",
                (release.approval_id,),
            ).fetchone()
            risk = db.execute(
                "SELECT state FROM risk_authorizations WHERE authorization_id=?",
                (release.risk_authorization_id,),
            ).fetchone()
            reservation = db.execute(
                "SELECT active FROM risk_reservations WHERE authorization_id=?",
                (release.risk_authorization_id,),
            ).fetchone()
            budget = db.execute(
                "SELECT real_submission_count FROM production_submission_counter WHERE singleton=1"
            ).fetchone()
            runtime = db.execute(
                "SELECT production_state FROM canary_runtime WHERE singleton=1"
            ).fetchone()
            event = db.execute(
                "SELECT COUNT(*) FROM canary_events "
                "WHERE event_type='M27O_ATOMIC_RELEASE_COMMITTED' AND reference_hash=?",
                (release.content_hash,),
            ).fetchone()
            halt = db.execute("SELECT active FROM global_halt_state WHERE singleton=1").fetchone()
            compliance = db.execute(
                "SELECT state FROM compliance_state WHERE singleton=1"
            ).fetchone()
            kills = db.execute("SELECT category,level FROM durable_kill_states").fetchall()
            loss_holds = db.execute(
                "SELECT weekly_review_required,monthly_review_required,experiment_halt_required "
                "FROM durable_loss_holds WHERE singleton=1"
            ).fetchone()
    except sqlite3.Error as exc:
        raise LiveCanaryExecutionError("M27O durable Phase-B state could not be verified") from exc

    if session != (
        release.preview_id,
        release.approval_id,
        release.client_order_id,
        "SUBMISSION_PENDING",
        1,
    ):
        raise LiveCanaryExecutionError("M27O durable canary session changed before send")
    if approval != ("CONSUMED",) or risk != ("CONSUMED",) or reservation != (0,):
        raise LiveCanaryExecutionError("M27O one-shot approvals were not durably consumed")
    if budget != (1,) or runtime != ("DISARMED",) or event != (1,):
        raise LiveCanaryExecutionError("M27O durable one-shot submission state changed")
    kill_map = {str(category): str(level) for category, level in kills}
    if not (
        halt == (0,)
        and compliance == ("CLEAR",)
        and set(kill_map) == _REQUIRED_KILL_CATEGORIES
        and all(level == "NORMAL" for level in kill_map.values())
        and loss_holds is not None
        and not any(int(value) for value in loss_holds)
    ):
        raise LiveCanaryExecutionError("durable safety state changed before live send")


def _validate_m27h(payload: object, *, now: datetime) -> None:
    check = validate_installed_credential_evidence_for_readiness(payload, now=now)
    if not check.credential_installed or not check.signer_verified_fresh:
        raise LiveCanaryExecutionError(check.reason or "fresh M27H credential evidence required")


def _mark_submitted_or_unknown(path: Path, *, session_id: str, now: datetime) -> None:
    try:
        with sqlite3.connect(path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "UPDATE canary_sessions SET state='SUBMITTED_OR_UNKNOWN',possibly_submitted=1 "
                "WHERE session_id=? AND state='SUBMISSION_PENDING'",
                (session_id,),
            ).rowcount
            if changed != 1:
                raise LiveCanaryExecutionError("M27O canary session transition lost its owner")
            db.execute(
                "INSERT INTO canary_events(happened_at,event_type,reference_hash,actor) "
                "VALUES(?,?,?,?)",
                (now.isoformat(), "M27O_LIVE_SEND_RECONCILIATION_REQUIRED", session_id, "M27O"),
            )
    except sqlite3.Error as exc:
        raise LiveCanaryExecutionError(
            "M27O canary reconciliation state could not be persisted"
        ) from exc


def _unknown(
    *,
    journal: ProductionJournal,
    shared_state_path: Path,
    envelope: ProductionRequestEnvelope,
    commit: AtomicReleaseCommit,
    now: datetime,
    status: int | None,
) -> LiveCanaryOutcome:
    journal.transition(
        envelope.execution_id,
        "UNKNOWN_RECONCILIATION_REQUIRED",
        possibly_sent=True,
    )
    _mark_submitted_or_unknown(shared_state_path, session_id=commit.session_id, now=now)
    return LiveCanaryOutcome(
        "UNKNOWN_RECONCILIATION_REQUIRED",
        status,
        True,
        envelope.execution_id,
        commit.session_id,
    )


def execute_one_contract_live_canary(
    *,
    release: OneContractCanaryRelease,
    atomic_commit: AtomicReleaseCommit,
    preflight_payload: object,
    envelope: ProductionRequestEnvelope,
    m27h_payload: object,
    shared_state_path: Path,
    credential_store: ProtectedWriteCredentialStore,
    journal: ProductionJournal,
    clock: Clock,
) -> LiveCanaryOutcome:
    """Attempt exactly one real M27O opening order; every possible send requires reconciliation.

    There is deliberately no sender/URL/method/path/credential argument. The production
    transport, destination, signer primitive, and committed credential store are fixed by this
    module. A caller may provide only already-bound M27 artifacts plus their durable stores.
    """
    first_now = _utc(clock.now())
    _validate_release_commit(release=release, commit=atomic_commit, now=first_now)
    _validate_preflight(payload=preflight_payload, release=release, now=first_now)
    _validate_envelope(envelope=envelope, release=release, now=first_now)
    _validate_m27h(m27h_payload, now=first_now)
    _validate_durable_phase_b(path=shared_state_path, release=release, commit=atomic_commit)
    if journal.state() != "DISARMED":
        raise LiveCanaryExecutionError("generic production journal is not DISARMED")

    with credential_store.exclusive() as lock:
        send_now = _utc(clock.now())
        _validate_release_commit(release=release, commit=atomic_commit, now=send_now)
        _validate_preflight(payload=preflight_payload, release=release, now=send_now)
        _validate_envelope(envelope=envelope, release=release, now=send_now)
        _validate_m27h(m27h_payload, now=send_now)
        _validate_durable_phase_b(path=shared_state_path, release=release, commit=atomic_commit)

        try:
            credential = credential_store._decode_committed_credential(lock)
        except PermissionError as exc:
            raise LiveCanaryExecutionError(
                "committed production write credential unavailable"
            ) from exc
        try:
            if not isinstance(m27h_payload, dict):
                raise LiveCanaryExecutionError("M27H payload malformed")
            key_id_hash = hashlib.sha256(credential.key_id.encode()).hexdigest()
            fingerprint = _candidate_fingerprint(credential)
            if (
                m27h_payload.get("key_id_hash") != key_id_hash
                or m27h_payload.get("credential_fingerprint") != fingerprint
            ):
                raise LiveCanaryExecutionError(
                    "installed credential changed after M27H verification"
                )

            if not journal.claim(envelope, version=BOUNDARY_VERSION):
                raise LiveCanaryExecutionError("single-use production journal claim already exists")

            timestamp_ms = int(send_now.timestamp() * 1000)
            message = f"{timestamp_ms}{envelope.method}{envelope.path}".encode()
            try:
                signature = _rsa_pss_sha256(credential.private_key_pem, message)
            except Exception:
                return _unknown(
                    journal=journal,
                    shared_state_path=shared_state_path,
                    envelope=envelope,
                    commit=atomic_commit,
                    now=send_now,
                    status=None,
                )

            headers = {
                "KALSHI-ACCESS-KEY": credential.key_id,
                "KALSHI-ACCESS-TIMESTAMP": str(timestamp_ms),
                "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
                "Content-Type": "application/json",
            }
            journal.transition(envelope.execution_id, "BOUNDARY_ENTERED", possibly_sent=True)
            try:
                status, response = FixedKalshiProductionTransport().send_exact(
                    origin=PRODUCTION_ORIGIN,
                    method="POST",
                    path=ORDER_PATH,
                    body=envelope.canonical_body,
                    headers=headers,
                    verify_tls=True,
                    follow_redirects=False,
                    timeout_seconds=TIMEOUT_SECONDS,
                    maximum_response_bytes=MAX_RESPONSE_BYTES,
                )
            except (ProductionTransportError, TimeoutError):
                return _unknown(
                    journal=journal,
                    shared_state_path=shared_state_path,
                    envelope=envelope,
                    commit=atomic_commit,
                    now=send_now,
                    status=None,
                )
            finally:
                headers.clear()

            if len(response) > MAX_RESPONSE_BYTES or status == 429 or status >= 500:
                return _unknown(
                    journal=journal,
                    shared_state_path=shared_state_path,
                    envelope=envelope,
                    commit=atomic_commit,
                    now=send_now,
                    status=status,
                )
            journal.transition(
                envelope.execution_id,
                "ACKNOWLEDGED_RECONCILIATION_REQUIRED",
                possibly_sent=True,
            )
            _mark_submitted_or_unknown(
                shared_state_path,
                session_id=atomic_commit.session_id,
                now=send_now,
            )
            return LiveCanaryOutcome(
                "ACKNOWLEDGED_RECONCILIATION_REQUIRED",
                status,
                True,
                envelope.execution_id,
                atomic_commit.session_id,
            )
        finally:
            del credential
