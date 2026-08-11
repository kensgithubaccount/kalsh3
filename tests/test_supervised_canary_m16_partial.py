from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from services.supervised_canary.domain import CanaryPreview, CanaryReadiness, CanaryState

NOW = datetime(2026, 8, 10, tzinfo=UTC)


def test_m16_preview_is_one_contract_short_lived_and_nonexecuting() -> None:
    preview = CanaryPreview(
        "preview-1",
        "candidate-1",
        "risk-1",
        "intent-1",
        "MARKET",
        "YES",
        Decimal(".42"),
        Decimal(1),
        Decimal(".43"),
        NOW,
        NOW + timedelta(minutes=2),
    )
    assert preview.state == CanaryState.DRAFT and preview.content_hash
    with pytest.raises(ValueError, match="one contract"):
        CanaryPreview(
            "preview-2",
            "candidate",
            "risk",
            "intent",
            "MARKET",
            "YES",
            Decimal(".42"),
            Decimal(2),
            Decimal(".86"),
            NOW,
            NOW + timedelta(minutes=1),
        )


def test_even_all_fixture_readiness_cannot_arm_or_execute() -> None:
    readiness = CanaryReadiness(*(True for _ in range(9)))
    assert readiness.evaluate() == CanaryState.APPROVAL_UNAVAILABLE
    assert "ARMED" not in tuple(state.value for state in CanaryState)
    assert "EXECUTE" not in tuple(state.value for state in CanaryState)
