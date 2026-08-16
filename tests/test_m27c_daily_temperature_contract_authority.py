from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from services.forecasting.daily_temperature import (
    AUTHORITY_IDENTITY,
    SETTLEMENT_LOCATIONS,
    DailyTemperatureReason,
    DailyTemperatureRoute,
    DailyTemperatureRouteState,
    route_daily_temperature,
)
from services.forecasting.domain import ForecastError
from services.market_universe.domain import Event, Market, stable_hash


def event(**changes: Any) -> Event:
    raw = {
        "event_ticker": "KXHIGHAUS-26AUG15",
        "series_ticker": "KXHIGHAUS",
        "title": "Highest temperature in Austin on Aug 15, 2026?",
        "settlement_sources": [
            {"name": "The Weather Company", "url": "https://weather.com/kalshi"}
        ],
    }
    raw.update(changes)
    return Event.parse(raw)


def daily_rule(
    measurement: str = "maximum",
    location: str = "Austin",
    identifier: str = "CLIAUS",
    date_text: str = "Aug 15, 2026",
    phrase: str = "between 100-101",
    unit: str = "fahrenheit",
) -> str:
    return (
        f"If the {measurement} temperature recorded at {location}({identifier}) for "
        f"{date_text}, is {phrase}° {unit} according to The Weather Company, then the "
        "market resolves to Yes."
    )


def market(
    *,
    measurement: str = "maximum",
    location: str = "Austin",
    identifier: str = "CLIAUS",
    strike_type: str = "between",
    floor: object = 100,
    cap: object = 101,
    date_text: str = "Aug 15, 2026",
    unit: str = "fahrenheit",
    rule: str | None = None,
) -> Market:
    phrase = {
        "between": f"between {floor}-{cap}",
        "greater": f"greater than {floor}",
        "less": f"less than {cap}",
    }.get(strike_type, f"equal to {floor}")
    rules = rule or daily_rule(measurement, location, identifier, date_text, phrase, unit)
    return Market.parse(
        {
            "ticker": "KXHIGHAUS-26AUG15-B100.5",
            "event_ticker": "KXHIGHAUS-26AUG15",
            "title": "Daily temperature",
            "market_type": "binary",
            "status": "active",
            "price_level_structure": "linear_cent",
            "rules_primary": rules,
            "rules_secondary": "The official value is reported by the Weather Company.",
            "strike_type": strike_type,
            "floor_strike": floor,
            "cap_strike": cap,
            "volume_fp": "0",
            "open_interest_fp": "0",
        }
    )


@pytest.mark.parametrize(
    ("strike_type", "floor", "cap", "comparator", "lower", "upper"),
    [
        ("between", 100, 101, "RANGE", Decimal("100"), Decimal("101")),
        ("greater", 105.5, None, "GT", Decimal("105.5"), None),
        ("less", None, "70", "LT", Decimal("70"), None),
    ],
)
def test_supported_strike_shapes(
    strike_type: str,
    floor: object,
    cap: object,
    comparator: str,
    lower: Decimal,
    upper: Decimal | None,
) -> None:
    route = route_daily_temperature(market(strike_type=strike_type, floor=floor, cap=cap), event())
    assert route.state is DailyTemperatureRouteState.SUPPORTED and route.reason is None
    assert route.contract is not None
    assert (route.contract.comparator, route.contract.lower, route.contract.upper) == (
        comparator,
        lower,
        upper,
    )
    assert route.production_influence == Decimal("0") and route.research_only


@pytest.mark.parametrize(
    ("measurement", "expected"), [("maximum", "DAILY_MAX"), ("minimum", "DAILY_MIN")]
)
def test_both_measurements(measurement: str, expected: str) -> None:
    result = route_daily_temperature(market(measurement=measurement), event())
    assert result.contract is not None and result.contract.measurement == expected


@pytest.mark.parametrize(
    ("identifier", "location", "timezone"),
    [
        ("CLIATL", "Atlanta", "America/New_York"),
        ("CLIAUS", "Austin", "America/Chicago"),
        ("CLIDEN", "Denver", "America/Denver"),
        ("CLILAX", "Los Angeles", "America/Los_Angeles"),
        ("CLIPHX", "Phoenix", "America/Phoenix"),
    ],
)
def test_reviewed_location_timezone_authority(
    identifier: str, location: str, timezone: str
) -> None:
    result = route_daily_temperature(market(identifier=identifier, location=location), event())
    assert result.contract is not None
    assert (result.contract.station_id, result.contract.location, result.contract.timezone) == (
        identifier,
        location,
        timezone,
    )


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"identifier": "CLIXYZ"}, DailyTemperatureReason.UNKNOWN_SETTLEMENT_LOCATION_ID),
        ({"location": "Dallas"}, DailyTemperatureReason.LOCATION_AUTHORITY_CONFLICT),
        ({"date_text": "someday"}, DailyTemperatureReason.DATE_UNPARSED),
        ({"identifier": "BROKEN"}, DailyTemperatureReason.RULE_SHAPE_UNSUPPORTED),
        ({"unit": "celsius"}, DailyTemperatureReason.UNIT_UNSUPPORTED),
    ],
)
def test_rule_authority_abstentions(
    changes: dict[str, object], reason: DailyTemperatureReason
) -> None:
    assert route_daily_temperature(market(**changes), event()).reason is reason


@pytest.mark.parametrize(
    "rule",
    [
        "Will Snowflake Inc. beat its maximum temperature forecast?",
        "Will it rain in Austin?",
        "Will a hurricane make landfall?",
        "Will climate policy pass?",
        "The temperature may exceed 90.",
    ],
)
def test_lookalikes_are_not_daily_temperature(rule: str) -> None:
    assert (
        route_daily_temperature(market(rule=rule), event()).reason
        is DailyTemperatureReason.NOT_DAILY_TEMPERATURE
    )


@pytest.mark.parametrize(
    "sources",
    [
        [],
        [{"name": "NOAA"}],
        [{"name": "The Weather Company"}, {"name": "NOAA"}],
        None,
    ],
)
def test_event_settlement_source_fails_closed(sources: object) -> None:
    result = route_daily_temperature(market(), event(settlement_sources=sources))
    assert result.reason is DailyTemperatureReason.SETTLEMENT_SOURCE_CONFLICT


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), "bad", "1e2"])
def test_malformed_strikes_abstain(value: object) -> None:
    result = route_daily_temperature(
        market(
            floor=value,
            rule=daily_rule(),
        ),
        event(),
    )
    assert result.reason is DailyTemperatureReason.STRIKE_MALFORMED


@pytest.mark.parametrize(
    ("strike_type", "floor", "cap"),
    [
        ("greater", 100, 101),
        ("less", 100, 101),
    ],
)
def test_one_sided_strikes_reject_opposite_bound(
    strike_type: str, floor: object, cap: object
) -> None:
    result = route_daily_temperature(market(strike_type=strike_type, floor=floor, cap=cap), event())
    assert result.state is DailyTemperatureRouteState.ABSTAIN
    assert result.reason is DailyTemperatureReason.RULE_METADATA_CONFLICT
    assert result.contract is None


@pytest.mark.parametrize(("floor", "cap"), [(100, 100), (101, 100)])
def test_between_strikes_require_strictly_increasing_bounds(floor: object, cap: object) -> None:
    result = route_daily_temperature(market(floor=floor, cap=cap), event())
    assert result.state is DailyTemperatureRouteState.ABSTAIN
    assert result.reason is DailyTemperatureReason.STRIKE_MALFORMED
    assert result.contract is None


def test_rule_metadata_conflicts_and_unsupported_strike() -> None:
    mismatched = market(
        floor=99,
        rule=daily_rule(),
    )
    assert (
        route_daily_temperature(mismatched, event()).reason
        is DailyTemperatureReason.RULE_METADATA_CONFLICT
    )
    unsupported = market(
        strike_type="equal",
        rule=daily_rule(),
    )
    assert (
        route_daily_temperature(unsupported, event()).reason
        is DailyTemperatureReason.STRIKE_UNSUPPORTED
    )


def test_identity_is_deterministic_and_materially_bound() -> None:
    first, second = (
        route_daily_temperature(market(), event()),
        route_daily_temperature(market(), event()),
    )
    changed = route_daily_temperature(
        market(
            rule=daily_rule(phrase="between 99-101"),
            floor=99,
        ),
        event(),
    )
    assert first.source_identity == second.source_identity
    assert changed.source_identity != first.source_identity
    assert first.policy_identity == AUTHORITY_IDENTITY
    assert stable_hash(("changed-version", tuple(SETTLEMENT_LOCATIONS))) != AUTHORITY_IDENTITY
    with pytest.raises(ForecastError):
        DailyTemperatureRoute(state=DailyTemperatureRouteState.SUPPORTED)  # type: ignore[call-arg]
    with pytest.raises(ForecastError):
        replace(first, production_influence=Decimal("1"))


def test_authority_is_exact_and_immutable() -> None:
    assert len(SETTLEMENT_LOCATIONS) == 20
    with pytest.raises(TypeError):
        SETTLEMENT_LOCATIONS["FORGED"] = SETTLEMENT_LOCATIONS["CLIAUS"]  # type: ignore[index]


def test_module_has_no_execution_network_credentials_or_trading_objects() -> None:
    import inspect

    import services.forecasting.daily_temperature as module

    source = inspect.getsource(module)
    forbidden = (
        "services.production_execution",
        "requests",
        "httpx",
        "credential",
        "TradeCandidate",
        "DecisionReceipt",
        "RiskIntent",
        "order(",
        "cancel(",
        "allocation",
        "sizing",
    )
    assert all(term not in source for term in forbidden)
