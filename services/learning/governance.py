"""Human-gated promotion, quarantine, drift, and champion/challenger governance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from .domain import LearningError
from .evaluation import (
    MINIMUM_UNIQUE_SETTLED_EVENTS,
    STRONGER_EVIDENCE,
    PerformanceInterval,
)


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
    """Human-gated, zero-influence proposal.

    A promotion proposal may not carry a bare ``unique_settled_events`` count:
    the count is bound to ``event_manifest``, and any mismatch between the two
    fails closed.  ``incremental_effect`` is the independently supplied
    after-cost / market-relative effect and must be strictly positive.

    ``event_manifest`` is a *descriptive* record of the distinct event ids the
    caller claims the evidence was computed over -- it is checked only for
    internal self-consistency (no duplicates, length matches the supplied
    count and the interval's ``event_count``).  There is no authoritative
    evaluation-cohort issuer at this checkpoint, so a caller-authored manifest,
    however large or internally consistent, confers no promotion authority by
    itself.  ``same_event_manifest`` is likewise descriptive only.  Promotion
    is gated on ``interval.evidence == STRONGER_EVIDENCE``, which no current
    ``PerformanceInterval`` can carry (see ``evaluation.py``), so
    ``PROMOTION_PROPOSAL`` fails closed regardless of manifest contents.
    """

    proposal_id: str
    proposal_type: ProposalType
    component_id: str
    current_state: str
    proposed_state: str
    interval: PerformanceInterval | None
    unique_settled_events: int
    same_event_manifest: str
    rationale: str
    event_manifest: tuple[str, ...] = ()
    incremental_effect: Decimal = Decimal("0")
    human_approval_required: bool = True
    production_influence: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.production_influence != 0 or not self.human_approval_required:
            raise LearningError("governance proposals are human-gated with zero influence")
        if self.proposal_type != ProposalType.PROMOTION_PROPOSAL:
            return
        if len(set(self.event_manifest)) != len(self.event_manifest):
            raise LearningError("event manifest contains duplicate event ids")
        if len(self.event_manifest) != self.unique_settled_events:
            raise LearningError("proposal event count does not match the event manifest")
        if self.unique_settled_events < MINIMUM_UNIQUE_SETTLED_EVENTS:
            raise LearningError("promotion evidence threshold not met")
        if self.interval is None or self.interval.event_count != self.unique_settled_events:
            raise LearningError("promotion interval does not match the event manifest")
        # Direction is required independently of the evidence state so negative
        # or flat results can never promote even if a future inferential method
        # reports confidence.
        if self.incremental_effect <= 0 or not self.interval.positive_direction:
            raise LearningError("promotion requires positive incremental effect")
        if not self.interval.meets_event_floor:
            raise LearningError("promotion evidence threshold not met")
        if self.interval.evidence != STRONGER_EVIDENCE:
            # Authoritative promotion evidence does not exist at this
            # checkpoint: no PerformanceInterval can carry STRONGER_EVIDENCE
            # (see evaluation.py), so this is the fail-closed floor for every
            # promotion proposal, independent of manifest size or contents.
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
    """Same-event challenger comparison.

    Fails closed on duplicate ids, differing event sets, or an interval computed
    over a different number of events.  Returns ``True`` only for a strictly
    better challenger on a floor-satisfying, positively directed result whose
    evidence state is ``STRONGER_EVIDENCE``.  No ``PerformanceInterval`` can
    carry that state at this checkpoint (see ``evaluation.py``), so this
    always returns ``False`` regardless of what a caller supplies as
    ``champion_events``/``challenger_events`` -- those identifiers are
    descriptive comparison inputs, not a source of promotion authority.
    """
    if len(set(champion_events)) != len(champion_events):
        raise LearningError("duplicate event ids cannot increase the settled-event denominator")
    if champion_events != challenger_events:
        raise LearningError("champion and challenger evaluated on different events")
    if promotion_interval.event_count != len(champion_events):
        raise LearningError("promotion interval does not match the compared event manifest")
    return (
        challenger_score < champion_score
        and promotion_interval.meets_event_floor
        and promotion_interval.positive_direction
        and promotion_interval.evidence == STRONGER_EVIDENCE
    )
