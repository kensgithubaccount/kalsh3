"""Small PostgreSQL durability adapters for the M15 and M16 store contracts.

The normal offline stores remain SQLite-backed.  These adapters use the PostgreSQL
schemas required by the production architecture and are intentionally dependency-
optional: importing the package does not require psycopg, while constructing an
adapter fails clearly when the runtime is absent.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

try:
    import psycopg
    from psycopg import Connection
    from psycopg.errors import UniqueViolation
except ImportError:  # pragma: no cover - exercised only without optional runtime
    psycopg = None  # type: ignore[assignment]
    Connection = Any  # type: ignore[misc,assignment]


class PostgresStoreError(RuntimeError):
    """PostgreSQL runtime or invariant failure."""


M15_SCHEMA = """
CREATE TABLE IF NOT EXISTS production_state(
 singleton smallint PRIMARY KEY CHECK(singleton=1), state text NOT NULL CHECK(state='DISARMED'));
INSERT INTO production_state(singleton,state) VALUES(1,'DISARMED') ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS production_journal(
 execution_id text PRIMARY KEY, authorization_id text NOT NULL UNIQUE,
 intent_hash text NOT NULL, client_order_id text NOT NULL UNIQUE,
 origin text NOT NULL CHECK(origin='https://external-api.kalshi.com'), method text NOT NULL,
 path text NOT NULL, body_hash text NOT NULL, boundary_version text NOT NULL,
 created_at timestamptz NOT NULL, state text NOT NULL, possibly_sent boolean NOT NULL,
 CHECK(possibly_sent IN (true,false)));
CREATE TABLE IF NOT EXISTS production_audit(
 event_id bigserial PRIMARY KEY, happened_at timestamptz NOT NULL,
 event_type text NOT NULL, execution_id text, reason_code text NOT NULL);
"""

M16_SCHEMA = """
CREATE TABLE IF NOT EXISTS canary_runtime(
 singleton smallint PRIMARY KEY CHECK(singleton=1),
 production_state text NOT NULL CHECK(production_state='DISARMED'));
INSERT INTO canary_runtime(singleton,production_state) VALUES(1,'DISARMED') ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS canary_sessions(
 session_id text PRIMARY KEY, preview_id text NOT NULL UNIQUE,
 approval_id text NOT NULL UNIQUE, client_order_id text NOT NULL UNIQUE,
 state text NOT NULL, filled_atoms bigint NOT NULL DEFAULT 0,
 remaining_atoms bigint NOT NULL DEFAULT 1000000,
 reconciliation_version text, possibly_submitted boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL, resolved_at timestamptz,
 CHECK(filled_atoms>=0 AND remaining_atoms>=0 AND filled_atoms+remaining_atoms=1000000));
CREATE UNIQUE INDEX IF NOT EXISTS one_unresolved_canary ON canary_sessions((true))
 WHERE state IN ('READY_FOR_APPROVAL','AWAITING_REAUTH','HUMAN_APPROVED',
 'FINAL_REVALIDATION','CANARY_AUTHORIZED','SUBMISSION_PENDING','SUBMITTED_OR_UNKNOWN','RECONCILING');
CREATE TABLE IF NOT EXISTS production_submission_counter(
 singleton smallint PRIMARY KEY CHECK(singleton=1),
 real_submission_count integer NOT NULL CHECK(real_submission_count BETWEEN 0 AND 1));
INSERT INTO production_submission_counter(singleton,real_submission_count)
 VALUES(1,0) ON CONFLICT DO NOTHING;
"""


def _require_psycopg() -> Any:
    if psycopg is None:
        raise PostgresStoreError("psycopg is required for PostgreSQL stores")
    return psycopg


class _BasePostgresStore:
    schema: str

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[Connection[Any]]:
        driver = _require_psycopg()
        connection = driver.connect(self.dsn)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(self.schema)


class PostgresProductionJournal(_BasePostgresStore):
    schema = M15_SCHEMA

    def claim(self, envelope: Any, *, version: str) -> bool:
        try:
            with self._connection() as connection, connection.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO production_journal
                        (execution_id,authorization_id,intent_hash,client_order_id,origin,method,path,
                         body_hash,boundary_version,created_at,state,possibly_sent)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,false)""",
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
                        envelope.created_at.astimezone(UTC),
                        "PREPARED_NOT_SENT",
                    ),
                )
                cursor.execute(
                    "INSERT INTO production_audit(happened_at,event_type,execution_id,"
                    "reason_code) VALUES(%s,%s,%s,%s)",
                    (
                        datetime.now(UTC),
                        "PRODUCTION_REQUEST_PREPARED",
                        envelope.execution_id,
                        "POSTGRES",
                    ),
                )
            return True
        except UniqueViolation:
            return False

    def transition(self, execution_id: str, state: str, *, possibly_sent: bool) -> None:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE production_journal SET state=%s, possibly_sent=%s WHERE execution_id=%s",
                (state, possibly_sent, execution_id),
            )
            cursor.execute(
                "INSERT INTO production_audit(happened_at,event_type,execution_id,"
                "reason_code) VALUES(%s,%s,%s,%s)",
                (datetime.now(UTC), state, execution_id, state),
            )

    def recover(self) -> tuple[str, ...]:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """UPDATE production_journal SET state='UNKNOWN_RECONCILIATION_REQUIRED'
                    WHERE state NOT IN ('RECONCILED','REJECTED_DISARMED') RETURNING execution_id"""
            )
            rows = cursor.fetchall()
        return tuple(str(row[0]) for row in rows)

    def state(self) -> str:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT state FROM production_state WHERE singleton=1")
            row = cursor.fetchone()
        return "DISARMED" if row is None else str(row[0])


class PostgresCanaryStore(_BasePostgresStore):
    schema = M16_SCHEMA

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
            with self._connection() as connection, connection.cursor() as cursor:
                cursor.execute(
                    "SELECT real_submission_count FROM production_submission_counter "
                    "WHERE singleton=1 FOR UPDATE"
                )
                row = cursor.fetchone()
                if row is None or int(row[0]) != 0:
                    return False
                cursor.execute(
                    """INSERT INTO canary_sessions
                        (session_id,preview_id,approval_id,client_order_id,state,created_at)
                        VALUES (%s,%s,%s,%s,%s,%s)""",
                    (
                        session_id,
                        preview_id,
                        approval_id,
                        client_order_id,
                        "CANARY_AUTHORIZED",
                        now.astimezone(UTC),
                    ),
                )
            return True
        except UniqueViolation:
            return False

    def record_submission_attempt(self, *, session_id: str, mode: str) -> None:
        if mode != "REAL_PRODUCTION":
            raise ValueError("only real production submission attempts consume this budget")
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM canary_sessions WHERE session_id=%s", (session_id,))
            if cursor.fetchone() is None:
                raise ValueError("unknown canary session")
            cursor.execute(
                "UPDATE production_submission_counter SET real_submission_count=1 "
                "WHERE singleton=1 AND real_submission_count=0"
            )
            if cursor.rowcount != 1:
                raise ValueError("global one-order experimental canary limit already used")

    def recover(self) -> tuple[str, ...]:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE canary_runtime SET production_state='DISARMED' WHERE singleton=1"
            )
            cursor.execute(
                """UPDATE canary_sessions SET state='SUBMITTED_OR_UNKNOWN'
                    WHERE possibly_submitted=true AND state NOT IN
                    ('CANARY_COMPLETE','CANARY_FAILED','REVOKED','EXPIRED')
                    RETURNING session_id"""
            )
            rows = cursor.fetchall()
        return tuple(str(row[0]) for row in rows)

    def state(self) -> str:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT production_state FROM canary_runtime WHERE singleton=1")
            row = cursor.fetchone()
        return "DISARMED" if row is None else str(row[0])
