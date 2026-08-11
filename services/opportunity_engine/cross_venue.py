"""Semantic-gated cross-venue discrepancy observations with explicit leg risk."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class SemanticMatch(StrEnum):
    IDENTICAL = "IDENTICAL"
    HEDGEABLE_WITH_BASIS = "HEDGEABLE_WITH_BASIS"
    RELATED_ONLY = "RELATED_ONLY"
    INCOMPATIBLE = "INCOMPATIBLE"


@dataclass(frozen=True, slots=True)
class CrossVenueOpportunityObservation:
    observation_id: str
    semantic_match: SemanticMatch
    kalshi_price: Decimal
    kalshi_depth: Decimal
    polymarket_price: Decimal
    polymarket_depth: Decimal
    kalshi_fee: Decimal | None
    polymarket_fee: Decimal | None
    expected_slippage: Decimal | None
    timestamp_skew_ms: int
    semantic_reserve: Decimal
    leg_risk_reserve: Decimal
    venue_state_risk: str
    reference_overlap: bool
    research_state: str
    production_influence: Decimal = Decimal(0)

    @classmethod
    def evaluate(
        cls,
        *,
        observation_id: str,
        semantic_match: SemanticMatch,
        kalshi_price: Decimal,
        kalshi_depth: Decimal,
        polymarket_price: Decimal,
        polymarket_depth: Decimal,
        kalshi_fee: Decimal | None,
        polymarket_fee: Decimal | None,
        expected_slippage: Decimal | None,
        timestamp_skew_ms: int,
        semantic_reserve: Decimal,
        leg_risk_reserve: Decimal,
        venue_state_risk: str,
        reference_overlap: bool,
    ) -> CrossVenueOpportunityObservation:
        allowed = semantic_match in {SemanticMatch.IDENTICAL, SemanticMatch.HEDGEABLE_WITH_BASIS}
        known = None not in {kalshi_fee, polymarket_fee, expected_slippage}
        discrepancy = abs(kalshi_price - polymarket_price)
        hurdle = (
            (kalshi_fee or 0)
            + (polymarket_fee or 0)
            + (expected_slippage or 0)
            + semantic_reserve
            + leg_risk_reserve
            + Decimal(".015")
        )
        state = (
            "INCOMPLETE"
            if not known
            else "SEMANTIC_MATCH_INSUFFICIENT"
            if not allowed
            else "CROSS_VENUE_RESEARCH_CANDIDATE"
            if discrepancy >= max(Decimal(".04"), hurdle)
            else "WATCH"
        )
        if reference_overlap and state == "CROSS_VENUE_RESEARCH_CANDIDATE":
            state = "WATCH"
        return cls(
            observation_id,
            semantic_match,
            kalshi_price,
            kalshi_depth,
            polymarket_price,
            polymarket_depth,
            kalshi_fee,
            polymarket_fee,
            expected_slippage,
            timestamp_skew_ms,
            semantic_reserve,
            leg_risk_reserve,
            venue_state_risk,
            reference_overlap,
            state,
        )
