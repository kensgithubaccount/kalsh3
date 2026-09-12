"""Acceptance specification for the real public GDP composition boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from services.production_gdp_strategy import one_decision


def test_public_boundary_is_research_only_and_fee_incomplete() -> None:
    result = one_decision.run_one_research_decision()
    assert result.research_only is True
    assert result.production_influence == Decimal("0")
    assert result.classification is one_decision.DecisionClass.EVIDENCE_INCOMPLETE
    assert result.entry_gate_passed is False
    assert result.entry_fee is None
    assert result.all_in_debit is None


def test_decision_timestamp_cannot_predate_pipeline_completion() -> None:
    sample = one_decision._ClockSample(datetime.now(UTC), 1)
    receipt = one_decision._incomplete_receipt(sample)
    object.__setattr__(receipt, "decision_timestamp", datetime(2020, 1, 1, tzinfo=UTC))
    with pytest.raises(one_decision.DecisionError):
        one_decision.validate_decision_receipt(receipt)


@pytest.mark.parametrize(
    "field,value",
    [("selected_threshold", Decimal("1.0")), ("schedule_id", "mutated-schedule")],
)
def test_public_material_identity_mutations_fail_closed(field: str, value: object) -> None:
    receipt = one_decision.run_one_research_decision()
    object.__setattr__(receipt, field, value)
    with pytest.raises(one_decision.DecisionError):
        one_decision.validate_decision_receipt(receipt)


def test_real_public_entrypoint_is_the_acceptance_target() -> None:
    assert one_decision.run_one_research_decision.__name__ == "run_one_research_decision"
