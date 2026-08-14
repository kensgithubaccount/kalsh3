"""M26D controlled, descriptive comparison of outcome-linked research evidence.

The complete cohort is derived from an :class:`EvaluationStore`; callers cannot
provide evaluation IDs.  This module has no execution, allocation, governance,
network, credential, or strategy dependency.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from services.opportunity_engine.books import OutcomeSide

from .domain import AGENT_REGISTRY, ZERO_INFLUENCE, AgentDefinition
from .evaluation import (
    EVALUATION_POLICY_VERSION,
    EvaluationEligibility,
    ReceiptEvaluation,
    effective_evaluations,
)

if TYPE_CHECKING:
    from .evaluation_store import EvaluationStore

M26D_COMPARISON_POLICY_VERSION = "m26d-shared-unit-comparison-v1"
M26D_CAPABILITY_AUTHORITY_VERSION = "m26d-comparison-capabilities-v1"
M26D_TEMPORAL_POLICY = "unaligned-forecast-horizons-descriptive-v1"
M26D_WINDOW_BASIS = "evaluated_at"
COMPARISON_UNIT_DOMAIN = "m26d-comparison-unit-v1"
COMPARISON_COHORT_DOMAIN = "m26d-comparison-cohort-v1"
BINARY_BRIER_SEMANTICS = "binary-contract-outcome-brier-v1"


class ComparisonError(ValueError):
    pass


class ComparisonEligibility(StrEnum):
    COMPARABLE_DESCRIPTIVELY = "COMPARABLE_DESCRIPTIVELY"
    INSUFFICIENT_SHARED_EVIDENCE = "INSUFFICIENT_SHARED_EVIDENCE"
    EVALUATION_UNSUPPORTED = "EVALUATION_UNSUPPORTED"
    OUTCOME_SEMANTICS_MISMATCH = "OUTCOME_SEMANTICS_MISMATCH"
    POLICY_MISMATCH = "POLICY_MISMATCH"
    TARGET_VERSION_MISMATCH = "TARGET_VERSION_MISMATCH"
    NO_SHARED_UNITS = "NO_SHARED_UNITS"
    AMBIGUOUS_DUPLICATE_UNIT = "AMBIGUOUS_DUPLICATE_UNIT"
    EVENT_INDEPENDENCE_UNPROVEN = "EVENT_INDEPENDENCE_UNPROVEN"
    CORRUPT_EVIDENCE = "CORRUPT_EVIDENCE"
    UNSUPPORTED = "UNSUPPORTED"
    SELF_COMPARISON = "SELF_COMPARISON"


class CompetitionEvidenceState(StrEnum):
    NO_COMPARISON = "NO_COMPARISON"
    INCONCLUSIVE_INDEPENDENCE = "INCONCLUSIVE_INDEPENDENCE"
    EVIDENCE_UNAVAILABLE = "EVIDENCE_UNAVAILABLE"


class TemporalAlignment(StrEnum):
    UNALIGNED = "UNALIGNED"


class InformationSetComparability(StrEnum):
    NOT_PROVEN = "NOT_PROVEN"


@dataclass(frozen=True, slots=True)
class ComparisonCapability:
    """Versioned authority for an agent's supported outcome-comparison semantics."""

    agent_id: str
    evaluation_policy_version: str
    outcome_semantics: str
    target_version: str
    authority_version: str = M26D_CAPABILITY_AUTHORITY_VERSION


COMPARISON_CAPABILITIES: tuple[ComparisonCapability, ...] = (
    ComparisonCapability(
        "event-edge",
        EVALUATION_POLICY_VERSION,
        BINARY_BRIER_SEMANTICS,
        "event-edge-binary-v1",
    ),
)


def _capability_for(
    agent_id: str,
    evaluation_policy_version: str,
    outcome_semantics: str,
    target_version: str,
) -> ComparisonCapability | None:
    return next(
        (
            capability
            for capability in COMPARISON_CAPABILITIES
            if (
                capability.agent_id,
                capability.evaluation_policy_version,
                capability.outcome_semantics,
                capability.target_version,
            )
            == (agent_id, evaluation_policy_version, outcome_semantics, target_version)
        ),
        None,
    )


@dataclass(frozen=True, slots=True)
class ComparisonContender:
    """One historical implementation; independent of current registry state."""

    agent_id: str
    agent_version: str
    evaluation_policy_version: str
    outcome_semantics: str
    target_version: str
    market_family: str | None = None
    production_influence: Decimal = ZERO_INFLUENCE

    def __post_init__(self) -> None:
        if not self.agent_id or not self.agent_version:
            raise ComparisonError("contender agent identity and version are required")
        if (
            not self.evaluation_policy_version
            or not self.outcome_semantics
            or not self.target_version
        ):
            raise ComparisonError("contender comparison semantics are required")
        if self.production_influence != ZERO_INFLUENCE:
            raise ComparisonError("comparison contenders have exactly zero production influence")
        if (
            _capability_for(
                self.agent_id,
                self.evaluation_policy_version,
                self.outcome_semantics,
                self.target_version,
            )
            is None
        ):
            raise ComparisonError("contender semantic capability is not authorized by M26D")

    def material(self) -> dict[str, str | None]:
        return {
            "agent_id": self.agent_id,
            "agent_version": self.agent_version,
            "evaluation_policy_version": self.evaluation_policy_version,
            "market_family": self.market_family,
            "outcome_semantics": self.outcome_semantics,
            "production_influence": "0",
            "target_version": self.target_version,
        }


@dataclass(frozen=True, slots=True)
class ComparisonUnit:
    unit_id: str
    market_ticker: str
    outcome_side: OutcomeSide
    target_version: str
    settlement_track_id: str
    market_family: str | None

    @classmethod
    def from_evaluation(cls, evaluation: ReceiptEvaluation) -> ComparisonUnit:
        material = {
            "domain": COMPARISON_UNIT_DOMAIN,
            "market_family": evaluation.market_family,
            "market_ticker": evaluation.market_ticker,
            "outcome_side": evaluation.target.outcome_side.value,
            "settlement_track_id": evaluation.settlement_track_id,
            "target_version": evaluation.target.target_version,
        }
        return cls(
            _hash(material),
            evaluation.market_ticker,
            evaluation.target.outcome_side,
            evaluation.target.target_version,
            evaluation.settlement_track_id,
            evaluation.market_family,
        )


@dataclass(frozen=True, slots=True)
class SharedComparisonUnit:
    unit: ComparisonUnit
    contender_a_evaluation_id: str
    contender_b_evaluation_id: str


@dataclass(frozen=True, slots=True)
class ComparisonExclusion:
    unit_id: str
    market_ticker: str
    reason: str
    contender: str | None = None


@dataclass(frozen=True, slots=True)
class ComparisonCohort:
    cohort_id: str
    comparison_policy_version: str
    contender_a: ComparisonContender
    contender_b: ComparisonContender
    start_at: datetime
    end_at: datetime
    shared_units: tuple[SharedComparisonUnit, ...]
    excluded_units: tuple[ComparisonExclusion, ...]
    source_manifest_ids: tuple[str, str]
    temporal_policy: str
    temporal_alignment: TemporalAlignment
    information_set_comparability: InformationSetComparability
    window_basis: str
    generated_at: datetime
    production_influence: Decimal = ZERO_INFLUENCE

    def __post_init__(self) -> None:
        _utc(self.start_at, "start_at")
        _utc(self.end_at, "end_at")
        _utc(self.generated_at, "generated_at")
        if self.start_at > self.end_at:
            raise ComparisonError("comparison window is inverted")
        if self.production_influence != ZERO_INFLUENCE:
            raise ComparisonError("comparison cohorts have exactly zero production influence")
        if (
            self.temporal_policy != M26D_TEMPORAL_POLICY
            or self.temporal_alignment is not TemporalAlignment.UNALIGNED
            or self.information_set_comparability is not InformationSetComparability.NOT_PROVEN
            or self.window_basis != M26D_WINDOW_BASIS
        ):
            raise ComparisonError("comparison cohort temporal semantics are unsupported")
        if self.cohort_id != _cohort_identity(self):
            raise ComparisonError("comparison cohort identity does not match immutable material")


@dataclass(frozen=True, slots=True)
class PairwiseDescriptiveMetrics:
    shared_unit_count: int
    contender_a_mean_brier: Decimal
    contender_b_mean_brier: Decimal
    a_minus_b_brier: Decimal
    contender_a_mean_market_relative_brier_improvement: Decimal | None
    contender_b_mean_market_relative_brier_improvement: Decimal | None
    contender_a_decision_composition: tuple[tuple[str, int], ...]
    contender_b_decision_composition: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class PairwiseComparison:
    cohort: ComparisonCohort | None
    eligibility: ComparisonEligibility
    metrics: PairwiseDescriptiveMetrics | None
    evidence_state: CompetitionEvidenceState
    proven_unique_event_count: None = None
    winner: None = None
    explanation: str = ""
    production_influence: Decimal = ZERO_INFLUENCE

    def __post_init__(self) -> None:
        if self.production_influence != ZERO_INFLUENCE or self.winner is not None:
            raise ComparisonError("M26D cannot influence production or select a winner")


@dataclass(frozen=True, slots=True)
class ContenderAvailability:
    agent_id: str
    agent_version: str
    supported: bool
    explanation: str


@dataclass(frozen=True, slots=True)
class ResearchCompetitionSnapshot:
    contenders: tuple[ContenderAvailability, ...]
    comparisons: tuple[PairwiseComparison, ...]
    evidence_state: CompetitionEvidenceState
    no_winner_reason: str
    winner: None = None
    production_influence: Decimal = ZERO_INFLUENCE

    def __post_init__(self) -> None:
        if self.production_influence != ZERO_INFLUENCE or self.winner is not None:
            raise ComparisonError("competition snapshots have zero influence and no winner")


def supported_contender(
    agent_id: str,
    agent_version: str,
    *,
    target_version: str = "event-edge-binary-v1",
    market_family: str | None = None,
    evaluation_policy_version: str = EVALUATION_POLICY_VERSION,
) -> ComparisonContender:
    """Construct only the currently explicit M26C binary scoring contract."""
    return ComparisonContender(
        agent_id,
        agent_version,
        evaluation_policy_version,
        BINARY_BRIER_SEMANTICS,
        target_version,
        market_family,
    )


def semantic_eligibility(
    contender_a: ComparisonContender, contender_b: ComparisonContender
) -> ComparisonEligibility:
    if contender_a == contender_b:
        return ComparisonEligibility.SELF_COMPARISON
    if contender_a.outcome_semantics != contender_b.outcome_semantics:
        return ComparisonEligibility.OUTCOME_SEMANTICS_MISMATCH
    if contender_a.evaluation_policy_version != contender_b.evaluation_policy_version:
        return ComparisonEligibility.POLICY_MISMATCH
    if contender_a.target_version != contender_b.target_version:
        return ComparisonEligibility.TARGET_VERSION_MISMATCH
    if contender_a.market_family != contender_b.market_family:
        return ComparisonEligibility.OUTCOME_SEMANTICS_MISMATCH
    return ComparisonEligibility.COMPARABLE_DESCRIPTIVELY


def _contender_capability_is_authorized(contender: ComparisonContender) -> bool:
    """Defense in depth even if a caller bypasses frozen-dataclass construction."""
    return (
        contender.production_influence == ZERO_INFLUENCE
        and _capability_for(
            contender.agent_id,
            contender.evaluation_policy_version,
            contender.outcome_semantics,
            contender.target_version,
        )
        is not None
    )


def compare_from_store(
    store: EvaluationStore,
    contender_a: ComparisonContender,
    contender_b: ComparisonContender,
    *,
    start_at: datetime,
    end_at: datetime,
    generated_at: datetime,
) -> PairwiseComparison:
    """Compare shared propositions, not aligned forecast horizons or information sets."""
    _utc(start_at, "start_at")
    _utc(end_at, "end_at")
    _utc(generated_at, "generated_at")
    if not _contender_capability_is_authorized(
        contender_a
    ) or not _contender_capability_is_authorized(contender_b):
        return PairwiseComparison(
            None,
            ComparisonEligibility.EVALUATION_UNSUPPORTED,
            None,
            CompetitionEvidenceState.NO_COMPARISON,
            explanation="A contender lacks an authorized M26D outcome-comparison capability.",
        )
    semantic_state = semantic_eligibility(contender_a, contender_b)
    if semantic_state is not ComparisonEligibility.COMPARABLE_DESCRIPTIVELY:
        return PairwiseComparison(
            None,
            semantic_state,
            None,
            CompetitionEvidenceState.NO_COMPARISON,
            explanation="The contenders do not share the same scoring contract.",
        )
    # all() deliberately validates every persisted row. Any corruption fails the
    # whole universe before filtering, rather than producing a survivor-only result.
    evaluations = store.all()
    try:
        cohort, selected = _build_cohort(
            evaluations,
            contender_a,
            contender_b,
            start_at=start_at,
            end_at=end_at,
            generated_at=generated_at,
        )
    except ComparisonError:
        return PairwiseComparison(
            None,
            ComparisonEligibility.EVALUATION_UNSUPPORTED,
            None,
            CompetitionEvidenceState.NO_COMPARISON,
            explanation="Persisted evaluation evidence disagrees with the authorized capability.",
        )
    if not cohort.shared_units:
        duplicate = any(
            exclusion.reason == ComparisonEligibility.AMBIGUOUS_DUPLICATE_UNIT.value
            for exclusion in cohort.excluded_units
        )
        state = (
            ComparisonEligibility.AMBIGUOUS_DUPLICATE_UNIT
            if duplicate
            else ComparisonEligibility.NO_SHARED_UNITS
        )
        return PairwiseComparison(
            cohort,
            state,
            None,
            CompetitionEvidenceState.NO_COMPARISON,
            explanation="No unambiguous shared comparison units are available.",
        )
    metrics = _metrics(cohort, selected)
    return PairwiseComparison(
        cohort,
        ComparisonEligibility.COMPARABLE_DESCRIPTIVELY,
        metrics,
        CompetitionEvidenceState.INCONCLUSIVE_INDEPENDENCE,
        explanation=(
            f"On {metrics.shared_unit_count} shared markets, the paired Brier difference "
            f"(A minus B) was {metrics.a_minus_b_brier}. Forecast horizons and information "
            "sets are not aligned; this is descriptive shared-proposition scoring only. "
            "These markets are not proven independent events, so no winner is selected."
        ),
    )


def _build_cohort(
    evaluations: tuple[ReceiptEvaluation, ...],
    contender_a: ComparisonContender,
    contender_b: ComparisonContender,
    *,
    start_at: datetime,
    end_at: datetime,
    generated_at: datetime,
) -> tuple[ComparisonCohort, dict[str, ReceiptEvaluation]]:
    contenders = (contender_a, contender_b)
    rows_by_contender: list[tuple[ReceiptEvaluation, ...]] = []
    manifest_ids: list[str] = []
    for contender in contenders:
        attempts = tuple(
            row
            for row in evaluations
            if row.agent_id == contender.agent_id
            and row.agent_version == contender.agent_version
            and row.policy_version == contender.evaluation_policy_version
            and start_at <= row.evaluated_at <= end_at
            and (contender.market_family is None or row.market_family == contender.market_family)
        )
        rows_by_contender.append(
            effective_evaluations(attempts, policy_version=contender.evaluation_policy_version)
        )
        manifest_ids.append(
            _evaluation_manifest_provenance(contender, attempts, start_at=start_at, end_at=end_at)
        )

    grouped: list[dict[str, list[ReceiptEvaluation]]] = []
    units: dict[str, ComparisonUnit] = {}
    exclusions: list[ComparisonExclusion] = []
    for index, rows in enumerate(rows_by_contender):
        by_unit: dict[str, list[ReceiptEvaluation]] = defaultdict(list)
        for row in rows:
            if row.eligibility is EvaluationEligibility.ELIGIBLE and not _eligible_row_agrees(
                row, contenders[index]
            ):
                raise ComparisonError("eligible row disagrees with contender capability")
            unit = ComparisonUnit.from_evaluation(row)
            units[unit.unit_id] = unit
            if (
                row.eligibility is not EvaluationEligibility.ELIGIBLE
                or row.target.target_version != contenders[index].target_version
            ):
                exclusions.append(
                    ComparisonExclusion(
                        unit.unit_id,
                        row.market_ticker,
                        row.eligibility.value
                        if row.eligibility is not EvaluationEligibility.ELIGIBLE
                        else ComparisonEligibility.TARGET_VERSION_MISMATCH.value,
                        "A" if index == 0 else "B",
                    )
                )
                continue
            by_unit[unit.unit_id].append(row)
        grouped.append(by_unit)

    shared: list[SharedComparisonUnit] = []
    selected: dict[str, ReceiptEvaluation] = {}
    all_unit_ids = sorted(set(grouped[0]) | set(grouped[1]))
    for unit_id in all_unit_ids:
        a_rows, b_rows = grouped[0].get(unit_id, []), grouped[1].get(unit_id, [])
        unit = units[unit_id]
        if len(a_rows) > 1 or len(b_rows) > 1:
            exclusions.append(
                ComparisonExclusion(
                    unit_id,
                    unit.market_ticker,
                    ComparisonEligibility.AMBIGUOUS_DUPLICATE_UNIT.value,
                )
            )
        elif not a_rows or not b_rows:
            exclusions.append(
                ComparisonExclusion(
                    unit_id,
                    unit.market_ticker,
                    "A_ONLY" if a_rows else "B_ONLY",
                    "B" if a_rows else "A",
                )
            )
        else:
            a_row, b_row = a_rows[0], b_rows[0]
            shared.append(SharedComparisonUnit(unit, a_row.evaluation_id, b_row.evaluation_id))
            selected[a_row.evaluation_id] = a_row
            selected[b_row.evaluation_id] = b_row

    ordered_shared = tuple(sorted(shared, key=lambda item: item.unit.unit_id))
    ordered_exclusions = tuple(
        sorted(exclusions, key=lambda item: (item.unit_id, item.reason, item.contender or ""))
    )
    cohort_id = _cohort_digest(
        M26D_COMPARISON_POLICY_VERSION,
        contender_a,
        contender_b,
        start_at,
        end_at,
        ordered_shared,
        ordered_exclusions,
        (manifest_ids[0], manifest_ids[1]),
        M26D_TEMPORAL_POLICY,
        TemporalAlignment.UNALIGNED,
        InformationSetComparability.NOT_PROVEN,
        M26D_WINDOW_BASIS,
    )
    cohort = ComparisonCohort(
        cohort_id,
        M26D_COMPARISON_POLICY_VERSION,
        contender_a,
        contender_b,
        start_at,
        end_at,
        ordered_shared,
        ordered_exclusions,
        (manifest_ids[0], manifest_ids[1]),
        M26D_TEMPORAL_POLICY,
        TemporalAlignment.UNALIGNED,
        InformationSetComparability.NOT_PROVEN,
        M26D_WINDOW_BASIS,
        generated_at,
    )
    return cohort, selected


def _eligible_row_agrees(evaluation: ReceiptEvaluation, contender: ComparisonContender) -> bool:
    capability = _capability_for(
        contender.agent_id,
        contender.evaluation_policy_version,
        contender.outcome_semantics,
        contender.target_version,
    )
    return (
        capability is not None
        and evaluation.agent_id == contender.agent_id == capability.agent_id
        and evaluation.agent_version == contender.agent_version
        and evaluation.policy_version
        == contender.evaluation_policy_version
        == capability.evaluation_policy_version
        and evaluation.target.target_version
        == contender.target_version
        == capability.target_version
        and evaluation.model_brier is not None
    )


def _metrics(
    cohort: ComparisonCohort, selected: dict[str, ReceiptEvaluation]
) -> PairwiseDescriptiveMetrics:
    a_rows = [selected[item.contender_a_evaluation_id] for item in cohort.shared_units]
    b_rows = [selected[item.contender_b_evaluation_id] for item in cohort.shared_units]
    if any(row.model_brier is None for row in (*a_rows, *b_rows)):
        raise ComparisonError("shared eligible unit is missing Brier evidence")
    count = len(a_rows)
    a_brier = [row.model_brier for row in a_rows if row.model_brier is not None]
    b_brier = [row.model_brier for row in b_rows if row.model_brier is not None]

    def mean(values: list[Decimal]) -> Decimal:
        return sum(values, ZERO_INFLUENCE) / Decimal(len(values))

    def optional_mean(rows: list[ReceiptEvaluation]) -> Decimal | None:
        values = [
            row.market_relative_brier_improvement
            for row in rows
            if row.market_relative_brier_improvement is not None
        ]
        return None if len(values) != count else mean(values)

    return PairwiseDescriptiveMetrics(
        count,
        mean(a_brier),
        mean(b_brier),
        mean([a - b for a, b in zip(a_brier, b_brier, strict=True)]),
        optional_mean(a_rows),
        optional_mean(b_rows),
        tuple(sorted(Counter(row.decision.value for row in a_rows).items())),
        tuple(sorted(Counter(row.decision.value for row in b_rows).items())),
    )


def current_competition_snapshot(
    evaluations_available: bool,
) -> ResearchCompetitionSnapshot:
    """Truthful current registry projection; historical pairings are store-built separately."""
    availability = tuple(_availability(agent) for agent in AGENT_REGISTRY)
    if not evaluations_available:
        reason = "COMPARISON EVIDENCE UNAVAILABLE — persisted evidence failed validation."
        evidence_state = CompetitionEvidenceState.EVIDENCE_UNAVAILABLE
    else:
        reason = (
            "No valid cross-agent comparison yet. Event Edge has outcome-linked evidence, "
            "but no second agent currently has compatible outcome-performance semantics. "
            "Independent event identity is unavailable, so no winner is selected."
        )
        evidence_state = CompetitionEvidenceState.NO_COMPARISON
    return ResearchCompetitionSnapshot(
        availability,
        (),
        evidence_state,
        reason,
    )


def _availability(agent: AgentDefinition) -> ContenderAvailability:
    if agent.agent_id == "event-edge":
        return ContenderAvailability(
            agent.agent_id, agent.version, True, "Outcome evaluation available"
        )
    explanations = {
        "cross-market": "Realized two-venue outcome semantics unavailable",
        "breaking-signals": "No compatible probability/outcome evaluation yet",
        "resolution": "No standalone agent-performance semantics yet",
        "learning": "Evaluates research; not a competing prediction agent",
        "perps": "Strategy unavailable and disabled",
        "portfolio": "Strategy unavailable and disabled",
    }
    return ContenderAvailability(
        agent.agent_id,
        agent.version,
        False,
        explanations.get(agent.agent_id, "Outcome-performance semantics unsupported"),
    )


def _evaluation_manifest_provenance(
    contender: ComparisonContender,
    attempts: tuple[ReceiptEvaluation, ...],
    *,
    start_at: datetime,
    end_at: datetime,
) -> str:
    return _hash(
        {
            "contender": contender.material(),
            "dataset_semantics": "M26C_EVALUATION_ATTEMPT_HISTORY",
            "end_at": _time(end_at),
            "evaluation_ids": sorted(row.evaluation_id for row in attempts),
            "settlement_hashes": sorted({row.settlement_hash for row in attempts}),
            "start_at": _time(start_at),
        }
    )


def _cohort_identity(cohort: ComparisonCohort) -> str:
    return _cohort_digest(
        cohort.comparison_policy_version,
        cohort.contender_a,
        cohort.contender_b,
        cohort.start_at,
        cohort.end_at,
        cohort.shared_units,
        cohort.excluded_units,
        cohort.source_manifest_ids,
        cohort.temporal_policy,
        cohort.temporal_alignment,
        cohort.information_set_comparability,
        cohort.window_basis,
    )


def _cohort_digest(
    comparison_policy_version: str,
    contender_a: ComparisonContender,
    contender_b: ComparisonContender,
    start_at: datetime,
    end_at: datetime,
    shared_units: tuple[SharedComparisonUnit, ...],
    excluded_units: tuple[ComparisonExclusion, ...],
    source_manifest_ids: tuple[str, str],
    temporal_policy: str,
    temporal_alignment: TemporalAlignment,
    information_set_comparability: InformationSetComparability,
    window_basis: str,
) -> str:
    material = {
        "comparison_policy_version": comparison_policy_version,
        "contender_a": contender_a.material(),
        "contender_b": contender_b.material(),
        "domain": COMPARISON_COHORT_DOMAIN,
        "end_at": _time(end_at),
        "excluded_units": [
            [item.unit_id, item.market_ticker, item.reason, item.contender]
            for item in excluded_units
        ],
        "production_influence": "0",
        "shared_units": [
            [
                item.unit.unit_id,
                item.contender_a_evaluation_id,
                item.contender_b_evaluation_id,
            ]
            for item in shared_units
        ],
        "source_manifest_ids": list(source_manifest_ids),
        "start_at": _time(start_at),
        "temporal_policy": temporal_policy,
        "temporal_alignment": temporal_alignment.value,
        "information_set_comparability": information_set_comparability.value,
        "window_basis": window_basis,
    }
    return _hash(material)


def _utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ComparisonError(f"{name} must be UTC")


def _time(value: datetime) -> str:
    _utc(value, "timestamp")
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _hash(value: object) -> str:
    canonical = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()
