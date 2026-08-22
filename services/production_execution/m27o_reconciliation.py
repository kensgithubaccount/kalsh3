"""M27O Phase D -- authenticated, read-only reconciliation after the one live canary POST.

This module can never submit, cancel, amend, or decrease an order.  It exists only after the
Phase-C mutation boundary has already classified the canary as reconciliation-required.  It
uses the committed production credential solely for authenticated GETs through the existing
read-only KalshiAccountClient, then resolves the exact release-bound client_order_id against
current orders and fills.

Terminal classifications are intentionally narrow:

* FILLED: exactly one matching terminal executed order, exactly 1.00 matching taker fill;
* NO_FILL: exactly one matching terminal canceled FOK order and zero matching fills;
* UNKNOWN: every other state, including missing/duplicate order, resting order, partial fill,
  schema drift, account-read failure, or local persistence ambiguity.

Only FILLED and NO_FILL may mark the canary CANARY_COMPLETE and production journal RECONCILED.
UNKNOWN never retries the POST and never clears the unresolved canary.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Protocol

from services.kalshi_account_gateway.auth import AuthenticationError, RequestSigner
from services.kalshi_account_gateway.client import (
    AccountGatewayError,
    KalshiAccountClient,
    UrllibReadTransport,
)
from services.supervised_canary.m27o import AtomicReleaseCommit, OneContractCanaryRelease

from .enrollment import ProtectedWriteCredentialStore
from .m27o_live_canary import _hash_intact
from .store import ProductionJournal

SCHEMA = "kalsh3.m27o.post-send-reconciliation.v1"
SOFTWARE_VERSION = "kalsh3.m27o.post-send-reconciliation/1"
ONE_CONTRACT = Decimal("1.00")
_ORDER_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_TERMINAL_JOURNAL_INPUTS = frozenset(
    {
        "BOUNDARY_ENTERED",
        "ACKNOWLEDGED_RECONCILIATION_REQUIRED",
        "UNKNOWN_RECONCILIATION_REQUIRED",
    }
)


class Clock(Protocol):
    def now(self) -> datetime: ...


class M27OReconciliationError(PermissionError):
    """A fail-closed reconciliation invariant failed. Never contains secrets."""


@dataclass(frozen=True, slots=True)
class PostSendReconciliation:
    schema: str
    software_version: str
    observed_at: datetime
    completed_at: datetime
    classification: str
    reason: str | None
    execution_id: str
    session_id: str
    client_order_id: str
    order_id: str | None
    order_status: str | None
    filled_quantity: Decimal | None
    maximum_fill_price: Decimal | None
    total_fee: Decimal | None
    orders_sha256: str | None
    fills_sha256: str | None
    positions_sha256: str | None
    terminal_state: str
    reconciliation_required: bool
    content_hash: str = ""

    def __post_init__(self) -> None:
        material = self._material()
        object.__setattr__(self, "content_hash", _canonical_hash(material))

    def _material(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "software_version": self.software_version,
            "observed_at": self.observed_at.astimezone(UTC).isoformat(),
            "completed_at": self.completed_at.astimezone(UTC).isoformat(),
            "classification": self.classification,
            "reason": self.reason,
            "execution_id": self.execution_id,
            "session_id": self.session_id,
            "client_order_id": self.client_order_id,
            "order_id": self.order_id,
            "order_status": self.order_status,
            "filled_quantity": None if self.filled_quantity is None else str(self.filled_quantity),
            "maximum_fill_price": (
                None if self.maximum_fill_price is None else str(self.maximum_fill_price)
            ),
            "total_fee": None if self.total_fee is None else str(self.total_fee),
            "orders_sha256": self.orders_sha256,
            "fills_sha256": self.fills_sha256,
            "positions_sha256": self.positions_sha256,
            "terminal_state": self.terminal_state,
            "reconciliation_required": self.reconciliation_required,
        }

    def to_json(self) -> dict[str, object]:
        return {**self._material(), "content_hash": self.content_hash}


@dataclass(frozen=True, slots=True)
class _ExecutionFacts:
    classification: str
    reason: str | None
    order_id: str | None
    order_status: str | None
    filled_quantity: Decimal | None
    maximum_fill_price: Decimal | None
    total_fee: Decimal | None


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise M27OReconciliationError("reconciliation clock must be timezone-aware")
    return value.astimezone(UTC)


def _decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, str):
        raise M27OReconciliationError(f"{field} is missing or malformed")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise M27OReconciliationError(f"{field} is missing or malformed") from exc
    if not parsed.is_finite():
        raise M27OReconciliationError(f"{field} is missing or malformed")
    return parsed


def _validate_static_binding(
    *, release: OneContractCanaryRelease, commit: AtomicReleaseCommit
) -> None:
    # Expiry is deliberately not re-enforced here: once a POST may have occurred, recovery must
    # remain possible forever.  Only immutable identity/hash bindings are revalidated.
    if not _hash_intact(release) or not _hash_intact(commit):
        raise M27OReconciliationError("M27O release or atomic commit hash changed")
    if (
        commit.release_hash != release.content_hash
        or commit.preview_id != release.preview_id
        or commit.approval_id != release.approval_id
        or commit.risk_authorization_id != release.risk_authorization_id
        or commit.client_order_id != release.client_order_id
        or commit.session_id == ""
        or commit.possibly_submitted is not True
    ):
        raise M27OReconciliationError("M27O atomic commit no longer matches release")


def _validate_local_recovery_state(
    *,
    shared_state_path: Path,
    journal: ProductionJournal,
    release: OneContractCanaryRelease,
    commit: AtomicReleaseCommit,
    execution_id: str,
) -> None:
    try:
        with sqlite3.connect(shared_state_path, timeout=10) as db:
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
            budget = db.execute(
                "SELECT real_submission_count FROM production_submission_counter WHERE singleton=1"
            ).fetchone()
        with sqlite3.connect(journal.path, timeout=10) as db:
            row = db.execute(
                "SELECT authorization_id,intent_hash,client_order_id,body_hash,state,possibly_sent "
                "FROM production_journal WHERE execution_id=?",
                (execution_id,),
            ).fetchone()
    except sqlite3.Error as exc:
        raise M27OReconciliationError("durable reconciliation state could not be read") from exc

    if session is None:
        raise M27OReconciliationError("M27O canary session is missing")
    if session[:3] != (release.preview_id, release.approval_id, release.client_order_id):
        raise M27OReconciliationError("M27O canary session identity changed")
    if str(session[3]) not in {"SUBMITTED_OR_UNKNOWN", "RECONCILING", "CANARY_COMPLETE"}:
        raise M27OReconciliationError("M27O canary is not in a post-send reconciliation state")
    if str(session[3]) != "CANARY_COMPLETE" and int(session[4]) != 1:
        raise M27OReconciliationError("M27O unresolved canary lost possibly-submitted state")
    if approval != ("CONSUMED",) or risk != ("CONSUMED",) or budget != (1,):
        raise M27OReconciliationError("M27O one-shot authorization state changed")
    if row is None:
        raise M27OReconciliationError("production journal claim is missing")
    if row[:4] != (
        release.risk_authorization_id,
        release.intent_hash,
        release.client_order_id,
        release.body_hash,
    ):
        raise M27OReconciliationError("production journal identity changed")
    if str(row[4]) == "RECONCILED":
        return
    if str(row[4]) not in _TERMINAL_JOURNAL_INPUTS or int(row[5]) != 1:
        raise M27OReconciliationError("production journal is not reconciliation-owned")


def _read_account_state(
    *, credential_store: ProtectedWriteCredentialStore
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    with credential_store.exclusive() as lock:
        try:
            credential = credential_store._decode_committed_credential(lock)
        except PermissionError as exc:
            raise M27OReconciliationError(
                "committed production write credential unavailable for recovery reads"
            ) from exc
        try:
            signer = RequestSigner(credential.key_id, credential.private_key_pem)
            client = KalshiAccountClient(
                signer,
                UrllibReadTransport(),
                timeout_seconds=3,
                max_retries=1,
            )
            orders = client.get_collection("orders")
            fills = client.get_collection("fills")
            positions = client.get_collection("positions")
            return orders, fills, positions
        except (AccountGatewayError, AuthenticationError) as exc:
            raise M27OReconciliationError(
                "authenticated reconciliation GETs did not complete"
            ) from exc
        finally:
            try:
                del client
                del signer
            except UnboundLocalError:
                pass
            del credential


def _execution_facts(
    *,
    release: OneContractCanaryRelease,
    orders: list[dict[str, object]],
    fills: list[dict[str, object]],
) -> _ExecutionFacts:
    matches = [item for item in orders if item.get("client_order_id") == release.client_order_id]
    if len(matches) != 1:
        return _ExecutionFacts(
            "UNKNOWN",
            "expected exactly one order with the release-bound client_order_id",
            None,
            None,
            None,
            None,
            None,
        )
    order = matches[0]
    order_id = order.get("order_id")
    if not isinstance(order_id, str) or not _ORDER_ID.fullmatch(order_id):
        return _ExecutionFacts(
            "UNKNOWN", "matching order_id malformed", None, None, None, None, None
        )
    status = order.get("status")
    if status not in {"executed", "canceled", "resting"}:
        return _ExecutionFacts(
            "UNKNOWN", "matching order status malformed", order_id, None, None, None, None
        )
    try:
        initial = _decimal(order.get("initial_count_fp"), "initial_count_fp")
        order_filled = _decimal(order.get("fill_count_fp"), "fill_count_fp")
        remaining = _decimal(order.get("remaining_count_fp"), "remaining_count_fp")
    except M27OReconciliationError as exc:
        return _ExecutionFacts("UNKNOWN", str(exc), order_id, str(status), None, None, None)
    if (
        order.get("ticker") != release.market_ticker
        or str(order.get("outcome_side", "")).upper() != release.selected_side
        or order.get("subaccount_number") != 0
        or initial != ONE_CONTRACT
        or not Decimal(0) <= order_filled <= ONE_CONTRACT
        or not Decimal(0) <= remaining <= ONE_CONTRACT
    ):
        return _ExecutionFacts(
            "UNKNOWN",
            "matching order identity or quantity changed",
            order_id,
            str(status),
            None,
            None,
            None,
        )

    matching_fills = [item for item in fills if item.get("order_id") == order_id]
    fill_total = Decimal(0)
    fee_total = Decimal(0)
    max_price: Decimal | None = None
    for item in matching_fills:
        try:
            count = _decimal(item.get("count_fp"), "fill count_fp")
            price_field = (
                "no_price_dollars" if release.selected_side == "NO" else "yes_price_dollars"
            )
            price = _decimal(item.get(price_field), f"fill {price_field}")
            fee = _decimal(item.get("fee_cost"), "fill fee_cost")
        except M27OReconciliationError as exc:
            return _ExecutionFacts("UNKNOWN", str(exc), order_id, str(status), None, None, None)
        ticker = item.get("ticker") or item.get("market_ticker")
        if (
            ticker != release.market_ticker
            or str(item.get("outcome_side", "")).upper() != release.selected_side
            or item.get("subaccount_number") != 0
            or item.get("is_taker") is not True
            or count <= 0
            or count > ONE_CONTRACT
            or price <= 0
            or price >= 1
            or fee < 0
        ):
            return _ExecutionFacts(
                "UNKNOWN",
                "matching fill identity or economics malformed",
                order_id,
                str(status),
                None,
                None,
                None,
            )
        fill_total += count
        fee_total += fee
        max_price = price if max_price is None else max(max_price, price)

    if fill_total != order_filled:
        return _ExecutionFacts(
            "UNKNOWN",
            "order fill count disagrees with authenticated fill records",
            order_id,
            str(status),
            None,
            None,
            None,
        )
    if status == "resting":
        return _ExecutionFacts(
            "UNKNOWN",
            "fill-or-kill canary unexpectedly remained resting",
            order_id,
            str(status),
            fill_total,
            max_price,
            fee_total,
        )
    if status == "executed" and order_filled == ONE_CONTRACT and remaining == 0:
        if fill_total != ONE_CONTRACT or max_price is None:
            return _ExecutionFacts(
                "UNKNOWN",
                "executed order lacks exactly one contract of fills",
                order_id,
                str(status),
                fill_total,
                max_price,
                fee_total,
            )
        classification = "FILLED"
        reason = None
        if max_price > release.exact_price or fee_total > release.maximum_fee:
            classification = "FILLED_POLICY_VIOLATION"
            reason = "authenticated fill exceeded release-bound price or fee ceiling"
        return _ExecutionFacts(
            classification, reason, order_id, str(status), fill_total, max_price, fee_total
        )
    if status == "canceled" and order_filled == 0 and fill_total == 0:
        return _ExecutionFacts("NO_FILL", None, order_id, str(status), Decimal(0), None, Decimal(0))
    return _ExecutionFacts(
        "UNKNOWN",
        "fill-or-kill order ended in a non-terminal or partial state",
        order_id,
        str(status),
        fill_total,
        max_price,
        fee_total,
    )


def _finalize_shared_state(
    *,
    path: Path,
    commit: AtomicReleaseCommit,
    filled: Decimal,
    classification: str,
    now: datetime,
) -> None:
    atoms = int(filled * Decimal(1_000_000))
    if atoms not in {0, 1_000_000}:
        raise M27OReconciliationError("only zero-fill or exactly-one-fill can terminally reconcile")
    try:
        with sqlite3.connect(path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT state,filled_atoms FROM canary_sessions WHERE session_id=?",
                (commit.session_id,),
            ).fetchone()
            if row is None:
                raise M27OReconciliationError("M27O canary session disappeared during finalize")
            state, previous_atoms = str(row[0]), int(row[1])
            if state == "CANARY_COMPLETE":
                if previous_atoms != atoms:
                    raise M27OReconciliationError("terminal canary fill quantity changed")
                return
            if state not in {"SUBMITTED_OR_UNKNOWN", "RECONCILING"}:
                raise M27OReconciliationError("M27O canary lost reconciliation ownership")
            if previous_atoms > atoms:
                raise M27OReconciliationError("reconciled fill quantity decreased")
            if atoms > previous_atoms:
                db.execute(
                    "UPDATE production_fill_counter "
                    "SET real_fill_count=MIN(50,real_fill_count+1) WHERE singleton=1"
                )
            db.execute(
                "UPDATE canary_sessions SET filled_atoms=?,remaining_atoms=?,"
                "state='CANARY_COMPLETE',possibly_submitted=0,resolved_at=? WHERE session_id=?",
                (atoms, 1_000_000 - atoms, now.isoformat(), commit.session_id),
            )
            db.execute("UPDATE canary_runtime SET production_state='DISARMED' WHERE singleton=1")
            db.execute(
                "INSERT INTO canary_events(happened_at,event_type,reference_hash,actor) "
                "VALUES(?,?,?,?)",
                (
                    now.isoformat(),
                    f"M27O_RECONCILED_{classification}",
                    commit.release_hash,
                    "M27O",
                ),
            )
    except sqlite3.Error as exc:
        raise M27OReconciliationError("terminal reconciliation could not be persisted") from exc


def _evidence(
    *,
    observed_at: datetime,
    completed_at: datetime,
    facts: _ExecutionFacts,
    execution_id: str,
    commit: AtomicReleaseCommit,
    release: OneContractCanaryRelease,
    orders: list[dict[str, object]] | None,
    fills: list[dict[str, object]] | None,
    positions: list[dict[str, object]] | None,
    terminal_state: str,
    reconciliation_required: bool,
) -> PostSendReconciliation:
    return PostSendReconciliation(
        SCHEMA,
        SOFTWARE_VERSION,
        observed_at,
        completed_at,
        facts.classification,
        facts.reason,
        execution_id,
        commit.session_id,
        release.client_order_id,
        facts.order_id,
        facts.order_status,
        facts.filled_quantity,
        facts.maximum_fill_price,
        facts.total_fee,
        None if orders is None else _canonical_hash(orders),
        None if fills is None else _canonical_hash(fills),
        None if positions is None else _canonical_hash(positions),
        terminal_state,
        reconciliation_required,
    )


def reconcile_one_contract_live_canary(
    *,
    release: OneContractCanaryRelease,
    atomic_commit: AtomicReleaseCommit,
    execution_id: str,
    shared_state_path: Path,
    credential_store: ProtectedWriteCredentialStore,
    journal: ProductionJournal,
    clock: Clock,
) -> PostSendReconciliation:
    """Perform authenticated GET-only recovery; never retry or mutate the exchange."""
    observed_at = _utc(clock.now())
    _validate_static_binding(release=release, commit=atomic_commit)
    _validate_local_recovery_state(
        shared_state_path=shared_state_path,
        journal=journal,
        release=release,
        commit=atomic_commit,
        execution_id=execution_id,
    )
    try:
        orders, fills, positions = _read_account_state(credential_store=credential_store)
    except M27OReconciliationError as exc:
        facts = _ExecutionFacts("UNKNOWN", str(exc), None, None, None, None, None)
        return _evidence(
            observed_at=observed_at,
            completed_at=_utc(clock.now()),
            facts=facts,
            execution_id=execution_id,
            commit=atomic_commit,
            release=release,
            orders=None,
            fills=None,
            positions=None,
            terminal_state="SUBMITTED_OR_UNKNOWN",
            reconciliation_required=True,
        )

    facts = _execution_facts(release=release, orders=orders, fills=fills)
    completed_at = _utc(clock.now())
    if facts.classification in {"FILLED", "FILLED_POLICY_VIOLATION", "NO_FILL"}:
        try:
            if facts.filled_quantity is None:
                raise M27OReconciliationError("terminal exchange evidence lacks filled quantity")
            _finalize_shared_state(
                path=shared_state_path,
                commit=atomic_commit,
                filled=facts.filled_quantity,
                classification=facts.classification,
                now=completed_at,
            )
            journal.transition(execution_id, "RECONCILED", possibly_sent=True)
        except (M27OReconciliationError, sqlite3.Error) as exc:
            unknown = _ExecutionFacts(
                "UNKNOWN",
                (
                    "terminal exchange evidence obtained but local finalize failed: "
                    f"{type(exc).__name__}"
                ),
                facts.order_id,
                facts.order_status,
                facts.filled_quantity,
                facts.maximum_fill_price,
                facts.total_fee,
            )
            return _evidence(
                observed_at=observed_at,
                completed_at=_utc(clock.now()),
                facts=unknown,
                execution_id=execution_id,
                commit=atomic_commit,
                release=release,
                orders=orders,
                fills=fills,
                positions=positions,
                terminal_state="SUBMITTED_OR_UNKNOWN",
                reconciliation_required=True,
            )
        return _evidence(
            observed_at=observed_at,
            completed_at=_utc(clock.now()),
            facts=facts,
            execution_id=execution_id,
            commit=atomic_commit,
            release=release,
            orders=orders,
            fills=fills,
            positions=positions,
            terminal_state="CANARY_COMPLETE",
            reconciliation_required=False,
        )

    return _evidence(
        observed_at=observed_at,
        completed_at=completed_at,
        facts=facts,
        execution_id=execution_id,
        commit=atomic_commit,
        release=release,
        orders=orders,
        fills=fills,
        positions=positions,
        terminal_state="SUBMITTED_OR_UNKNOWN",
        reconciliation_required=True,
    )
