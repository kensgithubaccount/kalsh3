"""Immutable, effective-dated capital limits from the authoritative specification."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    policy_id: str = "m13-canonical-risk-policy"
    version: str = "1"
    effective_at: datetime = datetime(2026, 1, 1, tzinfo=UTC)
    predecessor: str | None = None
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
    risk_timezone: str = "America/New_York"
    code_sha: str = "M13"
    content_hash: str = ""

    def __post_init__(self) -> None:
        money = (
            self.bankroll,
            self.protected_reserve,
            self.active_capital,
            self.aggregate_open_risk_limit,
            self.market_loss_limit,
            self.related_event_risk_limit,
            self.daily_loss_stop,
            self.weekly_loss_stop,
            self.monthly_loss_stop,
            self.total_drawdown_stop,
        )
        if any(
            not isinstance(value, Decimal) or not value.is_finite() or value < 0 for value in money
        ):
            raise ValueError("risk policy money must be finite non-negative Decimal")
        ZoneInfo(self.risk_timezone)
        payload = {
            "policy_id": self.policy_id,
            "version": self.version,
            "effective_at": self.effective_at.isoformat(),
            "predecessor": self.predecessor,
            "money": tuple(str(value) for value in money),
            "risk_timezone": self.risk_timezone,
            "code_sha": self.code_sha,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        object.__setattr__(self, "content_hash", digest)

    @property
    def starting_bankroll(self) -> Decimal:
        return self.bankroll

    @property
    def active_capital_limit(self) -> Decimal:
        return self.active_capital

    @property
    def per_market_risk_limit(self) -> Decimal:
        return self.market_loss_limit

    @property
    def experiment_drawdown_stop(self) -> Decimal:
        return self.total_drawdown_stop


def select_risk_policy(policies: tuple[RiskPolicy, ...], at: datetime) -> RiskPolicy:
    eligible = [policy for policy in policies if policy.effective_at <= at]
    if not eligible:
        raise ValueError("no risk policy was effective at evaluation time")
    latest = max(policy.effective_at for policy in eligible)
    matches = [policy for policy in eligible if policy.effective_at == latest]
    if len(matches) != 1:
        raise ValueError("ambiguous risk policy history")
    return matches[0]


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
    from .invariants import validate_policy_is_not_weaker

    validate_policy_is_not_weaker(limits)
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
