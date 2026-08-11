"""Human-gated promotion, quarantine, drift, and champion/challenger governance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from .domain import LearningError
from .evaluation import PerformanceInterval


class SourceState(StrEnum):
    CANDIDATE = "CANDIDATE"
    SHADOW = "SHADOW"
    ELIGIBLE = "ELIGIBLE"
    LIMITED_PRODUCTION = "LIMITED_PRODUCTION"
    APPROVED = "APPROVED"
    QUARANTINED = "QUARANTINED"
    DISABLED = "DISABLED"
    SETUP_REQUIRED = "SETUP_REQUIRED"


class ModelState(StrEnum):
    DEVELOPMENT = "DEVELOPMENT"
    RESEARCH = "RESEARCH"
    CHALLENGER = "CHALLENGER"
    RESEARCH_CHAMPION = "RESEARCH_CHAMPION"
    ELIGIBLE = "ELIGIBLE"
    QUARANTINED = "QUARANTINED"
    RETIRED = "RETIRED"


class ProposalType(StrEnum):
    PROMOTION_PROPOSAL = "PROMOTION_PROPOSAL"
    DEMOTION_PROPOSAL = "DEMOTION_PROPOSAL"
    QUARANTINE_PROPOSAL = "QUARANTINE_PROPOSAL"


@dataclass(frozen=True, slots=True)
class EvaluationWindow:
    development: tuple[datetime, datetime]
    validation: tuple[datetime, datetime]
    promotion: tuple[datetime, datetime]

    def __post_init__(self) -> None:
        if not (
            self.development[1] <= self.validation[0] and self.validation[1] <= self.promotion[0]
        ):
            raise LearningError("development, validation, and promotion windows overlap")


@dataclass(frozen=True, slots=True)
class GovernanceProposal:
    proposal_id: str
    proposal_type: ProposalType
    component_id: str
    current_state: str
    proposed_state: str
    interval: PerformanceInterval | None
    unique_settled_events: int
    same_event_manifest: str
    rationale: str
    human_approval_required: bool = True
    production_influence: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.production_influence != 0 or not self.human_approval_required:
            raise LearningError("governance proposals are human-gated with zero influence")
        if self.proposal_type == ProposalType.PROMOTION_PROPOSAL and (
            self.unique_settled_events < 50
            or self.interval is None
            or self.interval.evidence != "STRONGER_EVIDENCE"
        ):
            raise LearningError("promotion evidence threshold not met")


@dataclass(frozen=True, slots=True)
class DriftMetric:
    component_id: str
    dimension: str
    baseline: Decimal
    current: Decimal
    warning_threshold: Decimal
    quarantine_threshold: Decimal

    @property
    def action(self) -> str:
        change = abs(self.current - self.baseline)
        if change >= self.quarantine_threshold:
            return "QUARANTINE_PROPOSAL"
        if change >= self.warning_threshold:
            return "WARNING"
        return "NONE"


def compare_challenger(
    champion_events: tuple[str, ...],
    challenger_events: tuple[str, ...],
    champion_score: Decimal,
    challenger_score: Decimal,
    promotion_interval: PerformanceInterval,
) -> bool:
    if champion_events != challenger_events:
        raise LearningError("champion and challenger evaluated on different events")
    return challenger_score < champion_score and promotion_interval.evidence == "STRONGER_EVIDENCE"
