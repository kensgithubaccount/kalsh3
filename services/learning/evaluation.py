"""Event-level ablation, redundancy, timeliness, uncertainty, and concentration."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .domain import AblationResult, LearningError

# Evidence states.  ``STRONGER_EVIDENCE`` names the state a *prespecified and
# valid* inferential method would have to produce before governance may promote.
# No such method exists yet for this estimator, so nothing in M9 can currently
# emit it and every promotion path stays fail-closed on ``INCONCLUSIVE``.
INCONCLUSIVE = "INCONCLUSIVE"
STRONGER_EVIDENCE = "STRONGER_EVIDENCE"

# Leave-one-event-out extrema describe only how far the point estimate moves
# when a single event is dropped.  They are a stability/influence diagnostic:
# they carry no coverage guarantee, they do not model dependence between events,
# and they do not correct for repeated looks.  They must never be read as a
# confidence interval.  27 events at +0.1 and 23 at -0.1 yield extrema that
# exclude zero purely because the denominator is large, not because a real
# effect was demonstrated.
LEAVE_ONE_EVENT_OUT_SENSITIVITY = "LEAVE_ONE_EVENT_OUT_SENSITIVITY"

# Floor on distinct authoritative settled events.  It may be raised by a caller
# but never lowered.
MINIMUM_UNIQUE_SETTLED_EVENTS = 50


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
    """Descriptive point estimate plus a leave-one-event-out sensitivity range.

    ``sensitivity_low``/``sensitivity_high`` are deliberately *not* named
    ``lower``/``upper``: they are not interval bounds and carry no inferential
    meaning.

    ``evidence`` cannot currently leave ``INCONCLUSIVE``.  No prespecified,
    accepted inferential method exists at this checkpoint, so there is no
    allow-list to validate a caller-supplied ``inferential_method`` name
    against -- a caller simply *naming* one must never be treated as
    authority.  The constructor therefore rejects any ``evidence`` value other
    than ``INCONCLUSIVE`` outright, and rejects any non-``None``
    ``inferential_method``, regardless of what either claims.
    ``STRONGER_EVIDENCE`` remains defined only as the symbolic state a future
    prespecified method would have to produce; nothing can instantiate a
    ``PerformanceInterval`` carrying it today.
    """

    point: Decimal
    sensitivity_low: Decimal
    sensitivity_high: Decimal
    event_count: int
    evidence: str
    meets_event_floor: bool = False
    method: str = LEAVE_ONE_EVENT_OUT_SENSITIVITY
    inferential_method: str | None = None

    def __post_init__(self) -> None:
        if self.sensitivity_low > self.sensitivity_high:
            raise LearningError("sensitivity range is inverted")
        if self.event_count < 0:
            raise LearningError("event count cannot be negative")
        if self.evidence != INCONCLUSIVE:
            # No accepted inferential method exists at this checkpoint, so
            # there is nothing to validate a caller-supplied
            # ``inferential_method`` name against.  A caller naming one is
            # not authority: reject unconditionally, regardless of the name.
            raise LearningError(
                "no prespecified inferential method exists at this checkpoint; "
                "evidence stronger than INCONCLUSIVE cannot currently be constructed"
            )
        if self.inferential_method is not None:
            raise LearningError(
                "inferential_method must remain unset until an accepted method is prespecified"
            )

    @property
    def positive_direction(self) -> bool:
        """Descriptive direction check: improvement that no single event flips.

        This is a direction diagnostic, not evidence of significance.  It is a
        necessary condition for promotion, never a sufficient one.
        """
        return self.point > 0 and self.sensitivity_low > 0


def paired_event_interval(
    events: tuple[EventContribution, ...], minimum: int = MINIMUM_UNIQUE_SETTLED_EVENTS
) -> PerformanceInterval:
    """Summarise per-event contributions over *distinct* authoritative events.

    Returns a descriptive point estimate and a leave-one-event-out sensitivity
    range.  The evidence state is always ``INCONCLUSIVE``: leave-one-out extrema
    are a stability diagnostic, and no prespecified inferential method that
    handles event dependence and repeated looks has been established for this
    estimator.  Promotion therefore cannot be justified from this result alone.
    """
    if not events:
        raise LearningError("no settled events")
    if minimum < MINIMUM_UNIQUE_SETTLED_EVENTS:
        raise LearningError("unique settled event floor cannot be lowered below 50")
    identifiers = [event.event_id for event in events]
    if len(set(identifiers)) != len(identifiers):
        # Duplicate ids are contract-level pseudo-replication: they must never
        # enlarge the denominator, and silently collapsing them would hide an
        # unreconciled input, so this fails closed.
        raise LearningError("duplicate event ids cannot increase the settled-event denominator")
    values = sorted(event.contribution for event in events)
    mean = sum(values, Decimal(0)) / Decimal(len(values))
    leave_one = (
        [
            sum((value for index, value in enumerate(values) if index != omitted), Decimal(0))
            / Decimal(len(values) - 1)
            for omitted in range(len(values))
        ]
        if len(values) > 1
        else [Decimal(0)]
    )
    return PerformanceInterval(
        mean,
        min(leave_one),
        max(leave_one),
        len(identifiers),
        INCONCLUSIVE,
        meets_event_floor=len(identifiers) >= minimum,
    )


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
