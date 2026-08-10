"""Immutable capital limits from the authoritative specification."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    bankroll: Decimal = Decimal("1000")
    protected_reserve: Decimal = Decimal("700")
    active_capital: Decimal = Decimal("300")
    aggregate_open_risk_limit: Decimal = Decimal("100")
    market_loss_limit: Decimal = Decimal("10")
    related_event_risk_limit: Decimal = Decimal("25")
    daily_loss_stop: Decimal = Decimal("20")
    weekly_loss_stop: Decimal = Decimal("50")
    monthly_loss_stop: Decimal = Decimal("100")
    total_drawdown_stop: Decimal = Decimal("200")


@dataclass(frozen=True, slots=True)
class RiskRequest:
    incremental_market_loss: Decimal
    current_market_risk: Decimal
    current_event_risk: Decimal
    current_open_risk: Decimal
    daily_loss: Decimal = Decimal("0")
    weekly_loss: Decimal = Decimal("0")
    monthly_loss: Decimal = Decimal("0")
    total_drawdown: Decimal = Decimal("0")
    data_fresh: bool = False
    reconciled: bool = False
    globally_halted: bool = True


@dataclass(frozen=True, slots=True)
class RiskDecision:
    authorized: bool
    reason: str


def authorize_new_risk(request: RiskRequest, policy: RiskPolicy | None = None) -> RiskDecision:
    """Fail closed and authorize only requests within every hard maximum."""
    limits = policy or RiskPolicy()
    if request.globally_halted:
        return RiskDecision(False, "global halt is active")
    if not request.data_fresh:
        return RiskDecision(False, "market data is stale or unverified")
    if not request.reconciled:
        return RiskDecision(False, "portfolio is not reconciled")
    if request.incremental_market_loss <= 0:
        return RiskDecision(False, "incremental loss must be positive")
    checks = (
        (
            request.current_market_risk + request.incremental_market_loss,
            limits.market_loss_limit,
            "market loss cap",
        ),
        (
            request.current_event_risk + request.incremental_market_loss,
            limits.related_event_risk_limit,
            "related-event cap",
        ),
        (
            request.current_open_risk + request.incremental_market_loss,
            limits.aggregate_open_risk_limit,
            "aggregate open-risk cap",
        ),
        (request.daily_loss, limits.daily_loss_stop, "daily loss stop"),
        (request.weekly_loss, limits.weekly_loss_stop, "weekly loss stop"),
        (request.monthly_loss, limits.monthly_loss_stop, "monthly loss stop"),
        (request.total_drawdown, limits.total_drawdown_stop, "total drawdown stop"),
    )
    for observed, maximum, reason in checks:
        if observed >= maximum:
            return RiskDecision(False, reason)
    return RiskDecision(True, "all deterministic risk checks passed")
