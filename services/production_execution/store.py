"""Secret-free, durable M15 submission ownership and recovery journal."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .domain import ProductionRequestEnvelope


class ProductionJournal:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS production_state(
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), state TEXT NOT NULL CHECK(state='DISARMED'));
              INSERT OR REPLACE INTO production_state VALUES(1,'DISARMED');
              CREATE TABLE IF NOT EXISTS production_journal(
                execution_id TEXT PRIMARY KEY, authorization_id TEXT NOT NULL UNIQUE,
                intent_hash TEXT NOT NULL, client_order_id TEXT NOT NULL UNIQUE,
                origin TEXT NOT NULL, method TEXT NOT NULL, path TEXT NOT NULL,
                body_hash TEXT NOT NULL, boundary_version TEXT NOT NULL,
                created_at TEXT NOT NULL, state TEXT NOT NULL, possibly_sent INTEGER NOT NULL,
                CHECK(origin='https://external-api.kalshi.com'),
                CHECK(possibly_sent IN (0,1)));
              CREATE TABLE IF NOT EXISTS production_audit(
                event_id INTEGER PRIMARY KEY AUTOINCREMENT, happened_at TEXT NOT NULL,
                event_type TEXT NOT NULL, execution_id TEXT, reason_code TEXT NOT NULL);
            """)
        path.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def claim(self, envelope: ProductionRequestEnvelope, *, version: str) -> bool:
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "INSERT INTO production_journal VALUES(?,?,?,?,?,?,?,?,?,?,?,0)",
                    (
                        envelope.execution_id,
                        envelope.risk_authorization_id,
                        envelope.intent_hash,
                        envelope.client_order_id,
                        envelope.origin,
                        envelope.method,
                        envelope.path,
                        envelope.body_hash,
                        version,
                        envelope.created_at.astimezone(UTC).isoformat(),
                        "PREPARED_NOT_SENT",
                    ),
                )
                self._audit(db, "PRODUCTION_REQUEST_PREPARED", envelope.execution_id, "OFFLINE")
            return True
        except sqlite3.IntegrityError:
            return False

    def transition(self, execution_id: str, state: str, *, possibly_sent: bool) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE production_journal SET state=?,possibly_sent=? WHERE execution_id=?",
                (state, int(possibly_sent), execution_id),
            )
            self._audit(db, state, execution_id, state)

    def recover(self) -> tuple[str, ...]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT execution_id FROM production_journal WHERE state NOT IN ('RECONCILED','REJECTED_DISARMED')"
            ).fetchall()
            for (execution_id,) in rows:
                db.execute(
                    "UPDATE production_journal SET state='UNKNOWN_RECONCILIATION_REQUIRED' WHERE execution_id=?",
                    (execution_id,),
                )
                self._audit(db, "PRODUCTION_RECONCILIATION_REQUIRED", execution_id, "RESTART")
        return tuple(str(row[0]) for row in rows)

    def state(self) -> str:
        with self._connect() as db:
            row = db.execute("SELECT state FROM production_state WHERE singleton=1").fetchone()
        return "DISARMED" if row is None else str(row[0])

    @staticmethod
    def _audit(db: sqlite3.Connection, event: str, execution_id: str, reason: str) -> None:
        db.execute(
            "INSERT INTO production_audit(happened_at,event_type,execution_id,reason_code) VALUES(?,?,?,?)",
            (datetime.now(UTC).isoformat(), event, execution_id, reason),
        )
