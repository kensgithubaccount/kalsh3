"""WN-A1 current-live Chicago daily-high contract authority (bound to the real live source).

This module is deliberately INDEPENDENT of ``services.forecasting.daily_temperature``
(frozen M27C). M27C's authority is preserved unchanged and remains scoped to its own
historical/TWC semantics; nothing here imports it, extends it, or claims its semantics are
current. This module exists solely to bind CURRENT, LIVE Kalshi Chicago daily-high
(``KXHIGHCHI``) contract semantics for the WN-A1 research alert lane, from live evidence
only.

Ground truth, established 2026-09-14 by fetching the live public Kalshi API directly
(``GET https://external-api.kalshi.com/trade-api/v2/markets?series_ticker=KXHIGHCHI`` and
the corresponding ``/events/...`` and ``/series/...`` endpoints) -- NOT from a help-center
article: every currently active ``KXHIGHCHI-*`` market's ``rules_primary`` text, and both
its event- and series-level ``settlement_sources`` fields, name "The Weather Company" as
the settlement source. The same is true for every other checked daily-high city
(``KXHIGHNY``, ``KXHIGHMIA``, ``KXHIGHDEN``, ``KXHIGHAUS``, ``KXHIGHLAX``).

Kalshi's own help-center article (``https://help.kalshi.com/en/articles/13823837-weather-
markets``, dated 2026-07-22) claims daily markets settle from the "final NWS Daily Climate
Report" with a DST-sensitive local-standard-time window. That claim directly CONTRADICTS
the live contract evidence above. Per explicit product direction, live
market/event/series evidence is controlling authority; the help-center claim is retained
here only as documented, non-authoritative context (see
``docs/reviews/WN_A1_CHICAGO_DAILY_HIGH_RESEARCH_ALERT.md``) and is never substituted for
what the live contract itself states. If a future live contract's settlement source
changes, this module fails closed (ABSTAIN) rather than silently continuing to assume TWC.

Day-window semantics: this module does NOT assume the NWS local-standard-time convention
described in the (contradicted) help article. Instead it treats each selected market's own
``early_close_condition`` field -- itself live, first-party contract evidence, e.g. "The
Last Trading Time will be 11:59 PM local time on September 15, 2026 regardless of..." -- as
the only accepted positive evidence for a civil local-calendar-day measurement window
(local midnight to local midnight). If that exact phrase, naming the contract's own target
date, is not present, window derivation explicitly ABSTAINS (``WindowStatus.NOT_
ESTABLISHED``) rather than inventing or assuming any window.

Reused, clearly-labeled research-only physical fact: the CLI identifier ``CLIMDW`` and the
NWS/GHCN station identity for Chicago Midway (``KMDW`` / ``USW00014819``) are pure
geography, independent of which entity currently happens to be Kalshi's settlement source.
This module redeclares that identity locally (does not import the frozen M27C physical-
source authority module) so that a future change to M27C's settlement-authority chain can
never silently alter WN-A1's semantics, and vice versa.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from zoneinfo import ZoneInfo

from services.market_universe.domain import Event, Market, Series, stable_hash

from .wn_a1_domain import PRODUCTION_INFLUENCE, RESEARCH_ONLY, WnA1Error

POLICY_VERSION = "wn-a1-current-live-daily-high-twc-authority-v1"
SERIES_TICKER = "KXHIGHCHI"
LOCATION = "Chicago"
STATION_ID = "CLIMDW"
NWS_STATION_ID = "KMDW"
GHCND_STATION_ID = "USW00014819"
TIMEZONE = "America/Chicago"
SETTLEMENT_SOURCE = "The Weather Company"
SETTLEMENT_SOURCE_URL = "https://weather.com/kalshi"
MEASUREMENT = "DAILY_MAX"

# Contradicted, non-authoritative context only -- see module docstring. Never consulted by
# route_current_daily_high(); retained purely so the discrepancy is machine-visible.
HELP_CENTER_CLAIM_URL = "https://help.kalshi.com/en/articles/13823837-weather-markets"
HELP_CENTER_CLAIM_TEXT = (
    "These markets settle the next morning based on the high temperature recorded in the "
    "final NWS Daily Climate Report."
)
HELP_CENTER_CLAIM_STATUS = "CONTRADICTED_BY_LIVE_CONTRACT_EVIDENCE"

_RULE = re.compile(
    r"\AIf the maximum temperature recorded at Chicago \(CLIMDW\) for "
    r"(?P<date>[A-Z][a-z]{2} \d{1,2}, \d{4}), is "
    r"(?:(?:between (?P<between_low>\d+(?:\.\d+)?)-(?P<between_high>\d+(?:\.\d+)?))|"
    r"(?:greater than (?P<greater>\d+(?:\.\d+)?))|"
    r"(?:less than (?P<less>\d+(?:\.\d+)?)))"
    r"\N{DEGREE SIGN} fahrenheit according to The Weather Company, then the market resolves "
    r"to Yes\.\Z"
)
_MINIMUM_RULE_MARKER = re.compile(r"\bminimum temperature recorded\b", re.IGNORECASE)
_EARLY_CLOSE = re.compile(
    r"Last Trading Time will be 11:59\s?PM local time on "
    r"(?P<month>[A-Z][a-z]+) (?P<day>\d{1,2}), (?P<year>\d{4})\b"
)
_NUMBER = re.compile(r"\d+(?:\.\d+)?\Z")
_EVENT_TICKER_DATE = re.compile(r"\AKXHIGHCHI-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<day>\d{2})\Z")


class CurrentDailyHighRouteState(StrEnum):
    SUPPORTED = "SUPPORTED"
    ABSTAIN = "ABSTAIN"


class CurrentDailyHighReason(StrEnum):
    WRONG_SERIES = "WRONG_SERIES"
    EVENT_MARKET_MISMATCH = "EVENT_MARKET_MISMATCH"
    MEASUREMENT_OUT_OF_SCOPE = "MEASUREMENT_OUT_OF_SCOPE"
    WRONG_STATION_OR_CITY = "WRONG_STATION_OR_CITY"
    RULE_SHAPE_UNSUPPORTED = "RULE_SHAPE_UNSUPPORTED"
    DATE_UNPARSED = "DATE_UNPARSED"
    STRIKE_UNSUPPORTED = "STRIKE_UNSUPPORTED"
    STRIKE_MALFORMED = "STRIKE_MALFORMED"
    RULE_METADATA_CONFLICT = "RULE_METADATA_CONFLICT"
    EVENT_TICKER_DATE_UNPARSED = "EVENT_TICKER_DATE_UNPARSED"
    EVENT_TICKER_DATE_MISMATCH = "EVENT_TICKER_DATE_MISMATCH"
    SETTLEMENT_SOURCE_NOT_TWC = "SETTLEMENT_SOURCE_NOT_TWC"


class WindowStatus(StrEnum):
    ESTABLISHED_CIVIL_LOCAL_DAY = "ESTABLISHED_CIVIL_LOCAL_DAY"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"


@dataclass(frozen=True, slots=True)
class CurrentDailyHighContract:
    market_ticker: str
    event_ticker: str
    series_ticker: str
    station_id: str
    location: str
    measurement: str
    local_date: date
    timezone: str
    lower: Decimal
    upper: Decimal | None
    comparator: str  # "RANGE" | "GT" | "LT"
    unit: str
    settlement_source: str
    settlement_source_url: str | None
    window_status: WindowStatus
    window_start_local: datetime | None
    window_end_local: datetime | None
    window_evidence_text: str | None


_ROUTE_CAPABILITY = object()


@dataclass(frozen=True, slots=True, init=False)
class CurrentDailyHighRoute:
    state: CurrentDailyHighRouteState
    reason: CurrentDailyHighReason | None
    market_ticker: str
    event_ticker: str
    series_ticker: str
    contract: CurrentDailyHighContract | None
    source_identity: str
    policy_identity: str
    research_only: bool
    production_influence: Decimal

    def __init__(self, *, _capability: object | None = None, **values: object) -> None:
        if _capability is not _ROUTE_CAPABILITY:
            raise WnA1Error("current daily-high route authority is not caller-constructible")
        for name, value in values.items():
            object.__setattr__(self, name, value)


POLICY_IDENTITY = stable_hash(
    (POLICY_VERSION, SERIES_TICKER, LOCATION, STATION_ID, TIMEZONE, SETTLEMENT_SOURCE)
)


def route_current_daily_high(market: Market, event: Event, series: Series) -> CurrentDailyHighRoute:
    """Evaluate exactly one canonical Market/Event/Series triple; SUPPORTED or ABSTAIN.

    Chicago DAILY_MAX only. Requires the live evidence itself -- not any external claim --
    to state The Weather Company as settlement source at both event and series level, and
    requires the exact reviewed ``rules_primary`` shape observed live on 2026-09-14.
    """
    source_identity = stable_hash(
        {
            "market_ticker": market.ticker,
            "event_ticker": market.event_ticker,
            "series_ticker": event.series_ticker,
            "rules_primary": market.raw.get("rules_primary"),
            "rules_secondary": market.raw.get("rules_secondary"),
            "early_close_condition": market.raw.get("early_close_condition"),
            "strike_type": market.raw.get("strike_type"),
            "floor_strike": market.raw.get("floor_strike"),
            "cap_strike": market.raw.get("cap_strike"),
            "event_settlement_sources": event.raw.get("settlement_sources"),
            "series_settlement_sources": series.raw.get("settlement_sources"),
        }
    )
    base: dict[str, object] = dict(
        market_ticker=market.ticker,
        event_ticker=event.ticker,
        series_ticker=event.series_ticker,
        source_identity=source_identity,
        policy_identity=POLICY_IDENTITY,
        research_only=RESEARCH_ONLY,
        production_influence=PRODUCTION_INFLUENCE,
    )
    if event.series_ticker != SERIES_TICKER or series.ticker != SERIES_TICKER:
        return _abstain(base, CurrentDailyHighReason.WRONG_SERIES)
    if market.event_ticker != event.ticker:
        return _abstain(base, CurrentDailyHighReason.EVENT_MARKET_MISMATCH)
    rule = market.raw.get("rules_primary")
    if not isinstance(rule, str) or not rule:
        return _abstain(base, CurrentDailyHighReason.RULE_SHAPE_UNSUPPORTED)
    if _MINIMUM_RULE_MARKER.search(rule):
        return _abstain(base, CurrentDailyHighReason.MEASUREMENT_OUT_OF_SCOPE)
    if "Chicago" not in rule or "CLIMDW" not in rule:
        return _abstain(base, CurrentDailyHighReason.WRONG_STATION_OR_CITY)
    match = _RULE.fullmatch(rule)
    if match is None:
        return _abstain(base, CurrentDailyHighReason.RULE_SHAPE_UNSUPPORTED)
    if not _settlement_source_confirmed(event, series):
        return _abstain(base, CurrentDailyHighReason.SETTLEMENT_SOURCE_NOT_TWC)
    try:
        local_date = datetime.strptime(match.group("date"), "%b %d, %Y").date()
    except ValueError:
        return _abstain(base, CurrentDailyHighReason.DATE_UNPARSED)
    ticker_match = _EVENT_TICKER_DATE.fullmatch(event.ticker)
    if ticker_match is None:
        return _abstain(base, CurrentDailyHighReason.EVENT_TICKER_DATE_UNPARSED)
    try:
        ticker_date = datetime.strptime(
            f"{ticker_match.group('yy')}{ticker_match.group('mon')}{ticker_match.group('day')}",
            "%y%b%d",
        ).date()
    except ValueError:
        return _abstain(base, CurrentDailyHighReason.EVENT_TICKER_DATE_UNPARSED)
    if ticker_date != local_date:
        return _abstain(base, CurrentDailyHighReason.EVENT_TICKER_DATE_MISMATCH)
    strike_type = market.raw.get("strike_type")
    expected = (
        "between" if match.group("between_low") else "greater" if match.group("greater") else "less"
    )
    if strike_type not in {"between", "greater", "less"}:
        return _abstain(base, CurrentDailyHighReason.STRIKE_UNSUPPORTED)
    if strike_type != expected:
        return _abstain(base, CurrentDailyHighReason.RULE_METADATA_CONFLICT)
    try:
        if expected == "between":
            lower = _strike(market.raw.get("floor_strike"), "floor_strike")
            upper = _strike(market.raw.get("cap_strike"), "cap_strike")
            if lower >= upper:
                raise WnA1Error("range bounds must be strictly increasing")
            rule_lower = Decimal(match.group("between_low"))
            rule_upper: Decimal | None = Decimal(match.group("between_high"))
            comparator = "RANGE"
        elif expected == "greater":
            lower, upper = _strike(market.raw.get("floor_strike"), "floor_strike"), None
            rule_lower, rule_upper, comparator = Decimal(match.group("greater")), None, "GT"
        else:
            lower, upper = _strike(market.raw.get("cap_strike"), "cap_strike"), None
            rule_lower, rule_upper, comparator = Decimal(match.group("less")), None, "LT"
    except (WnA1Error, InvalidOperation):
        return _abstain(base, CurrentDailyHighReason.STRIKE_MALFORMED)
    if lower != rule_lower or upper != rule_upper:
        return _abstain(base, CurrentDailyHighReason.RULE_METADATA_CONFLICT)
    window_status, window_start, window_end, window_text = _derive_window(
        market.raw.get("early_close_condition"), local_date
    )
    contract = CurrentDailyHighContract(
        market_ticker=market.ticker,
        event_ticker=event.ticker,
        series_ticker=event.series_ticker,
        station_id=STATION_ID,
        location=LOCATION,
        measurement=MEASUREMENT,
        local_date=local_date,
        timezone=TIMEZONE,
        lower=lower,
        upper=upper,
        comparator=comparator,
        unit="degF",
        settlement_source=SETTLEMENT_SOURCE,
        settlement_source_url=SETTLEMENT_SOURCE_URL,
        window_status=window_status,
        window_start_local=window_start,
        window_end_local=window_end,
        window_evidence_text=window_text,
    )
    return CurrentDailyHighRoute(
        _capability=_ROUTE_CAPABILITY,
        state=CurrentDailyHighRouteState.SUPPORTED,
        reason=None,
        contract=contract,
        **base,
    )


def _settlement_source_confirmed(event: Event, series: Series) -> bool:
    def _only_twc(sources: object) -> bool:
        return (
            isinstance(sources, list)
            and bool(sources)
            and all(isinstance(s, dict) and s.get("name") == SETTLEMENT_SOURCE for s in sources)
        )

    return _only_twc(event.raw.get("settlement_sources")) and _only_twc(
        series.raw.get("settlement_sources")
    )


def _derive_window(
    early_close_condition: object, local_date: date
) -> tuple[WindowStatus, datetime | None, datetime | None, str | None]:
    if not isinstance(early_close_condition, str) or not early_close_condition:
        return WindowStatus.NOT_ESTABLISHED, None, None, None
    match = _EARLY_CLOSE.search(early_close_condition)
    if match is None:
        return WindowStatus.NOT_ESTABLISHED, None, None, None
    try:
        stated_date = datetime.strptime(
            f"{match.group('month')} {match.group('day')}, {match.group('year')}", "%B %d, %Y"
        ).date()
    except ValueError:
        return WindowStatus.NOT_ESTABLISHED, None, None, None
    if stated_date != local_date:
        return WindowStatus.NOT_ESTABLISHED, None, None, None
    tz = ZoneInfo(TIMEZONE)
    start = datetime(local_date.year, local_date.month, local_date.day, 0, 0, 0, tzinfo=tz)
    end = start + timedelta(days=1)
    return WindowStatus.ESTABLISHED_CIVIL_LOCAL_DAY, start, end, early_close_condition


def _strike(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise WnA1Error(f"invalid {field}")
    if isinstance(value, float) and not math.isfinite(value):
        raise WnA1Error(f"invalid {field}")
    if isinstance(value, str) and _NUMBER.fullmatch(value) is None:
        raise WnA1Error(f"invalid {field}")
    try:
        result = Decimal(str(value)) if isinstance(value, float) else Decimal(value)
    except InvalidOperation as exc:
        raise WnA1Error(f"invalid {field}") from exc
    if not result.is_finite():
        raise WnA1Error(f"invalid {field}")
    return result


def _abstain(base: dict[str, object], reason: CurrentDailyHighReason) -> CurrentDailyHighRoute:
    return CurrentDailyHighRoute(
        _capability=_ROUTE_CAPABILITY,
        state=CurrentDailyHighRouteState.ABSTAIN,
        reason=reason,
        contract=None,
        **base,
    )
