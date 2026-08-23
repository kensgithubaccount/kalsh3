"""Generic autonomous shadow-ranking and settlement-learning contracts for M28.

The module is deliberately pure. It turns already-produced model probabilities and
already-reconstructed economics into deterministic shadow decisions and later evaluation
records. It has no network transport, credential access, account reads, production-state
mutation, risk authorization, approval, execution authorization, burn, or order path.

The same contracts are market-family agnostic so weather can be the first proving ground
without becoming the architectural limit.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from services.historical_replay.archive import stable_hash
from services.production_weather_strategy.historical_economics import TradeSide


class ShadowLoopError(ValueError):
    """A shadow-ranking or learning invariant was violated."""


@dataclass(frozen=True, slots=True)
class ShadowRankingPolicy:
    policy_id: str
    minimum_after_cost_edge: Decimal
    maximum_candidates: int
    one_candidate_per_event: bool
    family_limits: tuple[tuple[str, int], ...]
    content_hash: str

    @classmethod
    def build(
        cls,
        *,
        minimum_after_cost_edge: Decimal,
        maximum_candidates: int,
        one_candidate_per_event: bool = True,
        family_limits: tuple[tuple[str, int], ...] = (),
    ) -> ShadowRankingPolicy:
        if not minimum_after_cost_edge.is_finite() or not Decimal(
            "0"
        ) <= minimum_after_cost_edge < Decimal("1"):
            raise ShadowLoopError("minimum edge must be finite and in [0,1)")
        if maximum_candidates < 1:
            raise ShadowLoopError("maximum candidate count must be positive")
        normalized = tuple(sorted(family_limits))
        names = [family for family, _ in normalized]
        if len(names) != len(set(names)):
            raise ShadowLoopError("family ranking limits must be unique")
        if any(not family.strip() or limit < 1 for family, limit in normalized):
            raise ShadowLoopError("family ranking limits are invalid")
        material = (
            "m28e-shadow-ranking-policy-v1",
            str(minimum_after_cost_edge),
            maximum_candidates,
            one_candidate_per_event,
            normalized,
        )
        digest = stable_hash(material)
        return cls(
            policy_id=digest,
            minimum_after_cost_edge=minimum_after_cost_edge,
            maximum_candidates=maximum_candidates,
            one_candidate_per_event=one_candidate_per_event,
            family_limits=normalized,
            content_hash=digest,
        )

    def family_limit(self, family: str) -> int | None:
        return dict(self.family_limits).get(family)


@dataclass(frozen=True, slots=True)
class ShadowOpportunity:
    opportunity_id: str
    family: str
    event_ticker: str
    market_ticker: str
    model_id: str
    model_yes_probability: Decimal
    selected_side: TradeSide
    selected_side_probability: Decimal
    all_in_cost: Decimal
    after_cost_edge: Decimal
    maximum_loss: Decimal
    evidence_ids: tuple[str, ...]
    observed_at: datetime
    decision_cutoff: datetime
    content_hash: str

    @classmethod
    def build(
        cls,
        *,
        family: str,
        event_ticker: str,
        market_ticker: str,
        model_id: str,
        model_yes_probability: Decimal,
        yes_all_in_cost: Decimal,
        no_all_in_cost: Decimal,
        evidence_ids: tuple[str, ...],
        observed_at: datetime,
        decision_cutoff: datetime,
    ) -> ShadowOpportunity:
        for value, name in (
            (family, "family"),
            (event_ticker, "event ticker"),
            (market_ticker, "market ticker"),
            (model_id, "model id"),
        ):
            if not value.strip():
                raise ShadowLoopError(f"{name} is required")
        if not evidence_ids or len(evidence_ids) != len(set(evidence_ids)):
            raise ShadowLoopError("shadow evidence ids must be nonempty and unique")
        observed = _utc(observed_at, "observed_at")
        cutoff = _utc(decision_cutoff, "decision_cutoff")
        if observed > cutoff:
            raise ShadowLoopError("shadow opportunity was observed after decision cutoff")
        for value, name in (
            (model_yes_probability, "model probability"),
            (yes_all_in_cost, "YES all-in cost"),
            (no_all_in_cost, "NO all-in cost"),
        ):
            if not value.is_finite() or not Decimal("0") <= value <= Decimal("1"):
                raise ShadowLoopError(f"{name} is outside [0,1]")
        model_no_probability = Decimal("1") - model_yes_probability
        yes_edge = model_yes_probability - yes_all_in_cost
        no_edge = model_no_probability - no_all_in_cost
        if yes_edge >= no_edge:
            side = TradeSide.YES
            side_probability = model_yes_probability
            all_in_cost = yes_all_in_cost
            edge = yes_edge
        else:
            side = TradeSide.NO
            side_probability = model_no_probability
            all_in_cost = no_all_in_cost
            edge = no_edge
        normalized_evidence = tuple(sorted(evidence_ids))
        material = (
            "m28e-shadow-opportunity-v1",
            family,
            event_ticker,
            market_ticker,
            model_id,
            str(model_yes_probability),
            side.value,
            str(side_probability),
            str(all_in_cost),
            str(edge),
            normalized_evidence,
            observed.isoformat(),
            cutoff.isoformat(),
        )
        digest = stable_hash(material)
        return cls(
            opportunity_id=digest,
            family=family,
            event_ticker=event_ticker,
            market_ticker=market_ticker,
            model_id=model_id,
            model_yes_probability=model_yes_probability,
            selected_side=side,
            selected_side_probability=side_probability,
            all_in_cost=all_in_cost,
            after_cost_edge=edge,
            maximum_loss=all_in_cost,
            evidence_ids=normalized_evidence,
            observed_at=observed,
            decision_cutoff=cutoff,
            content_hash=digest,
        )


@dataclass(frozen=True, slots=True)
class ShadowScanDecision:
    decision_id: str
    policy_id: str
    considered_opportunity_ids: tuple[str, ...]
    selected_opportunity_ids: tuple[str, ...]
    rejected: tuple[tuple[str, str], ...]
    content_hash: str


def rank_shadow_opportunities(
    opportunities: tuple[ShadowOpportunity, ...],
    *,
    policy: ShadowRankingPolicy,
) -> ShadowScanDecision:
    """Rank edge after costs with deterministic event/family concentration bounds."""

    if len({item.opportunity_id for item in opportunities}) != len(opportunities):
        raise ShadowLoopError("duplicate shadow opportunity identity")
    ordered = sorted(
        opportunities,
        key=lambda item: (
            -item.after_cost_edge,
            item.family,
            item.event_ticker,
            item.market_ticker,
        ),
    )
    selected: list[ShadowOpportunity] = []
    rejected: list[tuple[str, str]] = []
    used_events: set[str] = set()
    family_counts: dict[str, int] = defaultdict(int)
    for item in ordered:
        if item.after_cost_edge < policy.minimum_after_cost_edge:
            rejected.append((item.opportunity_id, "EDGE_BELOW_POLICY"))
            continue
        if policy.one_candidate_per_event and item.event_ticker in used_events:
            rejected.append((item.opportunity_id, "EVENT_CONCENTRATION"))
            continue
        family_limit = policy.family_limit(item.family)
        if family_limit is not None and family_counts[item.family] >= family_limit:
            rejected.append((item.opportunity_id, "FAMILY_CONCENTRATION"))
            continue
        if len(selected) >= policy.maximum_candidates:
            rejected.append((item.opportunity_id, "GLOBAL_CANDIDATE_LIMIT"))
            continue
        selected.append(item)
        used_events.add(item.event_ticker)
        family_counts[item.family] += 1
    considered_ids = tuple(sorted(item.opportunity_id for item in opportunities))
    selected_ids = tuple(item.opportunity_id for item in selected)
    rejected_rows = tuple(sorted(rejected))
    material = (
        "m28e-shadow-scan-decision-v1",
        policy.policy_id,
        considered_ids,
        selected_ids,
        rejected_rows,
    )
    digest = stable_hash(material)
    return ShadowScanDecision(
        decision_id=digest,
        policy_id=policy.policy_id,
        considered_opportunity_ids=considered_ids,
        selected_opportunity_ids=selected_ids,
        rejected=rejected_rows,
        content_hash=digest,
    )


@dataclass(frozen=True, slots=True)
class ShadowSettledOutcome:
    outcome_id: str
    opportunity_id: str
    event_ticker: str
    market_ticker: str
    model_id: str
    realized_yes: int
    brier_score: Decimal
    hypothetical_pnl: Decimal
    settled_at: datetime
    settlement_evidence_id: str
    content_hash: str

    @classmethod
    def build(
        cls,
        opportunity: ShadowOpportunity,
        *,
        realized_yes: int,
        settled_at: datetime,
        settlement_evidence_id: str,
    ) -> ShadowSettledOutcome:
        if realized_yes not in {0, 1}:
            raise ShadowLoopError("shadow settlement result must be binary")
        if not settlement_evidence_id.strip():
            raise ShadowLoopError("settlement evidence id is required")
        settled = _utc(settled_at, "settled_at")
        if settled <= opportunity.decision_cutoff:
            raise ShadowLoopError("shadow outcome settled before its decision cutoff")
        realized = Decimal(realized_yes)
        brier = (opportunity.model_yes_probability - realized) ** 2
        won = (opportunity.selected_side is TradeSide.YES and realized_yes == 1) or (
            opportunity.selected_side is TradeSide.NO and realized_yes == 0
        )
        pnl = Decimal("1") - opportunity.all_in_cost if won else -opportunity.all_in_cost
        material = (
            "m28e-shadow-settled-outcome-v1",
            opportunity.opportunity_id,
            realized_yes,
            str(brier),
            str(pnl),
            settled.isoformat(),
            settlement_evidence_id,
        )
        digest = stable_hash(material)
        return cls(
            outcome_id=digest,
            opportunity_id=opportunity.opportunity_id,
            event_ticker=opportunity.event_ticker,
            market_ticker=opportunity.market_ticker,
            model_id=opportunity.model_id,
            realized_yes=realized_yes,
            brier_score=brier,
            hypothetical_pnl=pnl,
            settled_at=settled,
            settlement_evidence_id=settlement_evidence_id,
            content_hash=digest,
        )


@dataclass(frozen=True, slots=True)
class ShadowLearningSummary:
    summary_id: str
    unique_events: int
    settled_opportunities: int
    mean_brier_score: Decimal
    hypothetical_total_pnl: Decimal
    hypothetical_win_rate: Decimal
    content_hash: str


def summarize_shadow_outcomes(
    outcomes: tuple[ShadowSettledOutcome, ...],
) -> ShadowLearningSummary:
    if not outcomes:
        raise ShadowLoopError("shadow learning summary cannot be empty")
    if len({item.outcome_id for item in outcomes}) != len(outcomes):
        raise ShadowLoopError("duplicate shadow outcome identity")
    event_groups: dict[str, list[ShadowSettledOutcome]] = defaultdict(list)
    for item in outcomes:
        event_groups[item.event_ticker].append(item)
    event_briers = [
        sum((item.brier_score for item in rows), Decimal("0")) / Decimal(len(rows))
        for rows in event_groups.values()
    ]
    mean_brier = sum(event_briers, Decimal("0")) / Decimal(len(event_briers))
    total_pnl = sum((item.hypothetical_pnl for item in outcomes), Decimal("0"))
    wins = sum(1 for item in outcomes if item.hypothetical_pnl > 0)
    win_rate = Decimal(wins) / Decimal(len(outcomes))
    ordered_ids = tuple(sorted(item.outcome_id for item in outcomes))
    material = (
        "m28e-shadow-learning-summary-v1",
        ordered_ids,
        len(event_groups),
        str(mean_brier),
        str(total_pnl),
        str(win_rate),
    )
    digest = stable_hash(material)
    return ShadowLearningSummary(
        summary_id=digest,
        unique_events=len(event_groups),
        settled_opportunities=len(outcomes),
        mean_brier_score=mean_brier,
        hypothetical_total_pnl=total_pnl,
        hypothetical_win_rate=win_rate,
        content_hash=digest,
    )


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ShadowLoopError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)
