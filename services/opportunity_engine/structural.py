"""M27B research-only universal routing and directional structural scanning."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from services.contract_intelligence.specification import ContractSpecification
from services.forecasting.domain import ModelFamily
from services.market_universe.domain import (
    Event,
    Market,
    MarketStatus,
    UniverseValidationError,
    stable_hash,
)
from services.market_universe.quality import Family, classify

from .books import OutcomeSide
from .domain import OpportunityError
from .live_economics import (
    DiscoveryQuotes,
    MarketEconomicsEvidence,
    replay_market_economics,
)

POLICY_VERSION = "m27b-directional-structural-v1"
ZERO = Decimal("0")
SUPPORTED_STRIKE_TYPES = frozenset(("greater", "greater_or_equal"))
_FIXED_POINT_STRIKE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)")


class RouteState(StrEnum):
    STRUCTURAL_DIRECTIONAL_THRESHOLD = "STRUCTURAL_DIRECTIONAL_THRESHOLD"
    ROUTE_ONLY = "ROUTE_ONLY"
    ABSTAIN = "ABSTAIN"


class StructuralPattern(StrEnum):
    DIRECTIONAL_THRESHOLD = "DIRECTIONAL_THRESHOLD"
    UNSUPPORTED_V1 = "UNSUPPORTED_V1"
    INVALID = "INVALID"


class RouteReason(StrEnum):
    UNSUPPORTED_STRIKE_TYPE = "UNSUPPORTED_STRIKE_TYPE"
    MISSING_EVENT = "MISSING_EVENT"
    MALFORMED_CUSTOM_STRIKE = "MALFORMED_CUSTOM_STRIKE"
    INVALID_FLOOR_STRIKE = "INVALID_FLOOR_STRIKE"
    MIXED_CUSTOM_STRIKE_PRESENCE = "MIXED_CUSTOM_STRIKE_PRESENCE"
    DUPLICATE_THRESHOLD = "DUPLICATE_THRESHOLD"
    INVALID_DISCOVERY_QUOTE = "INVALID_DISCOVERY_QUOTE"
    NON_ACTIVE_MARKET = "NON_ACTIVE_MARKET"
    NON_BINARY_MARKET = "NON_BINARY_MARKET"
    PROVISIONAL_MARKET = "PROVISIONAL_MARKET"
    MULTIVARIATE_MARKET = "MULTIVARIATE_MARKET"


class RelationshipType(StrEnum):
    YES_HIGH_SUBSET_OF_YES_LOW = "YES_HIGH_SUBSET_OF_YES_LOW"


class ConfirmationState(StrEnum):
    FINAL_FEE_UNKNOWN_PREFILL = "FINAL_FEE_UNKNOWN_PREFILL"
    INSUFFICIENT_BROAD_YES_DEPTH = "INSUFFICIENT_BROAD_YES_DEPTH"
    INSUFFICIENT_NARROW_NO_DEPTH = "INSUFFICIENT_NARROW_NO_DEPTH"


@dataclass(frozen=True, slots=True)
class StructuralRoute:
    route_id: str
    market_ticker: str
    event_ticker: str
    series_ticker: str | None
    exchange_category: str | None
    family: Family
    specialist_model_family: ModelFamily | None
    strike_type: str | None
    pattern: StructuralPattern
    subject_identity: str | None
    cohort_identity: str | None
    canonical_custom_strike: str | None
    threshold: Decimal | None
    discovery_quote_available: bool
    discovery_quote_source_hash: str | None
    state: RouteState
    reasons: tuple[RouteReason, ...]
    rules_hash: str
    metadata_hash: str
    source_authority: str
    research_only: bool = True
    production_influence: Decimal = ZERO


@dataclass(frozen=True, slots=True)
class StructuralLead:
    lead_id: str
    relationship_type: RelationshipType
    cohort_identity: str
    event_ticker: str
    broad_market_ticker: str
    narrow_market_ticker: str
    broad_threshold: Decimal
    narrow_threshold: Decimal
    broad_quote_source_hash: str
    narrow_quote_source_hash: str
    broad_rules_hash: str
    broad_metadata_hash: str
    narrow_rules_hash: str
    narrow_metadata_hash: str
    source_authority: str
    indicative_gross_gap: Decimal
    indicative_quantity: Decimal | None
    priority_gap: Decimal
    priority_quantity: Decimal | None
    exact_confirmation_required: bool = True
    research_only: bool = True
    production_influence: Decimal = ZERO


@dataclass(frozen=True, slots=True)
class StructuralConfirmation:
    confirmation_id: str
    lead_id: str
    state: ConfirmationState
    requested_quantity: Decimal
    broad_side: OutcomeSide
    narrow_side: OutcomeSide
    broad_evidence_id: str
    narrow_evidence_id: str
    minimum_guaranteed_settlement_payout: Decimal | None
    exact_gross_package_cost: Decimal | None
    gross_structural_gap: Decimal | None
    broad_centicent_formula_fee: Decimal | None
    narrow_centicent_formula_fee: Decimal | None
    centicent_formula_fees: Decimal | None
    formula_adjusted_structural_gap: Decimal | None
    final_net_profit: None = None
    guaranteed_net_profit: None = None
    research_only: bool = True
    production_influence: Decimal = ZERO


@dataclass(frozen=True, slots=True)
class StructuralScanManifest:
    manifest_id: str
    policy_version: str
    source_authority: str
    content_identity: str
    markets_evaluated: int
    markets_routed: int
    markets_abstained: int
    family_counts: tuple[tuple[str, int], ...]
    strike_type_counts: tuple[tuple[str, int], ...]
    directional_structural_eligible: int
    structural_cohorts: int
    cohorts_rejected_or_ambiguous: int
    discovery_leads: int
    exactly_confirmed_leads: int
    insufficient_depth_confirmations: int
    research_only: bool = True
    production_influence: Decimal = ZERO


@dataclass(frozen=True, slots=True)
class StructuralScanResult:
    routes: tuple[StructuralRoute, ...]
    leads: tuple[StructuralLead, ...]
    manifest: StructuralScanManifest


def scan_structural_markets(
    markets: Iterable[Market],
    *,
    events: Mapping[str, Event],
    discovery_quotes: Mapping[str, DiscoveryQuotes | None],
    source_authority: str,
    confirmations: Iterable[StructuralConfirmation] = (),
) -> StructuralScanResult:
    """Route every market and scan supported cohorts in O(N log N) time."""
    if not source_authority:
        raise OpportunityError("structural scan source authority missing")
    ordered_markets = sorted(markets, key=lambda market: market.ticker)
    if len({market.ticker for market in ordered_markets}) != len(ordered_markets):
        raise OpportunityError("duplicate market ticker in structural scan")
    invalid_quote_tickers = {
        ticker
        for ticker, quote in discovery_quotes.items()
        if quote is not None and not _valid_discovery_quote(quote)
    }
    safe_quotes = {
        ticker: None if ticker in invalid_quote_tickers else quote
        for ticker, quote in discovery_quotes.items()
    }
    routes = [
        _initial_route(
            market,
            events.get(market.event_ticker),
            safe_quotes.get(market.ticker),
            source_authority,
        )
        for market in ordered_markets
    ]
    routes = [
        _with_reason(route, RouteReason.INVALID_DISCOVERY_QUOTE)
        if route.market_ticker in invalid_quote_tickers
        else route
        for route in routes
    ]

    # Presence is checked before cohort partitioning so missing identity never merges with
    # populated identity inside the same Event/strike semantic group.
    presence: dict[tuple[str, str], set[bool]] = {}
    for route in routes:
        if route.state is RouteState.STRUCTURAL_DIRECTIONAL_THRESHOLD:
            key = (route.event_ticker, route.strike_type or "")
            presence.setdefault(key, set()).add(route.canonical_custom_strike is not None)
    mixed_keys = {key for key, values in presence.items() if len(values) > 1}
    routes = [
        _abstain(route, RouteReason.MIXED_CUSTOM_STRIKE_PRESENCE)
        if (route.event_ticker, route.strike_type or "") in mixed_keys
        else route
        for route in routes
    ]

    cohorts: dict[str, list[StructuralRoute]] = {}
    for route in routes:
        if route.state is RouteState.STRUCTURAL_DIRECTIONAL_THRESHOLD and route.cohort_identity:
            cohorts.setdefault(route.cohort_identity, []).append(route)
    rejected = len(mixed_keys)
    duplicate_cohorts = {
        cohort_id
        for cohort_id, members in cohorts.items()
        if len({member.threshold for member in members}) != len(members)
    }
    rejected += len(duplicate_cohorts)
    routes = [
        _abstain(route, RouteReason.DUPLICATE_THRESHOLD)
        if route.cohort_identity in duplicate_cohorts
        else route
        for route in routes
    ]
    leads: list[StructuralLead] = []
    for cohort_id in sorted(set(cohorts) - duplicate_cohorts):
        leads.extend(_scan_cohort(cohorts[cohort_id], safe_quotes))
    leads.sort(key=_lead_order)
    route_tuple = tuple(routes)
    lead_tuple = tuple(leads)
    confirmation_tuple = tuple(confirmations)
    lead_ids = {lead.lead_id for lead in lead_tuple}
    if any(
        confirmation.lead_id not in lead_ids
        or not confirmation.research_only
        or confirmation.production_influence != ZERO
        for confirmation in confirmation_tuple
    ):
        raise OpportunityError("structural scan confirmation provenance mismatch")
    manifest = _manifest(route_tuple, lead_tuple, confirmation_tuple, source_authority, rejected)
    return StructuralScanResult(route_tuple, lead_tuple, manifest)


def confirm_structural_lead(
    lead: StructuralLead,
    broad_evidence: MarketEconomicsEvidence,
    narrow_evidence: MarketEconomicsEvidence,
    *,
    broad_specification: ContractSpecification,
    narrow_specification: ContractSpecification,
) -> StructuralConfirmation:
    """Pure exact-depth review; final pre-fill exchange fees remain unknown."""
    if not lead.research_only or lead.production_influence != ZERO:
        raise OpportunityError("structural lead must remain research-only")
    expected_lead_id = stable_hash(
        (
            POLICY_VERSION,
            lead.cohort_identity,
            lead.broad_market_ticker,
            lead.narrow_market_ticker,
            str(lead.broad_threshold),
            str(lead.narrow_threshold),
            lead.broad_quote_source_hash,
            lead.narrow_quote_source_hash,
            lead.broad_rules_hash,
            lead.broad_metadata_hash,
            lead.narrow_rules_hash,
            lead.narrow_metadata_hash,
            lead.source_authority,
        )
    )
    if lead.lead_id != expected_lead_id:
        raise OpportunityError("structural lead cohort identity mismatch")
    if (
        broad_evidence.market_ticker != lead.broad_market_ticker
        or narrow_evidence.market_ticker != lead.narrow_market_ticker
    ):
        raise OpportunityError("structural confirmation ticker mismatch")
    if (
        broad_evidence.event_ticker != lead.event_ticker
        or narrow_evidence.event_ticker != lead.event_ticker
    ):
        raise OpportunityError("structural confirmation event mismatch")
    _validate_contract_semantics(
        lead,
        broad_evidence,
        narrow_evidence,
        broad_specification,
        narrow_specification,
    )
    if (
        broad_evidence.market_rules_hash != lead.broad_rules_hash
        or broad_evidence.market_metadata_hash != lead.broad_metadata_hash
    ):
        raise OpportunityError("broad evidence structural identity mismatch")
    if (
        narrow_evidence.market_rules_hash != lead.narrow_rules_hash
        or narrow_evidence.market_metadata_hash != lead.narrow_metadata_hash
    ):
        raise OpportunityError("narrow evidence structural identity mismatch")
    if any(
        not evidence.research_only or evidence.production_influence != ZERO
        for evidence in (broad_evidence, narrow_evidence)
    ):
        raise OpportunityError("economics evidence must remain research-only")
    if broad_evidence.requested_quantity != narrow_evidence.requested_quantity:
        raise OpportunityError("structural confirmation quantities must be exactly equal")
    if broad_evidence.orderbook_observed_at != narrow_evidence.orderbook_observed_at:
        raise OpportunityError("cross-market orderbook observations are not simultaneous")
    if replay_market_economics(broad_evidence) != (
        broad_evidence.yes,
        broad_evidence.no,
    ) or replay_market_economics(narrow_evidence) != (
        narrow_evidence.yes,
        narrow_evidence.no,
    ):
        raise OpportunityError("structural confirmation evidence replay mismatch")
    quantity = broad_evidence.requested_quantity
    state = ConfirmationState.FINAL_FEE_UNKNOWN_PREFILL
    broad_cost = broad_evidence.yes
    narrow_cost = narrow_evidence.no
    if broad_cost is None:
        state = ConfirmationState.INSUFFICIENT_BROAD_YES_DEPTH
    elif narrow_cost is None:
        state = ConfirmationState.INSUFFICIENT_NARROW_NO_DEPTH
    if state is not ConfirmationState.FINAL_FEE_UNKNOWN_PREFILL:
        return _confirmation(lead, broad_evidence, narrow_evidence, state, quantity)
    if broad_cost is None or narrow_cost is None:  # pragma: no cover - narrowed above
        raise OpportunityError("structural confirmation side-state invariant failed")
    if broad_cost.side is not OutcomeSide.YES or narrow_cost.side is not OutcomeSide.NO:
        raise OpportunityError("structural confirmation side mismatch")
    gross_cost = broad_cost.depth.total_cost + narrow_cost.depth.total_cost
    fees = broad_cost.centicent_rounded_fee + narrow_cost.centicent_rounded_fee
    return _confirmation(
        lead,
        broad_evidence,
        narrow_evidence,
        state,
        quantity,
        gross_cost=gross_cost,
        broad_fee=broad_cost.centicent_rounded_fee,
        narrow_fee=narrow_cost.centicent_rounded_fee,
        fees=fees,
    )


def _initial_route(
    market: Market,
    event: Event | None,
    quote: DiscoveryQuotes | None,
    source_authority: str,
) -> StructuralRoute:
    strike = market.raw.get("strike_type")
    strike_type = strike if isinstance(strike, str) and strike else None
    raw_category = market.raw.get("category")
    category = event.category if event else raw_category if isinstance(raw_category, str) else None
    family = classify(category or "", (market.title or "") + " " + (event.title if event else ""))
    # Family classification alone does not prove that a concrete specialist applies.
    specialist: ModelFamily | None = None
    base = dict(
        market_ticker=market.ticker,
        event_ticker=market.event_ticker,
        series_ticker=event.series_ticker
        if event
        else _optional_text(market.raw.get("series_ticker")),
        exchange_category=category,
        family=family,
        specialist_model_family=specialist,
        strike_type=strike_type,
        discovery_quote_available=quote is not None
        and quote.yes_bid is not None
        and quote.yes_ask is not None,
        discovery_quote_source_hash=quote.source_hash if quote else None,
        rules_hash=market.rules_hash,
        metadata_hash=market.metadata_hash,
        source_authority=source_authority,
    )
    if event is None:
        return _route(
            **base,
            pattern=StructuralPattern.INVALID,
            state=RouteState.ABSTAIN,
            reasons=(RouteReason.MISSING_EVENT,),
        )
    base_gate_reasons = tuple(
        reason
        for failed, reason in (
            (market.status is not MarketStatus.ACTIVE, RouteReason.NON_ACTIVE_MARKET),
            (market.market_type != "binary", RouteReason.NON_BINARY_MARKET),
            (market.provisional, RouteReason.PROVISIONAL_MARKET),
            (market.multivariate, RouteReason.MULTIVARIATE_MARKET),
        )
        if failed
    )
    if base_gate_reasons:
        return _route(
            **base,
            pattern=StructuralPattern.UNSUPPORTED_V1,
            state=RouteState.ROUTE_ONLY,
            reasons=base_gate_reasons,
        )
    if strike_type not in SUPPORTED_STRIKE_TYPES:
        return _route(
            **base,
            pattern=StructuralPattern.UNSUPPORTED_V1,
            state=RouteState.ROUTE_ONLY,
            reasons=(RouteReason.UNSUPPORTED_STRIKE_TYPE,),
        )
    custom = market.raw.get("custom_strike")
    if custom is not None and not isinstance(custom, dict):
        return _route(
            **base,
            pattern=StructuralPattern.INVALID,
            state=RouteState.ABSTAIN,
            reasons=(RouteReason.MALFORMED_CUSTOM_STRIKE,),
        )
    canonical_custom = None
    if custom:
        try:
            canonical_custom = json.dumps(
                custom,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError):
            return _route(
                **base,
                pattern=StructuralPattern.INVALID,
                state=RouteState.ABSTAIN,
                reasons=(RouteReason.MALFORMED_CUSTOM_STRIKE,),
            )
    try:
        threshold = _parse_exchange_strike(market.raw.get("floor_strike"))
    except UniverseValidationError:
        return _route(
            **base,
            pattern=StructuralPattern.INVALID,
            state=RouteState.ABSTAIN,
            reasons=(RouteReason.INVALID_FLOOR_STRIKE,),
        )
    subject = stable_hash((market.event_ticker, canonical_custom or "EVENT_SUBJECT"))
    cohort = stable_hash((market.event_ticker, strike_type, canonical_custom or "EVENT_SUBJECT"))
    return _route(
        **base,
        pattern=StructuralPattern.DIRECTIONAL_THRESHOLD,
        state=RouteState.STRUCTURAL_DIRECTIONAL_THRESHOLD,
        reasons=(),
        subject_identity=subject,
        cohort_identity=cohort,
        canonical_custom_strike=canonical_custom,
        threshold=threshold,
    )


def _parse_exchange_strike(value: object) -> Decimal:
    """Parse decoded exchange strike metadata at the narrow M27B boundary.

    Exchange JSON numbers may decode as floats. For this descriptive field only,
    ``Decimal(str(value))`` preserves the decoded exchange-facing decimal value
    without introducing the binary expansion from ``Decimal(value)``. The archive
    does not retain the original JSON numeric token, so this does not reconstruct
    its byte-for-byte lexical representation.
    """
    if isinstance(value, bool) or not isinstance(value, (int, str, float)):
        raise UniverseValidationError(
            "floor_strike must be integer, fixed-point string, or finite decoded float"
        )
    if isinstance(value, float) and not math.isfinite(value):
        raise UniverseValidationError("invalid floor_strike")
    if isinstance(value, str) and _FIXED_POINT_STRIKE.fullmatch(value) is None:
        raise UniverseValidationError("invalid floor_strike")
    try:
        result = Decimal(str(value)) if isinstance(value, float) else Decimal(value)
    except InvalidOperation as exc:
        raise UniverseValidationError("invalid floor_strike") from exc
    if not result.is_finite():
        raise UniverseValidationError("invalid floor_strike")
    return result


def _route(**values: object) -> StructuralRoute:
    values.setdefault("subject_identity", None)
    values.setdefault("cohort_identity", None)
    values.setdefault("canonical_custom_strike", None)
    values.setdefault("threshold", None)
    route_id = stable_hash(
        (POLICY_VERSION, tuple(sorted((key, str(value)) for key, value in values.items())))
    )
    return StructuralRoute(route_id=route_id, **values)  # type: ignore[arg-type]


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _abstain(route: StructuralRoute, reason: RouteReason) -> StructuralRoute:
    reasons = tuple(sorted((*route.reasons, reason), key=str))
    values = {
        name: getattr(route, name) for name in route.__dataclass_fields__ if name != "route_id"
    }
    values.update(state=RouteState.ABSTAIN, pattern=StructuralPattern.INVALID, reasons=reasons)
    return _route(**values)


def _with_reason(route: StructuralRoute, reason: RouteReason) -> StructuralRoute:
    reasons = tuple(sorted((*route.reasons, reason), key=str))
    values = {
        name: getattr(route, name) for name in route.__dataclass_fields__ if name != "route_id"
    }
    values.update(reasons=reasons)
    return _route(**values)


def _valid_discovery_quote(quote: DiscoveryQuotes) -> bool:
    prices = (quote.yes_bid, quote.yes_ask, quote.no_bid, quote.no_ask)
    present_prices = tuple(price for price in prices if price is not None)
    quantities = (
        quote.yes_bid_size,
        quote.yes_ask_size,
        quote.volume,
        quote.volume_24h,
        quote.open_interest,
        quote.liquidity,
    )
    return (
        quote.research_only
        and quote.production_influence == ZERO
        and bool(quote.source_hash)
        and all(price.is_finite() and ZERO < price < Decimal(1) for price in present_prices)
        and all(value.is_finite() and value >= ZERO for value in quantities)
        and (quote.yes_bid is None or quote.yes_ask is None or quote.yes_bid <= quote.yes_ask)
        and (quote.no_bid is None or quote.no_ask is None or quote.no_bid <= quote.no_ask)
    )


def _validate_contract_semantics(
    lead: StructuralLead,
    broad_evidence: MarketEconomicsEvidence,
    narrow_evidence: MarketEconomicsEvidence,
    broad: ContractSpecification,
    narrow: ContractSpecification,
) -> None:
    if not broad.strategy_supported:
        raise OpportunityError("broad contract specification is not strategy-supported")
    if not narrow.strategy_supported:
        raise OpportunityError("narrow contract specification is not strategy-supported")
    if broad.market_ticker != lead.broad_market_ticker:
        raise OpportunityError("broad contract specification ticker mismatch")
    if narrow.market_ticker != lead.narrow_market_ticker:
        raise OpportunityError("narrow contract specification ticker mismatch")
    if broad.event_ticker != lead.event_ticker or narrow.event_ticker != lead.event_ticker:
        raise OpportunityError("contract specification event mismatch")
    if (
        broad.series_ticker != narrow.series_ticker
        or broad_evidence.series_ticker != broad.series_ticker
        or narrow_evidence.series_ticker != narrow.series_ticker
    ):
        raise OpportunityError("contract specification series mismatch")
    if (
        broad.market_rules_hash != broad_evidence.market_rules_hash
        or broad.market_metadata_hash != broad_evidence.market_metadata_hash
        or narrow.market_rules_hash != narrow_evidence.market_rules_hash
        or narrow.market_metadata_hash != narrow_evidence.market_metadata_hash
    ):
        raise OpportunityError("contract specification market provenance mismatch")
    if _semantic_context(broad) != _semantic_context(narrow):
        raise OpportunityError("contract specification semantic context mismatch")


def _semantic_context(specification: ContractSpecification) -> tuple[object, ...]:
    sources = tuple(
        (
            source.normalized_name,
            source.url,
            source.origin,
            source.classification,
        )
        for source in specification.settlement_sources
    )
    return (
        specification.event_ticker,
        specification.series_ticker,
        specification.settlement_type,
        specification.payout_model,
        specification.measured_event_or_value,
        specification.subject_entities,
        specification.geographic_scope,
        specification.comparator,
        specification.inclusivity,
        specification.threshold_unit,
        specification.measurement_window_start,
        specification.measurement_window_end,
        specification.deadline,
        specification.timezone,
        specification.occurrence_time,
        specification.expected_expiration,
        specification.actual_expiration,
        specification.settlement_authority,
        sources,
        specification.source_precedence_status,
        specification.rounding_rules,
        specification.revision_rules,
        specification.correction_rules,
        specification.recount_rules,
        specification.cancellation_rules,
        specification.postponement_rules,
        specification.early_close_rules,
        specification.exception_rules,
    )


def _scan_cohort(
    routes: list[StructuralRoute], quotes: Mapping[str, DiscoveryQuotes | None]
) -> list[StructuralLead]:
    ordered = sorted(routes, key=lambda route: (route.threshold, route.market_ticker))
    best_broad: tuple[Decimal, Decimal, str, StructuralRoute, DiscoveryQuotes] | None = None
    leads: list[StructuralLead] = []
    for route in ordered:
        quote = quotes.get(route.market_ticker)
        if quote is not None and quote.yes_bid is not None and best_broad is not None:
            broad_ask, _, _, broad, broad_quote = best_broad
            if quote.yes_bid > broad_ask:
                displayed = min(broad_quote.yes_ask_size, quote.yes_bid_size)
                quantity: Decimal | None = displayed if displayed > 0 else None
                gap = quote.yes_bid - broad_ask
                identity = (
                    POLICY_VERSION,
                    route.cohort_identity,
                    broad.market_ticker,
                    route.market_ticker,
                    str(broad.threshold),
                    str(route.threshold),
                    broad_quote.source_hash,
                    quote.source_hash,
                    broad.rules_hash,
                    broad.metadata_hash,
                    route.rules_hash,
                    route.metadata_hash,
                    route.source_authority,
                )
                leads.append(
                    StructuralLead(
                        stable_hash(identity),
                        RelationshipType.YES_HIGH_SUBSET_OF_YES_LOW,
                        route.cohort_identity or "",
                        route.event_ticker,
                        broad.market_ticker,
                        route.market_ticker,
                        broad.threshold or ZERO,
                        route.threshold or ZERO,
                        broad_quote.source_hash,
                        quote.source_hash,
                        broad.rules_hash,
                        broad.metadata_hash,
                        route.rules_hash,
                        route.metadata_hash,
                        route.source_authority,
                        gap,
                        quantity,
                        gap,
                        quantity,
                    )
                )
        if quote is not None and quote.yes_ask is not None:
            candidate = (
                quote.yes_ask,
                -quote.yes_ask_size,
                route.market_ticker,
                route,
                quote,
            )
            if best_broad is None or candidate[:3] < best_broad[:3]:
                best_broad = candidate
    return leads


def _lead_order(lead: StructuralLead) -> tuple[Decimal, Decimal, str, str]:
    quantity = lead.priority_quantity if lead.priority_quantity is not None else Decimal("-1")
    return (-lead.priority_gap, -quantity, lead.broad_market_ticker, lead.narrow_market_ticker)


def _confirmation(
    lead: StructuralLead,
    broad: MarketEconomicsEvidence,
    narrow: MarketEconomicsEvidence,
    state: ConfirmationState,
    quantity: Decimal,
    *,
    gross_cost: Decimal | None = None,
    broad_fee: Decimal | None = None,
    narrow_fee: Decimal | None = None,
    fees: Decimal | None = None,
) -> StructuralConfirmation:
    payout = quantity if gross_cost is not None else None
    gross_gap = payout - gross_cost if payout is not None and gross_cost is not None else None
    adjusted = gross_gap - fees if gross_gap is not None and fees is not None else None
    identity = (
        POLICY_VERSION,
        lead.lead_id,
        broad.evidence_id,
        narrow.evidence_id,
        state,
        str(quantity),
    )
    return StructuralConfirmation(
        stable_hash(identity),
        lead.lead_id,
        state,
        quantity,
        OutcomeSide.YES,
        OutcomeSide.NO,
        broad.evidence_id,
        narrow.evidence_id,
        payout,
        gross_cost,
        gross_gap,
        broad_fee,
        narrow_fee,
        fees,
        adjusted,
    )


def _manifest(
    routes: tuple[StructuralRoute, ...],
    leads: tuple[StructuralLead, ...],
    confirmations: tuple[StructuralConfirmation, ...],
    source_authority: str,
    rejected: int,
) -> StructuralScanManifest:
    families = tuple(sorted(Counter(route.family.value for route in routes).items()))
    strikes = tuple(
        sorted(Counter(route.strike_type or "missing/unknown" for route in routes).items())
    )
    active_cohorts = len(
        {
            route.cohort_identity
            for route in routes
            if route.state is RouteState.STRUCTURAL_DIRECTIONAL_THRESHOLD
        }
    )
    confirmed = sum(
        item.state is ConfirmationState.FINAL_FEE_UNKNOWN_PREFILL for item in confirmations
    )
    insufficient = len(confirmations) - confirmed
    content = stable_hash(
        (
            tuple(route.route_id for route in routes),
            tuple(lead.lead_id for lead in leads),
            tuple(item.confirmation_id for item in confirmations),
        )
    )
    routed = sum(route.state is not RouteState.ABSTAIN for route in routes)
    abstained = sum(route.state is RouteState.ABSTAIN for route in routes)
    eligible = sum(route.state is RouteState.STRUCTURAL_DIRECTIONAL_THRESHOLD for route in routes)
    manifest_id = stable_hash(
        (
            POLICY_VERSION,
            source_authority,
            content,
            len(routes),
            routed,
            abstained,
            families,
            strikes,
            eligible,
            active_cohorts,
            rejected,
            len(leads),
            confirmed,
            insufficient,
        )
    )
    return StructuralScanManifest(
        manifest_id,
        POLICY_VERSION,
        source_authority,
        content,
        len(routes),
        routed,
        abstained,
        families,
        strikes,
        eligible,
        active_cohorts,
        rejected,
        len(leads),
        confirmed,
        insufficient,
    )
