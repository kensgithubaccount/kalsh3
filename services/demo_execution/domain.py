"""Exact, immutable, non-production execution domain objects."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

DEMO_REST_ORIGIN = "https://external-api.demo.kalshi.co/trade-api/v2"
DEMO_WS_ORIGIN = "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2"


class ExecutionEnvironment(StrEnum):
    MOCK = "MOCK"
    PAPER = "PAPER"
    DEMO = "DEMO"


class OrderState(StrEnum):
    PROPOSED = "PROPOSED"
    RISK_APPROVED = "RISK_APPROVED"
    SUBMISSION_PENDING = "SUBMISSION_PENDING"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLATION_PENDING = "CANCELLATION_PENDING"
    AMENDMENT_PENDING = "AMENDMENT_PENDING"
    DECREASE_PENDING = "DECREASE_PENDING"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    UNKNOWN_RECONCILIATION_REQUIRED = "UNKNOWN_RECONCILIATION_REQUIRED"


class PaperOrderState(StrEnum):
    CREATED = "CREATED"
    SUBMISSION_PENDING = "SUBMISSION_PENDING"
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"
    RESTING = "RESTING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELED = "CANCELED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class ReconciliationOutcome(StrEnum):
    FOUND_EXACTLY_ONE = "FOUND_EXACTLY_ONE"
    NOT_FOUND_YET = "NOT_FOUND_YET"
    MULTIPLE_CONFLICT = "MULTIPLE_CONFLICT"
    READ_FAILED = "READ_FAILED"


_TRANSITIONS: dict[OrderState, frozenset[OrderState]] = {
    OrderState.PROPOSED: frozenset({OrderState.RISK_APPROVED}),
    OrderState.RISK_APPROVED: frozenset({OrderState.SUBMISSION_PENDING}),
    OrderState.SUBMISSION_PENDING: frozenset(
        {OrderState.ACCEPTED, OrderState.REJECTED, OrderState.UNKNOWN_RECONCILIATION_REQUIRED}
    ),
    OrderState.ACCEPTED: frozenset(
        {
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCELLATION_PENDING,
            OrderState.AMENDMENT_PENDING,
            OrderState.DECREASE_PENDING,
            OrderState.UNKNOWN_RECONCILIATION_REQUIRED,
        }
    ),
    OrderState.PARTIALLY_FILLED: frozenset(
        {
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCELLATION_PENDING,
            OrderState.AMENDMENT_PENDING,
            OrderState.DECREASE_PENDING,
            OrderState.UNKNOWN_RECONCILIATION_REQUIRED,
        }
    ),
    OrderState.CANCELLATION_PENDING: frozenset(
        {
            OrderState.CANCELED,
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.UNKNOWN_RECONCILIATION_REQUIRED,
        }
    ),
    OrderState.AMENDMENT_PENDING: frozenset(
        {
            OrderState.ACCEPTED,
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.UNKNOWN_RECONCILIATION_REQUIRED,
        }
    ),
    OrderState.DECREASE_PENDING: frozenset(
        {
            OrderState.ACCEPTED,
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCELED,
            OrderState.UNKNOWN_RECONCILIATION_REQUIRED,
        }
    ),
    OrderState.UNKNOWN_RECONCILIATION_REQUIRED: frozenset(
        {
            OrderState.ACCEPTED,
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCELED,
            OrderState.REJECTED,
        }
    ),
    OrderState.FILLED: frozenset(),
    OrderState.CANCELED: frozenset(),
    OrderState.REJECTED: frozenset(),
}


def stable_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


@dataclass(frozen=True, slots=True, repr=False)
class DemoWriteCredential:
    key_id: str
    private_key_pem: bytes
    credential_class: str = "DEMO_WRITE"

    def __post_init__(self) -> None:
        if self.credential_class != "DEMO_WRITE" or not self.key_id or not self.private_key_pem:
            raise ValueError("only a complete DEMO_WRITE credential is accepted")

    def __repr__(self) -> str:
        return "DemoWriteCredential(<redacted>)"


@dataclass(frozen=True, slots=True)
class ExecutionIntent:
    intent_hash: str
    ticker: str
    client_order_id: str
    outcome_side: str
    book_side: str
    price: Decimal
    quantity: Decimal
    time_in_force: str
    expiration_time: datetime | None
    post_only: bool
    cancel_order_on_pause: bool
    reduce_only: bool
    self_trade_prevention_type: str
    order_group_id: str | None
    exchange_index: int | None
    subaccount: int = 0

    def __post_init__(self) -> None:
        if self.subaccount != 0:
            raise ValueError("M14 supports only explicit subaccount 0")
        if not self.client_order_id.startswith("kalsh3-v1-"):
            raise ValueError("client_order_id is outside the bot namespace")
        if not all(
            isinstance(value, Decimal) and value.is_finite()
            for value in (self.price, self.quantity)
        ):
            raise TypeError("price and quantity must be finite Decimal")
        if not Decimal(0) < self.price < Decimal(1) or self.quantity <= 0:
            raise ValueError("invalid binary-contract price or quantity")
        if self.time_in_force not in {"good_till_canceled", "immediate_or_cancel", "fill_or_kill"}:
            raise ValueError("unsupported time_in_force")
        if self.post_only and self.time_in_force != "good_till_canceled":
            raise ValueError("post_only requires good_till_canceled")
        if self.expiration_time is not None and self.expiration_time.tzinfo is None:
            raise ValueError("expiration must be timezone aware")
        expected = stable_hash(self.hash_payload())
        if self.intent_hash != expected:
            raise ValueError("INTENT_CHANGED")

    def hash_payload(self) -> dict[str, object]:
        return {
            "ticker": self.ticker,
            "client_order_id": self.client_order_id,
            "outcome_side": self.outcome_side,
            "book_side": self.book_side,
            "price": str(self.price),
            "quantity": str(self.quantity),
            "time_in_force": self.time_in_force,
            "expiration_time": self.expiration_time,
            "post_only": self.post_only,
            "cancel_order_on_pause": self.cancel_order_on_pause,
            "reduce_only": self.reduce_only,
            "self_trade_prevention_type": self.self_trade_prevention_type,
            "order_group_id": self.order_group_id,
            "exchange_index": self.exchange_index,
            "subaccount": self.subaccount,
        }


@dataclass(frozen=True, slots=True)
class LocalOrder:
    execution_id: str
    client_order_id: str
    environment: ExecutionEnvironment
    intent_hash: str
    ticker: str
    outcome_side: str
    price: Decimal
    quantity: Decimal
    filled_quantity: Decimal
    fees: Decimal
    state: OrderState
    updated_at: datetime
    exchange_order_id: str | None = None
    reconciliation_required: bool = True
    production_influence: str = "NONE"

    def __post_init__(self) -> None:
        if self.updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone aware")
        if not self.execution_id.startswith("exec-"):
            raise ValueError("local execution identifier required")
        if self.filled_quantity < 0 or self.filled_quantity > self.quantity or self.fees < 0:
            raise ValueError("invalid exact order accounting")
        if self.production_influence != "NONE":
            raise ValueError("M14 has no production influence")

    def transition(
        self,
        target: OrderState,
        *,
        at: datetime,
        filled_quantity: Decimal | None = None,
        fees: Decimal | None = None,
        exchange_order_id: str | None = None,
        reconciled: bool = False,
    ) -> LocalOrder:
        at = at.astimezone(UTC)
        if at < self.updated_at.astimezone(UTC) or target not in _TRANSITIONS[self.state]:
            raise ValueError("invalid order lifecycle transition")
        filled = self.filled_quantity if filled_quantity is None else filled_quantity
        next_fees = self.fees if fees is None else fees
        if filled < self.filled_quantity or filled > self.quantity:
            raise ValueError("fill quantity must be monotonic and bounded")
        if target == OrderState.FILLED and filled != self.quantity:
            raise ValueError("FILLED requires exact complete quantity")
        if target == OrderState.PARTIALLY_FILLED and not Decimal(0) < filled < self.quantity:
            raise ValueError("PARTIALLY_FILLED requires a proper partial quantity")
        return replace(
            self,
            state=target,
            filled_quantity=filled,
            fees=next_fees,
            exchange_order_id=exchange_order_id or self.exchange_order_id,
            reconciliation_required=not reconciled,
            updated_at=at,
        )


@dataclass(frozen=True, slots=True)
class PaperOrder:
    """Compatibility façade retained for the initial M14 boundary."""

    paper_order_id: str
    client_order_id: str
    environment: ExecutionEnvironment
    intent_hash: str
    quantity: Decimal
    filled_quantity: Decimal
    state: PaperOrderState
    updated_at: datetime
    production_influence: str = "NONE"

    def __post_init__(self) -> None:
        if self.updated_at.tzinfo is None or not self.paper_order_id.startswith("paper-"):
            raise ValueError("paper order requires timezone and synthetic identifier")
        if self.quantity <= 0 or not Decimal(0) <= self.filled_quantity <= self.quantity:
            raise ValueError("invalid exact quantity accounting")
        if self.production_influence != "NONE":
            raise ValueError("demo execution cannot have production influence")

    def transition(
        self, target: PaperOrderState, *, at: datetime, filled_quantity: Decimal | None = None
    ) -> PaperOrder:
        legacy = {
            PaperOrderState.CREATED: {PaperOrderState.SUBMISSION_PENDING},
            PaperOrderState.SUBMISSION_PENDING: {
                PaperOrderState.SUBMISSION_UNKNOWN,
                PaperOrderState.RESTING,
                PaperOrderState.FILLED,
            },
            PaperOrderState.SUBMISSION_UNKNOWN: {PaperOrderState.RECONCILIATION_REQUIRED},
            PaperOrderState.RESTING: {
                PaperOrderState.PARTIALLY_FILLED,
                PaperOrderState.FILLED,
                PaperOrderState.CANCEL_PENDING,
            },
            PaperOrderState.PARTIALLY_FILLED: {
                PaperOrderState.FILLED,
                PaperOrderState.CANCEL_PENDING,
            },
            PaperOrderState.CANCEL_PENDING: {
                PaperOrderState.CANCELED,
                PaperOrderState.PARTIALLY_FILLED,
                PaperOrderState.FILLED,
            },
        }
        if at < self.updated_at or target not in legacy.get(self.state, set()):
            raise ValueError("invalid transition or clock regression")
        filled = self.filled_quantity if filled_quantity is None else filled_quantity
        if target == PaperOrderState.FILLED and filled != self.quantity:
            raise ValueError("FILLED requires the entire exact quantity")
        if (
            target == PaperOrderState.PARTIALLY_FILLED
            and not self.filled_quantity < filled < self.quantity
        ):
            raise ValueError("partial fill must increase but not complete quantity")
        return replace(self, state=target, filled_quantity=filled, updated_at=at.astimezone(UTC))


def require_nonproduction_environment(value: str) -> ExecutionEnvironment:
    try:
        return ExecutionEnvironment(value.upper())
    except ValueError as error:
        raise ValueError("M14 accepts only DEMO, MOCK, or PAPER") from error
