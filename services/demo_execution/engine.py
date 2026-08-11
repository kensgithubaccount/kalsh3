"""One state machine shared by MOCK, PAPER, and allowlisted DEMO transports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from services.risk_engine.authorization import AuthorizationStore, RiskAuthorization

from .adapter import (
    DemoMutationTransport,
    MutationKind,
    MutationRequest,
    TransportResponse,
    amend_request,
    cancel_request,
    create_request,
    decrease_request,
)
from .domain import (
    DEMO_REST_ORIGIN,
    ExecutionEnvironment,
    ExecutionIntent,
    LocalOrder,
    OrderState,
    ReconciliationOutcome,
    stable_hash,
)
from .faults import AmbiguousMutation
from .store import ExecutionStore


class LocalTransport(Protocol):
    def __call__(self, request: MutationRequest) -> TransportResponse: ...


@dataclass(frozen=True, slots=True)
class PreSendState:
    safety_state_hash: str
    portfolio_state_hash: str
    global_halt_clear: bool
    compliance_clear: bool
    kill_states_clear: bool
    reconciliation_fresh: bool
    market_rules_valid: bool

    @property
    def valid(self) -> bool:
        return all(
            (
                self.global_halt_clear,
                self.compliance_clear,
                self.kill_states_clear,
                self.reconciliation_fresh,
                self.market_rules_valid,
            )
        )


def _new_order(intent: ExecutionIntent, mode: ExecutionEnvironment, now: datetime) -> LocalOrder:
    return LocalOrder(
        execution_id=f"exec-{stable_hash((intent.intent_hash, mode))[:24]}",
        client_order_id=intent.client_order_id,
        environment=mode,
        intent_hash=intent.intent_hash,
        ticker=intent.ticker,
        outcome_side=intent.outcome_side,
        price=intent.price,
        quantity=intent.quantity,
        filled_quantity=Decimal(0),
        fees=Decimal(0),
        state=OrderState.PROPOSED,
        updated_at=now.astimezone(UTC),
    )


class ExecutionEngine:
    def __init__(
        self,
        *,
        store: ExecutionStore,
        authorization_store: AuthorizationStore,
        demo_transport: DemoMutationTransport | None = None,
        local_transport: LocalTransport | None = None,
    ) -> None:
        self.store = store
        self.authorization_store = authorization_store
        self.demo_transport = demo_transport
        self.local_transport = local_transport

    def _send(self, mode: ExecutionEnvironment, request: MutationRequest) -> TransportResponse:
        if mode is ExecutionEnvironment.DEMO:
            if self.demo_transport is None:
                raise ValueError("demo transport is unavailable")
            return self.demo_transport.send(mode, request)
        if self.local_transport is None:
            raise ValueError("paper/mock transport is unavailable")
        return self.local_transport(request)

    def submit(
        self,
        *,
        mode: ExecutionEnvironment,
        intent: ExecutionIntent,
        authorization: RiskAuthorization,
        state: PreSendState,
        now: datetime,
    ) -> LocalOrder:
        if intent.intent_hash != authorization.intent_hash:
            raise ValueError("INTENT_CHANGED")
        if not state.valid or authorization.expires_at <= now.astimezone(UTC):
            raise ValueError("material safety state blocks submission")
        if mode is ExecutionEnvironment.DEMO and self.demo_transport is None:
            raise ValueError("demo transport is unavailable")
        if mode is not ExecutionEnvironment.DEMO and self.local_transport is None:
            raise ValueError("paper/mock transport is unavailable")
        order = _new_order(intent, mode, now).transition(OrderState.RISK_APPROVED, at=now)
        request = create_request(intent)
        host = DEMO_REST_ORIGIN if mode is ExecutionEnvironment.DEMO else f"local://{mode.lower()}"
        if not self.store.acquire_journal(
            execution_id=order.execution_id,
            intent_hash=intent.intent_hash,
            client_order_id=intent.client_order_id,
            authorization_id=authorization.authorization_id,
            environment=mode,
            host=host,
            request=request,
            now=now,
        ):
            raise ValueError("submission journal is already owned")
        order = order.transition(OrderState.SUBMISSION_PENDING, at=now)
        self.store.save_order(order)
        consumed = self.authorization_store.consume(
            authorization.authorization_id,
            intent_hash=intent.intent_hash,
            portfolio_state_hash=state.portfolio_state_hash,
            safety_state_hash=state.safety_state_hash,
        )
        if not consumed:
            self.store.journal_state(
                order.execution_id, "AUTHORIZATION_NOT_CONSUMED", may_have_been_sent=False
            )
            raise ValueError("M13 authorization is not consumable")
        self.store.journal_state(order.execution_id, "SEND_STARTED", may_have_been_sent=True)
        try:
            response = self._send(mode, request)
        except AmbiguousMutation as error:
            journal_state = "PROVEN_UNSENT" if not error.may_have_been_sent else "RESPONSE_UNKNOWN"
            self.store.journal_state(
                order.execution_id, journal_state, may_have_been_sent=error.may_have_been_sent
            )
            if error.may_have_been_sent:
                order = order.transition(OrderState.UNKNOWN_RECONCILIATION_REQUIRED, at=now)
                self.store.save_order(order)
            raise
        if response.status == 201 and isinstance(response.body.get("order_id"), str):
            order = order.transition(
                OrderState.ACCEPTED,
                at=now,
                exchange_order_id=str(response.body["order_id"]),
            )
            self.store.journal_state(
                order.execution_id, "RESPONSE_PERSISTED_RECONCILE_REQUIRED", may_have_been_sent=True
            )
        elif 400 <= response.status < 500 and response.status != 429:
            order = order.transition(OrderState.REJECTED, at=now)
            self.store.journal_state(order.execution_id, "REJECTED", may_have_been_sent=True)
        else:
            order = order.transition(OrderState.UNKNOWN_RECONCILIATION_REQUIRED, at=now)
            self.store.journal_state(
                order.execution_id, "RESPONSE_UNKNOWN", may_have_been_sent=True
            )
        self.store.save_order(order)
        return order

    def mutate(
        self,
        *,
        order: LocalOrder,
        kind: MutationKind,
        state: PreSendState,
        now: datetime,
        reduce_by: Decimal | None = None,
        reduce_to: Decimal | None = None,
        replacement_intent: ExecutionIntent | None = None,
        authorized_intent_hash: str | None = None,
        new_client_order_id: str | None = None,
    ) -> LocalOrder:
        if not state.valid or order.exchange_order_id is None or order.reconciliation_required:
            raise ValueError("mutation requires a fresh reconciled order")
        if kind == MutationKind.CANCEL:
            request = cancel_request(order.exchange_order_id)
            pending = OrderState.CANCELLATION_PENDING
        elif kind == MutationKind.DECREASE:
            request = decrease_request(
                order.exchange_order_id, reduce_by=reduce_by, reduce_to=reduce_to
            )
            pending = OrderState.DECREASE_PENDING
        elif kind == MutationKind.AMEND:
            if (
                replacement_intent is None
                or authorized_intent_hash is None
                or new_client_order_id is None
            ):
                raise ValueError("amend requires a newly authorized exact replacement intent")
            request = amend_request(
                order.exchange_order_id,
                authorized_intent_hash=authorized_intent_hash,
                replacement_intent=replacement_intent,
                new_client_order_id=new_client_order_id,
            )
            pending = OrderState.AMENDMENT_PENDING
        else:
            raise ValueError("create uses submit, not mutate")
        attempt_id = self.store.acquire_mutation(
            execution_id=order.execution_id, kind=kind, request=request, now=now
        )
        if attempt_id is None:
            raise ValueError("identical mutation already journaled; reconcile instead of retrying")
        pending_order = order.transition(pending, at=now)
        self.store.save_order(pending_order)
        self.store.mutation_state(attempt_id, "SEND_STARTED", may_have_been_sent=True)
        try:
            response = self._send(order.environment, request)
        except AmbiguousMutation as error:
            self.store.mutation_state(
                attempt_id,
                "PROVEN_UNSENT" if not error.may_have_been_sent else "RESPONSE_UNKNOWN",
                may_have_been_sent=error.may_have_been_sent,
            )
            if not error.may_have_been_sent:
                raise
            unknown = pending_order.transition(OrderState.UNKNOWN_RECONCILIATION_REQUIRED, at=now)
            self.store.save_order(unknown)
            return unknown
        if response.status < 200 or response.status >= 300:
            unknown = pending_order.transition(OrderState.UNKNOWN_RECONCILIATION_REQUIRED, at=now)
            self.store.mutation_state(attempt_id, "RESPONSE_UNKNOWN", may_have_been_sent=True)
            self.store.save_order(unknown)
            return unknown
        filled = Decimal(str(response.body.get("filled_quantity", order.filled_quantity)))
        fees = Decimal(str(response.body.get("fees", order.fees)))
        if filled == order.quantity:
            target = OrderState.FILLED
        elif filled > order.filled_quantity:
            target = OrderState.PARTIALLY_FILLED
        elif kind == MutationKind.CANCEL:
            target = OrderState.CANCELED
        else:
            target = OrderState.ACCEPTED
        result = pending_order.transition(target, at=now, filled_quantity=filled, fees=fees)
        self.store.mutation_state(attempt_id, "ACK_RECONCILE_REQUIRED", may_have_been_sent=True)
        if kind == MutationKind.AMEND and new_client_order_id is not None:
            self.store.record_lineage(
                order.client_order_id, new_client_order_id, order.execution_id, now
            )
        self.store.save_order(result)
        return result


def reconcile_client_order(
    *, client_order_id: str, orders: tuple[dict[str, object], ...] | None, read_failed: bool = False
) -> ReconciliationOutcome:
    if read_failed or orders is None:
        return ReconciliationOutcome.READ_FAILED
    scoped = [
        order
        for order in orders
        if order.get("subaccount") == 0 and order.get("client_order_id") == client_order_id
    ]
    if len(scoped) == 1:
        return ReconciliationOutcome.FOUND_EXACTLY_ONE
    if len(scoped) > 1:
        return ReconciliationOutcome.MULTIPLE_CONFLICT
    return ReconciliationOutcome.NOT_FOUND_YET
