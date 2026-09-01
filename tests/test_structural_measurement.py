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
    StructuralConfirmation,
    StructuralLead,
    confirm_structural_lead,
    scan_structural_markets,
)
from services.opportunity_engine.structural_measurement import (
    FeeTreatment,
    LeadLifetime,
    MeasurementState,
    compute_lifetime,
    record_ambiguous,
    record_disappeared,
    record_discovery_only,
    record_exact_confirmation,
    record_fee_unknown,
    record_stale,
    relationship_id,
    summarize_run,
)

NOW = datetime(2026, 8, 15, 13, tzinfo=UTC)


def event(ticker: str = "E") -> Event:
    return Event.parse(
        {
            "event_ticker": ticker,
            "series_ticker": "S",
            "title": "Observed event",
            "category": "Economics",
        }
    )


def semantic_market_fields(ticker: str, threshold: object) -> dict[str, Any]:
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


def market(ticker: str, threshold: object = "1") -> Market:
    return Market.parse(semantic_market_fields(ticker, threshold))


def quote(bid: str, ask: str, *, size: str = "5") -> DiscoveryQuotes:
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
        }
    )


def inverted_lead() -> tuple[StructuralLead, Market, Market]:
    low = market("LOW", "1")
    high = market("HIGH", "2")
    result = scan_structural_markets(
        [low, high],
        events={"E": event()},
        discovery_quotes={"LOW": quote(".20", ".45"), "HIGH": quote(".55", ".60")},
        source_authority="test-authority",
    )
    assert len(result.leads) == 1
    return result.leads[0], low, high


def specification(ticker: str, threshold: str) -> ContractSpecification:
    bundle = SemanticsInputBundle.build(
        semantic_market_fields(ticker, threshold),
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


def evidence(
    ticker: str,
    market_layer: Market,
    *,
    quantity: Decimal = Decimal(1),
    # Raw per-side bid prices (mirrors the exact Kalshi orderbook_fp shape): the executable
    # yes_ask/no_ask this evidence produces are DERIVED as complements of the OTHER side's raw
    # bid (yes_ask = 1 - no_bid_raw, no_ask = 1 - yes_bid_raw) -- see
    # services.opportunity_engine.books.normalize_binary_book. Defaults reproduce canonical
    # M27B's own test fixture (yes_ask=.40, no_ask=.70): a deliberately unfavorable book.
    yes_bid_raw: str = ".30",
    no_bid_raw: str = ".60",
    depth: str = "5",
) -> MarketEconomicsEvidence:
    ladder = PriceLadder.parse("linear_cent", [{"start": "0", "end": "1", "step": ".01"}])
    observation = normalize_live_orderbook(
        {
            "ticker": ticker,
            "orderbook_fp": {
                "yes_dollars": [[yes_bid_raw, depth]],
                "no_dollars": [[no_bid_raw, depth]],
            },
        },
        ticker=ticker,
        ladder=ladder,
        source_id=f"book-{ticker}",
        observed_at=NOW,
        market_rules_hash=market_layer.rules_hash,
    )
    series = CurrentSeriesFeeObservation.parse(
        {
            "ticker": "S",
            "title": "Series",
            "category": "Economics",
            "frequency": "event",
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
        market_rules_hash=market_layer.rules_hash,
        market_metadata_hash=market_layer.metadata_hash,
        price_range_hash=observation.price_range_hash,
        event_fee_hash=regime.event_metadata_hash,
        series_fee_observation_id=regime.series_observation_id,
        resolved_fee_regime_id=regime.regime_id,
        fee_policy_id=policy.policy_id,
        orderbook_source_id=observation.source_id,
        orderbook_source_hash=observation.source_hash,
        market_observed_at=NOW,
        orderbook_observed_at=NOW,
        economics_observed_at=NOW,
        requested_quantity=quantity,
        yes=cost(OutcomeSide.YES),
        no=cost(OutcomeSide.NO),
        replay_input=replay_input,
    )


def confirm(
    lead: StructuralLead, broad: MarketEconomicsEvidence, narrow: MarketEconomicsEvidence
) -> StructuralConfirmation:
    return confirm_structural_lead(
        lead,
        broad,
        narrow,
        broad_specification=specification("LOW", "1"),
        narrow_specification=specification("HIGH", "2"),
    )


# -- relationship_id ---------------------------------------------------------------------------


def test_relationship_id_is_stable_across_quote_changes_but_not_across_rules_changes() -> None:
    lead, low, high = inverted_lead()
    result_moved = scan_structural_markets(
        [low, high],
        events={"E": event()},
        discovery_quotes={"LOW": quote(".21", ".46"), "HIGH": quote(".56", ".61")},
        source_authority="test-authority",
    )
    moved_lead = result_moved.leads[0]
    assert lead.lead_id != moved_lead.lead_id
    assert relationship_id(lead) == relationship_id(moved_lead)

    different_high = market("HIGH", "3")
    result_amended = scan_structural_markets(
        [low, different_high],
        events={"E": event()},
        discovery_quotes={"LOW": quote(".20", ".45"), "HIGH": quote(".55", ".60")},
        source_authority="test-authority",
    )
    assert relationship_id(result_amended.leads[0]) != relationship_id(lead)


# -- constructors -------------------------------------------------------------------------------


def test_record_discovery_only_requires_a_reason_and_carries_no_confirmed_economics() -> None:
    lead, _low, _high = inverted_lead()
    observation = record_discovery_only(
        lead,
        relationship_id_value=relationship_id(lead),
        scan_run_id="scan-1",
        observed_at=NOW,
        blocker_reason="contract specification not strategy-supported",
    )
    assert observation.state is MeasurementState.DISCOVERY_ONLY
    assert observation.fee_treatment is FeeTreatment.NOT_ATTEMPTED
    assert observation.confirmed_depth is None and observation.formula_adjusted_gap is None
    assert observation.production_influence == 0 and observation.research_only
    with pytest.raises(OpportunityError, match="non-empty blocker"):
        record_discovery_only(
            lead,
            relationship_id_value=relationship_id(lead),
            scan_run_id="scan-1",
            observed_at=NOW,
            blocker_reason="",
        )


def test_record_fee_unknown_never_carries_a_positive_after_cost_claim() -> None:
    lead, _low, _high = inverted_lead()
    observation = record_fee_unknown(
        lead,
        relationship_id_value=relationship_id(lead),
        scan_run_id="scan-1",
        observed_at=NOW,
        gross_apparent_gap=Decimal("0.10"),
        confirmed_depth=Decimal(5),
        blocker_reason="fee regime unresolved: unsupported fee type",
    )
    assert observation.state is MeasurementState.FEE_UNKNOWN
    assert observation.fee_treatment is FeeTreatment.FEE_UNKNOWN
    assert observation.formula_adjusted_gap is None
    assert observation.gross_apparent_gap == Decimal("0.10")


def test_record_exact_confirmation_maps_insufficient_depth() -> None:
    lead, low, high = inverted_lead()
    broad_short = evidence("LOW", low, depth="0.5")
    narrow_full = evidence("HIGH", high)
    confirmation = confirm(lead, broad_short, narrow_full)
    assert confirmation.state is ConfirmationState.INSUFFICIENT_BROAD_YES_DEPTH
    observation = record_exact_confirmation(
        lead,
        confirmation,
        relationship_id_value=relationship_id(lead),
        scan_run_id="scan-1",
        observed_at=NOW,
    )
    assert observation.state is MeasurementState.INSUFFICIENT_DEPTH
    assert observation.fee_treatment is FeeTreatment.NOT_ATTEMPTED
    assert observation.formula_adjusted_gap is None


def test_record_exact_confirmation_maps_fee_eliminated_gap_to_exact_confirmed() -> None:
    lead, low, high = inverted_lead()
    # Default fixture asks (.30/.60 complement) cost more than the deterministic $1 payout even
    # before fees -- this is the "fee eliminates apparent lead" scenario.
    broad = evidence("LOW", low)
    narrow = evidence("HIGH", high)
    confirmation = confirm(lead, broad, narrow)
    assert confirmation.state is ConfirmationState.FINAL_FEE_UNKNOWN_PREFILL
    assert confirmation.formula_adjusted_structural_gap is not None
    assert confirmation.formula_adjusted_structural_gap <= 0
    observation = record_exact_confirmation(
        lead,
        confirmation,
        relationship_id_value=relationship_id(lead),
        scan_run_id="scan-1",
        observed_at=NOW,
    )
    assert observation.state is MeasurementState.EXACT_CONFIRMED
    assert observation.fee_treatment is FeeTreatment.CANONICAL_FORMULA_FEE
    assert observation.confirmation_id == confirmation.confirmation_id


def test_record_exact_confirmation_maps_positive_after_cost_gap() -> None:
    lead, low, high = inverted_lead()
    # Cheap asks on both legs: deterministic $1 payout comfortably exceeds cost + fees.
    broad = evidence("LOW", low, yes_bid_raw=".05", no_bid_raw=".85")  # yes_ask == .15
    narrow = evidence("HIGH", high, yes_bid_raw=".85", no_bid_raw=".05")  # no_ask == .15
    confirmation = confirm(lead, broad, narrow)
    assert confirmation.formula_adjusted_structural_gap is not None
    assert confirmation.formula_adjusted_structural_gap > 0
    observation = record_exact_confirmation(
        lead,
        confirmation,
        relationship_id_value=relationship_id(lead),
        scan_run_id="scan-1",
        observed_at=NOW,
    )
    assert observation.state is MeasurementState.AFTER_COST_POSITIVE_RESEARCH
    assert observation.formula_adjusted_gap == confirmation.formula_adjusted_structural_gap


def test_record_exact_confirmation_rejects_a_mismatched_confirmation() -> None:
    lead, low, high = inverted_lead()
    other_lead, _l2, _h2 = inverted_lead()
    broad = evidence("LOW", low)
    narrow = evidence("HIGH", high)
    confirmation = confirm(lead, broad, narrow)
    mismatched = replace(confirmation, lead_id="not-this-lead")
    with pytest.raises(OpportunityError, match="does not belong to this exact lead"):
        record_exact_confirmation(
            other_lead,
            mismatched,
            relationship_id_value=relationship_id(other_lead),
            scan_run_id="scan-1",
            observed_at=NOW,
        )


def test_measurement_boundary_rejects_forged_leads_and_confirmations() -> None:
    lead, low, high = inverted_lead()
    with pytest.raises(OpportunityError, match="research-only"):
        record_discovery_only(
            replace(lead, production_influence=Decimal("1")),
            relationship_id_value=relationship_id(lead),
            scan_run_id="scan-1",
            observed_at=NOW,
            blocker_reason="forged",
        )
    with pytest.raises(OpportunityError, match="identity formula"):
        record_discovery_only(
            replace(lead, lead_id="forged"),
            relationship_id_value=relationship_id(lead),
            scan_run_id="scan-1",
            observed_at=NOW,
            blocker_reason="forged",
        )
    confirmation = confirm(lead, evidence("LOW", low), evidence("HIGH", high))
    with pytest.raises(OpportunityError, match="research-only"):
        record_exact_confirmation(
            lead,
            replace(confirmation, production_influence=Decimal("1")),
            relationship_id_value=relationship_id(lead),
            scan_run_id="scan-1",
            observed_at=NOW,
        )
    with pytest.raises(OpportunityError, match="identity formula"):
        record_exact_confirmation(
            lead,
            replace(confirmation, confirmation_id="forged"),
            relationship_id_value=relationship_id(lead),
            scan_run_id="scan-1",
            observed_at=NOW,
        )
    with pytest.raises(OpportunityError, match="economics"):
        record_exact_confirmation(
            lead,
            replace(
                confirmation,
                exact_gross_package_cost=Decimal("0"),
            ),
            relationship_id_value=relationship_id(lead),
            scan_run_id="scan-1",
            observed_at=NOW,
        )


def test_record_stale_disappeared_and_ambiguous_require_a_previous_observation_and_reason() -> None:
    lead, _low, _high = inverted_lead()
    previous = record_discovery_only(
        lead,
        relationship_id_value=relationship_id(lead),
        scan_run_id="scan-1",
        observed_at=NOW,
        blocker_reason="initial blocker",
    )
    stale = record_stale(
        previous,
        scan_run_id="scan-2",
        observed_at=NOW + timedelta(minutes=15),
        blocker_reason="stale quote",
    )
    assert stale.state is MeasurementState.STALE
    assert stale.relationship_id == previous.relationship_id
    with pytest.raises(OpportunityError, match="non-empty"):
        record_stale(previous, scan_run_id="scan-2", observed_at=NOW, blocker_reason="")

    disappeared = record_disappeared(
        previous, scan_run_id="scan-3", observed_at=NOW + timedelta(minutes=30)
    )
    assert disappeared.state is MeasurementState.DISAPPEARED
    assert disappeared.lead_id is None

    ambiguous = record_ambiguous(
        previous,
        scan_run_id="scan-4",
        observed_at=NOW + timedelta(minutes=45),
        blocker_reason="cohort became ambiguous",
    )
    assert ambiguous.state is MeasurementState.AMBIGUOUS
    with pytest.raises(OpportunityError, match="cannot be converted"):
        record_disappeared(ambiguous, scan_run_id="scan-5", observed_at=NOW)
    with pytest.raises(OpportunityError, match="non-empty"):
        record_ambiguous(previous, scan_run_id="scan-4", observed_at=NOW, blocker_reason="")


def test_observation_invariants_are_fail_closed() -> None:
    lead, low, high = inverted_lead()
    broad = evidence("LOW", low, yes_bid_raw=".05", no_bid_raw=".85")
    narrow = evidence("HIGH", high, yes_bid_raw=".85", no_bid_raw=".05")
    confirmation = confirm(lead, broad, narrow)
    observation = record_exact_confirmation(
        lead,
        confirmation,
        relationship_id_value=relationship_id(lead),
        scan_run_id="scan-1",
        observed_at=NOW,
    )
    with pytest.raises(OpportunityError, match="research-only"):
        replace(observation, production_influence=Decimal("0.01"))
    with pytest.raises(OpportunityError, match="timezone-aware"):
        replace(observation, observed_at=datetime(2026, 1, 1))
    with pytest.raises(OpportunityError, match="positive formula-adjusted gap"):
        replace(observation, formula_adjusted_gap=None)
    with pytest.raises(OpportunityError, match="canonical formula-fee treatment"):
        replace(
            observation,
            state=MeasurementState.EXACT_CONFIRMED,
            fee_treatment=FeeTreatment.FEE_UNKNOWN,
        )


# -- lifetime -------------------------------------------------------------------------------


def test_compute_lifetime_tracks_persistence_and_right_censors_active_relationships() -> None:
    lead, _low, _high = inverted_lead()
    rel_id = relationship_id(lead)
    first = record_discovery_only(
        lead, relationship_id_value=rel_id, scan_run_id="s1", observed_at=NOW, blocker_reason="r"
    )
    second = record_discovery_only(
        lead,
        relationship_id_value=rel_id,
        scan_run_id="s2",
        observed_at=NOW + timedelta(minutes=15),
        blocker_reason="r",
    )
    third = record_discovery_only(
        lead,
        relationship_id_value=rel_id,
        scan_run_id="s3",
        observed_at=NOW + timedelta(minutes=30),
        blocker_reason="r",
    )
    lifetime = compute_lifetime([third, first, second])
    assert lifetime.still_active
    assert lifetime.disappeared_at is None
    assert lifetime.observed_lifetime_upper_bound_seconds is None
    assert lifetime.observed_lifetime_lower_bound_seconds == Decimal(1800)
    assert lifetime.consecutive_observations == 3
    assert lifetime.observation_count == 3


def test_compute_lifetime_closes_on_disappearance() -> None:
    lead, _low, _high = inverted_lead()
    rel_id = relationship_id(lead)
    first = record_discovery_only(
        lead, relationship_id_value=rel_id, scan_run_id="s1", observed_at=NOW, blocker_reason="r"
    )
    gone = record_disappeared(first, scan_run_id="s2", observed_at=NOW + timedelta(minutes=15))
    lifetime = compute_lifetime([first, gone])
    assert not lifetime.still_active
    assert lifetime.disappeared_at == NOW + timedelta(minutes=15)
    assert lifetime.observed_lifetime_upper_bound_seconds == Decimal(900)
    assert lifetime.consecutive_observations == 0


def test_compute_lifetime_rejects_observations_after_a_closed_gap() -> None:
    lead, _low, _high = inverted_lead()
    first = record_discovery_only(
        lead,
        relationship_id_value=relationship_id(lead),
        scan_run_id="scan-1",
        observed_at=NOW,
        blocker_reason="initial blocker",
    )
    gone = record_disappeared(first, scan_run_id="scan-2", observed_at=NOW + timedelta(minutes=15))
    returned = record_discovery_only(
        lead,
        relationship_id_value=relationship_id(lead),
        scan_run_id="scan-3",
        observed_at=NOW + timedelta(minutes=30),
        blocker_reason="returned",
    )
    with pytest.raises(OpportunityError, match="closed observation gap"):
        compute_lifetime([first, gone, returned])


def test_ambiguous_observation_censors_lifetime_without_extending_lower_bound() -> None:
    lead, _low, _high = inverted_lead()
    first = record_discovery_only(
        lead,
        relationship_id_value=relationship_id(lead),
        scan_run_id="scan-1",
        observed_at=NOW,
        blocker_reason="initial blocker",
    )
    ambiguous = record_ambiguous(
        first,
        scan_run_id="scan-2",
        observed_at=NOW + timedelta(minutes=15),
        blocker_reason="cohort became ambiguous",
    )
    lifetime = compute_lifetime([first, ambiguous])
    assert lifetime.first_seen_at == NOW
    assert lifetime.last_seen_at == NOW
    assert lifetime.observed_lifetime_lower_bound_seconds == Decimal(0)
    assert lifetime.consecutive_observations == 0
    assert lifetime.disappeared_at is None
    assert not lifetime.still_active
    assert lifetime.ambiguity_censored_at == NOW + timedelta(minutes=15)
    assert lifetime.observed_lifetime_upper_bound_seconds is None
    summary = summarize_run(
        [first, ambiguous], [lifetime], scans_completed=2, independent_cohorts_observed=1
    )
    assert summary.still_active_count == 0
    assert summary.disappeared_count == 0
    assert summary.ambiguity_censored_count == 1


def test_ambiguous_interval_cannot_be_bridged_by_later_visible_observation() -> None:
    lead, _low, _high = inverted_lead()
    first = record_discovery_only(
        lead,
        relationship_id_value=relationship_id(lead),
        scan_run_id="scan-1",
        observed_at=NOW,
        blocker_reason="initial blocker",
    )
    ambiguous = record_ambiguous(
        first,
        scan_run_id="scan-2",
        observed_at=NOW + timedelta(minutes=15),
        blocker_reason="cohort became ambiguous",
    )
    returned = record_discovery_only(
        lead,
        relationship_id_value=relationship_id(lead),
        scan_run_id="scan-3",
        observed_at=NOW + timedelta(minutes=30),
        blocker_reason="returned after censoring",
    )
    with pytest.raises(OpportunityError, match="ambiguous observation gap"):
        compute_lifetime([first, ambiguous, returned])


def test_ambiguous_then_disappeared_is_rejected_as_two_terminal_states() -> None:
    lead, _low, _high = inverted_lead()
    first = record_discovery_only(
        lead,
        relationship_id_value=relationship_id(lead),
        scan_run_id="scan-1",
        observed_at=NOW,
        blocker_reason="initial blocker",
    )
    ambiguous = record_ambiguous(
        first,
        scan_run_id="scan-2",
        observed_at=NOW + timedelta(minutes=15),
        blocker_reason="cohort became ambiguous",
    )
    disappeared = record_disappeared(
        first, scan_run_id="scan-3", observed_at=NOW + timedelta(minutes=30)
    )
    assert ambiguous.state is MeasurementState.AMBIGUOUS
    assert disappeared.state is MeasurementState.DISAPPEARED
    with pytest.raises(OpportunityError, match="terminal observation"):
        compute_lifetime([first, ambiguous, disappeared])


def test_malformed_lifetime_status_is_rejected() -> None:
    fields = dict(
        relationship_id="relationship",
        event_ticker="event",
        broad_market_ticker="broad",
        narrow_market_ticker="narrow",
        first_seen_at=NOW,
        last_seen_at=NOW,
        observation_count=1,
        consecutive_observations=1,
        still_active=True,
        disappeared_at=NOW + timedelta(minutes=15),
        ambiguity_censored_at=None,
        observed_lifetime_lower_bound_seconds=Decimal(0),
        observed_lifetime_upper_bound_seconds=Decimal(900),
        maximum_gross_inversion=None,
        maximum_confirmed_depth=None,
        maximum_after_cost_gap=None,
    )
    with pytest.raises(OpportunityError, match="exactly one"):
        LeadLifetime(**fields)


def test_active_lifetime_with_upper_bound_is_rejected() -> None:
    fields = dict(
        relationship_id="relationship",
        event_ticker="event",
        broad_market_ticker="broad",
        narrow_market_ticker="narrow",
        first_seen_at=NOW,
        last_seen_at=NOW,
        observation_count=1,
        consecutive_observations=1,
        still_active=True,
        disappeared_at=None,
        ambiguity_censored_at=None,
        observed_lifetime_lower_bound_seconds=Decimal(0),
        observed_lifetime_upper_bound_seconds=Decimal(900),
        maximum_gross_inversion=None,
        maximum_confirmed_depth=None,
        maximum_after_cost_gap=None,
    )
    with pytest.raises(OpportunityError, match="active lifetime"):
        LeadLifetime(**fields)


def test_disappeared_lifetime_without_upper_bound_is_rejected() -> None:
    fields = dict(
        relationship_id="relationship",
        event_ticker="event",
        broad_market_ticker="broad",
        narrow_market_ticker="narrow",
        first_seen_at=NOW,
        last_seen_at=NOW,
        observation_count=1,
        consecutive_observations=0,
        still_active=False,
        disappeared_at=NOW + timedelta(minutes=15),
        ambiguity_censored_at=None,
        observed_lifetime_lower_bound_seconds=Decimal(0),
        observed_lifetime_upper_bound_seconds=None,
        maximum_gross_inversion=None,
        maximum_confirmed_depth=None,
        maximum_after_cost_gap=None,
    )
    with pytest.raises(OpportunityError, match="requires an upper bound"):
        LeadLifetime(**fields)


def test_compute_lifetime_rejects_mixed_relationships() -> None:
    lead, _low, _high = inverted_lead()
    other_lead, _l2, _h2 = inverted_lead()
    a = record_discovery_only(
        lead,
        relationship_id_value=relationship_id(lead),
        scan_run_id="s1",
        observed_at=NOW,
        blocker_reason="r",
    )
    b = record_discovery_only(
        other_lead,
        relationship_id_value="different",
        scan_run_id="s1",
        observed_at=NOW,
        blocker_reason="r",
    )
    with pytest.raises(OpportunityError, match="share one relationship_id"):
        compute_lifetime([a, b])
    with pytest.raises(OpportunityError, match="zero observations"):
        compute_lifetime([])


def test_compute_lifetime_rejects_duplicate_scans_and_mixed_market_identities() -> None:
    lead, _low, _high = inverted_lead()
    first = record_discovery_only(
        lead,
        relationship_id_value=relationship_id(lead),
        scan_run_id="scan-1",
        observed_at=NOW,
        blocker_reason="r",
    )
    duplicate = record_discovery_only(
        lead,
        relationship_id_value=relationship_id(lead),
        scan_run_id="scan-1",
        observed_at=NOW + timedelta(minutes=1),
        blocker_reason="different content",
    )
    with pytest.raises(OpportunityError, match="duplicate scan_run_id"):
        compute_lifetime([first, duplicate])
    mixed = replace(first, scan_run_id="scan-2", narrow_market_ticker="OTHER")
    with pytest.raises(OpportunityError, match="event and market identities"):
        compute_lifetime([first, mixed])


def test_lifetime_rejects_every_malformed_integrity_field() -> None:
    lead, _low, _high = inverted_lead()
    first = record_discovery_only(
        lead,
        relationship_id_value=relationship_id(lead),
        scan_run_id="scan-1",
        observed_at=NOW,
        blocker_reason="r",
    )
    valid_active = compute_lifetime([first])
    bad_active = (
        {"relationship_id": ""},
        {"first_seen_at": datetime(2026, 1, 1)},
        {"first_seen_at": NOW + timedelta(minutes=1)},
        {"observation_count": 0},
        {"consecutive_observations": 2},
        {"consecutive_observations": 0},
        {"observed_lifetime_lower_bound_seconds": Decimal("-1")},
        {"observed_lifetime_lower_bound_seconds": Decimal("1")},
    )
    for changes in bad_active:
        with pytest.raises(OpportunityError):
            replace(valid_active, **changes)

    gone = record_disappeared(first, scan_run_id="scan-2", observed_at=NOW)
    valid_disappeared = compute_lifetime([first, gone])
    bad_disappeared = (
        {"consecutive_observations": 1},
        {"disappeared_at": NOW - timedelta(minutes=1)},
        {"observed_lifetime_upper_bound_seconds": Decimal("-1")},
        {"observed_lifetime_upper_bound_seconds": Decimal("1")},
    )
    for changes in bad_disappeared:
        with pytest.raises(OpportunityError):
            replace(valid_disappeared, **changes)

    ambiguous = record_ambiguous(
        first,
        scan_run_id="scan-3",
        observed_at=NOW,
        blocker_reason="ambiguous",
    )
    valid_ambiguous = compute_lifetime([first, ambiguous])
    with pytest.raises(OpportunityError):
        replace(valid_ambiguous, ambiguity_censored_at=NOW - timedelta(minutes=1))


def test_summary_rejects_duplicate_lifetime_identities() -> None:
    lead, _low, _high = inverted_lead()
    observation = record_discovery_only(
        lead,
        relationship_id_value=relationship_id(lead),
        scan_run_id="scan-1",
        observed_at=NOW,
        blocker_reason="r",
    )
    lifetime = compute_lifetime([observation])
    with pytest.raises(OpportunityError, match="unique relationship identities"):
        summarize_run(
            [observation], [lifetime, lifetime], scans_completed=1, independent_cohorts_observed=1
        )


# -- summary ---------------------------------------------------------------------------------


def test_summarize_run_separates_frequency_persistence_and_executability() -> None:
    lead, low, high = inverted_lead()
    rel_id = relationship_id(lead)
    broad = evidence("LOW", low, yes_bid_raw=".05", no_bid_raw=".85")
    narrow = evidence("HIGH", high, yes_bid_raw=".85", no_bid_raw=".05")
    confirmation = confirm(lead, broad, narrow)
    positive = record_exact_confirmation(
        lead, confirmation, relationship_id_value=rel_id, scan_run_id="s1", observed_at=NOW
    )
    lifetime = compute_lifetime([positive])
    summary = summarize_run(
        [positive], [lifetime], scans_completed=1, independent_cohorts_observed=1
    )
    assert summary.scans_completed == 1
    assert summary.discovery_lead_count == 1
    assert summary.leads_per_event == Decimal(1)
    assert summary.after_cost_positive_count == 1
    assert summary.exact_confirmation_rate == Decimal(1)
    assert summary.gross_gap_distribution and summary.depth_distribution
    assert summary.still_active_count == 1
    assert summary.disappeared_count == 0
    assert summary.ambiguity_censored_count == 0
    with pytest.raises(OpportunityError, match="non-negative"):
        summarize_run([], [], scans_completed=-1, independent_cohorts_observed=0)
