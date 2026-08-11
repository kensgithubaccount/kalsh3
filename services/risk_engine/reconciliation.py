"""Explicit subaccount-0 reconciliation and external-activity holds."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from .domain import Ownership, ReconciliationStatus


@dataclass(frozen=True, slots=True)
class AccountRiskItem:
    stable_id: str
    subaccount: int
    kind: str
    ownership: Ownership
    market_ticker: str
    event_id: str
    maximum_loss: Decimal
    client_order_id: str | None


@dataclass(frozen=True, slots=True)
class ReconciliationInput:
    observed_at: datetime
    balance_complete: bool
    positions_complete: bool
    orders_complete: bool
    fills_complete: bool
    settlements_complete: bool
    ledger_complete: bool
    bot_client_ids_complete: bool
    api_authenticated: bool
    api_healthy: bool
    items: tuple[AccountRiskItem, ...]
    exchange_market_exposure: Decimal | None
    calculated_market_exposure: Decimal | None
    exchange_event_exposure: Decimal | None
    calculated_event_exposure: Decimal | None


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    version: str
    status: ReconciliationStatus
    scoped_items: tuple[AccountRiskItem, ...]
    unknown_orders: int
    unknown_positions: int
    issues: tuple[str, ...]


def reconcile(
    value: ReconciliationInput,
    *,
    now: datetime,
    max_age: timedelta = timedelta(seconds=30),
    exposure_tolerance: Decimal = Decimal("0.01"),
) -> ReconciliationResult:
    scoped = tuple(item for item in value.items if item.subaccount == 0)
    issues: list[str] = []
    if not value.api_authenticated:
        status = ReconciliationStatus.AUTH_FAILURE
    elif not value.api_healthy:
        status = ReconciliationStatus.API_FAILURE
    elif now - value.observed_at > max_age:
        status = ReconciliationStatus.STALE
    elif not all(
        (
            value.balance_complete,
            value.positions_complete,
            value.orders_complete,
            value.fills_complete,
            value.settlements_complete,
            value.ledger_complete,
            value.bot_client_ids_complete,
        )
    ):
        status = ReconciliationStatus.PARTIAL
    else:
        status = ReconciliationStatus.RECONCILED
    unknown_orders = sum(
        item.kind == "ORDER" and item.ownership == Ownership.EXTERNAL_UNKNOWN for item in scoped
    )
    unknown_positions = sum(
        item.kind == "POSITION" and item.ownership == Ownership.EXTERNAL_UNKNOWN for item in scoped
    )
    if unknown_orders:
        status = ReconciliationStatus.UNKNOWN_ORDER
        issues.append("UNKNOWN_ORDER_HOLD")
    if unknown_positions:
        status = ReconciliationStatus.UNKNOWN_POSITION
        issues.append("EXTERNAL_POSITION_HOLD")
    pairs = (
        (value.exchange_market_exposure, value.calculated_market_exposure, "MARKET"),
        (value.exchange_event_exposure, value.calculated_event_exposure, "EVENT"),
    )
    for exchange, calculated, label in pairs:
        if exchange is None or calculated is None:
            if status == ReconciliationStatus.RECONCILED:
                status = ReconciliationStatus.PARTIAL
            issues.append(f"{label}_EXPOSURE_UNAVAILABLE")
        elif abs(exchange - calculated) > exposure_tolerance:
            status = ReconciliationStatus.MISMATCH
            issues.append("EXPOSURE_RECONCILIATION_MISMATCH")
    version = f"reconcile:{value.observed_at.isoformat()}:{len(scoped)}:{status}"
    return ReconciliationResult(
        version, status, scoped, unknown_orders, unknown_positions, tuple(issues)
    )
