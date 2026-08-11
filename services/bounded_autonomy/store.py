"""Durable M17 state that resets to OFF and cannot authorize production."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .domain import AutonomyReadinessSnapshot, BoundedAutonomyProposal


class AutonomyStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS autonomy_runtime(
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                autonomy_state TEXT NOT NULL CHECK(autonomy_state='OFF'),
                production_state TEXT NOT NULL CHECK(production_state='DISARMED'));
              INSERT OR REPLACE INTO autonomy_runtime VALUES(1,'OFF','DISARMED');
              CREATE TABLE IF NOT EXISTS autonomy_readiness_snapshots(
                snapshot_id TEXT PRIMARY KEY, observed_at TEXT NOT NULL,
                policy_version TEXT NOT NULL, content_hash TEXT NOT NULL UNIQUE,
                missing_gates TEXT NOT NULL, state TEXT NOT NULL CHECK(state='OFF'));
              CREATE TABLE IF NOT EXISTS autonomy_proposals(
                proposal_id TEXT PRIMARY KEY, readiness_hash TEXT NOT NULL,
                created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                rationale TEXT NOT NULL, content_hash TEXT NOT NULL UNIQUE,
                requested_state TEXT NOT NULL CHECK(requested_state='OFF'),
                production_influence TEXT NOT NULL CHECK(production_influence='NONE'));
              CREATE TABLE IF NOT EXISTS autonomy_events(
                event_id INTEGER PRIMARY KEY AUTOINCREMENT, happened_at TEXT NOT NULL,
                event_type TEXT NOT NULL, reference_hash TEXT NOT NULL);
            """)
        path.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def persist_snapshot(self, snapshot: AutonomyReadinessSnapshot) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO autonomy_readiness_snapshots VALUES(?,?,?,?,?,'OFF')",
                (
                    snapshot.snapshot_id,
                    snapshot.observed_at.isoformat(),
                    snapshot.policy_version,
                    snapshot.content_hash,
                    ",".join(snapshot.evidence.missing()),
                ),
            )

    def persist_proposal(self, proposal: BoundedAutonomyProposal) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO autonomy_proposals VALUES(?,?,?,?,?,?,?,?)",
                (
                    proposal.proposal_id,
                    proposal.readiness_hash,
                    proposal.created_at.isoformat(),
                    proposal.expires_at.isoformat(),
                    proposal.rationale,
                    proposal.content_hash,
                    proposal.requested_state,
                    proposal.production_influence,
                ),
            )
            db.execute(
                "INSERT INTO autonomy_events(happened_at,event_type,reference_hash) VALUES(?,?,?)",
                (
                    datetime.now(UTC).isoformat(),
                    "AUTONOMY_PROPOSAL_RECORDED_OFF_ONLY",
                    proposal.content_hash,
                ),
            )

    def recover(self) -> tuple[str, str]:
        with self._connect() as db:
            db.execute(
                "UPDATE autonomy_runtime SET autonomy_state='OFF',"
                "production_state='DISARMED' WHERE singleton=1"
            )
            row = db.execute(
                "SELECT autonomy_state,production_state FROM autonomy_runtime WHERE singleton=1"
            ).fetchone()
        return ("OFF", "DISARMED") if row is None else (str(row[0]), str(row[1]))
