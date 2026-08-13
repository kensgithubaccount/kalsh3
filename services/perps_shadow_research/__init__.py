"""Research-only perps observations and edge-decay measurements.

This package is intentionally pure: no network clients, credentials, order builders,
execution imports, or production influence.
"""

from .book_evidence import BookEvidenceObservation, BookUpdateKind
from .domain import (
    Direction,
    EdgeDecayObservation,
    LeverageEstimate,
    MarginMarketObservation,
    PortfolioMarginObservation,
    QuoteObservation,
)
from .edge_decay import measure_edge_decay
from .pipeline import ReadOnlyBookEvidencePipeline
from .store import BookEvidenceStore

__all__ = [
    "BookEvidenceObservation",
    "BookEvidenceStore",
    "BookUpdateKind",
    "Direction",
    "EdgeDecayObservation",
    "LeverageEstimate",
    "MarginMarketObservation",
    "PortfolioMarginObservation",
    "QuoteObservation",
    "ReadOnlyBookEvidencePipeline",
    "measure_edge_decay",
]
