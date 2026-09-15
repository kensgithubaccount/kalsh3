"""WN-A1 shared research-only domain: alert states and safety constants.

WN-A1 is a research-only, human-alert-only lane built on WeatherNext 3 forecasts and live
Kalshi Chicago daily-high evidence. It never places, previews, or authorizes an order and
has zero production influence anywhere in this package. No module under this ``wn_a1_``
prefix imports or reinterprets ``services.forecasting.daily_temperature`` (frozen M27C);
see ``docs/reviews/WN_A1_CHICAGO_DAILY_HIGH_RESEARCH_ALERT.md`` for the full boundary.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum


class WnA1Error(ValueError):
    """Raised for any WN-A1 fail-closed validation failure."""


RESEARCH_ONLY = True
PRODUCTION_INFLUENCE = Decimal(0)


class AlertState(StrEnum):
    """The only four headline states the primary WN-A1 alert may show a human."""

    TAKE_A_LOOK = "TAKE A LOOK"
    SKIP = "SKIP"
    TOO_UNCERTAIN = "TOO UNCERTAIN"
    DATA_NOT_READY = "DATA NOT READY"


class AlertSide(StrEnum):
    """Which executable side (if any) the primary alert names plainly to a human."""

    YES = "YES"
    NO = "NO"


BANNED_ALERT_WORDS = (
    "guaranteed",
    "profitable",
    "profit",
    "alpha",
    "arbitrage",
    "free money",
    "expected profit",
    "ev+",
)
