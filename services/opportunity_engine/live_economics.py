"""M27A research-only live market economics evidence (never an opportunity)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from typing import Any

from services.historical_replay.archive import stable_hash
from services.market_universe.domain import UniverseValidationError, exact
from services.market_universe.pricing import PriceLadder

from .books import (
    DepthWalk,
    NormalizedBook,
    OutcomeSide,
    RawBidLevel,
    normalize_binary_book,
    walk_depth,
)
from .domain import OpportunityError
from .fees import FeeEstimateQuality, FeePolicy, calculate_fee
from .live_fees import ResolvedFeeRegime


@dataclass(frozen=True, slots=True)
class DiscoveryQuotes:
    yes_bid: Decimal | None
    yes_ask: Decimal | None
    yes_bid_size: Decimal
    yes_ask_size: Decimal
    no_bid: Decimal | None
    no_ask: Decimal | None
    volume: Decimal
    volume_24h: Decimal
    open_interest: Decimal
    liquidity: Decimal
    source_hash: str
    research_only: bool = True
    production_influence: Decimal = Decimal(0)

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> DiscoveryQuotes:
        values = {
            name: exact(raw.get(name), name)
            for name in (
                "yes_bid_dollars",
                "yes_ask_dollars",
                "yes_bid_size_fp",
                "yes_ask_size_fp",
                "no_bid_dollars",
                "no_ask_dollars",
                "volume_fp",
                "volume_24h_fp",
                "open_interest_fp",
                "liquidity_dollars",
            )
        }
        if (
            values["yes_ask_dollars"] != 1 - values["no_bid_dollars"]
            or values["no_ask_dollars"] != 1 - values["yes_bid_dollars"]
        ):
            raise UniverseValidationError("broad quote complement mismatch")
        if any(values[name] < 0 for name in values):
            raise UniverseValidationError("negative broad quote field")
        yes_bid = None if values["yes_bid_dollars"] == 0 else values["yes_bid_dollars"]
        yes_ask = None if values["yes_ask_dollars"] == 1 else values["yes_ask_dollars"]
        no_bid = None if values["no_bid_dollars"] == 0 else values["no_bid_dollars"]
        no_ask = None if values["no_ask_dollars"] == 1 else values["no_ask_dollars"]
        return cls(
            yes_bid,
            yes_ask,
            values["yes_bid_size_fp"],
            values["yes_ask_size_fp"],
            no_bid,
            no_ask,
            values["volume_fp"],
            values["volume_24h_fp"],
            values["open_interest_fp"],
            values["liquidity_dollars"],
            stable_hash(raw),
        )


@dataclass(frozen=True, slots=True)
class BookObservation:
    book: NormalizedBook
    source_id: str
    source_hash: str
    observed_at: datetime
    market_rules_hash: str
    price_range_hash: str


def normalize_live_orderbook(
    raw: dict[str, Any],
    *,
    ticker: str,
    ladder: PriceLadder,
    source_id: str,
    observed_at: datetime,
    market_rules_hash: str,
) -> BookObservation:
    if raw.get("ticker") != ticker or not isinstance(raw.get("orderbook_fp"), dict):
        raise OpportunityError("orderbook market identity mismatch")
    levels: list[RawBidLevel] = []
    for key, side in (("yes_dollars", OutcomeSide.YES), ("no_dollars", OutcomeSide.NO)):
        rows = raw["orderbook_fp"].get(key)
        if not isinstance(rows, list):
            raise OpportunityError("orderbook side missing")
        seen: set[Decimal] = set()
        for row in rows:
            if not isinstance(row, list) or len(row) != 2:
                raise OpportunityError("malformed orderbook level")
            price, quantity = exact(row[0], "orderbook price"), exact(row[1], "orderbook quantity")
            if price in seen or not ladder.is_valid(price):
                raise OpportunityError("duplicate or off-ladder orderbook level")
            seen.add(price)
            levels.append(
                RawBidLevel(stable_hash((source_id, side.value, str(price))), side, price, quantity)
            )
    source_hash = stable_hash(raw)
    price_hash = stable_hash((ladder.structure, ladder.ranges))
    return BookObservation(
        normalize_binary_book(tuple(levels)),
        source_id,
        source_hash,
        _utc(observed_at),
        market_rules_hash,
        price_hash,
    )


@dataclass(frozen=True, slots=True)
class TakerCost:
    side: OutcomeSide
    depth: DepthWalk
    deterministic_fee: Decimal
    centicent_rounded_fee: Decimal
    fill_rounding_reserve: Decimal | None
    rebate_uncertainty: Decimal | None
    conservative_total_entry_cost: Decimal | None
    fee_quality: FeeEstimateQuality


@dataclass(frozen=True, slots=True)
class MarketEconomicsReplayInput:
    book_observation: BookObservation
    price_ladder: PriceLadder
    fee_regime: ResolvedFeeRegime
    fee_policy: FeePolicy

    def __post_init__(self) -> None:
        observation = self.book_observation
        expected_range_hash = stable_hash((self.price_ladder.structure, self.price_ladder.ranges))
        if observation.price_range_hash != expected_range_hash:
            raise OpportunityError("replay price-range hash mismatch")
        raw_levels = tuple(
            RawBidLevel(
                level.raw_level_id,
                level.raw_side,
                level.raw_price,
                level.quantity,
            )
            for level in observation.book.yes_bids + observation.book.no_bids
        )
        if any(not self.price_ladder.is_valid(level.price) for level in raw_levels):
            raise OpportunityError("replay book contains off-ladder level")
        if normalize_binary_book(raw_levels) != observation.book:
            raise OpportunityError("replay book is not canonically normalized")
        regime = self.fee_regime
        expected_regime_id = stable_hash(
            (
                regime.version,
                regime.fee_type,
                regime.fee_multiplier,
                regime.source,
                regime.series_observation_id,
                regime.event_metadata_hash,
            )
        )
        if regime.regime_id != expected_regime_id:
            raise OpportunityError("replay fee regime identity mismatch")
        if (
            self.fee_policy.fee_type is not regime.fee_type
            or self.fee_policy.fee_multiplier != regime.fee_multiplier
        ):
            raise OpportunityError("replay fee policy incompatible with resolved regime")


def taker_cost(
    book: NormalizedBook, side: OutcomeSide, quantity: Decimal, policy: FeePolicy
) -> TakerCost:
    asks = book.yes_asks if side is OutcomeSide.YES else book.no_asks
    walk = walk_depth(asks, quantity)
    if not walk.complete:
        raise OpportunityError("insufficient displayed depth")
    theoretical = sum(
        (
            calculate_fee(
                policy,
                level.price,
                min(
                    level.quantity,
                    max(
                        Decimal(0),
                        quantity - sum((prior.quantity for prior in asks[:index]), Decimal(0)),
                    ),
                ),
            ).theoretical_trade_fee
            for index, level in enumerate(asks)
            if quantity > sum((prior.quantity for prior in asks[:index]), Decimal(0))
        ),
        Decimal(0),
    )
    centicent = (theoretical / Decimal("0.0001")).to_integral_value(
        rounding=ROUND_CEILING
    ) * Decimal("0.0001")
    # Fill fragmentation and accumulator behavior are not present in a pre-trade book.
    return TakerCost(
        side,
        walk,
        theoretical,
        centicent,
        None,
        None,
        None,
        FeeEstimateQuality.DETERMINISTIC_FORMULA_ONLY,
    )


@dataclass(frozen=True, slots=True)
class MarketEconomicsEvidence:
    evidence_id: str
    market_ticker: str
    event_ticker: str
    series_ticker: str
    market_source_id: str
    market_rules_hash: str
    market_metadata_hash: str
    price_range_hash: str
    event_fee_hash: str
    series_fee_observation_id: str
    resolved_fee_regime_id: str
    fee_policy_id: str
    orderbook_source_id: str
    orderbook_source_hash: str
    market_observed_at: datetime
    orderbook_observed_at: datetime
    economics_observed_at: datetime
    requested_quantity: Decimal
    yes: TakerCost | None
    no: TakerCost | None
    replay_input: MarketEconomicsReplayInput
    analysis_type: str = "TAKER_NOW"
    research_only: bool = True
    production_influence: Decimal = Decimal(0)

    @classmethod
    def create(cls, **values: Any) -> MarketEconomicsEvidence:
        if (
            values.get("production_influence", Decimal(0)) != 0
            or values.get("research_only", True) is not True
        ):
            raise OpportunityError("economics evidence must remain research-only")
        required_text = (
            "market_ticker",
            "event_ticker",
            "series_ticker",
            "market_source_id",
            "market_rules_hash",
            "market_metadata_hash",
            "price_range_hash",
            "event_fee_hash",
            "series_fee_observation_id",
            "resolved_fee_regime_id",
            "fee_policy_id",
            "orderbook_source_id",
            "orderbook_source_hash",
        )
        if any(not isinstance(values.get(name), str) or not values[name] for name in required_text):
            raise OpportunityError("economics evidence provenance incomplete")
        quantity = values.get("requested_quantity")
        if not isinstance(quantity, Decimal) or not quantity.is_finite() or quantity <= 0:
            raise OpportunityError("economics evidence quantity invalid")
        market_at = values.get("market_observed_at")
        book_at = values.get("orderbook_observed_at")
        economics_at = values.get("economics_observed_at")
        if (
            not isinstance(market_at, datetime)
            or not isinstance(book_at, datetime)
            or not isinstance(economics_at, datetime)
        ):
            raise OpportunityError("economics evidence timestamps missing")
        if _utc(market_at) > _utc(economics_at) or _utc(book_at) > _utc(economics_at):
            raise OpportunityError("impossible economics timestamp ordering")
        replay_input = values.get("replay_input")
        if not isinstance(replay_input, MarketEconomicsReplayInput):
            raise OpportunityError("economics replay input missing")
        observation = replay_input.book_observation
        regime = replay_input.fee_regime
        policy = replay_input.fee_policy
        if (
            values["orderbook_source_id"] != observation.source_id
            or values["orderbook_source_hash"] != observation.source_hash
            or _utc(book_at) != observation.observed_at
            or values["market_rules_hash"] != observation.market_rules_hash
        ):
            raise OpportunityError("economics orderbook provenance mismatch")
        if values["price_range_hash"] != observation.price_range_hash:
            raise OpportunityError("economics price-range provenance mismatch")
        if values["resolved_fee_regime_id"] != regime.regime_id:
            raise OpportunityError("economics fee regime provenance mismatch")
        if (
            values["series_fee_observation_id"] != regime.series_observation_id
            or values["event_fee_hash"] != regime.event_metadata_hash
        ):
            raise OpportunityError("economics fee regime source mismatch")
        if values["fee_policy_id"] != policy.policy_id:
            raise OpportunityError("economics fee policy provenance mismatch")
        replayed_yes, replayed_no = _replay_costs(replay_input, quantity)
        if values.get("yes") != replayed_yes or values.get("no") != replayed_no:
            raise OpportunityError("economics results do not match replay inputs")
        digest = stable_hash(tuple(sorted(values.items())))
        return cls(evidence_id=digest, **values)


def replay_market_economics(
    evidence: MarketEconomicsEvidence,
) -> tuple[TakerCost | None, TakerCost | None]:
    """Recompute both TAKER_NOW sides using only immutable evidence material."""
    return _replay_costs(evidence.replay_input, evidence.requested_quantity)


def _replay_costs(
    replay_input: MarketEconomicsReplayInput, quantity: Decimal
) -> tuple[TakerCost | None, TakerCost | None]:
    book = replay_input.book_observation.book
    policy = replay_input.fee_policy
    return (
        _replay_side(book, OutcomeSide.YES, quantity, policy),
        _replay_side(book, OutcomeSide.NO, quantity, policy),
    )


def _replay_side(
    book: NormalizedBook, side: OutcomeSide, quantity: Decimal, policy: FeePolicy
) -> TakerCost | None:
    asks = book.yes_asks if side is OutcomeSide.YES else book.no_asks
    if not walk_depth(asks, quantity).complete:
        return None
    return taker_cost(book, side, quantity, policy)


def validate_economics_time(
    *, market_at: datetime, book_at: datetime, observed_at: datetime, maximum_book_age: timedelta
) -> None:
    market, book, observed = _utc(market_at), _utc(book_at), _utc(observed_at)
    if market > observed or book > observed or observed - book > maximum_book_age:
        raise OpportunityError("stale or impossible economics timestamp ordering")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise OpportunityError("timestamp must be timezone-aware")
    return value.astimezone(UTC)
