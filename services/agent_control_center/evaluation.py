"""M26C immutable, outcome-linked research evaluation.

This module evaluates historical research receipts only.  It has no execution,
network, credential, allocation, sizing, or strategy-mutation dependency.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from services.contract_intelligence.settlement import ReconciliationStatus, SettlementRecord
from services.forecasting.scoring import score
from services.opportunity_engine.books import OutcomeSide

from .attribution import receipt_identity
from .domain import ZERO_INFLUENCE, DecisionReceipt, ResearchDecision

EVALUATION_POLICY_VERSION = "m26c-receipt-outcome-v1"
CALIBRATION_POLICY_VERSION = "m26c-equal-width-deciles-v1"
EVALUATION_DATASET_SEMANTICS = "EVALUATION_ATTEMPT_HISTORY"


class EvaluationError(ValueError):
    pass


class EvaluationEligibility(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    OUTCOME_NOT_FINAL = "OUTCOME_NOT_FINAL"
    RECONCILIATION_MISMATCH = "RECONCILIATION_MISMATCH"
    TARGET_UNPROVEN = "TARGET_UNPROVEN"
    TEMPORAL_LEAKAGE_RISK = "TEMPORAL_LEAKAGE_RISK"
    UNSUPPORTED = "UNSUPPORTED"


class EvidenceState(StrEnum):
    NO_EVIDENCE = "NO EVIDENCE"
    INCONCLUSIVE = "EARLY / INCONCLUSIVE"
    SUFFICIENT = "SUFFICIENT DESCRIPTIVE EVIDENCE"


@dataclass(frozen=True, slots=True)
class EvaluationTarget:
    """Explicit orientation proved by the immutable M26B source binding."""

    market_ticker: str
    outcome_side: OutcomeSide
    source_kind: str
    source_id: str
    source_observed_at: datetime
    source_content_hash: str
    target_version: str = "event-edge-binary-v1"

    def __post_init__(self) -> None:
        _utc(self.source_observed_at, "source_observed_at")
        if self.source_kind != "trade-candidate" or not self.source_id:
            raise EvaluationError("Event Edge target requires an immutable TradeCandidate source")
        if len(self.source_content_hash) != 64:
            raise EvaluationError("target source content hash must be SHA-256")
        if self.source_id != self.source_content_hash:
            raise EvaluationError(
                "TradeCandidate source identity must equal its immutable content hash"
            )

    @property
    def instrument_id(self) -> str:
        return f"{self.market_ticker}:{self.outcome_side.value}"

    def material(self) -> dict[str, str]:
        return {
            "market_ticker": self.market_ticker,
            "outcome_side": self.outcome_side.value,
            "source_kind": self.source_kind,
            "source_id": self.source_id,
            "source_observed_at": _time(self.source_observed_at),
            "source_content_hash": self.source_content_hash,
            "target_version": self.target_version,
        }


def settlement_hash(settlement: SettlementRecord) -> str:
    material = {
        "determined_at": _time(settlement.determined_at),
        "exchange_record_hash": settlement.exchange_record_hash,
        "finalized_at": None if settlement.finalized_at is None else _time(settlement.finalized_at),
        "market_ticker": settlement.market_ticker,
        "reconciliation_status": settlement.reconciliation_status.value,
        "result": settlement.result,
        "rules_version": settlement.rules_version,
        "semantic_spec_id": settlement.semantic_spec_id,
        "settlement_value_dollars": str(settlement.settlement_value_dollars),
        "source_observation_id": settlement.source_observation_id,
    }
    return _hash(material)


def settlement_track_identity(settlement: SettlementRecord) -> str:
    """Identity of one settlement lifecycle, excluding maturation-only fields."""
    return _hash(
        {
            "determined_at": _time(settlement.determined_at),
            "market_ticker": settlement.market_ticker,
            "result": settlement.result,
            "rules_version": settlement.rules_version,
            "semantic_spec_id": settlement.semantic_spec_id,
            "settlement_value_dollars": str(settlement.settlement_value_dollars),
        }
    )


@dataclass(frozen=True, slots=True)
class ReceiptOutcomeLink:
    receipt_id: str
    agent_id: str
    agent_version: str
    receipt_instrument_id: str
    target: EvaluationTarget
    settlement_hash: str
    settlement_track_id: str
    settlement_market_ticker: str
    finalized_outcome: str
    determined_at: datetime
    finalized_at: datetime | None
    reconciliation_status: ReconciliationStatus
    provenance_hashes: tuple[str, ...]
    production_influence: Decimal = ZERO_INFLUENCE

    def __post_init__(self) -> None:
        _utc(self.determined_at, "determined_at")
        if self.finalized_at is not None:
            _utc(self.finalized_at, "finalized_at")
        if self.production_influence != ZERO_INFLUENCE:
            raise EvaluationError("outcome links have exactly zero production influence")
        if self.receipt_instrument_id != self.target.instrument_id:
            raise EvaluationError("target orientation does not match receipt instrument identity")
        if self.settlement_market_ticker != self.target.market_ticker:
            raise EvaluationError("settlement ticker does not match explicit evaluation target")
        if (
            len(self.settlement_hash) != 64
            or len(self.settlement_track_id) != 64
            or any(len(value) != 64 for value in self.provenance_hashes)
        ):
            raise EvaluationError("outcome link provenance must use SHA-256 identities")


@dataclass(frozen=True, slots=True)
class ReceiptEvaluation:
    evaluation_id: str
    policy_version: str
    receipt_id: str
    agent_id: str
    agent_version: str
    decision: ResearchDecision
    instrument_id: str
    market_ticker: str
    market_family: str | None
    target: EvaluationTarget
    settlement_hash: str
    settlement_track_id: str
    settlement_result: str
    determined_at: datetime
    finalized_at: datetime | None
    evaluated_at: datetime
    eligibility: EvaluationEligibility
    exclusion_detail: str | None
    realized_target: int | None
    model_probability: Decimal | None
    market_probability: Decimal | None
    historical_after_cost_edge: Decimal | None
    model_brier: Decimal | None
    market_brier: Decimal | None
    market_relative_brier_improvement: Decimal | None
    counterfactual_unit_value: Decimal | None
    evidence_references: tuple[str, ...]
    provenance_hashes: tuple[str, ...]
    production_influence: Decimal = ZERO_INFLUENCE

    def __post_init__(self) -> None:
        for value, name in (
            (self.determined_at, "determined_at"),
            (self.evaluated_at, "evaluated_at"),
        ):
            _utc(value, name)
        if self.finalized_at is not None:
            _utc(self.finalized_at, "finalized_at")
        if self.production_influence != ZERO_INFLUENCE:
            raise EvaluationError("evaluations have exactly zero production influence")
        metrics = (
            self.realized_target,
            self.model_brier,
            self.market_brier,
            self.market_relative_brier_improvement,
            self.counterfactual_unit_value,
        )
        if self.eligibility is not EvaluationEligibility.ELIGIBLE and any(
            x is not None for x in metrics
        ):
            raise EvaluationError("excluded evaluation cannot contain outcome metrics")
        if self.eligibility is EvaluationEligibility.ELIGIBLE and self.model_brier is None:
            raise EvaluationError("eligible Event Edge evaluation requires a Brier score")
        if self.evaluation_id != evaluation_identity(
            self.receipt_id, self.settlement_hash, self.target, self.policy_version
        ):
            raise EvaluationError("evaluation identity does not match immutable inputs")

    def to_json(self) -> str:
        payload = {
            "agent_id": self.agent_id,
            "agent_version": self.agent_version,
            "counterfactual_unit_value": _decimal(self.counterfactual_unit_value),
            "decision": self.decision.value,
            "determined_at": _time(self.determined_at),
            "eligibility": self.eligibility.value,
            "evaluated_at": _time(self.evaluated_at),
            "evaluation_id": self.evaluation_id,
            "evidence_references": list(self.evidence_references),
            "exclusion_detail": self.exclusion_detail,
            "finalized_at": None if self.finalized_at is None else _time(self.finalized_at),
            "historical_after_cost_edge": _decimal(self.historical_after_cost_edge),
            "instrument_id": self.instrument_id,
            "market_brier": _decimal(self.market_brier),
            "market_family": self.market_family,
            "market_probability": _decimal(self.market_probability),
            "market_relative_brier_improvement": _decimal(self.market_relative_brier_improvement),
            "market_ticker": self.market_ticker,
            "model_brier": _decimal(self.model_brier),
            "model_probability": _decimal(self.model_probability),
            "policy_version": self.policy_version,
            "production_influence": str(self.production_influence),
            "provenance_hashes": list(self.provenance_hashes),
            "realized_target": self.realized_target,
            "receipt_id": self.receipt_id,
            "settlement_hash": self.settlement_hash,
            "settlement_track_id": self.settlement_track_id,
            "settlement_result": self.settlement_result,
            "target": self.target.material(),
        }
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def evaluation_logical_material(evaluation: ReceiptEvaluation) -> str:
    """Return canonical logical facts, excluding only processing-time ``evaluated_at``.

    The originally persisted evaluation retains its audit timestamp. This material is
    used only to recognize a replay of that same logical evaluation; every receipt,
    target, settlement, policy, eligibility, metric, and influence fact remains bound.
    """
    payload = json.loads(evaluation.to_json())
    del payload["evaluated_at"]
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def evaluation_identity(
    receipt_id: str,
    outcome_hash: str,
    target: EvaluationTarget,
    policy_version: str = EVALUATION_POLICY_VERSION,
) -> str:
    return _hash([policy_version, receipt_id, outcome_hash, target.material()])


def evaluate_receipt(
    receipt: DecisionReceipt,
    settlement: SettlementRecord,
    target: EvaluationTarget,
    *,
    evaluated_at: datetime,
    market_family: str | None = None,
    policy_version: str = EVALUATION_POLICY_VERSION,
) -> ReceiptEvaluation:
    """Bind and score one receipt; preserve honest exclusions instead of dropping them."""
    _utc(evaluated_at, "evaluated_at")
    if receipt.agent_id != "event-edge":
        eligibility, detail = (
            EvaluationEligibility.UNSUPPORTED,
            "Agent outcome semantics unsupported",
        )
    else:
        eligibility, detail = EvaluationEligibility.ELIGIBLE, None
    if target.source_id == receipt.receipt_id:
        raise EvaluationError("source identity cannot be replaced by receipt identity")
    if receipt.instrument_id != target.instrument_id:
        raise EvaluationError("target orientation does not match historical receipt")
    if settlement.market_ticker != target.market_ticker:
        raise EvaluationError("settlement ticker does not match historical receipt target")
    outcome_hash = settlement_hash(settlement)
    track_id = settlement_track_identity(settlement)
    ReceiptOutcomeLink(
        receipt.receipt_id,
        receipt.agent_id,
        receipt.agent_version,
        receipt.instrument_id,
        target,
        outcome_hash,
        track_id,
        settlement.market_ticker,
        settlement.result,
        settlement.determined_at,
        settlement.finalized_at,
        settlement.reconciliation_status,
        (target.source_content_hash, outcome_hash),
    )
    if eligibility is EvaluationEligibility.ELIGIBLE:
        if not settlement.eligible_training_label:
            if settlement.finalized_at is None:
                eligibility, detail = (
                    EvaluationEligibility.OUTCOME_NOT_FINAL,
                    "Settlement is not finalized",
                )
            else:
                eligibility, detail = (
                    EvaluationEligibility.RECONCILIATION_MISMATCH,
                    settlement.reconciliation_status.value,
                )
        elif receipt.receipt_id != receipt_identity(
            receipt.agent_id, receipt.agent_version, target.source_kind, target.source_id
        ):
            eligibility, detail = (
                EvaluationEligibility.TARGET_UNPROVEN,
                "Target source binding unavailable",
            )
        elif (
            target.source_observed_at > receipt.created_at
            or receipt.created_at >= settlement.determined_at
        ):
            eligibility, detail = (
                EvaluationEligibility.TEMPORAL_LEAKAGE_RISK,
                "Decision/source did not precede determination",
            )
        elif settlement.result not in {"YES", "NO"} or settlement.settlement_value_dollars not in {
            Decimal(0),
            Decimal(1),
        }:
            eligibility, detail = (
                EvaluationEligibility.UNSUPPORTED,
                "Authoritative outcome is not simple binary",
            )
        elif (settlement.result, settlement.settlement_value_dollars) not in {
            ("YES", Decimal(1)),
            ("NO", Decimal(0)),
        }:
            eligibility, detail = (
                EvaluationEligibility.UNSUPPORTED,
                "Binary settlement result contradicts settlement value",
            )
        elif receipt.model_probability is None:
            eligibility, detail = (
                EvaluationEligibility.TARGET_UNPROVEN,
                "Historical target probability unavailable",
            )
    realized = model_brier = market_brier = relative = unit_value = None
    if eligibility is EvaluationEligibility.ELIGIBLE:
        if receipt.model_probability is None:
            raise EvaluationError("eligible evaluation has no historical model probability")
        yes_won = settlement.result == "YES"
        realized = int(yes_won == (target.outcome_side is OutcomeSide.YES))
        model_brier = score(receipt.model_probability, realized).brier
        if receipt.observed_market_price is not None:
            market_brier = score(receipt.observed_market_price, realized).brier
            relative = market_brier - model_brier
        if all(
            value is not None
            for value in (
                receipt.observed_market_price,
                receipt.estimated_fees,
                receipt.estimated_slippage,
            )
        ):
            if (
                receipt.observed_market_price is None
                or receipt.estimated_fees is None
                or receipt.estimated_slippage is None
            ):
                raise EvaluationError("counterfactual inputs disappeared after validation")
            unit_value = (
                Decimal(realized)
                - receipt.observed_market_price
                - receipt.estimated_fees
                - receipt.estimated_slippage
            )
    return ReceiptEvaluation(
        evaluation_identity(receipt.receipt_id, outcome_hash, target, policy_version),
        policy_version,
        receipt.receipt_id,
        receipt.agent_id,
        receipt.agent_version,
        receipt.decision,
        receipt.instrument_id,
        target.market_ticker,
        market_family,
        target,
        outcome_hash,
        track_id,
        settlement.result,
        settlement.determined_at,
        settlement.finalized_at,
        evaluated_at,
        eligibility,
        detail,
        realized,
        receipt.model_probability,
        receipt.observed_market_price,
        receipt.after_cost_edge,
        model_brier,
        market_brier,
        relative,
        unit_value,
        receipt.evidence_references,
        (target.source_content_hash, outcome_hash),
    )


@dataclass(frozen=True, slots=True)
class CalibrationBucket:
    lower: Decimal
    upper: Decimal
    count: int
    mean_probability: Decimal | None
    observed_frequency: Decimal | None
    sufficient: bool


def calibration(
    evaluations: tuple[ReceiptEvaluation, ...], minimum_per_bucket: int = 10
) -> tuple[CalibrationBucket, ...]:
    eligible = [
        e
        for e in effective_evaluations(evaluations)
        if e.eligibility is EvaluationEligibility.ELIGIBLE and e.model_probability is not None
    ]
    buckets: list[CalibrationBucket] = []
    for index in range(10):
        lower, upper = Decimal(index) / Decimal(10), Decimal(index + 1) / Decimal(10)
        rows = [
            e
            for e in eligible
            if e.model_probability is not None
            and lower <= e.model_probability <= upper
            and (index == 9 or e.model_probability < upper)
        ]
        count = len(rows)
        mean = (
            None
            if not rows
            else sum(
                (e.model_probability for e in rows if e.model_probability is not None), Decimal(0)
            )
            / Decimal(count)
        )
        observed = (
            None
            if not rows
            else sum((Decimal(e.realized_target or 0) for e in rows), Decimal(0)) / Decimal(count)
        )
        buckets.append(
            CalibrationBucket(lower, upper, count, mean, observed, count >= minimum_per_bucket)
        )
    return tuple(buckets)


@dataclass(frozen=True, slots=True)
class AgentPerformance:
    agent_id: str
    agent_version: str
    total_persisted_decisions: int
    evaluation_attempt_count: int
    outcome_linked_decisions: int
    effective_eligible_decisions: int
    currently_unscoreable_decisions: int
    awaiting_evaluation_decisions: int
    excluded_by_reason: tuple[tuple[str, int], ...]
    unique_market_count: int
    proven_unique_event_count: int | None
    average_model_brier: Decimal | None
    average_market_brier: Decimal | None
    average_market_relative_brier_improvement: Decimal | None
    decision_breakdown: tuple[tuple[str, int], ...]
    evidence_state: EvidenceState
    interval: None = None

    @property
    def eligible_scored_decisions(self) -> int:
        return self.effective_eligible_decisions


_EFFECTIVE_RANK = {
    EvaluationEligibility.ELIGIBLE: 6,
    EvaluationEligibility.RECONCILIATION_MISMATCH: 5,
    EvaluationEligibility.OUTCOME_NOT_FINAL: 4,
    EvaluationEligibility.TEMPORAL_LEAKAGE_RISK: 3,
    EvaluationEligibility.TARGET_UNPROVEN: 2,
    EvaluationEligibility.UNSUPPORTED: 1,
}


def effective_evaluations(
    evaluations: tuple[ReceiptEvaluation, ...],
    *,
    policy_version: str = EVALUATION_POLICY_VERSION,
) -> tuple[ReceiptEvaluation, ...]:
    """Select one deterministic lifecycle-effective attempt per receipt/policy."""
    grouped: dict[str, list[ReceiptEvaluation]] = {}
    for evaluation in evaluations:
        if evaluation.policy_version == policy_version:
            grouped.setdefault(evaluation.receipt_id, []).append(evaluation)

    def key(value: ReceiptEvaluation) -> tuple[int, datetime, datetime, str]:
        finalized = value.finalized_at or datetime.min.replace(tzinfo=UTC)
        return (
            _EFFECTIVE_RANK[value.eligibility],
            finalized,
            value.evaluated_at,
            value.evaluation_id,
        )

    return tuple(max(rows, key=key) for _receipt_id, rows in sorted(grouped.items()))


def performance(
    evaluations: tuple[ReceiptEvaluation, ...],
    *,
    agent_id: str,
    agent_version: str,
    total_persisted_decisions: int,
    policy_version: str = EVALUATION_POLICY_VERSION,
) -> AgentPerformance:
    attempts = tuple(
        e
        for e in evaluations
        if e.agent_id == agent_id
        and e.agent_version == agent_version
        and e.policy_version == policy_version
    )
    effective = effective_evaluations(attempts, policy_version=policy_version)
    eligible = [e for e in effective if e.eligibility is EvaluationEligibility.ELIGIBLE]
    excluded = [e for e in effective if e.eligibility is not EvaluationEligibility.ELIGIBLE]
    linked = len(effective)
    state = EvidenceState.NO_EVIDENCE if not eligible else EvidenceState.INCONCLUSIVE

    def average(name: str) -> Decimal | None:
        values = [value for row in eligible if (value := getattr(row, name)) is not None]
        return None if not values else sum(values, Decimal(0)) / Decimal(len(values))

    return AgentPerformance(
        agent_id,
        agent_version,
        total_persisted_decisions,
        len(attempts),
        linked,
        len(eligible),
        len(excluded),
        max(0, total_persisted_decisions - linked),
        tuple(sorted(Counter(e.eligibility.value for e in excluded).items())),
        len({e.market_ticker for e in eligible}),
        None,
        average("model_brier"),
        average("market_brier"),
        average("market_relative_brier_improvement"),
        tuple(sorted(Counter(e.decision.value for e in effective).items())),
        state,
    )


@dataclass(frozen=True, slots=True)
class EvaluationDatasetManifest:
    """Complete append-only evaluation-attempt history for the selected universe.

    Included and excluded entries are attempts, so one receipt may occur more than
    once as its settlement matures. Raw entry counts MUST NOT be interpreted or
    summed as decision/performance observations. Performance and calibration
    consumers must select one receipt/policy row with :func:`effective_evaluations`.
    """

    manifest_id: str
    policy_version: str
    start_at: datetime
    end_at: datetime
    agent_versions: tuple[tuple[str, str], ...]
    market_family: str | None
    included_evaluation_ids: tuple[str, ...]
    excluded_evaluations: tuple[tuple[str, str], ...]
    settlement_hashes: tuple[str, ...]
    generated_at: datetime
    dataset_semantics: str = field(default=EVALUATION_DATASET_SEMANTICS, init=False)

    @classmethod
    def _materialize_complete_universe(
        cls,
        evaluations: tuple[ReceiptEvaluation, ...],
        *,
        start_at: datetime,
        end_at: datetime,
        agent_versions: tuple[tuple[str, str], ...],
        policy_version: str,
        market_family: str | None,
        generated_at: datetime,
    ) -> EvaluationDatasetManifest:
        for value, name in (
            (start_at, "start_at"),
            (end_at, "end_at"),
            (generated_at, "generated_at"),
        ):
            _utc(value, name)
        selected = sorted(
            (
                e
                for e in evaluations
                if start_at <= e.evaluated_at <= end_at
                and (e.agent_id, e.agent_version) in agent_versions
                and e.policy_version == policy_version
                and (market_family is None or e.market_family == market_family)
            ),
            key=lambda e: e.evaluation_id,
        )
        included = tuple(
            e.evaluation_id for e in selected if e.eligibility is EvaluationEligibility.ELIGIBLE
        )
        excluded = tuple(
            (e.evaluation_id, e.eligibility.value)
            for e in selected
            if e.eligibility is not EvaluationEligibility.ELIGIBLE
        )
        settlements = tuple(sorted({e.settlement_hash for e in selected}))
        agents = tuple(sorted(agent_versions))
        digest = _hash(
            [
                EVALUATION_DATASET_SEMANTICS,
                policy_version,
                _time(start_at),
                _time(end_at),
                agents,
                market_family,
                included,
                excluded,
                settlements,
            ]
        )
        return cls(
            digest,
            policy_version,
            start_at,
            end_at,
            agents,
            market_family,
            included,
            excluded,
            settlements,
            generated_at,
        )


def _utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise EvaluationError(f"{name} must be UTC")


def _time(value: datetime) -> str:
    _utc(value, "timestamp")
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=True, separators=(",", ":"), sort_keys=True, default=str
        ).encode()
    ).hexdigest()


def restore_evaluation(canonical_json: str) -> ReceiptEvaluation:
    """Restore only canonical, structurally valid historical evaluation evidence."""
    try:
        value = json.loads(canonical_json)
        target_value = value["target"]
        target = EvaluationTarget(
            target_value["market_ticker"],
            OutcomeSide(target_value["outcome_side"]),
            target_value["source_kind"],
            target_value["source_id"],
            datetime.fromisoformat(target_value["source_observed_at"].replace("Z", "+00:00")),
            target_value["source_content_hash"],
            target_value["target_version"],
        )

        def decimal(name: str) -> Decimal | None:
            item = value[name]
            return None if item is None else Decimal(item)

        restored = ReceiptEvaluation(
            value["evaluation_id"],
            value["policy_version"],
            value["receipt_id"],
            value["agent_id"],
            value["agent_version"],
            ResearchDecision(value["decision"]),
            value["instrument_id"],
            value["market_ticker"],
            value["market_family"],
            target,
            value["settlement_hash"],
            value["settlement_track_id"],
            value["settlement_result"],
            datetime.fromisoformat(value["determined_at"].replace("Z", "+00:00")),
            None
            if value["finalized_at"] is None
            else datetime.fromisoformat(value["finalized_at"].replace("Z", "+00:00")),
            datetime.fromisoformat(value["evaluated_at"].replace("Z", "+00:00")),
            EvaluationEligibility(value["eligibility"]),
            value["exclusion_detail"],
            value["realized_target"],
            decimal("model_probability"),
            decimal("market_probability"),
            decimal("historical_after_cost_edge"),
            decimal("model_brier"),
            decimal("market_brier"),
            decimal("market_relative_brier_improvement"),
            decimal("counterfactual_unit_value"),
            tuple(value["evidence_references"]),
            tuple(value["provenance_hashes"]),
            Decimal(value["production_influence"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise EvaluationError("persisted evaluation is invalid") from exc
    if restored.to_json() != canonical_json:
        raise EvaluationError("persisted evaluation JSON is not canonical")
    return restored
