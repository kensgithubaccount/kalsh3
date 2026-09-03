"""Immutable, zero-authority contracts for structure-aware shadow market making.

These objects are deliberately not exchange orders, risk intents, or execution envelopes.  They
record what a passive maker *would* quote after validated fair-value, book, fee, and inventory
checks.  Production influence is permanently zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise

from services.historical_replay.archive import stable_hash
from services.opportunity_engine.books import ExecutableLevel, NormalizedBook, OutcomeSide


class MarketMakingError(ValueError):
    """A market-making research invariant failed closed."""


class ComparisonDirection(StrEnum):
    GREATER_THAN = "GREATER_THAN"
    LESS_THAN = "LESS_THAN"


class FairValueEligibility(StrEnum):
    ELIGIBLE_SHADOW_RESEARCH = "ELIGIBLE_SHADOW_RESEARCH"
    INELIGIBLE = "INELIGIBLE"
    QUARANTINED = "QUARANTINED"


class ShadowQuoteState(StrEnum):
    TWO_SIDED = "TWO_SIDED"
    ONE_SIDED_INVENTORY_REDUCTION = "ONE_SIDED_INVENTORY_REDUCTION"
    ABSTAIN = "ABSTAIN"


class QuoteBlocker(StrEnum):
    FAIR_VALUE_INELIGIBLE = "FAIR_VALUE_INELIGIBLE"
    FAIR_VALUE_STALE = "FAIR_VALUE_STALE"
    FAIR_VALUE_IDENTITY_MISMATCH = "FAIR_VALUE_IDENTITY_MISMATCH"
    MARKET_SNAPSHOT_IDENTITY_MISMATCH = "MARKET_SNAPSHOT_IDENTITY_MISMATCH"
    INVENTORY_IDENTITY_MISMATCH = "INVENTORY_IDENTITY_MISMATCH"
    MARKET_NOT_IN_CURVE = "MARKET_NOT_IN_CURVE"
    RULES_MISMATCH = "RULES_MISMATCH"
    SPECIFICATION_MISMATCH = "SPECIFICATION_MISMATCH"
    BOOK_STALE = "BOOK_STALE"
    BOOK_SEQUENCE_GAP = "BOOK_SEQUENCE_GAP"
    MARKET_INACTIVE = "MARKET_INACTIVE"
    MARKET_PAUSED = "MARKET_PAUSED"
    SOURCE_UNHEALTHY = "SOURCE_UNHEALTHY"
    OWN_ORDER_STATE_UNKNOWN = "OWN_ORDER_STATE_UNKNOWN"
    ECONOMICS_BINDING_INVALID = "ECONOMICS_BINDING_INVALID"
    ECONOMICS_IDENTITY_MISMATCH = "ECONOMICS_IDENTITY_MISMATCH"
    ECONOMICS_STALE = "ECONOMICS_STALE"
    FEE_UNVERIFIED = "FEE_UNVERIFIED"
    FEE_NOT_EFFECTIVE = "FEE_NOT_EFFECTIVE"
    QUOTE_POLICY_INVALID = "QUOTE_POLICY_INVALID"
    INVENTORY_STALE = "INVENTORY_STALE"
    INVENTORY_UNRECONCILED = "INVENTORY_UNRECONCILED"
    INVENTORY_LIMIT = "INVENTORY_LIMIT"
    TOO_CLOSE_TO_CLOSE = "TOO_CLOSE_TO_CLOSE"
    YES_EDGE_BELOW_HURDLE = "YES_EDGE_BELOW_HURDLE"
    NO_EDGE_BELOW_HURDLE = "NO_EDGE_BELOW_HURDLE"
    YES_NOT_COMPETITIVE = "YES_NOT_COMPETITIVE"
    NO_NOT_COMPETITIVE = "NO_NOT_COMPETITIVE"
    TWO_SIDED_REQUIRED = "TWO_SIDED_REQUIRED"


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MarketMakingError(f"{field} must be timezone-aware")


def _finite(value: Decimal, field: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise MarketMakingError(f"{field} must be a finite Decimal")


@dataclass(frozen=True, slots=True)
class FairValuePoint:
    market_ticker: str
    threshold: Decimal
    probability_yes: Decimal
    lower_yes: Decimal
    upper_yes: Decimal
    rules_hash: str
    specification_hash: str

    def __post_init__(self) -> None:
        _finite(self.threshold, "threshold")
        for value, name in (
            (self.probability_yes, "probability_yes"),
            (self.lower_yes, "lower_yes"),
            (self.upper_yes, "upper_yes"),
        ):
            _finite(value, name)
        if not Decimal(0) <= self.lower_yes <= self.probability_yes <= self.upper_yes <= Decimal(1):
            raise MarketMakingError("fair-value interval is invalid")
        if not self.market_ticker or not self.rules_hash or not self.specification_hash:
            raise MarketMakingError("fair-value point identity is incomplete")


@dataclass(frozen=True, slots=True)
class FairValueCurve:
    curve_id: str
    event_id: str
    cohort_id: str
    comparison: ComparisonDirection
    points: tuple[FairValuePoint, ...]
    model_id: str
    model_version: str
    calibration_id: str
    evidence_manifest_id: str
    validation_receipt_id: str
    issued_at: datetime
    expires_at: datetime
    eligibility: FairValueEligibility
    content_hash: str
    production_influence: Decimal = Decimal(0)

    @classmethod
    def build(
        cls,
        *,
        event_id: str,
        cohort_id: str,
        comparison: ComparisonDirection,
        points: tuple[FairValuePoint, ...],
        model_id: str,
        model_version: str,
        calibration_id: str,
        evidence_manifest_id: str,
        validation_receipt_id: str,
        issued_at: datetime,
        expires_at: datetime,
        eligibility: FairValueEligibility,
    ) -> FairValueCurve:
        _aware(issued_at, "issued_at")
        _aware(expires_at, "expires_at")
        if expires_at <= issued_at:
            raise MarketMakingError("fair-value authority expiry is invalid")
        identities = (
            event_id,
            cohort_id,
            model_id,
            model_version,
            calibration_id,
            evidence_manifest_id,
        )
        if any(not value for value in identities):
            raise MarketMakingError("fair-value curve identity is incomplete")
        if (
            eligibility is FairValueEligibility.ELIGIBLE_SHADOW_RESEARCH
            and not validation_receipt_id
        ):
            raise MarketMakingError("eligible fair value requires a validation receipt")
        if len(points) < 2:
            raise MarketMakingError("structure-aware curve requires at least two siblings")
        if len({point.market_ticker for point in points}) != len(points):
            raise MarketMakingError("fair-value curve contains duplicate market tickers")
        ordered = tuple(sorted(points, key=lambda point: point.threshold))
        if len({point.threshold for point in ordered}) != len(ordered):
            raise MarketMakingError("fair-value curve contains duplicate thresholds")
        probability_fields = ("lower_yes", "probability_yes", "upper_yes")
        for field in probability_fields:
            values = tuple(getattr(point, field) for point in ordered)
            if comparison is ComparisonDirection.GREATER_THAN:
                coherent = all(left >= right for left, right in pairwise(values))
            else:
                coherent = all(left <= right for left, right in pairwise(values))
            if not coherent:
                raise MarketMakingError("fair-value curve violates threshold monotonicity")
        material = (
            "mm-a1-fair-value-curve-v1",
            event_id,
            cohort_id,
            comparison,
            ordered,
            model_id,
            model_version,
            calibration_id,
            evidence_manifest_id,
            validation_receipt_id,
            issued_at,
            expires_at,
            eligibility,
        )
        digest = stable_hash(material)
        return cls(
            digest,
            event_id,
            cohort_id,
            comparison,
            ordered,
            model_id,
            model_version,
            calibration_id,
            evidence_manifest_id,
            validation_receipt_id,
            issued_at,
            expires_at,
            eligibility,
            digest,
        )

    def validated_copy(self) -> FairValueCurve:
        rebuilt = FairValueCurve.build(
            event_id=self.event_id,
            cohort_id=self.cohort_id,
            comparison=self.comparison,
            points=self.points,
            model_id=self.model_id,
            model_version=self.model_version,
            calibration_id=self.calibration_id,
            evidence_manifest_id=self.evidence_manifest_id,
            validation_receipt_id=self.validation_receipt_id,
            issued_at=self.issued_at,
            expires_at=self.expires_at,
            eligibility=self.eligibility,
        )
        if (
            self.production_influence != 0
            or self.curve_id != rebuilt.curve_id
            or self.content_hash != rebuilt.content_hash
        ):
            raise MarketMakingError("fair-value curve identity or authority changed")
        return rebuilt


def _book_material(book: NormalizedBook) -> tuple[object, ...]:
    def levels(values: tuple[ExecutableLevel, ...]) -> tuple[tuple[str, ...], ...]:
        return tuple(
            (
                str(level.outcome),
                str(level.book_side),
                str(level.price),
                str(level.quantity),
                str(level.raw_level_id),
                str(level.raw_side),
                str(level.raw_price),
            )
            for level in values
        )

    return (
        levels(book.yes_bids),
        levels(book.yes_asks),
        levels(book.no_bids),
        levels(book.no_asks),
    )


@dataclass(frozen=True, slots=True)
class ShadowMarketSnapshot:
    snapshot_id: str
    market_ticker: str
    event_id: str
    rules_hash: str
    specification_hash: str
    observed_at: datetime
    book_observed_at: datetime
    closes_at: datetime
    book_source_hash: str
    economics_evidence_id: str
    book: NormalizedBook
    sequence_contiguous: bool
    market_active: bool
    market_paused: bool
    source_healthy: bool
    own_order_state_known: bool
    content_hash: str
    production_influence: Decimal = Decimal(0)

    @classmethod
    def build(cls, **values: object) -> ShadowMarketSnapshot:
        for name in ("observed_at", "book_observed_at", "closes_at"):
            value = values.get(name)
            if not isinstance(value, datetime):
                raise MarketMakingError(f"{name} must be a datetime")
            _aware(value, name)
        observed_at = values["observed_at"]
        book_observed_at = values["book_observed_at"]
        if not isinstance(observed_at, datetime) or not isinstance(book_observed_at, datetime):
            raise MarketMakingError("snapshot timestamps missing")
        if book_observed_at > observed_at:
            raise MarketMakingError("book observation cannot be from the future")
        for name in (
            "market_ticker",
            "event_id",
            "rules_hash",
            "specification_hash",
            "book_source_hash",
            "economics_evidence_id",
        ):
            if not isinstance(values.get(name), str) or not values[name]:
                raise MarketMakingError(f"{name} is required")
        book = values.get("book")
        if not isinstance(book, NormalizedBook):
            raise MarketMakingError("canonical normalized book required")
        material = (
            "mm-a1-market-snapshot-v1",
            tuple(sorted((key, str(value)) for key, value in values.items() if key != "book")),
            _book_material(book),
        )
        digest = stable_hash(material)
        return cls(snapshot_id=digest, content_hash=digest, **values)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class InventorySnapshot:
    inventory_id: str
    market_ticker: str
    event_id: str
    observed_at: datetime
    net_yes_contracts: Decimal
    max_abs_yes_contracts: Decimal
    reconciled: bool
    content_hash: str
    production_influence: Decimal = Decimal(0)

    @classmethod
    def build(
        cls,
        *,
        market_ticker: str,
        event_id: str,
        observed_at: datetime,
        net_yes_contracts: Decimal,
        max_abs_yes_contracts: Decimal,
        reconciled: bool,
    ) -> InventorySnapshot:
        _aware(observed_at, "inventory observed_at")
        _finite(net_yes_contracts, "net_yes_contracts")
        _finite(max_abs_yes_contracts, "max_abs_yes_contracts")
        if not market_ticker or not event_id or max_abs_yes_contracts <= 0:
            raise MarketMakingError("inventory identity or limit is invalid")
        digest = stable_hash(
            (
                "mm-a1-inventory-v1",
                market_ticker,
                event_id,
                observed_at,
                str(net_yes_contracts),
                str(max_abs_yes_contracts),
                reconciled,
            )
        )
        return cls(
            digest,
            market_ticker,
            event_id,
            observed_at,
            net_yes_contracts,
            max_abs_yes_contracts,
            reconciled,
            digest,
        )


@dataclass(frozen=True, slots=True)
class QuotePolicy:
    policy_id: str
    tick_size: Decimal
    quote_quantity: Decimal
    minimum_net_edge: Decimal
    adverse_selection_reserve: Decimal
    latency_volatility_reserve: Decimal
    capital_turnover_reserve: Decimal
    inventory_skew_per_contract: Decimal
    maximum_inventory_skew: Decimal
    maximum_book_age: timedelta
    maximum_inventory_age: timedelta
    close_guard: timedelta
    content_hash: str
    production_influence: Decimal = Decimal(0)

    @classmethod
    def build(
        cls,
        *,
        policy_id: str,
        tick_size: Decimal,
        quote_quantity: Decimal,
        minimum_net_edge: Decimal,
        adverse_selection_reserve: Decimal,
        latency_volatility_reserve: Decimal,
        capital_turnover_reserve: Decimal,
        inventory_skew_per_contract: Decimal,
        maximum_inventory_skew: Decimal,
        maximum_book_age: timedelta,
        maximum_inventory_age: timedelta,
        close_guard: timedelta,
    ) -> QuotePolicy:
        decimals = (
            tick_size,
            quote_quantity,
            minimum_net_edge,
            adverse_selection_reserve,
            latency_volatility_reserve,
            capital_turnover_reserve,
            inventory_skew_per_contract,
            maximum_inventory_skew,
        )
        if any(not value.is_finite() for value in decimals):
            raise MarketMakingError("quote policy contains non-finite Decimal")
        if not policy_id:
            raise MarketMakingError("quote policy identity is invalid")
        if not Decimal("0.04") <= minimum_net_edge <= Decimal("0.08"):
            raise MarketMakingError("minimum net edge must remain inside reviewed 4%-8% range")
        if quote_quantity != Decimal(1):
            raise MarketMakingError("MM-A1 is fixed to one hypothetical contract per side")
        if not Decimal(0) < tick_size < Decimal(1):
            raise MarketMakingError("tick size is invalid")
        if any(value < 0 for value in decimals[3:]):
            raise MarketMakingError("quote reserves and inventory skew must be non-negative")
        if any(value <= timedelta(0) for value in (maximum_book_age, maximum_inventory_age)):
            raise MarketMakingError("freshness windows must be positive")
        if close_guard < timedelta(0):
            raise MarketMakingError("close guard must be non-negative")
        values = (
            "mm-a1-quote-policy-v1",
            policy_id,
            *(str(value) for value in decimals),
            maximum_book_age,
            maximum_inventory_age,
            close_guard,
        )
        digest = stable_hash(values)
        return cls(
            policy_id,
            tick_size,
            quote_quantity,
            minimum_net_edge,
            adverse_selection_reserve,
            latency_volatility_reserve,
            capital_turnover_reserve,
            inventory_skew_per_contract,
            maximum_inventory_skew,
            maximum_book_age,
            maximum_inventory_age,
            close_guard,
            digest,
        )

    def validated_copy(self) -> QuotePolicy:
        rebuilt = QuotePolicy.build(
            policy_id=self.policy_id,
            tick_size=self.tick_size,
            quote_quantity=self.quote_quantity,
            minimum_net_edge=self.minimum_net_edge,
            adverse_selection_reserve=self.adverse_selection_reserve,
            latency_volatility_reserve=self.latency_volatility_reserve,
            capital_turnover_reserve=self.capital_turnover_reserve,
            inventory_skew_per_contract=self.inventory_skew_per_contract,
            maximum_inventory_skew=self.maximum_inventory_skew,
            maximum_book_age=self.maximum_book_age,
            maximum_inventory_age=self.maximum_inventory_age,
            close_guard=self.close_guard,
        )
        if self.production_influence != 0 or self.content_hash != rebuilt.content_hash:
            raise MarketMakingError("quote policy identity or influence changed")
        return rebuilt


@dataclass(frozen=True, slots=True)
class ShadowQuote:
    quote_id: str
    outcome_side: OutcomeSide
    price: Decimal
    quantity: Decimal
    conservative_fair_probability: Decimal
    maker_fee: Decimal
    non_fee_reserve: Decimal
    net_edge_per_contract: Decimal
    inventory_before: Decimal
    inventory_after_full_fill: Decimal
    inventory_reducing: bool
    post_only: bool = True
    cancel_order_on_pause: bool = True
    self_trade_prevention: str = "TAKER_AT_CROSS"
    order_group_required: bool = True
    exchange_order: bool = False
    production_influence: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if (
            self.production_influence != 0
            or self.exchange_order
            or not self.post_only
            or not self.cancel_order_on_pause
            or not self.order_group_required
        ):
            raise MarketMakingError("shadow quote gained execution authority or lost safety flags")


@dataclass(frozen=True, slots=True)
class ShadowQuotePlan:
    plan_id: str
    planned_at: datetime
    market_ticker: str
    event_id: str
    cohort_id: str
    curve_id: str
    market_snapshot_id: str
    inventory_snapshot_id: str
    economics_evidence_id: str
    fee_policy_id: str
    quote_policy_id: str
    state: ShadowQuoteState
    quotes: tuple[ShadowQuote, ...]
    blockers: tuple[QuoteBlocker, ...]
    research_only: bool = True
    production_influence: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        _aware(self.planned_at, "planned_at")
        if not self.research_only or self.production_influence != 0:
            raise MarketMakingError("quote plan must remain research-only")
        if self.state is ShadowQuoteState.TWO_SIDED and {
            quote.outcome_side for quote in self.quotes
        } != {
            OutcomeSide.YES,
            OutcomeSide.NO,
        }:
            raise MarketMakingError("two-sided state requires exact YES and NO shadow quotes")
        if self.state is ShadowQuoteState.ONE_SIDED_INVENTORY_REDUCTION and (
            len(self.quotes) != 1 or not self.quotes[0].inventory_reducing
        ):
            raise MarketMakingError("one-sided state is restricted to inventory reduction")
        if self.state is ShadowQuoteState.ABSTAIN and self.quotes:
            raise MarketMakingError("abstention cannot carry quotes")
