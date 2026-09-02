"""Issuer-bound, append-only, research-only prospective outcome scoring."""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import math
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from statistics import NormalDist
from typing import Any, cast

from .prospective_receipts import (
    ProspectivePredictionReceipt,
    ProspectiveReceiptError,
    ProspectiveReceiptStore,
)
from .trial_ledger import TrialLedger, TrialStatus

SCHEMA_VERSION = "fr-a3-authoritative-outcome-scoring-v2"
SCORING_POLICY_VERSION = "fr-a3-binary-v2"
LOG_LOSS_CLIP = 1e-6
_CAPABILITY = object()
_APPEND_CAPABILITY = object()


def _scoring_now() -> datetime:
    return datetime.now(UTC)


class ScoringError(ValueError):
    """A fail-closed authority, chronology, identity, or journal error."""


class OutcomeStatus(StrEnum):
    RESOLVED = "RESOLVED"
    PENDING = "PENDING"
    UNKNOWN = "UNKNOWN"
    NO_RELEASE = "NO_RELEASE"
    CANCELLED = "CANCELLED"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True, init=False)
class OutcomeEvidenceReceipt:
    source_authority: str
    artifact_sha256: str
    artifact_size: int
    artifact_locator: str
    market_ticker: str
    event_ticker: str
    underlying_event_id: str
    observation_date: str
    predicate_identity: str
    status: OutcomeStatus
    value: int | None
    published_at: datetime
    available_at: datetime
    acquired_at: datetime
    revision_policy: str
    research_only: bool
    production_influence: int
    receipt_id: str
    issuer_mac: str

    @property
    def identity(self) -> str:
        return self.receipt_id

    def __init__(self, *, _capability: object, issuer_key: bytes | None, **values: object) -> None:
        if _capability is not _CAPABILITY:
            raise ScoringError("outcome receipt requires reviewed issuer capability")
        expected = _digest({k: values[k] for k in values if k not in {"receipt_id", "issuer_mac"}})
        if values.get("receipt_id") != expected:
            raise ScoringError("outcome receipt identity mismatch")
        if issuer_key is not None:
            expected_mac = hmac.new(
                issuer_key, _canonical({**values, "issuer_mac": ""}), hashlib.sha256
            ).hexdigest()
            if values.get("issuer_mac") != expected_mac:
                raise ScoringError("outcome receipt authentication mismatch")
        for field in fields(self):
            object.__setattr__(self, field.name, values[field.name])
        _validate_outcome(self)


class OutcomeEvidenceAuthority:
    """Reviewed source/predicate boundary issuing receipts from frozen artifacts."""

    def __init__(self, root: str | Path, *, _capability: object) -> None:
        self.root = Path(root)
        if _capability is not _CAPABILITY:
            raise ScoringError("unreviewed outcome adapter")
        self.source_authority = "fr-a3-test-source-v1"
        self.predicate_identity = "binary-rule-v1"
        self.key = _load_key(self.root.with_name(self.root.name + ".issuer-key"))

    def issue(self, *, artifact: str | Path) -> OutcomeEvidenceReceipt:
        path = Path(artifact)
        if path.is_symlink() or not path.is_file():
            raise ScoringError("outcome artifact must be a regular file")
        try:
            content = path.read_bytes()
            source = _parse_outcome_artifact(content)
        except OSError as exc:
            raise ScoringError("outcome artifact unavailable") from exc
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ScoringError("outcome artifact does not match reviewed schema") from exc
        status = OutcomeStatus(cast(str, source["status"]))
        published_at = _utc(source["published_at"], "publication")
        available_at = datetime.now(UTC)
        acquired_at = available_at
        archive = self.root / "artifacts" / hashlib.sha256(content).hexdigest()
        archive.parent.mkdir(parents=True, exist_ok=True)
        if archive.exists() and archive.read_bytes() != content:
            raise ScoringError("conflicting frozen artifact")
        if not archive.exists():
            with archive.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            fd = os.open(archive.parent, os.O_RDONLY)
            os.fsync(fd)
            os.close(fd)
        values: dict[str, object] = {
            "source_authority": self.source_authority,
            "artifact_sha256": hashlib.sha256(content).hexdigest(),
            "artifact_size": len(content),
            "artifact_locator": str(archive.resolve()),
            "market_ticker": _text(source["market_ticker"], "market ticker"),
            "event_ticker": _text(source["event_ticker"], "event ticker"),
            "underlying_event_id": _text(source["underlying_event_id"], "underlying event"),
            "observation_date": _text(source["observation_date"], "observation date"),
            "predicate_identity": self.predicate_identity,
            "status": status,
            "value": source["value"],
            "published_at": published_at,
            "available_at": available_at,
            "acquired_at": acquired_at,
            "revision_policy": _text(source["revision_policy"], "revision policy"),
            "research_only": True,
            "production_influence": 0,
        }
        _validate_values(values)
        values["receipt_id"] = _digest(values)
        values["issuer_mac"] = hmac.new(
            self.key, _canonical({**values, "issuer_mac": ""}), hashlib.sha256
        ).hexdigest()
        return OutcomeEvidenceReceipt(_capability=_CAPABILITY, issuer_key=self.key, **values)

    def validate(self, receipt: OutcomeEvidenceReceipt) -> None:
        if (
            receipt.source_authority != self.source_authority
            or receipt.predicate_identity != self.predicate_identity
        ):
            raise ScoringError("outcome issuer or predicate authority mismatch")
        archive = self.root / "artifacts" / receipt.artifact_sha256
        locator = Path(receipt.artifact_locator)
        try:
            if locator != archive.resolve() or locator.is_symlink() or not locator.is_file():
                raise ScoringError("frozen outcome locator is not canonical")
            content = locator.read_bytes()
        except OSError as exc:
            raise ScoringError("frozen outcome artifact unavailable") from exc
        if (
            len(content) != receipt.artifact_size
            or hashlib.sha256(content).hexdigest() != receipt.artifact_sha256
        ):
            raise ScoringError("frozen outcome artifact changed")
        source = _parse_outcome_artifact(content)
        expected = {
            "market_ticker": receipt.market_ticker,
            "event_ticker": receipt.event_ticker,
            "underlying_event_id": receipt.underlying_event_id,
            "observation_date": receipt.observation_date,
            "status": receipt.status.value,
            "value": receipt.value,
            "published_at": receipt.published_at,
            "revision_policy": receipt.revision_policy,
        }
        if source != expected:
            raise ScoringError("outcome receipt does not rederive from frozen artifact")
        OutcomeEvidenceReceipt(
            _capability=_CAPABILITY,
            issuer_key=self.key,
            **{f.name: getattr(receipt, f.name) for f in fields(receipt)},
        )


def _parse_outcome_artifact(content: bytes) -> dict[str, object]:
    def reject_constant(value: str) -> object:
        raise ScoringError(f"non-finite JSON constant: {value}")

    def reject_duplicate(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ScoringError("duplicate outcome artifact key")
            result[key] = value
        return result

    try:
        source = json.loads(
            content, object_pairs_hook=reject_duplicate, parse_constant=reject_constant
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ScoringError("outcome artifact does not match reviewed schema") from exc
    expected_keys = {
        "market_ticker",
        "event_ticker",
        "underlying_event_id",
        "observation_date",
        "status",
        "value",
        "published_at",
        "revision_policy",
    }
    if type(source) is not dict or set(source) != expected_keys:
        raise ScoringError("outcome artifact schema mismatch")
    if type(source["status"]) is not str or type(source["value"]) not in (int, type(None)):
        raise ScoringError("outcome artifact outcome types are invalid")
    for key in (
        "market_ticker",
        "event_ticker",
        "underlying_event_id",
        "observation_date",
        "published_at",
        "revision_policy",
    ):
        if type(source[key]) is not str or not source[key]:
            raise ScoringError("outcome artifact text field is invalid")
    source["status"] = OutcomeStatus(source["status"])
    source["published_at"] = _utc(
        datetime.fromisoformat(cast(str, source["published_at"])), "publication"
    )
    _validate_values(
        {
            "status": source["status"],
            "value": source["value"],
            "published_at": source["published_at"],
            "available_at": source["published_at"],
            "acquired_at": source["published_at"],
            "research_only": True,
            "production_influence": 0,
        }
    )
    source["status"] = cast(OutcomeStatus, source["status"]).value
    return source


@dataclass(frozen=True, slots=True)
class ScoringRecord:
    trial_id: str
    forecast_receipt_id: str
    forecast_receipt_hash: str
    registration_history_identity: str
    market_ticker: str
    event_ticker: str
    underlying_event_id: str
    candidate_family: str
    model_identity: str
    calibrator_identity: str
    decision_cutoff: datetime
    scored_at: datetime
    forecast_probability: float | None
    outcome_receipt: OutcomeEvidenceReceipt
    score_status: str
    brier_score: float | None
    log_loss: float | None
    scoring_policy_version: str
    record_identity: str


@dataclass(frozen=True, slots=True)
class ReplayContext:
    outcome_authority: OutcomeEvidenceAuthority
    receipt_store: ProspectiveReceiptStore
    ledger: TrialLedger


def _jsonable(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, tuple | list):
        return [_jsonable(v) for v in value]
    if hasattr(value, "__dataclass_fields__"):
        return {f.name: _jsonable(getattr(value, f.name)) for f in fields(cast(Any, value))}
    return value


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            _jsonable(value), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    except (TypeError, ValueError) as exc:
        raise ScoringError("non-canonical value") from exc


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value:
        raise ScoringError(f"{name} must be non-empty exact text")
    return value


def _utc(value: object, name: str) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != UTC.utcoffset(value)
    ):
        raise ScoringError(f"{name} must be UTC")
    return value


def _validate_values(values: Mapping[str, object]) -> None:
    status = values["status"]
    if type(status) is not OutcomeStatus:
        raise ScoringError("invalid outcome status")
    if status is OutcomeStatus.RESOLVED and values["value"] not in (0, 1):
        raise ScoringError("invalid resolved value")
    if status is not OutcomeStatus.RESOLVED and values["value"] is not None:
        raise ScoringError("non-resolved outcome has value")
    for name in ("published_at", "available_at", "acquired_at"):
        _utc(values[name], name)
    if (
        not cast(datetime, values["published_at"])
        <= cast(datetime, values["available_at"])
        <= cast(datetime, values["acquired_at"])
    ):
        raise ScoringError("outcome chronology is invalid")
    if values["research_only"] is not True or values["production_influence"] != 0:
        raise ScoringError("outcome safety invalid")


def _validate_outcome(outcome: OutcomeEvidenceReceipt) -> None:
    _validate_values(
        {
            f.name: getattr(outcome, f.name)
            for f in fields(outcome)
            if f.name not in {"receipt_id", "issuer_mac"}
        }
    )


def _load_key(path: Path) -> bytes:
    if path.exists():
        key = path.read_bytes()
        if len(key) != 32:
            raise ScoringError("issuer key invalid")
        return key
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.write(fd, key)
    os.fsync(fd)
    os.close(fd)
    return key


class OutcomeScoringStore:
    """FR-A2-style journal/checkpoint authority; no SQLite recovery path."""

    def __init__(
        self, path: str | Path, *, _create: bool = False, context: ReplayContext | None = None
    ) -> None:
        self.path = Path(path)
        self.journal = self.path.with_name(self.path.name + ".journal")
        self.head = self.path.with_name(self.path.name + ".head")
        key_path = self.path.with_name(self.path.name + ".issuer-key")
        existed = key_path.exists()
        if not existed and not _create:
            raise ScoringError("scoring store must be explicitly created or opened")
        if existed and (not self.journal.exists() or not self.head.exists()):
            raise ScoringError("scoring genesis or journal is missing")
        self.key = _load_key(key_path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.lock_path.touch(exist_ok=True)
        self.context = context
        if not existed:
            self.journal.parent.mkdir(parents=True, exist_ok=True)
            self.journal.touch()
            head = {"schema_version": SCHEMA_VERSION, "last_sequence": 0, "last_entry_hash": ""}
            head["issuer_mac"] = hmac.new(self.key, _canonical(head), hashlib.sha256).hexdigest()
            with self.head.open("xb") as stream:
                stream.write(_canonical(head))
                stream.flush()
                os.fsync(stream.fileno())
        self._entries()

    @classmethod
    def create(cls, path: str | Path) -> OutcomeScoringStore:
        target = Path(path)
        for suffix in ("", ".journal", ".head", ".issuer-key", ".lock"):
            if Path(str(target) + suffix).exists():
                raise ScoringError("scoring store already exists")
        return cls(target, _create=True)

    @classmethod
    def open(cls, path: str | Path, *, context: ReplayContext) -> OutcomeScoringStore:
        if type(context) is not ReplayContext:
            raise ScoringError("replay requires an explicit authority context")
        return cls(path, context=context)

    def _lock(self) -> Any:
        store = self

        class Lock:
            def __enter__(self) -> Lock:
                self.fd = os.open(store.lock_path, os.O_RDWR)
                fcntl.flock(self.fd, fcntl.LOCK_EX)
                return self

            def __exit__(self, *_: object) -> None:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
                os.close(self.fd)

        return Lock()

    def append(self, record: ScoringRecord) -> None:
        raise ScoringError("public scoring append is not an authority boundary")

    def _append(self, record: ScoringRecord, *, _capability: object) -> None:
        if _capability is not _APPEND_CAPABILITY:
            raise ScoringError("scoring append requires reviewed issuance capability")
        with self._lock():
            _validate_record(record, self.context)
            entries = self._entries()
            if any(e["record"]["trial_id"] == record.trial_id for e in entries):
                raise ScoringError("trial has already been scored")
            payload = _jsonable(record)
            if (
                _digest(
                    {
                        k: v
                        for k, v in cast(Mapping[str, object], payload).items()
                        if k != "record_identity"
                    }
                )
                != record.record_identity
            ):
                raise ScoringError("record identity mismatch")
            entry: dict[str, object] = {
                "schema_version": SCHEMA_VERSION,
                "sequence": len(entries) + 1,
                "previous_entry_hash": entries[-1]["entry_hash"] if entries else "",
                "record": payload,
            }
            entry["entry_hash"] = _digest(entry)
            entry["issuer_mac"] = hmac.new(self.key, _canonical(entry), hashlib.sha256).hexdigest()
            with self.journal.open("ab") as stream:
                stream.write(_canonical(entry) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            head = {
                "schema_version": SCHEMA_VERSION,
                "last_sequence": entry["sequence"],
                "last_entry_hash": entry["entry_hash"],
            }
            head["issuer_mac"] = hmac.new(self.key, _canonical(head), hashlib.sha256).hexdigest()
            tmp = self.head.with_name(self.head.name + ".tmp")
            with tmp.open("wb") as stream:
                stream.write(_canonical(head))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, self.head)
            self._entries()

    @property
    def records(self) -> tuple[ScoringRecord, ...]:
        if self._entries() and self.context is None:
            raise ScoringError("non-empty replay requires full authority context")
        return tuple(
            _decode(e["record"], self.key, cast(ReplayContext, self.context))
            for e in self._entries()
        )

    def _entries(self) -> list[dict[str, Any]]:
        if not self.journal.exists() and not self.head.exists():
            return []
        if not self.journal.exists() or not self.head.exists():
            raise ScoringError("journal/checkpoint incomplete")
        try:
            head = json.loads(self.head.read_bytes())
            mac = head.pop("issuer_mac")
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise ScoringError("checkpoint corrupt") from exc
        if not hmac.compare_digest(
            mac, hmac.new(self.key, _canonical(head), hashlib.sha256).hexdigest()
        ):
            raise ScoringError("checkpoint authentication mismatch")
        result: list[dict[str, Any]] = []
        try:
            for line in self.journal.read_bytes().splitlines():
                item = json.loads(line)
                saved_hash = item.pop("entry_hash")
                saved_mac = item.pop("issuer_mac")
                if (
                    item["schema_version"] != SCHEMA_VERSION
                    or item["sequence"] != len(result) + 1
                    or item["previous_entry_hash"] != (result[-1]["entry_hash"] if result else "")
                    or saved_hash != _digest(item)
                    or not hmac.compare_digest(
                        saved_mac,
                        hmac.new(
                            self.key, _canonical({**item, "entry_hash": saved_hash}), hashlib.sha256
                        ).hexdigest(),
                    )
                ):
                    raise ScoringError("journal authentication mismatch")
                item["entry_hash"] = saved_hash
                result.append(item)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ScoringError("journal corrupt") from exc
        if head["last_sequence"] != len(result) or head["last_entry_hash"] != (
            result[-1]["entry_hash"] if result else ""
        ):
            raise ScoringError("checkpoint disagrees with journal")
        return result


def _decode(value: Mapping[str, Any], key: bytes, context: ReplayContext) -> ScoringRecord:
    raw = dict(cast(Mapping[str, Any], value["outcome_receipt"]))
    for name in ("published_at", "available_at", "acquired_at"):
        raw[name] = datetime.fromisoformat(raw[name])
    raw["status"] = OutcomeStatus(raw["status"])
    value = dict(value)
    value["decision_cutoff"] = datetime.fromisoformat(cast(str, value["decision_cutoff"]))
    value["scored_at"] = datetime.fromisoformat(cast(str, value["scored_at"]))
    result = ScoringRecord(
        **{
            **value,
            "outcome_receipt": OutcomeEvidenceReceipt(
                _capability=_CAPABILITY, issuer_key=context.outcome_authority.key, **raw
            ),
            "scored_at": value["scored_at"],
        }
    )
    payload = cast(Mapping[str, object], _jsonable(result))
    if (
        _digest({k: v for k, v in payload.items() if k != "record_identity"})
        != result.record_identity
    ):
        raise ScoringError("replayed record identity mismatch")
    _validate_record(result, context)
    return result


def _validate_record(record: ScoringRecord, context: ReplayContext | None) -> None:
    if type(record) is not ScoringRecord:
        raise ScoringError("record type is not recognized")
    if any(
        type(getattr(record, name)) is not str or not getattr(record, name)
        for name in (
            "trial_id",
            "forecast_receipt_id",
            "forecast_receipt_hash",
            "registration_history_identity",
            "market_ticker",
            "event_ticker",
            "underlying_event_id",
            "candidate_family",
            "model_identity",
            "calibrator_identity",
            "scoring_policy_version",
            "record_identity",
        )
    ):
        raise ScoringError("record identity fields are invalid")
    _utc(record.decision_cutoff, "decision cutoff")
    _utc(record.scored_at, "scored at")
    if record.scoring_policy_version != SCORING_POLICY_VERSION:
        raise ScoringError("scoring policy version is not recognized")
    if record.outcome_receipt.receipt_id != record.outcome_receipt.identity:
        raise ScoringError("outcome receipt identity is invalid")
    receipt_payload = {
        f.name: getattr(record.outcome_receipt, f.name)
        for f in fields(record.outcome_receipt)
        if f.name not in {"receipt_id", "issuer_mac"}
    }
    if _digest(receipt_payload) != record.outcome_receipt.receipt_id:
        raise ScoringError("outcome receipt content identity is invalid")
    if record.score_status not in {
        "SCORED",
        "ABSTAINED",
        "PENDING",
        "UNKNOWN",
        "NO_RELEASE",
        "CANCELLED",
        "INVALID",
        "TERMINAL_UNSCORED",
    }:
        raise ScoringError("score status is not recognized")
    if record.outcome_receipt.status is OutcomeStatus.RESOLVED and record.score_status not in {
        "SCORED",
        "ABSTAINED",
        "TERMINAL_UNSCORED",
    }:
        raise ScoringError("resolved outcome has an invalid score status")
    if record.outcome_receipt.status is not OutcomeStatus.RESOLVED and record.score_status not in {
        record.outcome_receipt.status.value,
        "ABSTAINED",
        "TERMINAL_UNSCORED",
    }:
        raise ScoringError("unresolved outcome has an invalid score status")
    if record.forecast_probability is not None:
        _probability(record.forecast_probability)
    resolved = record.outcome_receipt.status is OutcomeStatus.RESOLVED
    should_score = (
        resolved and record.score_status == "SCORED" and record.forecast_probability is not None
    )
    if record.score_status == "SCORED" and not should_score:
        raise ScoringError("SCORED record is not resolved and non-abstained")
    if record.score_status == "ABSTAINED" and (
        record.forecast_probability is not None
        or record.brier_score is not None
        or record.log_loss is not None
    ):
        raise ScoringError("abstained record contains scores")
    if not should_score and (record.brier_score is not None or record.log_loss is not None):
        raise ScoringError("unscored record contains scores")
    if should_score:
        brier, log_loss = _scores(
            record.forecast_probability, cast(int, record.outcome_receipt.value)
        )
        if record.brier_score != brier or record.log_loss != log_loss:
            raise ScoringError("scores do not recompute from frozen inputs")
    payload = cast(Mapping[str, object], _jsonable(record))
    if (
        _digest({k: v for k, v in payload.items() if k != "record_identity"})
        != record.record_identity
    ):
        raise ScoringError("record identity does not match content")
    if context is not None:
        context.outcome_authority.validate(record.outcome_receipt)
        trial = context.ledger.get(record.trial_id)
        history = _digest(
            {
                "definition": trial.definition.content_hash,
                "events": [e.content_hash for e in context.ledger.status_events(record.trial_id)],
            }
        )
        if history != record.registration_history_identity:
            raise ScoringError("registration history mismatch")
        forecast = context.receipt_store.read_receipt(record.forecast_receipt_id)
        if forecast.content_hash != record.forecast_receipt_hash:
            raise ScoringError("forecast receipt identity mismatch")
        if (
            forecast.market_ticker != record.market_ticker
            or forecast.event_ticker != record.event_ticker
            or forecast.underlying_event_id != record.underlying_event_id
            or forecast.decision_at != record.decision_cutoff
            or forecast.calibrator_id != record.calibrator_identity
        ):
            raise ScoringError("replayed forecast identity mismatch")
        if trial.definition.underlying_event_id != record.underlying_event_id:
            raise ScoringError("replayed trial identity mismatch")
        if record.market_ticker not in trial.definition.sibling_market_ids:
            raise ScoringError("replayed market identity mismatch")
        plan = trial.definition.evaluation_plan.value
        if plan.get("event_ticker") not in (None, record.event_ticker):
            raise ScoringError("replayed event identity mismatch")
        if plan.get("predicate_identity") != record.outcome_receipt.predicate_identity:
            raise ScoringError("replayed predicate identity mismatch")
        if (
            trial.status in (TrialStatus.FAILED, TrialStatus.ABANDONED)
            and record.score_status != "TERMINAL_UNSCORED"
        ):
            raise ScoringError("terminal trial status mismatch")
        if record.score_status == "TERMINAL_UNSCORED" and trial.status not in (
            TrialStatus.FAILED,
            TrialStatus.ABANDONED,
        ):
            raise ScoringError("terminal score lacks terminal trial state")
        if trial.definition.created_at > record.decision_cutoff:
            raise ScoringError("replayed registration chronology is invalid")
        publication = context.receipt_store.read_publication(forecast)
        outcome = record.outcome_receipt
        if not (
            record.decision_cutoff
            <= publication.published_at
            < outcome.published_at
            <= outcome.available_at
            <= outcome.acquired_at
            <= record.scored_at
        ):
            raise ScoringError("replayed outcome chronology is invalid")
        if publication.published_at >= record.outcome_receipt.published_at:
            raise ScoringError("replayed forecast publication chronology is invalid")


def score_trial(
    *,
    ledger: TrialLedger,
    receipt_store: ProspectiveReceiptStore,
    scoring_store: OutcomeScoringStore,
    outcome_authority: OutcomeEvidenceAuthority,
    trial_id: str,
    receipt: ProspectivePredictionReceipt,
    outcome: OutcomeEvidenceReceipt,
) -> ScoringRecord:
    if type(outcome) is not OutcomeEvidenceReceipt:
        raise ScoringError("outcome is not an authenticated evidence receipt")
    outcome_authority.validate(outcome)
    trial = ledger.get(trial_id)
    definition = trial.definition
    plan = definition.evaluation_plan.value
    if trial.status in (TrialStatus.PLANNED, TrialStatus.RUNNING):
        raise ScoringError("trial is not terminal")
    if trial.status in (TrialStatus.FAILED, TrialStatus.ABANDONED):
        status = "TERMINAL_UNSCORED"
    elif outcome.status in (OutcomeStatus.PENDING, OutcomeStatus.UNKNOWN):
        raise ScoringError("outcome is not final")
    elif outcome.status is OutcomeStatus.RESOLVED:
        status = "ABSTAINED" if receipt.abstained else "SCORED"
    else:
        status = "TERMINAL_UNSCORED"
    if not definition.research_only or definition.production_influence != 0:
        raise ScoringError("trial safety invalid")
    if receipt.decision_at < definition.created_at:
        raise ScoringError("forecast precedes registration")
    if (
        outcome.market_ticker != receipt.market_ticker
        or outcome.market_ticker not in definition.sibling_market_ids
        or outcome.event_ticker != receipt.event_ticker
        or outcome.underlying_event_id != definition.underlying_event_id
    ):
        raise ScoringError("outcome identity mismatch")
    if plan.get("predicate_identity") is None:
        raise ScoringError("evaluation plan lacks predicate identity")
    if any(
        plan.get(k) not in (None, getattr(outcome, attr))
        for k, attr in (
            ("event_ticker", "event_ticker"),
            ("market_ticker", "market_ticker"),
            ("predicate_identity", "predicate_identity"),
        )
    ):
        raise ScoringError("evaluation identity mismatch")
    if outcome.published_at <= definition.created_at:
        raise ScoringError("outcome not published after registration")
    try:
        publication = receipt_store.require_frozen(receipt, outcome.available_at)
    except ProspectiveReceiptError as exc:
        raise ScoringError(str(exc)) from exc
    p = None if receipt.abstained else _probability(receipt.calibrated_probability)
    brier = log_loss = None
    if status == "SCORED":
        brier, log_loss = _scores(p, cast(int, outcome.value))
    history = _digest(
        {
            "definition": definition.content_hash,
            "events": [e.content_hash for e in ledger.status_events(trial_id)],
        }
    )
    scored_at = _utc(_scoring_now(), "scored_at")
    if receipt.decision_at > publication.published_at:
        raise ScoringError("forecast decision is after authenticated publication")
    if outcome.acquired_at > scored_at:
        raise ScoringError("outcome was acquired after scoring time")
    if publication.published_at >= outcome.published_at:
        raise ScoringError("forecast publication is not before outcome publication")
    values = {
        "trial_id": trial_id,
        "forecast_receipt_id": receipt.receipt_id,
        "forecast_receipt_hash": receipt.content_hash,
        "registration_history_identity": history,
        "market_ticker": receipt.market_ticker,
        "event_ticker": receipt.event_ticker,
        "underlying_event_id": receipt.underlying_event_id,
        "candidate_family": definition.candidate_family,
        "model_identity": definition.model_identity,
        "calibrator_identity": receipt.calibrator_id,
        "decision_cutoff": receipt.decision_at,
        "scored_at": scored_at,
        "forecast_probability": p,
        "outcome_receipt": outcome,
        "score_status": status,
        "brier_score": brier,
        "log_loss": log_loss,
        "scoring_policy_version": SCORING_POLICY_VERSION,
    }
    record = ScoringRecord(**values, record_identity=_digest(values))  # type: ignore[arg-type]
    scoring_store._append(record, _capability=_APPEND_CAPABILITY)
    return record


def _probability(value: object) -> float:
    if (
        type(value) not in (int, float, Decimal)
        or not math.isfinite(float(value))  # type: ignore[arg-type]
        or not 0 <= float(value) <= 1  # type: ignore[arg-type]
    ):
        raise ScoringError("probability malformed")
    return float(value)  # type: ignore[arg-type]


def _scores(probability: float | None, outcome: int) -> tuple[float, float]:
    if probability is None:
        raise ScoringError("missing forecast probability")
    p = min(1 - LOG_LOSS_CLIP, max(LOG_LOSS_CLIP, probability))
    return (probability - outcome) ** 2, -(outcome * math.log(p) + (1 - outcome) * math.log(1 - p))


def confidence_interval(
    values: list[float] | tuple[float, ...], confidence: float = 0.95
) -> tuple[float, float] | None:
    if (
        type(confidence) not in (int, float)
        or not math.isfinite(float(confidence))
        or not 0 < confidence < 1
    ):
        raise ScoringError("confidence must be between zero and one")
    if any(type(value) not in (int, float) or not math.isfinite(float(value)) for value in values):
        raise ScoringError("confidence values must be finite")
    if not values:
        return None
    mean = sum(values) / len(values)
    if len(values) == 1:
        return mean, mean
    margin = (
        NormalDist().inv_cdf((1 + confidence) / 2)
        * math.sqrt(sum((x - mean) ** 2 for x in values) / (len(values) - 1))
        / math.sqrt(len(values))
    )
    return mean - margin, mean + margin


def event_equal_aggregation(
    records: list[ScoringRecord] | tuple[ScoringRecord, ...],
) -> dict[str, object]:
    if not records:
        return {
            "event_count": 0,
            "scored_trial_count": 0,
            "abstained_trial_count": 0,
            "abstained_event_count": 0,
            "scored_event_count": 0,
        }
    population = (
        records[0].candidate_family,
        records[0].model_identity,
        records[0].calibrator_identity,
    )
    groups: dict[str, list[ScoringRecord]] = {}
    seen_records: set[str] = set()
    seen_trials: set[str] = set()
    for record in records:
        _validate_record(record, None)
        if record.record_identity in seen_records or record.trial_id in seen_trials:
            raise ScoringError("duplicate scoring identity")
        seen_records.add(record.record_identity)
        seen_trials.add(record.trial_id)
        if (
            record.candidate_family,
            record.model_identity,
            record.calibrator_identity,
        ) != population:
            raise ScoringError("mixed populations require explicit grouping")
        if record.score_status == "SCORED":
            groups.setdefault(record.underlying_event_id, []).append(record)
    brier = [
        sum(float(cast(float, r.brier_score)) for r in group) / len(group)
        for group in groups.values()
    ]
    logs = [
        sum(float(cast(float, r.log_loss)) for r in group) / len(group) for group in groups.values()
    ]
    abstained_trials = [r for r in records if r.score_status == "ABSTAINED"]
    abstained_events = {r.underlying_event_id for r in abstained_trials}
    return {
        "event_count": len(groups),
        "scored_event_count": len(groups),
        "scored_trial_count": sum(map(len, groups.values())),
        "abstained_trial_count": len(abstained_trials),
        "abstained_event_count": len(abstained_events),
        "abstention_count": len(abstained_trials),
        "brier_mean": None if not brier else sum(brier) / len(brier),
        "log_loss_mean": None if not logs else sum(logs) / len(logs),
        "brier_confidence_interval": confidence_interval(brier),
        "log_loss_confidence_interval": confidence_interval(logs),
    }


def calibration_buckets(
    records: list[ScoringRecord] | tuple[ScoringRecord, ...], bucket_count: int = 10
) -> tuple[dict[str, object], ...]:
    if type(bucket_count) is not int or bucket_count < 1:
        raise ScoringError("bucket count invalid")
    buckets: list[list[ScoringRecord]] = [[] for _ in range(bucket_count)]
    for record in records:
        if record.score_status == "SCORED" and record.forecast_probability is not None:
            index = min(bucket_count - 1, int(record.forecast_probability * bucket_count))
            buckets[index].append(record)
    result: list[dict[str, object]] = []
    for index, group in enumerate(buckets):
        event_ids = {record.underlying_event_id for record in group}
        result.append(
            {
                "bucket": index,
                "count": len(group),
                "event_count": len(event_ids),
                "mean_forecast": None
                if not group
                else sum(cast(float, r.forecast_probability) for r in group) / len(group),
                "observed_rate": None
                if not group
                else sum(cast(int, r.outcome_receipt.value) for r in group) / len(group),
            }
        )
    return tuple(result)
