"""M9-E1 regressions: leave-one-out extrema are a diagnostic, not an interval.

Every promotion path in M9 must fail closed until a prespecified inferential
method that handles event dependence and repeated looks exists.  Production
influence stays exactly 0 and no trading path is reachable from here.
"""

import dataclasses
from decimal import Decimal

import pytest

from services.learning.domain import LearningError
from services.learning.evaluation import (
    MINIMUM_UNIQUE_SETTLED_EVENTS,
    EventContribution,
    PerformanceInterval,
    paired_event_interval,
)
from services.learning.governance import (
    GovernanceProposal,
    ProposalType,
    compare_challenger,
)


def contribution(event_id: str, delta: str) -> EventContribution:
    return EventContribution(event_id, Decimal(".18"), Decimal(".18") + Decimal(delta), 0, 1, 1)


def sample(count: int, delta: str, prefix: str = "e") -> tuple[EventContribution, ...]:
    return tuple(contribution(f"{prefix}{index}", delta) for index in range(count))


def promotion(
    interval: PerformanceInterval | None,
    count: int,
    manifest: tuple[str, ...],
    effect: str = ".01",
) -> GovernanceProposal:
    return GovernanceProposal(
        "proposal",
        ProposalType.PROMOTION_PROPOSAL,
        "model",
        "CHALLENGER",
        "RESEARCH_CHAMPION",
        interval,
        count,
        "same-events",
        "rationale",
        event_manifest=manifest,
        incremental_effect=Decimal(effect),
    )


def test_mixed_sign_events_cannot_promote_on_leave_one_out_extrema() -> None:
    """27 at +0.1 and 23 at -0.1: extrema exclude zero, evidence must not."""
    events = sample(27, ".1", "pos") + sample(23, "-.1", "neg")
    interval = paired_event_interval(events)

    # The defect: both extrema sit strictly above zero.
    assert interval.sensitivity_low > 0 and interval.sensitivity_high > 0
    # The repair: that is a stability diagnostic and proves nothing.
    assert interval.evidence == "INCONCLUSIVE"
    assert interval.method == "LEAVE_ONE_EVENT_OUT_SENSITIVITY"
    assert interval.inferential_method is None

    manifest = tuple(event.event_id for event in events)
    with pytest.raises(LearningError, match="promotion evidence threshold not met"):
        promotion(interval, 50, manifest)
    assert not compare_challenger(manifest, manifest, Decimal(".18"), Decimal(".17"), interval)


def test_fifty_duplicate_event_ids_cannot_satisfy_the_fifty_event_gate() -> None:
    duplicates = tuple(contribution("same-event", ".1") for _ in range(50))
    with pytest.raises(LearningError, match="duplicate event ids"):
        paired_event_interval(duplicates)

    repeated = ("same-event",) * 50
    with pytest.raises(LearningError, match="duplicate event ids"):
        compare_challenger(
            repeated,
            repeated,
            Decimal(".18"),
            Decimal(".17"),
            paired_event_interval(sample(50, ".1")),
        )
    with pytest.raises(LearningError, match="duplicate event ids"):
        promotion(paired_event_interval(sample(50, ".1")), 50, repeated)


def test_fifty_all_negative_events_cannot_promote() -> None:
    events = sample(50, "-.1")
    interval = paired_event_interval(events)

    # Uniformly negative: extrema exclude zero from below.
    assert interval.point < 0 and interval.sensitivity_high < 0
    assert interval.evidence == "INCONCLUSIVE"
    assert not interval.positive_direction

    manifest = tuple(event.event_id for event in events)
    with pytest.raises(LearningError, match="positive incremental effect"):
        promotion(interval, 50, manifest, effect="-.1")
    # A positive supplied effect cannot launder a negative observed direction.
    with pytest.raises(LearningError, match="positive incremental effect"):
        promotion(interval, 50, manifest, effect=".01")
    assert not compare_challenger(manifest, manifest, Decimal(".18"), Decimal(".17"), interval)


def test_manifest_count_mismatch_fails_closed() -> None:
    events = sample(50, ".1")
    interval = paired_event_interval(events)
    manifest = tuple(event.event_id for event in events)

    # A supplied count is never trusted on its own.
    with pytest.raises(LearningError, match="does not match the event manifest"):
        promotion(interval, 50, manifest[:3])
    with pytest.raises(LearningError, match="does not match the event manifest"):
        promotion(interval, 50, ())
    with pytest.raises(LearningError, match="does not match the event manifest"):
        promotion(interval, 3, manifest)

    # The interval itself must cover the same manifest.
    narrow = paired_event_interval(sample(60, ".1", "other"))
    with pytest.raises(LearningError, match="interval does not match the event manifest"):
        promotion(narrow, 50, manifest)
    with pytest.raises(LearningError, match="interval does not match"):
        compare_challenger(manifest[:10], manifest[:10], Decimal(".18"), Decimal(".17"), interval)


def test_negative_interval_or_proposal_cannot_promote() -> None:
    events = sample(50, ".1")
    manifest = tuple(event.event_id for event in events)
    interval = paired_event_interval(events)

    for effect in ("-.5", "-.0001", "0"):
        with pytest.raises(LearningError, match="positive incremental effect"):
            promotion(interval, 50, manifest, effect=effect)

    # Concentration: the point estimate is positive, but one event carries it and
    # dropping that event turns the mean negative.  Direction is not established.
    flipping = (*sample(49, "-.001"), contribution("winner", ".06"))
    unstable = paired_event_interval(flipping)
    assert unstable.point > 0 and unstable.sensitivity_low < 0
    assert not unstable.positive_direction
    with pytest.raises(LearningError, match="positive incremental effect"):
        promotion(unstable, 50, tuple(event.event_id for event in flipping), effect=".01")


def test_evidence_stronger_than_inconclusive_is_unconstructible() -> None:
    """No accepted inferential method exists at this checkpoint: STRONGER_EVIDENCE
    is not reachable from any current caller, named method or not."""
    with pytest.raises(LearningError, match="no prespecified inferential method"):
        PerformanceInterval(Decimal(".01"), Decimal(".01"), Decimal(".01"), 50, "STRONGER_EVIDENCE")
    assert paired_event_interval(sample(50, ".1")).inferential_method is None


def test_caller_naming_an_inferential_method_confers_no_authority() -> None:
    """Exact independent-review counterexample: a caller cannot manufacture
    promotion authority merely by naming an inferential_method string."""
    with pytest.raises(LearningError, match="no prespecified inferential method"):
        PerformanceInterval(
            point=Decimal(".01"),
            sensitivity_low=Decimal(".01"),
            sensitivity_high=Decimal(".01"),
            event_count=50,
            evidence="STRONGER_EVIDENCE",
            meets_event_floor=True,
            inferential_method="arbitrary-caller-name",
        )


@pytest.mark.parametrize(
    "inferential_method",
    [None, "arbitrary-caller-name", "hypothetical-prespecified-v0", ""],
)
def test_no_inferential_method_name_unlocks_stronger_evidence(
    inferential_method: str | None,
) -> None:
    """No caller-supplied inferential_method value -- absent, plausible-looking,
    or empty -- can produce a valid STRONGER_EVIDENCE PerformanceInterval."""
    with pytest.raises(LearningError, match="no prespecified inferential method"):
        PerformanceInterval(
            Decimal(".01"),
            Decimal(".01"),
            Decimal(".01"),
            50,
            "STRONGER_EVIDENCE",
            meets_event_floor=True,
            inferential_method=inferential_method,
        )


def test_inconclusive_interval_rejects_any_inferential_method_name() -> None:
    """inferential_method must stay None even when evidence is legitimately
    INCONCLUSIVE: naming one is never harmless, since it is meaningless."""
    with pytest.raises(LearningError, match="inferential_method must remain unset"):
        PerformanceInterval(
            Decimal(".01"),
            Decimal(".01"),
            Decimal(".01"),
            50,
            "INCONCLUSIVE",
            meets_event_floor=True,
            inferential_method="arbitrary-caller-name",
        )


def test_fifty_id_caller_authored_manifest_cannot_produce_a_promotion_proposal() -> None:
    """A caller-authored 50-id event_manifest, on its own, cannot manufacture
    promotion evidence authority -- the manifest is descriptive only."""
    manifest = tuple(f"caller-invented-{i}" for i in range(50))
    interval = paired_event_interval(sample(50, ".1"))
    assert interval.event_count == 50
    with pytest.raises(LearningError, match="promotion evidence threshold not met"):
        promotion(interval, 50, manifest)


def test_compare_challenger_cannot_return_true_from_fabricated_stronger_evidence() -> None:
    """compare_challenger must reject fabricated stronger evidence rather than
    trust it, and can never return True under current M9 evidence."""
    events = sample(50, ".1")
    manifest = tuple(event.event_id for event in events)
    interval = paired_event_interval(events)
    assert interval.positive_direction and interval.meets_event_floor
    # Even a strictly better challenger score cannot promote: evidence is
    # INCONCLUSIVE, and no interval carrying STRONGER_EVIDENCE is constructible.
    assert not compare_challenger(manifest, manifest, Decimal(".18"), Decimal(".01"), interval)


def test_fifty_uniformly_positive_events_stay_inconclusive_with_no_promotion_authority() -> None:
    """Legitimate, uniformly positive evidence still yields no promotion
    authority: INCONCLUSIVE by construction, not by any defect in the inputs."""
    events = sample(50, ".1")
    interval = paired_event_interval(events)

    assert interval.positive_direction
    assert interval.meets_event_floor
    assert interval.evidence == "INCONCLUSIVE"

    manifest = tuple(event.event_id for event in events)
    with pytest.raises(LearningError, match="promotion evidence threshold not met"):
        promotion(interval, 50, manifest)
    assert not compare_challenger(manifest, manifest, Decimal(".18"), Decimal(".01"), interval)


def test_fifty_unique_settled_event_floor_is_preserved_and_cannot_be_lowered() -> None:
    assert MINIMUM_UNIQUE_SETTLED_EVENTS == 50
    for lowered in (1, 2, 49):
        with pytest.raises(LearningError, match="cannot be lowered below 50"):
            paired_event_interval(sample(50, ".1"), minimum=lowered)
    # Raising the floor stays allowed.
    assert not paired_event_interval(sample(50, ".1"), minimum=100).meets_event_floor
    assert paired_event_interval(sample(50, ".1")).meets_event_floor

    events = sample(49, ".1")
    with pytest.raises(LearningError, match="promotion evidence threshold not met"):
        promotion(
            paired_event_interval(events),
            49,
            tuple(event.event_id for event in events),
        )


def test_existing_abstention_and_inconclusive_behaviour_stays_deterministic() -> None:
    with pytest.raises(LearningError, match="no settled events"):
        paired_event_interval(())

    small = paired_event_interval(sample(7, ".01"))
    assert small.evidence == "INCONCLUSIVE" and not small.meets_event_floor

    # Repeated evaluation of the same events is byte-identical.
    events = sample(60, ".01")
    assert paired_event_interval(events) == paired_event_interval(events)
    assert paired_event_interval(events) == paired_event_interval(tuple(reversed(events)))

    # Non-promotion proposals are untouched by the promotion gate.
    for proposal_type in (
        ProposalType.DEMOTION_PROPOSAL,
        ProposalType.QUARANTINE_PROPOSAL,
    ):
        assert (
            GovernanceProposal(
                "p",
                proposal_type,
                "model",
                "RESEARCH_CHAMPION",
                "QUARANTINED",
                None,
                0,
                "same-events",
                "drift",
            ).production_influence
            == 0
        )


def test_evaluation_artifacts_remain_immutable_and_zero_influence() -> None:
    interval = paired_event_interval(sample(50, ".1"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        interval.evidence = "STRONGER_EVIDENCE"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        interval.event_count = 9_999  # type: ignore[misc]

    events = sample(50, ".1")
    proposal = GovernanceProposal(
        "p",
        ProposalType.DEMOTION_PROPOSAL,
        "model",
        "RESEARCH_CHAMPION",
        "RESEARCH",
        interval,
        50,
        "same-events",
        "rationale",
        event_manifest=tuple(event.event_id for event in events),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        proposal.unique_settled_events = 51  # type: ignore[misc]
    assert proposal.production_influence == 0 and proposal.human_approval_required

    with pytest.raises(LearningError, match="human-gated with zero influence"):
        GovernanceProposal(
            "p",
            ProposalType.DEMOTION_PROPOSAL,
            "model",
            "RESEARCH_CHAMPION",
            "RESEARCH",
            interval,
            50,
            "same-events",
            "rationale",
            production_influence=Decimal("0.01"),
        )
