"""Balanced family research tournament and exploration-preserving budgets."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .domain import LearningError


@dataclass(frozen=True, slots=True)
class FamilyScore:
    family: str
    market_count: int
    usable_markets: int
    unique_settled_events: int
    forecast_coverage: Decimal
    market_relative_skill: Decimal | None
    calibration_quality: Decimal
    abstention_rate: Decimal
    data_completeness: Decimal
    source_reliability: Decimal
    median_latency_ms: Decimal
    liquidity_quality: Decimal
    monthly_research_cost: Decimal
    operational_complexity: Decimal
    research_score: Decimal
    capital_allocation: str = "NOT DETERMINED"

    @classmethod
    def calculate(
        cls,
        *,
        family: str,
        market_count: int,
        usable_markets: int,
        unique_settled_events: int,
        forecast_coverage: Decimal,
        market_relative_skill: Decimal | None,
        calibration_quality: Decimal,
        abstention_rate: Decimal,
        data_completeness: Decimal,
        source_reliability: Decimal,
        median_latency_ms: Decimal,
        liquidity_quality: Decimal,
        monthly_research_cost: Decimal,
        operational_complexity: Decimal,
    ) -> FamilyScore:
        skill = market_relative_skill or Decimal(0)
        evidence_factor = min(Decimal(1), Decimal(unique_settled_events) / Decimal(50))
        score = (
            evidence_factor
            * (
                forecast_coverage
                + skill
                + calibration_quality
                + data_completeness
                + source_reliability
                + liquidity_quality
                - abstention_rate
            )
            / Decimal(6)
        )
        return cls(
            family,
            market_count,
            usable_markets,
            unique_settled_events,
            forecast_coverage,
            market_relative_skill,
            calibration_quality,
            abstention_rate,
            data_completeness,
            source_reliability,
            median_latency_ms,
            liquidity_quality,
            monthly_research_cost,
            operational_complexity,
            score,
        )


@dataclass(frozen=True, slots=True)
class FamilyBudget:
    family: str
    forecast_jobs: int
    source_polls: int
    llm_units: int
    backfill_units: int


@dataclass(frozen=True, slots=True)
class ResearchBudget:
    total_forecast_jobs: int
    total_source_polls: int
    total_llm_units: int
    total_backfill_units: int
    exploration_floor: Decimal
    family_budgets: tuple[FamilyBudget, ...]
    production_influence: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.production_influence != 0 or not Decimal(0) < self.exploration_floor <= Decimal(1):
            raise LearningError("research budget is invalid or has production influence")
        family_count = len(self.family_budgets)
        floor_jobs = int(Decimal(self.total_forecast_jobs) * self.exploration_floor / family_count)
        if any(item.forecast_jobs < floor_jobs for item in self.family_budgets):
            raise LearningError("family research exploration floor violated")


def allocate_budget(
    scores: tuple[FamilyScore, ...], total_jobs: int, exploration_floor: Decimal = Decimal("0.20")
) -> ResearchBudget:
    if not scores:
        raise LearningError("no family scores")
    minimum = int(Decimal(total_jobs) * exploration_floor / Decimal(len(scores)))
    remaining = total_jobs - minimum * len(scores)
    positive = [max(Decimal(0), score.research_score) for score in scores]
    total = sum(positive, Decimal(0))
    allocations = [
        minimum
        + (remaining // len(scores) if total == 0 else int(Decimal(remaining) * value / total))
        for value in positive
    ]
    allocations[-1] += total_jobs - sum(allocations)
    budgets = tuple(
        FamilyBudget(score.family, jobs, jobs * 2, max(1, jobs // 10), jobs)
        for score, jobs in zip(scores, allocations, strict=True)
    )
    return ResearchBudget(
        total_jobs,
        total_jobs * 2,
        sum(x.llm_units for x in budgets),
        total_jobs,
        exploration_floor,
        budgets,
    )
