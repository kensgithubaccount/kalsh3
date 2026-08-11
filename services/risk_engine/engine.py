"""Pure deterministic all-reason M13 risk evaluation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from .domain import (
    ComplianceState,
    KillCategory,
    KillLevel,
    PortfolioRiskSnapshot,
    ReconciliationStatus,
    RequiredOrderGroupPolicy,
    RiskDecision,
    RiskDecisionState,
    RiskIntent,
    RiskReason,
)
from .invariants import NewRiskReadiness, validate_policy_is_not_weaker
from .ledger import ExposureProjection
from .policy import RiskPolicy
from .states import SafetyState


@dataclass(frozen=True, slots=True)
class RiskEvaluationContext:
    market_data_version: str
    loss_state_version: str
    compliance_state_version: str
    kill_state_version: str
    expected_rules_hash: str
    readiness: NewRiskReadiness
    safety: SafetyState
    order_group: RequiredOrderGroupPolicy
    client_order_id_unique: bool
    client_order_id_namespace_valid: bool
    conflicting_bot_order: bool
    authorization_service_available: bool
    exposure_match_tolerance: Decimal = Decimal("0.01")


READINESS_REASONS = {
    "market_active": RiskReason.MARKET_INACTIVE,
    "market_not_provisional": RiskReason.MARKET_PROVISIONAL,
    "rules_valid": RiskReason.RULES_MISMATCH,
    "semantics_valid": RiskReason.CONTRACT_AMBIGUOUS,
    "settlement_source_known": RiskReason.SETTLEMENT_UNKNOWN,
    "payout_supported": RiskReason.RISK_UNCOMPUTABLE,
    "price_structure_supported": RiskReason.PRICE_STRUCTURE_UNSUPPORTED,
    "quantity_structure_supported": RiskReason.QUANTITY_STRUCTURE_UNSUPPORTED,
    "sources_healthy": RiskReason.SOURCE_UNHEALTHY,
    "data_fresh": RiskReason.DATA_STALE,
    "book_valid": RiskReason.BOOK_GAP,
    "account_fresh": RiskReason.ACCOUNT_STALE,
    "no_material_gap": RiskReason.BOOK_GAP,
    "api_compatible": RiskReason.API_COMPATIBILITY_FAILURE,
    "model_valid": RiskReason.MODEL_NOT_ELIGIBLE,
    "calibrator_valid": RiskReason.CALIBRATOR_NOT_ELIGIBLE,
    "frozen_forecast": RiskReason.MODEL_NOT_ELIGIBLE,
    "uncertainty_valid": RiskReason.MODEL_NOT_ELIGIBLE,
    "forecast_non_abstaining": RiskReason.FORECAST_ABSTAINED,
    "forecast_rules_match": RiskReason.RULES_MISMATCH,
    "price_valid": RiskReason.RISK_UNCOMPUTABLE,
    "quantity_valid": RiskReason.RISK_UNCOMPUTABLE,
    "after_cost_value_valid": RiskReason.AFTER_COST_GATE_FAILED,
    "fee_verified": RiskReason.FEE_UNVERIFIED,
    "slippage_acceptable": RiskReason.AFTER_COST_GATE_FAILED,
    "exposure_known": RiskReason.RISK_UNCOMPUTABLE,
    "reserve_preserved": RiskReason.RESERVE_STATE_UNCERTAIN,
    "loss_limits_clear": RiskReason.DAILY_LOSS_STOP,
    "reconciled": RiskReason.ACCOUNT_NOT_RECONCILED,
    "no_unknown_orders": RiskReason.UNKNOWN_ORDER,
    "kill_switches_clear": RiskReason.PORTFOLIO_KILL,
    "compliance_clear": RiskReason.COMPLIANCE_HOLD,
    "order_group_valid": RiskReason.ORDER_GROUP_UNAVAILABLE,
    "client_id_valid": RiskReason.DUPLICATE_CLIENT_ORDER_ID,
    "authorization_available": RiskReason.AUTHORIZATION_UNAVAILABLE,
}


def _financial_missing(intent: RiskIntent, snapshot: PortfolioRiskSnapshot) -> bool:
    required = (
        intent.price,
        intent.quantity,
        intent.maximum_expected_fee,
        intent.maximum_expected_cash_commitment,
        intent.maximum_loss_if_filled,
        snapshot.cash,
        snapshot.account_equity,
        snapshot.active_capital_available,
        snapshot.current_market_risk,
        snapshot.current_event_risk,
        snapshot.current_aggregate_risk,
        snapshot.resting_order_potential_risk,
        snapshot.realized_daily_pnl,
        snapshot.realized_weekly_pnl,
        snapshot.realized_monthly_pnl,
        snapshot.experiment_equity,
        snapshot.experiment_high_water_mark,
        snapshot.experiment_drawdown,
    )
    return any(value is None for value in required)


def evaluate_risk(
    *,
    intent: RiskIntent,
    snapshot: PortfolioRiskSnapshot,
    projection: ExposureProjection,
    policy: RiskPolicy,
    context: RiskEvaluationContext,
    now: datetime,
) -> RiskDecision:
    validate_policy_is_not_weaker(policy)
    reasons: set[RiskReason] = set()
    if intent.content_hash == "" or intent.rules_hash != context.expected_rules_hash:
        reasons.add(RiskReason.RULES_MISMATCH)
    if intent.subaccount != 0:
        reasons.add(RiskReason.SUBACCOUNT_INVALID)
    if _financial_missing(intent, snapshot):
        reasons.add(RiskReason.RISK_UNCOMPUTABLE)
    for name, reason in READINESS_REASONS.items():
        if getattr(context.readiness, name) is not True:
            reasons.add(reason)
    if not snapshot.account_fresh:
        reasons.add(RiskReason.ACCOUNT_STALE)
    if snapshot.reconciliation_status != ReconciliationStatus.RECONCILED:
        reasons.add(RiskReason.ACCOUNT_NOT_RECONCILED)
    if (
        snapshot.unknown_orders
        or context.safety.reconciliation == ReconciliationStatus.UNKNOWN_ORDER
    ):
        reasons.add(RiskReason.UNKNOWN_ORDER)
    if (
        snapshot.external_positions
        or context.safety.reconciliation == ReconciliationStatus.UNKNOWN_POSITION
    ):
        reasons.add(RiskReason.UNKNOWN_POSITION)
    if snapshot.external_orders:
        reasons.add(RiskReason.EXTERNAL_ACTIVITY_UNRESOLVED)
    exposure_pairs = (
        (snapshot.exchange_market_exposure, snapshot.independently_calculated_market_exposure),
        (snapshot.exchange_event_exposure, snapshot.independently_calculated_event_exposure),
    )
    if any(
        exchange is None
        or calculated is None
        or abs(exchange - calculated) > context.exposure_match_tolerance
        for exchange, calculated in exposure_pairs
    ):
        reasons.add(RiskReason.EXPOSURE_RECONCILIATION_MISMATCH)
    if intent.maximum_expected_cash_commitment is not None:
        if snapshot.cash is None or snapshot.cash < intent.maximum_expected_cash_commitment:
            reasons.add(RiskReason.CASH_INSUFFICIENT)
        if (
            snapshot.active_capital_available is None
            or intent.maximum_expected_cash_commitment > snapshot.active_capital_available
        ):
            reasons.add(RiskReason.ACTIVE_POOL_EXCEEDED)
    if snapshot.account_equity is None or snapshot.account_equity < policy.protected_reserve:
        reasons.add(RiskReason.RESERVE_VIOLATION)
    if projection.market_risk > policy.market_loss_limit:
        reasons.add(RiskReason.MARKET_RISK_EXCEEDED)
    if projection.event_risk > policy.related_event_risk_limit:
        reasons.add(RiskReason.EVENT_RISK_EXCEEDED)
    if projection.aggregate_risk > policy.aggregate_open_risk_limit:
        reasons.add(RiskReason.AGGREGATE_RISK_EXCEEDED)
    if intent.reduce_only and not projection.risk_reducing:
        reasons.add(RiskReason.NOT_RISK_REDUCING)
    namespace_valid = bool(re.fullmatch(r"kalsh3-v1-[A-Za-z0-9-]{8,}", intent.client_order_id))
    if (
        not context.client_order_id_unique
        or not context.client_order_id_namespace_valid
        or not namespace_valid
    ):
        reasons.add(RiskReason.DUPLICATE_CLIENT_ORDER_ID)
    if context.conflicting_bot_order:
        reasons.add(RiskReason.SELF_TRADE_CONFLICT)
    if context.order_group.required and (
        not context.order_group.group_active
        or not context.order_group.auto_cancel_ready
        or context.order_group.expected_subaccount != 0
    ):
        reasons.add(RiskReason.ORDER_GROUP_UNAVAILABLE)
    if not intent.cancel_order_on_pause or not intent.self_trade_prevention:
        reasons.add(RiskReason.RISK_UNCOMPUTABLE)
    for kill in context.safety.kills:
        if kill.level == KillLevel.KILLED:
            reasons.add(
                {
                    KillCategory.STRATEGY: RiskReason.STRATEGY_KILL,
                    KillCategory.DATA: RiskReason.DATA_KILL,
                    KillCategory.PORTFOLIO: RiskReason.PORTFOLIO_KILL,
                    KillCategory.CREDENTIAL: RiskReason.CREDENTIAL_KILL,
                }[kill.category]
            )
    reasons.update(context.safety.losses.reasons)
    if context.safety.compliance != ComplianceState.CLEAR:
        reasons.add(RiskReason.COMPLIANCE_HOLD)
    if context.safety.global_halt:
        reasons.add(RiskReason.GLOBAL_HALT)
    if not context.authorization_service_available:
        reasons.add(RiskReason.AUTHORIZATION_UNAVAILABLE)

    hard_halt = bool({RiskReason.EXPERIMENT_DRAWDOWN_STOP, RiskReason.GLOBAL_HALT} & reasons)
    pause = bool(
        {
            RiskReason.DAILY_LOSS_STOP,
            RiskReason.WEEKLY_LOSS_STOP,
            RiskReason.MONTHLY_LOSS_STOP,
            RiskReason.STRATEGY_KILL,
            RiskReason.DATA_KILL,
            RiskReason.PORTFOLIO_KILL,
            RiskReason.CREDENTIAL_KILL,
        }
        & reasons
    )
    risk_reduction_permitted_reasons = {
        RiskReason.DAILY_LOSS_STOP,
        RiskReason.WEEKLY_LOSS_STOP,
        RiskReason.MONTHLY_LOSS_STOP,
    }
    can_reduce = projection.risk_reducing and reasons <= risk_reduction_permitted_reasons
    if hard_halt:
        state = RiskDecisionState.HALT
    elif can_reduce:
        state = RiskDecisionState.PASS_NEXT_GATE
        reasons.clear()
    elif pause:
        state = RiskDecisionState.PAUSE
    elif reasons:
        state = RiskDecisionState.REJECT
    else:
        state = RiskDecisionState.PASS_NEXT_GATE
    return RiskDecision.freeze(
        state=state,
        intent_hash=intent.content_hash,
        risk_policy_version=policy.version,
        portfolio_state_hash=snapshot.content_hash,
        reconciliation_version=snapshot.reconciliation_version,
        rules_version=intent.rules_version,
        market_data_version=context.market_data_version,
        loss_state_version=context.loss_state_version,
        compliance_state_version=context.compliance_state_version,
        kill_state_version=context.kill_state_version,
        decided_at=now,
        expires_at=now + timedelta(seconds=5),
        reasons=tuple(sorted(reasons)),
        display_result="RISK CHECK PASSED"
        if state == RiskDecisionState.PASS_NEXT_GATE
        else "RISK CHECK FAILED",
        production_write_authorized=False,
    )
