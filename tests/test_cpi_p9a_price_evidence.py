from datetime import UTC, datetime
from decimal import Decimal

from services.historical_replay.cpi_price_evidence import build_price_evidence


def market() -> dict[str, object]:
    return {
        "event_ticker": "KXCPI-25JUL",
        "ticker": "KXCPI-25JUL-T0.2",
        "floor_strike": 0.2,
        "open_time": "2025-06-01T00:00:00Z",
        "close_time": "2025-08-12T12:25:00Z",
        "volume_fp": "1234.00",
    }


def candle(end: int = 1755000000) -> dict[str, object]:
    return {
        "end_period_ts": end,
        "yes_bid": {"close": "0.42"},
        "yes_ask": {"close": "0.47"},
        "volume": "12.00",
    }


def test_builds_strict_preclose_complement_quotes() -> None:
    result = build_price_evidence(
        market(),
        request_path="/trade-api/v2/historical/markets/KXCPI-25JUL-T0.2/candlesticks",
        request_start_ts=1,
        request_end_ts=2_000_000_000,
        raw_body=b'{"candlesticks":[]}',
        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
        candles=[candle()],
    )
    assert result.yes_ask == Decimal(".47")
    assert result.no_bid == Decimal(".53")
    assert result.no_ask == Decimal(".58")
    assert result.no_quote_provenance == "DERIVED_COMPLEMENT"
    assert result.historical_total_volume == Decimal("1234.00")


def test_postclose_candle_is_explicit_missing_evidence() -> None:
    postclose = candle(end=int(datetime(2025, 8, 12, 12, 26, tzinfo=UTC).timestamp()))
    result = build_price_evidence(
        market(),
        request_path="/x",
        request_start_ts=1,
        request_end_ts=2,
        raw_body=b"{}",
        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
        candles=[postclose],
    )
    assert result.candle_end_period_ts is None
    assert result.missing_side_reason == "NO_CANDLE_STRICTLY_BEFORE_CLOSE"


def test_boundary_quotes_do_not_claim_both_executable_sides() -> None:
    boundary = candle()
    boundary["yes_bid"] = {"close": "0.00"}
    boundary["yes_ask"] = {"close": "1.00"}
    result = build_price_evidence(
        market(),
        request_path="/x",
        request_start_ts=1,
        request_end_ts=2,
        raw_body=b"{}",
        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
        candles=[boundary],
    )
    assert "YES_ENTRY_BOUNDARY_ASK_1.00" in (result.missing_side_reason or "")
    assert "NO_ENTRY_BOUNDARY_FROM_YES_BID_0.00" in (result.missing_side_reason or "")
