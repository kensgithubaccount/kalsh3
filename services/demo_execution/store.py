"""Durable submission journal, order, fill, private-stream, and reconciliation state."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from .adapter import MutationRequest
from .domain import ExecutionEnvironment, LocalOrder, OrderState, stable_hash


@dataclass(frozen=True, slots=True)
class FillEvent:
    trade_id: str
    order_id: str
    ticker: str
    price: Decimal
    quantity: Decimal
    fee: Decimal
    is_taker: bool
    outcome_side: str
    matching_time: datetime
    subaccount: int


@dataclass(frozen=True, slots=True)
class QueueObservation:
    observed_at: datetime
    order_id: str
    market: str
    queue_position: Decimal
    remaining_quantity: Decimal
    price: Decimal
    quality: str = "OBSERVED_DEMO_ORDER_QUEUE"


class ExecutionStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS execution_journals(
                  execution_id TEXT PRIMARY KEY, intent_hash TEXT NOT NULL,
                  client_order_id TEXT NOT NULL UNIQUE, authorization_id TEXT NOT NULL UNIQUE,
                  environment TEXT NOT NULL CHECK(environment IN ('MOCK','PAPER','DEMO')),
                  host TEXT NOT NULL, method TEXT NOT NULL, path TEXT NOT NULL,
                  body_json TEXT NOT NULL, body_hash TEXT NOT NULL, created_at TEXT NOT NULL,
                  attempt_number INTEGER NOT NULL CHECK(attempt_number=1), state TEXT NOT NULL,
                  may_have_been_sent INTEGER NOT NULL DEFAULT 0 CHECK(may_have_been_sent IN (0,1))
                );
                CREATE TABLE IF NOT EXISTS local_orders(
                  execution_id TEXT PRIMARY KEY, client_order_id TEXT NOT NULL UNIQUE,
                  environment TEXT NOT NULL, intent_hash TEXT NOT NULL, ticker TEXT NOT NULL,
                  outcome_side TEXT NOT NULL, price TEXT NOT NULL, quantity TEXT NOT NULL,
                  filled_quantity TEXT NOT NULL, fees TEXT NOT NULL, state TEXT NOT NULL,
                  updated_at TEXT NOT NULL, exchange_order_id TEXT UNIQUE,
                  reconciliation_required INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS order_state_events(
                  event_key TEXT PRIMARY KEY, execution_id TEXT NOT NULL, sequence INTEGER,
                  state TEXT NOT NULL, happened_at TEXT NOT NULL, source TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS exchange_fills(
                  trade_id TEXT PRIMARY KEY, order_id TEXT NOT NULL, ticker TEXT NOT NULL,
                  price TEXT NOT NULL, quantity TEXT NOT NULL, fee TEXT NOT NULL,
                  is_taker INTEGER NOT NULL, outcome_side TEXT NOT NULL,
                  matching_time TEXT NOT NULL, subaccount INTEGER NOT NULL CHECK(subaccount=0)
                );
                CREATE TABLE IF NOT EXISTS queue_observations(
                  observation_hash TEXT PRIMARY KEY, observed_at TEXT NOT NULL,
                  order_id TEXT NOT NULL, market TEXT NOT NULL, queue_position TEXT NOT NULL,
                  remaining_quantity TEXT NOT NULL, price TEXT NOT NULL, quality TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS execution_reconciliation(
                  execution_id TEXT PRIMARY KEY, state TEXT NOT NULL, checked_at TEXT NOT NULL,
                  orders_match INTEGER NOT NULL, fills_match INTEGER NOT NULL,
                  positions_match INTEGER NOT NULL, fees_match INTEGER NOT NULL,
                  account_risk_match INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS mutation_attempts(
                  attempt_id TEXT PRIMARY KEY, execution_id TEXT NOT NULL,
                  kind TEXT NOT NULL, method TEXT NOT NULL, path TEXT NOT NULL,
                  body_hash TEXT NOT NULL, created_at TEXT NOT NULL,
                  state TEXT NOT NULL, may_have_been_sent INTEGER NOT NULL,
                  UNIQUE(execution_id,kind,body_hash)
                );
                CREATE TABLE IF NOT EXISTS ws_state(
                  channel TEXT PRIMARY KEY, last_sequence INTEGER,
                  trustworthy INTEGER NOT NULL, reason TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS client_order_lineage(
                  old_client_order_id TEXT NOT NULL, new_client_order_id TEXT NOT NULL UNIQUE,
                  execution_id TEXT NOT NULL, changed_at TEXT NOT NULL,
                  PRIMARY KEY(old_client_order_id,new_client_order_id)
                );
            """)
        path.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def acquire_journal(
        self,
        *,
        execution_id: str,
        intent_hash: str,
        client_order_id: str,
        authorization_id: str,
        environment: ExecutionEnvironment,
        host: str,
        request: MutationRequest,
        now: datetime,
    ) -> bool:
        body_json = json.dumps(request.body, sort_keys=True, separators=(",", ":"))
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "INSERT INTO execution_journals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
                    (
                        execution_id,
                        intent_hash,
                        client_order_id,
                        authorization_id,
                        environment,
                        host,
                        request.method,
                        request.path,
                        body_json,
                        stable_hash(body_json),
                        now.astimezone(UTC).isoformat(),
                        1,
                        "JOURNALED_NOT_SENT",
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def journal_state(self, execution_id: str, state: str, *, may_have_been_sent: bool) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE execution_journals SET state=?,may_have_been_sent=? WHERE execution_id=?",
                (state, int(may_have_been_sent), execution_id),
            )

    def acquire_mutation(
        self, *, execution_id: str, kind: str, request: MutationRequest, now: datetime
    ) -> str | None:
        body_hash = stable_hash(json.dumps(request.body, sort_keys=True, separators=(",", ":")))
        attempt_id = stable_hash((execution_id, kind, request.path, body_hash))
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "INSERT INTO mutation_attempts VALUES(?,?,?,?,?,?,?,?,0)",
                    (
                        attempt_id,
                        execution_id,
                        kind,
                        request.method,
                        request.path,
                        body_hash,
                        now.astimezone(UTC).isoformat(),
                        "JOURNALED_NOT_SENT",
                    ),
                )
            return attempt_id
        except sqlite3.IntegrityError:
            return None

    def mutation_state(self, attempt_id: str, state: str, *, may_have_been_sent: bool) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE mutation_attempts SET state=?,may_have_been_sent=? WHERE attempt_id=?",
                (state, int(may_have_been_sent), attempt_id),
            )

    def incomplete_journals(self) -> tuple[tuple[object, ...], ...]:
        with self._connect() as db:
            return tuple(
                db.execute(
                    "SELECT execution_id,client_order_id,state,may_have_been_sent FROM execution_journals WHERE state NOT IN ('RECONCILED','PROVEN_UNSENT') ORDER BY execution_id"
                ).fetchall()
            )

    def save_order(self, order: LocalOrder) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO local_orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(execution_id) DO UPDATE SET filled_quantity=excluded.filled_quantity,fees=excluded.fees,state=excluded.state,updated_at=excluded.updated_at,exchange_order_id=COALESCE(excluded.exchange_order_id,local_orders.exchange_order_id),reconciliation_required=excluded.reconciliation_required",
                (
                    order.execution_id,
                    order.client_order_id,
                    order.environment,
                    order.intent_hash,
                    order.ticker,
                    order.outcome_side,
                    str(order.price),
                    str(order.quantity),
                    str(order.filled_quantity),
                    str(order.fees),
                    order.state,
                    order.updated_at.astimezone(UTC).isoformat(),
                    order.exchange_order_id,
                    int(order.reconciliation_required),
                ),
            )

    def add_fill(self, fill: FillEvent) -> bool:
        if fill.subaccount != 0:
            raise ValueError("fill must be explicitly scoped to subaccount 0")
        with self._connect() as db:
            changed = db.execute(
                "INSERT OR IGNORE INTO exchange_fills VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    fill.trade_id,
                    fill.order_id,
                    fill.ticker,
                    str(fill.price),
                    str(fill.quantity),
                    str(fill.fee),
                    int(fill.is_taker),
                    fill.outcome_side,
                    fill.matching_time.astimezone(UTC).isoformat(),
                    0,
                ),
            ).rowcount
        return changed == 1

    def fill_totals(self, order_id: str) -> tuple[Decimal, Decimal]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT quantity,fee FROM exchange_fills WHERE order_id=?", (order_id,)
            ).fetchall()
        return (
            sum((Decimal(row[0]) for row in rows), Decimal(0)),
            sum((Decimal(row[1]) for row in rows), Decimal(0)),
        )

    def ingest_ws_event(
        self,
        *,
        channel: str,
        event_key: str,
        execution_id: str,
        state: OrderState,
        sequence: int | None,
        happened_at: datetime,
    ) -> bool:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT last_sequence FROM ws_state WHERE channel=?", (channel,)
            ).fetchone()
            if sequence is not None and prior and prior[0] is not None and sequence != prior[0] + 1:
                db.execute(
                    "INSERT INTO ws_state VALUES(?,?,0,'SEQUENCE_GAP',?) ON CONFLICT(channel) DO UPDATE SET last_sequence=excluded.last_sequence,trustworthy=0,reason='SEQUENCE_GAP',updated_at=excluded.updated_at",
                    (channel, sequence, happened_at.astimezone(UTC).isoformat()),
                )
                return False
            changed = db.execute(
                "INSERT OR IGNORE INTO order_state_events VALUES(?,?,?,?,?,?)",
                (
                    event_key,
                    execution_id,
                    sequence,
                    state,
                    happened_at.astimezone(UTC).isoformat(),
                    "WS",
                ),
            ).rowcount
            db.execute(
                "INSERT INTO ws_state VALUES(?,?,1,'CURRENT',?) ON CONFLICT(channel) DO UPDATE SET last_sequence=excluded.last_sequence,trustworthy=excluded.trustworthy,reason=excluded.reason,updated_at=excluded.updated_at",
                (channel, sequence, happened_at.astimezone(UTC).isoformat()),
            )
        return changed == 1

    def disconnect(self, channel: str, now: datetime) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO ws_state VALUES(?,NULL,0,'DISCONNECTED',?) ON CONFLICT(channel) DO UPDATE SET trustworthy=0,reason='DISCONNECTED',updated_at=excluded.updated_at",
                (channel, now.astimezone(UTC).isoformat()),
            )

    def add_queue_observation(self, value: QueueObservation) -> None:
        key = stable_hash(value)
        with self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO queue_observations VALUES(?,?,?,?,?,?,?,?)",
                (
                    key,
                    value.observed_at.astimezone(UTC).isoformat(),
                    value.order_id,
                    value.market,
                    str(value.queue_position),
                    str(value.remaining_quantity),
                    str(value.price),
                    value.quality,
                ),
            )

    def record_lineage(self, old: str, new: str, execution_id: str, now: datetime) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO client_order_lineage VALUES(?,?,?,?)",
                (old, new, execution_id, now.astimezone(UTC).isoformat()),
            )

    def recover(self) -> tuple[str, ...]:
        """Never resubmit: every incomplete durable journal becomes reconciliation work."""
        rows = self.incomplete_journals()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for execution_id, _, _, _ in rows:
                db.execute(
                    "UPDATE execution_journals SET state='RECOVERY_RECONCILIATION_REQUIRED' WHERE execution_id=?",
                    (execution_id,),
                )
                db.execute(
                    "UPDATE local_orders SET state=?,reconciliation_required=1 WHERE execution_id=?",
                    (OrderState.UNKNOWN_RECONCILIATION_REQUIRED, execution_id),
                )
        return tuple(str(row[0]) for row in rows)
