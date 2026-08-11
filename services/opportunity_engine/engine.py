"""Fail-closed YES/NO economics, uncertainty gating, ranking, and hysteresis."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from .books import NormalizedBook, OutcomeSide, walk_depth
from .domain import OpportunityError
from .fees import FeePolicy, calculate_fee
from .models import (
    DecisionState,
    OutcomeEconomics,
    RejectionReason,
)


@dataclass(frozen=True, slots=True)
class ResearchThresholds:
    general: Decimal = Decimal(".05")
    weather_objective: Decimal = Decimal(".07")
    election_legal: Decimal = Decimal(".10")
    watch_margin: Decimal = Decimal(".015")
    close_guard: timedelta = timedelta(minutes=15)

    def for_family(self, family: str) -> Decimal:
        if family in {"weather", "macro", "objective_data"}:
            return self.weather_objective
        if family in {"election", "legal"}:
            return self.election_legal
        return self.general


def outcome_probabilities(
    p_yes: Decimal, lower_yes: Decimal, upper_yes: Decimal
) -> dict[OutcomeSide, tuple[Decimal, Decimal, Decimal]]:
    if not Decimal(0) <= lower_yes <= p_yes <= upper_yes <= Decimal(1):
        raise OpportunityError("forecast distribution invalid")
    return {
        OutcomeSide.YES: (p_yes, lower_yes, upper_yes),
        OutcomeSide.NO: (Decimal(1) - p_yes, Decimal(1) - upper_yes, Decimal(1) - lower_yes),
    }


def evaluate_outcome(
    side: OutcomeSide,
    fair: Decimal,
    conservative: Decimal,
    executable_price: Decimal,
    policy: FeePolicy,
    quantity: Decimal,
    slippage: Decimal,
) -> OutcomeEconomics:
    fee = calculate_fee(policy, executable_price, quantity).total_fee / quantity
    break_even = executable_price + fee + slippage
    return OutcomeEconomics(
        side,
        fair,
        conservative,
        executable_price,
        fair - executable_price,
        fair - executable_price,
        break_even,
        fee,
        slippage,
        conservative - break_even,
    )


def evaluate_both(
    book: NormalizedBook,
    p_yes: Decimal,
    lower_yes: Decimal,
    upper_yes: Decimal,
    policy: FeePolicy,
    quantity: Decimal,
) -> dict[OutcomeSide, OutcomeEconomics]:
    probabilities = outcome_probabilities(p_yes, lower_yes, upper_yes)
    output = {}
    for side, asks in ((OutcomeSide.YES, book.yes_asks), (OutcomeSide.NO, book.no_asks)):
        walk = walk_depth(asks, quantity)
        if not walk.complete or walk.average_price is None:
            raise OpportunityError("insufficient displayed depth")
        best = asks[0].price
        slippage = walk.average_price - best
        fair, conservative, _ = probabilities[side]
        output[side] = evaluate_outcome(
            side, fair, conservative, walk.average_price, policy, quantity, slippage
        )
    return output


def decide(
    economics: OutcomeEconomics,
    family: str,
    time_to_close: timedelta,
    reasons: set[RejectionReason],
    thresholds: ResearchThresholds,
    previous: DecisionState | None = None,
) -> tuple[DecisionState, tuple[RejectionReason, ...]]:
    if time_to_close <= thresholds.close_guard:
        reasons.add(RejectionReason.TOO_CLOSE_TO_CLOSE)
    if economics.conservative_expected_value <= 0:
        reasons.add(RejectionReason.INSUFFICIENT_SEPARATION)
    hurdle = thresholds.for_family(family)
    if economics.conservative_expected_value < hurdle:
        reasons.add(RejectionReason.NET_VALUE_BELOW_THRESHOLD)
    hard = reasons - {
        RejectionReason.NET_VALUE_BELOW_THRESHOLD,
        RejectionReason.INSUFFICIENT_SEPARATION,
    }
    if hard:
        state = DecisionState.REJECTED
    elif not economics.expected_fee.is_finite():
        state = DecisionState.INCOMPLETE
    elif economics.conservative_expected_value >= hurdle:
        state = DecisionState.RESEARCH_CANDIDATE
    else:
        state = DecisionState.WATCH
    # A prior candidate remains WATCH inside the margin; it never stays a candidate automatically.
    if (
        previous == DecisionState.RESEARCH_CANDIDATE
        and state == DecisionState.WATCH
        and economics.conservative_expected_value >= hurdle - thresholds.watch_margin
    ):
        state = DecisionState.WATCH
    return state, tuple(sorted(reasons))


def rank_score(
    conservative_ev: Decimal,
    forecast_quality: Decimal,
    fill_quality: Decimal,
    turnover: Decimal,
    liquidity: Decimal,
    correlation_factor: Decimal,
    semantic_factor: Decimal,
    decay: Decimal,
) -> Decimal:
    factors = (
        forecast_quality,
        fill_quality,
        turnover,
        liquidity,
        correlation_factor,
        semantic_factor,
        decay,
    )
    if any(not Decimal(0) <= factor <= Decimal(1) for factor in factors):
        raise OpportunityError("ranking component outside bounds")
    return (
        max(Decimal(0), conservative_ev)
        * forecast_quality
        * fill_quality
        * turnover
        * liquidity
        * correlation_factor
        * semantic_factor
        * decay
    )


def stale_reasons(
    now: datetime,
    book_at: datetime,
    forecast_at: datetime,
    evidence_at: datetime,
    max_book_age: timedelta,
    max_forecast_age: timedelta,
    max_evidence_age: timedelta,
) -> set[RejectionReason]:
    reasons = set()
    if now - book_at > max_book_age:
        reasons.add(RejectionReason.BOOK_STALE)
    if now - forecast_at > max_forecast_age:
        reasons.add(RejectionReason.FORECAST_STALE)
    if now - evidence_at > max_evidence_age:
        reasons.add(RejectionReason.INFORMATION_TOO_STALE)
    return reasons
