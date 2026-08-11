"""Durable one-canary, one-use approval, fill counter, audit, and restart safety."""

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from .domain import ApprovalState, CanaryState, HumanCanaryApproval, HumanCanaryPreview

UNRESOLVED = (
    "READY_FOR_APPROVAL",
    "AWAITING_REAUTH",
    "HUMAN_APPROVED",
    "FINAL_REVALIDATION",
    "CANARY_AUTHORIZED",
    "SUBMISSION_PENDING",
    "SUBMITTED_OR_UNKNOWN",
    "RECONCILING",
)


class CanaryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS canary_runtime(
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), production_state TEXT NOT NULL CHECK(production_state='DISARMED'));
              INSERT OR REPLACE INTO canary_runtime VALUES(1,'DISARMED');
              CREATE TABLE IF NOT EXISTS canary_previews(
                preview_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL UNIQUE,
                candidate_id TEXT NOT NULL, client_order_id TEXT NOT NULL UNIQUE,
                price TEXT NOT NULL, quantity TEXT NOT NULL CHECK(quantity='1.00'),
                created_at TEXT NOT NULL, expires_at TEXT NOT NULL, state TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS canary_approvals(
                approval_id TEXT PRIMARY KEY, preview_hash TEXT NOT NULL UNIQUE,
                owner_identity TEXT NOT NULL, content_hash TEXT NOT NULL UNIQUE,
                approved_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('ISSUED','CONSUMED','EXPIRED','REVOKED')));
              CREATE TABLE IF NOT EXISTS canary_sessions(
                session_id TEXT PRIMARY KEY, preview_id TEXT NOT NULL UNIQUE,
                approval_id TEXT NOT NULL UNIQUE, client_order_id TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL, filled_atoms INTEGER NOT NULL DEFAULT 0,
                remaining_atoms INTEGER NOT NULL DEFAULT 1000000, reconciliation_version TEXT,
                possibly_submitted INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
                resolved_at TEXT, CHECK(filled_atoms>=0 AND remaining_atoms>=0
                  AND filled_atoms+remaining_atoms=1000000));
              CREATE UNIQUE INDEX IF NOT EXISTS one_unresolved_canary ON canary_sessions((1))
                WHERE state IN ('READY_FOR_APPROVAL','AWAITING_REAUTH','HUMAN_APPROVED','FINAL_REVALIDATION','CANARY_AUTHORIZED','SUBMISSION_PENDING','SUBMITTED_OR_UNKNOWN','RECONCILING');
              CREATE TABLE IF NOT EXISTS production_fill_counter(
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), real_fill_count INTEGER NOT NULL CHECK(real_fill_count BETWEEN 0 AND 50));
              INSERT OR IGNORE INTO production_fill_counter VALUES(1,0);
              CREATE TABLE IF NOT EXISTS canary_events(
                event_id INTEGER PRIMARY KEY AUTOINCREMENT, happened_at TEXT NOT NULL,
                event_type TEXT NOT NULL, reference_hash TEXT NOT NULL, actor TEXT NOT NULL);
            """)
            self._migrate_legacy_fill_columns(db)
        path.chmod(0o600)

    @staticmethod
    def _migrate_legacy_fill_columns(db: sqlite3.Connection) -> None:
        columns = {
            str(row[1]) for row in db.execute("PRAGMA table_info(canary_sessions)").fetchall()
        }
        if "filled_quantity" not in columns:
            return
        rows = db.execute(
            "SELECT session_id,preview_id,approval_id,client_order_id,state,filled_quantity,"
            "remaining_quantity,reconciliation_version,possibly_submitted,created_at,resolved_at "
            "FROM canary_sessions"
        ).fetchall()
        converted: list[tuple[object, ...]] = []
        for row in rows:
            filled = Decimal(str(row[5]))
            remaining = Decimal(str(row[6]))
            filled_atoms = filled * Decimal(1_000_000)
            remaining_atoms = remaining * Decimal(1_000_000)
            if (
                not filled.is_finite()
                or not remaining.is_finite()
                or filled_atoms != filled_atoms.to_integral_value()
                or remaining_atoms != remaining_atoms.to_integral_value()
                or filled_atoms + remaining_atoms != 1_000_000
            ):
                raise RuntimeError("legacy canary quantities cannot be migrated exactly")
            converted.append((*row[:5], int(filled_atoms), int(remaining_atoms), *row[7:]))
        db.execute("BEGIN IMMEDIATE")
        db.execute("DROP INDEX IF EXISTS one_unresolved_canary")
        db.execute("ALTER TABLE canary_sessions RENAME TO canary_sessions_legacy")
        db.execute("""CREATE TABLE canary_sessions(
            session_id TEXT PRIMARY KEY, preview_id TEXT NOT NULL UNIQUE,
            approval_id TEXT NOT NULL UNIQUE, client_order_id TEXT NOT NULL UNIQUE,
            state TEXT NOT NULL, filled_atoms INTEGER NOT NULL DEFAULT 0,
            remaining_atoms INTEGER NOT NULL DEFAULT 1000000, reconciliation_version TEXT,
            possibly_submitted INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
            resolved_at TEXT, CHECK(filled_atoms>=0 AND remaining_atoms>=0
              AND filled_atoms+remaining_atoms=1000000))""")
        db.executemany("INSERT INTO canary_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?)", converted)
        db.execute("DROP TABLE canary_sessions_legacy")
        db.execute("""CREATE UNIQUE INDEX one_unresolved_canary ON canary_sessions((1))
            WHERE state IN ('READY_FOR_APPROVAL','AWAITING_REAUTH','HUMAN_APPROVED',
              'FINAL_REVALIDATION','CANARY_AUTHORIZED','SUBMISSION_PENDING',
              'SUBMITTED_OR_UNKNOWN','RECONCILING');
        """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def add_preview(self, preview: HumanCanaryPreview) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO canary_previews VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    preview.preview_id,
                    preview.content_hash,
                    preview.candidate_id,
                    preview.client_order_id,
                    str(preview.limit_price),
                    str(preview.quantity),
                    preview.created_at.isoformat(),
                    preview.expires_at.isoformat(),
                    CanaryState.DRAFT,
                ),
            )
            self._event(db, "CANARY_PREVIEW_CREATED", preview.content_hash, "SYSTEM")

    def issue_approval(
        self,
        approval: HumanCanaryApproval,
        *,
        preview: HumanCanaryPreview,
        now: datetime,
        authenticated_session: bool,
        recent_session: bool,
        password_valid: bool,
        totp_valid: bool,
        csrf_valid: bool,
        rate_limit_clear: bool,
    ) -> None:
        if not all(
            (
                authenticated_session,
                recent_session,
                password_valid,
                totp_valid,
                csrf_valid,
                rate_limit_clear,
            )
        ):
            raise PermissionError("step-up authentication failed")
        if now >= preview.expires_at or approval.preview_hash != preview.content_hash:
            raise PermissionError("preview expired or changed")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO canary_approvals VALUES(?,?,?,?,?,?,?)",
                (
                    approval.approval_id,
                    approval.preview_hash,
                    approval.owner_identity,
                    approval.content_hash,
                    approval.approved_at.isoformat(),
                    approval.expires_at.isoformat(),
                    ApprovalState.ISSUED,
                ),
            )
            self._event(db, "CANARY_HUMAN_APPROVED", approval.content_hash, approval.owner_identity)

    def consume_approval(
        self, approval_id: str, *, owner: str, preview_hash: str, now: datetime
    ) -> bool:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "UPDATE canary_approvals SET state='CONSUMED' WHERE approval_id=? AND owner_identity=? AND preview_hash=? AND state='ISSUED' AND expires_at>?",
                (approval_id, owner, preview_hash, now.isoformat()),
            ).rowcount
            return changed == 1

    def open_session(
        self,
        *,
        session_id: str,
        preview_id: str,
        approval_id: str,
        client_order_id: str,
        now: datetime,
    ) -> bool:
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "INSERT INTO canary_sessions(session_id,preview_id,approval_id,client_order_id,state,created_at) VALUES(?,?,?,?,?,?)",
                    (
                        session_id,
                        preview_id,
                        approval_id,
                        client_order_id,
                        CanaryState.CANARY_AUTHORIZED,
                        now.isoformat(),
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def record_fill(self, session_id: str, *, filled: Decimal, mode: str) -> None:
        if mode != "REAL_PRODUCTION":
            return
        if not filled.is_finite() or not Decimal(0) <= filled <= Decimal("1.00"):
            raise ValueError("fill exceeds one-contract canary")
        atoms_value = filled * Decimal(1_000_000)
        if atoms_value != atoms_value.to_integral_value():
            raise ValueError("fill quantity exceeds six-decimal fixed-point precision")
        filled_atoms = int(atoms_value)
        remaining_atoms = 1_000_000 - filled_atoms
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT filled_atoms FROM canary_sessions WHERE session_id=?", (session_id,)
            ).fetchone()
            if row is None:
                raise ValueError("unknown canary session")
            previous_atoms = int(row[0])
            if filled_atoms < previous_atoms:
                raise ValueError("cumulative fill quantity cannot decrease")
            db.execute(
                "UPDATE canary_sessions SET filled_atoms=?,remaining_atoms=?,state=? WHERE session_id=?",
                (filled_atoms, remaining_atoms, CanaryState.RECONCILING, session_id),
            )
            if filled_atoms > previous_atoms:
                db.execute(
                    "UPDATE production_fill_counter SET real_fill_count=MIN(50,real_fill_count+1) WHERE singleton=1"
                )

    def resolve(self, session_id: str, state: CanaryState, now: datetime) -> None:
        if state not in {
            CanaryState.CANARY_COMPLETE,
            CanaryState.CANARY_FAILED,
            CanaryState.SUBMITTED_OR_UNKNOWN,
            CanaryState.REVOKED,
            CanaryState.EXPIRED,
        }:
            raise ValueError("terminal/disarm state required")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE canary_sessions SET state=?,resolved_at=?,possibly_submitted=? WHERE session_id=?",
                (
                    state,
                    now.isoformat(),
                    int(state == CanaryState.SUBMITTED_OR_UNKNOWN),
                    session_id,
                ),
            )
            db.execute("UPDATE canary_runtime SET production_state='DISARMED' WHERE singleton=1")
            self._event(db, "PRODUCTION_AUTO_DISARMED", session_id, "SYSTEM")

    def recover(self) -> tuple[str, ...]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE canary_runtime SET production_state='DISARMED' WHERE singleton=1")
            rows = db.execute(
                "SELECT session_id FROM canary_sessions WHERE possibly_submitted=1 AND state NOT IN ('CANARY_COMPLETE','CANARY_FAILED','REVOKED','EXPIRED')"
            ).fetchall()
            for (session_id,) in rows:
                db.execute(
                    "UPDATE canary_sessions SET state='SUBMITTED_OR_UNKNOWN' WHERE session_id=?",
                    (session_id,),
                )
        return tuple(str(row[0]) for row in rows)

    @staticmethod
    def _event(db: sqlite3.Connection, kind: str, reference: str, actor: str) -> None:
        db.execute(
            "INSERT INTO canary_events(happened_at,event_type,reference_hash,actor) VALUES(?,?,?,?)",
            (datetime.now(UTC).isoformat(), kind, reference, actor),
        )
