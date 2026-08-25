"""Offline tests for M28C.1 series discovery/scoping over canonical M28B semantics."""

from __future__ import annotations

import inspect
from typing import Any

import pytest

import services.production_weather_strategy.series_scope as series_scope_module
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
    category: str = "Weather",
    frequency: str = "daily",
    source: object = "The Weather Company",
) -> dict[str, object]:
    return {
        "ticker": ticker,
        "title": title,
        "category": category,
        "frequency": frequency,
        "settlement_sources": [{"name": source}],
    }


def _row(
    *,
    series: str = "KXHIGHAUS",
    identifier: str = "CLIAUS",
    location: str = "Austin",
    date_text: str = "Jun 15, 2024",
    event_suffix: str = "24JUN15",
    floor: int = 70,
    cap: int = 80,
    result: str = "yes",
) -> dict[str, object]:
    event = f"{series}-{event_suffix}"
    return {
        "ticker": f"{event}-B{floor}.5",
        "event_ticker": event,
        "market_type": "binary",
        "status": "settled",
        "result": result,
        "settlement_value_dollars": "1.0000" if result == "yes" else "0.0000",
        "settlement_ts": "2024-06-16T12:00:00Z",
        "rules_primary": (
            f"If the maximum temperature recorded at {location}({identifier}) for "
            f"{date_text}, is between {floor}-{cap}° fahrenheit according to The Weather "
            "Company, then the market resolves to Yes."
        ),
        "rules_secondary": "Official value follows the named rule source.",
        "strike_type": "between",
        "floor_strike": floor,
        "cap_strike": cap,
    }


def _legacy_row(*, series: str = "KXHIGHAUS") -> dict[str, object]:
    value = _row(series=series, event_suffix="23JUN15", date_text="Jun 15, 2023")
    value["settlement_ts"] = "2023-06-16T12:00:00Z"
    value["rules_primary"] = (
        "If the maximum temperature recorded at Austin for Jun 15, 2023, is between "
        "70-80° fahrenheit according to the National Weather Service's Climatological "
        "Report (Daily), then the market resolves to Yes."
    )
    return value


def test_candidate_series_uses_reviewed_categories_title_and_daily_frequency() -> None:
    payload = {
        "series": [
            _series("KXHIGHAUS", "Highest temperature in Austin today?"),
            _series(
                "KXHIGHCHI",
                "Highest temperature in Chicago today?",
                category="Climate and Weather",
            ),
            _series("KXRAINAUS", "Rain in Austin today?"),
            _series("KXMONTHLY", "Average temperature in Austin", frequency="monthly"),
        ]
    }
    assert candidate_temperature_series(payload) == ("KXHIGHAUS", "KXHIGHCHI")


def test_candidate_series_never_treats_series_source_metadata_as_authority() -> None:
    value = _series(
        "KXHIGHAUS",
        "Highest temperature in Austin today?",
        category="Climate and Weather",
        source=None,
    )
    assert candidate_temperature_series({"series": [value]}) == ("KXHIGHAUS",)


def test_candidate_series_fails_closed_on_malformed_or_duplicate_candidate_metadata() -> None:
    duplicate = _series("KXHIGHAUS", "Highest temperature in Austin today?")
    with pytest.raises(SeriesScopeError, match="duplicated"):
        candidate_temperature_series({"series": [duplicate, duplicate]})
    malformed = _series("KXHIGHAUS", "Highest temperature in Austin today?")
    malformed["frequency"] = None
    with pytest.raises(SeriesScopeError, match="frequency"):
        candidate_temperature_series({"series": [malformed]})
    with pytest.raises(SeriesScopeError, match="malformed"):
        candidate_temperature_series({"series": ["bad-row"]})


def test_scoping_includes_current_twc_and_preserves_deterministic_manifest() -> None:
    client = FakeRecentClient(
        {
            "KXHIGHAUS": [_row()],
            "KXHIGHCHI": [
                _row(
                    series="KXHIGHCHI",
                    identifier="CLIMDW",
                    location="Chicago",
                )
            ],
        }
    )
    rows, manifest = scope_recent_settled_markets(client, ("KXHIGHCHI", "KXHIGHAUS"))
    assert client.calls == ["KXHIGHAUS", "KXHIGHCHI"]
    assert [row["ticker"] for row in rows] == sorted(row["ticker"] for row in rows)
    assert manifest.included_series_tickers == ("KXHIGHAUS", "KXHIGHCHI")
    assert manifest.station_ids == ("CLIAUS", "CLIMDW")
    assert manifest.raw_market_count == 2
    assert manifest.supported_event_count == 2
    assert manifest.supported_contract_count == 2
    again = scope_recent_settled_markets(
        FakeRecentClient(client.rows_by_series), ("KXHIGHAUS", "KXHIGHCHI")
    )[1]
    assert again.content_hash == manifest.content_hash


def test_scoping_handles_mixed_legacy_and_current_regimes_without_relabeling() -> None:
    current = _row()
    client = FakeRecentClient({"KXHIGHAUS": [_legacy_row(), current]})
    rows, manifest = scope_recent_settled_markets(client, ("KXHIGHAUS",))
    assert [row["ticker"] for row in rows] == [current["ticker"]]
    assert manifest.legacy_regime_market_count == 1
    assert manifest.included_series[0].raw_market_count == 2
    assert manifest.included_series[0].supported_contract_count == 1


def test_legacy_only_series_is_explicit_exclusion_when_another_series_is_supported() -> None:
    client = FakeRecentClient(
        {
            "KXHIGHAUS": [_legacy_row()],
            "KXHIGHCHI": [_row(series="KXHIGHCHI", identifier="CLIMDW", location="Chicago")],
        }
    )
    _, manifest = scope_recent_settled_markets(client, ("KXHIGHAUS", "KXHIGHCHI"))
    record = next(row for row in manifest.excluded_series if row.series_ticker == "KXHIGHAUS")
    assert record.reason == "NO_SUPPORTED_CURRENT_REGIME_MARKETS"
    assert record.legacy_regime_market_count == 1


def test_unreviewed_current_location_is_an_explicit_series_exclusion() -> None:
    unreviewed = _row()
    unreviewed["rules_primary"] = (
        "If the maximum temperature recorded at Atlantis for Jun 15, 2024, is between "
        "70-80° fahrenheit according to The Weather Company, then the market resolves to Yes."
    )
    client = FakeRecentClient(
        {
            "KXBAD": [unreviewed],
            "KXHIGHAUS": [_row()],
        }
    )
    _, manifest = scope_recent_settled_markets(client, ("KXBAD", "KXHIGHAUS"))
    record = next(row for row in manifest.excluded_series if row.series_ticker == "KXBAD")
    assert record.reason == "UNREVIEWED_SETTLEMENT_LOCATION"


def test_malformed_current_regime_fails_closed_instead_of_becoming_exclusion() -> None:
    malformed = _row()
    malformed["floor_strike"] = 69
    with pytest.raises(HistoricalWeatherDatasetError, match="strike values conflict"):
        scope_recent_settled_markets(FakeRecentClient({"KXHIGHAUS": [malformed]}), ("KXHIGHAUS",))


def test_included_series_must_bind_exactly_one_reviewed_station() -> None:
    austin = _row(event_suffix="24JUN15")
    chicago = _row(
        identifier="CLIMDW",
        location="Chicago",
        event_suffix="24JUN16",
        date_text="Jun 16, 2024",
    )
    with pytest.raises(SeriesScopeError, match="exactly one"):
        scope_recent_settled_markets(
            FakeRecentClient({"KXHIGHAUS": [austin, chicago]}), ("KXHIGHAUS",)
        )


def test_scoping_does_not_infer_series_identity_from_requested_ticker() -> None:
    wrong = _row(series="KXHIGHCHI", identifier="CLIMDW", location="Chicago")
    with pytest.raises(SeriesScopeError, match="crossed its series identity"):
        scope_recent_settled_markets(FakeRecentClient({"KXHIGHAUS": [wrong]}), ("KXHIGHAUS",))


def test_unrelated_rows_do_not_become_current_weather_evidence() -> None:
    unrelated = _row()
    unrelated["rules_primary"] = "Will the Fed cut rates?"
    with pytest.raises(SeriesScopeError, match="no reviewed current-regime"):
        scope_recent_settled_markets(FakeRecentClient({"KXHIGHAUS": [unrelated]}), ("KXHIGHAUS",))


def test_series_scope_uses_canonical_m28b_classifier_without_duplicate_rule_parser() -> None:
    source = inspect.getsource(series_scope_module)
    assert "classify_resolved_temperature_market" in source
    assert "re.compile" not in source
    assert "import re" not in source
