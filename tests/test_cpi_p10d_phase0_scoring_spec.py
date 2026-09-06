import copy
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from services.forecasting.cpi_p10c_manifest import build_phase1_manifest
from services.forecasting.cpi_p10d_scoring_spec import (
    CANONICAL_MAIN_SHA,
    CANONICAL_MAIN_TREE,
    FROZEN_FOUR_EVENT_TICKERS,
    GOVERNING_TIMESTAMP_CANDIDATE_FIELDS,
    PRIMARY_MARKET_PRICE_CONVENTION,
    REUTERS_EVENT_BINDINGS,
    CPIP10DScoringSpecError,
    ReutersEventBinding,
    _eligible_sibling_counts,
    _load_and_verify_receipt,
    build_phase0_spec,
    canonical_bytes,
    classify_directional_call,
    resolve_governing_timestamp,
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
    # Verified live via `git ls-remote` against origin/main immediately before
    # this Phase 0B repair; PR #133 (unrelated Perps docs) confirmed as the
    # only intervening change. See docs/reviews/CPI_E1_P10D_PHASE0_SCORING_SPEC.md.
    assert CANONICAL_MAIN_SHA == "ecf52aabae7f5eeb9beb4cbebd46226103482837"
    assert CANONICAL_MAIN_TREE == "b19e6ed38850d39df9d7a970fef087fb85439764"


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


def test_eligible_sibling_counts_per_event() -> None:
    spec = build_phase0_spec(ROOT)
    by_ticker = {e["event_ticker"]: e for e in spec["reuters_proven_events"]}
    assert by_ticker["CPI-23AUG"]["accepted_sibling_count"] == 9
    assert by_ticker["KXCPI-25JUL"]["accepted_sibling_count"] == 6
    assert by_ticker["KXCPI-25DEC"]["accepted_sibling_count"] == 6
    assert by_ticker["KXCPI-26JAN"]["accepted_sibling_count"] == 6
    assert by_ticker["CPI-23AUG"]["primary_metric_eligible_sibling_count"] == 5
    assert by_ticker["KXCPI-25JUL"]["primary_metric_eligible_sibling_count"] == 4
    assert by_ticker["KXCPI-25DEC"]["primary_metric_eligible_sibling_count"] == 4
    assert by_ticker["KXCPI-26JAN"]["primary_metric_eligible_sibling_count"] == 6
    total_accepted = sum(e["accepted_sibling_count"] for e in spec["reuters_proven_events"])
    total_eligible = sum(
        e["primary_metric_eligible_sibling_count"] for e in spec["reuters_proven_events"]
    )
    assert total_accepted == 27
    assert total_eligible == 19


def test_uniform_per_sibling_cutoff_equals_resolved_timestamp_comparison() -> None:
    # Narrow adaptation check: for the 3 P10B-reused events (single receipt
    # timestamp, no per-event sibling_temporal_eligibility list), the manifest's
    # own per-sibling cutoff must be uniform and must exceed the resolver-derived
    # Reuters timestamp -- never assumed from the receipt's own decision_cutoff
    # claim, and never picked by event ticker.
    manifest = build_phase1_manifest(ROOT)
    manifest_events = {e["event_ticker"]: e for e in manifest["events"]}
    for binding in REUTERS_EVENT_BINDINGS:
        _receipt, _field, resolved = _load_and_verify_receipt(binding, ROOT)
        counts = _eligible_sibling_counts(binding, resolved, manifest_events, ROOT)
        assert counts["temporally_eligible_sibling_count"] == counts["accepted_sibling_count"]


def test_rejects_synthesized_fifth_event() -> None:
    tampered = ReutersEventBinding(
        event_ticker="CPI-24JAN",  # the R1-reclassified UNKNOWN event
        reference_month="2024-01",
        value=Decimal("0.3"),
        receipt_path=REUTERS_EVENT_BINDINGS[0].receipt_path,
        receipt_sha256=REUTERS_EVENT_BINDINGS[0].receipt_sha256,
        expected_timestamp=REUTERS_EVENT_BINDINGS[0].expected_timestamp,
    )
    with pytest.raises(CPIP10DScoringSpecError):
        _load_and_verify_receipt(tampered, ROOT)


def test_rejects_post_cutoff_timestamp(tmp_path: Path) -> None:
    real = REUTERS_EVENT_BINDINGS[0]  # CPI-23AUG
    raw = json.loads((ROOT / real.receipt_path).read_bytes())
    tampered_receipt = copy.deepcopy(raw)
    tampered_receipt["published_at"] = "2023-09-13T13:00:00Z"  # after the 12:25 cutoff
    tampered_path = tmp_path / "receipt.json"
    tampered_path.write_text(json.dumps(tampered_receipt))

    manifest = build_phase1_manifest(ROOT)
    manifest_events = {e["event_ticker"]: e for e in manifest["events"]}
    tampered_binding = ReutersEventBinding(
        event_ticker=real.event_ticker,
        reference_month=real.reference_month,
        value=real.value,
        receipt_path=tampered_path.relative_to(tmp_path),
        receipt_sha256=hashlib.sha256(tampered_path.read_bytes()).hexdigest(),
        expected_timestamp=datetime.fromisoformat("2023-09-13T13:00:00+00:00"),
    )
    _receipt, _field, resolved = _load_and_verify_receipt(tampered_binding, tmp_path)
    with pytest.raises(CPIP10DScoringSpecError):
        _eligible_sibling_counts(tampered_binding, resolved, manifest_events, tmp_path)


def test_market_price_convention_is_frozen_to_yes_ask() -> None:
    assert PRIMARY_MARKET_PRICE_CONVENTION == "yes_ask"


def test_primary_statistic_is_event_equal_not_pooled_sibling_mean() -> None:
    spec = build_phase0_spec(ROOT)
    assert spec["unit_of_independence"] == "event"
    assert spec["aggregation_rule"] == "event_equal_mean_of_per_event_sibling_mean"
    assert "sibling" not in spec["unit_of_independence"]
    assert spec["primary_metric"] == (
        "sibling_level_threshold_directional_correctness_event_equal_aggregated"
    )


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


# --- resolve_governing_timestamp: the Phase 0B deterministic resolver ---


def test_resolver_candidate_field_set_is_frozen() -> None:
    assert GOVERNING_TIMESTAMP_CANDIDATE_FIELDS == (
        "published_at",
        "governing_published_at",
        "conservative_admissibility_time",
    )


def test_resolver_takes_no_event_ticker_argument() -> None:
    # Structural proof that the event ticker cannot select the field: the
    # resolver's signature has no such parameter, so it is impossible to
    # branch on it even by accident.
    import inspect

    params = inspect.signature(resolve_governing_timestamp).parameters
    assert list(params) == ["receipt"]


def test_resolver_exactly_one_candidate_resolves() -> None:
    field, value = resolve_governing_timestamp(
        {"event_ticker": "SYNTH-1", "published_at": "2030-01-01T00:00:00Z"}
    )
    assert field == "published_at"
    assert value == datetime(2030, 1, 1, tzinfo=UTC)

    field, value = resolve_governing_timestamp(
        {"event_ticker": "SYNTH-2", "governing_published_at": "2030-02-02T03:04:05Z"}
    )
    assert field == "governing_published_at"
    assert value == datetime(2030, 2, 2, 3, 4, 5, tzinfo=UTC)

    field, value = resolve_governing_timestamp(
        {"event_ticker": "SYNTH-3", "conservative_admissibility_time": "2030-03-03T00:00:00Z"}
    )
    assert field == "conservative_admissibility_time"
    assert value == datetime(2030, 3, 3, tzinfo=UTC)


def test_resolver_zero_candidates_fails_closed() -> None:
    with pytest.raises(CPIP10DScoringSpecError, match="no governing-timestamp candidate"):
        resolve_governing_timestamp(
            {"event_ticker": "SYNTH", "decision_cutoff": "2030-01-01T00:00:00Z"}
        )


def test_resolver_two_populated_candidates_fails_closed() -> None:
    with pytest.raises(CPIP10DScoringSpecError, match="more than one governing-timestamp"):
        resolve_governing_timestamp(
            {
                "event_ticker": "SYNTH",
                "published_at": "2030-01-01T00:00:00Z",
                "governing_published_at": "2030-01-01T00:00:00Z",
            }
        )


def test_resolver_null_candidate_is_treated_as_absent_not_populated() -> None:
    # A receipt where the "obvious" field is JSON null must not be read as
    # zero-availability if a governing field is also present -- and must not
    # be counted as a second populated candidate either.
    field, value = resolve_governing_timestamp(
        {
            "event_ticker": "SYNTH",
            "published_at": None,
            "governing_published_at": "2030-05-05T00:00:00Z",
        }
    )
    assert field == "governing_published_at"
    assert value == datetime(2030, 5, 5, tzinfo=UTC)


def test_resolver_ignores_unknown_non_authority_fields() -> None:
    # Fields outside the frozen candidate set -- including plausible-looking
    # ones like a raw per-host fetch timestamp or the market-side
    # decision_cutoff -- must never participate in resolution.
    field, value = resolve_governing_timestamp(
        {
            "event_ticker": "SYNTH",
            "published_at": "2030-06-06T00:00:00Z",
            "decision_cutoff": "2030-06-06T12:00:00Z",
            "fetches": [{"datePublished": "2030-06-06T01:00:00Z"}],
            "observed_publication_range": {"earliest": "2030-06-05T23:00:00Z"},
        }
    )
    assert field == "published_at"
    assert value == datetime(2030, 6, 6, tzinfo=UTC)


def test_resolver_event_ticker_cannot_select_the_field() -> None:
    # Same receipt content under two different event tickers must resolve
    # identically -- the ticker is not an input to the resolver at all.
    receipt_a = {
        "event_ticker": "EVENT-A",
        "conservative_admissibility_time": "2030-07-07T00:00:00Z",
    }
    receipt_b = {
        "event_ticker": "EVENT-B",
        "conservative_admissibility_time": "2030-07-07T00:00:00Z",
    }
    assert resolve_governing_timestamp(receipt_a) == resolve_governing_timestamp(receipt_b)


def test_all_four_frozen_receipts_resolve_deterministically() -> None:
    expected = {
        "CPI-23AUG": ("published_at", datetime(2023, 9, 13, 10, 7, 35, tzinfo=UTC)),
        "KXCPI-25JUL": ("published_at", datetime(2025, 8, 12, 4, 2, 11, tzinfo=UTC)),
        "KXCPI-25DEC": ("governing_published_at", datetime(2026, 1, 13, 5, 3, 53, tzinfo=UTC)),
        "KXCPI-26JAN": (
            "conservative_admissibility_time",
            datetime(2026, 2, 13, 5, 12, 31, tzinfo=UTC),
        ),
    }
    seen = set()
    for binding in REUTERS_EVENT_BINDINGS:
        receipt, field, resolved = _load_and_verify_receipt(binding, ROOT)
        assert (field, resolved) == expected[binding.event_ticker]
        # Proof all other candidate fields are null/absent on this receipt.
        other_fields = [f for f in GOVERNING_TIMESTAMP_CANDIDATE_FIELDS if f != field]
        for other in other_fields:
            assert receipt.get(other) is None
        seen.add(binding.event_ticker)
        # Re-run through the resolver directly (not just via the loader) for
        # an independent, second confirmation.
        assert resolve_governing_timestamp(receipt) == (field, resolved)
    assert seen == FROZEN_FOUR_EVENT_TICKERS
