"""Pure planner for passive, structure-aware shadow quotes.

The planner emits immutable research proposals only.  It cannot import the risk engine, build a
client order ID, form an exchange request, access credentials, or contact a network.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import ROUND_FLOOR, Decimal

from services.historical_replay.archive import stable_hash
from services.opportunity_engine.authoritative_economics import (
    validate_authoritative_economics_market_binding,
)
from services.opportunity_engine.books import ExecutableLevel, NormalizedBook, OutcomeSide
from services.opportunity_engine.domain import OpportunityError
from services.opportunity_engine.fees import FeePolicy, calculate_fee
from services.opportunity_engine.live_economics import MarketEconomicsEvidence

from .domain import (
    FairValueCurve,
    FairValueEligibility,
    FairValuePoint,
    InventorySnapshot,
    MarketMakingError,
    QuoteBlocker,
    QuotePolicy,
    ShadowMarketSnapshot,
    ShadowQuote,
    ShadowQuotePlan,
    ShadowQuoteState,
)


def default_shadow_quote_policy() -> QuotePolicy:
    """Conservative research policy aligned with the repository's reviewed 5% hurdle."""
    return QuotePolicy.build(
        policy_id="mm-a1-one-contract-shadow-v2",
        tick_size=Decimal("0.01"),
        quote_quantity=Decimal(1),
        minimum_net_edge=Decimal("0.05"),
        adverse_selection_reserve=Decimal("0.01"),
        latency_volatility_reserve=Decimal("0.01"),
        capital_turnover_reserve=Decimal("0.005"),
        inventory_skew_per_contract=Decimal("0.005"),
        maximum_inventory_skew=Decimal("0.02"),
        maximum_book_age=timedelta(seconds=2),
        maximum_inventory_age=timedelta(seconds=2),
        close_guard=timedelta(minutes=15),
    )


def _snapshot_valid(snapshot: ShadowMarketSnapshot) -> bool:
    try:
        rebuilt = ShadowMarketSnapshot.build(
            market_ticker=snapshot.market_ticker,
            event_id=snapshot.event_id,
            rules_hash=snapshot.rules_hash,
            specification_hash=snapshot.specification_hash,
            observed_at=snapshot.observed_at,
            book_observed_at=snapshot.book_observed_at,
            closes_at=snapshot.closes_at,
            book_source_hash=snapshot.book_source_hash,
            economics_evidence_id=snapshot.economics_evidence_id,
            book=snapshot.book,
            sequence_contiguous=snapshot.sequence_contiguous,
            market_active=snapshot.market_active,
            market_paused=snapshot.market_paused,
            source_healthy=snapshot.source_healthy,
            own_order_state_known=snapshot.own_order_state_known,
        )
    except MarketMakingError:
        return False
    return (
        snapshot.production_influence == 0
        and snapshot.snapshot_id == rebuilt.snapshot_id
        and snapshot.content_hash == rebuilt.content_hash
    )


def _inventory_valid(inventory: InventorySnapshot) -> bool:
    try:
        rebuilt = InventorySnapshot.build(
            market_ticker=inventory.market_ticker,
            event_id=inventory.event_id,
            observed_at=inventory.observed_at,
            net_yes_contracts=inventory.net_yes_contracts,
            max_abs_yes_contracts=inventory.max_abs_yes_contracts,
            reconciled=inventory.reconciled,
        )
    except MarketMakingError:
        return False
    return (
        inventory.production_influence == 0
        and inventory.inventory_id == rebuilt.inventory_id
        and inventory.content_hash == rebuilt.content_hash
    )


def _economics_valid(economics: MarketEconomicsEvidence) -> bool:
    if (
        economics.analysis_type != "TAKER_NOW"
        or not economics.research_only
        or economics.production_influence != 0
    ):
        return False
    try:
        rebuilt = MarketEconomicsEvidence.create(
            market_ticker=economics.market_ticker,
            event_ticker=economics.event_ticker,
            series_ticker=economics.series_ticker,
            market_source_id=economics.market_source_id,
            market_rules_hash=economics.market_rules_hash,
            market_metadata_hash=economics.market_metadata_hash,
            price_range_hash=economics.price_range_hash,
            event_fee_hash=economics.event_fee_hash,
            series_fee_observation_id=economics.series_fee_observation_id,
            resolved_fee_regime_id=economics.resolved_fee_regime_id,
            fee_policy_id=economics.fee_policy_id,
            orderbook_source_id=economics.orderbook_source_id,
            orderbook_source_hash=economics.orderbook_source_hash,
            market_observed_at=economics.market_observed_at,
            orderbook_observed_at=economics.orderbook_observed_at,
            economics_observed_at=economics.economics_observed_at,
            requested_quantity=economics.requested_quantity,
            yes=economics.yes,
            no=economics.no,
            replay_input=economics.replay_input,
        )
    except OpportunityError:
        return False
    return economics == rebuilt


def _floor_tick(value: Decimal, tick: Decimal) -> Decimal:
    return (value / tick).to_integral_value(rounding=ROUND_FLOOR) * tick


def _side_levels(
    book: NormalizedBook, side: OutcomeSide
) -> tuple[tuple[ExecutableLevel, ...], tuple[ExecutableLevel, ...]]:
    if side is OutcomeSide.YES:
        return book.yes_bids, book.yes_asks
    return book.no_bids, book.no_asks


def _side_blocker(side: OutcomeSide, *, edge: bool) -> QuoteBlocker:
    if side is OutcomeSide.YES:
        return QuoteBlocker.YES_EDGE_BELOW_HURDLE if edge else QuoteBlocker.YES_NOT_COMPETITIVE
    return QuoteBlocker.NO_EDGE_BELOW_HURDLE if edge else QuoteBlocker.NO_NOT_COMPETITIVE


def _propose_side(
    *,
    point: FairValuePoint,
    market: ShadowMarketSnapshot,
    book: NormalizedBook,
    inventory: InventorySnapshot,
    fee_policy: FeePolicy,
    policy: QuotePolicy,
    side: OutcomeSide,
) -> tuple[ShadowQuote | None, QuoteBlocker | None]:
    after = (
        inventory.net_yes_contracts + policy.quote_quantity
        if side is OutcomeSide.YES
        else inventory.net_yes_contracts - policy.quote_quantity
    )
    reducing = abs(after) < abs(inventory.net_yes_contracts)
    if abs(after) > inventory.max_abs_yes_contracts:
        return None, QuoteBlocker.INVENTORY_LIMIT

    bids, asks = _side_levels(book, side)
    if not bids or not asks:
        return None, _side_blocker(side, edge=False)
    best_bid, best_ask = bids[0].price, asks[0].price
    improve = best_bid + policy.tick_size
    competitive_target = improve if improve < best_ask else best_bid

    raw_skew = inventory.net_yes_contracts * policy.inventory_skew_per_contract
    skew = max(-policy.maximum_inventory_skew, min(policy.maximum_inventory_skew, raw_skew))
    desired = competitive_target - skew if side is OutcomeSide.YES else competitive_target + skew
    post_only_cap = best_ask - policy.tick_size
    price = _floor_tick(min(desired, post_only_cap), policy.tick_size)
    if price < best_bid or price <= 0 or price >= best_ask:
        return None, _side_blocker(side, edge=False)

    conservative = point.lower_yes if side is OutcomeSide.YES else Decimal(1) - point.upper_yes
    non_fee_reserve = (
        policy.adverse_selection_reserve
        + policy.latency_volatility_reserve
        + policy.capital_turnover_reserve
    )
    try:
        fee = calculate_fee(fee_policy, price, policy.quote_quantity, maker=True).total_fee
    except OpportunityError:
        return None, QuoteBlocker.FEE_UNVERIFIED
    edge = conservative - price - fee / policy.quote_quantity - non_fee_reserve
    if edge < policy.minimum_net_edge:
        return None, _side_blocker(side, edge=True)
    quote_id = stable_hash(
        (
            "mm-a1-shadow-quote-v1",
            market.snapshot_id,
            inventory.inventory_id,
            policy.content_hash,
            fee_policy.policy_id,
            side,
            str(price),
            str(policy.quote_quantity),
            str(conservative),
            str(fee),
            str(non_fee_reserve),
            str(edge),
            str(inventory.net_yes_contracts),
            str(after),
            reducing,
        )
    )
    return (
        ShadowQuote(
            quote_id,
            side,
            price,
            policy.quote_quantity,
            conservative,
            fee,
            non_fee_reserve,
            edge,
            inventory.net_yes_contracts,
            after,
            reducing,
        ),
        None,
    )


def _finish(
    *,
    curve: FairValueCurve,
    market: ShadowMarketSnapshot,
    inventory: InventorySnapshot,
    fee_policy: FeePolicy,
    economics: MarketEconomicsEvidence,
    policy: QuotePolicy,
    state: ShadowQuoteState,
    quotes: tuple[ShadowQuote, ...],
    blockers: set[QuoteBlocker],
) -> ShadowQuotePlan:
    ordered_blockers = tuple(sorted(blockers, key=str))
    material = (
        "mm-a1-shadow-plan-v1",
        market.observed_at,
        market.market_ticker,
        market.event_id,
        curve.cohort_id,
        curve.curve_id,
        market.snapshot_id,
        inventory.inventory_id,
        economics.evidence_id,
        fee_policy.policy_id,
        policy.content_hash,
        state,
        tuple(quote.quote_id for quote in quotes),
        ordered_blockers,
    )
    return ShadowQuotePlan(
        stable_hash(material),
        market.observed_at,
        market.market_ticker,
        market.event_id,
        curve.cohort_id,
        curve.curve_id,
        market.snapshot_id,
        inventory.inventory_id,
        economics.evidence_id,
        fee_policy.policy_id,
        policy.policy_id,
        state,
        quotes,
        ordered_blockers,
    )


def plan_shadow_quotes(
    *,
    curve: FairValueCurve,
    market: ShadowMarketSnapshot,
    inventory: InventorySnapshot,
    economics: MarketEconomicsEvidence,
    economics_binding: object,
    policy: QuotePolicy,
) -> ShadowQuotePlan:
    """Create a zero-authority passive quote plan or an explicit abstention."""
    blockers: set[QuoteBlocker] = set()
    try:
        validated_curve = curve.validated_copy()
    except MarketMakingError:
        validated_curve = curve
        blockers.add(QuoteBlocker.FAIR_VALUE_IDENTITY_MISMATCH)
    try:
        validated_policy = policy.validated_copy()
    except MarketMakingError:
        validated_policy = policy
        blockers.add(QuoteBlocker.QUOTE_POLICY_INVALID)
    fee_policy = economics.replay_input.fee_policy
    if not _economics_valid(economics):
        blockers.add(QuoteBlocker.ECONOMICS_IDENTITY_MISMATCH)
    try:
        binding = validate_authoritative_economics_market_binding(
            economics_binding,
            economics=economics,
            expected_market_ticker=market.market_ticker,
            expected_event_ticker=market.event_id,
        )
    except (OpportunityError, TypeError, ValueError):
        binding = None
    if binding is None or not binding.succeeded:
        blockers.add(QuoteBlocker.ECONOMICS_BINDING_INVALID)
    if not _snapshot_valid(market):
        blockers.add(QuoteBlocker.MARKET_SNAPSHOT_IDENTITY_MISMATCH)
    if not _inventory_valid(inventory):
        blockers.add(QuoteBlocker.INVENTORY_IDENTITY_MISMATCH)
    if validated_curve.eligibility is not FairValueEligibility.ELIGIBLE_SHADOW_RESEARCH:
        blockers.add(QuoteBlocker.FAIR_VALUE_INELIGIBLE)
    if not validated_curve.issued_at <= market.observed_at < validated_curve.expires_at:
        blockers.add(QuoteBlocker.FAIR_VALUE_STALE)
    if market.event_id != validated_curve.event_id:
        blockers.add(QuoteBlocker.FAIR_VALUE_IDENTITY_MISMATCH)
    points = [
        point for point in validated_curve.points if point.market_ticker == market.market_ticker
    ]
    point = points[0] if len(points) == 1 else None
    if point is None:
        blockers.add(QuoteBlocker.MARKET_NOT_IN_CURVE)
    elif point.rules_hash != market.rules_hash:
        blockers.add(QuoteBlocker.RULES_MISMATCH)
    elif point.specification_hash != market.specification_hash:
        blockers.add(QuoteBlocker.SPECIFICATION_MISMATCH)
    if (
        economics.evidence_id != market.economics_evidence_id
        or economics.market_ticker != market.market_ticker
        or economics.event_ticker != market.event_id
        or economics.market_rules_hash != market.rules_hash
        or economics.orderbook_source_hash != market.book_source_hash
        or economics.orderbook_observed_at != market.book_observed_at
        or economics.replay_input.book_observation.book != market.book
        or economics.requested_quantity != validated_policy.quote_quantity
    ):
        blockers.add(QuoteBlocker.ECONOMICS_IDENTITY_MISMATCH)
    if binding is not None and binding.succeeded and binding.rules_hash != market.rules_hash:
        blockers.add(QuoteBlocker.ECONOMICS_IDENTITY_MISMATCH)
    if (
        economics.economics_observed_at > market.observed_at
        or market.observed_at - economics.economics_observed_at > validated_policy.maximum_book_age
    ):
        blockers.add(QuoteBlocker.ECONOMICS_STALE)
    if market.observed_at - market.book_observed_at > validated_policy.maximum_book_age:
        blockers.add(QuoteBlocker.BOOK_STALE)
    if not market.sequence_contiguous:
        blockers.add(QuoteBlocker.BOOK_SEQUENCE_GAP)
    if not market.market_active:
        blockers.add(QuoteBlocker.MARKET_INACTIVE)
    if market.market_paused:
        blockers.add(QuoteBlocker.MARKET_PAUSED)
    if not market.source_healthy:
        blockers.add(QuoteBlocker.SOURCE_UNHEALTHY)
    if not market.own_order_state_known:
        blockers.add(QuoteBlocker.OWN_ORDER_STATE_UNKNOWN)
    if market.closes_at - market.observed_at <= validated_policy.close_guard:
        blockers.add(QuoteBlocker.TOO_CLOSE_TO_CLOSE)
    if market.observed_at - inventory.observed_at > validated_policy.maximum_inventory_age:
        blockers.add(QuoteBlocker.INVENTORY_STALE)
    if not inventory.reconciled:
        blockers.add(QuoteBlocker.INVENTORY_UNRECONCILED)
    if inventory.market_ticker != market.market_ticker or inventory.event_id != market.event_id:
        blockers.add(QuoteBlocker.INVENTORY_IDENTITY_MISMATCH)
    if abs(inventory.net_yes_contracts) > inventory.max_abs_yes_contracts:
        blockers.add(QuoteBlocker.INVENTORY_LIMIT)
    if not fee_policy.verified:
        blockers.add(QuoteBlocker.FEE_UNVERIFIED)
    if not fee_policy.applies_at(market.observed_at):
        blockers.add(QuoteBlocker.FEE_NOT_EFFECTIVE)

    if blockers or point is None:
        return _finish(
            curve=curve,
            market=market,
            inventory=inventory,
            fee_policy=fee_policy,
            economics=economics,
            policy=validated_policy,
            state=ShadowQuoteState.ABSTAIN,
            quotes=(),
            blockers=blockers,
        )

    quotes: list[ShadowQuote] = []
    for side in (OutcomeSide.YES, OutcomeSide.NO):
        quote, blocker = _propose_side(
            point=point,
            market=market,
            book=economics.replay_input.book_observation.book,
            inventory=inventory,
            fee_policy=fee_policy,
            policy=validated_policy,
            side=side,
        )
        if quote is not None:
            quotes.append(quote)
        if blocker is not None:
            blockers.add(blocker)

    if len(quotes) == 2:
        state = ShadowQuoteState.TWO_SIDED
    elif len(quotes) == 1 and quotes[0].inventory_reducing:
        state = ShadowQuoteState.ONE_SIDED_INVENTORY_REDUCTION
    else:
        if quotes:
            blockers.add(QuoteBlocker.TWO_SIDED_REQUIRED)
        quotes = []
        state = ShadowQuoteState.ABSTAIN
    return _finish(
        curve=curve,
        market=market,
        inventory=inventory,
        fee_policy=fee_policy,
        economics=economics,
        policy=validated_policy,
        state=state,
        quotes=tuple(quotes),
        blockers=blockers,
    )
