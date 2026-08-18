"""M27E PostgreSQL acceptance; skipped unless an operator supplies a test DSN."""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import UTC, datetime

import pytest

pytest.importorskip("psycopg")

from services.operations.postgres_stores import (
    PostgresCanaryStore,
    PostgresProductionJournal,
)
from tests.test_production_execution_m15_complete import envelope

POSTGRES_DSN_ENV = "KALSH3_TEST_POSTGRES_DSN"
DSN = os.environ.get(POSTGRES_DSN_ENV)

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not DSN,
        reason=f"{POSTGRES_DSN_ENV} is not set",
    ),
]
NOW = datetime(2026, 8, 17, 12, tzinfo=UTC)


def open_process(index: int) -> bool:
    dsn = os.environ[POSTGRES_DSN_ENV]
    store = PostgresCanaryStore(dsn)
    return store.open_session(
        session_id=f"process-session-{index}",
        preview_id=f"process-preview-{index}",
        approval_id=f"process-approval-{index}",
        client_order_id=f"process-client-{index}",
        now=NOW,
    )


def test_schema_journal_uniqueness_reconnect_and_recovery() -> None:
    assert DSN is not None
    first = PostgresProductionJournal(DSN)
    PostgresCanaryStore(DSN)
    reset_database()
    env = envelope()
    assert first.state() == "DISARMED"
    assert first.claim(env, version="m27e-postgres")
    assert not PostgresProductionJournal(DSN).claim(env, version="m27e-postgres")
    first.transition(env.execution_id, "UNKNOWN_RECONCILIATION_REQUIRED", possibly_sent=True)
    assert PostgresProductionJournal(DSN).recover() == (env.execution_id,)


def test_m16_global_submission_and_concurrent_unresolved_uniqueness() -> None:
    assert DSN is not None
    store = PostgresCanaryStore(DSN)
    reset_database()

    with ProcessPoolExecutor(max_workers=3) as pool:
        assert sum(pool.map(open_process, range(3))) == 1
    reset_database()

    def open_one(index: int) -> tuple[str, bool]:
        session_id = f"session-{index}"
        opened = store.open_session(
            session_id=session_id,
            preview_id=f"preview-{index}",
            approval_id=f"approval-{index}",
            client_order_id=f"client-{index}",
            now=NOW,
        )
        return session_id, opened

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(open_one, range(4)))
    winners = [session_id for session_id, opened in results if opened]
    assert len(winners) == 1
    winner = winners[0]
    store.record_submission_attempt(session_id=winner, mode="REAL_PRODUCTION")
    with pytest.raises(ValueError, match="global one-order"):
        store.record_submission_attempt(session_id=winner, mode="REAL_PRODUCTION")


def test_postgres_transaction_rollback_and_restart_recovery() -> None:
    assert DSN is not None
    store = PostgresCanaryStore(DSN)
    reset_database()
    # A failed mode validation commits nothing and leaves the singleton usable.
    with pytest.raises(ValueError):
        store.record_submission_attempt(session_id="missing", mode="DEMO")
    assert store.state() == "DISARMED"
    assert store.open_session(
        session_id="session-0",
        preview_id="preview-0",
        approval_id="approval-0",
        client_order_id="client-0",
        now=NOW,
    )

    import psycopg

    with psycopg.connect(DSN) as connection, connection.cursor() as cursor:
        cursor.execute(
            "UPDATE canary_sessions SET possibly_submitted=true,state='SUBMISSION_PENDING'"
        )
    assert store.recover() == ("session-0",)
    assert PostgresCanaryStore(DSN).state() == "DISARMED"


def reset_database() -> None:
    import psycopg

    assert DSN is not None
    with psycopg.connect(DSN) as connection, connection.cursor() as cursor:
        cursor.execute("TRUNCATE production_journal, production_audit RESTART IDENTITY")
        cursor.execute("TRUNCATE canary_sessions RESTART IDENTITY")
        cursor.execute("UPDATE production_submission_counter SET real_submission_count=0")
        cursor.execute("UPDATE canary_runtime SET production_state='DISARMED'")
