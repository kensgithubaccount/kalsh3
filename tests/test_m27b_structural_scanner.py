from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from services.contract_intelligence.specification import (
    ContractSpecification,
    ContractSpecificationParser,
    SemanticsInputBundle,
    SemanticStatus,
)
from services.market_universe.domain import Event, Market
from services.market_universe.pricing import PriceLadder
from services.opportunity_engine.books import OutcomeSide, walk_depth
from services.opportunity_engine.domain import OpportunityError
from services.opportunity_engine.fees import FeeType, current_event_formula_policy
from services.opportunity_engine.live_economics import (
    DiscoveryQuotes,
    MarketEconomicsEvidence,
    MarketEconomicsReplayInput,
    TakerCost,
    normalize_live_orderbook,
    taker_cost,
)
from services.opportunity_engine.live_fees import (
    CurrentSeriesFeeObservation,
    EventFeeOverride,
    resolve_current_fee_regime,
)
from services.opportunity_engine.structural import (
    ConfirmationState,
    RouteReason,
    RouteState,
    StructuralLead,
    confirm_structural_lead,
    scan_structural_markets,
)

NOW = datetime(2026, 8, 15, 13, tzinfo=UTC)


def event(ticker: str = "E", *, category: str = "Sports") -> Event:
    return Event.parse(
        {
            "event_ticker": ticker,
            "series_ticker": "S",
            "title": "Observed event",
            "category": category,
        }
    )


def semantic_market_fields(ticker: str, threshold: str) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "event_ticker": "E",
        "title": f"Will the measured value be at least {threshold} units?",
        "yes_sub_title": f"Measured value is at least {threshold} units",
        "no_sub_title": f"Measured value is below {threshold} units",
        "rules_primary": f"YES if the measured value is at least {threshold} units.",
        "rules_secondary": "Use the final published report.",
        "market_type": "binary",
        "status": "active",
        "price_level_structure": "linear_cent",
        "floor_strike": threshold,
        "strike_type": "greater",
        "custom_strike": None,
        "timezone": "UTC",
        "expiration_time": "2026-08-16T20:00:00Z",
        "expected_expiration_time": "2026-08-16T20:00:00Z",
        "occurrence_datetime": "2026-08-16T12:00:00Z",
        "rounding_rules": "nearest whole unit",
        "revision_rules": "final report controls",
        "correction_rules": "published corrections control",
        "recount_rules": "none",
        "cancellation_rules": "void only under exchange rules",
        "postponement_rules": "deadline unchanged",
        "early_close_condition": "none",
        "exception_rules": ["none"],
        "measured_event_or_value": "final measured value",
        "subject_entities": ["subject-a"],
        "geographic_scope": "scope-a",
        "threshold_unit": "units",
        "is_provisional": False,
    }


def market(
    ticker: str,
    threshold: str = "1",
    *,
    event_ticker: str = "E",
    strike_type: str | None = "greater",
    custom: object = None,
    status: str = "active",
    market_type: str = "binary",
    provisional: bool = False,
    multivariate: bool = False,
) -> Market:
    raw = semantic_market_fields(ticker, threshold)
    raw.update(
        event_ticker=event_ticker,
        market_type=market_type,
        status=status,
        custom_strike=custom,
        is_provisional=provisional,
    )
    if multivariate:
        raw["mve_collection_ticker"] = "MVE"
    if strike_type is not None:
        raw["strike_type"] = strike_type
    else:
        raw.pop("strike_type")
    return Market.parse(raw)


def quote(bid: str, ask: str, *, size: str = "5", source: str = "a") -> DiscoveryQuotes:
    return DiscoveryQuotes.parse(
        {
            "yes_bid_dollars": bid,
            "yes_ask_dollars": ask,
            "yes_bid_size_fp": size,
            "yes_ask_size_fp": size,
            "no_bid_dollars": str(Decimal(1) - Decimal(ask)),
            "no_ask_dollars": str(Decimal(1) - Decimal(bid)),
            "volume_fp": "0",
            "volume_24h_fp": "0",
            "open_interest_fp": "0",
            "liquidity_dollars": "0",
            "test_source": source,
        }
    )


def scan(
    markets: list[Market],
    quotes: dict[str, DiscoveryQuotes | None] | None = None,
    *,
    authority: str = "caller-supplied-observation",
):
    return scan_structural_markets(
        markets,
        events={item.event_ticker: event(item.event_ticker) for item in markets},
        discovery_quotes=quotes or {},
        source_authority=authority,
    )


def test_universal_routing_is_deterministic_research_only_and_truthful() -> None:
    markets = [
        market("Z", strike_type="structured"),
        market("A", strike_type="greater"),
        market("M", strike_type=None),
    ]
    first = scan(markets)
    second = scan(list(reversed(markets)))
    assert first == second
    assert [route.market_ticker for route in first.routes] == ["A", "M", "Z"]
    assert first.manifest.markets_evaluated == 3
    assert len(first.routes) == 3
    assert all(route.production_influence == 0 and route.research_only for route in first.routes)
    assert all(route.specialist_model_family is None for route in first.routes)
    assert first.routes[1].state is first.routes[2].state is RouteState.ROUTE_ONLY


@pytest.mark.parametrize("identity", ["player", "team", "index_contract"])
def test_complete_custom_strike_separates_entities(identity: str) -> None:
    markets = [
        market("A1", "1", custom={identity: "A", "period": "game"}),
        market("A2", "2", custom={"period": "game", identity: "A"}),
        market("B1", "1", custom={identity: "B", "period": "game"}),
        market("B2", "2", custom={identity: "B", "period": "game"}),
    ]
    result = scan(
        markets,
        {
            "A1": quote(".30", ".40"),
            "A2": quote(".50", ".60"),
            "B1": quote(".20", ".30"),
            "B2": quote(".40", ".50"),
        },
    )
    assert result.manifest.structural_cohorts == 2
    assert {(lead.broad_market_ticker, lead.narrow_market_ticker) for lead in result.leads} == {
        ("A1", "A2"),
        ("B1", "B2"),
    }
    assert result.routes[0].cohort_identity == result.routes[1].cohort_identity
    assert result.routes[0].cohort_identity != result.routes[2].cohort_identity


def test_malformed_mixed_identity_and_duplicate_thresholds_fail_closed() -> None:
    malformed = scan([market("BAD", custom="not-an-object")])
    assert malformed.routes[0].state is RouteState.ABSTAIN
    assert RouteReason.MALFORMED_CUSTOM_STRIKE in malformed.routes[0].reasons

    mixed = scan([market("MISSING", "1"), market("PRESENT", "2", custom={"player": "P"})])
    assert all(route.state is RouteState.ABSTAIN for route in mixed.routes)
    assert mixed.manifest.cohorts_rejected_or_ambiguous == 1

    duplicate = scan([market("D1", "1"), market("D2", "1")])
    assert all(route.state is RouteState.ABSTAIN for route in duplicate.routes)
    assert all(RouteReason.DUPLICATE_THRESHOLD in route.reasons for route in duplicate.routes)
    assert duplicate.manifest.cohorts_rejected_or_ambiguous == 1


@pytest.mark.parametrize("strike_type", ["greater", "greater_or_equal"])
def test_supported_directional_relationship_and_strongest_earlier_ask(strike_type: str) -> None:
    markets = [
        market("LOW", "1", strike_type=strike_type),
        market("MID", "2", strike_type=strike_type),
        market("HIGH", "3", strike_type=strike_type),
    ]
    result = scan(
        markets,
        {
            "LOW": quote(".20", ".45", size="7"),
            "MID": quote(".30", ".40", size="4"),
            "HIGH": quote(".55", ".60", size="5"),
        },
    )
    assert len(result.leads) == 1
    lead = result.leads[0]
    assert (lead.broad_market_ticker, lead.narrow_market_ticker) == ("MID", "HIGH")
    assert (lead.broad_threshold, lead.narrow_threshold) == (Decimal(2), Decimal(3))
    assert lead.indicative_gross_gap == Decimal(".15")
    assert lead.indicative_quantity == Decimal(4)
    assert lead.exact_confirmation_required and lead.production_influence == 0


def test_reverse_monotonic_missing_and_unsupported_quotes_never_create_leads() -> None:
    ordinary = scan(
        [market("L", "1"), market("H", "2")],
        {"L": quote(".7", ".8"), "H": quote(".5", ".6")},
    )
    reverse = scan(
        [market("L", "1"), market("H", "2")],
        {"L": quote(".4", ".5"), "H": quote(".3", ".4")},
    )
    missing = scan(
        [market("L", "1"), market("H", "2")],
        {"L": quote(".4", ".5"), "H": None},
    )
    invalid = scan(
        [market("L", "1"), market("H", "2")],
        {
            "L": quote(".2", ".3"),
            "H": replace(quote(".5", ".6"), production_influence=Decimal(".1")),
        },
    )
    for strike in ("structured", "custom", "between", "less", "less_or_equal"):
        unsupported = scan(
            [market("L", "1", strike_type=strike), market("H", "2", strike_type=strike)],
            {"L": quote(".2", ".3"), "H": quote(".5", ".6")},
        )
        assert not unsupported.leads
    assert not ordinary.leads and not reverse.leads and not missing.leads and not invalid.leads
    assert RouteReason.INVALID_DISCOVERY_QUOTE in invalid.routes[0].reasons


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"status": "inactive"}, RouteReason.NON_ACTIVE_MARKET),
        ({"status": "closed"}, RouteReason.NON_ACTIVE_MARKET),
        ({"status": "finalized"}, RouteReason.NON_ACTIVE_MARKET),
        ({"provisional": True}, RouteReason.PROVISIONAL_MARKET),
        ({"multivariate": True}, RouteReason.MULTIVARIATE_MARKET),
        ({"market_type": "scalar"}, RouteReason.NON_BINARY_MARKET),
    ],
)
def test_discovery_base_gates_route_but_never_lead(
    changes: dict[str, Any], reason: RouteReason
) -> None:
    gated = market("L", "1", **changes)
    result = scan(
        [gated, market("H", "2")],
        {"L": quote(".2", ".3"), "H": quote(".5", ".6")},
    )
    routes = {route.market_ticker: route for route in result.routes}
    assert len(result.routes) == 2 and not result.leads
    assert routes["L"].state is RouteState.ROUTE_ONLY
    assert reason in routes["L"].reasons


def test_equal_ask_prefers_capacity_then_ticker_deterministically() -> None:
    markets = [market("A", "1"), market("B", "2"), market("H", "3")]
    larger = {
        "A": quote(".2", ".3", size="2"),
        "B": quote(".2", ".3", size="7"),
        "H": quote(".5", ".6", size="10"),
    }
    first = scan(markets, larger)
    reverse = scan(list(reversed(markets)), larger)
    assert first.leads[0].broad_market_ticker == "B"
    assert first.leads[0].indicative_quantity == Decimal(7)
    assert first == reverse

    equal = {**larger, "A": quote(".2", ".3", size="7")}
    assert scan(markets, equal).leads[0].broad_market_ticker == "A"


def test_route_lead_and_manifest_identities_bind_all_material_inputs() -> None:
    markets = [market("L", "1", custom={"player": "A"}), market("H", "2", custom={"player": "A"})]
    quotes = {"L": quote(".2", ".3"), "H": quote(".5", ".6")}
    base = scan(markets, quotes)
    threshold = scan([markets[0], market("H", "3", custom={"player": "A"})], quotes)
    subject = scan(
        [market("L", "1", custom={"player": "B"}), market("H", "2", custom={"player": "B"})], quotes
    )
    changed_quote = scan(markets, {**quotes, "H": quote(".55", ".6", source="changed")})
    changed_source = scan(markets, quotes, authority="different-observation")
    base_routes = {route.market_ticker: route for route in base.routes}
    threshold_routes = {route.market_ticker: route for route in threshold.routes}
    assert base_routes["H"].route_id != threshold_routes["H"].route_id
    assert base.routes[0].cohort_identity != subject.routes[0].cohort_identity
    assert base.leads[0].lead_id != changed_quote.leads[0].lead_id
    assert base.leads[0].lead_id != changed_source.leads[0].lead_id
    assert base.manifest.manifest_id != changed_source.manifest.manifest_id


def test_large_cohort_and_50k_universe_have_linear_output_bounds() -> None:
    cohort = [market(f"C{index:04d}", str(index)) for index in range(1000)]
    quotes = {item.ticker: quote(".5", ".6") for item in cohort}
    cohort_result = scan(cohort, quotes)
    assert not cohort_result.leads
    assert cohort_result.manifest.structural_cohorts == 1

    large = [market(f"U{index:05d}", strike_type="structured") for index in range(50_001)]
    first = scan(large)
    second = scan(list(reversed(large)))
    assert first.manifest == second.manifest
    assert len(first.routes) == 50_001 and not first.leads


def evidence(
    ticker: str,
    rules_hash: str,
    metadata_hash: str,
    *,
    quantity: Decimal = Decimal("2"),
    yes_depth: str = "5",
    no_depth: str = "5",
    observed_at: datetime = NOW,
) -> MarketEconomicsEvidence:
    ladder = PriceLadder.parse("linear_cent", [{"start": "0", "end": "1", "step": ".01"}])
    observation = normalize_live_orderbook(
        {
            "ticker": ticker,
            "orderbook_fp": {
                "yes_dollars": [[".30", no_depth]],
                "no_dollars": [[".60", yes_depth]],
            },
        },
        ticker=ticker,
        ladder=ladder,
        source_id=f"book-{ticker}",
        observed_at=observed_at,
        market_rules_hash=rules_hash,
    )
    series = CurrentSeriesFeeObservation.parse(
        {
            "ticker": "S",
            "title": "Series",
            "category": "Sports",
            "frequency": "game",
            "tags": [],
            "settlement_sources": [],
            "fee_type": "quadratic",
            "fee_multiplier": "1",
            "last_updated_ts": "2026-08-15T12:00:00Z",
        },
        observed_at=NOW,
    )
    regime = resolve_current_fee_regime(series, EventFeeOverride.parse({}))
    policy = current_event_formula_policy(fee_type=FeeType.QUADRATIC, fee_multiplier=Decimal(1))
    replay_input = MarketEconomicsReplayInput(observation, ladder, regime, policy)

    def cost(side: OutcomeSide) -> TakerCost | None:
        asks = observation.book.yes_asks if side is OutcomeSide.YES else observation.book.no_asks
        return (
            taker_cost(observation.book, side, quantity, policy)
            if walk_depth(asks, quantity).complete
            else None
        )

    return MarketEconomicsEvidence.create(
        market_ticker=ticker,
        event_ticker="E",
        series_ticker="S",
        market_source_id=f"market-{ticker}",
        market_rules_hash=rules_hash,
        market_metadata_hash=metadata_hash,
        price_range_hash=observation.price_range_hash,
        event_fee_hash=regime.event_metadata_hash,
        series_fee_observation_id=regime.series_observation_id,
        resolved_fee_regime_id=regime.regime_id,
        fee_policy_id=policy.policy_id,
        orderbook_source_id=observation.source_id,
        orderbook_source_hash=observation.source_hash,
        market_observed_at=NOW,
        orderbook_observed_at=observed_at,
        economics_observed_at=NOW,
        requested_quantity=quantity,
        yes=cost(OutcomeSide.YES),
        no=cost(OutcomeSide.NO),
        replay_input=replay_input,
    )


def lead_fixture():
    markets = [market("L", "1"), market("H", "2")]
    result = scan(markets, {"L": quote(".2", ".3"), "H": quote(".5", ".6")})
    routes = {route.market_ticker: route for route in result.routes}
    return result.leads[0], routes["L"], routes["H"]


def specification_from_market(market_layer: dict[str, Any]) -> ContractSpecification:
    bundle = SemanticsInputBundle.build(
        market_layer,
        {
            "event_ticker": "E",
            "series_ticker": "S",
            "title": "Measured event",
            "category": "Economics",
            "timezone": "UTC",
            "settlement_sources": [{"name": "Official Source", "url": "https://example.test"}],
        },
        {
            "ticker": "S",
            "title": "Measured series",
            "category": "Economics",
            "frequency": "event",
            "settlement_sources": [{"name": "Official Source", "url": "https://example.test"}],
        },
    )
    result = ContractSpecificationParser().parse(bundle, NOW)
    assert result.strategy_supported
    return result


def specification(ticker: str, threshold: str) -> ContractSpecification:
    return specification_from_market(semantic_market_fields(ticker, threshold))


def exact_inputs() -> tuple[
    StructuralLead,
    MarketEconomicsEvidence,
    MarketEconomicsEvidence,
    ContractSpecification,
    ContractSpecification,
]:
    lead, broad_route, narrow_route = lead_fixture()
    return (
        lead,
        evidence("L", broad_route.rules_hash, broad_route.metadata_hash),
        evidence("H", narrow_route.rules_hash, narrow_route.metadata_hash),
        specification("L", "1"),
        specification("H", "2"),
    )


def confirm_exact(
    lead: StructuralLead,
    broad: MarketEconomicsEvidence,
    narrow: MarketEconomicsEvidence,
    broad_specification: ContractSpecification,
    narrow_specification: ContractSpecification,
):
    return confirm_structural_lead(
        lead,
        broad,
        narrow,
        broad_specification=broad_specification,
        narrow_specification=narrow_specification,
    )


@pytest.mark.parametrize(
    "delta", [timedelta(microseconds=1), timedelta(minutes=5), timedelta(hours=3)]
)
def test_confirmation_requires_identical_orderbook_observation_instant(
    delta: timedelta,
) -> None:
    lead, broad_route, narrow_route = lead_fixture()
    broad = evidence("L", broad_route.rules_hash, broad_route.metadata_hash, observed_at=NOW)
    narrow = evidence(
        "H",
        narrow_route.rules_hash,
        narrow_route.metadata_hash,
        observed_at=NOW - delta,
    )
    broad_specification = specification("L", "1")
    narrow_specification = specification("H", "2")
    # Both objects were individually accepted by the canonical M27A constructor.
    assert broad.evidence_id and narrow.evidence_id
    with pytest.raises(OpportunityError, match="orderbook observations are not simultaneous"):
        confirm_exact(lead, broad, narrow, broad_specification, narrow_specification)


def test_simultaneous_orderbook_observations_may_proceed() -> None:
    lead, broad, narrow, broad_specification, narrow_specification = exact_inputs()
    assert broad.orderbook_observed_at == narrow.orderbook_observed_at
    assert (
        confirm_exact(lead, broad, narrow, broad_specification, narrow_specification).state
        is ConfirmationState.FINAL_FEE_UNKNOWN_PREFILL
    )


def test_unsupported_contract_specification_on_either_leg_fails_closed() -> None:
    lead, broad, narrow, broad_specification, narrow_specification = exact_inputs()
    unsupported_broad = replace(broad_specification, semantic_status=SemanticStatus.UNSUPPORTED)
    unsupported_narrow = replace(narrow_specification, semantic_status=SemanticStatus.AMBIGUOUS)
    with pytest.raises(OpportunityError, match="broad contract specification"):
        confirm_exact(lead, broad, narrow, unsupported_broad, narrow_specification)
    with pytest.raises(OpportunityError, match="narrow contract specification"):
        confirm_exact(lead, broad, narrow, broad_specification, unsupported_narrow)


def test_contract_specification_identity_gates_fail_closed() -> None:
    lead, broad, narrow, broad_specification, narrow_specification = exact_inputs()
    with pytest.raises(OpportunityError, match="ticker"):
        confirm_exact(
            lead,
            broad,
            narrow,
            replace(broad_specification, market_ticker="WRONG"),
            narrow_specification,
        )
    with pytest.raises(OpportunityError, match="event"):
        confirm_exact(
            lead,
            broad,
            narrow,
            replace(broad_specification, event_ticker="WRONG"),
            narrow_specification,
        )
    with pytest.raises(OpportunityError, match="series"):
        confirm_exact(
            lead,
            broad,
            narrow,
            broad_specification,
            replace(narrow_specification, series_ticker="WRONG"),
        )


def test_contract_semantic_context_differences_fail_closed() -> None:
    lead, broad, narrow, broad_specification, narrow_specification = exact_inputs()
    changed_source = replace(
        narrow_specification,
        settlement_sources=(
            replace(
                narrow_specification.settlement_sources[0],
                normalized_name="different source",
            ),
            *narrow_specification.settlement_sources[1:],
        ),
    )
    variants = (
        replace(narrow_specification, settlement_authority="Different Authority"),
        changed_source,
        replace(
            narrow_specification, deadline=narrow_specification.deadline + timedelta(seconds=1)
        ),  # type: ignore[operator]
        replace(narrow_specification, timezone="America/New_York"),
        replace(narrow_specification, cancellation_rules="different cancellation"),
        replace(narrow_specification, early_close_rules="different early close"),
        replace(narrow_specification, measured_event_or_value="different measurement"),
        replace(narrow_specification, subject_entities=("different-subject",)),
    )
    for changed in variants:
        with pytest.raises(OpportunityError, match="semantic context"):
            confirm_exact(lead, broad, narrow, broad_specification, changed)


def test_threshold_and_proposition_differences_are_intentionally_compatible() -> None:
    lead, broad, narrow, broad_specification, narrow_specification = exact_inputs()
    assert broad_specification.threshold_value != narrow_specification.threshold_value
    assert broad_specification.yes_proposition != narrow_specification.yes_proposition
    assert broad_specification.no_proposition != narrow_specification.no_proposition
    result = confirm_exact(lead, broad, narrow, broad_specification, narrow_specification)
    assert result.minimum_guaranteed_settlement_payout == Decimal(2)


def test_matching_contract_market_provenance_allows_confirmation() -> None:
    lead, broad, narrow, broad_specification, narrow_specification = exact_inputs()
    assert broad_specification.market_rules_hash == broad.market_rules_hash
    assert broad_specification.market_metadata_hash == broad.market_metadata_hash
    assert narrow_specification.market_rules_hash == narrow.market_rules_hash
    assert narrow_specification.market_metadata_hash == narrow.market_metadata_hash
    result = confirm_exact(lead, broad, narrow, broad_specification, narrow_specification)
    assert result.minimum_guaranteed_settlement_payout == Decimal(2)


@pytest.mark.parametrize(
    ("leg", "field"),
    [
        ("broad", "market_rules_hash"),
        ("broad", "market_metadata_hash"),
        ("narrow", "market_rules_hash"),
        ("narrow", "market_metadata_hash"),
    ],
)
def test_contract_market_provenance_mismatch_never_constructs_confirmation(
    leg: str, field: str
) -> None:
    lead, broad, narrow, broad_specification, narrow_specification = exact_inputs()
    if leg == "broad":
        broad_specification = replace(broad_specification, **{field: "mismatch"})
    else:
        narrow_specification = replace(narrow_specification, **{field: "mismatch"})
    with pytest.raises(OpportunityError, match="market provenance mismatch"):
        confirm_exact(lead, broad, narrow, broad_specification, narrow_specification)


def test_stale_semantically_compatible_specification_cannot_confirm_current_evidence() -> None:
    lead, broad, narrow, broad_specification, narrow_specification = exact_inputs()
    stale_source = semantic_market_fields("L", "1")
    stale_source["rules_secondary"] = "Use the final published report. Source version A."
    stale_broad = specification_from_market(stale_source)
    assert stale_broad.market_ticker == broad.market_ticker
    assert stale_broad.event_ticker == broad.event_ticker
    assert stale_broad.series_ticker == broad.series_ticker
    assert stale_broad.strategy_supported
    assert stale_broad.market_rules_hash != broad.market_rules_hash
    assert stale_broad.market_metadata_hash == broad.market_metadata_hash
    assert stale_broad.measured_event_or_value == broad_specification.measured_event_or_value
    assert stale_broad.settlement_sources == broad_specification.settlement_sources
    with pytest.raises(OpportunityError, match="market provenance mismatch"):
        confirm_exact(lead, broad, narrow, stale_broad, narrow_specification)


def test_exact_confirmation_maps_broad_yes_narrow_no_and_preserves_fee_uncertainty() -> None:
    lead, broad, narrow, broad_specification, narrow_specification = exact_inputs()
    result = confirm_exact(lead, broad, narrow, broad_specification, narrow_specification)
    assert result.state is ConfirmationState.FINAL_FEE_UNKNOWN_PREFILL
    assert (result.broad_side, result.narrow_side) == (OutcomeSide.YES, OutcomeSide.NO)
    assert result.minimum_guaranteed_settlement_payout == Decimal(2)
    assert result.exact_gross_package_cost == Decimal("2.20")
    assert result.gross_structural_gap == Decimal("-.20")
    assert result.broad_centicent_formula_fee == broad.yes.centicent_rounded_fee  # type: ignore[union-attr]
    assert result.narrow_centicent_formula_fee == narrow.no.centicent_rounded_fee  # type: ignore[union-attr]
    assert result.formula_adjusted_structural_gap == (
        result.gross_structural_gap - result.centicent_formula_fees  # type: ignore[operator]
    )
    assert result.final_net_profit is result.guaranteed_net_profit is None
    assert result.research_only and result.production_influence == 0
    assert not any(
        hasattr(result, name)
        for name in ("trade_candidate", "decision_receipt", "risk_intent", "order")
    )


def test_confirmation_rejects_identity_quantity_and_research_boundary_mismatches() -> None:
    lead, broad, narrow, broad_specification, narrow_specification = exact_inputs()
    with pytest.raises(OpportunityError, match="ticker"):
        confirm_exact(
            lead,
            replace(broad, market_ticker="WRONG"),
            narrow,
            broad_specification,
            narrow_specification,
        )
    with pytest.raises(OpportunityError, match="event"):
        confirm_exact(
            lead,
            replace(broad, event_ticker="WRONG"),
            narrow,
            broad_specification,
            narrow_specification,
        )
    with pytest.raises(OpportunityError, match="provenance mismatch"):
        confirm_exact(
            lead,
            replace(broad, market_metadata_hash="WRONG"),
            narrow,
            broad_specification,
            narrow_specification,
        )
    with pytest.raises(OpportunityError, match="cohort"):
        confirm_exact(
            replace(lead, cohort_identity="WRONG"),
            broad,
            narrow,
            broad_specification,
            narrow_specification,
        )
    with pytest.raises(OpportunityError, match="quantities"):
        confirm_exact(
            lead,
            broad,
            replace(narrow, requested_quantity=Decimal(1)),
            broad_specification,
            narrow_specification,
        )
    with pytest.raises(OpportunityError, match="research-only"):
        confirm_exact(
            lead,
            replace(broad, production_influence=Decimal(".1")),
            narrow,
            broad_specification,
            narrow_specification,
        )


def test_confirmation_reports_each_insufficient_depth_without_manufactured_cost() -> None:
    lead, broad_route, narrow_route = lead_fixture()
    broad_specification = specification("L", "1")
    narrow_specification = specification("H", "2")
    broad_short = evidence("L", broad_route.rules_hash, broad_route.metadata_hash, yes_depth="1")
    broad_full = evidence("L", broad_route.rules_hash, broad_route.metadata_hash)
    narrow_short = evidence("H", narrow_route.rules_hash, narrow_route.metadata_hash, no_depth="1")
    narrow_full = evidence("H", narrow_route.rules_hash, narrow_route.metadata_hash)
    broad_result = confirm_exact(
        lead, broad_short, narrow_full, broad_specification, narrow_specification
    )
    narrow_result = confirm_exact(
        lead, broad_full, narrow_short, broad_specification, narrow_specification
    )
    assert broad_result.state is ConfirmationState.INSUFFICIENT_BROAD_YES_DEPTH
    assert narrow_result.state is ConfirmationState.INSUFFICIENT_NARROW_NO_DEPTH
    for result in (broad_result, narrow_result):
        assert result.exact_gross_package_cost is None
        assert result.minimum_guaranteed_settlement_payout is None
        assert result.formula_adjusted_structural_gap is None
