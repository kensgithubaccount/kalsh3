"""WN-A1 durable, append-only, fixed-location research-attempt journal.

Every WN-A1 evaluation attempt -- TAKE A LOOK, SKIP, TOO UNCERTAIN, or DATA NOT READY -- is
persisted here, never only the ones a human might act on, to avoid selection bias in later
scoring. Unlike ``EvaluationRecord`` (which binds only identities, in-memory), a persisted
``WnA1Attempt`` carries the actual WeatherNext member evidence, the exact selected grid
coordinate, both side economics, and enough of the bound contract to let a genuinely fresh
process reopen the row from disk and reproduce the decision -- see ``replay_decision``,
which recomputes the raw probability from the persisted member rows and re-runs
``decide_alert``, rather than merely recomputing a hash from an in-memory dataclass.

The canonical location is derived from this installed module, not from caller-controlled
environment, HOME, cwd, or a function argument (matching
``services.production_gdp_strategy.gdp_persistence``'s research-storage pattern).
``open_isolated_attempt_store`` is an explicit escape hatch for tests and tools only; the
live ``wn_a1_runner.run_canonical`` entrypoint never accepts a caller-selected root.

Duplicate persistence of the same exact attempt (same ``attempt_id``, WN-A1's
content-derived identity -- see ``EvaluationRecord.record_id``) fails closed: ``append``
always raises rather than silently allowing a second write, matching or not.

Every ``WnA1Attempt`` binds to its parent ``run_id``. Each canonical invocation is itself
durably journaled in two append-only phases (``register_run_start`` / ``append_run_terminal``):
a ``WnA1RunStart`` is persisted BEFORE any Kalshi discovery, WeatherNext acquisition, or
market/orderbook acquisition -- so a genuinely duplicate run identity is rejected before any
external getter/reader is ever called, and so a process that dies mid-run still leaves this
row durably visible. The matching ``WnA1RunTerminal`` (``COMPLETED``/``FAILED``) is persisted
once the invocation actually finishes; a run with a start but no terminal is visibly
STARTED/interrupted -- see ``WnA1AttemptStore.run_status``.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from .wn_a1_alert import DEFAULT_POLICY, AlertDecision, decide_alert
from .wn_a1_current_daily_high_authority import (
    LOCATION,
    MEASUREMENT,
    SETTLEMENT_SOURCE,
    SETTLEMENT_SOURCE_URL,
    STATION_ID,
    TIMEZONE,
    CurrentDailyHighContract,
    WindowStatus,
)
from .wn_a1_domain import PRODUCTION_INFLUENCE, RESEARCH_ONLY, WnA1Error
from .wn_a1_market_economics import is_fresh_at
from .wn_a1_probability import (
    BoundaryRiskDiagnostic,
    MemberDailyHigh,
    RawProbabilityResult,
    compute_member_daily_highs,
    compute_raw_probability,
)
from .wn_a1_weathernext_evidence import (
    UNIT,
    VARIABLE,
    EnsembleStatus,
    WeatherNextEnsembleEvidence,
    build_ensemble_evidence,
)

SCHEMA_VERSION = "wn-a1-attempt-store-v2-run-bound"
RUN_SCHEMA_VERSION = "wn-a1-run-journal-v2-two-phase"

# Canonical location derived from this installed module -- never caller-controlled.
_DEFAULT_ROOT = Path(__file__).resolve().parents[2] / ".kalsh3-wn-a1-research"
_DEFAULT_DB_PATH = _DEFAULT_ROOT / "attempts.sqlite3"

_MemberRow = tuple[int, int, int, str, str]  # sample, lead_h, lead_m, valid_time_iso, kelvin


class AttemptStoreError(WnA1Error):
    """Durable WN-A1 attempt persistence is invalid, conflicting, or unavailable."""


@dataclass(frozen=True, slots=True)
class WnA1Attempt:
    attempt_id: str
    # Item F3: every market-level attempt binds to the pre-registered run that produced it
    # (see ``WnA1RunStart``/``WnA1RunTerminal``) -- never an orphaned market row.
    run_id: str
    target_local_date: str
    market_ticker: str
    event_ticker: str
    series_ticker: str
    contract_policy_identity: str
    contract_policy_version: str
    contract_comparator: str
    contract_lower: Decimal
    contract_upper: Decimal | None
    research_window_start: datetime | None
    research_window_end: datetime | None
    window_status: str
    weathernext_evidence_identity: str | None
    weathernext_model: str | None
    weathernext_source_object: str | None
    weathernext_init_time: datetime | None
    weathernext_acquired_at: datetime | None
    weathernext_requested_latitude: Decimal | None
    weathernext_requested_longitude: Decimal | None
    weathernext_selected_latitude: Decimal | None
    weathernext_selected_longitude: Decimal | None
    weathernext_source_content_hash: str | None
    weathernext_members: tuple[_MemberRow, ...]
    kalshi_snapshot_identity: str | None
    # The exact market-freshness input used by the original decision (Item A): replay must
    # recompute freshness from this persisted timestamp using the same reviewed
    # ``is_fresh_at`` function, never hardcode ``market_fresh=True``. ``None`` when no market
    # snapshot was acquired at all -- that unavailable state is preserved on replay too.
    kalshi_orderbook_observed_at: datetime | None
    alert_policy_version: str
    decision_state: str
    decision_gate_failures: tuple[str, ...]
    side: str | None
    model_probability_yes: Decimal | None
    yes_conservative_debit: Decimal | None
    no_conservative_debit: Decimal | None
    yes_gap_pp: Decimal | None
    yes_buffered_gap_pp: Decimal | None
    no_gap_pp: Decimal | None
    no_buffered_gap_pp: Decimal | None
    yes_fee_quality: str | None
    no_fee_quality: str | None
    boundary_members_within_one_f: int | None
    boundary_baseline_ticker: str | None
    boundary_plus_one_ticker: str | None
    boundary_minus_one_ticker: str | None
    evaluated_at: datetime
    research_only: bool = RESEARCH_ONLY
    production_influence: Decimal = PRODUCTION_INFLUENCE

    def __post_init__(self) -> None:
        if self.research_only is not True or self.production_influence != 0:
            raise AttemptStoreError("WN-A1 attempt must remain research-only, zero influence")


_RouteOutcome = tuple[str, str, str | None]  # (market_ticker, route_state, reason)


@dataclass(frozen=True, slots=True)
class WnA1RunStart:
    """Item F1's immutable run registration record.

    Persisted BEFORE Kalshi discovery, WeatherNext acquisition, or any market/orderbook
    acquisition -- so duplicate-exact-run-identity rejection (``register_run_start``)
    happens before any external getter/reader is ever called, and so a process that dies
    mid-run still leaves this row durably visible (see ``WnA1RunTerminal`` and
    ``WnA1AttemptStore.run_status``).
    """

    run_id: str
    target_local_date: str
    event_ticker: str
    weathernext_source_object: str
    weathernext_init_time: datetime
    weathernext_requested_latitude: Decimal
    weathernext_requested_longitude: Decimal
    alert_policy_version: str
    pipeline_started_at: datetime
    research_only: bool = RESEARCH_ONLY
    production_influence: Decimal = PRODUCTION_INFLUENCE

    def __post_init__(self) -> None:
        if self.research_only is not True or self.production_influence != 0:
            raise AttemptStoreError("WN-A1 run start must remain research-only, zero influence")


@dataclass(frozen=True, slots=True)
class WnA1RunTerminal:
    """Item F4's immutable terminal record: the second half of the append-only two-phase
    run journal (START -> zero or more linked ``WnA1Attempt`` rows -> this terminal record).

    A run whose ``run_id`` has a ``WnA1RunStart`` but no matching ``WnA1RunTerminal`` is
    durably visible as STARTED/interrupted -- see ``WnA1AttemptStore.run_status``. This
    terminal record is self-describing (it repeats the WeatherNext run identity and start
    timestamp rather than requiring a join) and is denominator/preservation evidence only,
    never a trading authority: it never fabricates a market-level contract for a route that
    was never supported.
    """

    run_id: str
    status: str  # "COMPLETED" | "FAILED"
    pipeline_started_at: datetime
    terminal_at: datetime
    target_local_date: str
    event_ticker: str
    weathernext_source_object: str
    weathernext_init_time: datetime
    weathernext_requested_latitude: Decimal
    weathernext_requested_longitude: Decimal
    discovery_state: str  # "OK" | "DISCOVERY_FAILED"
    discovery_reason: str | None
    weathernext_state: str  # "COMPLETE" | "INCOMPLETE" | "ACQUISITION_FAILED"
    weathernext_reason: str | None
    weathernext_acquired_at: datetime | None
    route_outcomes: tuple[_RouteOutcome, ...]
    attempt_ids: tuple[str, ...]
    alert_policy_version: str
    failure_reason: str | None
    research_only: bool = RESEARCH_ONLY
    production_influence: Decimal = PRODUCTION_INFLUENCE

    def __post_init__(self) -> None:
        if self.research_only is not True or self.production_influence != 0:
            raise AttemptStoreError("WN-A1 run terminal must remain research-only, zero influence")
        if self.status not in ("COMPLETED", "FAILED"):
            raise AttemptStoreError(f"unrecognized WN-A1 run terminal status: {self.status!r}")


def weathernext_member_rows(evidence: WeatherNextEnsembleEvidence) -> tuple[_MemberRow, ...]:
    return tuple(
        (
            m.sample,
            m.lead_time_hours,
            m.lead_subtime_minutes,
            m.valid_time.isoformat(),
            str(m.value_kelvin),
        )
        for m in evidence.members
    )


class WnA1AttemptStore:
    """Append-only SQLite journal; UPDATE/DELETE are rejected at the database level."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            result = db.execute("PRAGMA quick_check").fetchone()
            if result is None or result[0] != "ok":
                raise AttemptStoreError("WN-A1 attempt database integrity check failed")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS wn_a1_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    market_ticker TEXT NOT NULL,
                    target_local_date TEXT NOT NULL,
                    decision_state TEXT NOT NULL,
                    evaluated_at TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    research_only TEXT NOT NULL DEFAULT '1' CHECK(research_only = '1'),
                    production_influence TEXT NOT NULL DEFAULT '0'
                        CHECK(production_influence = '0')
                );
                CREATE INDEX IF NOT EXISTS wn_a1_attempts_run_id ON wn_a1_attempts(run_id);
                CREATE TRIGGER IF NOT EXISTS wn_a1_attempts_no_update
                BEFORE UPDATE ON wn_a1_attempts BEGIN SELECT RAISE(ABORT, 'append only'); END;
                CREATE TRIGGER IF NOT EXISTS wn_a1_attempts_no_delete
                BEFORE DELETE ON wn_a1_attempts BEGIN SELECT RAISE(ABORT, 'append only'); END;
                CREATE TABLE IF NOT EXISTS wn_a1_run_starts (
                    run_id TEXT PRIMARY KEY,
                    target_local_date TEXT NOT NULL,
                    event_ticker TEXT NOT NULL,
                    pipeline_started_at TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    research_only TEXT NOT NULL DEFAULT '1' CHECK(research_only = '1'),
                    production_influence TEXT NOT NULL DEFAULT '0'
                        CHECK(production_influence = '0')
                );
                CREATE TRIGGER IF NOT EXISTS wn_a1_run_starts_no_update
                BEFORE UPDATE ON wn_a1_run_starts BEGIN SELECT RAISE(ABORT, 'append only'); END;
                CREATE TRIGGER IF NOT EXISTS wn_a1_run_starts_no_delete
                BEFORE DELETE ON wn_a1_run_starts BEGIN SELECT RAISE(ABORT, 'append only'); END;
                CREATE TABLE IF NOT EXISTS wn_a1_run_terminals (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    terminal_at TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    research_only TEXT NOT NULL DEFAULT '1' CHECK(research_only = '1'),
                    production_influence TEXT NOT NULL DEFAULT '0'
                        CHECK(production_influence = '0')
                );
                CREATE TRIGGER IF NOT EXISTS wn_a1_run_terminals_no_update
                BEFORE UPDATE ON wn_a1_run_terminals BEGIN SELECT RAISE(ABORT, 'append only'); END;
                CREATE TRIGGER IF NOT EXISTS wn_a1_run_terminals_no_delete
                BEFORE DELETE ON wn_a1_run_terminals BEGIN SELECT RAISE(ABORT, 'append only'); END;
                """
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA busy_timeout=30000")
        return db

    def append(self, attempt: WnA1Attempt) -> None:
        """Persist one attempt. Fails closed on any duplicate ``attempt_id``, matching
        content or not -- a caller that wants to re-persist must first construct a new,
        genuinely distinct attempt (e.g. a fresh ``evaluated_at``)."""
        if not isinstance(attempt, WnA1Attempt):
            raise AttemptStoreError("only WnA1Attempt may be persisted")
        payload = json.dumps(_encode(attempt), sort_keys=True, separators=(",", ":"))
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                existing = db.execute(
                    "SELECT 1 FROM wn_a1_attempts WHERE attempt_id=?", (attempt.attempt_id,)
                ).fetchone()
                if existing is not None:
                    raise AttemptStoreError(
                        f"duplicate WN-A1 attempt persistence rejected: {attempt.attempt_id}"
                    )
                db.execute(
                    "INSERT INTO wn_a1_attempts VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        attempt.attempt_id,
                        attempt.run_id,
                        attempt.market_ticker,
                        attempt.target_local_date,
                        attempt.decision_state,
                        _timestamp(attempt.evaluated_at),
                        SCHEMA_VERSION,
                        payload,
                        "1",
                        "0",
                    ),
                )
        except sqlite3.Error as exc:
            raise AttemptStoreError("WN-A1 attempt persistence rejected") from exc

    def get(self, attempt_id: str) -> WnA1Attempt:
        with self._connect() as db:
            row = db.execute(
                "SELECT payload FROM wn_a1_attempts WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
        if row is None:
            raise AttemptStoreError(f"unknown WN-A1 attempt: {attempt_id}")
        return _decode(json.loads(row["payload"]))

    def attempts_for_date(self, target_local_date: date) -> tuple[WnA1Attempt, ...]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT payload FROM wn_a1_attempts WHERE target_local_date=? "
                "ORDER BY evaluated_at, market_ticker",
                (target_local_date.isoformat(),),
            ).fetchall()
        return tuple(_decode(json.loads(row["payload"])) for row in rows)

    def attempts_for_run(self, run_id: str) -> tuple[WnA1Attempt, ...]:
        """Every market-level attempt bound to one run (Item F3), even one whose run is
        still STARTED/interrupted (no terminal record yet) -- see ``run_status``."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT payload FROM wn_a1_attempts WHERE run_id=? "
                "ORDER BY evaluated_at, market_ticker",
                (run_id,),
            ).fetchall()
        return tuple(_decode(json.loads(row["payload"])) for row in rows)

    def count(self) -> int:
        with self._connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM wn_a1_attempts").fetchone()[0])

    def register_run_start(self, start: WnA1RunStart) -> None:
        """Item F1/F2: persist the immutable run-registration record. Callers MUST call
        this before any Kalshi discovery, WeatherNext acquisition, or market/orderbook
        acquisition -- fails closed on any duplicate exact ``run_id`` (the same target
        date/WeatherNext-run/policy identity), and does so without this store ever having
        touched an external getter/reader itself."""
        if not isinstance(start, WnA1RunStart):
            raise AttemptStoreError("only WnA1RunStart may be registered")
        payload = json.dumps(_encode_run_start(start), sort_keys=True, separators=(",", ":"))
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                existing = db.execute(
                    "SELECT 1 FROM wn_a1_run_starts WHERE run_id=?", (start.run_id,)
                ).fetchone()
                if existing is not None:
                    raise AttemptStoreError(
                        f"duplicate WN-A1 run registration rejected: {start.run_id}"
                    )
                db.execute(
                    "INSERT INTO wn_a1_run_starts VALUES (?,?,?,?,?,?,?,?)",
                    (
                        start.run_id,
                        start.target_local_date,
                        start.event_ticker,
                        _timestamp(start.pipeline_started_at),
                        RUN_SCHEMA_VERSION,
                        payload,
                        "1",
                        "0",
                    ),
                )
        except sqlite3.Error as exc:
            raise AttemptStoreError("WN-A1 run registration rejected") from exc

    def get_run_start(self, run_id: str) -> WnA1RunStart:
        with self._connect() as db:
            row = db.execute(
                "SELECT payload FROM wn_a1_run_starts WHERE run_id=?", (run_id,)
            ).fetchone()
        if row is None:
            raise AttemptStoreError(f"unknown WN-A1 run: {run_id}")
        return _decode_run_start(json.loads(row["payload"]))

    def run_start_count(self) -> int:
        with self._connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM wn_a1_run_starts").fetchone()[0])

    def append_run_terminal(self, terminal: WnA1RunTerminal) -> None:
        """Item F4's second append-only phase. Fails closed on any duplicate ``run_id``
        (a run may reach its terminal state exactly once)."""
        if not isinstance(terminal, WnA1RunTerminal):
            raise AttemptStoreError("only WnA1RunTerminal may be persisted")
        payload = json.dumps(_encode_run_terminal(terminal), sort_keys=True, separators=(",", ":"))
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                existing = db.execute(
                    "SELECT 1 FROM wn_a1_run_terminals WHERE run_id=?", (terminal.run_id,)
                ).fetchone()
                if existing is not None:
                    raise AttemptStoreError(
                        f"duplicate WN-A1 run terminal persistence rejected: {terminal.run_id}"
                    )
                db.execute(
                    "INSERT INTO wn_a1_run_terminals VALUES (?,?,?,?,?,?,?)",
                    (
                        terminal.run_id,
                        terminal.status,
                        _timestamp(terminal.terminal_at),
                        RUN_SCHEMA_VERSION,
                        payload,
                        "1",
                        "0",
                    ),
                )
        except sqlite3.Error as exc:
            raise AttemptStoreError("WN-A1 run terminal persistence rejected") from exc

    def get_run_terminal(self, run_id: str) -> WnA1RunTerminal:
        with self._connect() as db:
            row = db.execute(
                "SELECT payload FROM wn_a1_run_terminals WHERE run_id=?", (run_id,)
            ).fetchone()
        if row is None:
            raise AttemptStoreError(f"WN-A1 run has no terminal record (yet): {run_id}")
        return _decode_run_terminal(json.loads(row["payload"]))

    def run_terminal_count(self) -> int:
        with self._connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM wn_a1_run_terminals").fetchone()[0])

    def run_status(self, run_id: str) -> str:
        """ "STARTED" (registered but no terminal yet -- durable evidence of an
        interrupted/incomplete invocation, e.g. the process died mid-run; Item F5), or the
        terminal record's own "COMPLETED"/"FAILED" status. Raises if ``run_id`` was never
        even registered."""
        with self._connect() as db:
            start_row = db.execute(
                "SELECT 1 FROM wn_a1_run_starts WHERE run_id=?", (run_id,)
            ).fetchone()
            if start_row is None:
                raise AttemptStoreError(f"unknown WN-A1 run: {run_id}")
            terminal_row = db.execute(
                "SELECT status FROM wn_a1_run_terminals WHERE run_id=?", (run_id,)
            ).fetchone()
        if terminal_row is None:
            return "STARTED"
        return str(terminal_row["status"])


def open_default_attempt_store() -> WnA1AttemptStore:
    """The one canonical, fixed-location WN-A1 attempt store used by real live runs."""
    return WnA1AttemptStore(_DEFAULT_DB_PATH)


def open_isolated_attempt_store(root: str | Path) -> WnA1AttemptStore:
    """Caller-selected-path store for tests/tools only -- never the live canonical run."""
    return WnA1AttemptStore(Path(root) / "attempts.sqlite3")


def replay_weathernext_evidence(attempt: WnA1Attempt) -> WeatherNextEnsembleEvidence:
    """Reopen persisted member rows and re-validate them against the persisted identity.

    Fails closed (``AttemptStoreError``) if the persisted rows do not reconstruct the exact
    evidence identity that was bound at evaluation time.
    """
    model = attempt.weathernext_model
    source_object = attempt.weathernext_source_object
    init_time = attempt.weathernext_init_time
    acquired_at = attempt.weathernext_acquired_at
    requested_lat = attempt.weathernext_requested_latitude
    requested_lon = attempt.weathernext_requested_longitude
    selected_lat = attempt.weathernext_selected_latitude
    selected_lon = attempt.weathernext_selected_longitude
    content_hash = attempt.weathernext_source_content_hash
    if (
        attempt.weathernext_evidence_identity is None
        or model is None
        or source_object is None
        or init_time is None
        or acquired_at is None
        or requested_lat is None
        or requested_lon is None
        or selected_lat is None
        or selected_lon is None
        or content_hash is None
        or not attempt.weathernext_members
    ):
        raise AttemptStoreError("attempt has no replayable WeatherNext evidence")
    raw_rows = [
        {
            "sample": sample,
            "lead_time_hours": lead_hours,
            "lead_subtime_minutes": lead_minutes,
            "valid_time": datetime.fromisoformat(valid_time_iso),
            "value_kelvin": Decimal(kelvin),
        }
        for sample, lead_hours, lead_minutes, valid_time_iso, kelvin in attempt.weathernext_members
    ]
    result = build_ensemble_evidence(
        model=model,
        source_object=source_object,
        init_time=init_time,
        acquired_at=acquired_at,
        variable=VARIABLE,
        requested_latitude=requested_lat,
        requested_longitude=requested_lon,
        selected_latitude=selected_lat,
        selected_longitude=selected_lon,
        unit=UNIT,
        raw_rows=raw_rows,
        source_content_hash=content_hash,
    )
    if result.status is not EnsembleStatus.COMPLETE or result.evidence is None:
        raise AttemptStoreError("persisted WeatherNext member rows no longer reconstruct")
    if result.evidence.evidence_identity != attempt.weathernext_evidence_identity:
        raise AttemptStoreError(
            "persisted WeatherNext member rows do not reproduce the bound evidence identity"
        )
    return result.evidence


def replay_decision(attempt: WnA1Attempt) -> AlertDecision:
    """Reopen ``attempt`` from its own persisted fields and reproduce its decision.

    This is a genuine fresh-process replay: it reconstructs the WeatherNext evidence from
    persisted raw member rows (re-validating the evidence identity), recomputes the raw
    ensemble probability over the persisted research window, reconstructs the bound
    contract predicate, recomputes market freshness from the persisted
    ``kalshi_orderbook_observed_at``/``evaluated_at`` pair using the same reviewed
    ``is_fresh_at`` function (never hardcoded), and re-runs ``decide_alert`` with the
    persisted side economics -- it never merely recomputes a hash from an in-memory object.
    """
    raw: RawProbabilityResult | None = None
    members: tuple[MemberDailyHigh, ...] | None = None
    if attempt.weathernext_evidence_identity is not None:
        if attempt.research_window_start is None or attempt.research_window_end is None:
            raise AttemptStoreError("attempt is missing its research probability window")
        evidence = replay_weathernext_evidence(attempt)
        # A window-bounding failure (no member values fall inside the persisted research
        # window) mirrors _run_evaluation's own handling at evaluation time: the evidence
        # was genuinely acquired, but the ensemble is not usable for this window -- treat
        # it as an incomplete ensemble on replay too, not a hard replay failure.
        try:
            members = compute_member_daily_highs(
                evidence, attempt.research_window_start, attempt.research_window_end
            )
        except WnA1Error:
            members = None
    if members is not None:
        contract = CurrentDailyHighContract(
            market_ticker=attempt.market_ticker,
            event_ticker=attempt.event_ticker,
            series_ticker=attempt.series_ticker,
            station_id=STATION_ID,
            location=LOCATION,
            measurement=MEASUREMENT,
            local_date=date.fromisoformat(attempt.target_local_date),
            timezone=TIMEZONE,
            lower=attempt.contract_lower,
            upper=attempt.contract_upper,
            comparator=attempt.contract_comparator,
            unit="degF",
            settlement_source=SETTLEMENT_SOURCE,
            settlement_source_url=SETTLEMENT_SOURCE_URL,
            window_status=WindowStatus(attempt.window_status),
            window_start_local=None,
            window_end_local=None,
            trading_cutoff_local=None,
            trading_cutoff_evidence_text=None,
        )
        raw = compute_raw_probability(members, contract)

    boundary: BoundaryRiskDiagnostic | None = None
    if attempt.boundary_baseline_ticker is not None:
        boundary = BoundaryRiskDiagnostic(
            members_within_one_f_of_boundary=attempt.boundary_members_within_one_f or 0,
            baseline_preferred_ticker=attempt.boundary_baseline_ticker,
            plus_one_preferred_ticker=(
                attempt.boundary_plus_one_ticker or attempt.boundary_baseline_ticker
            ),
            minus_one_preferred_ticker=(
                attempt.boundary_minus_one_ticker or attempt.boundary_baseline_ticker
            ),
        )

    if attempt.alert_policy_version != DEFAULT_POLICY.version:
        raise AttemptStoreError(
            "replay does not have historical alert-policy thresholds for "
            f"{attempt.alert_policy_version!r}; only the current policy is replayable"
        )
    # Never hardcode market_fresh=True: recompute it from the exact persisted freshness
    # input the original decision used. A missing orderbook_observed_at means no market
    # snapshot was ever acquired -- that unavailable state is not fresh, matching
    # evaluate_candidate's own ``snapshot is not None and is_fresh(...)`` at evaluation time.
    market_fresh = attempt.kalshi_orderbook_observed_at is not None and is_fresh_at(
        attempt.kalshi_orderbook_observed_at, attempt.evaluated_at
    )
    return decide_alert(
        contract_supported=True,
        ensemble_complete=raw is not None,
        market_fresh=market_fresh,
        yes_conservative_debit=attempt.yes_conservative_debit,
        no_conservative_debit=attempt.no_conservative_debit,
        raw=raw,
        window_status=WindowStatus(attempt.window_status),
        boundary=boundary,
        policy=DEFAULT_POLICY,
    )


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise AttemptStoreError("WN-A1 attempt timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _opt_dec(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _opt_dt(value: datetime | None) -> str | None:
    return None if value is None else _timestamp(value)


def _encode(attempt: WnA1Attempt) -> dict[str, object]:
    return {
        "attempt_id": attempt.attempt_id,
        "run_id": attempt.run_id,
        "target_local_date": attempt.target_local_date,
        "market_ticker": attempt.market_ticker,
        "event_ticker": attempt.event_ticker,
        "series_ticker": attempt.series_ticker,
        "contract_policy_identity": attempt.contract_policy_identity,
        "contract_policy_version": attempt.contract_policy_version,
        "contract_comparator": attempt.contract_comparator,
        "contract_lower": str(attempt.contract_lower),
        "contract_upper": _opt_dec(attempt.contract_upper),
        "research_window_start": _opt_dt(attempt.research_window_start),
        "research_window_end": _opt_dt(attempt.research_window_end),
        "window_status": attempt.window_status,
        "weathernext_evidence_identity": attempt.weathernext_evidence_identity,
        "weathernext_model": attempt.weathernext_model,
        "weathernext_source_object": attempt.weathernext_source_object,
        "weathernext_init_time": _opt_dt(attempt.weathernext_init_time),
        "weathernext_acquired_at": _opt_dt(attempt.weathernext_acquired_at),
        "weathernext_requested_latitude": _opt_dec(attempt.weathernext_requested_latitude),
        "weathernext_requested_longitude": _opt_dec(attempt.weathernext_requested_longitude),
        "weathernext_selected_latitude": _opt_dec(attempt.weathernext_selected_latitude),
        "weathernext_selected_longitude": _opt_dec(attempt.weathernext_selected_longitude),
        "weathernext_source_content_hash": attempt.weathernext_source_content_hash,
        "weathernext_members": [list(row) for row in attempt.weathernext_members],
        "kalshi_snapshot_identity": attempt.kalshi_snapshot_identity,
        "kalshi_orderbook_observed_at": _opt_dt(attempt.kalshi_orderbook_observed_at),
        "alert_policy_version": attempt.alert_policy_version,
        "decision_state": attempt.decision_state,
        "decision_gate_failures": list(attempt.decision_gate_failures),
        "side": attempt.side,
        "model_probability_yes": _opt_dec(attempt.model_probability_yes),
        "yes_conservative_debit": _opt_dec(attempt.yes_conservative_debit),
        "no_conservative_debit": _opt_dec(attempt.no_conservative_debit),
        "yes_gap_pp": _opt_dec(attempt.yes_gap_pp),
        "yes_buffered_gap_pp": _opt_dec(attempt.yes_buffered_gap_pp),
        "no_gap_pp": _opt_dec(attempt.no_gap_pp),
        "no_buffered_gap_pp": _opt_dec(attempt.no_buffered_gap_pp),
        "yes_fee_quality": attempt.yes_fee_quality,
        "no_fee_quality": attempt.no_fee_quality,
        "boundary_members_within_one_f": attempt.boundary_members_within_one_f,
        "boundary_baseline_ticker": attempt.boundary_baseline_ticker,
        "boundary_plus_one_ticker": attempt.boundary_plus_one_ticker,
        "boundary_minus_one_ticker": attempt.boundary_minus_one_ticker,
        "evaluated_at": _timestamp(attempt.evaluated_at),
        "research_only": True,
        "production_influence": "0",
    }


def _dec(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _dt(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AttemptStoreError("persisted WN-A1 timestamp is invalid")
    return datetime.fromisoformat(value)


def _decode(payload: dict[str, object]) -> WnA1Attempt:
    if payload.get("research_only") is not True or payload.get("production_influence") != "0":
        raise AttemptStoreError("persisted WN-A1 attempt lost its safety invariants")
    members_raw = payload["weathernext_members"]
    if not isinstance(members_raw, list):
        raise AttemptStoreError("persisted WN-A1 attempt member rows are malformed")
    members = tuple(
        (int(row[0]), int(row[1]), int(row[2]), str(row[3]), str(row[4])) for row in members_raw
    )
    gate_failures_raw = payload["decision_gate_failures"]
    if not isinstance(gate_failures_raw, list):
        raise AttemptStoreError("persisted WN-A1 attempt gate failures are malformed")
    evaluated_at = _dt(payload["evaluated_at"])
    if evaluated_at is None:
        raise AttemptStoreError("persisted WN-A1 attempt is missing its timestamp")
    return WnA1Attempt(
        attempt_id=str(payload["attempt_id"]),
        run_id=str(payload["run_id"]),
        target_local_date=str(payload["target_local_date"]),
        market_ticker=str(payload["market_ticker"]),
        event_ticker=str(payload["event_ticker"]),
        series_ticker=str(payload["series_ticker"]),
        contract_policy_identity=str(payload["contract_policy_identity"]),
        contract_policy_version=str(payload["contract_policy_version"]),
        contract_comparator=str(payload["contract_comparator"]),
        contract_lower=Decimal(str(payload["contract_lower"])),
        contract_upper=_dec(payload["contract_upper"]),
        research_window_start=_dt(payload["research_window_start"]),
        research_window_end=_dt(payload["research_window_end"]),
        window_status=str(payload["window_status"]),
        weathernext_evidence_identity=_str_or_none(payload["weathernext_evidence_identity"]),
        weathernext_model=_str_or_none(payload["weathernext_model"]),
        weathernext_source_object=_str_or_none(payload["weathernext_source_object"]),
        weathernext_init_time=_dt(payload["weathernext_init_time"]),
        weathernext_acquired_at=_dt(payload["weathernext_acquired_at"]),
        weathernext_requested_latitude=_dec(payload["weathernext_requested_latitude"]),
        weathernext_requested_longitude=_dec(payload["weathernext_requested_longitude"]),
        weathernext_selected_latitude=_dec(payload["weathernext_selected_latitude"]),
        weathernext_selected_longitude=_dec(payload["weathernext_selected_longitude"]),
        weathernext_source_content_hash=_str_or_none(payload["weathernext_source_content_hash"]),
        weathernext_members=members,
        kalshi_snapshot_identity=_str_or_none(payload["kalshi_snapshot_identity"]),
        kalshi_orderbook_observed_at=_dt(payload["kalshi_orderbook_observed_at"]),
        alert_policy_version=str(payload["alert_policy_version"]),
        decision_state=str(payload["decision_state"]),
        decision_gate_failures=tuple(str(v) for v in gate_failures_raw),
        side=_str_or_none(payload["side"]),
        model_probability_yes=_dec(payload["model_probability_yes"]),
        yes_conservative_debit=_dec(payload["yes_conservative_debit"]),
        no_conservative_debit=_dec(payload["no_conservative_debit"]),
        yes_gap_pp=_dec(payload["yes_gap_pp"]),
        yes_buffered_gap_pp=_dec(payload["yes_buffered_gap_pp"]),
        no_gap_pp=_dec(payload["no_gap_pp"]),
        no_buffered_gap_pp=_dec(payload["no_buffered_gap_pp"]),
        yes_fee_quality=_str_or_none(payload["yes_fee_quality"]),
        no_fee_quality=_str_or_none(payload["no_fee_quality"]),
        boundary_members_within_one_f=_int_or_none(payload["boundary_members_within_one_f"]),
        boundary_baseline_ticker=_str_or_none(payload["boundary_baseline_ticker"]),
        boundary_plus_one_ticker=_str_or_none(payload["boundary_plus_one_ticker"]),
        boundary_minus_one_ticker=_str_or_none(payload["boundary_minus_one_ticker"]),
        evaluated_at=evaluated_at,
    )


def _str_or_none(value: object) -> str | None:
    return None if value is None else str(value)


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise AttemptStoreError("persisted WN-A1 integer field is malformed")
    return value


def _encode_run_start(start: WnA1RunStart) -> dict[str, object]:
    return {
        "run_id": start.run_id,
        "target_local_date": start.target_local_date,
        "event_ticker": start.event_ticker,
        "weathernext_source_object": start.weathernext_source_object,
        "weathernext_init_time": _timestamp(start.weathernext_init_time),
        "weathernext_requested_latitude": str(start.weathernext_requested_latitude),
        "weathernext_requested_longitude": str(start.weathernext_requested_longitude),
        "alert_policy_version": start.alert_policy_version,
        "pipeline_started_at": _timestamp(start.pipeline_started_at),
        "research_only": True,
        "production_influence": "0",
    }


def _decode_run_start(payload: dict[str, object]) -> WnA1RunStart:
    if payload.get("research_only") is not True or payload.get("production_influence") != "0":
        raise AttemptStoreError("persisted WN-A1 run start lost its safety invariants")
    pipeline_started_at = _dt(payload["pipeline_started_at"])
    weathernext_init_time = _dt(payload["weathernext_init_time"])
    if pipeline_started_at is None or weathernext_init_time is None:
        raise AttemptStoreError("persisted WN-A1 run start is missing a required timestamp")
    return WnA1RunStart(
        run_id=str(payload["run_id"]),
        target_local_date=str(payload["target_local_date"]),
        event_ticker=str(payload["event_ticker"]),
        weathernext_source_object=str(payload["weathernext_source_object"]),
        weathernext_init_time=weathernext_init_time,
        weathernext_requested_latitude=Decimal(str(payload["weathernext_requested_latitude"])),
        weathernext_requested_longitude=Decimal(str(payload["weathernext_requested_longitude"])),
        alert_policy_version=str(payload["alert_policy_version"]),
        pipeline_started_at=pipeline_started_at,
    )


def _route_outcomes_from_payload(payload: dict[str, object]) -> tuple[_RouteOutcome, ...]:
    route_outcomes_raw = payload["route_outcomes"]
    if not isinstance(route_outcomes_raw, list):
        raise AttemptStoreError("persisted WN-A1 run route outcomes are malformed")
    route_outcomes: list[_RouteOutcome] = []
    for row in route_outcomes_raw:
        if not isinstance(row, list) or len(row) != 3:
            raise AttemptStoreError("persisted WN-A1 run route outcome row is malformed")
        ticker, state, reason = row
        route_outcomes.append((str(ticker), str(state), None if reason is None else str(reason)))
    return tuple(route_outcomes)


def _encode_run_terminal(terminal: WnA1RunTerminal) -> dict[str, object]:
    return {
        "run_id": terminal.run_id,
        "status": terminal.status,
        "pipeline_started_at": _timestamp(terminal.pipeline_started_at),
        "terminal_at": _timestamp(terminal.terminal_at),
        "target_local_date": terminal.target_local_date,
        "event_ticker": terminal.event_ticker,
        "weathernext_source_object": terminal.weathernext_source_object,
        "weathernext_init_time": _timestamp(terminal.weathernext_init_time),
        "weathernext_requested_latitude": str(terminal.weathernext_requested_latitude),
        "weathernext_requested_longitude": str(terminal.weathernext_requested_longitude),
        "discovery_state": terminal.discovery_state,
        "discovery_reason": terminal.discovery_reason,
        "weathernext_state": terminal.weathernext_state,
        "weathernext_reason": terminal.weathernext_reason,
        "weathernext_acquired_at": _opt_dt(terminal.weathernext_acquired_at),
        "route_outcomes": [list(row) for row in terminal.route_outcomes],
        "attempt_ids": list(terminal.attempt_ids),
        "alert_policy_version": terminal.alert_policy_version,
        "failure_reason": terminal.failure_reason,
        "research_only": True,
        "production_influence": "0",
    }


def _decode_run_terminal(payload: dict[str, object]) -> WnA1RunTerminal:
    if payload.get("research_only") is not True or payload.get("production_influence") != "0":
        raise AttemptStoreError("persisted WN-A1 run terminal lost its safety invariants")
    attempt_ids_raw = payload["attempt_ids"]
    if not isinstance(attempt_ids_raw, list):
        raise AttemptStoreError("persisted WN-A1 run terminal attempt ids are malformed")
    pipeline_started_at = _dt(payload["pipeline_started_at"])
    terminal_at = _dt(payload["terminal_at"])
    weathernext_init_time = _dt(payload["weathernext_init_time"])
    if pipeline_started_at is None or terminal_at is None or weathernext_init_time is None:
        raise AttemptStoreError("persisted WN-A1 run terminal is missing a required timestamp")
    return WnA1RunTerminal(
        run_id=str(payload["run_id"]),
        status=str(payload["status"]),
        pipeline_started_at=pipeline_started_at,
        terminal_at=terminal_at,
        target_local_date=str(payload["target_local_date"]),
        event_ticker=str(payload["event_ticker"]),
        weathernext_source_object=str(payload["weathernext_source_object"]),
        weathernext_init_time=weathernext_init_time,
        weathernext_requested_latitude=Decimal(str(payload["weathernext_requested_latitude"])),
        weathernext_requested_longitude=Decimal(str(payload["weathernext_requested_longitude"])),
        discovery_state=str(payload["discovery_state"]),
        discovery_reason=_str_or_none(payload["discovery_reason"]),
        weathernext_state=str(payload["weathernext_state"]),
        weathernext_reason=_str_or_none(payload["weathernext_reason"]),
        weathernext_acquired_at=_dt(payload["weathernext_acquired_at"]),
        route_outcomes=_route_outcomes_from_payload(payload),
        attempt_ids=tuple(str(v) for v in attempt_ids_raw),
        alert_policy_version=str(payload["alert_policy_version"]),
        failure_reason=_str_or_none(payload["failure_reason"]),
    )
