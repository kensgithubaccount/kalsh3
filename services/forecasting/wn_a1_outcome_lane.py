"""WN-A1 outcome-lane DATA MODEL boundary -- no fetch, no fabricated settled result.

This module defines only the SHAPE a later, independently-reviewed settlement
reconciliation would populate. It does not fetch, parse, or claim any settled outcome.

No small, bounded, credential-free public-read path to Kalshi/The Weather Company's final
settlement value was identified in this milestone: ``weather.com/kalshi``'s "Official
Climate Reports" portal structure (see
``docs/reviews/WN_A1_CHICAGO_DAILY_HIGH_RESEARCH_ALERT.md``) was not established as a
bounded, machine-readable read path, and expanding scope to find or build one was
explicitly out of scope for WN-A1.

Actual outcome acquisition and reconciliation against a bound
``CurrentDailyHighContract`` is deferred to the bounded next milestone:

    **WN-A2: authoritative TWC/Kalshi settlement outcome reconciliation**

WN-A2 is not implemented here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from .wn_a1_domain import PRODUCTION_INFLUENCE, RESEARCH_ONLY

NEXT_MILESTONE = "WN-A2: authoritative TWC/Kalshi settlement outcome reconciliation"


@dataclass(frozen=True, slots=True)
class SettlementOutcome:
    """Shape only. WN-A1 never constructs a real instance of this outside of a test that
    is explicitly exercising the shape; no code path in this milestone produces one from
    live data."""

    market_ticker: str
    target_local_date: date
    settled_value_f: Decimal
    source_url: str
    acquired_at: datetime
    raw_sha256: str
    research_only: bool = RESEARCH_ONLY
    production_influence: Decimal = PRODUCTION_INFLUENCE
