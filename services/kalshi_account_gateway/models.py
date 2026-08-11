"""Exact, immutable account snapshot domain objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from services.core.money import decimal_from_wire


class BudgetLike(Protocol):
    @property
    def requests(self) -> int: ...

    @property
    def retries(self) -> int: ...


class SnapshotValidationError(ValueError):
    """Account data was incomplete or unsafe to display as reconciled."""


def _money(value: Any, field: str) -> Decimal:
    if not isinstance(value, str):
        raise SnapshotValidationError(f"{field} must be a fixed-point string")
    try:
        result = decimal_from_wire(value)
    except ValueError as exc:
        raise SnapshotValidationError(f"invalid {field}") from exc
    return result


def _normalize_rows(rows: Any, kind: str) -> tuple[dict[str, Any], ...]:
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise SnapshotValidationError(f"malformed {kind}")
    normalized: list[dict[str, Any]] = []
    money_fields = {
        "yes_total_cost_dollars",
        "no_total_cost_dollars",
        "revenue_dollars",
        "fee_cost_dollars",
        "price_dollars",
        "yes_price_dollars",
        "no_price_dollars",
    }
    forbidden = {"yes_total_cost", "no_total_cost"}
    for row in rows:
        if forbidden.intersection(row):
            raise SnapshotValidationError("removed legacy cent-cost field received")
        item = dict(row)
        for field in money_fields.intersection(item):
            item[field] = _money(item[field], field)
        normalized.append(item)
    return tuple(normalized)


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    observed_at: datetime
    subaccount: int
    cash: Decimal
    portfolio_value: Decimal
    positions: tuple[dict[str, Any], ...]
    orders: tuple[dict[str, Any], ...]
    fills: tuple[dict[str, Any], ...]
    settlements: tuple[dict[str, Any], ...]
    api_tier: str
    read_refill_rate: Decimal
    read_capacity: Decimal
    write_refill_rate: Decimal
    write_capacity: Decimal
    read_requests: int
    read_retries: int
    reconciled: bool

    @classmethod
    def from_payloads(
        cls, payloads: dict[str, Any], observed_at: datetime, budget: BudgetLike
    ) -> AccountSnapshot:
        required = {"balance", "positions", "orders", "fills", "settlements", "limits"}
        if payloads.keys() != required or observed_at.tzinfo is None:
            raise SnapshotValidationError("account response set or timestamp is incomplete")
        balance, limits = payloads["balance"], payloads["limits"]
        if not isinstance(balance, dict) or not isinstance(limits, dict):
            raise SnapshotValidationError("balance or limits response is malformed")
        required_limits = {
            "usage_tier",
            "read_refill_rate",
            "read_capacity",
            "write_refill_rate",
            "write_capacity",
        }
        if not required_limits.issubset(limits) or not isinstance(limits["usage_tier"], str):
            raise SnapshotValidationError("account limits are incomplete")
        cash = _money(balance.get("balance_dollars"), "balance_dollars")
        portfolio = _money(balance.get("portfolio_value_dollars"), "portfolio_value_dollars")
        if cash < 0 or portfolio < 0:
            raise SnapshotValidationError("negative account totals are unsupported")
        return cls(
            observed_at=observed_at.astimezone(UTC),
            subaccount=0,
            cash=cash,
            portfolio_value=portfolio,
            positions=_normalize_rows(payloads["positions"], "positions"),
            orders=_normalize_rows(payloads["orders"], "orders"),
            fills=_normalize_rows(payloads["fills"], "fills"),
            settlements=_normalize_rows(payloads["settlements"], "settlements"),
            api_tier=limits["usage_tier"],
            read_refill_rate=_money(str(limits["read_refill_rate"]), "read_refill_rate"),
            read_capacity=_money(str(limits["read_capacity"]), "read_capacity"),
            write_refill_rate=_money(str(limits["write_refill_rate"]), "write_refill_rate"),
            write_capacity=_money(str(limits["write_capacity"]), "write_capacity"),
            read_requests=budget.requests,
            read_retries=budget.retries,
            reconciled=True,
        )
