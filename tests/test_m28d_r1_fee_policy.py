from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

import services.production_weather_strategy.historical_economics as economics_module
from services.opportunity_engine.fees import (
    FeeEstimateQuality,
    FeeType,
    current_event_formula_policy,
)
from services.production_weather_strategy.historical_economics import (
    HistoricalEconomicsEvidenceError,
    HistoricalFeePolicyEvidence,
)

CHECKPOINT = datetime(2026, 8, 23, 3, tzinfo=UTC)
JULY_1 = datetime(2026, 7, 1, 3, tzinfo=UTC)


def _policy(multiplier: str = "1"):
    return current_event_formula_policy(
        fee_type=FeeType.QUADRATIC,
        fee_multiplier=Decimal(multiplier),
    )


def _review(policy=None, *, checkpoint: datetime = CHECKPOINT, review_id: str = "reviewed"):
    return economics_module._issue_historical_fee_policy_evidence(
        policy=policy or _policy(),
        checkpoint_at=checkpoint,
        review_evidence_id=review_id,
        _capability=economics_module._HISTORICAL_FEE_POLICY_AUTHORITY_CAPABILITY,
    )


def test_reviewed_policy_covering_checkpoint_succeeds() -> None:
    evidence = _review()
    assert evidence.policy_id == "kalshi-event-fees-2026-07-07-v1"
    assert evidence.effective_at == datetime(2026, 7, 7, tzinfo=UTC)
    assert evidence.fee_estimate_quality is FeeEstimateQuality.DETERMINISTIC_FORMULA_ONLY
    assert evidence.final_exchange_fee_known is False


def test_policy_not_yet_effective_fails() -> None:
    with pytest.raises(HistoricalEconomicsEvidenceError, match="not yet effective"):
        _review(checkpoint=JULY_1)


def test_expired_policy_fails() -> None:
    expired = replace(_policy(), retired_at=CHECKPOINT - timedelta(seconds=1))
    with pytest.raises(HistoricalEconomicsEvidenceError, match="expired"):
        _review(expired)


def test_caller_selected_multiplier_without_reviewed_policy_authority_fails() -> None:
    policy = current_event_formula_policy(
        fee_type=FeeType.QUADRATIC,
        fee_multiplier=Decimal("9"),
    )
    with pytest.raises(HistoricalEconomicsEvidenceError, match="internal reviewed capability"):
        HistoricalFeePolicyEvidence(
            policy=policy,
            checkpoint_at=CHECKPOINT,
            review_evidence_id="caller-asserted",
        )


def test_july_7_policy_cannot_authorize_july_1_history() -> None:
    policy = _policy()
    assert policy.effective_at == datetime(2026, 7, 7, tzinfo=UTC)
    with pytest.raises(HistoricalEconomicsEvidenceError, match="not yet effective"):
        _review(policy, checkpoint=JULY_1)


def test_policy_identity_changes_when_reviewed_fee_semantics_change() -> None:
    baseline = _review(_policy("1"), review_id="same-review")
    changed = _review(_policy("2"), review_id="same-review")
    assert baseline.policy_content_hash != changed.policy_content_hash
    assert baseline.content_hash != changed.content_hash
