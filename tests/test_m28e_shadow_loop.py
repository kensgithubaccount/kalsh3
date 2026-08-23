from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from services.production_weather_strategy.historical_economics import TradeSide
from services.production_weather_strategy.shadow_loop import (
    ShadowLoopError,
    ShadowOpportunity,
    ShadowRankingPolicy,
    ShadowSettledOutcome,
    rank_shadow_opportunities,
    summarize_shadow_outcomes,
)


def _opportunity(
    *,
    family: str,
    event: str,
    market: str,
    model_yes: str,
    yes_cost: str,
    no_cost: str,
) -> ShadowOpportunity:
    cutoff = datetime(2026, 8, 23, 3, tzinfo=UTC)
    return ShadowOpportunity.build(
        family=family,
        event_ticker=event,
        market_ticker=market,
        model_id="model-v1",
        model_yes_probability=Decimal(model_yes),
        yes_all_in_cost=Decimal(yes_cost),
        no_all_in_cost=Decimal(no_cost),
        evidence_ids=(f"forecast-{market}", f"book-{market}"),
        observed_at=cutoff - timedelta(seconds=5),
        decision_cutoff=cutoff,
    )


def test_opportunity_selects_best_after_cost_side() -> None:
    item = _opportunity(
        family="DAILY_TEMPERATURE",
        event="EVENT1",
        market="MARKET1",
        model_yes="0.78",
        yes_cost="0.60",
        no_cost="0.45",
    )
    assert item.selected_side is TradeSide.YES
    assert item.after_cost_edge == Decimal("0.18")
    assert item.maximum_loss == Decimal("0.60")


def test_ranking_enforces_event_family_and_global_limits() -> None:
    a = _opportunity(
        family="WEATHER",
        event="E1",
        market="M1",
        model_yes="0.80",
        yes_cost="0.55",
        no_cost="0.50",
    )
    sibling = _opportunity(
        family="WEATHER",
        event="E1",
        market="M2",
        model_yes="0.75",
        yes_cost="0.55",
        no_cost="0.50",
    )
    b = _opportunity(
        family="WEATHER",
        event="E2",
        market="M3",
        model_yes="0.74",
        yes_cost="0.55",
        no_cost="0.50",
    )
    c = _opportunity(
        family="ECONOMICS",
        event="E3",
        market="M4",
        model_yes="0.90",
        yes_cost="0.60",
        no_cost="0.50",
    )
    policy = ShadowRankingPolicy.build(
        minimum_after_cost_edge=Decimal("0.10"),
        maximum_candidates=2,
        family_limits=(("WEATHER", 1), ("ECONOMICS", 1)),
    )
    result = rank_shadow_opportunities((a, sibling, b, c), policy=policy)
    assert len(result.selected_opportunity_ids) == 2
    reasons = {reason for _, reason in result.rejected}
    assert "EVENT_CONCENTRATION" in reasons or "FAMILY_CONCENTRATION" in reasons
    assert "FAMILY_CONCENTRATION" in reasons


def test_ranking_filters_below_edge_policy() -> None:
    item = _opportunity(
        family="WEATHER",
        event="E1",
        market="M1",
        model_yes="0.55",
        yes_cost="0.50",
        no_cost="0.55",
    )
    policy = ShadowRankingPolicy.build(
        minimum_after_cost_edge=Decimal("0.10"),
        maximum_candidates=1,
    )
    result = rank_shadow_opportunities((item,), policy=policy)
    assert result.selected_opportunity_ids == ()
    assert result.rejected == ((item.opportunity_id, "EDGE_BELOW_POLICY"),)


def test_shadow_settlement_and_summary_are_event_equal_weighted() -> None:
    a = _opportunity(
        family="WEATHER",
        event="E1",
        market="M1",
        model_yes="0.80",
        yes_cost="0.55",
        no_cost="0.50",
    )
    sibling = _opportunity(
        family="WEATHER",
        event="E1",
        market="M2",
        model_yes="0.20",
        yes_cost="0.30",
        no_cost="0.75",
    )
    b = _opportunity(
        family="WEATHER",
        event="E2",
        market="M3",
        model_yes="0.60",
        yes_cost="0.50",
        no_cost="0.55",
    )
    settled = datetime(2026, 8, 24, 18, tzinfo=UTC)
    outcomes = (
        ShadowSettledOutcome.build(
            a,
            realized_yes=1,
            settled_at=settled,
            settlement_evidence_id="settle-a",
        ),
        ShadowSettledOutcome.build(
            sibling,
            realized_yes=0,
            settled_at=settled,
            settlement_evidence_id="settle-sibling",
        ),
        ShadowSettledOutcome.build(
            b,
            realized_yes=1,
            settled_at=settled,
            settlement_evidence_id="settle-b",
        ),
    )
    summary = summarize_shadow_outcomes(outcomes)
    event_one_brier = (Decimal("0.04") + Decimal("0.04")) / Decimal("2")
    event_two_brier = Decimal("0.16")
    assert summary.mean_brier_score == (event_one_brier + event_two_brier) / Decimal("2")
    assert summary.unique_events == 2
    assert summary.settled_opportunities == 3


def test_future_observation_and_duplicate_identity_fail_closed() -> None:
    cutoff = datetime(2026, 8, 23, 3, tzinfo=UTC)
    with pytest.raises(ShadowLoopError, match="after decision cutoff"):
        ShadowOpportunity.build(
            family="WEATHER",
            event_ticker="E1",
            market_ticker="M1",
            model_id="model",
            model_yes_probability=Decimal("0.5"),
            yes_all_in_cost=Decimal("0.4"),
            no_all_in_cost=Decimal("0.6"),
            evidence_ids=("e1",),
            observed_at=cutoff + timedelta(seconds=1),
            decision_cutoff=cutoff,
        )

    item = _opportunity(
        family="WEATHER",
        event="E1",
        market="M1",
        model_yes="0.80",
        yes_cost="0.55",
        no_cost="0.50",
    )
    policy = ShadowRankingPolicy.build(
        minimum_after_cost_edge=Decimal("0.10"),
        maximum_candidates=2,
    )
    with pytest.raises(ShadowLoopError, match="duplicate"):
        rank_shadow_opportunities((item, item), policy=policy)
