"""Authenticated, research-only scoring of authoritative prospective outcomes.

The receipt archive and :class:`TrialLedger` remain the authorities for forecast
publication and trial registration.  This module only binds a later, immutable
outcome evidence unit to both authorities and derives forecast scores.  It has
no network, clock, trading, sizing, or production-influence path.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import secrets
from collections.abc import Mapping
from dataclasses import asdict, dataclass
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

SCHEMA_VERSION = "fr-a3-authoritative-outcome-scoring-v1"
SCORING_POLICY_VERSION = "fr-a3-binary-v1"
LOG_LOSS_CLIP = 1e-6


class ScoringError(ValueError):
    """An outcome, authority, or append-only scoring record is invalid."""


class OutcomeStatus(StrEnum):
    RESOLVED = "RESOLVED"
    PENDING = "PENDING"
    UNKNOWN = "UNKNOWN"
    NO_RELEASE = "NO_RELEASE"
    CANCELLED = "CANCELLED"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class AuthoritativeOutcome:
    status: OutcomeStatus
    value: int | None
    source: str
    source_artifact_id: str
    observation_date: str
    published_at: datetime
    acquired_at: datetime
    settlement_rule: str
    revision_policy: str
    available_at: datetime
    identity: str

    @classmethod
    def create(
        cls,
        *,
        status: OutcomeStatus,
        value: int | None,
        source: str,
        source_artifact_id: str,
        observation_date: str,
        published_at: datetime,
        acquired_at: datetime,
        settlement_rule: str,
        revision_policy: str,
        available_at: datetime,
    ) -> AuthoritativeOutcome:
        fields = {
            "status": status,
            "value": value,
            "source": source,
            "source_artifact_id": source_artifact_id,
            "observation_date": observation_date,
            "published_at": published_at,
            "acquired_at": acquired_at,
            "settlement_rule": settlement_rule,
            "revision_policy": revision_policy,
            "available_at": available_at,
        }
        return cls(**fields, identity=_hash(fields))  # type: ignore[arg-type]

    def __post_init__(self) -> None:
        if type(self.status) is not OutcomeStatus:
            raise ScoringError("outcome status is invalid")
        if self.status is OutcomeStatus.RESOLVED and self.value not in (0, 1):
            raise ScoringError("resolved binary outcome must be 0 or 1")
        if self.status is not OutcomeStatus.RESOLVED and self.value is not None:
            raise ScoringError("non-resolved outcome cannot have a value")
        for text_value, name in (
            (self.source, "source"),
            (self.source_artifact_id, "source_artifact_id"),
            (self.observation_date, "observation_date"),
            (self.settlement_rule, "settlement_rule"),
            (self.revision_policy, "revision_policy"),
            (self.identity, "identity"),
        ):
            if type(text_value) is not str or not text_value:
                raise ScoringError(f"{name} must be non-empty exact text")
        for timestamp_value, name in (
            (self.published_at, "published_at"),
            (self.acquired_at, "acquired_at"),
            (self.available_at, "available_at"),
        ):
            if (
                type(timestamp_value) is not datetime
                or timestamp_value.tzinfo is None
                or timestamp_value.utcoffset() != UTC.utcoffset(timestamp_value)
            ):
                raise ScoringError(f"{name} must be canonical UTC")
        if self.available_at > self.published_at or self.acquired_at < self.published_at:
            raise ScoringError("outcome acquisition chronology is invalid")
        expected = _hash(
            {
                k: getattr(self, k)
                for k in (
                    "status",
                    "value",
                    "source",
                    "source_artifact_id",
                    "observation_date",
                    "published_at",
                    "acquired_at",
                    "settlement_rule",
                    "revision_policy",
                    "available_at",
                )
            }
        )
        if self.identity != expected:
            raise ScoringError("outcome identity does not match its immutable fields")


@dataclass(frozen=True, slots=True)
class ScoringRecord:
    trial_id: str
    forecast_receipt_id: str
    forecast_receipt_hash: str
    registration_history_identity: str
    market_ticker: str
    event_ticker: str
    underlying_event_id: str
    decision_cutoff: datetime
    forecast_probability: float | None
    forecast_distribution: Mapping[str, float] | None
    outcome: AuthoritativeOutcome
    score_status: str
    brier_score: float | None
    log_loss: float | None
    market_brier_delta: float | None
    market_log_loss_delta: float | None
    scoring_policy_version: str
    record_identity: str
    issuer_mac: str


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
        return {k: _jsonable(v) for k, v in asdict(cast(Any, value)).items()}
    return value


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            _jsonable(value), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    except (TypeError, ValueError) as exc:
        raise ScoringError("value is not canonical JSON") from exc


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _parse_probability(value: object, name: str) -> float:
    if (
        type(value) not in (float, int, Decimal)
        or not math.isfinite(float(value))  # type: ignore[arg-type]
        or not 0 <= float(value) <= 1  # type: ignore[arg-type]
    ):
        raise ScoringError(f"{name} must be a finite probability in [0, 1]")
    return float(value)  # type: ignore[arg-type]


def _score(probability: float, outcome: int) -> tuple[float, float]:
    p = min(1 - LOG_LOSS_CLIP, max(LOG_LOSS_CLIP, probability))
    return (probability - outcome) ** 2, -(outcome * math.log(p) + (1 - outcome) * math.log(1 - p))


class OutcomeScoringStore:
    """HMAC-authenticated append-only journal; its SQLite index is disposable."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.journal = self.path.with_name(self.path.name + ".journal")
        self.head = self.path.with_name(self.path.name + ".head")
        self.key_path = self.path.with_name(self.path.name + ".issuer-key")
        self.key = self._load_key()
        self._verify()

    def append(self, record: ScoringRecord) -> None:
        if any(r.trial_id == record.trial_id for r in self.records):
            raise ScoringError("trial has already been scored")
        if record.record_identity in {r.record_identity for r in self.records}:
            raise ScoringError("duplicate scoring record")
        entries = self._entries()
        entry = {
            "schema_version": SCHEMA_VERSION,
            "sequence": len(entries) + 1,
            "previous_entry_hash": entries[-1]["entry_hash"] if entries else "",
            "record": _jsonable(record),
            "record_identity": record.record_identity,
        }
        entry["entry_hash"] = _hash(entry)
        entry["issuer_mac"] = hmac.new(self.key, _canonical(entry), hashlib.sha256).hexdigest()
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        with self.journal.open("ab") as stream:
            stream.write(_canonical(entry) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        self._verify()

    @property
    def records(self) -> tuple[ScoringRecord, ...]:
        return tuple(self._decode(e["record"]) for e in self._entries())

    def _load_key(self) -> bytes:
        if self.key_path.exists():
            key = self.key_path.read_bytes()
            if len(key) != 32:
                raise ScoringError("scoring issuer key is invalid")
            return key
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_bytes(32)
        fd = os.open(self.key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(fd, key)
        os.fsync(fd)
        os.close(fd)
        return key

    def _entries(self) -> list[dict[str, Any]]:
        if not self.journal.exists():
            return []
        result: list[dict[str, Any]] = []
        try:
            for line in self.journal.read_bytes().splitlines():
                item = json.loads(line)
                if item["schema_version"] != SCHEMA_VERSION or item["sequence"] != len(result) + 1:
                    raise ScoringError("scoring journal sequence or schema mismatch")
                prior = result[-1]["entry_hash"] if result else ""
                mac = item.pop("issuer_mac")
                entry_hash = item.pop("entry_hash")
                if (
                    item["previous_entry_hash"] != prior
                    or entry_hash != _hash(item)
                    or not hmac.compare_digest(
                        mac,
                        hmac.new(
                            self.key, _canonical({**item, "entry_hash": entry_hash}), hashlib.sha256
                        ).hexdigest(),
                    )
                ):
                    raise ScoringError("scoring journal authentication mismatch")
                item["entry_hash"] = entry_hash
                item["issuer_mac"] = mac
                result.append(item)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ScoringError("scoring journal is corrupt") from exc
        return result

    def _verify(self) -> None:
        self._entries()

    @staticmethod
    def _decode(value: Mapping[str, Any]) -> ScoringRecord:
        outcome = AuthoritativeOutcome(
            **{
                **value["outcome"],
                "status": OutcomeStatus(value["outcome"]["status"]),
                **{
                    k: datetime.fromisoformat(value["outcome"][k])
                    for k in ("published_at", "acquired_at", "available_at")
                },
            }
        )
        return ScoringRecord(
            **{
                **value,
                "outcome": outcome,
                "decision_cutoff": datetime.fromisoformat(value["decision_cutoff"]),
            }
        )


def score_trial(
    *,
    ledger: TrialLedger,
    receipt_store: ProspectiveReceiptStore,
    scoring_store: OutcomeScoringStore,
    trial_id: str,
    receipt: ProspectivePredictionReceipt,
    outcome: AuthoritativeOutcome,
    market_baseline_probability: float | None = None,
) -> ScoringRecord:
    """Validate authority and chronology, derive scores, and append exactly once."""
    trial = ledger.get(trial_id)
    definition = trial.definition
    if (
        definition.trial_id != trial_id
        or definition.research_only is not True
        or definition.production_influence != 0
    ):
        raise ScoringError("trial safety identity is invalid")
    if trial.status in (TrialStatus.FAILED, TrialStatus.ABANDONED):
        status = "TERMINAL_UNSCORED"
    elif trial.status not in (TrialStatus.PLANNED, TrialStatus.RUNNING, TrialStatus.COMPLETED):
        raise ScoringError("trial status is invalid")
    else:
        status = "SCORED" if outcome.status is OutcomeStatus.RESOLVED else str(outcome.status.value)
    plan = definition.evaluation_plan.value
    if (
        receipt.underlying_event_id != definition.underlying_event_id
        or receipt.market_ticker not in definition.sibling_market_ids
    ):
        raise ScoringError("forecast and registration identity mismatch")
    if plan.get("event_ticker") is not None and plan["event_ticker"] != receipt.event_ticker:
        raise ScoringError("event identity mismatch")
    if plan.get("market_ticker") is not None and plan["market_ticker"] != receipt.market_ticker:
        raise ScoringError("market identity mismatch")
    if receipt.decision_at < definition.created_at:
        raise ScoringError("forecast is before registration")
    if outcome.published_at <= definition.created_at:
        raise ScoringError("outcome was not published after registration")
    try:
        receipt_store.require_frozen(receipt, outcome.available_at)
    except ProspectiveReceiptError as exc:
        raise ScoringError(str(exc)) from exc
    probability = receipt.calibrated_probability
    p = None if probability is None else _parse_probability(probability, "forecast probability")
    baseline = (
        None
        if market_baseline_probability is None
        else _parse_probability(market_baseline_probability, "market baseline probability")
    )
    brier = log_loss = bdelta = ldelta = None
    if outcome.status is OutcomeStatus.RESOLVED and not receipt.abstained:
        if p is None:
            raise ScoringError("resolved score requires a forecast probability")
        brier, log_loss = _score(p, cast(int, outcome.value))
        if baseline is not None:
            mb, ml = _score(baseline, cast(int, outcome.value))
            bdelta = brier - mb
            ldelta = log_loss - ml
    history = _hash(
        {
            "trial": definition.content_hash,
            "events": [asdict(e) for e in ledger.status_events(trial_id)],
        }
    )
    identity_values = {
        "trial_id": trial_id,
        "receipt": receipt.content_hash,
        "history": history,
        "outcome": outcome.identity,
        "policy": SCORING_POLICY_VERSION,
    }
    record_identity = _hash(identity_values)
    record = ScoringRecord(
        trial_id,
        receipt.receipt_id,
        receipt.content_hash,
        history,
        receipt.market_ticker,
        receipt.event_ticker,
        receipt.underlying_event_id,
        receipt.decision_at,
        p,
        None,
        outcome,
        status,
        brier,
        log_loss,
        bdelta,
        ldelta,
        SCORING_POLICY_VERSION,
        record_identity,
        "",
    )
    scoring_store.append(record)
    return record


def confidence_interval(
    values: list[float] | tuple[float, ...], confidence: float = 0.95
) -> tuple[float, float] | None:
    """Normal mean interval; returns none for an empty or singleton sample."""
    if not values:
        return None
    if type(confidence) not in (float, int) or not 0 < confidence < 1:
        raise ScoringError("confidence is invalid")
    mean = sum(values) / len(values)
    if len(values) == 1:
        return (mean, mean)
    sd = math.sqrt(sum((x - mean) ** 2 for x in values) / (len(values) - 1))
    z = NormalDist().inv_cdf((1 + confidence) / 2)
    margin = z * sd / math.sqrt(len(values))
    return mean - margin, mean + margin


def calibration_buckets(
    records: list[ScoringRecord] | tuple[ScoringRecord, ...], bucket_count: int = 10
) -> tuple[dict[str, object], ...]:
    """Return reliability buckets for resolved, non-abstained binary forecasts."""
    if type(bucket_count) is not int or bucket_count < 1:
        raise ScoringError("bucket count is invalid")
    buckets: list[list[ScoringRecord]] = [[] for _ in range(bucket_count)]
    for record in records:
        if record.score_status != "SCORED" or record.forecast_probability is None:
            continue
        index = min(bucket_count - 1, int(record.forecast_probability * bucket_count))
        buckets[index].append(record)
    result: list[dict[str, object]] = []
    for index, values in enumerate(buckets):
        probabilities = [cast(float, r.forecast_probability) for r in values]
        outcomes = [cast(int, r.outcome.value) for r in values]
        result.append(
            {
                "bucket": index,
                "count": len(values),
                "mean_forecast": None if not values else sum(probabilities) / len(values),
                "observed_rate": None if not values else sum(outcomes) / len(values),
                "confidence_interval": confidence_interval([float(x) for x in outcomes]),
            }
        )
    return tuple(result)


def event_equal_aggregation(
    records: list[ScoringRecord] | tuple[ScoringRecord, ...],
) -> dict[str, object]:
    """Aggregate each underlying event once; siblings cannot inflate the result."""
    by_event: dict[str, list[ScoringRecord]] = {}
    for record in records:
        if record.score_status == "SCORED":
            by_event.setdefault(record.underlying_event_id, []).append(record)
    event_brier = [
        sum(float(cast(float, r.brier_score)) for r in group) / len(group)
        for group in by_event.values()
    ]
    event_log = [
        sum(float(cast(float, r.log_loss)) for r in group) / len(group)
        for group in by_event.values()
    ]
    abstentions = sum(
        1
        for r in records
        if r.outcome.status == OutcomeStatus.RESOLVED and r.forecast_probability is None
    )
    return {
        "event_count": len(by_event),
        "scored_trial_count": sum(map(len, by_event.values())),
        "abstention_count": abstentions,
        "brier_mean": None if not event_brier else sum(event_brier) / len(event_brier),
        "log_loss_mean": None if not event_log else sum(event_log) / len(event_log),
        "brier_confidence_interval": confidence_interval(event_brier),
        "log_loss_confidence_interval": confidence_interval(event_log),
    }
