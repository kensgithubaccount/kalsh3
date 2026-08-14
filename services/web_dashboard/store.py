"""Transactional M1 configuration, refresh state, sessions, and audit persistence."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ACCOUNT_VALUE_HISTORY_LIMIT = 200


@dataclass(frozen=True, slots=True)
class RefreshState:
    last_attempt: str | None
    last_success: str | None
    status: str
    failure_reason: str | None
    snapshot: dict[str, Any] | None


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        # WAL/NORMAL avoids a full filesystem sync for each deterministic fixture/event insert while
        # retaining atomic SQLite commits. This also keeps read-only UI pagination independent.
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS config (name TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS refresh_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), last_attempt TEXT,
                    last_success TEXT, status TEXT NOT NULL, failure_reason TEXT, snapshot TEXT
                );
                INSERT OR IGNORE INTO refresh_state(singleton,status) VALUES(1,'not_configured');
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY, happened_at TEXT NOT NULL, event TEXT NOT NULL,
                    actor TEXT NOT NULL, detail TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    digest TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS login_attempts (
                    identity TEXT PRIMARY KEY, failures INTEGER NOT NULL, blocked_until INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS universe_markets (
                    ticker TEXT PRIMARY KEY, title TEXT NOT NULL, family TEXT NOT NULL,
                    series_ticker TEXT NOT NULL, status TEXT NOT NULL, closes_at TEXT,
                    provisional INTEGER NOT NULL, mve INTEGER NOT NULL, quality_healthy INTEGER NOT NULL,
                    best_bid TEXT, best_ask TEXT, quote_observed_at TEXT, rules_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS universe_health (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), status TEXT NOT NULL,
                    last_baseline TEXT, last_incremental TEXT, watermark TEXT, historical_cutoff TEXT,
                    series_count INTEGER NOT NULL, event_count INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO universe_health(singleton,status,series_count,event_count)
                    VALUES(1,'NOT_STARTED',0,0);
                CREATE TABLE IF NOT EXISTS realtime_health (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), state TEXT NOT NULL,
                    epoch TEXT, connected_at TEXT, last_frame TEXT, last_application TEXT,
                    last_ticker TEXT, last_trade TEXT, last_lifecycle TEXT,
                    subscription_count INTEGER NOT NULL, depth_watch_count INTEGER NOT NULL,
                    current_books INTEGER NOT NULL, stale_books INTEGER NOT NULL,
                    gap_count INTEGER NOT NULL, unresolved_gaps INTEGER NOT NULL,
                    reconnect_count INTEGER NOT NULL, processing_lag_ms INTEGER NOT NULL,
                    queue_depth INTEGER NOT NULL, archive_state TEXT NOT NULL
                );
                INSERT OR IGNORE INTO realtime_health VALUES(
                    1,'DISCONNECTED',NULL,NULL,NULL,NULL,NULL,NULL,NULL,0,0,0,0,0,0,0,0,0,'NOT_STARTED'
                );
                CREATE TABLE IF NOT EXISTS historical_replay_health (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), coverage TEXT NOT NULL,
                    market_coverage TEXT NOT NULL, trade_coverage TEXT NOT NULL,
                    account_coverage TEXT NOT NULL, archive_health TEXT NOT NULL,
                    dataset_count INTEGER NOT NULL, gap_count INTEGER NOT NULL,
                    availability_quality TEXT NOT NULL, rules_quality TEXT NOT NULL,
                    fee_quality TEXT NOT NULL, last_sync TEXT, last_verified_replay TEXT
                );
                INSERT OR IGNORE INTO historical_replay_health VALUES(
                    1,'PARTIAL','NOT_COLLECTED','NOT_COLLECTED','NOT_COLLECTED','NOT_STARTED',
                    0,0,'UNKNOWN','UNKNOWN','UNKNOWN',NULL,NULL
                );
                CREATE TABLE IF NOT EXISTS llm_evidence_health (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), state TEXT NOT NULL,
                    provider TEXT NOT NULL, model TEXT NOT NULL, prompt_version TEXT NOT NULL,
                    request_count INTEGER NOT NULL, schema_failures INTEGER NOT NULL,
                    citation_failures INTEGER NOT NULL, abstentions INTEGER NOT NULL,
                    input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
                    estimated_monthly_cost TEXT, last_success TEXT, eval_status TEXT NOT NULL
                );
                INSERT OR IGNORE INTO llm_evidence_health VALUES(
                    1,'NOT_CONFIGURED','fixture','fixture-v1','evidence-v1',0,0,0,0,0,0,NULL,NULL,
                    'OFFLINE_NOT_RUN'
                );
                CREATE TABLE IF NOT EXISTS market_evidence_ui (
                    claim_id TEXT PRIMARY KEY, market_ticker TEXT NOT NULL, bundle_time TEXT NOT NULL,
                    claim_text TEXT NOT NULL, source_name TEXT NOT NULL, publication_time TEXT,
                    claim_type TEXT NOT NULL, cited_span TEXT NOT NULL, contract_relation TEXT NOT NULL,
                    correction_state TEXT NOT NULL, contradiction_state TEXT NOT NULL,
                    provider_model TEXT NOT NULL, validation_state TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS forecasting_health (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), state TEXT NOT NULL,
                    settled_forecasts INTEGER NOT NULL, settled_events INTEGER NOT NULL,
                    abstention_rate TEXT NOT NULL, calibration_status TEXT NOT NULL,
                    sample_status TEXT NOT NULL, last_evaluation TEXT, synthetic INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO forecasting_health VALUES(
                    1,'SHADOW',0,0,'UNKNOWN','NOT_FITTED','INSUFFICIENT REAL EVIDENCE',NULL,0
                );
                CREATE TABLE IF NOT EXISTS forecast_research_ui (
                    forecast_id TEXT PRIMARY KEY, market_ticker TEXT NOT NULL,
                    family TEXT NOT NULL, horizon TEXT NOT NULL, forecast_kind TEXT NOT NULL,
                    probability TEXT, lower_probability TEXT, upper_probability TEXT,
                    market_bid TEXT, market_ask TEXT, market_reference TEXT,
                    model_version TEXT NOT NULL, calibration_status TEXT NOT NULL,
                    data_quality TEXT NOT NULL, explanation TEXT NOT NULL,
                    issued_at TEXT NOT NULL, synthetic INTEGER NOT NULL,
                    production_influence TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS learning_health (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), state TEXT NOT NULL,
                    real_settled_events INTEGER NOT NULL, promotion_minimum INTEGER NOT NULL,
                    current_configuration TEXT, previous_configuration TEXT,
                    production_influence TEXT NOT NULL
                );
                INSERT OR IGNORE INTO learning_health VALUES(
                    1,'INSUFFICIENT REAL EVIDENCE',0,50,NULL,NULL,'NONE'
                );
                CREATE TABLE IF NOT EXISTS learning_proposal_ui (
                    proposal_id TEXT PRIMARY KEY, component_type TEXT NOT NULL,
                    component_name TEXT NOT NULL, family TEXT NOT NULL, horizon TEXT NOT NULL,
                    settled_events INTEGER NOT NULL, observed_contribution TEXT,
                    interval TEXT NOT NULL, evidence_state TEXT NOT NULL,
                    current_weight TEXT NOT NULL, proposed_weight TEXT NOT NULL,
                    rationale TEXT NOT NULL, rollback_target TEXT NOT NULL,
                    status TEXT NOT NULL, synthetic INTEGER NOT NULL,
                    production_influence TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS family_learning_ui (
                    family TEXT PRIMARY KEY, usable_markets INTEGER NOT NULL,
                    settled_events INTEGER NOT NULL, skill_state TEXT NOT NULL,
                    data_completeness TEXT NOT NULL, research_cost TEXT NOT NULL,
                    research_priority TEXT NOT NULL, capital_allocation TEXT NOT NULL,
                    synthetic INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS opportunity_health (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), worker_state TEXT NOT NULL,
                    last_cycle TEXT, evaluated INTEGER NOT NULL, rejected INTEGER NOT NULL,
                    watches INTEGER NOT NULL, research_candidates INTEGER NOT NULL,
                    stale_candidates INTEGER NOT NULL, fee_version TEXT NOT NULL,
                    fee_verification TEXT NOT NULL, slippage_version TEXT NOT NULL,
                    fill_quality TEXT NOT NULL, cross_venue_state TEXT NOT NULL
                );
                INSERT OR IGNORE INTO opportunity_health VALUES(
                    1,'NOT_STARTED',NULL,0,0,0,0,0,'UNCONFIGURED','UNVERIFIED',
                    'book-walk-v1','UNVALIDATED','NOT_VERIFIED'
                );
                CREATE TABLE IF NOT EXISTS opportunity_candidate_ui (
                    candidate_id TEXT PRIMARY KEY, market_ticker TEXT NOT NULL,
                    outcome_side TEXT NOT NULL, decision_state TEXT NOT NULL,
                    fair_probability TEXT NOT NULL, lower_probability TEXT NOT NULL,
                    upper_probability TEXT NOT NULL, executable_price TEXT NOT NULL,
                    raw_difference TEXT NOT NULL, expected_fee TEXT NOT NULL,
                    expected_slippage TEXT NOT NULL, conservative_value TEXT NOT NULL,
                    required_threshold TEXT NOT NULL, liquidity TEXT NOT NULL,
                    age TEXT NOT NULL, rejection_reasons TEXT NOT NULL,
                    data_mode TEXT NOT NULL, production_influence TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS execution_research_health (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), runs INTEGER NOT NULL,
                    attempts INTEGER NOT NULL, unique_events INTEGER NOT NULL,
                    real_observations TEXT NOT NULL, production_influence TEXT NOT NULL
                );
                INSERT OR IGNORE INTO execution_research_health VALUES(
                    1,0,0,0,'NOT VERIFIED / NONE','NONE'
                );
                CREATE TABLE IF NOT EXISTS execution_strategy_ui (
                    strategy_id TEXT PRIMARY KEY, strategy TEXT NOT NULL, family TEXT NOT NULL,
                    settled_events INTEGER NOT NULL, attempts INTEGER NOT NULL,
                    fill_rate TEXT NOT NULL, partial_fill_rate TEXT NOT NULL,
                    average_slippage TEXT NOT NULL, average_fee TEXT NOT NULL,
                    adverse_markout TEXT NOT NULL, net_pnl TEXT NOT NULL,
                    return_on_capital TEXT NOT NULL, max_drawdown TEXT NOT NULL,
                    optimistic_result TEXT NOT NULL, base_result TEXT NOT NULL,
                    adverse_result TEXT NOT NULL, advancement_status TEXT NOT NULL,
                    failure_reason TEXT NOT NULL, candidate_economics TEXT NOT NULL,
                    arrival_time TEXT NOT NULL, arrival_book TEXT NOT NULL,
                    hypothetical_order TEXT NOT NULL, queue_assumption TEXT NOT NULL,
                    fill_path TEXT NOT NULL, result_lineage TEXT NOT NULL,
                    data_mode TEXT NOT NULL, production_influence TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS risk_evaluation_ui (
                    evaluation_id TEXT PRIMARY KEY, market_ticker TEXT NOT NULL,
                    intended_maximum_loss TEXT NOT NULL, existing_market_risk TEXT NOT NULL,
                    projected_market_risk TEXT NOT NULL, market_limit TEXT NOT NULL,
                    projected_event_risk TEXT NOT NULL, event_limit TEXT NOT NULL,
                    projected_aggregate_risk TEXT NOT NULL, aggregate_limit TEXT NOT NULL,
                    reserve_state TEXT NOT NULL, result TEXT NOT NULL,
                    reason_codes TEXT NOT NULL, data_mode TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS semantic_specs (
                    market_ticker TEXT PRIMARY KEY, yes_proposition TEXT NOT NULL,
                    no_proposition TEXT NOT NULL, authority TEXT, sources TEXT NOT NULL,
                    measured_value TEXT NOT NULL, threshold TEXT, deadline TEXT, timezone TEXT,
                    revision_rules TEXT, correction_rules TEXT, early_close_rules TEXT,
                    cancellation_rules TEXT, postponement_rules TEXT, payout_model TEXT NOT NULL,
                    semantic_status TEXT NOT NULL, rules_version TEXT NOT NULL,
                    interpretation_version TEXT NOT NULL, issues TEXT NOT NULL,
                    semantic_hash TEXT NOT NULL, parser_versions TEXT NOT NULL,
                    provenance_summary TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS external_source_ui (
                    source_id TEXT PRIMARY KEY, source_name TEXT NOT NULL, state TEXT NOT NULL,
                    source_class TEXT NOT NULL, last_event TEXT, latency_ms INTEGER,
                    uptime TEXT NOT NULL, duplicate_rate TEXT NOT NULL, parse_errors INTEGER NOT NULL,
                    unique_relevant_signals INTEGER NOT NULL, monthly_cost TEXT NOT NULL,
                    setup_requirement TEXT, production_influence TEXT NOT NULL,
                    integration_note TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS account_snapshot_history (
                    observed_at TEXT PRIMARY KEY, cash TEXT NOT NULL, portfolio_value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS breaking_signal_ui (
                    signal_id TEXT PRIMARY KEY, detected_at TEXT NOT NULL, headline TEXT NOT NULL,
                    source_name TEXT NOT NULL, source_class TEXT NOT NULL, age_label TEXT NOT NULL,
                    latency_label TEXT NOT NULL, kalshi_market TEXT, polymarket_relationship TEXT,
                    verification_state TEXT NOT NULL, corroboration TEXT NOT NULL,
                    manipulation_flags TEXT NOT NULL, kalshi_reaction TEXT NOT NULL,
                    polymarket_reaction TEXT NOT NULL, current_action TEXT NOT NULL,
                    production_influence TEXT NOT NULL, source_lineage TEXT NOT NULL,
                    provenance TEXT NOT NULL, duplicate_chain TEXT NOT NULL,
                    correction_state TEXT NOT NULL
                );
            """)
        self.path.chmod(0o600)

    def set_config(self, name: str, value: str) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO config(name,value) VALUES(?,?)", (name, value))

    def config(self, name: str) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT value FROM config WHERE name=?", (name,)).fetchone()
        return None if row is None else str(row["value"])

    def historical_replay_summary(self) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM historical_replay_health WHERE singleton=1").fetchone()
        return dict(row)

    def llm_evidence_summary(self) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM llm_evidence_health WHERE singleton=1").fetchone()
        return dict(row)

    def market_evidence(self, ticker: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM market_evidence_ui WHERE market_ticker=? ORDER BY bundle_time DESC LIMIT ?",
                (ticker, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def seed_market_evidence_fixture(self, values: dict[str, Any]) -> None:
        fields = (
            "claim_id",
            "market_ticker",
            "bundle_time",
            "claim_text",
            "source_name",
            "publication_time",
            "claim_type",
            "cited_span",
            "contract_relation",
            "correction_state",
            "contradiction_state",
            "provider_model",
            "validation_state",
        )
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO market_evidence_ui VALUES("
                + ",".join("?" for _ in fields)
                + ")",
                tuple(values.get(field) for field in fields),
            )

    def forecasting_summary(self) -> dict[str, Any]:
        with self._connect() as db:
            health = db.execute("SELECT * FROM forecasting_health WHERE singleton=1").fetchone()
            rows = db.execute(
                "SELECT * FROM forecast_research_ui ORDER BY issued_at DESC LIMIT 100"
            ).fetchall()
        return dict(health) | {"forecasts": [dict(row) for row in rows]}

    def market_forecasts(self, ticker: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM forecast_research_ui WHERE market_ticker=? ORDER BY issued_at DESC",
                (ticker,),
            ).fetchall()
        return [dict(row) for row in rows]

    def learning_summary(self) -> dict[str, Any]:
        with self._connect() as db:
            health = db.execute("SELECT * FROM learning_health WHERE singleton=1").fetchone()
            proposals = db.execute(
                "SELECT * FROM learning_proposal_ui ORDER BY proposal_id LIMIT 100"
            ).fetchall()
            families = db.execute(
                "SELECT * FROM family_learning_ui ORDER BY family LIMIT 100"
            ).fetchall()
        return dict(health) | {
            "proposals": [dict(row) for row in proposals],
            "families": [dict(row) for row in families],
        }

    def opportunity_summary(self) -> dict[str, Any]:
        with self._connect() as db:
            health = db.execute("SELECT * FROM opportunity_health WHERE singleton=1").fetchone()
            has_receipts = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='decision_receipts'"
            ).fetchone()
            rows = (
                db.execute(
                    "SELECT c.*,r.agent_id attributed_agent_id "
                    "FROM opportunity_candidate_ui c LEFT JOIN decision_receipts r "
                    "ON r.source_kind='trade-candidate' AND r.source_id=c.candidate_id "
                    "ORDER BY c.candidate_id LIMIT 100"
                ).fetchall()
                if has_receipts
                else db.execute(
                    "SELECT c.*,NULL attributed_agent_id FROM opportunity_candidate_ui c "
                    "ORDER BY c.candidate_id LIMIT 100"
                ).fetchall()
            )
        return dict(health) | {"candidates": [dict(row) for row in rows]}

    def execution_research_summary(self) -> dict[str, Any]:
        with self._connect() as db:
            health = db.execute(
                "SELECT * FROM execution_research_health WHERE singleton=1"
            ).fetchone()
            rows = db.execute(
                "SELECT * FROM execution_strategy_ui ORDER BY strategy_id LIMIT 100"
            ).fetchall()
        return dict(health) | {"strategies": [dict(row) for row in rows]}

    def risk_evaluations(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM risk_evaluation_ui ORDER BY evaluation_id LIMIT 100"
            ).fetchall()
        return [dict(row) for row in rows]

    def seed_risk_evaluation_fixture(self, values: dict[str, Any]) -> None:
        fields = (
            "evaluation_id",
            "market_ticker",
            "intended_maximum_loss",
            "existing_market_risk",
            "projected_market_risk",
            "market_limit",
            "projected_event_risk",
            "event_limit",
            "projected_aggregate_risk",
            "aggregate_limit",
            "reserve_state",
            "result",
            "reason_codes",
            "data_mode",
        )
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO risk_evaluation_ui VALUES("
                + ",".join("?" for _ in fields)
                + ")",
                tuple(values.get(field) for field in fields),
            )

    def configured(self) -> bool:
        return all(self.config(name) is not None for name in ("owner", "password_hash", "vault"))

    def refresh_started(self) -> None:
        now = datetime.now(UTC).isoformat()
        with self._connect() as db:
            db.execute(
                "UPDATE refresh_state SET last_attempt=?,status='connecting',failure_reason=NULL WHERE singleton=1",
                (now,),
            )

    def refresh_succeeded(self, snapshot: dict[str, Any]) -> None:
        now = datetime.now(UTC).isoformat()
        with self._connect() as db:
            db.execute(
                "UPDATE refresh_state SET last_attempt=?,last_success=?,status='healthy',failure_reason=NULL,snapshot=? WHERE singleton=1",
                (now, now, json.dumps(snapshot, default=str)),
            )
            self._record_account_value_point(db, snapshot)

    def _record_account_value_point(self, db: sqlite3.Connection, snapshot: dict[str, Any]) -> None:
        """Append a point to the real, read-only account-value history for charting.

        Best-effort: a snapshot missing usable cash/equity values simply is not
        plotted; it never blocks the refresh it was reconciled from.
        """
        observed_at, cash, portfolio_value = (
            snapshot.get("observed_at"),
            snapshot.get("cash"),
            snapshot.get("portfolio_value"),
        )
        if observed_at is None or cash is None or portfolio_value is None:
            return
        db.execute(
            "INSERT OR REPLACE INTO account_snapshot_history VALUES(?,?,?)",
            (str(observed_at), str(cash), str(portfolio_value)),
        )
        db.execute(
            "DELETE FROM account_snapshot_history WHERE observed_at NOT IN ("
            "SELECT observed_at FROM account_snapshot_history "
            "ORDER BY observed_at DESC LIMIT ?)",
            (ACCOUNT_VALUE_HISTORY_LIMIT,),
        )

    def account_value_history(
        self, limit: int = ACCOUNT_VALUE_HISTORY_LIMIT
    ) -> list[dict[str, Any]]:
        """Return the most recent `limit` observations, oldest-to-newest for charting.

        Selects the newest `limit` rows first (ORDER BY ... DESC LIMIT), then
        reverses them into chronological display order — selecting ASC LIMIT
        would instead return the *oldest* observations whenever more history
        exists than `limit`.
        """
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM account_snapshot_history ORDER BY observed_at DESC LIMIT ?",
                (min(max(limit, 1), ACCOUNT_VALUE_HISTORY_LIMIT),),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def refresh_failed(self, reason: str) -> None:
        now = datetime.now(UTC).isoformat()
        with self._connect() as db:
            db.execute(
                "UPDATE refresh_state SET last_attempt=?,status='error',failure_reason=? WHERE singleton=1",
                (now, reason[:240]),
            )

    def refresh_state(self) -> RefreshState:
        with self._connect() as db:
            row = db.execute("SELECT * FROM refresh_state WHERE singleton=1").fetchone()
        assert row is not None
        return RefreshState(
            row["last_attempt"],
            row["last_success"],
            row["status"],
            row["failure_reason"],
            None if row["snapshot"] is None else json.loads(row["snapshot"]),
        )

    def audit(self, event: str, actor: str, detail: str = "") -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO audit(happened_at,event,actor,detail) VALUES(?,?,?,?)",
                (datetime.now(UTC).isoformat(), event, actor, detail[:500]),
            )

    def create_session(self, now: int, ttl: int = 3600) -> tuple[str, str]:
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self._connect() as db:
            db.execute("INSERT INTO sessions VALUES(?,?,?)", (digest, csrf, now + ttl))
        return token, csrf

    def session_csrf(self, token: str, now: int) -> str | None:
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self._connect() as db:
            db.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            row = db.execute("SELECT csrf FROM sessions WHERE digest=?", (digest,)).fetchone()
        return None if row is None else str(row["csrf"])

    def delete_session(self, token: str) -> None:
        with self._connect() as db:
            db.execute(
                "DELETE FROM sessions WHERE digest=?", (hashlib.sha256(token.encode()).hexdigest(),)
            )

    def login_allowed(self, identity: str, now: int) -> bool:
        with self._connect() as db:
            row = db.execute(
                "SELECT blocked_until FROM login_attempts WHERE identity=?", (identity,)
            ).fetchone()
        return row is None or int(row["blocked_until"]) <= now

    def login_failed(self, identity: str, now: int) -> None:
        with self._connect() as db:
            row = db.execute(
                "SELECT failures FROM login_attempts WHERE identity=?", (identity,)
            ).fetchone()
            failures = 1 if row is None else int(row["failures"]) + 1
            blocked = now + min(900, 2 ** min(failures, 9))
            db.execute(
                "INSERT OR REPLACE INTO login_attempts VALUES(?,?,?)", (identity, failures, blocked)
            )

    def login_succeeded(self, identity: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM login_attempts WHERE identity=?", (identity,))

    def universe_summary(self) -> dict[str, Any]:
        with self._connect() as db:
            health = dict(db.execute("SELECT * FROM universe_health WHERE singleton=1").fetchone())
            row = db.execute("""SELECT count(*) AS "indexed",
                sum(status='active') active, sum(provisional) provisional, sum(mve) mve,
                sum(quality_healthy) healthy, count(DISTINCT family) families
                FROM universe_markets""").fetchone()
        return health | {key: int(value or 0) for key, value in dict(row).items()}

    def universe_markets(self, filters: dict[str, str], limit: int = 50) -> list[dict[str, Any]]:
        clauses, values = [], []
        for field in ("family", "series_ticker", "status"):
            if filters.get(field):
                clauses.append(f"{field}=?")
                values.append(filters[field])
        if filters.get("q"):
            clauses.append("(title LIKE ? OR ticker LIKE ?)")
            values.extend([f"%{filters['q']}%", f"%{filters['q']}%"])
        for field in ("provisional", "mve"):
            if filters.get(field) in {"0", "1"}:
                clauses.append(f"{field}=?")
                values.append(filters[field])
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM universe_markets"  # noqa: S608 - fixed allowlisted clauses
                + where
                + " ORDER BY closes_at,ticker LIMIT ?",
                (*values, min(max(limit, 1), 100)),
            ).fetchall()
        return [dict(row) for row in rows]

    def seed_universe_fixture(self, markets: list[dict[str, Any]], health: dict[str, Any]) -> None:
        """Test/dev fixture loader; background ingestion owns production writes."""
        with self._connect() as db:
            db.execute("DELETE FROM universe_markets")
            for market in markets:
                db.execute(
                    "INSERT INTO universe_markets VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    tuple(
                        market[key]
                        for key in (
                            "ticker",
                            "title",
                            "family",
                            "series_ticker",
                            "status",
                            "closes_at",
                            "provisional",
                            "mve",
                            "quality_healthy",
                            "best_bid",
                            "best_ask",
                            "quote_observed_at",
                            "rules_hash",
                        )
                    ),
                )
            db.execute(
                """UPDATE universe_health SET status=?,last_baseline=?,last_incremental=?,watermark=?,
                historical_cutoff=?,series_count=?,event_count=? WHERE singleton=1""",
                tuple(
                    health.get(key)
                    for key in (
                        "status",
                        "last_baseline",
                        "last_incremental",
                        "watermark",
                        "historical_cutoff",
                        "series_count",
                        "event_count",
                    )
                ),
            )

    def realtime_summary(self) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM realtime_health WHERE singleton=1").fetchone()
        if row is None:
            raise RuntimeError("realtime health state missing")
        return dict(row)

    def seed_realtime_fixture(self, values: dict[str, Any]) -> None:
        allowed = {
            "state",
            "epoch",
            "connected_at",
            "last_frame",
            "last_application",
            "last_ticker",
            "last_trade",
            "last_lifecycle",
            "subscription_count",
            "depth_watch_count",
            "current_books",
            "stale_books",
            "gap_count",
            "unresolved_gaps",
            "reconnect_count",
            "processing_lag_ms",
            "queue_depth",
            "archive_state",
        }
        updates = {key: value for key, value in values.items() if key in allowed}
        if not updates:
            return
        assignments = ",".join(f"{key}=?" for key in sorted(updates))
        with self._connect() as db:
            db.execute(
                f"UPDATE realtime_health SET {assignments} WHERE singleton=1",  # noqa: S608
                tuple(updates[key] for key in sorted(updates)),
            )

    def semantic_summary(self) -> dict[str, int]:
        with self._connect() as db:
            row = db.execute("""SELECT count(*) total,
                sum(semantic_status='VALID') valid,
                sum(semantic_status='AMBIGUOUS') ambiguous,
                sum(payout_model!='SIMPLE_BINARY') unsupported_payout,
                sum(sources='[]') missing_source,
                sum(semantic_status IN ('STALE','INVALIDATED')) revalidation
                FROM semantic_specs""").fetchone()
        if row is None:
            raise RuntimeError("semantic summary failed")
        return {key: int(value or 0) for key, value in dict(row).items()}

    def semantic_spec(self, ticker: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM semantic_specs WHERE market_ticker=?", (ticker,)
            ).fetchone()
        return None if row is None else dict(row)

    def seed_semantic_fixture(self, values: dict[str, Any]) -> None:
        fields = (
            "market_ticker",
            "yes_proposition",
            "no_proposition",
            "authority",
            "sources",
            "measured_value",
            "threshold",
            "deadline",
            "timezone",
            "revision_rules",
            "correction_rules",
            "early_close_rules",
            "cancellation_rules",
            "postponement_rules",
            "payout_model",
            "semantic_status",
            "rules_version",
            "interpretation_version",
            "issues",
            "semantic_hash",
            "parser_versions",
            "provenance_summary",
        )
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO semantic_specs VALUES("
                + ",".join("?" for _ in fields)
                + ")",
                tuple(values.get(field) for field in fields),
            )

    def external_intelligence_summary(self) -> dict[str, int]:
        with self._connect() as db:
            signals = db.execute("""SELECT count(*) total,
                sum(verification_state='PRIMARY_CONFIRMED') primary_confirmations,
                sum(verification_state='UNVERIFIED') unverified,
                sum(polymarket_relationship!='None') cross_venue FROM breaking_signal_ui""").fetchone()
            failures = db.execute(
                "SELECT count(*) count FROM external_source_ui WHERE state IN ('DEGRADED','OFFLINE','QUARANTINED')"
            ).fetchone()
        if signals is None or failures is None:
            raise RuntimeError("external intelligence summary failed")
        return {key: int(value or 0) for key, value in dict(signals).items()} | {
            "connector_failures": int(failures["count"])
        }

    def breaking_signals(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM breaking_signal_ui ORDER BY detected_at DESC LIMIT ?",
                (min(max(limit, 1), 100),),
            ).fetchall()
        return [dict(row) for row in rows]

    def breaking_signal(self, signal_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM breaking_signal_ui WHERE signal_id=?", (signal_id,)
            ).fetchone()
        return None if row is None else dict(row)

    def external_sources(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM external_source_ui ORDER BY source_name LIMIT 100"
            ).fetchall()
        return [dict(row) for row in rows]

    def seed_external_source_fixture(self, values: dict[str, Any]) -> None:
        fields = (
            "source_id",
            "source_name",
            "state",
            "source_class",
            "last_event",
            "latency_ms",
            "uptime",
            "duplicate_rate",
            "parse_errors",
            "unique_relevant_signals",
            "monthly_cost",
            "setup_requirement",
            "production_influence",
            "integration_note",
        )
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO external_source_ui VALUES("
                + ",".join("?" for _ in fields)
                + ")",
                tuple(values.get(field) for field in fields),
            )

    def seed_breaking_signal_fixture(self, values: dict[str, Any]) -> None:
        fields = (
            "signal_id",
            "detected_at",
            "headline",
            "source_name",
            "source_class",
            "age_label",
            "latency_label",
            "kalshi_market",
            "polymarket_relationship",
            "verification_state",
            "corroboration",
            "manipulation_flags",
            "kalshi_reaction",
            "polymarket_reaction",
            "current_action",
            "production_influence",
            "source_lineage",
            "provenance",
            "duplicate_chain",
            "correction_state",
        )
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO breaking_signal_ui VALUES("
                + ",".join("?" for _ in fields)
                + ")",
                tuple(values.get(field) for field in fields),
            )
