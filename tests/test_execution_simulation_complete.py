from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from services.execution_simulation.analytics import (
    concentration,
    drawdown_series,
    event_accounting,
    markout,
    settle,
)
from services.execution_simulation.cross_venue import CrossVenueLegSimulation, LegState
from services.execution_simulation.domain import (
    AdvancementEvidence,
    AdvancementState,
    BacktestRun,
    CandidateSimulation,
    ExecutionDatasetManifest,
    FillState,
    OrderState,
    QueueQuality,
    ReplayFidelity,
    SimulatedFill,
    SimulatedOrder,
    SimulatedOutcome,
    SimulationCase,
    SimulationError,
    StrategyType,
)
from services.execution_simulation.evaluation import (
    EvaluationPeriod,
    ExecutionCalibrationTarget,
    WalkForwardManifest,
    collision_diagnostic,
)
from services.execution_simulation.load import stream_load
from services.execution_simulation.policies import default_policies, fidelity_allows
from services.execution_simulation.replay import (
    FlowKind,
    ReplayBook,
    ReplayFlow,
    expire_partial,
    simulate_maker,
    simulate_taker,
)
from services.opportunity_engine.books import OutcomeSide, RawBidLevel, normalize_binary_book
from services.opportunity_engine.fees import FeePolicy, FeeType
from services.web_dashboard.app import DashboardApp
from services.web_dashboard.store import StateStore

NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)


def fee(effective: datetime = NOW - timedelta(days=1)) -> FeePolicy:
    return FeePolicy(
        "fixture-fee-v1",
        FeeType.FLAT,
        Decimal(1),
        effective,
        None,
        "fixture-only-v1",
        "SYNTHETIC TEST",
        True,
        flat_rate=Decimal(".001"),
    )


def book(yes_bid: str, no_bid: str, quantity: str = "10") -> object:
    return normalize_binary_book(
        (
            RawBidLevel("y", OutcomeSide.YES, Decimal(yes_bid), Decimal(quantity)),
            RawBidLevel("n", OutcomeSide.NO, Decimal(no_bid), Decimal(quantity)),
        )
    )


def flow(
    seconds: float,
    sequence: int,
    kind: FlowKind,
    quantity: str,
    side: OutcomeSide | None = OutcomeSide.YES,
    price: str | None = ".44",
) -> ReplayFlow:
    return ReplayFlow(
        f"e{sequence}",
        NOW + timedelta(seconds=seconds),
        sequence,
        kind,
        side,
        None if price is None else Decimal(price),
        Decimal(quantity),
        f"book-{sequence}",
    )


def test_taker_uses_arrival_book_not_candidate_book() -> None:
    policy = default_policies(NOW)[1]  # BASE arrives 300ms later
    candidate = ReplayBook(NOW, 1, book(".40", ".51"), ReplayFidelity.HIGH_RESOLUTION_BOOK, "b1")
    arrival = ReplayBook(
        NOW + timedelta(milliseconds=250),
        2,
        book(".32", ".40"),  # YES ask has jumped from .49 to .60
        ReplayFidelity.HIGH_RESOLUTION_BOOK,
        "b2",
    )
    order = simulate_taker(
        simulated_order_id="sim-taker",
        candidate_id="frozen-candidate",
        side=OutcomeSide.YES,
        candidate_time=NOW,
        decision_time=NOW,
        quantity=Decimal("1.5"),
        policy=policy,
        books=(candidate, arrival),
        fee_policy=fee(),
        max_book_age=timedelta(seconds=1),
    )
    assert order.arrival_time == NOW + timedelta(milliseconds=300)
    assert order.fills[0].price == Decimal(".60")
    assert order.fills[0].quantity == Decimal("1.5")
    assert order.fills[0].timestamp >= order.arrival_time


def test_taker_fractional_partial_fill_and_effective_fee() -> None:
    policy = default_policies(NOW)[0]
    snapshot = ReplayBook(
        NOW + timedelta(milliseconds=50),
        2,
        book(".5513", ".40", ".25"),
        ReplayFidelity.SEQUENCE_BOOK_AND_TRADES,
        "subpenny",
    )
    order = simulate_taker(
        simulated_order_id="sim-partial",
        candidate_id="c",
        side=OutcomeSide.NO,
        candidate_time=NOW,
        decision_time=NOW,
        quantity=Decimal(".4"),
        policy=policy,
        books=(snapshot,),
        fee_policy=fee(),
        max_book_age=timedelta(seconds=1),
    )
    assert order.state == OrderState.PARTIALLY_FILLED_SIMULATION
    assert order.fills[0].price == Decimal(".4487")
    assert order.fills[0].quantity == Decimal(".25")
    assert order.remaining_quantity == Decimal(".15")


def test_maker_aggregate_queue_is_not_exact_and_400_does_not_clear_500() -> None:
    policy = default_policies(NOW)[1]
    order = simulate_maker(
        simulated_order_id="sim-maker",
        candidate_id="c",
        strategy=StrategyType.MAKER_AT_BEST,
        side=OutcomeSide.YES,
        price=Decimal(".44"),
        quantity=Decimal(1),
        displayed_ahead=Decimal(500),
        candidate_time=NOW,
        decision_time=NOW,
        policy=policy,
        flows=(flow(1, 1, FlowKind.TRADE, "400"),),
        fee_policy=fee(),
    )
    assert order.state == OrderState.NO_FILL_SIMULATION
    assert order.queue_quality == QueueQuality.CONSERVATIVE_QUEUE_ASSUMPTION
    assert order.queue_ahead == Decimal("150")  # includes BASE competing-flow reserve


def test_level_reduction_credit_is_scenario_specific_not_fill() -> None:
    reduction = (flow(1, 1, FlowKind.LEVEL_REDUCTION, "500"),)
    optimistic, base, adverse = default_policies(NOW)
    kwargs = dict(
        candidate_id="c",
        strategy=StrategyType.MAKER_AT_BEST,
        side=OutcomeSide.YES,
        price=Decimal(".44"),
        quantity=Decimal(1),
        displayed_ahead=Decimal(500),
        candidate_time=NOW,
        decision_time=NOW,
        flows=reduction,
        fee_policy=fee(),
    )
    opt = simulate_maker(simulated_order_id="opt", policy=optimistic, **kwargs)
    bas = simulate_maker(simulated_order_id="base", policy=base, **kwargs)
    adv = simulate_maker(simulated_order_id="adv", policy=adverse, **kwargs)
    assert not opt.fills and not bas.fills and not adv.fills
    assert opt.queue_ahead < bas.queue_ahead < adv.queue_ahead


def test_maker_direction_price_and_pre_arrival_events_cannot_fill() -> None:
    policy = default_policies(NOW)[1]
    flows = (
        flow(0.1, 1, FlowKind.TRADE, "10"),  # before 300ms arrival
        flow(1, 2, FlowKind.TRADE, "10", OutcomeSide.NO, ".44"),
        flow(2, 3, FlowKind.TRADE, "10", OutcomeSide.YES, ".45"),
    )
    order = simulate_maker(
        simulated_order_id="directions",
        candidate_id="c",
        strategy=StrategyType.MAKER_AT_BEST,
        side=OutcomeSide.YES,
        price=Decimal(".44"),
        quantity=Decimal(1),
        displayed_ahead=Decimal(0),
        candidate_time=NOW,
        decision_time=NOW,
        policy=policy,
        flows=flows,
        fee_policy=fee(),
    )
    assert not order.fills


def test_cancel_fill_race_retains_fill_and_order_level_fee_accumulation() -> None:
    policy = default_policies(NOW)[1]
    rows = (
        flow(1, 1, FlowKind.SOURCE_FAILURE, "0", None, None),
        flow(1.1, 2, FlowKind.TRADE, ".2"),  # inside 300ms cancel latency
        flow(1.2, 3, FlowKind.TRADE, ".7"),
        flow(2, 4, FlowKind.TRADE, "1"),  # after cancel effective; ignored
    )
    order = simulate_maker(
        simulated_order_id="cancel-race",
        candidate_id="c",
        strategy=StrategyType.MAKER_AT_BEST,
        side=OutcomeSide.YES,
        price=Decimal(".44"),
        quantity=Decimal(1),
        displayed_ahead=Decimal(0),
        candidate_time=NOW,
        decision_time=NOW,
        policy=policy,
        flows=rows,
        fee_policy=fee(),
    )
    assert order.state == OrderState.PARTIALLY_FILLED_SIMULATION
    assert sum((row.quantity for row in order.fills), Decimal(0)) == Decimal(".9")
    assert sum((row.fee for row in order.fills), Decimal(0)) == Decimal(".0009")
    assert expire_partial(order).state == OrderState.EXPIRED_SIMULATION


def test_replay_gap_while_resting_is_unknown_not_profitable() -> None:
    order = simulate_maker(
        simulated_order_id="gap",
        candidate_id="c",
        strategy=StrategyType.MAKER_AT_BEST,
        side=OutcomeSide.YES,
        price=Decimal(".44"),
        quantity=Decimal(1),
        displayed_ahead=Decimal(0),
        candidate_time=NOW,
        decision_time=NOW,
        policy=default_policies(NOW)[1],
        flows=(flow(1, 1, FlowKind.GAP, "0", None, None),),
        fee_policy=fee(),
    )
    assert order.state == OrderState.EXECUTION_OUTCOME_UNKNOWN
    assert order.invalidation_reason == "unresolved replay gap"
    assert not fidelity_allows(StrategyType.MAKER_AT_BEST, ReplayFidelity.CANDLE_ONLY)


def test_markout_settlement_no_fill_and_drawdown_are_distinct() -> None:
    fill_row = SimulatedFill(
        "f",
        "o",
        NOW,
        "YES",
        "BID",
        Decimal(".5"),
        Decimal(".2"),
        True,
        Decimal(0),
        "trade",
        Decimal(".001"),
        Decimal(".2"),
        Decimal(0),
        "book",
        1,
        SimulationCase.BASE,
    )
    order = SimulatedOrder(
        "o",
        "c",
        StrategyType.MAKER_AT_BEST,
        SimulationCase.BASE,
        NOW - timedelta(seconds=1),
        NOW - timedelta(seconds=1),
        NOW - timedelta(milliseconds=500),
        NOW - timedelta(milliseconds=100),
        Decimal(".2"),
        Decimal(0),
        Decimal(".5"),
        OrderState.FILLED_SIMULATION,
        QueueQuality.CONSERVATIVE_QUEUE_ASSUMPTION,
        Decimal(0),
        (fill_row,),
    )
    observation = markout(
        fill_row, OutcomeSide.YES, NOW + timedelta(seconds=1), Decimal(".44"), Decimal(".43")
    )
    assert observation.normalized_markout == Decimal("-.06")
    result = settle(order, OutcomeSide.YES, NOW + timedelta(days=1))
    assert result.gross_pnl == Decimal(".1")
    assert result.net_pnl == Decimal(".099")
    series = drawdown_series(((NOW, Decimal(2)), (NOW + timedelta(seconds=1), Decimal(-3))))
    assert series[-1].drawdown == Decimal(3)


def test_no_fill_has_no_counterfactual_pnl() -> None:
    order = SimulatedOrder(
        "o",
        "c",
        StrategyType.MAKER_AT_BEST,
        SimulationCase.BASE,
        NOW,
        NOW,
        NOW,
        NOW + timedelta(milliseconds=1),
        Decimal(1),
        Decimal(1),
        Decimal(".4"),
        OrderState.NO_FILL_SIMULATION,
        QueueQuality.CONSERVATIVE_QUEUE_ASSUMPTION,
        Decimal(1),
        (),
    )
    result = settle(order, OutcomeSide.YES, NOW + timedelta(days=1))
    assert result.net_pnl is None and result.gross_pnl is None


def test_advancement_requires_all_scenarios_base_adverse_and_evidence() -> None:
    outcomes = tuple(
        SimulatedOutcome(
            case,
            Decimal(1),
            Decimal(".4"),
            Decimal(".01"),
            Decimal(".01"),
            Decimal(".01"),
            value,
            FillState.FULL_FILL,
        )
        for case, value in (
            (SimulationCase.OPTIMISTIC, Decimal(".2")),
            (SimulationCase.BASE, Decimal(".05")),
            (SimulationCase.ADVERSE, Decimal("-.01")),
        )
    )
    evidence = AdvancementEvidence(
        100, Decimal(100), Decimal(10), Decimal(20), Decimal(".1"), Decimal(".3"), True, True, False
    )
    assert (
        CandidateSimulation.assess("c", outcomes, evidence).research_advancement
        == AdvancementState.FAIL
    )
    passing = (
        *outcomes[:-1],
        SimulatedOutcome(
            SimulationCase.ADVERSE,
            Decimal(1),
            Decimal(".5"),
            Decimal(".01"),
            Decimal(".02"),
            Decimal(".01"),
            Decimal(".01"),
            FillState.FULL_FILL,
        ),
    )
    assert (
        CandidateSimulation.assess("c", passing, evidence).research_advancement
        == AdvancementState.PASSES_EXECUTION_RESEARCH_GATE
    )
    low_sample = AdvancementEvidence(
        7, Decimal(7), Decimal(0), Decimal(20), Decimal(".1"), Decimal(".3"), True, True, False
    )
    assert (
        CandidateSimulation.assess("c", passing, low_sample).research_advancement
        == AdvancementState.FAIL
    )


def test_walk_forward_event_purge_and_variant_manifest() -> None:
    train = EvaluationPeriod("training", NOW, NOW + timedelta(days=30))
    validate = EvaluationPeriod("validation", NOW + timedelta(days=30), NOW + timedelta(days=60))
    promote = EvaluationPeriod("promotion", NOW + timedelta(days=60), NOW + timedelta(days=90))
    manifest = WalkForwardManifest(
        train, validate, promote, ("rest-5", "rest-30"), "holdout+FDR", (("cpi-1", "training"),)
    )
    assert manifest.multiple_comparison_method == "holdout+FDR"
    with pytest.raises(SimulationError):
        WalkForwardManifest(
            train,
            validate,
            promote,
            ("v",),
            "holdout",
            (("cpi-1", "training"), ("cpi-1", "promotion")),
        )


def test_event_accounting_does_not_count_partial_fills_as_events() -> None:
    accounting = event_accounting(
        (
            ("o1", "m1", "cpi", True, 25),
            ("o2", "m2", "cpi", True, 25),
            ("o3", "m3", "weather", False, 1),
        )
    )
    assert accounting.fills == 51
    assert accounting.unique_events == 2
    assert accounting.effective_sample_size == 1
    assert concentration({"a": Decimal(9), "b": Decimal(1)})[0] == Decimal(".9")


def test_cross_venue_one_leg_reprice_is_explicit_and_non_atomic() -> None:
    row = CrossVenueLegSimulation.evaluate(
        simulation_id="x",
        kalshi_arrival=NOW,
        polymarket_arrival=NOW + timedelta(milliseconds=500),
        kalshi_filled=True,
        polymarket_filled=False,
        kalshi_cost=Decimal(".4"),
        polymarket_cost=None,
        second_leg_repriced=True,
        leg_risk_loss=Decimal(".08"),
        basis_max_loss=Decimal(".2"),
    )
    assert row.state == LegState.SECOND_LEG_REPRICED
    assert row.label == "HYPOTHETICAL CROSS-VENUE SIMULATION"
    assert row.production_influence == 0


def test_calibration_collision_and_immutable_manifests() -> None:
    target = ExecutionCalibrationTarget(
        NOW,
        Decimal(".6"),
        False,
        Decimal(0),
        None,
        None,
        Decimal(".01"),
        Decimal(".02"),
        Decimal(".001"),
        Decimal(".001"),
    )
    assert target.fill_brier == Decimal(".36")
    assert collision_diagnostic("M", Decimal(".4"), "YES", ("a", "b"), Decimal(1)).collision
    values = dict(
        opportunity_dataset_id="opp",
        candidate_policy="candidate-v1",
        strategy_policy="taker-v1",
        execution_policy_versions=("opt", "base", "adverse"),
        replay_dataset="replay",
        start_at=NOW,
        end_at=NOW + timedelta(days=1),
        markets=("M",),
        events=("E",),
        code_sha="sha",
        source_config="source",
        model_config="model",
        learning_config="learning",
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=1),
    )
    run = BacktestRun.freeze(**values)
    assert run.run_id == run.content_hash
    with pytest.raises(FrozenInstanceError):
        run.code_sha = "later"  # type: ignore[misc]
    manifest = ExecutionDatasetManifest.freeze(
        replay_dataset="r",
        forecast_versions=("f",),
        learning_configuration="l",
        opportunity_dataset="o",
        execution_policies=("opt", "base", "adv"),
        latency_policy="lat",
        fee_policies=("fee",),
        queue_model="aggregate-assumption",
        fill_model="unvalidated",
        cancellation_policy="c",
        code_sha="sha",
        start_at=NOW,
        end_at=NOW + timedelta(days=1),
        gap_policy="fail-closed",
    )
    assert (
        manifest.content_hash
        == ExecutionDatasetManifest.freeze(
            **{key: value for key, value in manifest.as_manifest().items() if key != "content_hash"}
        ).content_hash
    )


def test_simulated_order_invariants_and_no_exact_hypothetical_queue() -> None:
    with pytest.raises(SimulationError):
        SimulatedOrder(
            "o",
            "c",
            StrategyType.MAKER_AT_BEST,
            SimulationCase.BASE,
            NOW,
            NOW,
            NOW,
            NOW,
            Decimal(1),
            Decimal(1),
            Decimal(".4"),
            OrderState.RESTING_SIMULATION,
            QueueQuality.OBSERVED_OWN_ORDER_QUEUE,
            Decimal(1),
            (),
        )
    with pytest.raises(SimulationError):
        SimulatedOrder(
            "o",
            "c",
            StrategyType.TAKER_NOW,
            SimulationCase.BASE,
            NOW,
            NOW,
            NOW,
            NOW - timedelta(seconds=1),
            Decimal(1),
            Decimal(1),
            None,
            OrderState.NO_FILL_SIMULATION,
            QueueQuality.UNKNOWN,
            None,
            (),
        )


def test_large_streaming_fixture_is_deterministic() -> None:
    first = stream_load()
    second = stream_load()
    assert first == second
    assert first.attempts == 100_000
    assert first.unique_events == 5_000
    assert first.partial_fills and first.cancellations and first.gaps and first.pauses


def test_m11_has_no_execution_or_risk_authority() -> None:
    source = "\n".join(
        path.read_text() for path in Path("services/execution_simulation").glob("*.py")
    )
    forbidden = (
        "RequestSigner",
        "kalshi_account_gateway",
        "risk_engine",
        "place_order(",
        "submit_order(",
        "execute(",
        "write_credential",
        "arm_trading",
    )
    assert not any(term in source for term in forbidden)


def test_backtest_ui_labels_scenarios_and_real_evidence_honestly(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    summary = store.execution_research_summary()
    assert summary["real_observations"] == "NOT VERIFIED / NONE"
    body = DashboardApp._backtests(summary)
    assert "HISTORICAL SIMULATION" in body
    assert "NOT REAL TRADING RESULTS" in body
    assert "INSUFFICIENT REAL EVIDENCE" in body
    assert "PRODUCTION INFLUENCE: NONE" in body
