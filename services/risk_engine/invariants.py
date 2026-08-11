"""M13 canonical hard-limit and fail-closed readiness boundary."""

from __future__ import annotations

from dataclasses import dataclass, fields
from decimal import Decimal
from enum import StrEnum

from .policy import RiskPolicy


class RiskInvariantError(ValueError):
    pass


class GateState(StrEnum):
    READY_FOR_DETERMINISTIC_EVALUATION = "READY_FOR_DETERMINISTIC_EVALUATION"
    REJECT = "REJECT"


CANONICAL_POLICY = RiskPolicy()


def validate_policy_is_not_weaker(policy: RiskPolicy) -> None:
    """Reject any attempted increase above the human-owned hard maxima."""
    if policy.bankroll != CANONICAL_POLICY.bankroll:
        raise RiskInvariantError("bankroll policy cannot be adapted")
    if policy.protected_reserve < CANONICAL_POLICY.protected_reserve:
        raise RiskInvariantError("protected reserve cannot be reduced")
    maximum_fields = (
        "active_capital",
        "aggregate_open_risk_limit",
        "market_loss_limit",
        "related_event_risk_limit",
        "daily_loss_stop",
        "weekly_loss_stop",
        "monthly_loss_stop",
        "total_drawdown_stop",
    )
    for name in maximum_fields:
        if getattr(policy, name) > getattr(CANONICAL_POLICY, name):
            raise RiskInvariantError(f"hard maximum cannot increase: {name}")


@dataclass(frozen=True, slots=True)
class NewRiskReadiness:
    market_active: bool = False
    market_not_provisional: bool = False
    rules_valid: bool = False
    semantics_valid: bool = False
    settlement_source_known: bool = False
    payout_supported: bool = False
    price_structure_supported: bool = False
    quantity_structure_supported: bool = False
    sources_healthy: bool = False
    data_fresh: bool = False
    book_valid: bool = False
    account_fresh: bool = False
    no_material_gap: bool = False
    api_compatible: bool = False
    model_valid: bool = False
    calibrator_valid: bool = False
    frozen_forecast: bool = False
    uncertainty_valid: bool = False
    forecast_non_abstaining: bool = False
    forecast_rules_match: bool = False
    price_valid: bool = False
    quantity_valid: bool = False
    after_cost_value_valid: bool = False
    fee_verified: bool = False
    slippage_acceptable: bool = False
    exposure_known: bool = False
    reserve_preserved: bool = False
    loss_limits_clear: bool = False
    reconciled: bool = False
    no_unknown_orders: bool = False
    kill_switches_clear: bool = False
    compliance_clear: bool = False
    order_group_valid: bool = False
    client_id_valid: bool = False
    authorization_available: bool = False


@dataclass(frozen=True, slots=True)
class GateReadinessDecision:
    state: GateState
    rejection_reasons: tuple[str, ...]
    production_write_authorized: bool = False


def evaluate_readiness(readiness: NewRiskReadiness) -> GateReadinessDecision:
    missing = tuple(
        field.name for field in fields(readiness) if getattr(readiness, field.name) is not True
    )
    state = GateState.READY_FOR_DETERMINISTIC_EVALUATION if not missing else GateState.REJECT
    # Readiness is only input validation. M13 does not create execution authority.
    return GateReadinessDecision(state, missing, False)


def decimal_risk_values(policy: RiskPolicy = CANONICAL_POLICY) -> tuple[Decimal, ...]:
    names = (
        "bankroll",
        "protected_reserve",
        "active_capital",
        "aggregate_open_risk_limit",
        "market_loss_limit",
        "related_event_risk_limit",
        "daily_loss_stop",
        "weekly_loss_stop",
        "monthly_loss_stop",
        "total_drawdown_stop",
    )
    return tuple(getattr(policy, name) for name in names)
