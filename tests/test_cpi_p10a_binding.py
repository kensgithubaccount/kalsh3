from pathlib import Path

import pytest

from services.forecasting.cpi_p10a_binding import _predicate, build_binding

ROOT = Path(__file__).resolve().parents[1]


def test_canonical_overlap_is_derived_and_event_weighted() -> None:
    report = build_binding(ROOT)
    assert report["p9a_events"] == 60
    assert report["p9a_siblings"] == 474
    assert report["bound_events"] == 42
    assert report["usable_events"] == 42
    assert report["quote_usable_rows"] == 200
    assert report["crossing_price_brier_diagnostic"] >= 0
    assert report["crossing_price_log_loss_diagnostic"] >= 0


def test_malformed_identity_fails_closed() -> None:
    from services.forecasting.cpi_p10a_binding import _event_month

    with pytest.raises(ValueError):
        _event_month("not-a-cpi-event")


def test_placeholder_rule_has_no_ticker_fallback() -> None:
    with pytest.raises(ValueError):
        _predicate({"rules_primary": "If the CPI increases by more than || percent ||%"})


def test_known_reference_month_mismatch_is_rejected() -> None:
    report = build_binding(ROOT)
    mismatches = [
        item
        for item in report["rejected_rows"]
        if item["reason"] == "predicate/reference month mismatch"
    ]
    assert mismatches == [
        {"market_ticker": "CPI-22JUN-T0.2", "reason": "predicate/reference month mismatch"}
    ]


def test_report_requires_predictor_acquisition() -> None:
    report = build_binding(ROOT)
    assert report["modelability"] == "PARTIAL_PREDICTOR_EVIDENCE_REQUIRED"
    assert report["predictor_inventory"]["missing"]
