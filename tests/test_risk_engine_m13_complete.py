from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from services.risk_engine.authorization import (
    AuthorizationError,
    AuthorizationStore,
    FixedClock,
)
from services.risk_engine.domain import (
    ComplianceState,
    EconomicAction,
    KillCategory,
    KillLevel,
    Ownership,
    PortfolioRiskSnapshot,
    ReconciliationStatus,
    RequiredOrderGroupPolicy,
    RiskDecisionState,
    RiskDomainError,
    RiskIntent,
    RiskReason,
)
from services.risk_engine.engine import RiskEvaluationContext, evaluate_risk
from services.risk_engine.invariants import NewRiskReadiness
from services.risk_engine.ledger import (
    ExperimentCapitalLedger,
    LedgerEntry,
    LedgerEntryType,
    available_active_capital,
    project_full_fill,
)
from services.risk_engine.load import stream_risk_load
from services.risk_engine.policy import RiskPolicy, select_risk_policy
from services.risk_engine.reconciliation import (
    AccountRiskItem,
    ReconciliationInput,
    reconcile,
)
from services.risk_engine.states import (
    KillState,
    RiskProductState,
    SafetyState,
    evaluate_loss_windows,
    resolve_product_state,
)

NOW = datetime(2026, 8, 10, 16, tzinfo=UTC)
POLICY = RiskPolicy()


def intent(
    *,
    client: str = "kalsh3-v1-00000001",
    maximum_loss: str = "2",
    commitment: str = "0.45",
    action: EconomicAction = EconomicAction.BUY_YES_OUTCOME,
    reduce_only: bool = False,
    quantity: str = "1",
    market: str = "M",
    event: str = "E",
) -> RiskIntent:
    return RiskIntent.freeze(
        intent_id=f"intent-{client}",
        created_at=NOW,
        market_ticker=market,
        event_id=event,
        correlation_cluster_id="C",
        rules_version="r1",
        rules_hash="rules-hash",
        contract_interpretation_version="i1",
        candidate_id="candidate",
        forecast_id="forecast",
        economic_action=action,
        outcome_side="YES",
        book_side="ASK",
        price=Decimal(".45"),
        quantity=Decimal(quantity),
        maximum_expected_fee=Decimal(".01"),
        maximum_expected_cash_commitment=Decimal(commitment),
        maximum_loss_if_filled=Decimal(maximum_loss),
        order_style="LIMIT",
        time_in_force_policy="GOOD_TILL_CANCELED",
        expires_at=NOW + timedelta(seconds=30),
        post_only=False,
        cancel_order_on_pause=True,
        reduce_only=reduce_only,
        self_trade_prevention="CANCEL_NEWEST",
        required_order_group_policy="family-v1",
        client_order_id=client,
        account="PRIMARY",
        subaccount=0,
    )


def snapshot(
    *,
    market: str = "0",
    event: str = "0",
    aggregate: str = "0",
    available: str = "300",
    equity: str = "1000",
    reconciliation: ReconciliationStatus = ReconciliationStatus.RECONCILED,
    unknown_orders: int = 0,
    external_positions: int = 0,
    external_orders: int = 0,
    exposure_exchange: str = "0",
    exposure_calculated: str = "0",
) -> PortfolioRiskSnapshot:
    return PortfolioRiskSnapshot.freeze(
        observed_at=NOW,
        account_snapshot_version="account-v1",
        reconciliation_version="reconcile-v1",
        cash=Decimal(equity),
        portfolio_value=Decimal(equity),
        account_equity=Decimal(equity),
        protected_reserve=Decimal("700"),
        active_capital_available=Decimal(available),
        current_market_risk=Decimal(market),
        current_event_risk=Decimal(event),
        current_aggregate_risk=Decimal(aggregate),
        resting_order_potential_risk=Decimal(0),
        projected_market_risk=Decimal(market),
        projected_event_risk=Decimal(event),
        projected_aggregate_risk=Decimal(aggregate),
        realized_daily_pnl=Decimal(0),
        realized_weekly_pnl=Decimal(0),
        realized_monthly_pnl=Decimal(0),
        experiment_equity=Decimal("300"),
        experiment_high_water_mark=Decimal("300"),
        experiment_drawdown=Decimal(0),
        external_positions=external_positions,
        external_orders=external_orders,
        unknown_orders=unknown_orders,
        account_fresh=True,
        reconciliation_status=reconciliation,
        exchange_market_exposure=Decimal(exposure_exchange),
        exchange_event_exposure=Decimal(exposure_exchange),
        independently_calculated_market_exposure=Decimal(exposure_calculated),
        independently_calculated_event_exposure=Decimal(exposure_calculated),
    )


def all_ready() -> NewRiskReadiness:
    return NewRiskReadiness(**{field.name: True for field in fields(NewRiskReadiness)})


def losses(
    *,
    daily: str = "0",
    weekly: str = "0",
    monthly: str = "0",
    drawdown: str = "0",
    prior_weekly: bool = False,
    prior_monthly: bool = False,
    prior_halt: bool = False,
) -> object:
    return evaluate_loss_windows(
        now=NOW,
        realized_daily_pnl=Decimal(daily),
        realized_weekly_pnl=Decimal(weekly),
        realized_monthly_pnl=Decimal(monthly),
        drawdown=Decimal(drawdown),
        policy=POLICY,
        prior_weekly_review=prior_weekly,
        prior_monthly_review=prior_monthly,
        prior_experiment_halt=prior_halt,
    )


def safety(*, loss_state: object | None = None) -> SafetyState:
    return SafetyState(
        False,
        None,
        ComplianceState.CLEAR,
        ReconciliationStatus.RECONCILED,
        tuple(KillState(category, KillLevel.NORMAL, "healthy", NOW) for category in KillCategory),
        loss_state or losses(),  # type: ignore[arg-type]
    )


def context(*, safety_state: SafetyState | None = None) -> RiskEvaluationContext:
    return RiskEvaluationContext(
        market_data_version="book-v1",
        loss_state_version="loss-v1",
        compliance_state_version="compliance-v1",
        kill_state_version="kills-v1",
        expected_rules_hash="rules-hash",
        readiness=all_ready(),
        safety=safety_state or safety(),
        order_group=RequiredOrderGroupPolicy("family-v1", True, 0, Decimal("10"), True, True),
        client_order_id_unique=True,
        client_order_id_namespace_valid=True,
        conflicting_bot_order=False,
        authorization_service_available=True,
    )


def projection(value: RiskIntent, snap: PortfolioRiskSnapshot, **kwargs: object) -> object:
    return project_full_fill(
        intent=value,
        current_market_risk=snap.current_market_risk or Decimal(0),
        current_event_risk=snap.current_event_risk or Decimal(0),
        current_aggregate_risk=snap.current_aggregate_risk or Decimal(0),
        existing_resting_market_risk=Decimal(str(kwargs.get("resting_market", "0"))),
        existing_resting_event_risk=Decimal(str(kwargs.get("resting_event", "0"))),
        existing_resting_aggregate_risk=Decimal(str(kwargs.get("resting_aggregate", "0"))),
        directional_liability_increases=bool(kwargs.get("directional_increase", True)),
    )


def decision(
    value: RiskIntent,
    snap: PortfolioRiskSnapshot,
    *,
    context_value: RiskEvaluationContext | None = None,
    projection_value: object | None = None,
) -> object:
    return evaluate_risk(
        intent=value,
        snapshot=snap,
        projection=projection_value or projection(value, snap),  # type: ignore[arg-type]
        policy=POLICY,
        context=context_value or context(),
        now=NOW,
    )


def test_clean_risk_pass_is_next_gate_only() -> None:
    result = decision(intent(), snapshot())
    assert result.state == RiskDecisionState.PASS_NEXT_GATE
    assert result.display_result == "RISK CHECK PASSED"
    assert not result.production_write_authorized
    assert "APPROVED" not in result.display_result and "EXECUTE" not in result.display_result


def test_full_fill_limits_ignore_expected_fill_probability() -> None:
    value = intent(maximum_loss="2")
    result = decision(value, snapshot(market="9"))
    assert result.state == RiskDecisionState.REJECT
    assert RiskReason.MARKET_RISK_EXCEEDED in result.reasons
    assert project_full_fill(
        intent=value,
        current_market_risk=Decimal(9),
        current_event_risk=Decimal(0),
        current_aggregate_risk=Decimal(0),
        existing_resting_market_risk=Decimal(0),
        existing_resting_event_risk=Decimal(0),
        existing_resting_aggregate_risk=Decimal(0),
    ).market_risk == Decimal(11)


def test_resting_event_and_aggregate_risk_are_included() -> None:
    value = intent(maximum_loss="10")
    snap = snapshot(event="10", aggregate="85")
    projected = projection(
        value, snap, resting_event="10", resting_aggregate="10", resting_market="0"
    )
    result = decision(value, snap, projection_value=projected)
    assert RiskReason.EVENT_RISK_EXCEEDED in result.reasons
    assert RiskReason.AGGREGATE_RISK_EXCEEDED in result.reasons


def test_reserve_and_active_cap_shrink_after_loss() -> None:
    assert available_active_capital(
        account_equity=Decimal(900),
        committed=Decimal(0),
        pending_commitments=Decimal(0),
        policy=POLICY,
    ) == Decimal(200)
    assert available_active_capital(
        account_equity=Decimal(1200),
        committed=Decimal(0),
        pending_commitments=Decimal(0),
        policy=POLICY,
    ) == Decimal(300)
    result = decision(intent(commitment="201"), snapshot(equity="900", available="200"))
    assert RiskReason.ACTIVE_POOL_EXCEEDED in result.reasons


def test_ledger_ignores_simulation_and_external_activity() -> None:
    entries = (
        LedgerEntry(
            "real", NOW, LedgerEntryType.REALIZED_PNL, Decimal("-20"), Ownership.BOT_OWNED, "fill"
        ),
        LedgerEntry(
            "sim",
            NOW,
            LedgerEntryType.REALIZED_PNL,
            Decimal("500"),
            Ownership.BOT_OWNED,
            "simulation",
            simulation=True,
        ),
        LedgerEntry(
            "manual",
            NOW,
            LedgerEntryType.REALIZED_PNL,
            Decimal("100"),
            Ownership.EXTERNAL_KNOWN,
            "manual",
        ),
        LedgerEntry("fee", NOW, LedgerEntryType.FEE, Decimal("1"), Ownership.BOT_OWNED, "fee"),
    )
    ledger = ExperimentCapitalLedger.build(entries)
    assert ledger.experiment_realized_pnl == Decimal("-20")
    assert ledger.experiment_equity == Decimal(279)
    assert ledger.drawdown == Decimal(21)
    with pytest.raises(RiskDomainError):
        ExperimentCapitalLedger.build((*entries, entries[0]))


def test_reconciliation_scopes_subaccount_zero_and_holds_unknowns() -> None:
    rows = (
        AccountRiskItem("a0", 0, "ORDER", Ownership.BOT_OWNED, "M", "E", Decimal(1), "kalsh3-v1-a"),
        AccountRiskItem("a1", 1, "ORDER", Ownership.EXTERNAL_UNKNOWN, "X", "X", Decimal(99), None),
    )
    base = ReconciliationInput(
        NOW,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        True,
        rows,
        Decimal(1),
        Decimal(1),
        Decimal(1),
        Decimal(1),
    )
    clean = reconcile(base, now=NOW)
    assert clean.status == ReconciliationStatus.RECONCILED
    assert {row.subaccount for row in clean.scoped_items} == {0}
    unknown = reconcile(
        replace(
            base,
            items=(
                *rows,
                AccountRiskItem(
                    "manual", 0, "ORDER", Ownership.EXTERNAL_UNKNOWN, "M", "E", Decimal(2), None
                ),
            ),
        ),
        now=NOW,
    )
    assert unknown.status == ReconciliationStatus.UNKNOWN_ORDER
    mismatch = reconcile(
        replace(base, exchange_event_exposure=Decimal(14), calculated_event_exposure=Decimal(23)),
        now=NOW,
    )
    assert mismatch.status == ReconciliationStatus.MISMATCH
    assert "EXPOSURE_RECONCILIATION_MISMATCH" in mismatch.issues


def test_missing_and_unknown_financial_state_returns_all_material_reasons() -> None:
    snap = replace(
        snapshot(
            reconciliation=ReconciliationStatus.UNKNOWN_ORDER,
            unknown_orders=1,
            external_positions=1,
            external_orders=1,
        ),
        cash=None,
        active_capital_available=None,
    )
    result = decision(intent(), snap)
    assert {
        RiskReason.RISK_UNCOMPUTABLE,
        RiskReason.CASH_INSUFFICIENT,
        RiskReason.ACTIVE_POOL_EXCEEDED,
        RiskReason.ACCOUNT_NOT_RECONCILED,
        RiskReason.UNKNOWN_ORDER,
        RiskReason.UNKNOWN_POSITION,
        RiskReason.EXTERNAL_ACTIVITY_UNRESOLVED,
    } <= set(result.reasons)
    assert result.state == RiskDecisionState.REJECT


def test_all_four_kill_categories_block_new_risk() -> None:
    killed = tuple(
        KillState(category, KillLevel.KILLED, "fixture", NOW) for category in KillCategory
    )
    state = replace(safety(), kills=killed)
    result = decision(intent(), snapshot(), context_value=context(safety_state=state))
    assert {
        RiskReason.STRATEGY_KILL,
        RiskReason.DATA_KILL,
        RiskReason.PORTFOLIO_KILL,
        RiskReason.CREDENTIAL_KILL,
    } <= set(result.reasons)
    assert result.state == RiskDecisionState.PAUSE


@pytest.mark.parametrize(
    ("changes", "expected_reason", "expected_state"),
    [
        ({"daily": "-20"}, RiskReason.DAILY_LOSS_STOP, RiskDecisionState.PAUSE),
        ({"weekly": "-50"}, RiskReason.WEEKLY_LOSS_STOP, RiskDecisionState.PAUSE),
        ({"monthly": "-100"}, RiskReason.MONTHLY_LOSS_STOP, RiskDecisionState.PAUSE),
        ({"drawdown": "200"}, RiskReason.EXPERIMENT_DRAWDOWN_STOP, RiskDecisionState.HALT),
    ],
)
def test_loss_stops_and_precedence(
    changes: dict[str, str], expected_reason: RiskReason, expected_state: RiskDecisionState
) -> None:
    loss_state = losses(**changes)
    result = decision(
        intent(), snapshot(), context_value=context(safety_state=safety(loss_state=loss_state))
    )
    assert expected_reason in result.reasons
    assert result.state == expected_state


def test_loss_windows_timezone_dst_and_persistence() -> None:
    spring = datetime(2026, 3, 8, 7, 30, tzinfo=UTC)
    fall = datetime(2026, 11, 1, 6, 30, tzinfo=UTC)
    spring_state = evaluate_loss_windows(
        now=spring,
        realized_daily_pnl=Decimal(-20),
        realized_weekly_pnl=Decimal(0),
        realized_monthly_pnl=Decimal(0),
        drawdown=Decimal(0),
        policy=POLICY,
    )
    fall_state = evaluate_loss_windows(
        now=fall,
        realized_daily_pnl=Decimal(0),
        realized_weekly_pnl=Decimal(0),
        realized_monthly_pnl=Decimal(0),
        drawdown=Decimal(0),
        policy=POLICY,
        prior_weekly_review=True,
        prior_monthly_review=True,
        prior_experiment_halt=True,
    )
    assert spring_state.risk_date == spring.astimezone(ZoneInfo("America/New_York")).date()
    assert fall_state.weekly_review_required and fall_state.monthly_review_required
    assert fall_state.experiment_halt_required


def test_daily_window_resets_by_new_york_date_but_history_remains_external() -> None:
    first = evaluate_loss_windows(
        now=NOW,
        realized_daily_pnl=Decimal(-20),
        realized_weekly_pnl=Decimal(-20),
        realized_monthly_pnl=Decimal(-20),
        drawdown=Decimal(20),
        policy=POLICY,
    )
    next_day = evaluate_loss_windows(
        now=NOW + timedelta(days=1),
        realized_daily_pnl=Decimal(0),
        realized_weekly_pnl=Decimal(-20),
        realized_monthly_pnl=Decimal(-20),
        drawdown=Decimal(20),
        policy=POLICY,
        prior_daily_triggered_at=first.daily_triggered_at,
        prior_risk_date=first.risk_date,
    )
    assert next_day.daily_triggered_at is None
    assert first.version != next_day.version


def test_historical_policy_selection_uses_effective_time() -> None:
    old = replace(
        POLICY, policy_id="old", version="0", effective_at=datetime(2025, 1, 1, tzinfo=UTC)
    )
    current = replace(POLICY, effective_at=datetime(2026, 1, 1, tzinfo=UTC))
    assert select_risk_policy((old, current), datetime(2025, 6, 1, tzinfo=UTC)) == old
    assert select_risk_policy((old, current), NOW) == current
    with pytest.raises(ValueError):
        select_risk_policy((), NOW)


def test_state_precedence_compliance_reconciliation_kills_and_halt() -> None:
    base = safety()
    assert resolve_product_state(base) == RiskProductState.LEARNING
    assert (
        resolve_product_state(replace(base, reconciliation=ReconciliationStatus.MISMATCH))
        == RiskProductState.NEEDS_ATTENTION
    )
    assert (
        resolve_product_state(replace(base, compliance=ComplianceState.UNKNOWN))
        == RiskProductState.HALTED
    )
    killed = replace(base, kills=(KillState(KillCategory.DATA, KillLevel.KILLED, "gap", NOW),))
    assert resolve_product_state(killed) == RiskProductState.PAUSED
    assert resolve_product_state(replace(killed, global_halt=True)) == RiskProductState.HALTED


def test_reduce_only_label_is_verified_mathematically() -> None:
    claimed = intent(action=EconomicAction.REDUCE_EXISTING_EXPOSURE, reduce_only=True)
    snap = snapshot(market="5", event="5", aggregate="5")
    not_reducing = projection(claimed, snap, directional_increase=True)
    assert not not_reducing.risk_reducing
    assert (
        RiskReason.NOT_RISK_REDUCING
        in decision(claimed, snap, projection_value=not_reducing).reasons
    )
    reducing = projection(claimed, snap, directional_increase=False)
    assert reducing.risk_reducing and reducing.market_risk == Decimal(3)


def test_intent_requires_decimal_and_subaccount_zero() -> None:
    with pytest.raises(RiskDomainError):
        replace(intent(), subaccount=1)
    with pytest.raises(RiskDomainError):
        replace(intent(), price=0.4)  # type: ignore[arg-type]
    changed = intent(quantity="2")
    assert changed.content_hash != intent().content_hash


def issue_ready_store(tmp_path: Path, value: RiskIntent) -> tuple[AuthorizationStore, object]:
    clock = FixedClock(NOW)
    store = AuthorizationStore(tmp_path / "risk.db", clock)
    store.set_compliance(ComplianceState.CLEAR, actor="OWNER", reason="fixture clear")
    result = decision(value, snapshot())
    return store, result


def test_authorization_is_short_lived_bound_and_single_use(tmp_path: Path) -> None:
    value = intent()
    store, result = issue_ready_store(tmp_path, value)
    authorization = store.issue(
        decision=result,
        intent=value,
        policy=POLICY,
        safety_state_hash="safe-v1",
        base_market_risk=Decimal(0),
        base_event_risk=Decimal(0),
        base_aggregate_risk=Decimal(0),
        requested_market_risk=Decimal(2),
        requested_event_risk=Decimal(2),
        requested_aggregate_risk=Decimal(2),
        cash_commitment=Decimal(".45"),
    )
    assert authorization.expires_at - authorization.created_at <= timedelta(seconds=5)
    assert not authorization.production_execution_authorized
    assert not store.consume(
        authorization.authorization_id,
        intent_hash=intent(quantity="2").content_hash,
        portfolio_state_hash=authorization.portfolio_state_hash,
        safety_state_hash="safe-v1",
    )
    assert store.consume(
        authorization.authorization_id,
        intent_hash=value.content_hash,
        portfolio_state_hash=authorization.portfolio_state_hash,
        safety_state_hash="safe-v1",
    )
    assert not store.consume(
        authorization.authorization_id,
        intent_hash=value.content_hash,
        portfolio_state_hash=authorization.portfolio_state_hash,
        safety_state_hash="safe-v1",
    )


def test_expiry_restart_and_halt_revoke_authorization(tmp_path: Path) -> None:
    value = intent()
    store, result = issue_ready_store(tmp_path, value)
    authorization = store.issue(
        decision=result,
        intent=value,
        policy=POLICY,
        safety_state_hash="safe",
        base_market_risk=Decimal(0),
        base_event_risk=Decimal(0),
        base_aggregate_risk=Decimal(0),
        requested_market_risk=Decimal(2),
        requested_event_risk=Decimal(2),
        requested_aggregate_risk=Decimal(2),
        cash_commitment=Decimal(".45"),
    )
    store.clock.advance(timedelta(seconds=6))  # type: ignore[attr-defined]
    restarted = AuthorizationStore(tmp_path / "risk.db", store.clock)
    assert not restarted.consume(
        authorization.authorization_id,
        intent_hash=value.content_hash,
        portfolio_state_hash=authorization.portfolio_state_hash,
        safety_state_hash="safe",
    )
    second_value = intent(client="kalsh3-v1-00000002")
    restarted.clock.value = NOW  # type: ignore[attr-defined]
    second_result = decision(second_value, snapshot())
    second = restarted.issue(
        decision=second_result,
        intent=second_value,
        policy=POLICY,
        safety_state_hash="safe",
        base_market_risk=Decimal(0),
        base_event_risk=Decimal(0),
        base_aggregate_risk=Decimal(0),
        requested_market_risk=Decimal(2),
        requested_event_risk=Decimal(2),
        requested_aggregate_risk=Decimal(2),
        cash_commitment=Decimal(".45"),
    )
    restarted.activate_global_halt(actor="OWNER", reason="manual safety halt", authenticated=True)
    assert not restarted.consume(
        second.authorization_id,
        intent_hash=second_value.content_hash,
        portfolio_state_hash=second.portfolio_state_hash,
        safety_state_hash="safe",
    )
    with pytest.raises(AuthorizationError):
        restarted.reset_global_halt(actor="OWNER", reason="", strong_reauthenticated=False)


def test_kills_and_long_loss_holds_survive_restart(tmp_path: Path) -> None:
    clock = FixedClock(NOW)
    store = AuthorizationStore(tmp_path / "durable.db", clock)
    store.set_kill_state(
        KillState(KillCategory.DATA, KillLevel.KILLED, "sequence gap", NOW), actor="SYSTEM"
    )
    store.record_loss_state(losses(weekly="-50", monthly="-100", drawdown="200"))  # type: ignore[arg-type]
    restarted = AuthorizationStore(tmp_path / "durable.db", clock)
    summary = restarted.safety_summary()
    assert ("DATA", "KILLED", "sequence gap", NOW.isoformat()) in summary["kill_states"]
    assert summary["weekly_review_required"]
    assert summary["monthly_review_required"]
    assert summary["experiment_halt_required"]


def test_concurrent_reservations_cannot_oversubscribe_market_cap(tmp_path: Path) -> None:
    clock = FixedClock(NOW)
    store = AuthorizationStore(tmp_path / "concurrent.db", clock)
    store.set_compliance(ComplianceState.CLEAR, actor="OWNER", reason="fixture clear")

    def attempt(index: int) -> bool:
        value = intent(client=f"kalsh3-v1-{index:08d}", maximum_loss="6")
        result = decision(value, snapshot())
        try:
            store.issue(
                decision=result,
                intent=value,
                policy=POLICY,
                safety_state_hash="safe",
                base_market_risk=Decimal(0),
                base_event_risk=Decimal(0),
                base_aggregate_risk=Decimal(90),
                requested_market_risk=Decimal(6),
                requested_event_risk=Decimal(6),
                requested_aggregate_risk=Decimal(6),
                cash_commitment=Decimal(".45"),
            )
            return True
        except AuthorizationError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(attempt, (1, 2)))
    assert sum(outcomes) == 1


def test_duplicate_client_id_is_durable(tmp_path: Path) -> None:
    value = intent()
    store, result = issue_ready_store(tmp_path, value)
    kwargs = dict(
        decision=result,
        intent=value,
        policy=POLICY,
        safety_state_hash="safe",
        base_market_risk=Decimal(0),
        base_event_risk=Decimal(0),
        base_aggregate_risk=Decimal(0),
        requested_market_risk=Decimal(1),
        requested_event_risk=Decimal(1),
        requested_aggregate_risk=Decimal(1),
        cash_commitment=Decimal(".45"),
    )
    store.issue(**kwargs)
    with pytest.raises(AuthorizationError):
        store.issue(**kwargs)


def test_client_order_namespace_and_order_group_are_deterministic_gates() -> None:
    bad_client = intent(client="random-id")
    assert RiskReason.DUPLICATE_CLIENT_ORDER_ID in decision(bad_client, snapshot()).reasons
    base = context()
    unavailable = replace(
        base,
        order_group=replace(base.order_group, group_active=False),
    )
    assert (
        RiskReason.ORDER_GROUP_UNAVAILABLE
        in decision(intent(), snapshot(), context_value=unavailable).reasons
    )


def test_same_event_and_unrelated_aggregate_reservations_are_serialized(tmp_path: Path) -> None:
    for mode in ("EVENT", "AGGREGATE"):
        clock = FixedClock(NOW)
        store = AuthorizationStore(tmp_path / f"{mode}.db", clock)
        store.set_compliance(ComplianceState.CLEAR, actor="OWNER", reason="fixture clear")

        def attempt(
            index: int,
            current_mode: str = mode,
            current_store: AuthorizationStore = store,
        ) -> bool:
            value = intent(
                client=f"kalsh3-v1-{current_mode.lower()}{index:08d}",
                maximum_loss="6",
                market=f"M{index}",
                event="SHARED" if current_mode == "EVENT" else f"E{index}",
            )
            result = decision(value, snapshot())
            try:
                current_store.issue(
                    decision=result,
                    intent=value,
                    policy=POLICY,
                    safety_state_hash="safe",
                    base_market_risk=Decimal(0),
                    base_event_risk=Decimal(19 if current_mode == "EVENT" else 0),
                    base_aggregate_risk=Decimal(0 if current_mode == "EVENT" else 90),
                    requested_market_risk=Decimal(6),
                    requested_event_risk=Decimal(6),
                    requested_aggregate_risk=Decimal(6),
                    cash_commitment=Decimal(".45"),
                )
                return True
            except AuthorizationError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = tuple(pool.map(attempt, (1, 2)))
        assert sum(outcomes) == 1


def test_large_scale_fixture_is_streaming_and_deterministic() -> None:
    first = stream_risk_load()
    assert first == stream_risk_load()
    assert first.evaluations == 50_000
    assert first.markets == 5_000 and first.events == 2_000
    assert first.rejected_market and first.rejected_event and first.rejected_aggregate


def test_risk_engine_security_isolation() -> None:
    source = "\n".join(path.read_text() for path in Path("services/risk_engine").glob("*.py"))
    forbidden = (
        "openai",
        "anthropic",
        "document_intelligence",
        "RequestSigner",
        "execution_gateway",
        "kalshi_account_gateway",
        "requests.post",
        "httpx.post",
        "submit_order",
        "place_order",
        "production_write_credential",
        "random.",
    )
    lowered = source.lower()
    assert not any(term.lower() in lowered for term in forbidden)
