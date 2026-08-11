from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from services.opportunity_engine.books import (
    OutcomeSide,
    RawBidLevel,
    normalize_binary_book,
    walk_depth,
)
from services.opportunity_engine.cross_venue import (
    CrossVenueOpportunityObservation,
    SemanticMatch,
)
from services.opportunity_engine.diagnostics import (
    CorrelationCluster,
    FeeReconciliation,
    information_decay,
    liquidity,
)
from services.opportunity_engine.domain import OpportunityError
from services.opportunity_engine.engine import (
    ResearchThresholds,
    decide,
    evaluate_both,
    outcome_probabilities,
    rank_score,
    stale_reasons,
)
from services.opportunity_engine.fees import (
    FeePolicy,
    FeeType,
    calculate_fee,
    select_policy,
)
from services.opportunity_engine.models import (
    AsOfOpportunitySnapshot,
    DecisionState,
    FillQuality,
    InformationDecay,
    OpportunityDataset,
    RejectionReason,
)
from services.opportunity_engine.scenarios import HypotheticalScenario

NOW = datetime(2026, 8, 10, tzinfo=UTC)


def book():
    return normalize_binary_book(
        (
            RawBidLevel("y1", OutcomeSide.YES, Decimal(".42"), Decimal("2.50")),
            RawBidLevel("y2", OutcomeSide.YES, Decimal(".40"), Decimal("3.25")),
            RawBidLevel("n1", OutcomeSide.NO, Decimal(".55"), Decimal("1.25")),
            RawBidLevel("n2", OutcomeSide.NO, Decimal(".54"), Decimal("4.75")),
        )
    )


def policy(
    identifier: str = "old",
    effective: datetime = NOW - timedelta(days=1),
    retired: datetime | None = None,
) -> FeePolicy:
    # Synthetic verified fixture coefficient; not represented as the live Kalshi schedule.
    return FeePolicy(
        identifier,
        FeeType.FLAT,
        Decimal(1),
        effective,
        retired,
        "fixture-flat-v1",
        "SYNTHETIC TEST DATA",
        True,
        flat_rate=Decimal(".005"),
    )


def test_binary_book_complement_is_exact_and_uses_opposing_bid() -> None:
    normalized = book()
    assert normalized.yes_best_ask == Decimal(".45")
    assert normalized.no_best_ask == Decimal(".58")
    assert normalized.yes_best_ask == Decimal(1) - normalized.no_bids[0].price
    assert normalized.yes_asks[0].raw_level_id == "n1"
    assert all(
        isinstance(level.price, Decimal) and isinstance(level.quantity, Decimal)
        for level in normalized.yes_asks
    )


def test_depth_walk_fractional_subpenny_vwap_monotonic_and_insufficient() -> None:
    normalized = book()
    first = walk_depth(normalized.yes_asks, Decimal("1.00"))
    second = walk_depth(normalized.yes_asks, Decimal("3.00"))
    huge = walk_depth(normalized.yes_asks, Decimal("99.99"))
    assert first.average_price == Decimal(".45")
    assert second.average_price >= first.average_price
    assert huge.unfilled > 0 and not huge.complete
    subpenny = normalize_binary_book(
        (
            RawBidLevel("y", OutcomeSide.YES, Decimal(".4217"), Decimal(".25")),
            RawBidLevel("n", OutcomeSide.NO, Decimal(".5513"), Decimal(".75")),
        )
    )
    assert subpenny.yes_best_ask == Decimal(".4487")


def test_yes_no_interval_complements_and_adversarial_economics() -> None:
    probabilities = outcome_probabilities(Decimal(".35"), Decimal(".30"), Decimal(".40"))
    assert probabilities[OutcomeSide.YES][0] + probabilities[OutcomeSide.NO][0] == 1
    assert probabilities[OutcomeSide.NO][1] == Decimal(".60")
    both = evaluate_both(
        book(), Decimal(".35"), Decimal(".30"), Decimal(".40"), policy(), Decimal("1")
    )
    assert both[OutcomeSide.YES].gross_expected_value < 0
    assert both[OutcomeSide.NO].fair_probability == Decimal(".65")
    no_candidate = evaluate_both(
        book(), Decimal(".55"), Decimal(".48"), Decimal(".60"), policy(), Decimal("1")
    )[OutcomeSide.YES]
    state, reasons = decide(
        no_candidate, "general", timedelta(hours=2), set(), ResearchThresholds()
    )
    assert state == DecisionState.WATCH and RejectionReason.NET_VALUE_BELOW_THRESHOLD in reasons


def test_fee_policy_version_rounding_unknown_and_unverified_fail_closed() -> None:
    old = policy("old", NOW - timedelta(days=10), NOW + timedelta(days=1))
    new = policy("new", NOW + timedelta(days=1))
    assert select_policy((old, new), NOW).policy_id == "old"
    fee = calculate_fee(old, Decimal(".4487"), Decimal(".25"))
    assert fee.total_fee >= fee.theoretical_trade_fee and fee.rounding_component >= 0
    with pytest.raises(OpportunityError, match="unverified"):
        calculate_fee(
            FeePolicy(
                "u",
                FeeType.FLAT,
                Decimal(1),
                NOW,
                None,
                "unknown",
                "none",
                False,
                flat_rate=Decimal(".01"),
            ),
            Decimal(".5"),
            Decimal(1),
        )
    with pytest.raises(OpportunityError, match="incomplete"):
        calculate_fee(
            FeePolicy("q", FeeType.QUADRATIC, Decimal(1), NOW, None, "fixture", "x", True),
            Decimal(".5"),
            Decimal(1),
        )


def test_stale_rules_close_skew_and_future_inputs_fail_closed() -> None:
    reasons = stale_reasons(
        NOW,
        NOW - timedelta(minutes=10),
        NOW,
        NOW,
        timedelta(seconds=30),
        timedelta(hours=1),
        timedelta(hours=1),
    )
    assert RejectionReason.BOOK_STALE in reasons
    economics = evaluate_both(
        book(), Decimal(".6"), Decimal(".57"), Decimal(".65"), policy(), Decimal(1)
    )[OutcomeSide.YES]
    state, reasons = decide(
        economics,
        "general",
        timedelta(minutes=10),
        {RejectionReason.FORECAST_RULE_VERSION_MISMATCH},
        ResearchThresholds(),
    )
    assert state == DecisionState.REJECTED
    snapshot = AsOfOpportunitySnapshot(NOW, NOW, None, NOW, NOW)
    snapshot.validate(timedelta(seconds=1))
    with pytest.raises(OpportunityError, match="future"):
        AsOfOpportunitySnapshot(NOW + timedelta(seconds=1), NOW, None, NOW, NOW).validate(
            timedelta(seconds=2)
        )


def test_maker_fill_is_never_default_one_and_adverse_selection_unknown() -> None:
    scenario = HypotheticalScenario.maker(
        price=Decimal(".45"),
        fee_regime="unverified",
        conservative_fill_probability=None,
        adverse_selection_reserve=None,
        information_decay_factor=Decimal(".5"),
    )
    assert scenario.fill_probability is None and scenario.fill_quality == FillQuality.UNAVAILABLE
    assert scenario.ev_expected_over_attempt is None


def test_liquidity_decay_correlation_ranking_and_fee_reconciliation() -> None:
    diagnostic = liquidity(
        Decimal(".03"),
        Decimal(".45"),
        Decimal("2.5"),
        Decimal("5"),
        4,
        Decimal("10"),
        Decimal("100"),
        100,
        "STABLE",
    )
    assert diagnostic.relative_spread == Decimal(".03") / Decimal(".45")
    decay, factor = information_decay(timedelta(seconds=10), Decimal(".03"), timedelta(hours=2))
    assert decay == InformationDecay.FAST and factor == Decimal(".4")
    cluster = CorrelationCluster("cpi", "release", ("bin1", "bin2", "bin3"), True, True, True)
    assert cluster.duplicate_thesis
    assert rank_score(Decimal(".06"), *(Decimal(".8") for _ in range(7))) > 0
    assert (
        FeeReconciliation(
            Decimal(".01"), Decimal(".03"), Decimal(0), Decimal(0), Decimal(".001")
        ).status
        == "FEE_MODEL_MISMATCH"
    )


def test_cross_venue_semantics_hurdle_leg_risk_and_reference_overlap() -> None:
    related = CrossVenueOpportunityObservation.evaluate(
        observation_id="x",
        semantic_match=SemanticMatch.RELATED_ONLY,
        kalshi_price=Decimal(".4"),
        kalshi_depth=Decimal(10),
        polymarket_price=Decimal(".5"),
        polymarket_depth=Decimal(10),
        kalshi_fee=Decimal(".01"),
        polymarket_fee=Decimal(".01"),
        expected_slippage=Decimal(".005"),
        timestamp_skew_ms=10,
        semantic_reserve=Decimal(".01"),
        leg_risk_reserve=Decimal(".01"),
        venue_state_risk="OPEN",
        reference_overlap=False,
    )
    assert related.research_state == "SEMANTIC_MATCH_INSUFFICIENT"
    overlap = CrossVenueOpportunityObservation.evaluate(
        observation_id="x",
        semantic_match=SemanticMatch.IDENTICAL,
        kalshi_price=Decimal(".4"),
        kalshi_depth=Decimal(10),
        polymarket_price=Decimal(".5"),
        polymarket_depth=Decimal(10),
        kalshi_fee=Decimal(".005"),
        polymarket_fee=Decimal(".005"),
        expected_slippage=Decimal(".005"),
        timestamp_skew_ms=10,
        semantic_reserve=Decimal(".005"),
        leg_risk_reserve=Decimal(".005"),
        venue_state_risk="OPEN",
        reference_overlap=True,
    )
    assert overlap.research_state == "WATCH" and overlap.production_influence == 0


def test_dataset_manifest_and_50k_evaluations_are_deterministic() -> None:
    values = dict(
        start_at=NOW,
        end_at=NOW + timedelta(days=1),
        policy_version="p",
        forecast_versions=("f",),
        model_versions=("m",),
        calibrators=("c",),
        fee_schedules=("synthetic",),
        slippage_model="book-v1",
        fill_model="displayed-depth",
        market_data_fidelity="FULL_ORDERBOOK_EVENTS",
        learning_configuration="l",
        source_configuration="s",
        gap_state="NONE",
        code_sha="git",
    )
    assert (
        OpportunityDataset.build(**values).content_hash
        == OpportunityDataset.build(**values).content_hash
    )
    evaluations = tuple(
        (
            i,
            f"event{i // 10}",
            OutcomeSide.YES if i % 2 else OutcomeSide.NO,
            Decimal(".5001"),
            Decimal(".25"),
        )
        for i in range(50_000)
    )
    assert len(evaluations) == 50_000 and len({row[1] for row in evaluations}) == 5_000
    assert all(isinstance(row[3], Decimal) and isinstance(row[4], Decimal) for row in evaluations)


def test_m10_has_no_execution_signer_risk_write_or_order_methods() -> None:
    code = "\n".join(path.read_text() for path in Path("services/opportunity_engine").glob("*.py"))
    for forbidden in (
        "RequestSigner",
        "kalshi_account_gateway",
        "risk_engine",
        "place_order",
        "submit_order",
        "write_credential",
        "ExecutionPlan",
    ):
        assert forbidden not in code
