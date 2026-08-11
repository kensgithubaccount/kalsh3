from dataclasses import replace
from decimal import Decimal

import pytest

from services.risk_engine.invariants import (
    CANONICAL_POLICY,
    GateState,
    NewRiskReadiness,
    RiskInvariantError,
    decimal_risk_values,
    evaluate_readiness,
    validate_policy_is_not_weaker,
)


def test_canonical_financial_limits_are_exact_decimals() -> None:
    assert CANONICAL_POLICY.bankroll == Decimal("1000")
    assert CANONICAL_POLICY.protected_reserve == Decimal("700")
    assert CANONICAL_POLICY.active_capital == Decimal("300")
    assert CANONICAL_POLICY.aggregate_open_risk_limit == Decimal("100")
    assert CANONICAL_POLICY.market_loss_limit == Decimal("10")
    assert CANONICAL_POLICY.related_event_risk_limit == Decimal("25")
    assert CANONICAL_POLICY.daily_loss_stop == Decimal("20")
    assert CANONICAL_POLICY.weekly_loss_stop == Decimal("50")
    assert CANONICAL_POLICY.monthly_loss_stop == Decimal("100")
    assert CANONICAL_POLICY.total_drawdown_stop == Decimal("200")
    assert all(isinstance(value, Decimal) for value in decimal_risk_values())


def test_adaptive_or_caller_policy_cannot_weaken_hard_limits() -> None:
    validate_policy_is_not_weaker(CANONICAL_POLICY)
    validate_policy_is_not_weaker(replace(CANONICAL_POLICY, market_loss_limit=Decimal("5")))
    with pytest.raises(RiskInvariantError):
        validate_policy_is_not_weaker(
            replace(CANONICAL_POLICY, aggregate_open_risk_limit=Decimal("101"))
        )
    with pytest.raises(RiskInvariantError):
        validate_policy_is_not_weaker(replace(CANONICAL_POLICY, protected_reserve=Decimal("699")))


def test_readiness_lists_every_missing_gate_and_never_authorizes_write() -> None:
    rejected = evaluate_readiness(NewRiskReadiness(data_fresh=True, reconciled=True))
    assert rejected.state == GateState.REJECT
    assert "rules_valid" in rejected.rejection_reasons
    assert "data_fresh" not in rejected.rejection_reasons
    assert not rejected.production_write_authorized
    ready = evaluate_readiness(
        NewRiskReadiness(**{name: True for name in NewRiskReadiness.__dataclass_fields__})
    )
    assert ready.state == GateState.READY_FOR_DETERMINISTIC_EVALUATION
    assert not ready.production_write_authorized
