from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from services.execution_simulation.analytics import markout, settle
from services.execution_simulation.domain import MarkoutObservation, StrategyType
from services.execution_simulation.policies import default_policies
from services.execution_simulation.replay import FlowKind, ReplayFlow, simulate_maker
from services.market_making_research.domain import (
    ComparisonDirection,
    FairValueCurve,
    FairValueEligibility,
    FairValuePoint,
    InventorySnapshot,
    MarketMakingError,
    QuoteBlocker,
    ShadowMarketSnapshot,
    ShadowQuoteState,
)
from services.market_making_research.evaluation import (
    REQUIRED_MARKOUT_HORIZONS,
    AttemptEvidenceState,
    build_attempt_receipt,
    summarize_attempts,
)
from services.market_making_research.planner import (
    default_shadow_quote_policy,
    plan_shadow_quotes,
)
from services.opportunity_engine.books import OutcomeSide
from services.opportunity_engine.fees import FeePolicy
from services.opportunity_engine.live_economics import MarketEconomicsEvidence
from tests.test_m27i_live_weather_preflight import _authoritative_economics, _raw_market

NOW = datetime(2026, 9, 3, 12, tzinfo=UTC)


def authority(
    yes_bid: str = "0.35", no_bid: str = "0.35"
) -> tuple[MarketEconomicsEvidence, dict[str, object]]:
    raw = _raw_market("MKT-B", "event-1")
    economics, binding, *_ = _authoritative_economics(
        NOW,
        ticker="MKT-B",
        event_ticker="event-1",
        price=yes_bid,
        no_price=no_bid,
        raw_market=raw,
    )
    return economics, binding.to_json()


def point(
    ticker: str, threshold: str, probability: str, *, rules_hash: str | None = None
) -> FairValuePoint:
    value = Decimal(probability)
    if ticker == "MKT-B" and rules_hash is None:
        rules_hash = authority()[0].market_rules_hash
    return FairValuePoint(
        ticker,
        Decimal(threshold),
        value,
        value - Decimal("0.01"),
        value + Decimal("0.01"),
        rules_hash or f"rules-{ticker}",
        f"spec-{ticker}",
    )


def curve(
    eligibility: FairValueEligibility = FairValueEligibility.ELIGIBLE_SHADOW_RESEARCH,
    *,
    rules_hash: str | None = None,
) -> FairValueCurve:
    return FairValueCurve.build(
        event_id="event-1",
        cohort_id="cohort-1",
        comparison=ComparisonDirection.GREATER_THAN,
        points=(
            point("MKT-A", "0", "0.60"),
            point("MKT-B", "1", "0.50", rules_hash=rules_hash),
        ),
        model_id="validated-model",
        model_version="1",
        calibration_id="calibration-1",
        evidence_manifest_id="evidence-1",
        validation_receipt_id="human-reviewed-validation-1",
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=5),
        eligibility=eligibility,
    )


def market(
    *, economics: MarketEconomicsEvidence | None = None, **changes: object
) -> ShadowMarketSnapshot:
    economics = economics or authority()[0]
    values: dict[str, object] = {
        "market_ticker": "MKT-B",
        "event_id": "event-1",
        "rules_hash": economics.market_rules_hash,
        "specification_hash": "spec-MKT-B",
        "observed_at": NOW,
        "book_observed_at": economics.orderbook_observed_at,
        "closes_at": NOW + timedelta(days=1),
        "book_source_hash": economics.orderbook_source_hash,
        "economics_evidence_id": economics.evidence_id,
        "book": economics.replay_input.book_observation.book,
        "sequence_contiguous": True,
        "market_active": True,
        "market_paused": False,
        "source_healthy": True,
        "own_order_state_known": True,
    }
    values.update(changes)
    return ShadowMarketSnapshot.build(**values)


def inventory(net: str = "0", maximum: str = "5", **changes: object) -> InventorySnapshot:
    values: dict[str, object] = {
        "market_ticker": "MKT-B",
        "event_id": "event-1",
        "observed_at": NOW - timedelta(milliseconds=50),
        "net_yes_contracts": Decimal(net),
        "max_abs_yes_contracts": Decimal(maximum),
        "reconciled": True,
    }
    values.update(changes)
    return InventorySnapshot.build(**values)  # type: ignore[arg-type]


def maker_fee() -> FeePolicy:
    return authority()[0].replay_input.fee_policy


def plan(**changes: object) -> object:
    economics, binding = authority()
    values: dict[str, object] = {
        "curve": curve(rules_hash=economics.market_rules_hash),
        "market": market(economics=economics),
        "inventory": inventory(),
        "economics": economics,
        "economics_binding": binding,
        "policy": default_shadow_quote_policy(),
    }
    values.update(changes)
    return plan_shadow_quotes(**values)  # type: ignore[arg-type]


def test_validated_wide_book_emits_two_safe_shadow_quotes() -> None:
    result = plan()
    assert result.state is ShadowQuoteState.TWO_SIDED
    assert {quote.outcome_side for quote in result.quotes} == {OutcomeSide.YES, OutcomeSide.NO}
    assert all(quote.price == Decimal("0.36") for quote in result.quotes)
    assert all(quote.net_edge_per_contract >= Decimal("0.05") for quote in result.quotes)
    assert all(
        quote.post_only
        and quote.cancel_order_on_pause
        and quote.order_group_required
        and not quote.exchange_order
        and quote.production_influence == 0
        for quote in result.quotes
    )
    assert result.research_only and result.production_influence == 0


def test_curve_rejects_nonmonotone_siblings_and_missing_validation() -> None:
    with pytest.raises(MarketMakingError, match="monotonicity"):
        FairValueCurve.build(
            event_id="e",
            cohort_id="c",
            comparison=ComparisonDirection.GREATER_THAN,
            points=(point("A", "0", "0.40"), point("B", "1", "0.50")),
            model_id="m",
            model_version="1",
            calibration_id="cal",
            evidence_manifest_id="evidence",
            validation_receipt_id="review",
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=1),
            eligibility=FairValueEligibility.ELIGIBLE_SHADOW_RESEARCH,
        )
    with pytest.raises(MarketMakingError, match="validation receipt"):
        FairValueCurve.build(
            event_id="e",
            cohort_id="c",
            comparison=ComparisonDirection.GREATER_THAN,
            points=(point("A", "0", "0.60"), point("B", "1", "0.50")),
            model_id="m",
            model_version="1",
            calibration_id="cal",
            evidence_manifest_id="evidence",
            validation_receipt_id="",
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=1),
            eligibility=FairValueEligibility.ELIGIBLE_SHADOW_RESEARCH,
        )


@pytest.mark.parametrize(
    ("market_change", "inventory_change", "expected"),
    (
        ({"book_observed_at": NOW - timedelta(seconds=3)}, {}, QuoteBlocker.BOOK_STALE),
        ({"sequence_contiguous": False}, {}, QuoteBlocker.BOOK_SEQUENCE_GAP),
        ({"market_active": False}, {}, QuoteBlocker.MARKET_INACTIVE),
        ({"market_paused": True}, {}, QuoteBlocker.MARKET_PAUSED),
        ({"source_healthy": False}, {}, QuoteBlocker.SOURCE_UNHEALTHY),
        ({"own_order_state_known": False}, {}, QuoteBlocker.OWN_ORDER_STATE_UNKNOWN),
        ({"rules_hash": "changed"}, {}, QuoteBlocker.RULES_MISMATCH),
        ({"specification_hash": "changed"}, {}, QuoteBlocker.SPECIFICATION_MISMATCH),
        ({"closes_at": NOW + timedelta(minutes=10)}, {}, QuoteBlocker.TOO_CLOSE_TO_CLOSE),
        ({}, {"reconciled": False}, QuoteBlocker.INVENTORY_UNRECONCILED),
        (
            {},
            {"observed_at": NOW - timedelta(seconds=3)},
            QuoteBlocker.INVENTORY_STALE,
        ),
    ),
)
def test_authority_health_and_freshness_fail_closed(
    market_change: dict[str, object],
    inventory_change: dict[str, object],
    expected: QuoteBlocker,
) -> None:
    result = plan(market=market(**market_change), inventory=inventory(**inventory_change))
    assert result.state is ShadowQuoteState.ABSTAIN
    assert expected in result.blockers
    assert not result.quotes


def test_ineligible_stale_and_tampered_fair_value_fail_closed() -> None:
    ineligible = plan(curve=curve(FairValueEligibility.INELIGIBLE))
    assert QuoteBlocker.FAIR_VALUE_INELIGIBLE in ineligible.blockers

    stale_curve = FairValueCurve.build(
        event_id="event-1",
        cohort_id="cohort-1",
        comparison=ComparisonDirection.GREATER_THAN,
        points=(point("MKT-A", "0", "0.60"), point("MKT-B", "1", "0.50")),
        model_id="model",
        model_version="1",
        calibration_id="cal",
        evidence_manifest_id="evidence",
        validation_receipt_id="review",
        issued_at=NOW - timedelta(hours=1),
        expires_at=NOW - timedelta(seconds=1),
        eligibility=FairValueEligibility.ELIGIBLE_SHADOW_RESEARCH,
    )
    stale = plan(curve=stale_curve)
    assert QuoteBlocker.FAIR_VALUE_STALE in stale.blockers

    original = curve()
    tampered = replace(original, model_version="silently-changed")
    changed = plan(curve=tampered)
    assert QuoteBlocker.FAIR_VALUE_IDENTITY_MISMATCH in changed.blockers


def test_unverified_or_out_of_effect_fee_policy_abstains() -> None:
    economics, _ = authority()
    unverified = replace(economics.replay_input.fee_policy, verified=False)
    unverified_economics = replace(
        economics, replay_input=replace(economics.replay_input, fee_policy=unverified)
    )
    result = plan(economics=unverified_economics)
    assert result.state is ShadowQuoteState.ABSTAIN
    assert QuoteBlocker.FEE_UNVERIFIED in result.blockers
    assert QuoteBlocker.ECONOMICS_IDENTITY_MISMATCH in result.blockers

    future = replace(economics.replay_input.fee_policy, effective_at=NOW + timedelta(days=1))
    future_economics = replace(
        economics, replay_input=replace(economics.replay_input, fee_policy=future)
    )
    result = plan(economics=future_economics)
    assert QuoteBlocker.FEE_NOT_EFFECTIVE in result.blockers


def test_economics_binding_and_policy_tampering_fail_closed() -> None:
    economics, binding = authority()
    bad_binding = dict(binding)
    bad_binding["economics_evidence_id"] = "different"
    result = plan(economics_binding=bad_binding)
    assert result.state is ShadowQuoteState.ABSTAIN
    assert QuoteBlocker.ECONOMICS_BINDING_INVALID in result.blockers

    result = plan(economics=replace(economics, requested_quantity=Decimal(2)))
    assert QuoteBlocker.ECONOMICS_IDENTITY_MISMATCH in result.blockers

    result = plan(policy=replace(default_shadow_quote_policy(), minimum_net_edge=Decimal("0.04")))
    assert QuoteBlocker.QUOTE_POLICY_INVALID in result.blockers


def test_inventory_at_limit_allows_only_inventory_reducing_quote() -> None:
    result = plan(inventory=inventory("5", "5"))
    assert result.state is ShadowQuoteState.ONE_SIDED_INVENTORY_REDUCTION
    assert len(result.quotes) == 1
    assert result.quotes[0].outcome_side is OutcomeSide.NO
    assert result.quotes[0].inventory_reducing
    assert QuoteBlocker.INVENTORY_LIMIT in result.blockers


def test_one_sided_new_risk_is_suppressed() -> None:
    economics, binding = authority("0.35", "0.48")
    result = plan(
        economics=economics,
        economics_binding=binding,
        market=market(economics=economics),
        curve=curve(rules_hash=economics.market_rules_hash),
    )
    assert result.state is ShadowQuoteState.ABSTAIN
    assert QuoteBlocker.NO_EDGE_BELOW_HURDLE in result.blockers
    assert QuoteBlocker.TWO_SIDED_REQUIRED in result.blockers
    assert not result.quotes


def test_narrow_spread_does_not_manufacture_maker_edge() -> None:
    economics, binding = authority("0.48", "0.48")
    result = plan(
        economics=economics,
        economics_binding=binding,
        market=market(economics=economics),
        curve=curve(rules_hash=economics.market_rules_hash),
    )
    assert result.state is ShadowQuoteState.ABSTAIN
    assert {
        QuoteBlocker.YES_EDGE_BELOW_HURDLE,
        QuoteBlocker.NO_EDGE_BELOW_HURDLE,
    }.issubset(result.blockers)


def _filled_order(result: object, side: OutcomeSide = OutcomeSide.YES) -> object:
    quote = next(item for item in result.quotes if item.outcome_side is side)
    flow = ReplayFlow(
        "trade-1",
        NOW + timedelta(seconds=1),
        1,
        FlowKind.TRADE,
        side,
        quote.price,
        Decimal(1),
        "book-lineage",
    )
    return simulate_maker(
        simulated_order_id=f"sim-{side}",
        candidate_id=quote.quote_id,
        strategy=StrategyType.MAKER_AT_BEST,
        side=side,
        price=quote.price,
        quantity=Decimal(1),
        displayed_ahead=Decimal(0),
        candidate_time=NOW,
        decision_time=NOW,
        policy=default_policies(NOW)[1],
        flows=(flow,),
        fee_policy=maker_fee(),
    )


def _markouts(order: object, side: OutcomeSide) -> tuple[MarkoutObservation, ...]:
    fill = order.fills[0]
    return tuple(
        markout(
            fill,
            side,
            fill.timestamp + horizon,
            Decimal("0.50"),
            Decimal("0.49"),
        )
        for horizon in REQUIRED_MARKOUT_HORIZONS
    )


def test_attempt_receipt_reuses_canonical_m11_fill_markout_and_settlement() -> None:
    result = plan()
    quote = next(item for item in result.quotes if item.outcome_side is OutcomeSide.YES)
    order = _filled_order(result)
    settlement = settle(order, OutcomeSide.YES, NOW + timedelta(days=1))
    receipt = build_attempt_receipt(
        plan=result,
        quote_id=quote.quote_id,
        order=order,
        markouts=_markouts(order, OutcomeSide.YES),
        settlement=settlement,
    )
    assert receipt.evidence_state is AttemptEvidenceState.SETTLED_COMPLETE
    assert receipt.filled_quantity == Decimal(1)
    assert receipt.maker_fees == order.fills[0].fee
    assert receipt.settlement_net_pnl == settlement.net_pnl
    assert receipt.profitability_claim == "NOT_ESTABLISHED"
    assert receipt.production_influence == 0

    summary = summarize_attempts((receipt,))
    assert summary.attempts == 1 and summary.unique_events == 1
    assert summary.mean_markouts
    assert summary.profitability_claim == "NOT_ESTABLISHED"
    assert summary.production_eligible is False


def test_missing_markout_or_wrong_quote_binding_is_rejected() -> None:
    result = plan()
    quote = result.quotes[0]
    order = _filled_order(result, quote.outcome_side)
    with pytest.raises(MarketMakingError, match="each reviewed horizon"):
        build_attempt_receipt(
            plan=result,
            quote_id=quote.quote_id,
            order=order,
            markouts=_markouts(order, quote.outcome_side)[:-1],
            settlement=None,
        )
    with pytest.raises(MarketMakingError, match="uniquely bound"):
        build_attempt_receipt(
            plan=result,
            quote_id="not-in-plan",
            order=order,
            markouts=(),
            settlement=None,
        )


def test_market_making_package_has_no_network_risk_or_execution_import() -> None:
    root = Path("services/market_making_research")
    source = "\n".join(path.read_text() for path in sorted(root.glob("*.py")))
    for forbidden in (
        "services.production_execution",
        "services.risk_engine",
        "services.demo_execution",
        "requests",
        "websockets",
        "client_order_id",
        "ProductionRequestEnvelope",
        "RiskIntent",
    ):
        assert forbidden not in source
