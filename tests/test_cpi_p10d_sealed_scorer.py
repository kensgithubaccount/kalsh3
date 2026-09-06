from decimal import Decimal

import pytest

from services.forecasting.cpi_p10d_sealed_scorer import (
    SealedScoringError,
    _canonical_outcome_for_row,
    score_rows,
)

EVENTS = ("CPI-23AUG", "KXCPI-25JUL", "KXCPI-25DEC", "KXCPI-26JAN")


def row(event: str, suffix: str, ask: str = "0.60", outcome: int = 1, valid: bool = True) -> dict:
    return {
        "event_ticker": event,
        "market_ticker": f"{event}-{suffix}",
        "threshold": "0.2",
        "reuters_point_forecast": "0.3",
        "kalshi_yes_ask": ask,
        "kalshi_midpoint": ask,
        "canonical_outcome": outcome,
        "quote_valid": valid,
    }


def authority(result: object = "yes") -> tuple[dict, dict]:
    accepted = {
        "T": ("CPI-23AUG", "0.2", "GT:0.2"),
    }
    inventory = {
        "T": {
            "ticker": "T",
            "event_ticker": "CPI-23AUG",
            "rules_primary": "more than 0.2%",
            "result": result,
        },
    }
    return accepted, inventory


def test_missing_p9a_result_is_irrelevant_to_canonical_authority() -> None:
    accepted, _inventory = authority()
    with pytest.raises(SealedScoringError):
        _canonical_outcome_for_row(
            {"market_ticker": "T", "event_ticker": "CPI-23AUG", "threshold": "0.2"}, accepted, {}
        )


def test_outcome_requires_verified_p10a_inventory_route() -> None:
    accepted, inventory = authority()
    assert (
        _canonical_outcome_for_row(
            {"market_ticker": "T", "event_ticker": "CPI-23AUG", "threshold": "0.2"},
            accepted,
            inventory,
        )
        == 1
    )


@pytest.mark.parametrize("result", [None, "maybe", 1])
def test_inventory_result_absent_or_outside_yes_no_fails_closed(result: object) -> None:
    accepted, inventory = authority(result)
    with pytest.raises(SealedScoringError):
        _canonical_outcome_for_row(
            {"market_ticker": "T", "event_ticker": "CPI-23AUG", "threshold": "0.2"},
            accepted,
            inventory,
        )


def test_ticker_not_in_p10a_accepted_identity_fails_closed() -> None:
    accepted, inventory = authority()
    with pytest.raises(SealedScoringError):
        _canonical_outcome_for_row(
            {"market_ticker": "OTHER", "event_ticker": "CPI-23AUG", "threshold": "0.2"},
            accepted,
            inventory,
        )


def test_threshold_mismatch_fails_closed() -> None:
    accepted, inventory = authority()
    with pytest.raises(SealedScoringError):
        _canonical_outcome_for_row(
            {"market_ticker": "T", "event_ticker": "CPI-23AUG", "threshold": "0.3"},
            accepted,
            inventory,
        )


def test_event_mismatch_fails_closed() -> None:
    accepted, inventory = authority()
    with pytest.raises(SealedScoringError):
        _canonical_outcome_for_row(
            {"market_ticker": "T", "event_ticker": "KXCPI-25JUL", "threshold": "0.2"},
            accepted,
            inventory,
        )


def test_event_equal_directional_aggregation_with_unequal_sibling_counts() -> None:
    rows = [row(EVENTS[0], "A", outcome=1)]
    rows.extend(
        row(EVENTS[i], f"{j}", ask="0.40", outcome=0) for i in range(1, 4) for j in range(3)
    )
    result = score_rows(rows, EVENTS)
    assert result["event_equal_kalshi_score"] == "1"
    assert result["counts"]["common_primary_after_tie"] == 10


def test_exact_half_excluded_primary_but_retained_probabilistic_diagnostics() -> None:
    rows = [row(event, "A", outcome=1) for event in EVENTS]
    rows.append(row(EVENTS[0], "TIE", ask="0.5000", outcome=1))
    result = score_rows(rows, EVENTS)
    assert result["counts"]["primary_before_tie"] == 5
    assert result["counts"]["common_primary_after_tie"] == 4
    assert result["counts"]["exact_0_5_tie_exclusions"] == 1
    assert result["counts"]["brier_diagnostic_rows"] == 5


def test_boundary_invalid_quote_excluded_for_both_sources_from_primary() -> None:
    rows = [row(event, "VALID") for event in EVENTS]
    rows.extend(row(event, "BOUNDARY", ask="1.0000", outcome=1, valid=False) for event in EVENTS)
    result = score_rows(rows, EVENTS)
    assert result["counts"]["primary_before_tie"] == 4
    assert result["counts"]["common_primary_after_tie"] == 4
    assert all(
        item["inclusion_exclusion_reason"] == "frozen_boundary_quote_exclusion"
        for item in result["per_sibling_result_rows"]
        if item["market_ticker"].endswith("BOUNDARY")
    )


def test_brier_logloss_and_midpoint_are_event_equal_not_pooled() -> None:
    rows = [row(EVENTS[0], "ONLY", ask="0.90", outcome=1)]
    rows.extend(
        row(EVENTS[i], f"{j}", ask="0.10", outcome=0) for i in range(1, 4) for j in range(3)
    )
    result = score_rows(rows, EVENTS)
    assert Decimal(result["secondary_diagnostics"]["kalshi_yes_ask_crossing_brier"]) == Decimal(
        "0.01"
    )
    assert Decimal(result["secondary_diagnostics"]["kalshi_two_sided_midpoint_brier"]) == Decimal(
        "0.01"
    )
    assert abs(
        Decimal(result["secondary_diagnostics"]["kalshi_yes_ask_clipped_log_loss"])
        - Decimal("0.10536051565782628")
    ) < Decimal("1e-16")
    assert result["counts"]["brier_diagnostic_rows"] == 10


def test_zero_common_primary_rows_for_any_event_fails_closed() -> None:
    rows = [row(event, "ONLY", ask="0.5000") for event in EVENTS]
    with pytest.raises(SealedScoringError):
        score_rows(rows, EVENTS)


def test_event_roster_outside_frozen_four_fails_closed() -> None:
    rows = [row(event, "A") for event in EVENTS]
    with pytest.raises(SealedScoringError):
        score_rows(rows, (*EVENTS[:3], "CPI-24JAN"))
