"""WN-A1 live Kalshi market/orderbook evidence and conservative TAKER_NOW economics.

Reuses the repository's single reviewed public Kalshi transport
(``services.market_universe.public_read``), M27E's independent response re-validation
(``services.market_universe.m27e_public_acceptance.validate_response_evidence``), and the
M27A/opportunity-engine TAKER_NOW book-walk and fee primitives
(``services.opportunity_engine.books``, ``.fees``, ``.live_economics.taker_cost``) rather
than inventing a second book-walk or fee formula.

This module does NOT reuse the full M27A/MM-A1 replay-binding envelope
(``MarketEconomicsEvidence.create``): WN-A1 is deliberately the smallest research lane and
only needs one conservative pre-trade taker cost per side, not MM-A1's stricter audit
envelope. Every ``TakerCost`` returned here carries
``FeeEstimateQuality.DETERMINISTIC_FORMULA_ONLY`` -- the pre-fill final exchange fee is
never claimed to be known.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from services.market_universe import public_read
from services.market_universe.domain import Event, Market, Series, stable_hash
from services.market_universe.m27e_public_acceptance import validate_response_evidence
from services.market_universe.pricing import PriceLadder
from services.opportunity_engine.books import (
    NormalizedBook,
    OutcomeSide,
    RawBidLevel,
    normalize_binary_book,
)
from services.opportunity_engine.domain import OpportunityError
from services.opportunity_engine.fees import FeePolicy, FeeType, current_event_formula_policy
from services.opportunity_engine.live_economics import TakerCost, taker_cost

from .wn_a1_domain import PRODUCTION_INFLUENCE, RESEARCH_ONLY, WnA1Error

DEFAULT_QUANTITY = Decimal(1)
MAX_BOOK_AGE = timedelta(minutes=5)

MarketGetter = Callable[[str], dict[str, object]]
EventGetter = Callable[[str], tuple[dict[str, object], bytes]]
OrderbookGetter = Callable[[str], tuple[dict[str, object], bytes]]
Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class KalshiMarketSnapshot:
    market: Market
    event: Event
    series: Series
    yes_best_ask: Decimal | None
    no_best_ask: Decimal | None
    yes_taker_cost: TakerCost | None
    no_taker_cost: TakerCost | None
    quantity: Decimal
    fee_policy: FeePolicy
    market_observed_at: datetime
    orderbook_observed_at: datetime
    snapshot_identity: str
    research_only: bool = RESEARCH_ONLY
    production_influence: Decimal = PRODUCTION_INFLUENCE


def acquire_market_snapshot(
    *,
    market_ticker: str,
    quantity: Decimal = DEFAULT_QUANTITY,
    get_market: MarketGetter = public_read.get_market,
    get_event: EventGetter = public_read.get_event_with_body,
    get_series: MarketGetter = public_read.get,
    get_orderbook: OrderbookGetter = public_read.get_orderbook_with_body,
) -> KalshiMarketSnapshot:
    """Acquire one immutable market/event/series/orderbook snapshot with conservative fees.

    No credentials, no mutation capability -- every getter defaults to the shared bounded
    public GET transport. Callers inject fakes in tests.
    """
    if quantity <= 0 or not quantity.is_finite():
        raise WnA1Error("requested quantity must be a positive finite Decimal")
    market_response = get_market(market_ticker)
    market_payload, market_observed_at = validate_response_evidence(market_response)
    market_raw = market_payload.get("market")
    if not isinstance(market_raw, dict):
        raise WnA1Error("Kalshi market payload malformed")
    market = Market.parse(market_raw)
    if market.ticker != market_ticker:
        raise WnA1Error("Kalshi market ticker mismatch")

    event_response, _event_body = get_event(market.event_ticker)
    event_payload, _event_observed_at = validate_response_evidence(event_response)
    event_raw = event_payload.get("event")
    if not isinstance(event_raw, dict):
        raise WnA1Error("Kalshi event payload malformed")
    event = Event.parse(event_raw)
    if event.ticker != market.event_ticker:
        raise WnA1Error("Kalshi event ticker mismatch")

    series_response = get_series(public_read.BASE + "/series/" + event.series_ticker)
    series_payload, _series_observed_at = validate_response_evidence(series_response)
    series_raw = series_payload.get("series")
    if not isinstance(series_raw, dict):
        raise WnA1Error("Kalshi series payload malformed")
    series = Series.parse(series_raw)
    if series.ticker != event.series_ticker:
        raise WnA1Error("Kalshi series ticker mismatch")

    orderbook_response, _orderbook_body = get_orderbook(market_ticker)
    orderbook_payload, orderbook_observed_at = validate_response_evidence(orderbook_response)
    book = _parse_book(orderbook_payload, market_ticker, market_raw)

    raw_fee_type = market_raw.get("fee_type") or series_raw.get("fee_type") or "quadratic"
    try:
        fee_type = FeeType(raw_fee_type)
    except ValueError as exc:
        raise WnA1Error(f"unsupported Kalshi fee_type: {raw_fee_type!r}") from exc
    fee_multiplier = _decimal_or(series_raw.get("fee_multiplier"), Decimal(1))
    policy = current_event_formula_policy(fee_type=fee_type, fee_multiplier=fee_multiplier)

    yes_cost = _side_cost(book, OutcomeSide.YES, quantity, policy)
    no_cost = _side_cost(book, OutcomeSide.NO, quantity, policy)

    identity_material = (
        "wn-a1-market-snapshot-v1",
        market.ticker,
        market.rules_hash,
        market.metadata_hash,
        event.metadata_hash,
        series.metadata_hash,
        str(quantity),
        orderbook_observed_at.isoformat(),
    )
    snapshot_identity = stable_hash(identity_material)
    return KalshiMarketSnapshot(
        market=market,
        event=event,
        series=series,
        yes_best_ask=book.yes_best_ask,
        no_best_ask=book.no_best_ask,
        yes_taker_cost=yes_cost,
        no_taker_cost=no_cost,
        quantity=quantity,
        fee_policy=policy,
        market_observed_at=market_observed_at,
        orderbook_observed_at=orderbook_observed_at,
        snapshot_identity=snapshot_identity,
    )


def is_fresh(
    snapshot: KalshiMarketSnapshot, at: datetime, *, max_age: timedelta = MAX_BOOK_AGE
) -> bool:
    at_utc = at.astimezone(UTC)
    observed_utc = snapshot.orderbook_observed_at.astimezone(UTC)
    if observed_utc > at_utc:
        return False
    return at_utc - observed_utc <= max_age


def _parse_book(
    payload: dict[str, object], ticker: str, market_raw: dict[str, object]
) -> NormalizedBook:
    orderbooks = payload.get("orderbooks")
    if not isinstance(orderbooks, list) or not orderbooks:
        raise WnA1Error("Kalshi orderbook payload missing")
    row = orderbooks[0]
    if not isinstance(row, dict):
        raise WnA1Error("Kalshi orderbook row malformed")
    ladder = PriceLadder.parse(
        market_raw.get("price_level_structure"), market_raw.get("price_ranges")
    )
    orderbook_fp = row.get("orderbook_fp")
    if not isinstance(orderbook_fp, dict):
        raise WnA1Error("Kalshi orderbook_fp missing")
    levels: list[RawBidLevel] = []
    for key, side in (("yes_dollars", OutcomeSide.YES), ("no_dollars", OutcomeSide.NO)):
        rows = orderbook_fp.get(key) or []
        if not isinstance(rows, list):
            raise WnA1Error("Kalshi orderbook side malformed")
        seen: set[Decimal] = set()
        for entry in rows:
            if not isinstance(entry, list) or len(entry) != 2:
                raise WnA1Error("malformed orderbook level")
            price, size = Decimal(str(entry[0])), Decimal(str(entry[1]))
            if not ladder.is_valid(price) or price in seen:
                raise WnA1Error("duplicate or off-ladder orderbook level")
            seen.add(price)
            levels.append(RawBidLevel(f"{ticker}:{side.value}:{price}", side, price, size))
    return normalize_binary_book(tuple(levels))


def _side_cost(
    book: NormalizedBook, side: OutcomeSide, quantity: Decimal, policy: FeePolicy
) -> TakerCost | None:
    try:
        return taker_cost(book, side, quantity, policy)
    except OpportunityError:
        # Insufficient displayed depth for the requested quantity is a normal, expected
        # research outcome (e.g. a thin book) -- not a WN-A1 evidence failure.
        return None


def _decimal_or(value: object, default: Decimal) -> Decimal:
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError, TypeError):
        return default
