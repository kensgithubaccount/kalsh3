import json
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest

from services.forecasting.cpi_p10a_binding import (
    P7_TIMING_ARTIFACT,
    EventRow,
    _calibration_bins,
    _load_p7_timing,
    _predicate,
    build_binding,
)

ROOT = Path(__file__).resolve().parents[1]


def test_canonical_overlap_is_derived_and_event_weighted() -> None:
    report = build_binding(ROOT)
    assert report["p9a_events"] == 60
    assert report["p9a_siblings"] == 474
    assert report["bound_events"] == 42
    assert report["usable_events"] == 42
    assert report["quote_evidence_vs_executable"]["quote_counts"]["two_sided"] == 200
    assert report["quote_evidence_vs_executable"]["ask_crossing"]["rows"] >= 200
    assert report["crossing_price_brier_diagnostic"] >= 0
    assert report["crossing_price_log_loss_diagnostic"] >= 0


def test_malformed_identity_fails_closed() -> None:
    from services.forecasting.cpi_p10a_binding import _event_month

    with pytest.raises(ValueError):
        _event_month("not-a-cpi-event")


def test_placeholder_rule_has_no_ticker_fallback() -> None:
    with pytest.raises(ValueError):
        _predicate({"rules_primary": "If the CPI increases by more than || percent ||%"})


def test_coherent_timing_receipt_mutation_fails_fixed_raw_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.forecasting import cpi_p10a_binding as module

    value = json.loads((ROOT / P7_TIMING_ARTIFACT).read_text())
    value["events"][0]["publication_local"] = "2025-08-12T08:31:00-04:00"
    value["events"][0]["publication_utc"] = "2025-08-12T12:31:00Z"
    value["events"][0]["source_url"] = "https://www.bls.gov/news.release/archives/cpi_08122025.htm"
    value["events"][0]["artifact_sha256"] = "0" * 64
    path = tmp_path / "timing.json"
    path.write_text(json.dumps(value))
    monkeypatch.setattr(module, "P7_TIMING_ARTIFACT", path)
    with pytest.raises(ValueError, match="raw SHA-256"):
        _load_p7_timing(ROOT)


def test_timing_receipt_rejects_duplicate_nan_and_extra_event(tmp_path: Path) -> None:
    raw = (ROOT / P7_TIMING_ARTIFACT).read_bytes()
    with pytest.raises(ValueError, match="duplicate JSON keys"):
        from services.forecasting.cpi_p10a_binding import _strict_json

        _strict_json(b'{"x": 1, "x": 2}')
    with pytest.raises(ValueError, match="non-standard"):
        _strict_json(b'{"x": NaN}')
    value = json.loads(raw)
    value["events"].append(dict(value["events"][0], **{"reference_month": "2024-01"}))
    path = tmp_path / "extra.json"
    path.write_text(json.dumps(value))
    from services.forecasting import cpi_p10a_binding as module

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(module, "P7_TIMING_ARTIFACT", path)
    monkeypatch.setattr(module, "P7_TIMING_RAW_SHA256", sha256(path.read_bytes()).hexdigest())
    try:
        with pytest.raises(ValueError, match="exactly three events"):
            _load_p7_timing(ROOT)
    finally:
        monkeypatch.undo()


def test_coherent_mutation_cannot_rebind_approved_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.forecasting import cpi_p10a_binding as module

    value = json.loads((ROOT / P7_TIMING_ARTIFACT).read_text())
    event = value["events"][0]
    event.update(
        publication_local="2025-08-12T08:31:00-04:00",
        publication_utc="2025-08-12T12:31:00Z",
        source_url="https://example.invalid/cpi",
        artifact_sha256="0" * 64,
    )
    path = tmp_path / "coherent.json"
    path.write_text(json.dumps(value))
    monkeypatch.setattr(module, "P7_TIMING_ARTIFACT", path)
    monkeypatch.setattr(module, "P7_TIMING_RAW_SHA256", sha256(path.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match="approved semantic mapping"):
        _load_p7_timing(ROOT)


def test_threshold_binding_requires_equal_decimal_value() -> None:
    from services.forecasting.cpi_p10a_binding import _predicate

    predicate, month = _predicate({"rules_primary": "If CPI is more than 0.20% in June 2022"})
    assert month == (2022, 6)
    assert predicate == Decimal("0.20")
    assert predicate != Decimal("0.21")


def test_quote_layers_and_denominators_are_explicit() -> None:
    report = build_binding(ROOT)
    layers = report["quote_evidence_vs_executable"]
    assert layers["ask_crossing"]["depth_or_fill_authority"] is False
    assert layers["two_sided_bid_ask"]["ask"]["sibling_rows"] == 200
    assert layers["midpoint"]["executable"] is False
    assert layers["boundary_one_sided_missing"]["retained_in_denominator"] is True
    assert layers["quote_counts"]["unusable"] == report["bound_rows"] - report["ask_usable_rows"]


def test_calibration_bins_before_event_aggregation() -> None:
    def row(ticker: str, probability: str, outcome: int) -> EventRow:
        instant = datetime(2020, 1, 1, tzinfo=UTC)
        return EventRow(
            "CPI-20JAN",
            "kalshi:CPI-20JAN",
            (2020, 1),
            instant,
            instant,
            ticker,
            Decimal("0.2"),
            outcome,
            Decimal(probability),
            0,
            "FRESH",
            "e",
            "r",
            None,
        )

    rows = [row("a", "0.05", 0), row("b", "0.95", 1)]
    bins = _calibration_bins(rows)
    assert bins[0]["events"] == 1 and bins[0]["sibling_rows"] == 1
    assert bins[4]["events"] == 1 and bins[4]["sibling_rows"] == 1
    assert bins[2]["events"] == 0


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
