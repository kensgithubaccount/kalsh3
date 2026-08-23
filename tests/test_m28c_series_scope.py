"""Offline tests for M28C reviewed-series discovery and settlement scoping."""

from __future__ import annotations

from typing import Any

import pytest

from services.production_weather_strategy.series_scope import (
    SeriesScopeError,
    candidate_temperature_series,
    scope_recent_settled_markets,
)
from services.production_weather_strategy.settlement_dataset import HistoricalWeatherDatasetError


class FakeRecentClient:
    def __init__(self, rows_by_series: dict[str, list[dict[str, Any]]]) -> None:
        self.rows_by_series = rows_by_series
        self.calls: list[str] = []

    def recent_settled_markets(self, *, series_ticker: str) -> list[dict[str, Any]]:
        self.calls.append(series_ticker)
        return list(self.rows_by_series.get(series_ticker, []))


def _series(
    ticker: str,
    title: str,
    *,
    source: str = "The Weather Company",
    category: str = "Weather",
    frequency: str = "daily",
) -> dict[str, object]:
    return {
        "ticker": ticker,
        "title": title,
        "category": category,
        "frequency": frequency,
        "settlement_sources": [{"name": source, "url": "https://example.invalid"}],
    }


def _settled_row(
    *,
    series: str,
    station_id: str,
    location: str,
    date_text: str = "Aug 20, 2026",
    floor: int = 76,
    cap: int = 77,
) -> dict[str, object]:
    event = f"{series}-26AUG20"
    return {
        "ticker": f"{event}-B76.5",
        "event_ticker": event,
        "market_type": "binary",
        "status": "settled",
        "result": "yes",
        "settlement_value_dollars": "1.0000",
        "settlement_ts": "2026-08-21T12:00:00Z",
        "rules_primary": (
            f"If the maximum temperature recorded at {location}({station_id}) for "
            f"{date_text}, is between {floor}-{cap}° fahrenheit according to The Weather "
            "Company, then the market resolves to Yes."
        ),
        "rules_secondary": "Outcome verified from The Weather Company.",
        "strike_type": "between",
        "floor_strike": floor,
        "cap_strike": cap,
    }


def test_candidate_series_uses_category_and_title_metadata_without_ticker_guessing() -> None:
    payload = {
        "series": [
            _series("KXHIGHCHI", "Highest temperature in Chicago today?"),
            _series(
                "KXLOWTNYC",
                "Lowest temperature in New York City today?",
                category="Climate and Weather",
            ),
            _series("KXRAINSEA", "Rainfall in Seattle today?"),
            _series(
                "KXHIGHOLD",
                "Highest temperature in Legacy City today?",
                source="National Weather Service",
            ),
            _series(
                "KXMONTHLYTEMP",
                "Average temperature in Example City this month?",
                frequency="monthly",
            ),
        ]
    }
    assert candidate_temperature_series(payload) == (
        "KXHIGHCHI",
        "KXHIGHOLD",
        "KXLOWTNYC",
    )


def test_candidate_series_does_not_treat_series_source_metadata_as_settlement_authority() -> None:
    legacy_source_metadata = _series(
        "KXHIGHNY",
        "Highest temperature in New York City today?",
        source="NWS Climatological Report",
        category="Climate and Weather",
    )
    legacy_source_metadata["settlement_sources"] = [{"name": None}]
    assert candidate_temperature_series({"series": [legacy_source_metadata]}) == ("KXHIGHNY",)


def test_candidate_series_ignores_unrelated_malformed_series() -> None:
    unrelated = {
        "category": "Politics",
        "settlement_sources": [{"name": None}],
    }
    weather_non_temperature = _series("KXRAINSEA", "Rainfall in Seattle today?")
    weather_non_temperature["settlement_sources"] = [{"name": None}]
    payload = {
        "series": [
            unrelated,
            weather_non_temperature,
            _series("KXHIGHCHI", "Highest temperature in Chicago today?"),
        ]
    }
    assert candidate_temperature_series(payload) == ("KXHIGHCHI",)


def test_candidate_series_fails_closed_on_duplicate_or_malformed_candidate_metadata() -> None:
    duplicate = _series("KXHIGHCHI", "Highest temperature in Chicago today?")
    with pytest.raises(SeriesScopeError, match="duplicated"):
        candidate_temperature_series({"series": [duplicate, duplicate]})
    malformed = _series("KXHIGHCHI", "Highest temperature in Chicago today?")
    malformed["frequency"] = None
    with pytest.raises(SeriesScopeError, match="frequency"):
        candidate_temperature_series({"series": [malformed]})


def test_scoping_keeps_reviewed_series_and_records_unreviewed_location_exclusion() -> None:
    client = FakeRecentClient(
        {
            "KXHIGHCHI": [
                _settled_row(series="KXHIGHCHI", station_id="CLIMDW", location="Chicago")
            ],
            "KXHIGHNY": [
                _settled_row(
                    series="KXHIGHNY",
                    station_id="CLINYC",
                    location="New York City",
                )
            ],
            "KXHIGHPDX": [
                _settled_row(
                    series="KXHIGHPDX",
                    station_id="CLIPDX",
                    location="Portland",
                )
            ],
        }
    )
    rows, manifest = scope_recent_settled_markets(
        client,
        ("KXHIGHPDX", "KXHIGHNY", "KXHIGHCHI"),
    )

    assert client.calls == ["KXHIGHCHI", "KXHIGHNY", "KXHIGHPDX"]
    assert [row["ticker"] for row in rows] == sorted(row["ticker"] for row in rows)
    assert manifest.included_series_tickers == ("KXHIGHCHI", "KXHIGHNY")
    assert manifest.station_ids == ("CLIMDW", "CLINYC")
    assert manifest.raw_market_count == 2
    assert manifest.supported_event_count == 2
    assert manifest.supported_contract_count == 2
    assert len(manifest.excluded_series) == 1
    assert manifest.excluded_series[0].series_ticker == "KXHIGHPDX"
    assert manifest.excluded_series[0].reason == "UNREVIEWED_SETTLEMENT_LOCATION"


def test_scoping_does_not_turn_malformed_reviewed_evidence_into_an_exclusion() -> None:
    malformed = _settled_row(series="KXHIGHCHI", station_id="CLIMDW", location="Chicago")
    malformed["floor_strike"] = 75
    client = FakeRecentClient({"KXHIGHCHI": [malformed]})
    with pytest.raises(HistoricalWeatherDatasetError, match="strike values conflict"):
        scope_recent_settled_markets(client, ("KXHIGHCHI",))


def test_scoping_records_series_without_current_regime_rows_instead_of_platform_sweep() -> None:
    old_rule = _settled_row(series="KXHIGHCHI", station_id="CLIMDW", location="Chicago")
    old_rule["rules_primary"] = (
        "Resolves Yes if the highest temperature in Chicago is between 76-77 degrees."
    )
    client = FakeRecentClient(
        {
            "KXHIGHCHI": [old_rule],
            "KXHIGHNY": [
                _settled_row(
                    series="KXHIGHNY",
                    station_id="CLINYC",
                    location="New York City",
                )
            ],
        }
    )
    rows, manifest = scope_recent_settled_markets(client, ("KXHIGHCHI", "KXHIGHNY"))
    assert len(rows) == 1
    assert manifest.included_series_tickers == ("KXHIGHNY",)
    assert manifest.excluded_series[0].series_ticker == "KXHIGHCHI"
    assert manifest.excluded_series[0].reason == "NO_SUPPORTED_CURRENT_REGIME_MARKETS"
