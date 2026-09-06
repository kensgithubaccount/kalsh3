from decimal import Decimal

from services.forecasting.cpi_p10d_sealed_scorer import score_rows


def row(event: str, ticker: str, ask: str, outcome: int, valid: bool = True) -> dict:
    return {
        "event_ticker": event,
        "market_ticker": ticker,
        "threshold": "0.2",
        "reuters_point_forecast": "0.3",
        "kalshi_yes_ask": ask,
        "kalshi_midpoint": ask,
        "canonical_outcome": outcome,
        "quote_valid": valid,
    }


def test_event_equal_and_tie_denominator_rules() -> None:
    events = ("CPI-23AUG", "KXCPI-25JUL", "KXCPI-25DEC", "KXCPI-26JAN")
    rows = []
    for event in events:
        rows.extend((row(event, event + "-A", "0.60", 1), row(event, event + "-B", "0.40", 0)))
    rows.append(row("CPI-23AUG", "CPI-23AUG-C", "0.5000", 1))
    result = score_rows(rows, events)
    assert result["counts"]["accepted_siblings"] == 9
    assert result["counts"]["primary_before_tie"] == 9
    assert result["counts"]["common_primary_after_tie"] == 8
    assert result["counts"]["exact_0_5_tie_exclusions"] == 1
    assert result["event_equal_reuters_score"] == "0.5"
    assert result["event_equal_kalshi_score"] == "1"
    assert Decimal(result["reuters_minus_kalshi_difference"]) == Decimal("-0.5")


def test_boundary_is_excluded_for_both_sources() -> None:
    events = ("CPI-23AUG", "KXCPI-25JUL", "KXCPI-25DEC", "KXCPI-26JAN")
    rows = [row(event, event + "-A", "0.60", 1) for event in events]
    rows.extend(row(event, event + "-B", "1.0000", 1, valid=False) for event in events)
    result = score_rows(rows, events)
    assert result["counts"]["primary_before_tie"] == 4
    assert result["counts"]["common_primary_after_tie"] == 4
    assert all(
        item["inclusion_exclusion_reason"] == "frozen_boundary_quote_exclusion"
        for item in result["per_sibling_result_rows"]
        if item["market_ticker"].endswith("-B")
    )
