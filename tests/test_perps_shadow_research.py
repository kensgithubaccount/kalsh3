from collections import UserDict
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from services.perps_shadow_research.domain import (
    Direction,
    EdgeDecayObservation,
    MarginMarketObservation,
    PortfolioMarginObservation,
    QuoteObservation,
    ShadowResearchError,
)
from services.perps_shadow_research.edge_decay import measure_edge_decay
from services.perps_shadow_research.parsing import (
    parse_margin_market,
    parse_portfolio_margin,
)


def _edge_observation(direction: Direction = Direction.LONG) -> EdgeDecayObservation:
    t0 = datetime(2026, 8, 13, 12, 0, 0, tzinfo=UTC)
    return measure_edge_decay(
        ticker="TEST-PERP",
        direction=direction,
        exchange_index=0,
        signal_created_at=t0,
        signal_available_at=t0 + timedelta(milliseconds=120),
        decision_at=t0 + timedelta(milliseconds=170),
        hypothetical_send_at=t0 + timedelta(milliseconds=190),
        signal_value=Decimal("101.00"),
        value_at_creation=Decimal("100.00"),
        value_at_available=Decimal("100.20"),
        value_at_decision=Decimal("100.55"),
        value_at_hypothetical_send=Decimal("100.70"),
        artifact_id="edge-1",
    )


def test_directional_leverage_is_kept_separate_and_exchange_index_required():
    now = datetime.now(UTC)
    obs = parse_margin_market(
        {
            "ticker": "TEST-PERP",
            "exchange_index": 0,
            "leverage_estimates": {"100": "2.0"},
            "long_leverage_estimates": {"100": "2.5"},
            "short_leverage_estimates": {"100": "1.8"},
        },
        observed_at=now,
    )
    assert obs.exchange_index == 0
    assert obs.long_leverage_estimates[0].leverage == Decimal("2.5")
    assert obs.short_leverage_estimates[0].leverage == Decimal("1.8")
    assert obs.symmetric_leverage_estimates[0].leverage == Decimal("2.0")

    with pytest.raises(ShadowResearchError):
        parse_margin_market({"ticker": "TEST-PERP"}, observed_at=now)
    for invalid_index in (True, False, -1):
        with pytest.raises(ShadowResearchError, match="exchange_index"):
            parse_margin_market(
                {"ticker": "TEST-PERP", "exchange_index": invalid_index},
                observed_at=now,
            )


def test_position_margin_fields_are_nullable_not_invented():
    obs = parse_portfolio_margin(
        {"portfolio_value": "1000.00"},
        observed_at=datetime.now(UTC),
        subaccount=0,
        exchange_index=0,
    )
    assert obs.portfolio_value == Decimal("1000.00")
    assert obs.available_balance is None
    assert obs.margin_used is None
    assert obs.maintenance_margin is None
    for invalid_subaccount in (True, False, -1):
        with pytest.raises(ShadowResearchError, match="subaccount"):
            parse_portfolio_margin(
                {},
                observed_at=datetime.now(UTC),
                subaccount=invalid_subaccount,
                exchange_index=0,
            )


def test_retained_raw_payload_is_recursively_read_only():
    nested_values = [1, 2]
    nested_mapping = UserDict({"values": nested_values})
    obs = parse_margin_market(
        {"ticker": "TEST-PERP", "exchange_index": 0, "nested": nested_mapping},
        observed_at=datetime.now(UTC),
    )
    nested_values.append(3)
    nested_mapping["changed_after_construction"] = True
    assert obs.raw is not None
    with pytest.raises(TypeError):
        obs.raw["ticker"] = "CHANGED"
    with pytest.raises(TypeError):
        obs.raw["nested"]["changed"] = True
    assert obs.raw["nested"]["values"] == (1, 2)
    assert "changed_after_construction" not in obs.raw["nested"]


def test_unsupported_mutable_raw_payload_value_fails_closed():
    class MutableValue:
        def __init__(self) -> None:
            self.value = "mutable"

    with pytest.raises(ShadowResearchError, match="unsupported raw payload value type"):
        parse_margin_market(
            {"ticker": "TEST-PERP", "exchange_index": 0, "unsupported": MutableValue()},
            observed_at=datetime.now(UTC),
        )


def test_long_edge_math_and_exact_latency():
    obs = _edge_observation()
    assert obs.value_at_creation == Decimal("100.00")
    assert obs.initial_edge == Decimal("1.00")
    assert obs.available_edge == Decimal("0.80")
    assert obs.decision_edge == Decimal("0.45")
    assert obs.send_edge == Decimal("0.30")
    assert obs.publication_to_available_ms == 120
    assert obs.available_to_decision_ms == 50
    assert obs.decision_to_send_ms == 20


def test_short_edge_math():
    obs = _edge_observation(Direction.SHORT)
    assert obs.initial_edge == Decimal("-1.00")
    assert obs.available_edge == Decimal("-0.80")
    assert obs.decision_edge == Decimal("-0.45")
    assert obs.send_edge == Decimal("-0.30")


def test_invalid_direction_fails_closed():
    with pytest.raises(ShadowResearchError, match="direction"):
        replace(_edge_observation(), direction="LONG")


def test_naive_timestamp_is_rejected_explicitly():
    with pytest.raises(ShadowResearchError, match="timezone-aware"):
        replace(_edge_observation(), signal_created_at=datetime(2026, 8, 13, 12))


def test_out_of_order_timestamps_fail_closed():
    obs = _edge_observation()
    with pytest.raises(ShadowResearchError, match="monotonic"):
        replace(obs, signal_available_at=obs.signal_created_at - timedelta(milliseconds=1))
    with pytest.raises(ShadowResearchError, match="monotonic"):
        measure_edge_decay(
            ticker="TEST-PERP",
            direction=Direction.LONG,
            exchange_index=0,
            signal_created_at=obs.signal_created_at,
            signal_available_at=obs.signal_created_at - timedelta(milliseconds=1),
            decision_at=obs.decision_at,
            hypothetical_send_at=obs.hypothetical_send_at,
            signal_value=obs.signal_value,
            value_at_creation=obs.value_at_creation,
            value_at_available=obs.value_at_available,
            value_at_decision=obs.value_at_decision,
            value_at_hypothetical_send=obs.value_at_hypothetical_send,
        )


def test_contradictory_latency_fails_closed():
    obs = _edge_observation()
    with pytest.raises(ShadowResearchError, match="stored latencies contradict"):
        replace(obs, available_to_decision_ms=51)
    with pytest.raises(ShadowResearchError, match="exact millisecond precision"):
        measure_edge_decay(
            ticker="TEST-PERP",
            direction=Direction.LONG,
            exchange_index=0,
            signal_created_at=obs.signal_created_at,
            signal_available_at=obs.signal_created_at + timedelta(microseconds=1),
            decision_at=obs.decision_at,
            hypothetical_send_at=obs.hypothetical_send_at,
            signal_value=obs.signal_value,
            value_at_creation=obs.value_at_creation,
            value_at_available=obs.value_at_available,
            value_at_decision=obs.value_at_decision,
            value_at_hypothetical_send=obs.value_at_hypothetical_send,
        )


def test_contradictory_edge_fails_closed():
    obs = _edge_observation()
    with pytest.raises(ShadowResearchError, match="stored edges contradict"):
        replace(obs, decision_edge=Decimal("0.46"))


def test_production_influence_rejected_for_every_shadow_observation_type():
    now = datetime.now(UTC)
    quote = QuoteObservation(now, Decimal("1"), "fixture", 0, subaccount=0)
    assert quote.observed_at == now
    assert quote.value == Decimal("1")
    assert quote.source == "fixture"
    assert quote.exchange_index == 0
    assert quote.subaccount == 0
    assert quote.production_influence == 0
    with pytest.raises(ShadowResearchError):
        MarginMarketObservation("TEST-PERP", 0, now, production_influence=Decimal("0.01"))
    with pytest.raises(ShadowResearchError):
        PortfolioMarginObservation(0, 0, now, production_influence=Decimal("0.01"))
    with pytest.raises(ShadowResearchError):
        QuoteObservation(now, Decimal("1"), "fixture", 0, production_influence=Decimal("0.01"))
    with pytest.raises(ShadowResearchError):
        replace(_edge_observation(), production_influence=Decimal("0.01"))
