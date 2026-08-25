from __future__ import annotations

import importlib.util
from collections.abc import Mapping
from pathlib import Path
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


def test_public_markets_use_series_filter_and_no_auth() -> None:
    transport = Pages([(200, {"markets": [{"ticker": "M1"}], "cursor": ""})])
    assert HistoricalClient(transport).markets(series_ticker="KX HIGH/CHI") == [{"ticker": "M1"}]
    assert transport.paths == [
        "/trade-api/v2/historical/markets?limit=1000&series_ticker=KX%20HIGH%2FCHI"
    ]
    assert transport.headers == [{}]


def test_unfiltered_markets_remain_supported_but_are_not_runner_complete_mode() -> None:
    transport = Pages([(200, {"markets": [{"ticker": "M1"}], "cursor": ""})])
    assert HistoricalClient(transport).markets() == [{"ticker": "M1"}]
    assert transport.paths == ["/trade-api/v2/historical/markets?limit=1000&mve_filter=exclude"]


def test_recent_settled_markets_complete_reviewed_series_partitions() -> None:
    transport = Pages([(200, {"markets": [{"ticker": "M1"}], "cursor": ""})])
    assert HistoricalClient(transport).recent_settled_markets(series_ticker="KXHIGHAUS") == [
        {"ticker": "M1"}
    ]
    assert transport.paths == [
        "/trade-api/v2/markets?limit=1000&status=settled&series_ticker=KXHIGHAUS"
    ]
    assert transport.headers == [{}]


def test_cursor_is_encoded_and_repeated_cursor_fails() -> None:
    transport = Pages(
        [
            (200, {"markets": [{"ticker": "M1"}], "cursor": "a/b+c=="}),
            (200, {"markets": [{"ticker": "M2"}], "cursor": "a/b+c=="}),
        ]
    )
    with pytest.raises(HistoricalError, match="cursor"):
        HistoricalClient(transport).markets(series_ticker="KXHIGHAUS")
    assert transport.paths[1].endswith("cursor=a%2Fb%2Bc%3D%3D")


def test_duplicate_records_across_pages_fail_closed() -> None:
    transport = Pages(
        [
            (200, {"markets": [{"ticker": "M1"}], "cursor": "next"}),
            (200, {"markets": [{"ticker": "M1"}], "cursor": ""}),
        ]
    )
    with pytest.raises(HistoricalError, match="duplicate"):
        HistoricalClient(transport).markets(series_ticker="KXHIGHAUS")


def test_non_200_and_malformed_pages_fail_closed() -> None:
    with pytest.raises(HistoricalError, match="status"):
        HistoricalClient(Pages([(503, {})])).markets(series_ticker="KXHIGHAUS")
    with pytest.raises(HistoricalError, match="malformed"):
        HistoricalClient(Pages([(200, {"markets": "bad", "cursor": ""})])).markets(
            series_ticker="KXHIGHAUS"
        )


def test_specific_market_uses_singular_response_and_encoded_ticker() -> None:
    transport = Pages([(200, {"market": {"ticker": "M/1"}})])
    assert HistoricalClient(transport).market("M/1") == {"ticker": "M/1"}
    assert transport.paths == ["/trade-api/v2/historical/markets/M%2F1"]
    assert transport.headers == [{}]


def test_candles_require_explicit_time_range_and_validate_periods() -> None:
    transport = Pages([(200, {"candlesticks": [{"end_period_ts": 101}]})])
    assert HistoricalClient(transport).candles("M1", 1, start_ts=100, end_ts=200) == [
        {"end_period_ts": 101}
    ]
    assert transport.paths == [
        "/trade-api/v2/historical/markets/M1/candlesticks?start_ts=100&end_ts=200&period_interval=1"
    ]
    with pytest.raises(HistoricalError):
        HistoricalClient(Pages([])).candles("M1", 1, start_ts=200, end_ts=100)
    with pytest.raises(HistoricalError, match="duplicate"):
        HistoricalClient(
            Pages([(200, {"candlesticks": [{"end_period_ts": 101}, {"end_period_ts": 101}]})])
        ).candles("M1", 1, start_ts=100, end_ts=200)


def test_private_history_stays_signer_gated_and_no_mutation_api() -> None:
    client = HistoricalClient(Pages([]))
    with pytest.raises(HistoricalError, match="read credential"):
        client.fills()
    with pytest.raises(HistoricalError, match="read credential"):
        client.orders()
    assert not any(hasattr(client, name) for name in ("post", "put", "delete", "submit_order"))


def test_runner_is_dormant_fixed_origin_no_auth_get_only_and_series_required() -> None:
    source = Path("scripts/run_m28b_public_historical_weather.py").read_text()
    assert "ORIGIN = PUBLIC_KALSHI_ORIGIN" in source
    assert 'method="GET"' in source
    assert "forbids authorization headers" in source
    assert 'parser.add_argument("--series-ticker", required=True)' in source
    assert "recent_settled_markets" in source and "client.markets" in source
    assert "O_EXCL" in source and "0o600" in source and "os.fsync" in source
    assert 'if __name__ == "__main__"' in source
    assert "requests.post" not in source and "submit_order" not in source


def test_importing_runner_performs_no_network() -> None:
    path = Path("scripts/run_m28b_public_historical_weather.py")
    spec = importlib.util.spec_from_file_location("m28b_runner_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.ORIGIN == "https://external-api.kalshi.com"
