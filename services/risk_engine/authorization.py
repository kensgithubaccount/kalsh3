"""Durable short-lived one-use internal risk authorizations and reservations."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from .domain import ComplianceState, RiskDecision, RiskDecisionState, RiskIntent, content_hash
from .policy import RiskPolicy
from .states import KillState, LossWindowState


class Clock(Protocol):
    def now(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(slots=True)
class FixedClock:
    value: datetime

    def now(self) -> datetime:
        return self.value

    def advance(self, duration: timedelta) -> None:
        self.value += duration


class AuthorizationState(StrEnum):
    ISSUED = "ISSUED"
    CONSUMED = "CONSUMED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


@dataclass(frozen=True, slots=True)
class RiskAuthorization:
    authorization_id: str
    risk_decision_id: str
    intent_hash: str
    portfolio_state_hash: str
    policy_version: str
    rules_version: str
    safety_state_hash: str
    created_at: datetime
    expires_at: datetime
    state: AuthorizationState
    production_execution_authorized: bool = False


class AuthorizationError(ValueError):
    pass


class AuthorizationStore:
    """SQLite transactions serialize capacity reservation and one-time consumption."""

    def __init__(self, path: Path, clock: Clock) -> None:
        self.path = path
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS risk_authorizations (
                    authorization_id TEXT PRIMARY KEY, risk_decision_id TEXT NOT NULL,
                    intent_hash TEXT NOT NULL, client_order_id TEXT NOT NULL UNIQUE,
                    market_ticker TEXT NOT NULL, event_id TEXT NOT NULL,
                    portfolio_state_hash TEXT NOT NULL, policy_version TEXT NOT NULL,
                    rules_version TEXT NOT NULL, safety_state_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('ISSUED','CONSUMED','EXPIRED','REVOKED')),
                    production_execution_authorized INTEGER NOT NULL DEFAULT 0
                        CHECK(production_execution_authorized=0)
                );
                CREATE TABLE IF NOT EXISTS risk_reservations (
                    authorization_id TEXT PRIMARY KEY REFERENCES risk_authorizations(authorization_id),
                    market_ticker TEXT NOT NULL, event_id TEXT NOT NULL,
                    market_risk TEXT NOT NULL, event_risk TEXT NOT NULL,
                    aggregate_risk TEXT NOT NULL, cash_commitment TEXT NOT NULL,
                    expires_at TEXT NOT NULL, active INTEGER NOT NULL CHECK(active IN (0,1))
                );
                CREATE TABLE IF NOT EXISTS global_halt_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), active INTEGER NOT NULL,
                    reason TEXT, changed_at TEXT NOT NULL, actor TEXT NOT NULL
                );
                INSERT OR IGNORE INTO global_halt_state VALUES(1,0,NULL,'1970-01-01T00:00:00+00:00','SYSTEM');
                CREATE TABLE IF NOT EXISTS compliance_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), state TEXT NOT NULL,
                    reason TEXT, changed_at TEXT NOT NULL, actor TEXT NOT NULL
                );
                INSERT OR IGNORE INTO compliance_state VALUES(1,'UNKNOWN','not established','1970-01-01T00:00:00+00:00','SYSTEM');
                CREATE TABLE IF NOT EXISTS risk_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL,
                    actor TEXT NOT NULL, happened_at TEXT NOT NULL, reason TEXT NOT NULL,
                    policy_version TEXT, state_hash TEXT
                );
                CREATE TABLE IF NOT EXISTS durable_kill_states (
                    category TEXT PRIMARY KEY, level TEXT NOT NULL, reason TEXT NOT NULL,
                    changed_at TEXT NOT NULL
                );
                INSERT OR IGNORE INTO durable_kill_states VALUES('STRATEGY','NORMAL','initial','1970-01-01T00:00:00+00:00');
                INSERT OR IGNORE INTO durable_kill_states VALUES('DATA','NORMAL','initial','1970-01-01T00:00:00+00:00');
                INSERT OR IGNORE INTO durable_kill_states VALUES('PORTFOLIO','NORMAL','initial','1970-01-01T00:00:00+00:00');
                INSERT OR IGNORE INTO durable_kill_states VALUES('CREDENTIAL','NORMAL','initial','1970-01-01T00:00:00+00:00');
                CREATE TABLE IF NOT EXISTS durable_loss_holds (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    weekly_review_required INTEGER NOT NULL,
                    monthly_review_required INTEGER NOT NULL,
                    experiment_halt_required INTEGER NOT NULL,
                    state_version TEXT NOT NULL, changed_at TEXT NOT NULL
                );
                INSERT OR IGNORE INTO durable_loss_holds VALUES(1,0,0,0,'initial','1970-01-01T00:00:00+00:00');
            """)
        self.path.chmod(0o600)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def _utc_now(self) -> datetime:
        value = self.clock.now()
        if value.tzinfo is None:
            raise AuthorizationError("clock must be timezone-aware")
        return value.astimezone(UTC)

    def _expire(self, db: sqlite3.Connection, now: datetime) -> None:
        stamp = now.isoformat()
        expired = db.execute(
            "SELECT authorization_id FROM risk_authorizations WHERE state='ISSUED' AND expires_at<=?",
            (stamp,),
        ).fetchall()
        for (authorization_id,) in expired:
            db.execute(
                "UPDATE risk_authorizations SET state='EXPIRED' WHERE authorization_id=? AND state='ISSUED'",
                (authorization_id,),
            )
            db.execute(
                "UPDATE risk_reservations SET active=0 WHERE authorization_id=?",
                (authorization_id,),
            )
            self._event(
                db, "RISK_AUTHORIZATION_EXPIRED", "SYSTEM", str(authorization_id), None, None
            )

    def _event(
        self,
        db: sqlite3.Connection,
        event_type: str,
        actor: str,
        reason: str,
        policy_version: str | None,
        state_hash: str | None,
    ) -> None:
        db.execute(
            "INSERT INTO risk_events(event_type,actor,happened_at,reason,policy_version,state_hash) VALUES(?,?,?,?,?,?)",
            (event_type, actor, self._utc_now().isoformat(), reason, policy_version, state_hash),
        )

    def set_compliance(self, state: ComplianceState, *, actor: str, reason: str) -> None:
        now = self._utc_now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE compliance_state SET state=?,reason=?,changed_at=?,actor=? WHERE singleton=1",
                (state, reason, now.isoformat(), actor),
            )
            self._event(
                db,
                "COMPLIANCE_HOLD_ACTIVATED"
                if state != ComplianceState.CLEAR
                else "COMPLIANCE_CLEARED",
                actor,
                reason,
                None,
                None,
            )

    def activate_global_halt(self, *, actor: str, reason: str, authenticated: bool) -> None:
        if not authenticated or not reason.strip():
            raise AuthorizationError("authenticated actor and explicit reason required")
        now = self._utc_now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE global_halt_state SET active=1,reason=?,changed_at=?,actor=? WHERE singleton=1",
                (reason, now.isoformat(), actor),
            )
            db.execute("UPDATE risk_authorizations SET state='REVOKED' WHERE state='ISSUED'")
            db.execute("UPDATE risk_reservations SET active=0 WHERE active=1")
            self._event(db, "GLOBAL_HALT_ACTIVATED", actor, reason, None, None)

    def reset_global_halt(self, *, actor: str, reason: str, strong_reauthenticated: bool) -> None:
        if not strong_reauthenticated or not reason.strip():
            raise AuthorizationError("strong reauthentication and explicit reset reason required")
        now = self._utc_now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE global_halt_state SET active=0,reason=?,changed_at=?,actor=? WHERE singleton=1",
                (reason, now.isoformat(), actor),
            )
            self._event(db, "GLOBAL_HALT_RESET", actor, reason, None, None)

    def set_kill_state(self, state: KillState, *, actor: str) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE durable_kill_states SET level=?,reason=?,changed_at=? WHERE category=?",
                (
                    state.level,
                    state.reason,
                    state.changed_at.astimezone(UTC).isoformat(),
                    state.category,
                ),
            )
            self._event(
                db,
                "KILL_STATE_CHANGED",
                actor,
                f"{state.category}:{state.level}:{state.reason}",
                None,
                None,
            )

    def record_loss_state(self, state: LossWindowState, *, actor: str = "RISK_ENGINE") -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT weekly_review_required,monthly_review_required,experiment_halt_required FROM durable_loss_holds WHERE singleton=1"
            ).fetchone()
            if prior is None:
                raise AuthorizationError("durable loss state unavailable")
            values = (
                int(bool(prior[0]) or state.weekly_review_required),
                int(bool(prior[1]) or state.monthly_review_required),
                int(bool(prior[2]) or state.experiment_halt_required),
            )
            db.execute(
                "UPDATE durable_loss_holds SET weekly_review_required=?,monthly_review_required=?,experiment_halt_required=?,state_version=?,changed_at=? WHERE singleton=1",
                (*values, state.version, self._utc_now().isoformat()),
            )
            if any(values):
                self._event(db, "LOSS_HOLD_PERSISTED", actor, state.version, None, None)

    def safety_summary(self) -> dict[str, object]:
        now = self._utc_now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db, now)
            halt = db.execute(
                "SELECT active,reason,changed_at FROM global_halt_state WHERE singleton=1"
            ).fetchone()
            compliance = db.execute(
                "SELECT state,reason,changed_at FROM compliance_state WHERE singleton=1"
            ).fetchone()
            active_reservations = db.execute(
                "SELECT aggregate_risk FROM risk_reservations WHERE active=1"
            ).fetchall()
            recent = db.execute(
                "SELECT event_type,reason,happened_at FROM risk_events ORDER BY event_id DESC LIMIT 10"
            ).fetchall()
            kills = db.execute(
                "SELECT category,level,reason,changed_at FROM durable_kill_states ORDER BY category"
            ).fetchall()
            loss_holds = db.execute(
                "SELECT weekly_review_required,monthly_review_required,experiment_halt_required,state_version FROM durable_loss_holds WHERE singleton=1"
            ).fetchone()
        return {
            "global_halt": bool(halt and halt[0]),
            "global_halt_reason": None if halt is None else halt[1],
            "global_halt_changed_at": None if halt is None else halt[2],
            "compliance_state": "UNKNOWN" if compliance is None else compliance[0],
            "compliance_reason": None if compliance is None else compliance[1],
            "active_reservations": len(active_reservations),
            "reserved_aggregate_risk": str(
                sum((Decimal(row[0]) for row in active_reservations), Decimal(0))
            ),
            "recent_events": tuple(recent),
            "kill_states": tuple(kills),
            "weekly_review_required": bool(loss_holds and loss_holds[0]),
            "monthly_review_required": bool(loss_holds and loss_holds[1]),
            "experiment_halt_required": bool(loss_holds and loss_holds[2]),
            "loss_state_version": None if loss_holds is None else loss_holds[3],
        }

    def issue(
        self,
        *,
        decision: RiskDecision,
        intent: RiskIntent,
        policy: RiskPolicy,
        safety_state_hash: str,
        base_market_risk: Decimal,
        base_event_risk: Decimal,
        base_aggregate_risk: Decimal,
        requested_market_risk: Decimal,
        requested_event_risk: Decimal,
        requested_aggregate_risk: Decimal,
        cash_commitment: Decimal,
    ) -> RiskAuthorization:
        now = self._utc_now()
        if decision.state != RiskDecisionState.PASS_NEXT_GATE or decision.reasons:
            raise AuthorizationError("only a clean PASS_NEXT_GATE decision can issue authorization")
        if decision.intent_hash != intent.content_hash or decision.expires_at <= now:
            raise AuthorizationError("decision expired or intent changed")
        expires = min(decision.expires_at, now + timedelta(seconds=5))
        authorization_id = content_hash(
            (decision.decision_id, intent.content_hash, safety_state_hash, now.isoformat())
        )
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                self._expire(db, now)
                halt = db.execute(
                    "SELECT active FROM global_halt_state WHERE singleton=1"
                ).fetchone()
                compliance = db.execute(
                    "SELECT state FROM compliance_state WHERE singleton=1"
                ).fetchone()
                if halt is None or int(halt[0]) or compliance is None or compliance[0] != "CLEAR":
                    raise AuthorizationError("halt or compliance state blocks authorization")
                rows = db.execute(
                    "SELECT market_ticker,event_id,market_risk,event_risk,aggregate_risk FROM risk_reservations WHERE active=1"
                ).fetchall()
                reserved_market = sum(
                    (Decimal(row[2]) for row in rows if row[0] == intent.market_ticker), Decimal(0)
                )
                reserved_event = sum(
                    (Decimal(row[3]) for row in rows if row[1] == intent.event_id), Decimal(0)
                )
                reserved_aggregate = sum((Decimal(row[4]) for row in rows), Decimal(0))
                if (
                    base_market_risk + reserved_market + requested_market_risk
                    > policy.market_loss_limit
                ):
                    raise AuthorizationError("market reservation would oversubscribe hard cap")
                if (
                    base_event_risk + reserved_event + requested_event_risk
                    > policy.related_event_risk_limit
                ):
                    raise AuthorizationError("event reservation would oversubscribe hard cap")
                if (
                    base_aggregate_risk + reserved_aggregate + requested_aggregate_risk
                    > policy.aggregate_open_risk_limit
                ):
                    raise AuthorizationError("aggregate reservation would oversubscribe hard cap")
                db.execute(
                    "INSERT INTO risk_authorizations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
                    (
                        authorization_id,
                        decision.decision_id,
                        intent.content_hash,
                        intent.client_order_id,
                        intent.market_ticker,
                        intent.event_id,
                        decision.portfolio_state_hash,
                        policy.version,
                        intent.rules_version,
                        safety_state_hash,
                        now.isoformat(),
                        expires.isoformat(),
                        AuthorizationState.ISSUED,
                    ),
                )
                db.execute(
                    "INSERT INTO risk_reservations VALUES(?,?,?,?,?,?,?,?,1)",
                    (
                        authorization_id,
                        intent.market_ticker,
                        intent.event_id,
                        str(requested_market_risk),
                        str(requested_event_risk),
                        str(requested_aggregate_risk),
                        str(cash_commitment),
                        expires.isoformat(),
                    ),
                )
                self._event(
                    db,
                    "RISK_AUTHORIZATION_ISSUED",
                    "RISK_ENGINE",
                    decision.decision_id,
                    policy.version,
                    safety_state_hash,
                )
        except sqlite3.IntegrityError as exc:
            raise AuthorizationError("duplicate client_order_id or authorization") from exc
        return RiskAuthorization(
            authorization_id,
            decision.decision_id,
            intent.content_hash,
            decision.portfolio_state_hash,
            policy.version,
            intent.rules_version,
            safety_state_hash,
            now,
            expires,
            AuthorizationState.ISSUED,
        )

    def consume(
        self,
        authorization_id: str,
        *,
        intent_hash: str,
        portfolio_state_hash: str,
        safety_state_hash: str,
    ) -> bool:
        now = self._utc_now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db, now)
            row = db.execute(
                "SELECT intent_hash,portfolio_state_hash,safety_state_hash,state,expires_at FROM risk_authorizations WHERE authorization_id=?",
                (authorization_id,),
            ).fetchone()
            halt = db.execute("SELECT active FROM global_halt_state WHERE singleton=1").fetchone()
            compliance = db.execute(
                "SELECT state FROM compliance_state WHERE singleton=1"
            ).fetchone()
            valid = bool(
                row
                and row[3] == AuthorizationState.ISSUED
                and datetime.fromisoformat(str(row[4])) > now
                and row[0] == intent_hash
                and row[1] == portfolio_state_hash
                and row[2] == safety_state_hash
                and halt
                and not int(halt[0])
                and compliance
                and compliance[0] == ComplianceState.CLEAR
            )
            if not valid:
                return False
            changed = db.execute(
                "UPDATE risk_authorizations SET state='CONSUMED' WHERE authorization_id=? AND state='ISSUED'",
                (authorization_id,),
            ).rowcount
            if changed != 1:
                return False
            db.execute(
                "UPDATE risk_reservations SET active=0 WHERE authorization_id=?",
                (authorization_id,),
            )
            self._event(
                db,
                "RISK_AUTHORIZATION_CONSUMED",
                "FAKE_INTERNAL_CONSUMER",
                authorization_id,
                None,
                safety_state_hash,
            )
            return True
