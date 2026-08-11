"""Event-level ablation, redundancy, timeliness, uncertainty, and concentration."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .domain import AblationResult, LearningError


@dataclass(frozen=True, slots=True)
class EventContribution:
    event_id: str
    full_brier: Decimal
    ablated_brier: Decimal
    source_published_ms: int
    kalshi_reaction_ms: int | None
    forecast_updated_ms: int | None

    @property
    def contribution(self) -> Decimal:
        return self.ablated_brier - self.full_brier


@dataclass(frozen=True, slots=True)
class PerformanceInterval:
    point: Decimal
    lower: Decimal
    upper: Decimal
    event_count: int
    evidence: str


def paired_event_interval(
    events: tuple[EventContribution, ...], minimum: int = 50
) -> PerformanceInterval:
    if not events:
        raise LearningError("no settled events")
    values = sorted(event.contribution for event in events)
    mean = sum(values, Decimal(0)) / Decimal(len(values))
    # Conservative leave-one-event-out interval avoids contract-level pseudo-replication.
    leave_one = (
        [
            sum((value for index, value in enumerate(values) if index != omitted), Decimal(0))
            / Decimal(len(values) - 1)
            for omitted in range(len(values))
        ]
        if len(values) > 1
        else [Decimal(0)]
    )
    lower, upper = min(leave_one), max(leave_one)
    evidence = (
        "INCONCLUSIVE" if len(events) < minimum or lower <= 0 <= upper else "STRONGER_EVIDENCE"
    )
    return PerformanceInterval(mean, lower, upper, len(events), evidence)


@dataclass(frozen=True, slots=True)
class Concentration:
    best_event_fraction: Decimal
    top_five_fraction: Decimal
    excluding_best: Decimal
    excluding_worst: Decimal


def concentration(events: tuple[EventContribution, ...]) -> Concentration:
    values = sorted((event.contribution for event in events), reverse=True)
    total = sum((abs(value) for value in values), Decimal(0))

    def fraction(selected: list[Decimal]) -> Decimal:
        return Decimal(0) if total == 0 else sum((abs(v) for v in selected), Decimal(0)) / total

    def mean_without(index: int) -> Decimal:
        if len(values) <= 1:
            return Decimal(0)
        retained = [value for position, value in enumerate(values) if position != index]
        return sum(retained, Decimal(0)) / Decimal(len(retained))

    return Concentration(
        fraction(values[:1]), fraction(values[:5]), mean_without(0), mean_without(len(values) - 1)
    )


@dataclass(frozen=True, slots=True)
class Timeliness:
    median_lead_ms: Decimal | None
    before_kalshi_fraction: Decimal
    after_kalshi_fraction: Decimal
    forecast_impact_fraction: Decimal


def timeliness(events: tuple[EventContribution, ...]) -> Timeliness:
    comparable = [
        (event, reaction) for event in events if (reaction := event.kalshi_reaction_ms) is not None
    ]
    leads = sorted(Decimal(reaction - event.source_published_ms) for event, reaction in comparable)
    median = leads[len(leads) // 2] if leads else None
    divisor = Decimal(len(comparable) or 1)
    before = (
        Decimal(sum(event.source_published_ms < reaction for event, reaction in comparable))
        / divisor
    )
    impact = Decimal(sum(event.forecast_updated_ms is not None for event in events)) / Decimal(
        len(events) or 1
    )
    return Timeliness(median, before, Decimal(1) - before if comparable else Decimal(0), impact)


@dataclass(frozen=True, slots=True)
class Redundancy:
    source_a: str
    source_b: str
    claim_overlap: Decimal
    common_root_fraction: Decimal
    timing_correlation: Decimal
    residual_correlation: Decimal
    same_primary_dependence: Decimal

    @property
    def duplicated_credit_factor(self) -> Decimal:
        return Decimal(1) - max(
            self.claim_overlap, self.common_root_fraction, self.same_primary_dependence
        )


def ablation(
    component: str,
    family: str,
    events: tuple[EventContribution, ...],
    dataset: str,
    synthetic: bool,
    segment: tuple[tuple[str, str], ...] = (),
) -> AblationResult:
    full = sum((event.full_brier for event in events), Decimal(0)) / Decimal(len(events))
    without = sum((event.ablated_brier for event in events), Decimal(0)) / Decimal(len(events))
    return AblationResult(
        f"{dataset}:{component}",
        family,
        component,
        full,
        without,
        len(events),
        dataset,
        synthetic,
        raw_forecast_count=len(events),
        contract_count=len(events),
        unique_market_count=len(events),
        unique_event_count=len({e.event_id for e in events}),
        effective_sample_size=Decimal(len({e.event_id for e in events})),
        segment=segment,
    )
