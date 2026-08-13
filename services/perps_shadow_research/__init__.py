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
from .margin_protocol import MarginChannel, MarginProtocolState
from .perps_events import PerpsBookDeltaEvent, PerpsBookSnapshotEvent, PerpsTickerEvent
from .perps_evidence import PerpsBookEvidenceObservation, PerpsMarketStateObservation
from .perps_metadata import PerpsMarketMetadata, parse_perps_market
from .perps_orderbook import PerpsBookState, PerpsSequencedBook
from .perps_runtime import OfflinePerpsEvidenceRuntime, ScriptedPerpsTransport
from .perps_store import PerpsEvidenceStore
from .pipeline import ReadOnlyBookEvidencePipeline
from .store import BookEvidenceStore

__all__ = [
    "BookEvidenceObservation",
    "BookEvidenceStore",
    "BookUpdateKind",
    "Direction",
    "EdgeDecayObservation",
    "LeverageEstimate",
    "MarginChannel",
    "MarginMarketObservation",
    "MarginProtocolState",
    "OfflinePerpsEvidenceRuntime",
    "PerpsBookDeltaEvent",
    "PerpsBookEvidenceObservation",
    "PerpsBookSnapshotEvent",
    "PerpsBookState",
    "PerpsEvidenceStore",
    "PerpsMarketMetadata",
    "PerpsMarketStateObservation",
    "PerpsSequencedBook",
    "PerpsTickerEvent",
    "PortfolioMarginObservation",
    "QuoteObservation",
    "ReadOnlyBookEvidencePipeline",
    "ScriptedPerpsTransport",
    "measure_edge_decay",
    "parse_perps_market",
]
