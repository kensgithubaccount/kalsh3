"""Research-only, structure-aware passive market-making evidence."""

from .domain import (
    ComparisonDirection,
    FairValueCurve,
    FairValueEligibility,
    FairValuePoint,
    InventorySnapshot,
    MarketMakingError,
    QuoteBlocker,
    QuotePolicy,
    ShadowMarketSnapshot,
    ShadowQuote,
    ShadowQuotePlan,
    ShadowQuoteState,
)
from .planner import default_shadow_quote_policy, plan_shadow_quotes

__all__ = (
    "ComparisonDirection",
    "FairValueCurve",
    "FairValueEligibility",
    "FairValuePoint",
    "InventorySnapshot",
    "MarketMakingError",
    "QuoteBlocker",
    "QuotePolicy",
    "ShadowMarketSnapshot",
    "ShadowQuote",
    "ShadowQuotePlan",
    "ShadowQuoteState",
    "default_shadow_quote_policy",
    "plan_shadow_quotes",
)
