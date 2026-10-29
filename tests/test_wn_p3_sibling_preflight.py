"""Synthetic only: successful preflight is not an attested Kalshi market issuer."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any

import pytest

from services.forecasting.wn_p3_sibling_preflight import (
    CITY_SERIES,
    PreflightError,
    StructuralRoster,
    event_ticker_for,
    validate_untrusted_roster,
)

TARGET = date(2026, 11, 1)
SERIES = "KXHIGHTBOS"


def sample(series: str = SERIES, target: date = TARGET) -> dict[str, Any]:
    event = event_ticker_for(series, target)
    specs = [
        ("T70", "less", None, 70),
        ("B70", "between", 70, 71),
        ("B72", "between", 72, 73),
        ("B74", "between", 74, 75),
        ("B76", "between", 76, 77),
        ("T77", "greater", 77, None),
    ]
    return {
        "event": {"event_ticker": event, "series_ticker": series},
        "markets": [
            {
                "ticker": f"{event}-{suffix}",
                "event_ticker": event,
                "status": "active",
                "market_type": "binary",
                "strike_type": strike,
                "floor_strike": low,
                "cap_strike": high,
                "rules_primary": "Untrusted synthetic rule fixture only",
            }
            for suffix, strike, low, high in specs
        ],
    }


def evaluate(
    payload: object, series: str = SERIES, target: date = TARGET
) -> StructuralRoster:
    return validate_untrusted_roster(
        series_ticker=series, target_local_date=target, event_payload=payload
    )


@pytest.mark.parametrize("series", sorted(CITY_SERIES))
def test_accepts_exact_structural_roster_only(series: str) -> None:
    result = evaluate(sample(series), series)
    assert len(result.market_tickers) == 6
    assert len(set(result.market_tickers)) == 6
    assert result.integer_fahrenheit_bins == ((70, 71), (72, 73), (74, 75), (76, 77))
    assert not result.input_source_attested
    assert not result.rules_and_station_authorized
    assert not result.fee_depth_authorized
    assert not result.probability_authorized
    assert not result.paper_authorized
    assert result.trading_authority == "NONE"
    assert result.research_only


def test_order_independent() -> None:
    payload = sample()
    markets = payload["markets"]
    assert isinstance(markets, list)
    markets.reverse()
    assert evaluate(payload).market_tickers == evaluate(sample()).market_tickers


@pytest.mark.parametrize("target", [date(2026, 10, 31), date(2100, 1, 1)])
def test_date_outside_scope_rejected(target: date) -> None:
    with pytest.raises(PreflightError):
        evaluate(sample(), target=target)


def test_wrong_series_rejected() -> None:
    with pytest.raises(PreflightError):
        evaluate(sample(), series="KXHIGHCHI")


@pytest.mark.parametrize("mutate", ["missing", "extra", "duplicate"])
def test_incomplete_extra_duplicate_roster_rejected(mutate: str) -> None:
    payload = sample()
    markets = payload["markets"]
    assert isinstance(markets, list)
    if mutate == "missing":
        markets.pop()
    elif mutate == "extra":
        markets.append(deepcopy(markets[-1]))
    else:
        markets[2] = deepcopy(markets[1])
    with pytest.raises(PreflightError):
        evaluate(payload)


@pytest.mark.parametrize("field,value", [
    ("event_ticker", "KXHIGHTBOS-26NOV02"),
    ("status", "closed"),
    ("market_type", "categorical"),
    ("is_provisional", True),
    ("functional_strike", "x"),
    ("strike_type", "binary"),
    ("ticker", "KXHIGHMIA-26NOV01-T70"),
    ("mve_collection_ticker", "MVE"),
    ("rules_primary", ""),
])
def test_wrong_market_metadata_abstains(field: str, value: object) -> None:
    payload = sample()
    markets = payload["markets"]
    assert isinstance(markets, list)
    markets[0][field] = value
    with pytest.raises(PreflightError):
        evaluate(payload)


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), True, 70.0, "70.5", "nan", "inf", {}, None]
)
def test_unsafe_or_fractional_bound_rejected(value: object) -> None:
    payload = sample()
    markets = payload["markets"]
    assert isinstance(markets, list)
    markets[0]["cap_strike"] = value
    with pytest.raises(PreflightError):
        evaluate(payload)


@pytest.mark.parametrize("row_index,field,value", [
    (0, "cap_strike", 69),
    (1, "floor_strike", 69),
    (1, "cap_strike", 72),
    (2, "floor_strike", 73),
    (3, "cap_strike", 80),
    (5, "floor_strike", 78),
    (4, "cap_strike", 75),
    (5, "cap_strike", 77),
    (0, "floor_strike", 10),
])
def test_gap_overlap_tail_and_inverted_topology_rejected(
    row_index: int, field: str, value: object
) -> None:
    payload = sample()
    markets = payload["markets"]
    assert isinstance(markets, list)
    markets[row_index][field] = value
    with pytest.raises(PreflightError):
        evaluate(payload)


def test_event_date_and_series_mismatch_rejected() -> None:
    payload = sample()
    event = payload["event"]
    assert isinstance(event, dict)
    event["event_ticker"] = "KXHIGHTBOS-26NOV02"
    with pytest.raises(PreflightError):
        evaluate(payload)
    event["event_ticker"] = "KXHIGHTBOS-26NOV01"
    event["series_ticker"] = "KXHIGHMIA"
    with pytest.raises(PreflightError):
        evaluate(payload)


def test_caller_authored_rules_never_grant_semantic_authority() -> None:
    payload = sample()
    markets = payload["markets"]
    assert isinstance(markets, list)
    markets[0]["rules_primary"] = "Forged station and temperature measurement"
    result = evaluate(payload)
    assert result.rules_and_station_authorized is False
    assert result.input_source_attested is False
