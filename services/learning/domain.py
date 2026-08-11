"""Immutable ablation measurements and human-gated research-weight proposals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class LearningError(ValueError):
    pass


class ProposalState(StrEnum):
    PROPOSED = "PROPOSED"
    APPROVED_RESEARCH = "APPROVED_RESEARCH"
    REJECTED = "REJECTED"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass(frozen=True, slots=True)
class AblationResult:
    artifact_id: str
    family: str
    component_id: str
    full_model_brier: Decimal
    ablated_model_brier: Decimal
    settled_event_count: int
    dataset_manifest: str
    synthetic: bool
    target_type: str = "OUTCOME_PREDICTION"
    raw_forecast_count: int = 0
    contract_count: int = 0
    unique_market_count: int = 0
    unique_event_count: int = 0
    effective_sample_size: Decimal = Decimal("0")
    segment: tuple[tuple[str, str], ...] = ()

    @property
    def descriptive_incremental_brier(self) -> Decimal:
        return self.ablated_model_brier - self.full_model_brier


@dataclass(frozen=True, slots=True)
class ResearchWeightProposal:
    proposal_id: str
    component_id: str
    previous_weight: Decimal
    proposed_weight: Decimal
    maximum_change: Decimal
    evidence_manifest: str
    evidence_window: tuple[datetime, datetime] | None = None
    effective_sample_size: Decimal = Decimal("0")
    metrics: tuple[tuple[str, Decimal], ...] = ()
    uncertainty: tuple[Decimal, Decimal] | None = None
    rationale: str = ""
    constraints_applied: tuple[str, ...] = ()
    rollback_target: str = ""
    proposer_version: str = "m9-v1"
    created_at: datetime | None = None
    state: ProposalState = ProposalState.PROPOSED
    production_influence: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if abs(self.proposed_weight - self.previous_weight) > self.maximum_change:
            raise LearningError("research weight change exceeds governance bound")
        if self.production_influence != 0:
            raise LearningError("M9 proposal cannot have production influence")
        if self.maximum_change > Decimal("0.10"):
            raise LearningError("weekly source/model change cap exceeds 10 percentage points")
