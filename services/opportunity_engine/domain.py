"""Immutable research TradeCandidate input boundary with zero execution authority."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


class OpportunityError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TradeCandidate:
    candidate_id: str
    market_ticker: str
    fair_probability: Decimal
    executable_price: Decimal
    fees: Decimal
    spread_cost: Decimal
    expected_slippage: Decimal
    fill_probability: Decimal
    uncertainty_reserve: Decimal
    liquidity_quality: Decimal
    correlation_reserve: Decimal
    capital_turnover_cost: Decimal
    information_decay: Decimal
    research_status: str = "SHADOW_CANDIDATE"
    production_influence: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        probabilities = (
            self.fair_probability,
            self.executable_price,
            self.fill_probability,
            self.liquidity_quality,
            self.information_decay,
        )
        if any(
            not value.is_finite() or not Decimal(0) <= value <= Decimal(1)
            for value in probabilities
        ):
            raise OpportunityError("candidate probability/quality input invalid")
        if self.production_influence != 0:
            raise OpportunityError("M10 candidate has zero production influence")

    @property
    def descriptive_after_cost_difference(self) -> Decimal:
        costs = (
            self.fees
            + self.spread_cost
            + self.expected_slippage
            + self.uncertainty_reserve
            + self.correlation_reserve
            + self.capital_turnover_cost
        )
        return (
            (self.fair_probability - self.executable_price - costs)
            * self.fill_probability
            * self.information_decay
        )
