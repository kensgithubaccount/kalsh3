from pathlib import Path

import pytest

from services.forecasting.cpi_p10a_binding import build_binding

ROOT = Path(__file__).resolve().parents[1]


def test_canonical_overlap_is_derived_and_event_weighted() -> None:
    report = build_binding(ROOT)
    assert report["p9a_events"] == 60
    assert report["p9a_siblings"] == 474
    assert report["bound_events"] == 46
    assert report["usable_events"] == 46
    assert report["quote_usable_rows"] == 218
    assert report["brier_score"] >= 0
    assert report["log_loss"] >= 0


def test_malformed_identity_fails_closed() -> None:
    from services.forecasting.cpi_p10a_binding import _event_month

    with pytest.raises(ValueError):
        _event_month("not-a-cpi-event")


def test_report_requires_predictor_acquisition() -> None:
    report = build_binding(ROOT)
    assert report["modelability"] == "PARTIAL_PREDICTOR_EVIDENCE_REQUIRED"
    assert report["predictor_inventory"]["missing"]
