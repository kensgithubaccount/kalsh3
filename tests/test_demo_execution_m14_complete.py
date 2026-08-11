from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from services.demo_execution.adapter import (
    DemoMutationTransport,
    MutationKind,
    TransportResponse,
    amend_request,
    cancel_request,
    create_request,
    decrease_request,
)
from services.demo_execution.calibration import FeeComparison, QueueComparison, SlippageComparison
from services.demo_execution.domain import (
    DEMO_REST_ORIGIN,
    DemoWriteCredential,
    ExecutionEnvironment,
    ExecutionIntent,
    LocalOrder,
    OrderState,
    ReconciliationOutcome,
    stable_hash,
)
from services.demo_execution.engine import ExecutionEngine, PreSendState, reconcile_client_order
from services.demo_execution.faults import AmbiguousMutation, Fault, FaultExchange
from services.demo_execution.load import run_fault_load
from services.demo_execution.portfolio import net_postings, postings_for_fill
from services.demo_execution.postgres import POSTGRES_RESERVATION_SQL, PostgresExecutionTransaction
from services.demo_execution.rate_budget import WriteBudget
from services.demo_execution.setup import DemoCredentialSetup
from services.demo_execution.store import ExecutionStore, FillEvent, QueueObservation
from services.risk_engine.authorization import AuthorizationState, RiskAuthorization
from services.web_dashboard.security import SecretBox
from services.web_dashboard.store import StateStore

NOW = datetime(2026, 8, 10, 12, tzinfo=UTC)


def make_intent(**changes: object) -> ExecutionIntent:
    values: dict[str, object] = {
        "ticker": "DEMO-MARKET",
        "client_order_id": "kalsh3-v1-demo0001",
        "outcome_side": "YES",
        "book_side": "bid",
        "price": Decimal("0.45"),
        "quantity": Decimal("1.25"),
        "time_in_force": "good_till_canceled",
        "expiration_time": None,
        "post_only": True,
        "cancel_order_on_pause": True,
        "reduce_only": False,
        "self_trade_prevention_type": "cancel_newest",
        "order_group_id": "demo-group",
        "exchange_index": 0,
        "subaccount": 0,
    }
    values.update(changes)
    temporary = object.__new__(ExecutionIntent)
    for key, value in values.items():
        object.__setattr__(temporary, key, value)
    values["intent_hash"] = stable_hash(temporary.hash_payload())
    return ExecutionIntent(**values)  # type: ignore[arg-type]


def authorization(intent: ExecutionIntent) -> RiskAuthorization:
    return RiskAuthorization(
        "auth-1",
        "decision-1",
        intent.intent_hash,
        "portfolio",
        "1",
        "rules",
        "safe",
        NOW,
        NOW + timedelta(seconds=5),
        AuthorizationState.ISSUED,
    )


class ConsumableAuthorization:
    def __init__(self) -> None:
        self.consumed = False

    def consume(self, *_: object, **__: object) -> bool:
        if self.consumed:
            return False
        self.consumed = True
        return True


def safety() -> PreSendState:
    return PreSendState("safe", "portfolio", True, True, True, True, True)


def resting(mode: ExecutionEnvironment = ExecutionEnvironment.PAPER) -> LocalOrder:
    value = make_intent()
    return LocalOrder(
        "exec-resting",
        value.client_order_id,
        mode,
        value.intent_hash,
        value.ticker,
        value.outcome_side,
        value.price,
        value.quantity,
        Decimal(".25"),
        Decimal(".01"),
        OrderState.PARTIALLY_FILLED,
        NOW,
        "order-1",
        False,
    )


def test_demo_origin_and_credential_are_hard_isolated() -> None:
    credential = DemoWriteCredential("demo-key", b"private-demo-fixture")
    assert "private" not in repr(credential)
    for origin in (
        "https://external-api.kalshi.com/trade-api/v2",
        "https://api.elections.kalshi.com",
        "https://example.com",
        DEMO_REST_ORIGIN + "/extra",
    ):
        with pytest.raises(ValueError, match="demo origin"):
            DemoMutationTransport.validate_origin(origin)
    with pytest.raises(ValueError):
        DemoWriteCredential("key", b"pem", credential_class="PRODUCTION_WRITE")


def test_current_v2_translation_is_fixed_point_and_subaccount_zero() -> None:
    value = make_intent(price=Decimal("0.4525"), quantity=Decimal("1.250"))
    request = create_request(value)
    assert (request.method, request.path) == ("POST", "/portfolio/events/orders")
    assert request.body["count"] == "1.250"
    assert request.body["price"] == "0.4525"
    assert request.body["subaccount"] == 0
    assert "yes_price" not in request.body and "no_price" not in request.body
    assert cancel_request("order-1").path.endswith("/order-1")


def test_tif_and_subaccount_fail_closed() -> None:
    with pytest.raises(ValueError):
        make_intent(subaccount=1)
    with pytest.raises(ValueError):
        make_intent(time_in_force="immediate_or_cancel", post_only=True)
    assert make_intent(time_in_force="fill_or_kill", post_only=False)


def test_decrease_exactly_one_and_amend_requires_new_hash() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        decrease_request("order", reduce_by=Decimal(1), reduce_to=Decimal(1))
    with pytest.raises(ValueError, match="exactly one"):
        decrease_request("order")
    assert decrease_request("order", reduce_by=Decimal(".25")).body == {
        "reduce_by": "0.25",
        "subaccount": 0,
    }
    replacement = make_intent(price=Decimal(".46"))
    with pytest.raises(ValueError, match="INTENT_CHANGED"):
        amend_request(
            "order",
            authorized_intent_hash="old",
            replacement_intent=replacement,
            new_client_order_id="kalsh3-v1-demo0002",
        )


def test_demo_transport_rejects_non_demo_mode_before_sender() -> None:
    exchange = FaultExchange([Fault.ACCEPT])
    transport = DemoMutationTransport(DemoWriteCredential("demo", b"pem"), exchange)
    with pytest.raises(ValueError, match="DEMO mode"):
        transport.send(ExecutionEnvironment.PAPER, create_request(make_intent()))
    assert not exchange.sent


def test_timeout_after_possible_send_is_unknown_and_never_retried(tmp_path: Path) -> None:
    intent = make_intent()
    exchange = FaultExchange([Fault.TIMEOUT_AFTER_SEND])
    engine = ExecutionEngine(
        store=ExecutionStore(tmp_path / "execution.db"),
        authorization_store=ConsumableAuthorization(),  # type: ignore[arg-type]
        demo_transport=DemoMutationTransport(DemoWriteCredential("demo", b"pem"), exchange),
    )
    with pytest.raises(AmbiguousMutation) as raised:
        engine.submit(
            mode=ExecutionEnvironment.DEMO,
            intent=intent,
            authorization=authorization(intent),
            state=safety(),
            now=NOW,
        )
    assert raised.value.may_have_been_sent
    assert len(exchange.sent) == 1
    assert engine.store.recover()
    assert len(exchange.sent) == 1


def test_timeout_before_send_is_proven_unsent_but_not_blindly_retried(tmp_path: Path) -> None:
    intent = make_intent()
    exchange = FaultExchange([Fault.TIMEOUT_BEFORE_SEND])
    engine = ExecutionEngine(
        store=ExecutionStore(tmp_path / "before.db"),
        authorization_store=ConsumableAuthorization(),  # type: ignore[arg-type]
        demo_transport=DemoMutationTransport(DemoWriteCredential("demo", b"pem"), exchange),
    )
    with pytest.raises(AmbiguousMutation) as raised:
        engine.submit(
            mode=ExecutionEnvironment.DEMO,
            intent=intent,
            authorization=authorization(intent),
            state=safety(),
            now=NOW,
        )
    assert not raised.value.may_have_been_sent and not exchange.sent


def test_exact_intent_and_presend_state_are_revalidated(tmp_path: Path) -> None:
    intent = make_intent()
    engine = ExecutionEngine(
        store=ExecutionStore(tmp_path / "blocked.db"),
        authorization_store=ConsumableAuthorization(),  # type: ignore[arg-type]
        local_transport=lambda request: pytest.fail(f"sent {request}"),
    )
    with pytest.raises(ValueError, match="INTENT_CHANGED"):
        engine.submit(
            mode=ExecutionEnvironment.PAPER,
            intent=intent,
            authorization=replace(authorization(intent), intent_hash="changed"),
            state=safety(),
            now=NOW,
        )
    with pytest.raises(ValueError, match="safety"):
        engine.submit(
            mode=ExecutionEnvironment.PAPER,
            intent=intent,
            authorization=authorization(intent),
            state=replace(safety(), global_halt_clear=False),
            now=NOW,
        )


def test_two_workers_only_one_acquires_submission_journal(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "race.db")
    intent = make_intent()
    request = create_request(intent)

    def acquire(_: int) -> bool:
        return store.acquire_journal(
            execution_id="exec-same",
            intent_hash=intent.intent_hash,
            client_order_id=intent.client_order_id,
            authorization_id="auth-same",
            environment=ExecutionEnvironment.PAPER,
            host="local://paper",
            request=request,
            now=NOW,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(acquire, (1, 2))) == 1


def test_client_id_reconciliation_is_bounded_and_subaccount_scoped() -> None:
    rows = (
        {"client_order_id": "target", "subaccount": 1},
        {"client_order_id": "target", "subaccount": 0},
    )
    assert (
        reconcile_client_order(client_order_id="target", orders=rows)
        == ReconciliationOutcome.FOUND_EXACTLY_ONE
    )
    assert (
        reconcile_client_order(client_order_id="missing", orders=rows)
        == ReconciliationOutcome.NOT_FOUND_YET
    )
    assert (
        reconcile_client_order(client_order_id="target", orders=None, read_failed=True)
        == ReconciliationOutcome.READ_FAILED
    )


def fill(trade_id: str = "trade-1") -> FillEvent:
    return FillEvent(
        trade_id, "order-1", "M", Decimal(".45"), Decimal(".5"), Decimal(".01"), True, "YES", NOW, 0
    )


def test_duplicate_fill_and_ws_events_do_not_duplicate_state(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "events.db")
    assert store.add_fill(fill())
    assert not store.add_fill(fill())
    assert store.fill_totals("order-1") == (Decimal(".5"), Decimal(".01"))
    assert store.ingest_ws_event(
        channel="fill",
        event_key="e1",
        execution_id="exec-1",
        state=OrderState.PARTIALLY_FILLED,
        sequence=1,
        happened_at=NOW,
    )
    assert not store.ingest_ws_event(
        channel="fill",
        event_key="e1",
        execution_id="exec-1",
        state=OrderState.PARTIALLY_FILLED,
        sequence=2,
        happened_at=NOW,
    )


def test_ws_gap_and_disconnect_require_rest_reconciliation(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "ws.db")
    assert store.ingest_ws_event(
        channel="user_orders",
        event_key="e1",
        execution_id="exec",
        state=OrderState.ACCEPTED,
        sequence=10,
        happened_at=NOW,
    )
    assert not store.ingest_ws_event(
        channel="user_orders",
        event_key="e2",
        execution_id="exec",
        state=OrderState.FILLED,
        sequence=12,
        happened_at=NOW,
    )
    store.disconnect("user_orders", NOW)


def test_cancel_fill_race_retains_fill_and_requires_reconciliation(tmp_path: Path) -> None:
    engine = ExecutionEngine(
        store=ExecutionStore(tmp_path / "cancel.db"),
        authorization_store=ConsumableAuthorization(),  # type: ignore[arg-type]
        local_transport=lambda request: TransportResponse(
            200, {"filled_quantity": "1.25", "fees": ".02"}
        ),
    )
    result = engine.mutate(
        order=resting(),
        kind=MutationKind.CANCEL,
        state=safety(),
        now=NOW,
    )
    assert result.state == OrderState.FILLED
    assert result.filled_quantity == Decimal("1.25") and result.reconciliation_required


def test_amend_fill_lineage_and_decrease_unknown(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "mutations.db")
    replacement = make_intent(price=Decimal(".46"), client_order_id="kalsh3-v1-demo0002")
    responses = iter(
        (
            TransportResponse(200, {"filled_quantity": ".75", "fees": ".015"}),
            TransportResponse(500, {}),
        )
    )
    engine = ExecutionEngine(
        store=store,
        authorization_store=ConsumableAuthorization(),  # type: ignore[arg-type]
        local_transport=lambda request: next(responses),
    )
    amended = engine.mutate(
        order=resting(),
        kind=MutationKind.AMEND,
        state=safety(),
        now=NOW,
        replacement_intent=replacement,
        authorized_intent_hash=replacement.intent_hash,
        new_client_order_id=replacement.client_order_id,
    )
    assert amended.state == OrderState.PARTIALLY_FILLED
    # A separately reconciled order is needed before another mutation.
    decrease_engine = ExecutionEngine(
        store=ExecutionStore(tmp_path / "decrease.db"),
        authorization_store=ConsumableAuthorization(),  # type: ignore[arg-type]
        local_transport=lambda request: next(responses),
    )
    decreased = decrease_engine.mutate(
        order=replace(resting(), execution_id="exec-decrease", exchange_order_id="order-2"),
        kind=MutationKind.DECREASE,
        state=safety(),
        now=NOW,
        reduce_to=Decimal(".5"),
    )
    assert decreased.state == OrderState.UNKNOWN_RECONCILIATION_REQUIRED


def test_queue_observation_and_calibrations_are_demo_labeled(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "queue.db")
    observation = QueueObservation(NOW, "order", "M", Decimal("5.5"), Decimal("1"), Decimal(".45"))
    store.add_queue_observation(observation)
    assert observation.quality == "OBSERVED_DEMO_ORDER_QUEUE"
    assert QueueComparison(NOW, "M", Decimal(6), Decimal("5.5"), "ACTIVE").discrepancy == Decimal(
        "-.5"
    )
    assert FeeComparison(Decimal(".01"), Decimal(".012"), "fee-v1").difference == Decimal(".002")
    assert SlippageComparison(Decimal(0), Decimal(".01"), Decimal(".015")).m11_error == Decimal(
        ".005"
    )


def test_double_entry_demo_ledger_is_mode_separated() -> None:
    postings = postings_for_fill(fill(), ExecutionEnvironment.DEMO)
    assert all(row.mode == ExecutionEnvironment.DEMO for row in postings)
    assert net_postings(postings) == Decimal(0)


def test_demo_setup_encrypts_only_after_demo_validation(tmp_path: Path) -> None:
    state = StateStore(tmp_path / "state.db")
    box = SecretBox(b"x" * 32)
    setup = DemoCredentialSetup(
        state,
        box,
        lambda credential, origin, subaccount: origin == DEMO_REST_ORIGIN and subaccount == 0,
    )
    with pytest.raises(ValueError):
        setup.enroll(
            key_id="key",
            pem=b"pem",
            requested_origin="https://external-api.kalshi.com",
            strongly_reauthenticated=True,
            explicit_confirmation="INSTALL DEMO CREDENTIAL",
        )
    setup.enroll(
        key_id="demo-key",
        pem=b"demo-pem",
        requested_origin=DEMO_REST_ORIGIN,
        strongly_reauthenticated=True,
        explicit_confirmation="INSTALL DEMO CREDENTIAL",
    )
    loaded = setup.load()
    assert loaded is not None and loaded.key_id == "demo-key"
    assert "demo-pem" not in repr(loaded)


class RecordingCursor:
    def __init__(self) -> None:
        self.sql: list[str] = []
        self.rows = iter((("ISSUED", NOW), (1,), ("exec",), ("auth",)))

    def execute(self, sql: str, parameters: tuple[object, ...] = ()) -> object:
        del parameters
        self.sql.append(sql)
        return self

    def fetchone(self) -> tuple[object, ...] | None:
        return next(self.rows, None)


@pytest.mark.postgres
def test_postgres_path_locks_and_claims_single_use_submission() -> None:
    cursor = RecordingCursor()
    assert PostgresExecutionTransaction(cursor).acquire("auth", "client", "exec")
    statements = "\n".join(cursor.sql) + POSTGRES_RESERVATION_SQL
    assert "SERIALIZABLE" in statements
    assert statements.count("FOR UPDATE") >= 3
    assert "ON CONFLICT DO NOTHING" in statements
    assert "state='CONSUMED'" in statements


def test_fault_load_is_deterministic_and_streaming() -> None:
    first = run_fault_load()
    assert first == run_fault_load()
    assert first.lifecycles == 20_000 and first.unique_orders == 20_000
    assert first.unknown > 0 and first.duplicate_fills_ignored > 0


def test_write_budget_reserves_cancel_and_decrease_capacity() -> None:
    budget = WriteBudget(2, 1)
    assert budget.acquire(MutationKind.CREATE)
    assert not budget.acquire(MutationKind.CREATE)
    assert budget.acquire(MutationKind.CANCEL)


def test_m14_static_security_boundary() -> None:
    source = "\n".join(path.read_text() for path in Path("services/demo_execution").glob("*.py"))
    lowered = source.lower()
    assert "external-api.kalshi.com" not in lowered
    assert "api.elections.kalshi.com" not in lowered
    assert "production_signer" not in lowered
    assert "document_intelligence" not in lowered and "openai" not in lowered
