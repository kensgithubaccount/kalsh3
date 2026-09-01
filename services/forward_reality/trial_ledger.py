"""Durable, append-only registration of prospective research trials.

This module has no outcome, forecast, execution, profitability, or promotion
authority. A trial must exist here before another system may score it.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType

SCHEMA_VERSION = "fr-a2-trial-ledger-v2"


class LedgerError(ValueError):
    """A malformed, conflicting, or unavailable ledger is rejected."""


class TrialStatus(StrEnum):
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABANDONED = "ABANDONED"


class EvaluationPlan:
    """A frozen, canonical evaluation specification."""

    __slots__ = ("_value", "identity")
    _value: Mapping[str, object]
    identity: str

    def __init__(self, value: Mapping[str, object]) -> None:
        if not isinstance(value, Mapping) or not value:
            raise LedgerError("evaluation plan must be a non-empty mapping")
        frozen = _freeze(value)
        object.__setattr__(self, "_value", frozen)
        object.__setattr__(self, "identity", _hash_bytes(_canonical_json(frozen)))

    def __setattr__(self, name: str, value: object) -> None:
        if hasattr(self, name):
            raise AttributeError("evaluation plan is frozen")
        object.__setattr__(self, name, value)

    @property
    def value(self) -> Mapping[str, object]:
        return self._value


class _TrustedIssuer:
    """Private issuer seam; production construction never accepts a clock."""

    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock

    def now(self) -> datetime:
        value = self._clock()
        if type(value) is not datetime or value.tzinfo is None:
            raise LedgerError("trusted issuer clock must return timezone-aware datetime")
        if value.utcoffset() != UTC.utcoffset(value):
            raise LedgerError("trusted issuer clock must return UTC datetime")
        return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class TrialDefinition:
    trial_id: str
    created_at: datetime
    candidate_family: str
    model_identity: str
    feature_specification_identity: str
    evaluation_plan: EvaluationPlan
    parent_trial_ids: tuple[str, ...]
    reason: str
    underlying_event_id: str
    sibling_market_ids: tuple[str, ...]
    research_only: bool
    production_influence: int
    schema_version: str
    registration_fingerprint: str


@dataclass(frozen=True, slots=True)
class TrialStatusEvent:
    trial_id: str
    sequence: int
    status: TrialStatus
    recorded_at: datetime
    event_fingerprint: str


@dataclass(frozen=True, slots=True)
class Trial:
    definition: TrialDefinition
    status: TrialStatus

    def __getattr__(self, name: str) -> object:
        return getattr(self.definition, name)


Clock = Callable[[], datetime]


class TrialLedger:
    """A restart-verifiable, create-only trial ledger backed by SQLite."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._issuer = _TrustedIssuer(lambda: datetime.now(UTC))
        self._open_or_create()

    @classmethod
    def _for_tests(cls, path: str | Path, clock: Clock) -> TrialLedger:
        """Private deterministic seam used only by FR-A2 tests."""
        ledger = cls(path)
        ledger._issuer = _TrustedIssuer(clock)
        return ledger

    def register(
        self,
        *,
        candidate_family: str,
        model_identity: str,
        feature_specification_identity: str,
        evaluation_plan: EvaluationPlan,
        underlying_event_id: str,
        reason: str,
        sibling_market_ids: tuple[str, ...] = (),
        parent_trial_ids: tuple[str, ...] = (),
    ) -> Trial:
        for value, name in (
            (candidate_family, "candidate_family"),
            (model_identity, "model_identity"),
            (feature_specification_identity, "feature_specification_identity"),
            (underlying_event_id, "underlying_event_id"),
            (reason, "reason"),
        ):
            _text(value, name)
        if type(evaluation_plan) is not EvaluationPlan:
            raise LedgerError("evaluation plan must be an EvaluationPlan")
        siblings = _strings(sibling_market_ids, "sibling_market_ids")
        parents = _strings(parent_trial_ids, "parent_trial_ids")
        if parents:
            with self._connect(read_only=True) as db:
                found = {
                    str(row[0])
                    for row in db.execute("SELECT trial_id FROM trial_definitions")
                    if str(row[0]) in parents
                }
            if found != set(parents):
                raise LedgerError("parent trial is not registered")
        created_at = self._issuer.now()
        identity_material = {
            "candidate_family": candidate_family,
            "model_identity": model_identity,
            "feature_specification_identity": feature_specification_identity,
            "evaluation_plan_identity": evaluation_plan.identity,
            "parent_trial_ids": parents,
            "reason": reason,
            "underlying_event_id": underlying_event_id,
            "sibling_market_ids": siblings,
        }
        trial_id = f"trial-{_digest(identity_material)}"
        fingerprint = _registration_fingerprint(
            trial_id=trial_id,
            created_at=created_at,
            candidate_family=candidate_family,
            model_identity=model_identity,
            feature_specification_identity=feature_specification_identity,
            evaluation_plan_identity=evaluation_plan.identity,
            parent_trial_ids=parents,
            reason=reason,
            underlying_event_id=underlying_event_id,
            sibling_market_ids=siblings,
            research_only=True,
            production_influence=0,
        )
        definition = TrialDefinition(
            trial_id,
            created_at,
            candidate_family,
            model_identity,
            feature_specification_identity,
            evaluation_plan,
            parents,
            reason,
            underlying_event_id,
            siblings,
            True,
            0,
            SCHEMA_VERSION,
            fingerprint,
        )
        with self._connect() as db:
            try:
                db.execute(
                    "INSERT INTO trial_definitions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    _definition_row(definition),
                )
                self._insert_event(db, definition, TrialStatus.PLANNED, created_at, 1)
            except sqlite3.IntegrityError as exc:
                raise LedgerError("duplicate or conflicting trial definition") from exc
        return Trial(definition, TrialStatus.PLANNED)

    def advance(self, trial_id: str, status: TrialStatus) -> Trial:
        definition, events = self._read_trial(trial_id)
        if type(status) is not TrialStatus:
            raise LedgerError("invalid trial status")
        current = _replay(definition, events)
        if current in (TrialStatus.COMPLETED, TrialStatus.FAILED, TrialStatus.ABANDONED):
            raise LedgerError("terminal trial cannot be rewritten")
        allowed = {
            TrialStatus.PLANNED: {TrialStatus.RUNNING, TrialStatus.FAILED, TrialStatus.ABANDONED},
            TrialStatus.RUNNING: {TrialStatus.COMPLETED, TrialStatus.FAILED, TrialStatus.ABANDONED},
        }
        if status not in allowed[current]:
            raise LedgerError(f"invalid status transition {current} -> {status}")
        recorded_at = self._issuer.now()
        with self._connect() as db:
            self._insert_event(db, definition, status, recorded_at, len(events) + 1)
        return Trial(definition, status)

    def get(self, trial_id: str) -> Trial:
        definition, events = self._read_trial(trial_id)
        return Trial(definition, _replay(definition, events))

    def status_events(self, trial_id: str) -> tuple[TrialStatusEvent, ...]:
        return self._read_trial(trial_id)[1]

    def trials_for_event(self, underlying_event_id: str) -> tuple[Trial, ...]:
        with self._connect(read_only=True) as db:
            rows = db.execute(
                "SELECT trial_id FROM trial_definitions "
                "WHERE underlying_event_id=? ORDER BY trial_id",
                (underlying_event_id,),
            ).fetchall()
        return tuple(self.get(str(row[0])) for row in rows)

    def unique_underlying_event_count(
        self, underlying_event_ids: tuple[str, ...] | None = None
    ) -> int:
        """Count unique real-world events, never sibling tickers or rows."""
        with self._connect(read_only=True) as db:
            if underlying_event_ids is None:
                row = db.execute(
                    "SELECT COUNT(DISTINCT underlying_event_id) FROM trial_definitions"
                ).fetchone()
            else:
                ids = _strings(underlying_event_ids, "underlying_event_ids")
                if not ids:
                    return 0
                placeholders = ",".join("?" for _ in ids)
                row = db.execute(
                    "SELECT COUNT(DISTINCT underlying_event_id) FROM trial_definitions "  # noqa: S608
                    f"WHERE underlying_event_id IN ({placeholders})",
                    ids,
                ).fetchone()
        return int(row[0]) if row is not None else 0

    @property
    def events(self) -> tuple[TrialStatusEvent, ...]:
        with self._connect(read_only=True) as db:
            rows = db.execute(
                "SELECT trial_id,sequence,status,recorded_at,event_fingerprint "
                "FROM trial_status_events ORDER BY trial_id,sequence"
            ).fetchall()
        return tuple(_event_from_row(row) for row in rows)

    def _read_trial(self, trial_id: str) -> tuple[TrialDefinition, tuple[TrialStatusEvent, ...]]:
        with self._connect(read_only=True) as db:
            row = db.execute(
                "SELECT * FROM trial_definitions WHERE trial_id=?", (trial_id,)
            ).fetchone()
            if row is None:
                raise LedgerError("unknown trial")
            definition = _definition_from_row(row)
            rows = db.execute(
                "SELECT trial_id,sequence,status,recorded_at,event_fingerprint "
                "FROM trial_status_events WHERE trial_id=? ORDER BY sequence",
                (trial_id,),
            ).fetchall()
        events = tuple(_event_from_row(item) for item in rows)
        if not events:
            raise LedgerError("trial has no status history")
        return definition, events

    def _insert_event(
        self,
        db: sqlite3.Connection,
        definition: TrialDefinition,
        status: TrialStatus,
        recorded_at: datetime,
        sequence: int,
    ) -> None:
        fingerprint = _digest(
            {
                "schema_version": SCHEMA_VERSION,
                "trial_id": definition.trial_id,
                "sequence": sequence,
                "status": status.value,
                "recorded_at": _timestamp(recorded_at),
                "definition_fingerprint": definition.registration_fingerprint,
            }
        )
        db.execute(
            "INSERT INTO trial_status_events VALUES (?,?,?,?,?)",
            (definition.trial_id, sequence, status.value, _timestamp(recorded_at), fingerprint),
        )

    def _connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        if read_only:
            db = sqlite3.connect(f"file:{self._path}?mode=ro", uri=True)
        else:
            db = sqlite3.connect(self._path, timeout=30)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _open_or_create(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            existing = {
                str(row[0])
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','trigger') "
                    "AND name NOT LIKE 'sqlite_%'"
                )
            }
            if not existing:
                db.executescript(_SCHEMA)
            elif existing != _SCHEMA_OBJECTS:
                raise LedgerError("trial ledger schema is incompatible or corrupt")
            metadata = db.execute(
                "SELECT schema_version FROM ledger_metadata WHERE singleton=1"
            ).fetchall()
            if len(metadata) != 1 or metadata[0][0] != SCHEMA_VERSION:
                raise LedgerError("trial ledger metadata is invalid")
            check = db.execute("PRAGMA quick_check").fetchone()
            if check is None or check[0] != "ok":
                raise LedgerError("trial ledger integrity check failed")
        self._verify_all()
        self._path.chmod(0o600)

    def _verify_all(self) -> None:
        with self._connect(read_only=True) as db:
            rows = db.execute("SELECT * FROM trial_definitions ORDER BY trial_id").fetchall()
        for row in rows:
            definition = _definition_from_row(row)
            _, events = self._read_trial(definition.trial_id)
            _replay(definition, events)


_SCHEMA = """
CREATE TABLE ledger_metadata (singleton INTEGER PRIMARY KEY, schema_version TEXT NOT NULL);
INSERT INTO ledger_metadata VALUES (1, 'fr-a2-trial-ledger-v2');
CREATE TABLE trial_definitions (
 trial_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, candidate_family TEXT NOT NULL,
 model_identity TEXT NOT NULL, feature_specification_identity TEXT NOT NULL,
 evaluation_plan_json TEXT NOT NULL, evaluation_plan_identity TEXT NOT NULL,
 parent_trial_ids_json TEXT NOT NULL, reason TEXT NOT NULL, underlying_event_id TEXT NOT NULL,
 sibling_market_ids_json TEXT NOT NULL, research_only INTEGER NOT NULL,
 production_influence INTEGER NOT NULL, schema_version TEXT NOT NULL,
 registration_fingerprint TEXT NOT NULL UNIQUE
);
CREATE TABLE trial_status_events (
 trial_id TEXT NOT NULL REFERENCES trial_definitions(trial_id), sequence INTEGER NOT NULL,
 status TEXT NOT NULL, recorded_at TEXT NOT NULL, event_fingerprint TEXT NOT NULL UNIQUE,
 PRIMARY KEY (trial_id, sequence)
);
CREATE TRIGGER trial_definitions_no_update BEFORE UPDATE ON trial_definitions
 BEGIN SELECT RAISE(ABORT, 'trial definitions are append-only'); END;
CREATE TRIGGER trial_definitions_no_delete BEFORE DELETE ON trial_definitions
 BEGIN SELECT RAISE(ABORT, 'trial definitions are append-only'); END;
CREATE TRIGGER trial_status_events_no_update BEFORE UPDATE ON trial_status_events
 BEGIN SELECT RAISE(ABORT, 'trial status events are append-only'); END;
CREATE TRIGGER trial_status_events_no_delete BEFORE DELETE ON trial_status_events
 BEGIN SELECT RAISE(ABORT, 'trial status events are append-only'); END;
"""
_SCHEMA_OBJECTS = {
    "ledger_metadata",
    "trial_definitions",
    "trial_status_events",
    "trial_definitions_no_update",
    "trial_definitions_no_delete",
    "trial_status_events_no_update",
    "trial_status_events_no_delete",
}


def _definition_row(d: TrialDefinition) -> tuple[object, ...]:
    return (
        d.trial_id,
        _timestamp(d.created_at),
        d.candidate_family,
        d.model_identity,
        d.feature_specification_identity,
        _canonical_json(d.evaluation_plan.value).decode(),
        d.evaluation_plan.identity,
        _canonical_json(d.parent_trial_ids).decode(),
        d.reason,
        d.underlying_event_id,
        _canonical_json(d.sibling_market_ids).decode(),
        1,
        0,
        d.schema_version,
        d.registration_fingerprint,
    )


def _definition_from_row(row: sqlite3.Row) -> TrialDefinition:
    try:
        definition = TrialDefinition(
            str(row["trial_id"]),
            _parse_timestamp(row["created_at"]),
            str(row["candidate_family"]),
            str(row["model_identity"]),
            str(row["feature_specification_identity"]),
            EvaluationPlan(json.loads(str(row["evaluation_plan_json"]))),
            tuple(json.loads(str(row["parent_trial_ids_json"]))),
            str(row["reason"]),
            str(row["underlying_event_id"]),
            tuple(json.loads(str(row["sibling_market_ids_json"]))),
            row["research_only"] == 1,
            row["production_influence"],
            str(row["schema_version"]),
            str(row["registration_fingerprint"]),
        )
    except (KeyError, LedgerError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LedgerError("corrupt trial definition") from exc
    if (
        definition.schema_version != SCHEMA_VERSION
        or not definition.research_only
        or definition.production_influence != 0
    ):
        raise LedgerError("trial safety fields are invalid")
    expected = _registration_fingerprint(
        trial_id=definition.trial_id,
        created_at=definition.created_at,
        candidate_family=definition.candidate_family,
        model_identity=definition.model_identity,
        feature_specification_identity=definition.feature_specification_identity,
        evaluation_plan_identity=definition.evaluation_plan.identity,
        parent_trial_ids=definition.parent_trial_ids,
        reason=definition.reason,
        underlying_event_id=definition.underlying_event_id,
        sibling_market_ids=definition.sibling_market_ids,
        research_only=definition.research_only,
        production_influence=definition.production_influence,
    )
    if (
        definition.evaluation_plan.identity != str(row["evaluation_plan_identity"])
        or expected != definition.registration_fingerprint
    ):
        raise LedgerError("trial definition fingerprint mismatch")
    return definition


def _event_from_row(row: sqlite3.Row) -> TrialStatusEvent:
    try:
        return TrialStatusEvent(
            str(row["trial_id"]),
            int(row["sequence"]),
            TrialStatus(str(row["status"])),
            _parse_timestamp(row["recorded_at"]),
            str(row["event_fingerprint"]),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise LedgerError("corrupt trial status event") from exc


def _replay(definition: TrialDefinition, events: tuple[TrialStatusEvent, ...]) -> TrialStatus:
    if events[0].status is not TrialStatus.PLANNED or events[0].sequence != 1:
        raise LedgerError("status history does not begin with PLANNED")
    current = TrialStatus.PLANNED
    allowed = {
        TrialStatus.PLANNED: {TrialStatus.RUNNING, TrialStatus.FAILED, TrialStatus.ABANDONED},
        TrialStatus.RUNNING: {TrialStatus.COMPLETED, TrialStatus.FAILED, TrialStatus.ABANDONED},
    }
    for expected_sequence, event in enumerate(events, 1):
        if event.trial_id != definition.trial_id or event.sequence != expected_sequence:
            raise LedgerError("status event sequence is invalid")
        expected_fingerprint = _digest(
            {
                "schema_version": SCHEMA_VERSION,
                "trial_id": event.trial_id,
                "sequence": event.sequence,
                "status": event.status.value,
                "recorded_at": _timestamp(event.recorded_at),
                "definition_fingerprint": definition.registration_fingerprint,
            }
        )
        if expected_fingerprint != event.event_fingerprint:
            raise LedgerError("status event fingerprint mismatch")
        if expected_sequence > 1 and (
            current in (TrialStatus.COMPLETED, TrialStatus.FAILED, TrialStatus.ABANDONED)
            or event.status not in allowed[current]
        ):
            raise LedgerError("invalid persisted status transition")
        current = event.status
    return current


def _registration_fingerprint(**values: object) -> str:
    return _digest(
        {
            "schema_version": SCHEMA_VERSION,
            **values,
            "research_only": True,
            "production_influence": 0,
        }
    )


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise LedgerError("evaluation plan keys must be strings")
        return MappingProxyType({key: _freeze(value[key]) for key in sorted(value)})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    if type(value) is float and not math.isfinite(value):
        raise LedgerError("evaluation plan cannot contain non-finite numbers")
    if type(value) in (str, int, float, bool) or value is None:
        return value
    raise LedgerError("evaluation plan contains unsupported value")


def _jsonable(value: object) -> object:
    if type(value) is datetime:
        return _timestamp(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    return value


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            _jsonable(value), allow_nan=False, sort_keys=True, separators=(",", ":")
        ).encode()
    except (TypeError, ValueError) as exc:
        raise LedgerError("value is not canonical JSON material") from exc


def _digest(value: object) -> str:
    return _hash_bytes(_canonical_json(value))


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _timestamp(value: datetime) -> str:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != UTC.utcoffset(value)
    ):
        raise LedgerError("timestamp must be canonical UTC")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime:
    if type(value) is not str:
        raise LedgerError("persisted timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LedgerError("persisted timestamp is invalid") from exc
    if _timestamp(parsed) != value:
        raise LedgerError("persisted timestamp is not canonical")
    return parsed


def _text(value: object, name: str) -> None:
    if type(value) is not str or not value.strip():
        raise LedgerError(f"{name} must be non-empty text")


def _strings(value: object, name: str) -> tuple[str, ...]:
    if type(value) is not tuple or any(type(item) is not str or not item for item in value):
        raise LedgerError(f"{name} must be a tuple of non-empty strings")
    return tuple(sorted(set(value)))
