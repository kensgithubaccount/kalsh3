import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

from services.forecasting.cpi_p10c_manifest import build_phase1_manifest
from services.forecasting.cpi_p10d_scoring_spec import (
    CANONICAL_MAIN_SHA,
    CANONICAL_MAIN_TREE,
    FROZEN_FOUR_EVENT_TICKERS,
    PRIMARY_MARKET_PRICE_CONVENTION,
    REUTERS_EVENT_BINDINGS,
    CPIP10DScoringSpecError,
    ReutersEventBinding,
    _eligible_sibling_counts,
    _load_and_verify_receipt,
    build_phase0_spec,
    canonical_bytes,
    classify_directional_call,
)

ROOT = Path(__file__).resolve().parents[1]
FROZEN_SPEC_PATH = ROOT / "docs/reviews/artifacts/cpi-p10d-phase0-scoring-spec/spec.json"


def test_frozen_four_event_roster_is_exact() -> None:
    assert {
        "CPI-23AUG",
        "KXCPI-25JUL",
        "KXCPI-25DEC",
        "KXCPI-26JAN",
    } == FROZEN_FOUR_EVENT_TICKERS
    assert len(REUTERS_EVENT_BINDINGS) == 4


def test_canonical_identity_matches_independently_verified_github_state() -> None:
    # Verified live via `git ls-remote` against origin/main before this spec was
    # written; PR #132 merge confirmed as an ancestor. See docs/reviews/
    # CPI_E1_P10D_PHASE0_SCORING_SPEC.md for the verification transcript.
    assert CANONICAL_MAIN_SHA == "9169160c4583c4a6e353c5eb6c01eb59b816d31c"
    assert CANONICAL_MAIN_TREE == "114569cd2ab1c86f892a529d1c1b6eb24457bff7"


def test_build_computes_no_comparative_score() -> None:
    spec = build_phase0_spec(ROOT)
    assert spec["reuters_vs_kalshi_score_computed"] is False
    assert spec["edge_pnl_fees_computed"] is False
    assert spec["research_only"] is True
    assert spec["production_influence"] == "0"
    for key in spec:
        assert "brier" not in key and "accuracy" not in key and "hit_rate" not in key


def test_deterministic_regeneration_is_byte_identical() -> None:
    first = build_phase0_spec(ROOT)
    second = build_phase0_spec(ROOT)
    assert canonical_bytes(first) == canonical_bytes(second)
    assert first["spec_digest_sha256"] == second["spec_digest_sha256"]


def test_spec_matches_committed_frozen_evidence() -> None:
    regenerated = build_phase0_spec(ROOT)
    frozen_on_disk = json.loads(FROZEN_SPEC_PATH.read_bytes())
    assert regenerated == frozen_on_disk


def test_per_event_timestamp_field_binding_is_not_uniform() -> None:
    # This is the concrete drift this module exists to prevent: a naive
    # single field name silently resolves to None for 2 of 4 receipts.
    fields = {b.event_ticker: b.timestamp_field for b in REUTERS_EVENT_BINDINGS}
    assert fields == {
        "CPI-23AUG": "published_at",
        "KXCPI-25JUL": "published_at",
        "KXCPI-25DEC": "governing_published_at",
        "KXCPI-26JAN": "conservative_admissibility_time",
    }


def test_eligible_sibling_counts_per_event() -> None:
    spec = build_phase0_spec(ROOT)
    by_ticker = {e["event_ticker"]: e for e in spec["reuters_proven_events"]}
    assert by_ticker["CPI-23AUG"]["accepted_sibling_count"] == 9
    assert by_ticker["KXCPI-25JUL"]["accepted_sibling_count"] == 6
    assert by_ticker["KXCPI-25DEC"]["accepted_sibling_count"] == 6
    assert by_ticker["KXCPI-26JAN"]["accepted_sibling_count"] == 6
    total_accepted = sum(e["accepted_sibling_count"] for e in spec["reuters_proven_events"])
    total_eligible = sum(
        e["primary_metric_eligible_sibling_count"] for e in spec["reuters_proven_events"]
    )
    assert total_accepted == 27
    assert total_eligible <= total_accepted


def test_uniform_per_sibling_cutoff_equals_receipt_bound_timestamp_comparison() -> None:
    # Narrow adaptation check: for the 3 P10B-reused events (single receipt
    # timestamp, no per-event sibling_temporal_eligibility list), the manifest's
    # own per-sibling cutoff must be uniform and must exceed the bound Reuters
    # timestamp -- never assumed from the receipt's own decision_cutoff claim.
    manifest = build_phase1_manifest(ROOT)
    manifest_events = {e["event_ticker"]: e for e in manifest["events"]}
    for binding in REUTERS_EVENT_BINDINGS:
        counts = _eligible_sibling_counts(binding, manifest_events, ROOT)
        assert counts["temporally_eligible_sibling_count"] == counts["accepted_sibling_count"]


def test_rejects_synthesized_fifth_event() -> None:
    tampered = ReutersEventBinding(
        event_ticker="CPI-24JAN",  # the R1-reclassified UNKNOWN event
        reference_month="2024-01",
        value=Decimal("0.3"),
        receipt_path=REUTERS_EVENT_BINDINGS[0].receipt_path,
        receipt_sha256=REUTERS_EVENT_BINDINGS[0].receipt_sha256,
        timestamp_field="published_at",
        timestamp_value=REUTERS_EVENT_BINDINGS[0].timestamp_value,
    )
    with pytest.raises(CPIP10DScoringSpecError):
        _load_and_verify_receipt(tampered, ROOT)


def test_rejects_wrong_timestamp_field_name() -> None:
    real = REUTERS_EVENT_BINDINGS[2]  # KXCPI-25DEC, field is governing_published_at
    wrong_field = ReutersEventBinding(
        event_ticker=real.event_ticker,
        reference_month=real.reference_month,
        value=real.value,
        receipt_path=real.receipt_path,
        receipt_sha256=real.receipt_sha256,
        timestamp_field="published_at",  # this receipt's published_at is None
        timestamp_value=real.timestamp_value,
    )
    with pytest.raises(CPIP10DScoringSpecError):
        _load_and_verify_receipt(wrong_field, ROOT)


def test_rejects_post_cutoff_timestamp(tmp_path) -> None:
    real = REUTERS_EVENT_BINDINGS[0]  # CPI-23AUG
    raw = json.loads((ROOT / real.receipt_path).read_bytes())
    tampered_receipt = copy.deepcopy(raw)
    tampered_receipt["published_at"] = "2023-09-13T13:00:00Z"  # after the 12:25 cutoff
    tampered_path = tmp_path / "receipt.json"
    tampered_path.write_text(json.dumps(tampered_receipt))
    manifest = build_phase1_manifest(ROOT)
    manifest_events = {e["event_ticker"]: e for e in manifest["events"]}

    import hashlib

    tampered_binding = ReutersEventBinding(
        event_ticker=real.event_ticker,
        reference_month=real.reference_month,
        value=real.value,
        receipt_path=tampered_path.relative_to(tmp_path),
        receipt_sha256=hashlib.sha256(tampered_path.read_bytes()).hexdigest(),
        timestamp_field="published_at",
        timestamp_value=__import__("datetime").datetime.fromisoformat("2023-09-13T13:00:00+00:00"),
    )
    with pytest.raises(CPIP10DScoringSpecError):
        _eligible_sibling_counts(tampered_binding, manifest_events, tmp_path)


def test_market_price_convention_is_frozen_to_yes_ask() -> None:
    assert PRIMARY_MARKET_PRICE_CONVENTION == "yes_ask"


def test_primary_statistic_is_event_equal_not_pooled_sibling_mean() -> None:
    spec = build_phase0_spec(ROOT)
    assert spec["unit_of_independence"] == "event"
    assert spec["aggregation_rule"] == "event_equal_mean_of_per_event_sibling_mean"
    assert "sibling" not in spec["unit_of_independence"]


def test_small_sample_rule_forbids_live_trading_promotion() -> None:
    spec = build_phase0_spec(ROOT)
    assert "live trading" in spec["small_sample_interpretation_rule"]
    assert "NOT" in spec["small_sample_interpretation_rule"]


# --- classify_directional_call: synthetic fixtures only, never real cohort data ---


def test_directional_call_reuters_above_threshold() -> None:
    result = classify_directional_call(
        reuters_value=Decimal("0.4"), kalshi_yes_ask=Decimal("0.9"), threshold=Decimal("0.2")
    )
    assert result == {"reuters_direction": 1, "kalshi_direction": 1}


def test_directional_call_reuters_equals_threshold_resolves_no() -> None:
    result = classify_directional_call(
        reuters_value=Decimal("0.3"), kalshi_yes_ask=Decimal("0.1"), threshold=Decimal("0.3")
    )
    assert result["reuters_direction"] == 0


def test_directional_call_kalshi_exact_half_is_tie() -> None:
    result = classify_directional_call(
        reuters_value=Decimal("0.1"), kalshi_yes_ask=Decimal("0.5"), threshold=Decimal("0.2")
    )
    assert result["kalshi_direction"] is None


def test_directional_call_disagreement_synthetic() -> None:
    result = classify_directional_call(
        reuters_value=Decimal("0.1"), kalshi_yes_ask=Decimal("0.9"), threshold=Decimal("0.2")
    )
    assert result == {"reuters_direction": 0, "kalshi_direction": 1}
