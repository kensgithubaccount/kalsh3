"""Research-only perps observations and edge-decay measurements.

This package is intentionally pure: no network clients, credentials, order builders,
execution imports, or production influence.
"""

from .domain import (
    Direction,
    EdgeDecayObservation,
    LeverageEstimate,
    MarginMarketObservation,
    PortfolioMarginObservation,
    QuoteObservation,
)
from .edge_decay import measure_edge_decay

__all__ = [
    "Direction",
    "EdgeDecayObservation",
    "LeverageEstimate",
    "MarginMarketObservation",
    "PortfolioMarginObservation",
    "QuoteObservation",
    "measure_edge_decay",
]
