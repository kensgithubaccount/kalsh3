"""Canonical immutable M13 risk artifacts; none are exchange requests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class RiskDomainError(ValueError):
    pass


def content_hash(value: object) -> str:
    def default(item: object) -> object:
        if isinstance(item, (datetime, Decimal, StrEnum)):
            return str(item)
        if is_dataclass(item) and not isinstance(item, type):
            return asdict(item)
        raise TypeError(f"unsupported risk hash type: {type(item).__name__}")

    payload = json.dumps(value, default=default, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


class EconomicAction(StrEnum):
    BUY_YES_OUTCOME = "BUY_YES_OUTCOME"
    BUY_NO_OUTCOME = "BUY_NO_OUTCOME"
    REDUCE_EXISTING_EXPOSURE = "REDUCE_EXISTING_EXPOSURE"


class Ownership(StrEnum):
    BOT_OWNED = "BOT_OWNED"
    EXTERNAL_KNOWN = "EXTERNAL_KNOWN"
    EXTERNAL_UNKNOWN = "EXTERNAL_UNKNOWN"


class ReconciliationStatus(StrEnum):
    RECONCILED = "RECONCILED"
    STALE = "STALE"
    PARTIAL = "PARTIAL"
    MISMATCH = "MISMATCH"
    UNKNOWN_ORDER = "UNKNOWN_ORDER"
    UNKNOWN_POSITION = "UNKNOWN_POSITION"
    AUTH_FAILURE = "AUTH_FAILURE"
    API_FAILURE = "API_FAILURE"


class KillCategory(StrEnum):
    STRATEGY = "STRATEGY"
    DATA = "DATA"
    PORTFOLIO = "PORTFOLIO"
    CREDENTIAL = "CREDENTIAL"


class KillLevel(StrEnum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    KILLED = "KILLED"


class ComplianceState(StrEnum):
    CLEAR = "CLEAR"
    HOLD = "HOLD"
    UNKNOWN = "UNKNOWN"


class RiskDecisionState(StrEnum):
    PASS_NEXT_GATE = "PASS_NEXT_GATE"
    REJECT = "REJECT"
    PAUSE = "PAUSE"
    HALT = "HALT"


class RiskReason(StrEnum):
    POLICY_MISSING = "POLICY_MISSING"
    ACCOUNT_STALE = "ACCOUNT_STALE"
    ACCOUNT_NOT_RECONCILED = "ACCOUNT_NOT_RECONCILED"
    UNKNOWN_ORDER = "UNKNOWN_ORDER"
    UNKNOWN_POSITION = "UNKNOWN_POSITION"
    EXTERNAL_ACTIVITY_UNRESOLVED = "EXTERNAL_ACTIVITY_UNRESOLVED"
    EXPOSURE_RECONCILIATION_MISMATCH = "EXPOSURE_RECONCILIATION_MISMATCH"
    CASH_INSUFFICIENT = "CASH_INSUFFICIENT"
    RESERVE_VIOLATION = "RESERVE_VIOLATION"
    RESERVE_STATE_UNCERTAIN = "RESERVE_STATE_UNCERTAIN"
    ACTIVE_POOL_EXCEEDED = "ACTIVE_POOL_EXCEEDED"
    AGGREGATE_RISK_EXCEEDED = "AGGREGATE_RISK_EXCEEDED"
    MARKET_RISK_EXCEEDED = "MARKET_RISK_EXCEEDED"
    EVENT_RISK_EXCEEDED = "EVENT_RISK_EXCEEDED"
    DAILY_LOSS_STOP = "DAILY_LOSS_STOP"
    WEEKLY_LOSS_STOP = "WEEKLY_LOSS_STOP"
    MONTHLY_LOSS_STOP = "MONTHLY_LOSS_STOP"
    EXPERIMENT_DRAWDOWN_STOP = "EXPERIMENT_DRAWDOWN_STOP"
    MARKET_INACTIVE = "MARKET_INACTIVE"
    MARKET_PROVISIONAL = "MARKET_PROVISIONAL"
    RULES_MISMATCH = "RULES_MISMATCH"
    CONTRACT_AMBIGUOUS = "CONTRACT_AMBIGUOUS"
    SETTLEMENT_UNKNOWN = "SETTLEMENT_UNKNOWN"
    PRICE_STRUCTURE_UNSUPPORTED = "PRICE_STRUCTURE_UNSUPPORTED"
    QUANTITY_STRUCTURE_UNSUPPORTED = "QUANTITY_STRUCTURE_UNSUPPORTED"
    RISK_UNCOMPUTABLE = "RISK_UNCOMPUTABLE"
    DATA_STALE = "DATA_STALE"
    BOOK_GAP = "BOOK_GAP"
    SOURCE_UNHEALTHY = "SOURCE_UNHEALTHY"
    API_COMPATIBILITY_FAILURE = "API_COMPATIBILITY_FAILURE"
    MODEL_NOT_ELIGIBLE = "MODEL_NOT_ELIGIBLE"
    CALIBRATOR_NOT_ELIGIBLE = "CALIBRATOR_NOT_ELIGIBLE"
    FORECAST_STALE = "FORECAST_STALE"
    FORECAST_ABSTAINED = "FORECAST_ABSTAINED"
    FEE_UNVERIFIED = "FEE_UNVERIFIED"
    AFTER_COST_GATE_FAILED = "AFTER_COST_GATE_FAILED"
    DUPLICATE_CLIENT_ORDER_ID = "DUPLICATE_CLIENT_ORDER_ID"
    INTENT_CHANGED = "INTENT_CHANGED"
    STRATEGY_KILL = "STRATEGY_KILL"
    DATA_KILL = "DATA_KILL"
    PORTFOLIO_KILL = "PORTFOLIO_KILL"
    CREDENTIAL_KILL = "CREDENTIAL_KILL"
    COMPLIANCE_HOLD = "COMPLIANCE_HOLD"
    GLOBAL_HALT = "GLOBAL_HALT"
    ORDER_GROUP_UNAVAILABLE = "ORDER_GROUP_UNAVAILABLE"
    SELF_TRADE_CONFLICT = "SELF_TRADE_CONFLICT"
    AUTHORIZATION_UNAVAILABLE = "AUTHORIZATION_UNAVAILABLE"
    SUBACCOUNT_INVALID = "SUBACCOUNT_INVALID"
    NOT_RISK_REDUCING = "NOT_RISK_REDUCING"


def _validate_money(values: tuple[Decimal | None, ...], *, allow_none: bool = False) -> None:
    for value in values:
        if value is None and allow_none:
            continue
        if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
            raise RiskDomainError("financial values must be finite non-negative Decimal")


@dataclass(frozen=True, slots=True)
class RiskIntent:
    intent_id: str
    created_at: datetime
    market_ticker: str
    event_id: str
    correlation_cluster_id: str
    rules_version: str
    rules_hash: str
    contract_interpretation_version: str
    candidate_id: str
    forecast_id: str
    economic_action: EconomicAction
    outcome_side: str
    book_side: str
    price: Decimal | None
    quantity: Decimal | None
    maximum_expected_fee: Decimal | None
    maximum_expected_cash_commitment: Decimal | None
    maximum_loss_if_filled: Decimal | None
    order_style: str
    time_in_force_policy: str
    expires_at: datetime | None
    post_only: bool
    cancel_order_on_pause: bool
    reduce_only: bool
    self_trade_prevention: str
    required_order_group_policy: str
    client_order_id: str
    account: str
    subaccount: int
    content_hash: str

    @classmethod
    def freeze(cls, **values: Any) -> RiskIntent:
        digest = content_hash(values)
        return cls(content_hash=digest, **values)

    def __post_init__(self) -> None:
        _validate_money(
            (
                self.price,
                self.quantity,
                self.maximum_expected_fee,
                self.maximum_expected_cash_commitment,
                self.maximum_loss_if_filled,
            ),
            allow_none=True,
        )
        if self.subaccount != 0:
            raise RiskDomainError("risk intent must explicitly use subaccount 0")


@dataclass(frozen=True, slots=True)
class PortfolioRiskSnapshot:
    observed_at: datetime
    account_snapshot_version: str
    reconciliation_version: str
    cash: Decimal | None
    portfolio_value: Decimal | None
    account_equity: Decimal | None
    protected_reserve: Decimal
    active_capital_available: Decimal | None
    current_market_risk: Decimal | None
    current_event_risk: Decimal | None
    current_aggregate_risk: Decimal | None
    resting_order_potential_risk: Decimal | None
    projected_market_risk: Decimal | None
    projected_event_risk: Decimal | None
    projected_aggregate_risk: Decimal | None
    realized_daily_pnl: Decimal | None
    realized_weekly_pnl: Decimal | None
    realized_monthly_pnl: Decimal | None
    experiment_equity: Decimal | None
    experiment_high_water_mark: Decimal | None
    experiment_drawdown: Decimal | None
    external_positions: int
    external_orders: int
    unknown_orders: int
    account_fresh: bool
    reconciliation_status: ReconciliationStatus
    exchange_market_exposure: Decimal | None
    exchange_event_exposure: Decimal | None
    independently_calculated_market_exposure: Decimal | None
    independently_calculated_event_exposure: Decimal | None
    content_hash: str

    @classmethod
    def freeze(cls, **values: Any) -> PortfolioRiskSnapshot:
        digest = content_hash(values)
        return cls(content_hash=digest, **values)

    def __post_init__(self) -> None:
        _validate_money(
            (
                self.cash,
                self.portfolio_value,
                self.account_equity,
                self.protected_reserve,
                self.active_capital_available,
                self.current_market_risk,
                self.current_event_risk,
                self.current_aggregate_risk,
                self.resting_order_potential_risk,
                self.projected_market_risk,
                self.projected_event_risk,
                self.projected_aggregate_risk,
                self.experiment_equity,
                self.experiment_high_water_mark,
                self.experiment_drawdown,
                self.exchange_market_exposure,
                self.exchange_event_exposure,
                self.independently_calculated_market_exposure,
                self.independently_calculated_event_exposure,
            ),
            allow_none=True,
        )
        for pnl in (self.realized_daily_pnl, self.realized_weekly_pnl, self.realized_monthly_pnl):
            if pnl is not None and (not isinstance(pnl, Decimal) or not pnl.is_finite()):
                raise RiskDomainError("P&L must be finite Decimal")


@dataclass(frozen=True, slots=True)
class RequiredOrderGroupPolicy:
    policy_id: str
    required: bool
    expected_subaccount: int
    contract_limit_ceiling: Decimal
    group_active: bool
    auto_cancel_ready: bool


@dataclass(frozen=True, slots=True)
class RiskDecision:
    decision_id: str
    state: RiskDecisionState
    intent_hash: str
    risk_policy_version: str
    portfolio_state_hash: str
    reconciliation_version: str
    rules_version: str
    market_data_version: str
    loss_state_version: str
    compliance_state_version: str
    kill_state_version: str
    decided_at: datetime
    expires_at: datetime
    reasons: tuple[RiskReason, ...]
    content_hash: str
    display_result: str
    production_write_authorized: bool = False

    @classmethod
    def freeze(cls, **values: Any) -> RiskDecision:
        digest = content_hash(values)
        return cls(decision_id=digest, content_hash=digest, **values)
