"""Issuer-authenticated, append-only ledger for prospective research trials."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import secrets
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

SCHEMA_VERSION = "fr-a2-trial-ledger-v3"


class LedgerError(ValueError):
    """The authenticated ledger is malformed, conflicting, or unavailable."""


class TrialStatus(StrEnum):
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABANDONED = "ABANDONED"


class EvaluationPlan:
    """Frozen, strict canonical JSON evaluation specification."""

    __slots__ = ("_value", "identity")
    _value: Mapping[str, object]
    identity: str

    def __init__(self, value: Mapping[str, object]) -> None:
        if not isinstance(value, Mapping) or not value:
            raise LedgerError("evaluation plan must be a non-empty mapping")
        frozen = _freeze(value)
        object.__setattr__(self, "_value", frozen)
        object.__setattr__(self, "identity", _hash(_canonical(frozen)))

    def __setattr__(self, name: str, value: object) -> None:
        if hasattr(self, name):
            raise AttributeError("evaluation plan is frozen")
        object.__setattr__(self, name, value)

    @property
    def value(self) -> Mapping[str, object]:
        return self._value


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
    content_hash: str
    issuer_mac: str

    @property
    def registration_fingerprint(self) -> str:
        return self.content_hash


@dataclass(frozen=True, slots=True)
class TrialStatusEvent:
    ledger_sequence: int
    trial_id: str
    sequence: int
    status: TrialStatus
    recorded_at: datetime
    content_hash: str
    issuer_mac: str


@dataclass(frozen=True, slots=True)
class Trial:
    definition: TrialDefinition
    status: TrialStatus

    def __getattr__(self, name: str) -> object:
        return getattr(self.definition, name)


def _fr_a2_utc_now() -> datetime:
    return datetime.now(UTC)


class TrialLedger:
    """Authenticated journal authority with a rebuildable SQLite index."""

    __slots__ = (
        "_head_path",
        "_journal_path",
        "_key",
        "_key_existed",
        "_key_path",
        "_path",
    )

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._journal_path = self._path.with_name(self._path.name + ".journal")
        self._key_path = self._path.with_name(self._path.name + ".issuer-key")
        self._head_path = self._path.with_name(self._path.name + ".head")
        self._key_existed = self._key_path.exists()
        self._key = _fr_a2_load_issuer_key(self._key_path)
        self._open_or_create()

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
        existing = self._definitions()
        if any(parent not in existing for parent in parents):
            raise LedgerError("parent trial is not registered")
        created_at = self._now()
        identity = {
            "candidate_family": candidate_family,
            "model_identity": model_identity,
            "feature_specification_identity": feature_specification_identity,
            "evaluation_plan_identity": evaluation_plan.identity,
            "parent_trial_ids": parents,
            "reason": reason,
            "underlying_event_id": underlying_event_id,
            "sibling_market_ids": siblings,
        }
        trial_id = "trial-" + _hash(_canonical(identity))
        payload = {
            "trial_id": trial_id,
            "created_at": _timestamp(created_at),
            **identity,
            "evaluation_plan": evaluation_plan.value,
            "initial_status": TrialStatus.PLANNED.value,
            "initial_recorded_at": _timestamp(created_at),
            "research_only": True,
            "production_influence": 0,
            "schema_version": SCHEMA_VERSION,
        }
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
            _hash(_canonical(payload)),
            "",
        )
        return self._append_registration(definition)

    def advance(self, trial_id: str, status: TrialStatus) -> Trial:
        trial = self.get(trial_id)
        if type(status) is not TrialStatus:
            raise LedgerError("invalid trial status")
        if trial.status in (TrialStatus.COMPLETED, TrialStatus.FAILED, TrialStatus.ABANDONED):
            raise LedgerError("terminal trial cannot be rewritten")
        allowed = {
            TrialStatus.PLANNED: {TrialStatus.RUNNING, TrialStatus.FAILED, TrialStatus.ABANDONED},
            TrialStatus.RUNNING: {TrialStatus.COMPLETED, TrialStatus.FAILED, TrialStatus.ABANDONED},
        }
        if status not in allowed[trial.status]:
            raise LedgerError(f"invalid status transition {trial.status} -> {status}")
        recorded_at = self._now()
        events = self.status_events(trial_id)
        if recorded_at < events[-1].recorded_at:
            raise LedgerError("issuer clock moved backwards")
        self._append_event(trial.definition, status, recorded_at, len(events) + 1)
        return self.get(trial_id)

    def get(self, trial_id: str) -> Trial:
        definitions = self._definitions()
        if trial_id not in definitions:
            raise LedgerError("unknown trial")
        events = self.status_events(trial_id)
        return Trial(definitions[trial_id], _replay(definitions[trial_id], events))

    def status_events(self, trial_id: str) -> tuple[TrialStatusEvent, ...]:
        return tuple(event for event in self._events() if event.trial_id == trial_id)

    def trials_for_event(self, underlying_event_id: str) -> tuple[Trial, ...]:
        return tuple(
            self.get(trial_id)
            for trial_id, definition in self._definitions().items()
            if definition.underlying_event_id == underlying_event_id
        )

    def unique_underlying_event_count(
        self, underlying_event_ids: tuple[str, ...] | None = None
    ) -> int:
        values = {definition.underlying_event_id for definition in self._definitions().values()}
        if underlying_event_ids is not None:
            values &= set(_strings(underlying_event_ids, "underlying_event_ids"))
        return len(values)

    @property
    def events(self) -> tuple[TrialStatusEvent, ...]:
        return self._events()

    def _now(self) -> datetime:
        value = _fr_a2_utc_now()
        if (
            type(value) is not datetime
            or value.tzinfo is None
            or value.utcoffset() != UTC.utcoffset(value)
        ):
            raise LedgerError("issuer time must be canonical UTC")
        return value.astimezone(UTC)

    def _append_registration(self, definition: TrialDefinition) -> Trial:
        if definition.trial_id in self._definitions():
            raise LedgerError("duplicate or conflicting trial definition")
        previous = self._head()["last_entry_hash"]
        entry = {
            "schema_version": SCHEMA_VERSION,
            "ledger_id": self._ledger_id(),
            "global_sequence": self._head()["last_sequence"] + 1,
            "previous_entry_hash": previous,
            "entry_type": "REGISTRATION",
            "payload": _registration_payload(definition),
            "content_hash": definition.content_hash,
            "created_at": _timestamp(definition.created_at),
            "research_only": True,
            "production_influence": 0,
        }
        self._append(entry)
        self._rebuild_index()
        return self.get(definition.trial_id)

    def _append_event(
        self, definition: TrialDefinition, status: TrialStatus, recorded_at: datetime, sequence: int
    ) -> None:
        payload = {
            "trial_id": definition.trial_id,
            "sequence": sequence,
            "status": status.value,
            "recorded_at": _timestamp(recorded_at),
            "definition_content_hash": definition.content_hash,
            "research_only": True,
            "production_influence": 0,
        }
        entry = {
            "schema_version": SCHEMA_VERSION,
            "ledger_id": self._ledger_id(),
            "global_sequence": self._head()["last_sequence"] + 1,
            "previous_entry_hash": self._head()["last_entry_hash"],
            "entry_type": "STATUS",
            "payload": payload,
            "content_hash": _hash(_canonical(payload)),
            "recorded_at": payload["recorded_at"],
            "research_only": True,
            "production_influence": 0,
        }
        self._append(entry)
        self._rebuild_index()

    def _append(self, entry: dict[str, object]) -> None:
        entry["entry_hash"] = _hash(_canonical(entry))
        entry["issuer_mac"] = _mac(self._key, entry)
        line = _canonical(entry) + b"\n"
        self._journal_path.parent.mkdir(parents=True, exist_ok=True)
        with self._journal_path.open("ab") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
        head = {
            "schema_version": SCHEMA_VERSION,
            "ledger_id": entry["ledger_id"],
            "last_sequence": entry["global_sequence"],
            "last_entry_hash": entry["entry_hash"],
        }
        head["issuer_mac"] = _mac(self._key, head)
        temporary = self._head_path.with_name(self._head_path.name + ".tmp")
        with temporary.open("wb") as stream:
            stream.write(_canonical(head))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self._head_path)
        fd = os.open(self._head_path.parent, os.O_RDONLY)
        os.fsync(fd)
        os.close(fd)

    def _head(self) -> dict[str, Any]:
        if not self._head_path.exists():
            return {
                "schema_version": SCHEMA_VERSION,
                "ledger_id": _hash(self._key),
                "last_sequence": 0,
                "last_entry_hash": "",
            }
        try:
            head = json.loads(self._head_path.read_text())
            if not isinstance(head, dict):
                raise LedgerError("issuer checkpoint is invalid")
            mac = head.pop("issuer_mac")
            if not hmac.compare_digest(str(mac), _mac(self._key, head)):
                raise LedgerError("issuer checkpoint MAC mismatch")
            if head["schema_version"] != SCHEMA_VERSION:
                raise LedgerError("issuer checkpoint schema mismatch")
            return head
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LedgerError("issuer checkpoint is corrupt") from exc

    def _ledger_id(self) -> str:
        return str(self._head()["ledger_id"])

    def _events(self) -> tuple[TrialStatusEvent, ...]:
        definitions = self._definitions()
        result: list[TrialStatusEvent] = []
        for entry in _read_entries(self._journal_path, self._key, self._head()):
            if entry["entry_type"] == "REGISTRATION":
                payload = entry["payload"]
                trial_id = str(payload["trial_id"])
                result.append(
                    TrialStatusEvent(
                        int(entry["global_sequence"]),
                        trial_id,
                        1,
                        TrialStatus.PLANNED,
                        _parse_timestamp(payload["created_at"]),
                        str(entry["content_hash"]),
                        str(entry["issuer_mac"]),
                    )
                )
            elif entry["entry_type"] == "STATUS":
                payload = entry["payload"]
                trial_id = str(payload["trial_id"])
                if trial_id not in definitions:
                    raise LedgerError("status event references unknown trial")
                if payload["definition_content_hash"] != definitions[trial_id].content_hash:
                    raise LedgerError("status event definition binding mismatch")
                if _hash(_canonical(payload)) != entry["content_hash"]:
                    raise LedgerError("status event content hash mismatch")
                result.append(
                    TrialStatusEvent(
                        int(entry["global_sequence"]),
                        trial_id,
                        int(payload["sequence"]),
                        TrialStatus(str(payload["status"])),
                        _parse_timestamp(payload["recorded_at"]),
                        str(entry["content_hash"]),
                        str(entry["issuer_mac"]),
                    )
                )
        return tuple(result)

    def _definitions(self) -> dict[str, TrialDefinition]:
        result: dict[str, TrialDefinition] = {}
        for entry in _read_entries(self._journal_path, self._key, self._head()):
            if entry["entry_type"] == "REGISTRATION":
                payload = entry["payload"]
                trial_id = str(payload["trial_id"])
                if trial_id in result:
                    raise LedgerError("duplicate trial registration")
                result[trial_id] = _definition_from_payload(payload, entry)
        return result

    def _open_or_create(self) -> None:
        if not self._journal_path.exists() and self._head_path.exists():
            raise LedgerError("checkpoint exists without journal")
        if (
            not self._journal_path.exists()
            and not self._head_path.exists()
            and (self._key_existed or self._path.exists())
        ):
            raise LedgerError("authenticated journal and checkpoint are missing")
        self._head()
        list(_read_entries(self._journal_path, self._key, self._head()))
        definitions = self._definitions()
        entries = _read_entries(self._journal_path, self._key, self._head())
        positions = {
            str(entry["payload"]["trial_id"]): int(entry["global_sequence"])
            for entry in entries
            if entry["entry_type"] == "REGISTRATION"
        }
        for definition in definitions.values():
            if any(
                parent not in definitions or positions[parent] >= positions[definition.trial_id]
                for parent in definition.parent_trial_ids
            ):
                raise LedgerError("parent trial is not a prior valid registration")
            _replay(
                definition,
                tuple(event for event in self._events() if event.trial_id == definition.trial_id),
            )
        self._rebuild_index()

    def _rebuild_index(self) -> None:
        with sqlite3.connect(self._path) as db:
            db.executescript(
                "DROP TABLE IF EXISTS trial_index; CREATE TABLE trial_index "
                "(trial_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL);"
            )
            for trial_id, definition in self._definitions().items():
                db.execute(
                    "INSERT INTO trial_index VALUES (?,?)", (trial_id, definition.content_hash)
                )


def _registration_payload(d: TrialDefinition) -> dict[str, object]:
    return {
        "trial_id": d.trial_id,
        "created_at": _timestamp(d.created_at),
        "candidate_family": d.candidate_family,
        "model_identity": d.model_identity,
        "feature_specification_identity": d.feature_specification_identity,
        "evaluation_plan": d.evaluation_plan.value,
        "evaluation_plan_identity": d.evaluation_plan.identity,
        "initial_status": TrialStatus.PLANNED.value,
        "initial_recorded_at": _timestamp(d.created_at),
        "parent_trial_ids": d.parent_trial_ids,
        "reason": d.reason,
        "underlying_event_id": d.underlying_event_id,
        "sibling_market_ids": d.sibling_market_ids,
        "research_only": True,
        "production_influence": 0,
        "schema_version": SCHEMA_VERSION,
    }


def _definition_from_payload(
    payload: Mapping[str, Any], entry: Mapping[str, Any]
) -> TrialDefinition:
    plan = EvaluationPlan(payload["evaluation_plan"])
    if (
        plan.identity != payload["evaluation_plan_identity"]
        or payload["research_only"] is not True
        or payload["production_influence"] != 0
        or payload["initial_status"] != TrialStatus.PLANNED.value
        or payload["initial_recorded_at"] != payload["created_at"]
    ):
        raise LedgerError("definition safety or evaluation identity mismatch")
    content_hash = _hash(_canonical(payload))
    if content_hash != entry["content_hash"]:
        raise LedgerError("definition content hash mismatch")
    return TrialDefinition(
        str(payload["trial_id"]),
        _parse_timestamp(payload["created_at"]),
        str(payload["candidate_family"]),
        str(payload["model_identity"]),
        str(payload["feature_specification_identity"]),
        plan,
        tuple(payload["parent_trial_ids"]),
        str(payload["reason"]),
        str(payload["underlying_event_id"]),
        tuple(payload["sibling_market_ids"]),
        True,
        0,
        SCHEMA_VERSION,
        content_hash,
        str(entry["issuer_mac"]),
    )


def _read_entries(path: Path, key: bytes, head: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    if not path.exists():
        if head["last_sequence"] != 0:
            raise LedgerError("journal missing authenticated history")
        return ()
    entries: list[dict[str, Any]] = []
    try:
        with path.open("rb") as stream:
            for raw in stream:
                entry = json.loads(raw)
                if not isinstance(entry, dict):
                    raise LedgerError("journal entry is invalid")
                if (
                    entry["schema_version"] != SCHEMA_VERSION
                    or entry["ledger_id"] != head["ledger_id"]
                ):
                    raise LedgerError("journal identity mismatch")
                seq = len(entries) + 1
                if entry["global_sequence"] != seq or entry["previous_entry_hash"] != (
                    entries[-1]["entry_hash"] if entries else ""
                ):
                    raise LedgerError("journal sequence or chain mismatch")
                mac = entry.pop("issuer_mac")
                entry_hash = entry.pop("entry_hash")
                if entry_hash != _hash(_canonical(entry)) or not hmac.compare_digest(
                    str(mac), _mac(key, {**entry, "entry_hash": entry_hash})
                ):
                    raise LedgerError("journal entry authentication mismatch")
                entry["entry_hash"] = entry_hash
                entry["issuer_mac"] = mac
                entries.append(entry)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LedgerError("journal is corrupt or truncated") from exc
    if len(entries) != head["last_sequence"] or (
        entries and entries[-1]["entry_hash"] != head["last_entry_hash"]
    ):
        raise LedgerError("journal disagrees with authenticated checkpoint")
    return tuple(entries)


def _replay(definition: TrialDefinition, events: tuple[TrialStatusEvent, ...]) -> TrialStatus:
    if not events or events[0].sequence != 1 or events[0].status is not TrialStatus.PLANNED:
        raise LedgerError("status history does not begin with PLANNED")
    current = TrialStatus.PLANNED
    previous = definition.created_at
    allowed = {
        TrialStatus.PLANNED: {TrialStatus.RUNNING, TrialStatus.FAILED, TrialStatus.ABANDONED},
        TrialStatus.RUNNING: {TrialStatus.COMPLETED, TrialStatus.FAILED, TrialStatus.ABANDONED},
    }
    for index, event in enumerate(events):
        if index == 0 and event.recorded_at != definition.created_at:
            raise LedgerError("PLANNED event time does not equal registration time")
        if event.recorded_at < previous or event.recorded_at < definition.created_at:
            raise LedgerError("status chronology is invalid")
        if event.sequence > 1 and event.status not in allowed.get(current, set()):
            raise LedgerError("status transition is invalid")
        previous, current = event.recorded_at, event.status
    return current


def _fr_a2_load_issuer_key(path: Path) -> bytes:
    if path.exists():
        value = path.read_bytes()
        if len(value) != 32:
            raise LedgerError("issuer key is invalid")
        return value
    value = secrets.token_bytes(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, value)
        os.fsync(fd)
    finally:
        os.close(fd)
    return value


def _mac(key: bytes, value: Mapping[str, object]) -> str:
    return hmac.new(key, _canonical(value), hashlib.sha256).hexdigest()


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
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    return value


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            _jsonable(value), allow_nan=False, sort_keys=True, separators=(",", ":")
        ).encode()
    except (TypeError, ValueError) as exc:
        raise LedgerError("value is not canonical JSON") from exc


def _hash(value: bytes | object) -> str:
    return hashlib.sha256(value if isinstance(value, bytes) else _canonical(value)).hexdigest()


def _timestamp(value: datetime) -> str:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != UTC.utcoffset(value)
    ):
        raise LedgerError("timestamp must be UTC")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime:
    if type(value) is not str:
        raise LedgerError("persisted timestamp is invalid")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LedgerError("persisted timestamp is invalid") from exc
    if _timestamp(result) != value:
        raise LedgerError("persisted timestamp is not canonical")
    return result


def _text(value: object, name: str) -> None:
    if type(value) is not str or not value.strip():
        raise LedgerError(f"{name} must be non-empty text")


def _strings(value: object, name: str) -> tuple[str, ...]:
    if type(value) is not tuple or any(type(item) is not str or not item for item in value):
        raise LedgerError(f"{name} must be a tuple of strings")
    return tuple(sorted(set(value)))
