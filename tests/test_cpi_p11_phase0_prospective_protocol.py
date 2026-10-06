import json
from decimal import Decimal
from pathlib import Path

import pytest

from services.forecasting.cpi_p11_prospective_protocol import (
    APPROVED_REUTERS_HOSTS,
    BLS_RELEASE_AT_UTC,
    CANONICAL_BASE_SHA,
    CANONICAL_BASE_TREE,
    EXPECTED_SIBLING_COUNT,
    KALSHI_CLOSE_AT_UTC,
    MIN_PRIMARY_ELIGIBLE_SIBLINGS,
    TARGET_EVENT,
    TARGET_REFERENCE_MONTH,
    ProspectiveDecision,
    build_phase0_protocol,
    canonical_bytes,
    classify_directional_call,
    classify_event_result,
)

ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT / "docs/reviews/artifacts/cpi-p11-phase0-prospective-protocol/spec.json"


def test_target_and_canonical_identity_are_frozen() -> None:
    assert CANONICAL_BASE_SHA == "1dca2f488e950dae86f8a57af556e000b7e3cca5"
    assert CANONICAL_BASE_TREE == "2165899e6770c9b363c2a5c38465d5578e93c911"
    assert TARGET_EVENT == "KXCPI-26SEP"
    assert TARGET_REFERENCE_MONTH == "2026-09"
    assert EXPECTED_SIBLING_COUNT == 14
    assert BLS_RELEASE_AT_UTC == "2026-10-14T12:30:00Z"
    assert KALSHI_CLOSE_AT_UTC == "2026-10-14T12:25:00Z"


def test_frozen_artifact_regenerates_exactly() -> None:
    regenerated = build_phase0_protocol()
    frozen = json.loads(FROZEN.read_bytes())
    assert regenerated == frozen
    assert regenerated["spec_digest_sha256"]
    assert canonical_bytes(regenerated) == canonical_bytes(frozen)


def test_protocol_keeps_predictor_unobserved_and_economics_out() -> None:
    spec = build_phase0_protocol()
    raw = json.dumps(spec, sort_keys=True).casefold()
    assert "reuters forecast > sibling threshold" in raw
    assert '"phase1_economic_test_authorized": false' in raw
    assert '"orders": false' in raw
    assert '"capital_allocation": false' in raw
    assert "reuters_value" not in raw
    assert "forecast_value" not in raw
    assert "pnl" in raw  # only in the explicit prohibition


def test_market_observation_reuses_fixed_historical_convention() -> None:
    spec = build_phase0_protocol()
    market = spec["market_observation"]
    assert market["interval_minutes"] == 60
    assert market["primary_price"] == "yes_ask"
    assert "strictly before" in market["selection_rule"]
    assert market["acquisition_window_utc"] == [
        "2026-10-14T12:05:00Z",
        "2026-10-14T12:20:00Z",
    ]


def test_reuters_evidence_bar_is_not_weakened() -> None:
    spec = build_phase0_protocol()
    evidence = spec["reuters_evidence"]
    assert tuple(evidence["approved_host_set"]) == APPROVED_REUTERS_HOSTS
    assert evidence["no_single_host_promotion"] is True
    assert any(
        "at least 2 independently operated hosts" in row for row in evidence["pass_requirements"]
    )
    assert evidence["publication_must_precede_each_scored_sibling_close"] is True


def test_primary_denominator_is_common_and_event_level() -> None:
    spec = build_phase0_protocol()
    scoring = spec["scoring"]
    assert scoring["unit_of_independence"] == "event"
    assert scoring["sibling_rows_independent"] is False
    assert scoring["minimum_primary_eligible_siblings"] == 4
    assert MIN_PRIMARY_ELIGIBLE_SIBLINGS == 4
    assert "same eligible siblings" in scoring["common_denominator_rule"]


def test_directional_calls_follow_frozen_strict_rules() -> None:
    assert classify_directional_call(
        reuters_value=Decimal("0.3"),
        kalshi_yes_ask=Decimal("0.7"),
        threshold=Decimal("0.2"),
    ) == (1, 1)
    assert classify_directional_call(
        reuters_value=Decimal("0.3"),
        kalshi_yes_ask=Decimal("0.2"),
        threshold=Decimal("0.3"),
    ) == (0, 0)
    assert classify_directional_call(
        reuters_value=Decimal("0.4"),
        kalshi_yes_ask=Decimal("0.5"),
        threshold=Decimal("0.3"),
    ) == (1, None)


def test_event_gate_is_frozen_before_target_result() -> None:
    assert (
        classify_event_result(reuters_correct=4, kalshi_correct=3, eligible_siblings=6)
        is ProspectiveDecision.SUPPORT
    )
    assert (
        classify_event_result(reuters_correct=2, kalshi_correct=4, eligible_siblings=6)
        is ProspectiveDecision.CONTRADICTION
    )
    assert (
        classify_event_result(reuters_correct=4, kalshi_correct=4, eligible_siblings=6)
        is ProspectiveDecision.TIE
    )
    assert (
        classify_event_result(reuters_correct=3, kalshi_correct=2, eligible_siblings=3)
        is ProspectiveDecision.INSUFFICIENT
    )


def test_event_gate_rejects_impossible_counts() -> None:
    with pytest.raises(ValueError):
        classify_event_result(reuters_correct=7, kalshi_correct=1, eligible_siblings=6)


def test_support_still_has_no_promotion_or_economic_authority() -> None:
    gate = build_phase0_protocol()["decision_gate"]
    assert gate["promotion_authority"] == "NONE"
    assert gate["phase1_economic_test_authorized"] is False
    assert "multi-event prospective extension" in gate["support_next_action"]
