"""WN-A1 evaluation-record preservation and fresh-process replay.

Every evaluated opportunity -- not only the ones a human acted on -- must be retained to
prevent selection bias. ``EvaluationRecord`` binds only immutable evidence identities
(it never re-fetches anything), so replaying it in a fresh process must reproduce the
identical ``record_id`` from the same bound identities: see ``replay_record_id``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from services.market_universe.domain import stable_hash

from .wn_a1_alert import AlertDecision
from .wn_a1_current_daily_high_authority import POLICY_VERSION as CONTRACT_POLICY_VERSION
from .wn_a1_domain import PRODUCTION_INFLUENCE, RESEARCH_ONLY, WnA1Error

RECORD_SCHEMA_VERSION = "wn-a1-evaluation-record-v1"


@dataclass(frozen=True, slots=True)
class EvaluationRecord:
    record_id: str
    market_ticker: str
    target_local_date: str
    weathernext_evidence_identity: str | None
    kalshi_snapshot_identity: str | None
    contract_policy_identity: str
    contract_policy_version: str
    alert_policy_version: str
    decision_state: str
    decision_gate_failures: tuple[str, ...]
    raw_probability: Decimal | None
    market_yes_probability: Decimal | None
    gap_pp: Decimal | None
    buffered_gap_pp: Decimal | None
    evaluated_at: datetime
    research_only: bool = RESEARCH_ONLY
    production_influence: Decimal = PRODUCTION_INFLUENCE

    @classmethod
    def create(
        cls,
        *,
        market_ticker: str,
        target_local_date: date,
        weathernext_evidence_identity: str | None,
        kalshi_snapshot_identity: str | None,
        contract_policy_identity: str,
        decision: AlertDecision,
        evaluated_at: datetime,
    ) -> EvaluationRecord:
        if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
            raise WnA1Error("evaluation record timestamp must be timezone-aware")
        evaluated_at_utc = evaluated_at.astimezone(UTC)
        record_id = _record_material(
            market_ticker=market_ticker,
            target_local_date=target_local_date.isoformat(),
            weathernext_evidence_identity=weathernext_evidence_identity,
            kalshi_snapshot_identity=kalshi_snapshot_identity,
            contract_policy_identity=contract_policy_identity,
            contract_policy_version=CONTRACT_POLICY_VERSION,
            alert_policy_version=decision.policy_version,
            decision_state=decision.state.value,
            decision_gate_failures=tuple(f.value for f in decision.gate_failures),
            raw_probability=_dec(decision.raw_probability),
            market_yes_probability=_dec(decision.market_yes_probability),
            evaluated_at_iso=evaluated_at_utc.isoformat(),
        )
        return cls(
            record_id=record_id,
            market_ticker=market_ticker,
            target_local_date=target_local_date.isoformat(),
            weathernext_evidence_identity=weathernext_evidence_identity,
            kalshi_snapshot_identity=kalshi_snapshot_identity,
            contract_policy_identity=contract_policy_identity,
            contract_policy_version=CONTRACT_POLICY_VERSION,
            alert_policy_version=decision.policy_version,
            decision_state=decision.state.value,
            decision_gate_failures=tuple(f.value for f in decision.gate_failures),
            raw_probability=decision.raw_probability,
            market_yes_probability=decision.market_yes_probability,
            gap_pp=decision.gap_pp,
            buffered_gap_pp=decision.buffered_gap_pp,
            evaluated_at=evaluated_at_utc,
        )


def replay_record_id(record: EvaluationRecord) -> str:
    """Recompute ``record_id`` purely from the record's own bound fields.

    A fresh process must produce the same value from the same persisted record; a caller
    that wants a true end-to-end replay should instead re-run evaluation from the original
    bound WeatherNext/Kalshi evidence and compare the resulting record to this one.
    """
    return _record_material(
        market_ticker=record.market_ticker,
        target_local_date=record.target_local_date,
        weathernext_evidence_identity=record.weathernext_evidence_identity,
        kalshi_snapshot_identity=record.kalshi_snapshot_identity,
        contract_policy_identity=record.contract_policy_identity,
        contract_policy_version=record.contract_policy_version,
        alert_policy_version=record.alert_policy_version,
        decision_state=record.decision_state,
        decision_gate_failures=record.decision_gate_failures,
        raw_probability=_dec(record.raw_probability),
        market_yes_probability=_dec(record.market_yes_probability),
        evaluated_at_iso=record.evaluated_at.isoformat(),
    )


def _dec(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _record_material(
    *,
    market_ticker: str,
    target_local_date: str,
    weathernext_evidence_identity: str | None,
    kalshi_snapshot_identity: str | None,
    contract_policy_identity: str,
    contract_policy_version: str,
    alert_policy_version: str,
    decision_state: str,
    decision_gate_failures: tuple[str, ...],
    raw_probability: str | None,
    market_yes_probability: str | None,
    evaluated_at_iso: str,
) -> str:
    return stable_hash(
        (
            RECORD_SCHEMA_VERSION,
            market_ticker,
            target_local_date,
            weathernext_evidence_identity,
            kalshi_snapshot_identity,
            contract_policy_identity,
            contract_policy_version,
            alert_policy_version,
            decision_state,
            decision_gate_failures,
            raw_probability,
            market_yes_probability,
            evaluated_at_iso,
        )
    )
