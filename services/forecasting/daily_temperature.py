"""M27C research-only authority for Kalshi daily-temperature contracts."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from types import MappingProxyType
from zoneinfo import ZoneInfo

from services.market_universe.domain import Event, Market, stable_hash

from .domain import ForecastError
from .weather import WeatherContract

POLICY_VERSION = "m27c-daily-temperature-contract-authority-v1"
SETTLEMENT_AUTHORITY = "Kalshi daily-temperature rules / The Weather Company"
SETTLEMENT_SOURCE = "The Weather Company"
ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class SettlementLocation:
    identifier: str
    location: str
    timezone: str


def _locations() -> Mapping[str, SettlementLocation]:
    rows = (
        ("CLIATL", "Atlanta", "America/New_York"),
        ("CLIAUS", "Austin", "America/Chicago"),
        ("CLIBOS", "Boston", "America/New_York"),
        ("CLIDCA", "Washington DC", "America/New_York"),
        ("CLIDEN", "Denver", "America/Denver"),
        ("CLIDFW", "Dallas", "America/Chicago"),
        ("CLIHOU", "Houston", "America/Chicago"),
        ("CLILAS", "Las Vegas", "America/Los_Angeles"),
        ("CLILAX", "Los Angeles", "America/Los_Angeles"),
        ("CLIMDW", "Chicago", "America/Chicago"),
        ("CLIMIA", "Miami", "America/New_York"),
        ("CLIMSP", "Minneapolis", "America/Chicago"),
        ("CLIMSY", "New Orleans", "America/Chicago"),
        ("CLINYC", "New York City", "America/New_York"),
        ("CLIOKC", "Oklahoma City", "America/Chicago"),
        ("CLIPHL", "Philadelphia", "America/New_York"),
        ("CLIPHX", "Phoenix", "America/Phoenix"),
        ("CLISAT", "San Antonio", "America/Chicago"),
        ("CLISEA", "Seattle", "America/Los_Angeles"),
        ("CLISFO", "San Francisco", "America/Los_Angeles"),
    )
    authority = {
        key: SettlementLocation(key, location, timezone) for key, location, timezone in rows
    }
    for value in authority.values():
        ZoneInfo(value.timezone)
    return MappingProxyType(authority)


SETTLEMENT_LOCATIONS = _locations()
AUTHORITY_IDENTITY = stable_hash(
    (
        POLICY_VERSION,
        tuple((x.identifier, x.location, x.timezone) for x in SETTLEMENT_LOCATIONS.values()),
    )
)


class DailyTemperatureRouteState(StrEnum):
    SUPPORTED = "SUPPORTED"
    ABSTAIN = "ABSTAIN"


class DailyTemperatureReason(StrEnum):
    NOT_DAILY_TEMPERATURE = "NOT_DAILY_TEMPERATURE"
    RULE_SHAPE_UNSUPPORTED = "RULE_SHAPE_UNSUPPORTED"
    UNKNOWN_SETTLEMENT_LOCATION_ID = "UNKNOWN_SETTLEMENT_LOCATION_ID"
    LOCATION_AUTHORITY_CONFLICT = "LOCATION_AUTHORITY_CONFLICT"
    DATE_UNPARSED = "DATE_UNPARSED"
    UNIT_UNSUPPORTED = "UNIT_UNSUPPORTED"
    SETTLEMENT_SOURCE_CONFLICT = "SETTLEMENT_SOURCE_CONFLICT"
    STRIKE_UNSUPPORTED = "STRIKE_UNSUPPORTED"
    STRIKE_MALFORMED = "STRIKE_MALFORMED"
    RULE_METADATA_CONFLICT = "RULE_METADATA_CONFLICT"


_RULE = re.compile(
    r"\AIf the (?P<measurement>maximum|minimum) temperature recorded at "
    r"(?P<location>[^()]+?)\s*\((?P<identifier>CLI[A-Z]+)\) for "
    r"(?P<date>[A-Z][a-z]{2} \d{1,2}, \d{4}), is "
    r"(?:(?:between (?P<between_low>[+-]?(?:\d+(?:\.\d+)?|\.\d+))-"
    r"(?P<between_high>[+-]?(?:\d+(?:\.\d+)?|\.\d+)))|"
    r"(?:greater than (?P<greater>[+-]?(?:\d+(?:\.\d+)?|\.\d+)))|"
    r"(?:less than (?P<less>[+-]?(?:\d+(?:\.\d+)?|\.\d+))))"
    r"° fahrenheit according to The Weather Company, then the market resolves to Yes\.\Z"
)
_CANDIDATE = re.compile(r"\b(?:maximum|minimum) temperature recorded at\b", re.IGNORECASE)
_TOKEN = re.compile(r"\((CLI[^)]*)\)")
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)\Z")
_ROUTE_CAPABILITY = object()


@dataclass(frozen=True, slots=True, init=False)
class DailyTemperatureRoute:
    state: DailyTemperatureRouteState
    reason: DailyTemperatureReason | None
    market_ticker: str
    event_ticker: str
    series_ticker: str
    contract: WeatherContract | None
    source_identity: str
    policy_identity: str
    research_only: bool
    production_influence: Decimal

    def __init__(self, *, _capability: object | None = None, **values: object) -> None:
        if _capability is not _ROUTE_CAPABILITY:
            raise ForecastError("daily-temperature route authority is not caller-constructible")
        for name, value in values.items():
            object.__setattr__(self, name, value)


def route_daily_temperature(market: Market, event: Event) -> DailyTemperatureRoute:
    """Evaluate exactly one canonical Market and return support or an explicit abstention."""
    source_identity = stable_hash(
        {
            "market_ticker": market.ticker,
            "event_ticker": market.event_ticker,
            "series_ticker": event.series_ticker,
            "rules_primary": market.raw.get("rules_primary"),
            "rules_secondary": market.raw.get("rules_secondary"),
            "strike_type": market.raw.get("strike_type"),
            "floor_strike": market.raw.get("floor_strike"),
            "cap_strike": market.raw.get("cap_strike"),
            "event_settlement_sources": event.raw.get("settlement_sources"),
        }
    )
    base = dict(
        market_ticker=market.ticker,
        event_ticker=event.ticker,
        series_ticker=event.series_ticker,
        source_identity=source_identity,
        policy_identity=AUTHORITY_IDENTITY,
        research_only=True,
        production_influence=ZERO,
    )
    if market.event_ticker != event.ticker:
        return _abstain(base, DailyTemperatureReason.RULE_METADATA_CONFLICT)
    rule = market.raw.get("rules_primary")
    if not isinstance(rule, str) or not _CANDIDATE.search(rule):
        return _abstain(base, DailyTemperatureReason.NOT_DAILY_TEMPERATURE)
    match = _RULE.fullmatch(rule)
    if match is None:
        lower_rule = rule.lower()
        if "celsius" in lower_rule or ("fahrenheit" not in lower_rule and "°" in rule):
            reason = DailyTemperatureReason.UNIT_UNSUPPORTED
        elif re.search(r"\b[A-Z][a-z]{2} \d{1,2}, \d{4}\b", rule) is None:
            reason = DailyTemperatureReason.DATE_UNPARSED
        elif (token := _TOKEN.search(rule)) and token.group(1) not in SETTLEMENT_LOCATIONS:
            reason = DailyTemperatureReason.UNKNOWN_SETTLEMENT_LOCATION_ID
        else:
            reason = DailyTemperatureReason.RULE_SHAPE_UNSUPPORTED
        return _abstain(base, reason)
    identifier = match.group("identifier")
    reviewed = SETTLEMENT_LOCATIONS.get(identifier)
    if reviewed is None:
        return _abstain(base, DailyTemperatureReason.UNKNOWN_SETTLEMENT_LOCATION_ID)
    if match.group("location").strip() != reviewed.location:
        return _abstain(base, DailyTemperatureReason.LOCATION_AUTHORITY_CONFLICT)
    if not _event_supports_source(event):
        return _abstain(base, DailyTemperatureReason.SETTLEMENT_SOURCE_CONFLICT)
    try:
        local_date = datetime.strptime(match.group("date"), "%b %d, %Y").date()
    except ValueError:
        return _abstain(base, DailyTemperatureReason.DATE_UNPARSED)
    strike_type = market.raw.get("strike_type")
    expected = (
        "between" if match.group("between_low") else "greater" if match.group("greater") else "less"
    )
    if strike_type not in {"between", "greater", "less"}:
        return _abstain(base, DailyTemperatureReason.STRIKE_UNSUPPORTED)
    if strike_type != expected:
        return _abstain(base, DailyTemperatureReason.RULE_METADATA_CONFLICT)
    if expected == "greater" and market.raw.get("cap_strike") is not None:
        return _abstain(base, DailyTemperatureReason.RULE_METADATA_CONFLICT)
    if expected == "less" and market.raw.get("floor_strike") is not None:
        return _abstain(base, DailyTemperatureReason.RULE_METADATA_CONFLICT)
    try:
        if expected == "between":
            lower = _strike(market.raw.get("floor_strike"), "floor_strike")
            upper = _strike(market.raw.get("cap_strike"), "cap_strike")
            if lower >= upper:
                raise ForecastError("range bounds must be strictly increasing")
            rule_lower, rule_upper, comparator = (
                Decimal(match.group("between_low")),
                Decimal(match.group("between_high")),
                "RANGE",
            )
        elif expected == "greater":
            lower, upper = _strike(market.raw.get("floor_strike"), "floor_strike"), None
            rule_lower, rule_upper, comparator = Decimal(match.group("greater")), None, "GT"
        else:
            lower, upper = _strike(market.raw.get("cap_strike"), "cap_strike"), None
            rule_lower, rule_upper, comparator = Decimal(match.group("less")), None, "LT"
    except (ForecastError, InvalidOperation):
        return _abstain(base, DailyTemperatureReason.STRIKE_MALFORMED)
    if lower != rule_lower or upper != rule_upper:
        return _abstain(base, DailyTemperatureReason.RULE_METADATA_CONFLICT)
    contract = WeatherContract(
        station_id=identifier,
        location=reviewed.location,
        measurement="DAILY_MAX" if match.group("measurement") == "maximum" else "DAILY_MIN",
        local_date=local_date,
        timezone=reviewed.timezone,
        lower=lower,
        upper=upper,
        comparator=comparator,
        unit="degF",
        rounding=None,
        settlement_authority=SETTLEMENT_AUTHORITY,
        settlement_source=SETTLEMENT_SOURCE,
        revision_policy="Authoritative Kalshi contract rules; no revision policy inferred",
    )
    contract.validate()
    return DailyTemperatureRoute(
        _capability=_ROUTE_CAPABILITY,
        state=DailyTemperatureRouteState.SUPPORTED,
        reason=None,
        contract=contract,
        **base,
    )


def _event_supports_source(event: Event) -> bool:
    sources = event.raw.get("settlement_sources")
    return (
        isinstance(sources, list)
        and bool(sources)
        and all(
            isinstance(source, dict) and source.get("name") == SETTLEMENT_SOURCE
            for source in sources
        )
    )


def _strike(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ForecastError(f"invalid {field}")
    if isinstance(value, float) and not math.isfinite(value):
        raise ForecastError(f"invalid {field}")
    if isinstance(value, str) and _NUMBER.fullmatch(value) is None:
        raise ForecastError(f"invalid {field}")
    try:
        result = Decimal(str(value)) if isinstance(value, float) else Decimal(value)
    except InvalidOperation as exc:
        raise ForecastError(f"invalid {field}") from exc
    if not result.is_finite():
        raise ForecastError(f"invalid {field}")
    return result


def _abstain(base: dict[str, object], reason: DailyTemperatureReason) -> DailyTemperatureRoute:
    return DailyTemperatureRoute(
        _capability=_ROUTE_CAPABILITY,
        state=DailyTemperatureRouteState.ABSTAIN,
        reason=reason,
        contract=None,
        **base,
    )
