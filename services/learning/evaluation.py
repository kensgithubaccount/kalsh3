"""Event-level ablation, redundancy, timeliness, uncertainty, and concentration."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
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
    """Legacy record shape; its evidence label grants no promotion authority."""

    point: Decimal
    lower: Decimal
    upper: Decimal
    event_count: int
    evidence: str


@dataclass(frozen=True, slots=True)
class PairedEventSensitivity:
    """Content-bound descriptive diagnostics, without an inferential claim.

    Distinct IDs establish neither independent outcomes nor settlement authority.
    No dependence or repeated-look method has been accepted for M9 promotion.
    """

    events: tuple[EventContribution, ...]
    minimum: int = 50
    event_ids: tuple[str, ...] = field(init=False)
    event_count: int = field(init=False)
    event_manifest: str = field(init=False)
    point: Decimal = field(init=False)
    sensitivity_lower: Decimal | None = field(init=False)
    sensitivity_upper: Decimal | None = field(init=False)

    def __post_init__(self) -> None:
        if type(self.minimum) is not int or self.minimum < 50:
            raise LearningError("minimum must be an integer of at least 50 distinct events")
        if not self.events:
            raise LearningError("no settled events")
        seen: set[str] = set()
        for event in self.events:
            if not isinstance(event.event_id, str) or not event.event_id.strip():
                raise LearningError("event identity must be nonempty")
            if event.event_id in seen:
                raise LearningError("duplicate event identity; aggregate event records upstream")
            seen.add(event.event_id)
            for score in (event.full_brier, event.ablated_brier):
                if not isinstance(score, Decimal) or not score.is_finite() or not 0 <= score <= 1:
                    raise LearningError("Brier scores must be finite Decimals in [0, 1]")
            if type(event.source_published_ms) is not int or any(
                timestamp is not None and type(timestamp) is not int
                for timestamp in (event.kalshi_reaction_ms, event.forecast_updated_ms)
            ):
                raise LearningError("event timestamps must be integers or optional nulls")
        ordered = tuple(sorted(self.events, key=lambda event: event.event_id))
        records = [
            (
                event.event_id,
                str(event.full_brier),
                str(event.ablated_brier),
                event.source_published_ms,
                event.kalshi_reaction_ms,
                event.forecast_updated_ms,
            )
            for event in ordered
        ]
        encoded = json.dumps(
            ["m9-paired-event-records-v1", records], ensure_ascii=True, separators=(",", ":")
        ).encode("utf-8")
        total = sum((event.contribution for event in ordered), Decimal(0))
        leave_one = (
            [(total - event.contribution) / Decimal(len(ordered) - 1) for event in ordered]
            if len(ordered) > 1
            else []
        )
        object.__setattr__(self, "events", ordered)
        object.__setattr__(self, "event_ids", tuple(event.event_id for event in ordered))
        object.__setattr__(self, "event_count", len(ordered))
        object.__setattr__(self, "event_manifest", hashlib.sha256(encoded).hexdigest())
        object.__setattr__(self, "point", total / Decimal(len(ordered)))
        object.__setattr__(self, "sensitivity_lower", min(leave_one) if leave_one else None)
        object.__setattr__(self, "sensitivity_upper", max(leave_one) if leave_one else None)

    @property
    def evidence(self) -> str:
        return "INCONCLUSIVE"

    @property
    def reason(self) -> str:
        if self.event_count < self.minimum:
            return "INSUFFICIENT_DISTINCT_EVENTS"
        if self.point <= 0:
            return "NONPOSITIVE_OBSERVED_CONTRIBUTION"
        return "NO_ACCEPTED_INFERENCE_METHOD"


def paired_event_sensitivity(
    events: tuple[EventContribution, ...], minimum: int = 50
) -> PairedEventSensitivity:
    return PairedEventSensitivity(events, minimum)


def paired_event_interval(
    events: tuple[EventContribution, ...], minimum: int = 50
) -> PairedEventSensitivity:
    """Compatibility entrypoint; returns sensitivity, not a confidence interval."""
    return paired_event_sensitivity(events, minimum)


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
