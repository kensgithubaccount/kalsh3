from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from services.forecasting import daily_temperature as m27c
from services.forecasting.wn_a1_current_daily_high_authority import (
    POLICY_IDENTITY,
    POLICY_VERSION,
    SERIES_TICKER,
    CurrentDailyHighReason,
    CurrentDailyHighRoute,
    CurrentDailyHighRouteState,
    WindowStatus,
    route_current_daily_high,
)
from services.forecasting.wn_a1_domain import WnA1Error
from services.market_universe.domain import Event, Market, Series

# Real live payload fields captured 2026-09-14 via
# GET https://external-api.kalshi.com/trade-api/v2/markets?series_ticker=KXHIGHCHI --
# reproduced here as static fixtures (no network access in tests).
EARLY_CLOSE = (
    "The Last Trading Time will be 11:59 PM local time on September 15, 2026 regardless "
    "of any data releases or events occurring. Expiration will occur on the sooner of the "
    "first 7:00 or 8:00 AM ET following the release of the data for September 15, 2026, or "
    "one week after September 15, 2026."
)
RULES_SECONDARY = (
    "Not all weather data is the same. While checking a source like AccuWeather or Google "
    "Weather may help guide your decision, the official and final value used to determine "
    "this market is the maximum/minimum temperature as reported by the Weather Company."
)


def market_raw(**changes: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "ticker": "KXHIGHCHI-26SEP15-T87",
        "event_ticker": "KXHIGHCHI-26SEP15",
        "market_type": "binary",
        "status": "active",
        "rules_primary": (
            "If the maximum temperature recorded at Chicago (CLIMDW) for Sep 15, 2026, is "
            "greater than 87\N{DEGREE SIGN} fahrenheit according to The Weather Company, "
            "then the market resolves to Yes."
        ),
        "rules_secondary": RULES_SECONDARY,
        "price_level_structure": "linear_cent",
        "price_ranges": [{"start": "0.0000", "end": "1.0000", "step": "0.0100"}],
        "strike_type": "greater",
        "floor_strike": 87,
        "cap_strike": None,
        "early_close_condition": EARLY_CLOSE,
    }
    row.update(changes)
    return row


def event_raw(**changes: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "event_ticker": "KXHIGHCHI-26SEP15",
        "series_ticker": "KXHIGHCHI",
        "title": "Highest temperature in Chicago on Sep 15, 2026?",
        "settlement_sources": [
            {"name": "The Weather Company", "url": "https://weather.com/kalshi"}
        ],
    }
    row.update(changes)
    return row


def series_raw(**changes: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "ticker": "KXHIGHCHI",
        "title": "Highest temperature in Chicago",
        "category": "Climate and Weather",
        "frequency": "daily",
        "settlement_sources": [
            {"name": "The Weather Company", "url": "https://weather.com/kalshi"}
        ],
    }
    row.update(changes)
    return row


def triple(
    m: dict[str, Any] | None = None,
    e: dict[str, Any] | None = None,
    s: dict[str, Any] | None = None,
):
    return (
        Market.parse(m or market_raw()),
        Event.parse(e or event_raw()),
        Series.parse(s or series_raw()),
    )


def test_supported_from_real_live_rule_shape() -> None:
    market, event, series = triple()
    route = route_current_daily_high(market, event, series)
    assert route.state is CurrentDailyHighRouteState.SUPPORTED
    assert route.reason is None
    contract = route.contract
    assert contract is not None
    assert contract.comparator == "GT"
    assert contract.lower == Decimal("87")
    assert contract.upper is None
    assert contract.settlement_source == "The Weather Company"
    assert contract.local_date == date(2026, 9, 15)
    assert contract.window_status is WindowStatus.ESTABLISHED_CIVIL_LOCAL_DAY
    assert route.research_only is True
    assert route.production_influence == Decimal(0)


def test_between_range_supported() -> None:
    m = market_raw(
        ticker="KXHIGHCHI-26SEP15-B86.5",
        rules_primary=(
            "If the maximum temperature recorded at Chicago (CLIMDW) for Sep 15, 2026, is "
            "between 86-87\N{DEGREE SIGN} fahrenheit according to The Weather Company, then "
            "the market resolves to Yes."
        ),
        strike_type="between",
        floor_strike=86,
        cap_strike=87,
    )
    market, event, series = triple(m)
    route = route_current_daily_high(market, event, series)
    assert route.state is CurrentDailyHighRouteState.SUPPORTED
    assert route.contract is not None
    assert route.contract.comparator == "RANGE"
    assert (route.contract.lower, route.contract.upper) == (Decimal("86"), Decimal("87"))


def test_less_than_supported() -> None:
    m = market_raw(
        ticker="KXHIGHCHI-26SEP15-T80",
        rules_primary=(
            "If the maximum temperature recorded at Chicago (CLIMDW) for Sep 15, 2026, is "
            "less than 80\N{DEGREE SIGN} fahrenheit according to The Weather Company, then "
            "the market resolves to Yes."
        ),
        strike_type="less",
        floor_strike=None,
        cap_strike=80,
    )
    market, event, series = triple(m)
    route = route_current_daily_high(market, event, series)
    assert route.state is CurrentDailyHighRouteState.SUPPORTED
    assert route.contract is not None
    assert route.contract.comparator == "LT"


def test_minimum_measurement_out_of_scope() -> None:
    m = market_raw(
        rules_primary=(
            "If the minimum temperature recorded at Chicago (CLIMDW) for Sep 15, 2026, is "
            "greater than 60\N{DEGREE SIGN} fahrenheit according to The Weather Company, "
            "then the market resolves to Yes."
        ),
        strike_type="greater",
        floor_strike=60,
    )
    market, event, series = triple(m)
    route = route_current_daily_high(market, event, series)
    assert route.state is CurrentDailyHighRouteState.ABSTAIN
    assert route.reason is CurrentDailyHighReason.MEASUREMENT_OUT_OF_SCOPE


def test_wrong_city_abstain() -> None:
    """A different city's contract must never be silently accepted as Chicago."""
    m = market_raw(
        ticker="KXHIGHNY-26SEP15-T77",
        rules_primary=(
            "If the maximum temperature recorded at New York City (CLINYC) for Sep 15, "
            "2026, is greater than 77\N{DEGREE SIGN} fahrenheit according to The Weather "
            "Company, then the market resolves to Yes."
        ),
    )
    market, event, series = triple(m)
    route = route_current_daily_high(market, event, series)
    assert route.state is CurrentDailyHighRouteState.ABSTAIN
    assert route.reason is CurrentDailyHighReason.WRONG_STATION_OR_CITY


def test_twc_hourly_contract_abstain() -> None:
    """An hourly TWC-sourced contract has a different rule shape and must abstain."""
    m = market_raw(
        ticker="KXTEMPCHI-26SEP15-1500ET-T77",
        rules_primary=(
            "If the temperature at Chicago (CLIMDW) at 3:00 PM ET on Sep 15, 2026, is "
            "greater than 77\N{DEGREE SIGN} fahrenheit according to The Weather Company, "
            "then the market resolves to Yes."
        ),
    )
    market, event, series = triple(m)
    route = route_current_daily_high(market, event, series)
    assert route.state is CurrentDailyHighRouteState.ABSTAIN
    assert route.reason is CurrentDailyHighReason.RULE_SHAPE_UNSUPPORTED


def test_settlement_source_never_assumed_even_when_claimed_nws() -> None:
    """No default source is ever assumed -- including NWS -- without positive live evidence.

    This repurposes the milestone's original "NWS daily contract accepted only from
    current evidence" requirement: WN-A1 was redirected mid-build (live evidence showed
    every current daily-high contract, Chicago included, actually names The Weather
    Company, not NWS -- see the review doc) to bind to the real live source instead of
    assuming NWS. The invariant this test protects is unchanged either way: a settlement
    source is never accepted unless the live event AND series evidence positively state
    it, and any other/missing claim must abstain rather than defaulting to TWC.
    """
    e = event_raw(
        settlement_sources=[{"name": "National Weather Service", "url": "https://weather.gov"}]
    )
    market, event, series = triple(e=e)
    route = route_current_daily_high(market, event, series)
    assert route.state is CurrentDailyHighRouteState.ABSTAIN
    assert route.reason is CurrentDailyHighReason.SETTLEMENT_SOURCE_NOT_TWC


def test_event_series_source_contradiction_abstains() -> None:
    e = event_raw(
        settlement_sources=[{"name": "The Weather Company", "url": "https://weather.com/kalshi"}]
    )
    s = series_raw(
        settlement_sources=[{"name": "National Weather Service", "url": "https://weather.gov"}]
    )
    market, event, series = triple(e=e, s=s)
    route = route_current_daily_high(market, event, series)
    assert route.state is CurrentDailyHighRouteState.ABSTAIN
    assert route.reason is CurrentDailyHighReason.SETTLEMENT_SOURCE_NOT_TWC


def test_wrong_series_abstain() -> None:
    market, event, series = triple(
        s=series_raw(ticker="KXHIGHNY", title="x"), e=event_raw(series_ticker="KXHIGHNY")
    )
    route = route_current_daily_high(market, event, series)
    assert route.state is CurrentDailyHighRouteState.ABSTAIN
    assert route.reason is CurrentDailyHighReason.WRONG_SERIES


def test_event_market_mismatch_abstain() -> None:
    market, event, series = triple(m=market_raw(event_ticker="KXHIGHCHI-26SEP16"))
    route = route_current_daily_high(market, event, series)
    assert route.state is CurrentDailyHighRouteState.ABSTAIN
    assert route.reason is CurrentDailyHighReason.EVENT_MARKET_MISMATCH


def test_event_ticker_date_mismatch_abstain() -> None:
    """A market whose rule text names a different date than its own event ticker encodes
    must abstain -- this is the concrete 'wrong Chicago contract/date' guard."""
    m = market_raw(
        rules_primary=(
            "If the maximum temperature recorded at Chicago (CLIMDW) for Sep 16, 2026, is "
            "greater than 87\N{DEGREE SIGN} fahrenheit according to The Weather Company, "
            "then the market resolves to Yes."
        ),
    )
    market, event, series = triple(m=m)  # event ticker still names Sep 15
    route = route_current_daily_high(market, event, series)
    assert route.state is CurrentDailyHighRouteState.ABSTAIN
    assert route.reason is CurrentDailyHighReason.EVENT_TICKER_DATE_MISMATCH


def test_event_ticker_unparseable_date_abstain() -> None:
    market, event, series = triple(
        e=event_raw(event_ticker="KXHIGHCHI-SPECIAL"),
        m=market_raw(event_ticker="KXHIGHCHI-SPECIAL"),
    )
    route = route_current_daily_high(market, event, series)
    assert route.state is CurrentDailyHighRouteState.ABSTAIN
    assert route.reason is CurrentDailyHighReason.EVENT_TICKER_DATE_UNPARSED


def test_strike_metadata_conflict_abstain() -> None:
    market, event, series = triple(m=market_raw(strike_type="less"))
    route = route_current_daily_high(market, event, series)
    assert route.state is CurrentDailyHighRouteState.ABSTAIN
    assert route.reason is CurrentDailyHighReason.RULE_METADATA_CONFLICT


def test_strike_malformed_abstain() -> None:
    market, event, series = triple(m=market_raw(floor_strike="not-a-number"))
    route = route_current_daily_high(market, event, series)
    assert route.state is CurrentDailyHighRouteState.ABSTAIN
    assert route.reason is CurrentDailyHighReason.STRIKE_MALFORMED


def test_window_established_from_real_early_close_condition() -> None:
    market, event, series = triple()
    route = route_current_daily_high(market, event, series)
    contract = route.contract
    assert contract is not None
    assert contract.window_status is WindowStatus.ESTABLISHED_CIVIL_LOCAL_DAY
    assert contract.window_start_local == datetime.fromisoformat("2026-09-15T00:00:00-05:00")
    assert contract.window_end_local == datetime.fromisoformat("2026-09-16T00:00:00-05:00")
    assert contract.window_evidence_text == EARLY_CLOSE


def test_window_not_established_when_early_close_missing() -> None:
    market, event, series = triple(m=market_raw(early_close_condition=None))
    route = route_current_daily_high(market, event, series)
    assert route.contract is not None
    assert route.contract.window_status is WindowStatus.NOT_ESTABLISHED
    assert route.contract.window_start_local is None


def test_window_not_established_when_early_close_date_mismatches_rule_date() -> None:
    wrong_date_close = EARLY_CLOSE.replace("September 15, 2026", "September 16, 2026")
    market, event, series = triple(m=market_raw(early_close_condition=wrong_date_close))
    route = route_current_daily_high(market, event, series)
    assert route.contract is not None
    assert route.contract.window_status is WindowStatus.NOT_ESTABLISHED


@pytest.mark.parametrize(
    ("target_date", "expected_start_offset", "expected_end_offset"),
    [
        (date(2026, 9, 15), "-05:00", "-05:00"),  # CDT on both sides, ordinary 24h day
        (date(2026, 11, 1), "-05:00", "-06:00"),  # US DST ends 2026-11-01: 25-hour local day
        (date(2027, 3, 14), "-06:00", "-05:00"),  # US DST begins 2027-03-14: 23-hour local day
    ],
)
def test_window_civil_local_day_correct_across_dst_transitions(
    target_date: date, expected_start_offset: str, expected_end_offset: str
) -> None:
    month_name = target_date.strftime("%B")
    close_text = (
        f"The Last Trading Time will be 11:59 PM local time on {month_name} "
        f"{target_date.day}, {target_date.year} regardless of any data releases."
    )
    rule_date_text = f"{target_date.strftime('%b')} {target_date.day}, {target_date.year}"
    ticker_suffix = target_date.strftime("%y%b%d").upper()
    m = market_raw(
        ticker=f"KXHIGHCHI-{ticker_suffix}-T60",
        event_ticker=f"KXHIGHCHI-{ticker_suffix}",
        rules_primary=(
            "If the maximum temperature recorded at Chicago (CLIMDW) for "
            f"{rule_date_text}, is greater than 60\N{DEGREE SIGN} "
            "fahrenheit according to The Weather Company, then the market resolves to Yes."
        ),
        floor_strike=60,
        early_close_condition=close_text,
    )
    e = event_raw(event_ticker=f"KXHIGHCHI-{ticker_suffix}")
    market, event, series = triple(m=m, e=e)
    route = route_current_daily_high(market, event, series)
    contract = route.contract
    assert contract is not None
    assert contract.window_status is WindowStatus.ESTABLISHED_CIVIL_LOCAL_DAY
    assert contract.window_start_local is not None and contract.window_end_local is not None
    start_offset = contract.window_start_local.utcoffset()
    end_offset = contract.window_end_local.utcoffset()
    assert start_offset is not None and end_offset is not None
    assert start_offset.total_seconds() == _offset_seconds(expected_start_offset)
    assert end_offset.total_seconds() == _offset_seconds(expected_end_offset)
    assert (
        timedelta(hours=23)
        <= contract.window_end_local - contract.window_start_local
        <= timedelta(hours=25)
    )


def _offset_seconds(offset: str) -> float:
    sign = -1 if offset.startswith("-") else 1
    hours, minutes = offset.lstrip("+-").split(":")
    return sign * (int(hours) * 3600 + int(minutes) * 60)


def test_frozen_m27c_semantics_are_not_reused_or_reinterpreted() -> None:
    """The new current-live authority must be fully independent of frozen M27C."""
    assert POLICY_VERSION != m27c.POLICY_VERSION
    assert POLICY_IDENTITY != m27c.AUTHORITY_IDENTITY
    assert SERIES_TICKER == "KXHIGHCHI"  # same series, but that is the only overlap
    source_path = (
        Path(__file__).parent.parent
        / "services"
        / "forecasting"
        / "wn_a1_current_daily_high_authority.py"
    )
    source_text = source_path.read_text()
    assert "import services.forecasting.daily_temperature" not in source_text
    assert "from .daily_temperature" not in source_text
    assert "from services.forecasting.daily_temperature" not in source_text
    assert "weather_source_authority" not in source_text


def test_route_not_caller_constructible() -> None:
    with pytest.raises(WnA1Error):
        CurrentDailyHighRoute(
            state=CurrentDailyHighRouteState.SUPPORTED,
            reason=None,
            market_ticker="x",
            event_ticker="x",
            series_ticker="x",
            contract=None,
            source_identity="x",
            policy_identity="x",
            research_only=True,
            production_influence=Decimal(0),
        )
