"""Append-only, content-addressed registration of research trials.

This module deliberately has no outcome, forecast, execution, or promotion
authority.  A trial must exist here before any separate system may score it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import cast


class LedgerError(ValueError):
    """Base error for fail-closed ledger violations."""


class TrialStatus(StrEnum):
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABANDONED = "ABANDONED"


class EvaluationPlan:
    """A frozen, canonical evaluation specification."""

    __slots__ = ("_value", "identity")

    def __init__(self, value: Mapping[str, object]) -> None:
        if not isinstance(value, Mapping) or not value:
            raise LedgerError("evaluation plan must be a non-empty mapping")
        frozen = _freeze(value)
        encoded = _canonical_json(frozen)
        self._value = frozen
        self.identity = hashlib.sha256(encoded).hexdigest()

    def __setattr__(self, name: str, value: object) -> None:
        if hasattr(self, name):
            raise AttributeError("evaluation plan is frozen")
        object.__setattr__(self, name, value)

    @property
    def value(self) -> Mapping[str, object]:
        return cast(Mapping[str, object], self._value)


@dataclass(frozen=True, slots=True)
class Trial:
    trial_id: str
    created_at: datetime
    candidate_family: str
    model_identity: str
    feature_specification_identity: str
    evaluation_plan: EvaluationPlan
    parent_trial_ids: tuple[str, ...]
    reason: str
    status: TrialStatus
    underlying_event_id: str
    sibling_market_ids: tuple[str, ...]
    research_only: bool = True
    production_influence: int = 0
    content_hash: str = field(default="")


@dataclass(frozen=True, slots=True)
class TrialEvent:
    trial_id: str
    status: TrialStatus
    recorded_at: datetime


Clock = Callable[[], datetime]


class TrialLedger:
    """An in-memory append-only trial ledger.

    ``issuer_clock`` is owned by the ledger boundary.  Callers cannot provide
    ``created_at``; timestamps are obtained only by invoking this trusted
    issuer.  Persistence, if added later, must retain both trial records and
    status events without update/delete semantics.
    """

    def __init__(self, *, issuer_clock: Clock) -> None:
        self._issuer_clock = issuer_clock
        self._trials: dict[str, Trial] = {}
        self._identities: dict[str, str] = {}
        self._events: list[TrialEvent] = []

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
        """Register before scoring; all definition fields are immutable."""
        self._validate_text(candidate_family, "candidate_family")
        self._validate_text(model_identity, "model_identity")
        self._validate_text(feature_specification_identity, "feature_specification_identity")
        self._validate_text(underlying_event_id, "underlying_event_id")
        self._validate_text(reason, "reason")
        if not isinstance(evaluation_plan, EvaluationPlan):
            raise LedgerError("evaluation plan must be an EvaluationPlan")
        siblings = tuple(sorted(set(sibling_market_ids)))
        if any(not isinstance(item, str) or not item for item in siblings):
            raise LedgerError("sibling market identities must be non-empty strings")
        parents = tuple(sorted(set(parent_trial_ids)))
        if any(parent not in self._trials for parent in parents):
            raise LedgerError("parent trial is not registered")

        created_at = self._trusted_time()
        identity_material = {
            "candidate_family": candidate_family,
            "model_identity": model_identity,
            "feature_specification_identity": feature_specification_identity,
            "evaluation_plan": evaluation_plan.identity,
            "underlying_event_id": underlying_event_id,
            "sibling_market_ids": siblings,
            "parent_trial_ids": parents,
            "reason": reason,
        }
        identity = _digest(identity_material)
        if identity in self._identities:
            raise LedgerError("duplicate trial identity; use the existing trial")
        trial_id = f"trial-{identity}"
        trial = Trial(
            trial_id=trial_id,
            created_at=created_at,
            candidate_family=candidate_family,
            model_identity=model_identity,
            feature_specification_identity=feature_specification_identity,
            evaluation_plan=evaluation_plan,
            parent_trial_ids=parents,
            reason=reason,
            status=TrialStatus.PLANNED,
            underlying_event_id=underlying_event_id,
            sibling_market_ids=siblings,
            content_hash=_digest({"trial_id": trial_id, **identity_material}),
        )
        self._trials[trial_id] = trial
        self._identities[identity] = trial_id
        self._events.append(TrialEvent(trial_id, TrialStatus.PLANNED, created_at))
        return trial

    def advance(self, trial_id: str, status: TrialStatus) -> Trial:
        """Append a status event; definitions and trial_id never change."""
        trial = self._trials.get(trial_id)
        if trial is None:
            raise LedgerError("unknown trial")
        if not isinstance(status, TrialStatus):
            raise LedgerError("invalid trial status")
        if trial.status in (TrialStatus.COMPLETED, TrialStatus.FAILED, TrialStatus.ABANDONED):
            raise LedgerError("terminal trial cannot be rewritten")
        allowed = {
            TrialStatus.PLANNED: {TrialStatus.RUNNING, TrialStatus.FAILED, TrialStatus.ABANDONED},
            TrialStatus.RUNNING: {TrialStatus.COMPLETED, TrialStatus.FAILED, TrialStatus.ABANDONED},
        }
        if status not in allowed[trial.status]:
            raise LedgerError(f"invalid status transition {trial.status} -> {status}")
        recorded_at = self._trusted_time()
        updated = Trial(
            trial_id=trial.trial_id,
            created_at=trial.created_at,
            candidate_family=trial.candidate_family,
            model_identity=trial.model_identity,
            feature_specification_identity=trial.feature_specification_identity,
            evaluation_plan=trial.evaluation_plan,
            parent_trial_ids=trial.parent_trial_ids,
            reason=trial.reason,
            status=status,
            underlying_event_id=trial.underlying_event_id,
            sibling_market_ids=trial.sibling_market_ids,
            content_hash=trial.content_hash,
        )
        self._trials[trial_id] = updated
        self._events.append(TrialEvent(trial_id, status, recorded_at))
        return updated

    def get(self, trial_id: str) -> Trial:
        try:
            return self._trials[trial_id]
        except KeyError as exc:
            raise LedgerError("unknown trial") from exc

    def trials_for_event(self, underlying_event_id: str) -> tuple[Trial, ...]:
        """Return all sibling-market attempts sharing one real-world event."""
        return tuple(
            t for t in self._trials.values() if t.underlying_event_id == underlying_event_id
        )

    def independent_trial_count(self, underlying_event_id: str) -> int:
        """Count event groups, never threshold-market tickers."""
        return int(bool(self.trials_for_event(underlying_event_id)))

    @property
    def events(self) -> tuple[TrialEvent, ...]:
        return tuple(self._events)

    def _trusted_time(self) -> datetime:
        value = self._issuer_clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise LedgerError("trusted issuer clock must return timezone-aware datetime")
        return value.astimezone(UTC)

    @staticmethod
    def _validate_text(value: str, name: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise LedgerError(f"{name} must be non-empty text")


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                str(key): _freeze(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            }
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise LedgerError("evaluation plan contains unsupported value")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        _jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()


def _jsonable(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(_freeze(value))).hexdigest()
