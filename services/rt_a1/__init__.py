"""RT-A1 prospective Rotten Tomatoes latency evidence collection.

This package is deliberately research-only.  It records public observations and
never emits a trading decision or calls an authenticated exchange surface.
"""

from .collector import (
    Classification,
    ExternalReviewEvidence,
    RtEligibility,
    RtInclusion,
    RtSnapshot,
    Transition,
    parse_external_review_html,
    parse_rt_html,
    reconstruct_transition,
)
from .domain import ActiveKxrtMarket, DiscoveryError, discover_active_kxrt
from .store import RtA1Store

__all__ = [
    "ActiveKxrtMarket",
    "Classification",
    "DiscoveryError",
    "ExternalReviewEvidence",
    "RtA1Store",
    "RtEligibility",
    "RtInclusion",
    "RtSnapshot",
    "Transition",
    "discover_active_kxrt",
    "parse_external_review_html",
    "parse_rt_html",
    "reconstruct_transition",
]
