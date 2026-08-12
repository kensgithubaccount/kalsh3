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


def _integer(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SnapshotValidationError(f"{field} must be an integer")
    return Decimal(value)


def _cents(value: Any, field: str) -> Decimal:
    return _integer(value, field) / Decimal(100)


def _balance_breakdown(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        raise SnapshotValidationError("balance breakdown must be an array")
    for item in value:
        if not isinstance(item, dict):
            raise SnapshotValidationError("balance breakdown item must be an object")


def _grants(value: Any) -> None:
    if not isinstance(value, list):
        raise SnapshotValidationError("account grants must be an array")
    for grant in value:
        if not isinstance(grant, dict):
            raise SnapshotValidationError("account grant must be an object")


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
        if not isinstance(limits.get("usage_tier"), str):
            raise SnapshotValidationError("account limits are incomplete")
        read_limits, write_limits = limits.get("read"), limits.get("write")
        if not isinstance(read_limits, dict) or not isinstance(write_limits, dict):
            raise SnapshotValidationError("account limits are incomplete")
        _grants(limits.get("grants"))
        breakdown = balance.get("balance_breakdown")
        if not isinstance(balance.get("updated_ts"), int) or isinstance(
            balance.get("updated_ts"), bool
        ):
            raise SnapshotValidationError("balance metadata is incomplete")
        _balance_breakdown(breakdown)
        cash = _cents(balance.get("balance"), "balance")
        _money(balance.get("balance_dollars"), "balance_dollars")
        portfolio = _cents(balance.get("portfolio_value"), "portfolio_value")
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
            read_refill_rate=_integer(read_limits.get("refill_rate"), "read.refill_rate"),
            read_capacity=_integer(read_limits.get("bucket_capacity"), "read.bucket_capacity"),
            write_refill_rate=_integer(write_limits.get("refill_rate"), "write.refill_rate"),
            write_capacity=_integer(write_limits.get("bucket_capacity"), "write.bucket_capacity"),
            read_requests=budget.requests,
            read_retries=budget.retries,
            reconciled=True,
        )
