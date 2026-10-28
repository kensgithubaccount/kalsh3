"""Completely invented values only: no source URLs, real CPI target or I/O."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from experiments.cpi_p12_reuters_provenance_synthetic_v1.probe import (
    InventedEvidence,
    ResearchOnlyDisposition,
    classify_invented_case,
)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"method_frozen": False}, "METHOD_OR_LOCATOR_UNFROZEN"),
        ({"locator_authorized": False}, "METHOD_OR_LOCATOR_UNFROZEN"),
        ({"retrieval_and_retention_rights": False}, "ACCESS_RIGHTS_UNVERIFIED"),
        ({"status_code": 403}, "SOURCE_UNAVAILABLE"),
        ({"status_code": 429}, "SOURCE_UNAVAILABLE"),
        ({"status_code": 503}, "SOURCE_UNAVAILABLE"),
        ({"status_code": 302}, "TRANSPORT_INVALID"),
        ({"transport_complete": False}, "TRANSPORT_INVALID"),
        ({"receipt_committed": False}, "PERSISTENCE_UNCONFIRMED"),
        ({"deterministic_selection_before_exposure": False}, "RETROSPECTIVE_SELECTION"),
        ({"candidate_count": 0}, "NO_CANDIDATE"),
        ({"candidate_count": 2}, "CANDIDATE_CONFLICT"),
        ({"has_conflicting_candidates": True}, "CANDIDATE_CONFLICT"),
        ({"issuer_attribution_verified": False}, "ISSUER_ATTRIBUTION_UNPROVED"),
        ({"claimed_first_publication_at": None}, "PUBLICATION_TIME_UNPROVED"),
        ({"first_publication_independently_verified": False}, "PUBLICATION_TIME_UNPROVED"),
        ({"revision_history_verified": False}, "REVISION_HISTORY_UNPROVED"),
        ({"corroborator_same_wire": True}, "DUPLICATED_WIRE_NOT_INDEPENDENT"),
        ({"corroborator_independent": False}, "CORROBORATION_UNPROVED"),
        ({"clock_error_ms": -1}, "INVALID_SYNTHETIC_INPUT"),
        ({"clock_error_ms": 3_600_001}, "INVALID_SYNTHETIC_INPUT"),
        ({"candidate_count": -1}, "INVALID_SYNTHETIC_INPUT"),
        ({"candidate_count": 101}, "INVALID_SYNTHETIC_INPUT"),
        ({"status_code": True}, "INVALID_SYNTHETIC_INPUT"),
        ({"corroboration_required": 1}, "INVALID_SYNTHETIC_INPUT"),
        ({"marker": "REAL_SOURCE"}, "INVALID_SYNTHETIC_INPUT"),
        ({"request_started_at": datetime(2026, 1, 2, 11, 0)}, "INVALID_SYNTHETIC_INPUT"),
        ({"claimed_first_publication_at": datetime(2026, 1, 2)}, "INVALID_SYNTHETIC_INPUT"),
        (
            {"response_finished_at": datetime(2026, 1, 2, 10, 59, tzinfo=UTC)},
            "LATE_OR_UNPROVABLE_TIMING",
        ),
        (
            {"response_finished_at": datetime(2026, 1, 2, 11, 5, tzinfo=UTC)},
            "LATE_OR_UNPROVABLE_TIMING",
        ),
        (
            {"claimed_first_publication_at": datetime(2026, 1, 2, 11, 5, tzinfo=UTC)},
            "LATE_OR_UNPROVABLE_PUBLICATION",
        ),
        ({"clock_error_ms": 240_000}, "LATE_OR_UNPROVABLE_TIMING"),
    ],
)
def test_negative_invented_variants_never_authorize(
    changes: dict[str, object], reason: str
) -> None:
    outcome = classify_invented_case(replace(InventedEvidence(), **changes))
    assert outcome.reason == reason
    _assert_no_authority(outcome)


def _assert_no_authority(outcome: ResearchOnlyDisposition) -> None:
    assert outcome.scientific_disposition == "INCONCLUSIVE"
    assert outcome.research_only is True
    assert outcome.scientific_evidence_authorized is False
    assert outcome.phase1_economic_test_authorized is False
    assert outcome.reuters_forecast_authorized is False
    assert outcome.p12_frozen is False
    assert outcome.production_influence == "0"
    assert outcome.promotion_authority == "NONE"
    assert outcome.trading_authority == "NONE"


def test_consistent_invented_case_remains_scientifically_inconclusive() -> None:
    outcome = classify_invented_case(InventedEvidence())
    assert outcome.reason == "CONSISTENT_INVENTED_CASE_ONLY"
    _assert_no_authority(outcome)


def test_source_count_cannot_be_used_to_skip_required_corroboration() -> None:
    x = InventedEvidence(corroborator_same_wire=True, corroborator_independent=True)
    assert classify_invented_case(x).reason == "DUPLICATED_WIRE_NOT_INDEPENDENT"
    _assert_no_authority(classify_invented_case(x))


def test_before_cutoff_must_include_clock_uncertainty() -> None:
    x = InventedEvidence(response_finished_at=datetime(2026, 1, 2, 11, 4, tzinfo=UTC))
    assert classify_invented_case(x).reason == "CONSISTENT_INVENTED_CASE_ONLY"
    y = replace(x, clock_error_ms=int(timedelta(minutes=1).total_seconds() * 1000))
    assert classify_invented_case(y).reason == "LATE_OR_UNPROVABLE_TIMING"


def test_absent_independent_witness_only_irrelevant_when_not_required() -> None:
    x = InventedEvidence(corroboration_required=False, corroborator_independent=False)
    assert classify_invented_case(x).reason == "CONSISTENT_INVENTED_CASE_ONLY"
    _assert_no_authority(classify_invented_case(x))


def test_fail_closed_on_wrong_python_type() -> None:
    assert (
        classify_invented_case(None).reason == "INVALID_SYNTHETIC_INPUT"  # type: ignore[arg-type]
    )
    assert classify_invented_case({}).reason == "INVALID_SYNTHETIC_INPUT"  # type: ignore[arg-type]
