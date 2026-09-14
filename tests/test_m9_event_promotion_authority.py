"""Regression coverage for descriptive evidence being mistaken for promotion authority."""

from dataclasses import FrozenInstanceError, asdict, replace
from decimal import Decimal

import pytest

from services.learning.domain import LearningError
from services.learning.evaluation import (
    EventContribution,
    PairedEventSensitivity,
    PerformanceInterval,
    paired_event_interval,
    paired_event_sensitivity,
)
from services.learning.governance import GovernanceProposal, ProposalType, compare_challenger


def sample(count: int = 50, delta: str = ".01") -> tuple[EventContribution, ...]:
    return tuple(
        EventContribution(str(i), Decimal(".2"), Decimal(".2") + Decimal(delta), i, i + 1, None)
        for i in range(count)
    )


def proposal(
    diagnostic: PairedEventSensitivity | PerformanceInterval,
    count: int,
    manifest: str,
    kind: ProposalType = ProposalType.PROMOTION_PROPOSAL,
) -> GovernanceProposal:
    return GovernanceProposal(
        "p", kind, "model", "CHALLENGER", "RESEARCH_CHAMPION", diagnostic, count, manifest, "test"
    )


@pytest.mark.parametrize("conflict", [False, True])
def test_repeated_event_cannot_inflate_fifty_event_floor(conflict: bool) -> None:
    event = sample(1)[0]
    repeated = (event,) * 49 + (replace(event, ablated_brier=Decimal(".4")) if conflict else event,)
    with pytest.raises(LearningError, match="duplicate event"):
        paired_event_sensitivity(repeated)


@pytest.mark.parametrize("minimum", [0, 2, 49, True, Decimal(50)])
def test_minimum_cannot_be_lowered_or_coerced(minimum: int) -> None:
    with pytest.raises(LearningError, match="at least 50"):
        paired_event_interval(sample(), minimum)


@pytest.mark.parametrize("delta", ["-.01", "0", ".01"])
@pytest.mark.parametrize("count", [49, 50, 60])
def test_sensitivity_never_claims_inferential_evidence(count: int, delta: str) -> None:
    diagnostic = paired_event_sensitivity(sample(count, delta))
    assert diagnostic.event_count == count
    assert diagnostic.point == Decimal(delta)
    assert diagnostic.sensitivity_lower == diagnostic.sensitivity_upper == Decimal(delta)
    assert diagnostic.evidence == "INCONCLUSIVE"
    with pytest.raises(LearningError, match="promotion evidence"):
        proposal(diagnostic, count, diagnostic.event_manifest)
    assert not compare_challenger(
        diagnostic.event_ids, diagnostic.event_ids, Decimal(".3"), Decimal(".2"), diagnostic
    )


def test_loo_extrema_are_descriptive_and_single_event_has_no_loo_range() -> None:
    events = sample(3, "0")
    diagnostic = paired_event_sensitivity(
        (
            events[0],
            replace(events[1], ablated_brier=Decimal(".4")),
            replace(events[2], ablated_brier=Decimal(".6")),
        )
    )
    assert diagnostic.point == Decimal(".2")
    assert diagnostic.sensitivity_lower == Decimal(".1")
    assert diagnostic.sensitivity_upper == Decimal(".3")
    single = paired_event_sensitivity(sample(1))
    assert single.sensitivity_lower is single.sensitivity_upper is None
    assert single.reason == "INSUFFICIENT_DISTINCT_EVENTS"
    assert paired_event_sensitivity(sample(), minimum=51).reason == "INSUFFICIENT_DISTINCT_EVENTS"
    assert (
        paired_event_sensitivity(sample(delta="-.01")).reason == "NONPOSITIVE_OBSERVED_CONTRIBUTION"
    )
    assert paired_event_sensitivity(sample()).reason == "NO_ACCEPTED_INFERENCE_METHOD"


@pytest.mark.parametrize(
    "score",
    [Decimal("NaN"), Decimal("sNaN"), Decimal("Infinity"), Decimal("-.1"), Decimal("1.1"), 0.2],
)
@pytest.mark.parametrize("field", ["full_brier", "ablated_brier"])
def test_invalid_paired_scores_rejected(score: Decimal, field: str) -> None:
    with pytest.raises(LearningError, match="Brier scores"):
        paired_event_sensitivity((replace(sample(1)[0], **{field: score}),))


@pytest.mark.parametrize("identity", ["", " "])
def test_missing_event_identity_rejected(identity: str) -> None:
    with pytest.raises(LearningError, match="identity must be nonempty"):
        paired_event_sensitivity((replace(sample(1)[0], event_id=identity),))


def test_empty_input_rejected() -> None:
    with pytest.raises(LearningError, match="no settled events"):
        paired_event_sensitivity(())


def test_manifest_is_order_invariant_and_binds_full_paired_records() -> None:
    events = sample()
    diagnostic = paired_event_sensitivity(events)
    assert paired_event_sensitivity(tuple(reversed(events))) == diagnostic
    assert diagnostic.event_count == len(set(diagnostic.event_ids)) == 50
    variants = (
        replace(events[0], event_id="other"),
        replace(events[0], full_brier=Decimal(".3")),
        replace(events[0], ablated_brier=Decimal(".3")),
        replace(events[0], source_published_ms=20),
        replace(events[0], kalshi_reaction_ms=20),
        replace(events[0], forecast_updated_ms=20),
    )
    for changed in variants:
        assert (
            paired_event_sensitivity((changed, *events[1:])).event_manifest
            != diagnostic.event_manifest
        )


@pytest.mark.parametrize("count,manifest", [(51, None), (50, "caller-supplied-text")])
def test_caller_metadata_cannot_disagree_with_diagnostic(count: int, manifest: str | None) -> None:
    diagnostic = paired_event_sensitivity(sample())
    with pytest.raises(LearningError, match="count or manifest"):
        proposal(diagnostic, count, manifest or diagnostic.event_manifest)


def test_legacy_or_fabricated_stronger_flag_cannot_authorize_any_promotion() -> None:
    legacy = PerformanceInterval(
        Decimal(".1"), Decimal(".1"), Decimal(".1"), 1, "STRONGER_EVIDENCE"
    )
    snapshot = asdict(legacy)
    with pytest.raises(LearningError, match="promotion evidence"):
        proposal(legacy, 50, "arbitrary-manifest")
    assert not compare_challenger(("one",), ("one",), Decimal(".2"), Decimal(".1"), legacy)
    assert asdict(legacy) == snapshot
    with pytest.raises(FrozenInstanceError):
        legacy.evidence = "INCONCLUSIVE"


def test_challenger_requires_matching_unique_content_bound_cohort() -> None:
    diagnostic = paired_event_sensitivity(sample())
    with pytest.raises(LearningError, match="different events"):
        compare_challenger(("a",), ("b",), Decimal(".2"), Decimal(".1"), diagnostic)
    for cohort, error in (
        (("a", "a"), "duplicate"),
        (("a",), "diagnostic records"),
        ((), "nonempty"),
    ):
        with pytest.raises(LearningError, match=error):
            compare_challenger(cohort, cohort, Decimal(".2"), Decimal(".1"), diagnostic)


@pytest.mark.parametrize("kind", [ProposalType.DEMOTION_PROPOSAL, ProposalType.QUARANTINE_PROPOSAL])
def test_nonpromotion_proposals_remain_available_without_new_evidence(kind: ProposalType) -> None:
    diagnostic = paired_event_sensitivity(sample(1))
    result = proposal(diagnostic, 1, diagnostic.event_manifest, kind)
    assert result.production_influence == 0 and result.human_approval_required
