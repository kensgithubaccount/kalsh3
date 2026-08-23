"""Create-only local bootstrap for the durable M27O shared SQLite state.

This module is deliberately non-networked and credential-free. It creates the
single SQLite database shared by M16 CanaryStore and M13 AuthorizationStore,
establishes an explicitly operator-confirmed CLEAR compliance state, verifies
the initial fail-closed state, and atomically publishes the database.

It cannot approve a canary, issue a risk authorization, burn the one-order
budget, arm production, sign, or send an order.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from services.risk_engine.authorization import AuthorizationStore, SystemClock
from services.risk_engine.domain import ComplianceState
from services.supervised_canary.store import CanaryStore

SCHEMA = "kalsh3.m27o.production-state-bootstrap.v1"
SOFTWARE_VERSION = "kalsh3.m27o.production-state-bootstrap/1"

EXACT_CONFIRMATION = "INITIALIZE DISARMED ONE-CONTRACT CANARY STATE WITH COMPLIANCE CLEAR"

REQUIRED_TABLES = frozenset(
    {
        "canary_runtime",
        "canary_previews",
        "canary_approvals",
        "canary_sessions",
        "production_fill_counter",
        "production_submission_counter",
        "canary_events",
        "risk_authorizations",
        "risk_reservations",
        "global_halt_state",
        "compliance_state",
        "risk_events",
        "durable_kill_states",
        "durable_loss_holds",
    }
)

EXPECTED_KILLS = (
    ("CREDENTIAL", "NORMAL"),
    ("DATA", "NORMAL"),
    ("PORTFOLIO", "NORMAL"),
    ("STRATEGY", "NORMAL"),
)


class StateBootstrapError(PermissionError):
    """Bootstrap invariant failed."""


@dataclass(frozen=True, slots=True)
class StateBootstrapReceipt:
    state_path: Path
    created_at: datetime
    production_state: str
    real_submission_count: int
    real_fill_count: int
    global_halt_active: bool
    compliance_state: str
    kill_states: tuple[tuple[str, str], ...]
    loss_holds: tuple[int, int, int]
    preview_count: int
    approval_count: int
    session_count: int
    risk_authorization_count: int
    risk_reservation_count: int

    def to_json(self) -> dict[str, object]:
        return {
            "schema": SCHEMA,
            "software_version": SOFTWARE_VERSION,
            "classification": "PASS",
            "state_path": str(self.state_path),
            "created_at": self.created_at.astimezone(UTC).isoformat(),
            "production_state": self.production_state,
            "real_submission_count": self.real_submission_count,
            "real_fill_count": self.real_fill_count,
            "global_halt_active": self.global_halt_active,
            "compliance_state": self.compliance_state,
            "kill_states": [list(item) for item in self.kill_states],
            "loss_holds": list(self.loss_holds),
            "preview_count": self.preview_count,
            "approval_count": self.approval_count,
            "session_count": self.session_count,
            "risk_authorization_count": self.risk_authorization_count,
            "risk_reservation_count": self.risk_reservation_count,
        }


def default_state_path() -> Path:
    state_home = os.environ.get("XDG_STATE_HOME")
    root = Path(state_home) if state_home else Path.home() / ".local" / "state"
    return root / "kalsh3" / "production-canary" / "m27o-shared.sqlite3"


def _sidecars(path: Path) -> tuple[Path, Path, Path]:
    return (
        path.with_name(path.name + "-wal"),
        path.with_name(path.name + "-shm"),
        path.with_name(path.name + "-journal"),
    )


def _validate_operator_text(name: str, value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise StateBootstrapError(f"{name} is required")
    if len(cleaned) > 200:
        raise StateBootstrapError(f"{name} is too long")
    if any(ord(character) < 32 for character in cleaned):
        raise StateBootstrapError(f"{name} contains control characters")
    return cleaned


def _scalar(db: sqlite3.Connection, query: str) -> object:
    row = db.execute(query).fetchone()
    if row is None or len(row) != 1:
        raise StateBootstrapError("required singleton state is unavailable")
    return row[0]


def _integer_scalar(db: sqlite3.Connection, query: str, *, field: str) -> int:
    value = _scalar(db, query)
    if isinstance(value, bool) or not isinstance(value, int):
        raise StateBootstrapError(f"{field} is not an integer")
    return value


def _count(db: sqlite3.Connection, table: str) -> int:
    queries = {
        "canary_previews": "SELECT COUNT(*) FROM canary_previews",
        "canary_approvals": "SELECT COUNT(*) FROM canary_approvals",
        "canary_sessions": "SELECT COUNT(*) FROM canary_sessions",
        "risk_authorizations": "SELECT COUNT(*) FROM risk_authorizations",
        "risk_reservations": "SELECT COUNT(*) FROM risk_reservations",
    }
    query = queries.get(table)
    if query is None:
        raise StateBootstrapError("unsupported bootstrap count")
    return _integer_scalar(db, query, field=f"{table} count")


def _verify_state(path: Path) -> StateBootstrapReceipt:
    uri = "file:" + str(path) + "?mode=ro"

    with sqlite3.connect(uri, uri=True) as db:
        db.execute("PRAGMA query_only=ON")

        tables = {
            str(row[0])
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }

        if not REQUIRED_TABLES.issubset(tables):
            raise StateBootstrapError("shared M16/M13 schema is incomplete")

        production_state = str(
            _scalar(
                db,
                "SELECT production_state FROM canary_runtime WHERE singleton=1",
            )
        )
        real_submission_count = _integer_scalar(
            db,
            "SELECT real_submission_count FROM production_submission_counter WHERE singleton=1",
            field="real submission count",
        )
        real_fill_count = _integer_scalar(
            db,
            "SELECT real_fill_count FROM production_fill_counter WHERE singleton=1",
            field="real fill count",
        )
        global_halt_value = _integer_scalar(
            db,
            "SELECT active FROM global_halt_state WHERE singleton=1",
            field="global halt active flag",
        )
        if global_halt_value not in (0, 1):
            raise StateBootstrapError("global halt active flag is outside the boolean domain")
        global_halt_active = bool(global_halt_value)
        compliance_state = str(
            _scalar(
                db,
                "SELECT state FROM compliance_state WHERE singleton=1",
            )
        )

        kill_states = tuple(
            (str(row[0]), str(row[1]))
            for row in db.execute(
                "SELECT category,level FROM durable_kill_states ORDER BY category"
            ).fetchall()
        )

        loss_row = db.execute(
            "SELECT weekly_review_required,"
            "monthly_review_required,"
            "experiment_halt_required "
            "FROM durable_loss_holds WHERE singleton=1"
        ).fetchone()

        if loss_row is None or len(loss_row) != 3:
            raise StateBootstrapError("durable loss-hold state unavailable")

        loss_holds = (
            int(loss_row[0]),
            int(loss_row[1]),
            int(loss_row[2]),
        )

        preview_count = _count(db, "canary_previews")
        approval_count = _count(db, "canary_approvals")
        session_count = _count(db, "canary_sessions")
        risk_authorization_count = _count(db, "risk_authorizations")
        risk_reservation_count = _count(db, "risk_reservations")

    if production_state != "DISARMED":
        raise StateBootstrapError("production state is not DISARMED")
    if real_submission_count != 0:
        raise StateBootstrapError("real submission budget is not unused")
    if real_fill_count != 0:
        raise StateBootstrapError("real fill counter is not zero")
    if global_halt_active:
        raise StateBootstrapError("global halt unexpectedly active")
    if compliance_state != ComplianceState.CLEAR:
        raise StateBootstrapError("compliance state is not CLEAR")
    if kill_states != EXPECTED_KILLS:
        raise StateBootstrapError("kill states are not all NORMAL")
    if loss_holds != (0, 0, 0):
        raise StateBootstrapError("durable loss holds are active")
    if any(
        (
            preview_count,
            approval_count,
            session_count,
            risk_authorization_count,
            risk_reservation_count,
        )
    ):
        raise StateBootstrapError("fresh bootstrap contains one-shot state")

    return StateBootstrapReceipt(
        state_path=path,
        created_at=datetime.now(UTC),
        production_state=production_state,
        real_submission_count=real_submission_count,
        real_fill_count=real_fill_count,
        global_halt_active=global_halt_active,
        compliance_state=compliance_state,
        kill_states=kill_states,
        loss_holds=loss_holds,
        preview_count=preview_count,
        approval_count=approval_count,
        session_count=session_count,
        risk_authorization_count=risk_authorization_count,
        risk_reservation_count=risk_reservation_count,
    )


def _remove_safe_sidecars(path: Path) -> None:
    wal, shm, journal = _sidecars(path)

    if os.path.lexists(journal):
        raise StateBootstrapError("unexpected SQLite rollback journal remained")

    if os.path.lexists(wal):
        if wal.stat().st_size != 0:
            raise StateBootstrapError("SQLite WAL still contains uncheckpointed data")
        wal.unlink()

    if os.path.lexists(shm):
        shm.unlink()


def _checkpoint_for_publication(path: Path) -> None:
    db = sqlite3.connect(
        path,
        timeout=10,
        isolation_level=None,
    )

    try:
        db.execute("PRAGMA busy_timeout=10000")
        checkpoint = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    except sqlite3.Error as exc:
        raise StateBootstrapError("SQLite WAL checkpoint failed") from exc
    finally:
        db.close()

    if checkpoint is None or int(checkpoint[0]) != 0:
        raise StateBootstrapError("SQLite WAL checkpoint did not complete")

    _remove_safe_sidecars(path)


def bootstrap_state(
    *,
    state_path: Path,
    actor: str,
    reason: str,
    confirmation: str,
) -> StateBootstrapReceipt:
    if confirmation != EXACT_CONFIRMATION:
        raise StateBootstrapError("exact bootstrap confirmation is required")

    actor = _validate_operator_text("actor", actor)
    reason = _validate_operator_text("reason", reason)

    state_path = state_path.expanduser()
    state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_path.parent.chmod(0o700)

    protected_paths = (state_path, *_sidecars(state_path))
    if any(os.path.lexists(item) for item in protected_paths):
        raise StateBootstrapError("production canary state or SQLite companion already exists")

    temporary = state_path.parent / (f".{state_path.name}.bootstrap-{uuid4().hex}.tmp")

    fd = os.open(
        temporary,
        os.O_CREAT | os.O_EXCL | os.O_RDWR,
        0o600,
    )
    os.close(fd)

    installed = False

    try:
        CanaryStore(temporary)
        authorization_store = AuthorizationStore(temporary, SystemClock())
        authorization_store.set_compliance(
            ComplianceState.CLEAR,
            actor=actor,
            reason=reason,
        )

        _checkpoint_for_publication(temporary)

        # Prove the main database is independently readable after the
        # checkpoint, then remove any read-only WAL bookkeeping before
        # publishing it.
        _verify_state(temporary)
        _remove_safe_sidecars(temporary)

        if any(os.path.lexists(item) for item in protected_paths):
            raise StateBootstrapError("production canary state appeared during bootstrap")

        try:
            os.link(temporary, state_path)
        except FileExistsError as exc:
            raise StateBootstrapError("production canary state already exists") from exc

        installed = True
        state_path.chmod(0o600)

        receipt = _verify_state(state_path)
        _remove_safe_sidecars(state_path)

        if (state_path.stat().st_mode & 0o777) != 0o600:
            raise StateBootstrapError("production canary state mode is not 0600")

        return receipt
    finally:
        if os.path.lexists(temporary):
            temporary.unlink()
        for sidecar in _sidecars(temporary):
            if os.path.lexists(sidecar):
                sidecar.unlink()

        # Once atomically published, never automatically delete the final path.
        # Any post-publication verification failure must require manual review.
        if installed:
            state_path.chmod(0o600)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create-only local M27O shared-state bootstrap. "
            "No network, credentials, approval, burn, arm, or order."
        )
    )
    parser.add_argument(
        "--state-path",
        type=Path,
        default=default_state_path(),
    )
    parser.add_argument("--actor", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--confirm", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    try:
        receipt = bootstrap_state(
            state_path=args.state_path,
            actor=args.actor,
            reason=args.reason,
            confirmation=args.confirm,
        )
    except Exception as exc:
        print(
            f"BLOCKER: M27O production-state bootstrap failed ({type(exc).__name__})",
            file=sys.stderr,
        )
        return 2

    print(json.dumps(receipt.to_json(), sort_keys=True, indent=2))
    print("PRODUCTION_ARMED: DISARMED")
    print("REAL_SUBMISSION_COUNT: 0")
    print("REAL_FILL_COUNT: 0")
    print("M16_APPROVAL: NONE")
    print("M13_AUTHORIZATION: NONE")
    print("M27O_EXECUTION_AUTHORIZATION: NONE")
    print("KALSHI_NETWORK: NONE")
    print("KALSHI_MUTATION: NONE")
    print("ORDER_SENT: NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
