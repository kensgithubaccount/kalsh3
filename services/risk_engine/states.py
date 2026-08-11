"""Loss windows, kill states, compliance, halt, and product-state precedence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

from .domain import ComplianceState, KillCategory, KillLevel, ReconciliationStatus, RiskReason
from .policy import RiskPolicy


class RiskProductState(StrEnum):
    LEARNING = "LEARNING"
    PAUSED = "PAUSED"
    NEEDS_ATTENTION = "NEEDS ATTENTION"
    HALTED = "HALTED"


@dataclass(frozen=True, slots=True)
class LossWindowState:
    version: str
    as_of: datetime
    risk_date: date
    week_start: date
    month_start: date
    daily_loss: Decimal
    weekly_loss: Decimal
    monthly_loss: Decimal
    drawdown: Decimal
    daily_triggered_at: datetime | None
    weekly_review_required: bool
    monthly_review_required: bool
    experiment_halt_required: bool
    reasons: tuple[RiskReason, ...]


def _loss(pnl: Decimal) -> Decimal:
    return max(Decimal(0), -pnl)


def evaluate_loss_windows(
    *,
    now: datetime,
    realized_daily_pnl: Decimal,
    realized_weekly_pnl: Decimal,
    realized_monthly_pnl: Decimal,
    drawdown: Decimal,
    policy: RiskPolicy,
    prior_weekly_review: bool = False,
    prior_monthly_review: bool = False,
    prior_experiment_halt: bool = False,
    prior_daily_triggered_at: datetime | None = None,
    prior_risk_date: date | None = None,
) -> LossWindowState:
    if now.tzinfo is None:
        raise ValueError("risk clock must be timezone-aware")
    local = now.astimezone(ZoneInfo(policy.risk_timezone))
    daily, weekly, monthly = (
        _loss(realized_daily_pnl),
        _loss(realized_weekly_pnl),
        _loss(realized_monthly_pnl),
    )
    reasons = []
    daily_at = prior_daily_triggered_at if prior_risk_date == local.date() else None
    if daily >= policy.daily_loss_stop:
        reasons.append(RiskReason.DAILY_LOSS_STOP)
        daily_at = daily_at or now.astimezone(UTC)
    weekly_review = prior_weekly_review or weekly >= policy.weekly_loss_stop
    if weekly_review:
        reasons.append(RiskReason.WEEKLY_LOSS_STOP)
    monthly_review = prior_monthly_review or monthly >= policy.monthly_loss_stop
    if monthly_review:
        reasons.append(RiskReason.MONTHLY_LOSS_STOP)
    experiment_halt = prior_experiment_halt or drawdown >= policy.total_drawdown_stop
    if experiment_halt:
        reasons.append(RiskReason.EXPERIMENT_DRAWDOWN_STOP)
    week_start = local.date() - timedelta(days=local.weekday())
    return LossWindowState(
        f"loss:{now.astimezone(UTC).isoformat()}",
        now.astimezone(UTC),
        local.date(),
        week_start,
        local.date().replace(day=1),
        daily,
        weekly,
        monthly,
        drawdown,
        daily_at,
        weekly_review,
        monthly_review,
        experiment_halt,
        tuple(reasons),
    )


@dataclass(frozen=True, slots=True)
class KillState:
    category: KillCategory
    level: KillLevel
    reason: str
    changed_at: datetime


@dataclass(frozen=True, slots=True)
class SafetyState:
    global_halt: bool
    global_halt_reason: str | None
    compliance: ComplianceState
    reconciliation: ReconciliationStatus
    kills: tuple[KillState, ...]
    losses: LossWindowState


def resolve_product_state(value: SafetyState) -> RiskProductState:
    """Precedence: experiment/global halt > compliance > portfolio > long stops > daily > normal."""
    if value.global_halt or value.losses.experiment_halt_required:
        return RiskProductState.HALTED
    if value.compliance != ComplianceState.CLEAR:
        return RiskProductState.HALTED
    if value.reconciliation != ReconciliationStatus.RECONCILED:
        return RiskProductState.NEEDS_ATTENTION
    if any(kill.level == KillLevel.KILLED for kill in value.kills):
        return RiskProductState.PAUSED
    if value.losses.monthly_review_required or value.losses.weekly_review_required:
        return RiskProductState.PAUSED
    if RiskReason.DAILY_LOSS_STOP in value.losses.reasons:
        return RiskProductState.PAUSED
    return RiskProductState.LEARNING
