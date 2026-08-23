"""Offline tests for M28B's public-only historical Kalshi read surface."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from services.historical_replay.client import HistoricalClient, HistoricalError


class Pages:
    def __init__(self, replies: list[tuple[int, dict[str, Any]]]) -> None:
        self.replies = iter(replies)
        self.paths: list[str] = []
        self.headers: list[Mapping[str, str]] = []

    def get(
        self, path: str, headers: Mapping[str, str], *, timeout_seconds: float
    ) -> tuple[int, dict[str, Any]]:
        self.paths.append(path)
        self.headers.append(headers)
        return next(self.replies)


def test_public_markets_use_max_page_and_optional_series_filter() -> None:
    transport = Pages([(200, {"markets": [{"ticker": "M1"}], "cursor": ""})])
    markets = HistoricalClient(transport).markets(series_ticker="KXHIGHCHI")
    assert markets == [{"ticker": "M1"}]
    assert transport.paths == [
        "/trade-api/v2/historical/markets?limit=1000&mve_filter=exclude&series_ticker=KXHIGHCHI"
    ]
    assert transport.headers == [{}]


def test_specific_market_uses_current_singular_market_response() -> None:
    ticker = "KXHIGHCHI-26AUG22-B80.5"
    transport = Pages([(200, {"market": {"ticker": ticker}})])
    market = HistoricalClient(transport).market(ticker)
    assert market["ticker"] == ticker
    assert transport.paths == [f"/trade-api/v2/historical/markets/{ticker}"]
    assert transport.headers == [{}]


def test_candles_bind_required_time_range_and_remain_public() -> None:
    transport = Pages(
        [
            (
                200,
                {
                    "ticker": "M1",
                    "candlesticks": [
                        {
                            "end_period_ts": 101,
                            "yes_bid": {"close": "0.4000"},
                            "yes_ask": {"close": "0.4200"},
                        }
                    ],
                },
            )
        ]
    )
    candles = HistoricalClient(transport).candles("M1", 1, start_ts=100, end_ts=200)
    assert candles[0]["end_period_ts"] == 101
    assert transport.paths == [
        "/trade-api/v2/historical/markets/M1/candlesticks"
        "?start_ts=100&end_ts=200&period_interval=1"
    ]
    assert transport.headers == [{}]


@pytest.mark.parametrize(
    ("interval", "start", "end"),
    [(5, 100, 200), (1, -1, 200), (1, 200, 100)],
)
def test_invalid_candle_requests_fail_before_transport(
    interval: int, start: int, end: int
) -> None:
    with pytest.raises(HistoricalError):
        HistoricalClient(Pages([])).candles("M1", interval, start_ts=start, end_ts=end)


def test_m28b_public_surface_sends_no_auth_headers() -> None:
    transport = Pages([(200, {"markets": [{"ticker": "M1"}], "cursor": ""})])
    HistoricalClient(transport).markets()
    assert transport.headers == [{}]
