"""KU-A1 deterministic whole-exchange discovery, semantic routing, and census evidence.

Consumes captured public metadata only. It never performs network I/O and can mint only
DISCOVERED or SEMANTICALLY_UNDERSTOOD research records with zero production influence.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from typing import Any

from services.contract_intelligence.specification import (
    Comparator,
    ContractSpecification,
    ContractSpecificationParser,
    SemanticsInputBundle,
    SemanticStatus,
    parse_comparison,
)
from services.opportunity_engine.live_economics import DiscoveryQuotes
from services.opportunity_engine.structural import StructuralRoute, scan_structural_markets

from .domain import Event, Market, Series, UniverseValidationError, exact, stable_hash
from .lifecycle import (
    ZERO_INFLUENCE,
    LifecycleState,
    MarketLifecycleRecord,
    ProductType,
    UniverseCaptureEvidence,
)
from .quality import Family, classify

ROUTER_POLICY_VERSION = "ku-a1-market-universe-router-v2"
CENSUS_SCHEMA_VERSION = "ku-a1-universe-census-v1"
QUARANTINE_SCHEMA_VERSION = "ku-a1-market-quarantine-v1"
COVERAGE_SCHEMA_VERSION = "ku-a1-family-coverage-v1"
COVERAGE_DESCRIPTOR_VERSION = "ku-a1-market-coverage-descriptor-v1"
_QUOTE_FIELDS = (
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


class UniverseCensusError(ValueError):
    """Captured universe evidence cannot be deterministically accounted for."""


@dataclass(frozen=True, slots=True)
class CensusQuarantineRecord:
    capture_id: str
    market_input_hash: str
    observed_market_ticker: str | None
    occurrence_ordinal: int
    reason: str
    detail: str
    schema_version: str = field(init=False, default=QUARANTINE_SCHEMA_VERSION)
    quarantine_id: str = field(init=False)
    content_hash: str = field(init=False)
    research_only: bool = field(init=False, default=True)
    production_influence: Decimal = field(init=False, default=ZERO_INFLUENCE)

    def __post_init__(self) -> None:
        if not self.capture_id or not self.market_input_hash or not self.reason or not self.detail:
            raise UniverseCensusError("quarantine evidence is incomplete")
        if self.occurrence_ordinal < 0:
            raise UniverseCensusError("quarantine occurrence ordinal cannot be negative")
        digest = stable_hash(
            (
                QUARANTINE_SCHEMA_VERSION,
                self.capture_id,
                self.market_input_hash,
                self.observed_market_ticker,
                self.occurrence_ordinal,
                self.reason,
                self.detail,
                "RESEARCH_ONLY",
                "0",
            )
        )
        object.__setattr__(self, "quarantine_id", digest)
        object.__setattr__(self, "content_hash", digest)


@dataclass(frozen=True, slots=True, init=False)
class UniverseCensusManifest:
    capture_id: str
    input_market_count: int
    accounted_market_count: int
    lifecycle_record_ids: tuple[str, ...]
    quarantine_ids: tuple[str, ...]
    state_counts: tuple[tuple[str, int], ...]
    schema_version: str
    manifest_id: str
    content_hash: str
    research_only: bool
    production_influence: Decimal

    def __init__(
        self,
        *,
        capture: UniverseCaptureEvidence,
        input_market_count: int,
        records: tuple[MarketLifecycleRecord, ...],
        quarantines: tuple[CensusQuarantineRecord, ...],
    ) -> None:
        if input_market_count < 0:
            raise UniverseCensusError("input market count cannot be negative")
        if any(record.capture_id != capture.capture_id for record in records):
            raise UniverseCensusError("lifecycle capture identity mismatch")
        if any(record.capture_id != capture.capture_id for record in quarantines):
            raise UniverseCensusError("quarantine capture identity mismatch")
        lifecycle_ids = tuple(sorted(record.lifecycle_record_id for record in records))
        quarantine_ids = tuple(sorted(record.quarantine_id for record in quarantines))
        if len(set(lifecycle_ids)) != len(lifecycle_ids):
            raise UniverseCensusError("duplicate lifecycle record identity")
        if len(set(quarantine_ids)) != len(quarantine_ids):
            raise UniverseCensusError("duplicate quarantine identity")
        accounted = len(lifecycle_ids) + len(quarantine_ids)
        if accounted != input_market_count:
            raise UniverseCensusError("census does not account for every supplied market")
        state_counts = _counts(record.state.value for record in records)
        digest = stable_hash(
            (
                CENSUS_SCHEMA_VERSION,
                capture.capture_id,
                input_market_count,
                accounted,
                lifecycle_ids,
                quarantine_ids,
                state_counts,
                "RESEARCH_ONLY",
                "0",
            )
        )
        object.__setattr__(self, "capture_id", capture.capture_id)
        object.__setattr__(self, "input_market_count", input_market_count)
        object.__setattr__(self, "accounted_market_count", accounted)
        object.__setattr__(self, "lifecycle_record_ids", lifecycle_ids)
        object.__setattr__(self, "quarantine_ids", quarantine_ids)
        object.__setattr__(self, "state_counts", state_counts)
        object.__setattr__(self, "schema_version", CENSUS_SCHEMA_VERSION)
        object.__setattr__(self, "manifest_id", digest)
        object.__setattr__(self, "content_hash", digest)
        object.__setattr__(self, "research_only", True)
        object.__setattr__(self, "production_influence", ZERO_INFLUENCE)


@dataclass(frozen=True, slots=True)
class MarketCoverageDescriptor:
    market_ticker: str
    exchange_category: str | None
    series_ticker: str | None
    recurrence: str | None
    product_type: ProductType
    payout_model: str
    strike_type: str | None
    semantic_status: str | None
    settlement_source_identity: str | None
    specialist_route_state: str | None
    major_reasons: tuple[str, ...]
    volume: Decimal
    volume_24h: Decimal | None
    open_interest: Decimal
    liquidity: Decimal | None
    yes_bid: Decimal | None
    yes_ask: Decimal | None
    yes_bid_size: Decimal | None
    yes_ask_size: Decimal | None
    descriptor_issues: tuple[str, ...]
    schema_version: str = field(init=False, default=COVERAGE_DESCRIPTOR_VERSION)
    descriptor_id: str = field(init=False)
    content_hash: str = field(init=False)
    research_only: bool = field(init=False, default=True)
    production_influence: Decimal = field(init=False, default=ZERO_INFLUENCE)

    def __post_init__(self) -> None:
        reasons = tuple(sorted(set(self.major_reasons)))
        issues = tuple(sorted(set(self.descriptor_issues)))
        material = (
            COVERAGE_DESCRIPTOR_VERSION,
            self.market_ticker,
            self.exchange_category,
            self.series_ticker,
            self.recurrence,
            self.product_type.value,
            self.payout_model,
            self.strike_type,
            self.semantic_status,
            self.settlement_source_identity,
            self.specialist_route_state,
            reasons,
            tuple(
                str(value) if value is not None else None
                for value in (
                    self.volume,
                    self.volume_24h,
                    self.open_interest,
                    self.liquidity,
                    self.yes_bid,
                    self.yes_ask,
                    self.yes_bid_size,
                    self.yes_ask_size,
                )
            ),
            issues,
            "DESCRIPTIVE_ONLY_NOT_DEPTH_CAPACITY_SLIPPAGE_OR_EDGE",
            "0",
        )
        digest = stable_hash(material)
        object.__setattr__(self, "major_reasons", reasons)
        object.__setattr__(self, "descriptor_issues", issues)
        object.__setattr__(self, "descriptor_id", digest)
        object.__setattr__(self, "content_hash", digest)


@dataclass(frozen=True, slots=True, init=False)
class FamilyCoverageManifest:
    census_manifest_id: str
    descriptor_ids: tuple[str, ...]
    category_counts: tuple[tuple[str, int], ...]
    series_counts: tuple[tuple[str, int], ...]
    recurrence_counts: tuple[tuple[str, int], ...]
    product_counts: tuple[tuple[str, int], ...]
    payout_counts: tuple[tuple[str, int], ...]
    strike_type_counts: tuple[tuple[str, int], ...]
    semantic_status_counts: tuple[tuple[str, int], ...]
    settlement_source_counts: tuple[tuple[str, int], ...]
    specialist_route_state_counts: tuple[tuple[str, int], ...]
    reason_counts: tuple[tuple[str, int], ...]
    schema_version: str
    manifest_id: str
    content_hash: str
    research_only: bool
    production_influence: Decimal

    def __init__(
        self,
        census_manifest: UniverseCensusManifest,
        descriptors: tuple[MarketCoverageDescriptor, ...],
    ) -> None:
        descriptor_ids = tuple(sorted(item.descriptor_id for item in descriptors))
        if len(set(descriptor_ids)) != len(descriptor_ids):
            raise UniverseCensusError("duplicate coverage descriptor identity")
        category_counts = _counts(item.exchange_category or "UNKNOWN" for item in descriptors)
        series_counts = _counts(item.series_ticker or "UNKNOWN" for item in descriptors)
        recurrence_counts = _counts(item.recurrence or "UNKNOWN" for item in descriptors)
        product_counts = _counts(item.product_type.value for item in descriptors)
        payout_counts = _counts(item.payout_model for item in descriptors)
        strike_counts = _counts(item.strike_type or "UNKNOWN" for item in descriptors)
        semantic_counts = _counts(item.semantic_status or "UNAVAILABLE" for item in descriptors)
        source_counts = _counts(
            item.settlement_source_identity or "UNAVAILABLE" for item in descriptors
        )
        route_counts = _counts(item.specialist_route_state or "UNAVAILABLE" for item in descriptors)
        reason_counts = _counts(
            reason
            for item in descriptors
            for reason in (*item.major_reasons, *item.descriptor_issues)
        )
        material = (
            COVERAGE_SCHEMA_VERSION,
            census_manifest.manifest_id,
            descriptor_ids,
            category_counts,
            series_counts,
            recurrence_counts,
            product_counts,
            payout_counts,
            strike_counts,
            semantic_counts,
            source_counts,
            route_counts,
            reason_counts,
            "NO_RESEARCH_READINESS_SCORE",
            "0",
        )
        digest = stable_hash(material)
        for name, value in (
            ("census_manifest_id", census_manifest.manifest_id),
            ("descriptor_ids", descriptor_ids),
            ("category_counts", category_counts),
            ("series_counts", series_counts),
            ("recurrence_counts", recurrence_counts),
            ("product_counts", product_counts),
            ("payout_counts", payout_counts),
            ("strike_type_counts", strike_counts),
            ("semantic_status_counts", semantic_counts),
            ("settlement_source_counts", source_counts),
            ("specialist_route_state_counts", route_counts),
            ("reason_counts", reason_counts),
        ):
            object.__setattr__(self, name, value)
        object.__setattr__(self, "schema_version", COVERAGE_SCHEMA_VERSION)
        object.__setattr__(self, "manifest_id", digest)
        object.__setattr__(self, "content_hash", digest)
        object.__setattr__(self, "research_only", True)
        object.__setattr__(self, "production_influence", ZERO_INFLUENCE)


@dataclass(frozen=True, slots=True)
class UniverseCensusResult:
    capture: UniverseCaptureEvidence
    records: tuple[MarketLifecycleRecord, ...]
    quarantines: tuple[CensusQuarantineRecord, ...]
    manifest: UniverseCensusManifest
    coverage_descriptors: tuple[MarketCoverageDescriptor, ...]
    coverage_manifest: FamilyCoverageManifest


@dataclass(frozen=True, slots=True)
class _ParsedMarket:
    market: Market
    input_hash: str
    quote: DiscoveryQuotes | None
    quote_issue: str | None


class MarketUniverseRouter:
    """Compose canonical parsers and M27B into KU-A1 lifecycle evidence."""

    def __init__(self) -> None:
        self._semantic_parser = ContractSpecificationParser()

    def census(
        self,
        *,
        market_rows: Iterable[Mapping[str, Any]],
        event_rows: Iterable[Mapping[str, Any]],
        series_rows: Iterable[Mapping[str, Any]],
        source_authority: str,
        request_locator: str,
        response_sha256: str,
        captured_at: datetime,
        previous_records: Mapping[str, MarketLifecycleRecord] | None = None,
    ) -> UniverseCensusResult:
        capture = UniverseCaptureEvidence(
            source_authority=source_authority,
            request_locator=request_locator,
            response_sha256=response_sha256,
            captured_at=captured_at,
        )
        raw_markets = tuple(dict(row) for row in market_rows)
        _reject_duplicate_market_identity(raw_markets)
        event_map, invalid_events = _parse_events(tuple(dict(row) for row in event_rows))
        series_map, invalid_series = _parse_series(tuple(dict(row) for row in series_rows))
        prepared = sorted(
            ((_row_hash(row), _text(row.get("ticker")), row) for row in raw_markets),
            key=lambda item: (item[0], item[1] or ""),
        )
        occurrences: Counter[str] = Counter()
        parsed: list[_ParsedMarket] = []
        quarantines: list[CensusQuarantineRecord] = []
        for input_hash, observed_ticker, raw in prepared:
            ordinal = occurrences[input_hash]
            occurrences[input_hash] += 1
            try:
                market = Market.parse(raw)
            except UniverseValidationError as exc:
                quarantines.append(
                    CensusQuarantineRecord(
                        capture.capture_id,
                        input_hash,
                        observed_ticker,
                        ordinal,
                        "MARKET_PARSE_FAILURE",
                        str(exc),
                    )
                )
                continue
            quote, quote_issue = _discovery_quote(raw)
            parsed.append(_ParsedMarket(market, input_hash, quote, quote_issue))
        routes = _m27b_routes(parsed, event_map, capture.source_authority)
        prior = previous_records or {}
        records: list[MarketLifecycleRecord] = []
        descriptors: list[MarketCoverageDescriptor] = []
        for item in parsed:
            route = routes[item.market.ticker]
            record = self._route_market(
                item,
                capture,
                event_map,
                invalid_events,
                series_map,
                invalid_series,
                route,
                prior.get(item.market.ticker),
            )
            records.append(record)
            descriptors.append(_coverage_descriptor(item, record, event_map, series_map, route))
        record_tuple = tuple(sorted(records, key=lambda item: item.market_ticker))
        quarantine_tuple = tuple(sorted(quarantines, key=lambda item: item.quarantine_id))
        descriptor_tuple = tuple(sorted(descriptors, key=lambda item: item.market_ticker))
        manifest = UniverseCensusManifest(
            capture=capture,
            input_market_count=len(raw_markets),
            records=record_tuple,
            quarantines=quarantine_tuple,
        )
        coverage = FamilyCoverageManifest(manifest, descriptor_tuple)
        return UniverseCensusResult(
            capture,
            record_tuple,
            quarantine_tuple,
            manifest,
            descriptor_tuple,
            coverage,
        )

    def _route_market(
        self,
        item: _ParsedMarket,
        capture: UniverseCaptureEvidence,
        event_map: Mapping[str, Event],
        invalid_events: Mapping[str, str],
        series_map: Mapping[str, Series],
        invalid_series: Mapping[str, str],
        route: StructuralRoute,
        previous: MarketLifecycleRecord | None,
    ) -> MarketLifecycleRecord:
        market = item.market
        blockers: list[str] = []
        unsupported: list[str] = []
        event = event_map.get(market.event_ticker)
        if event is None:
            blockers.append(
                "INVALID_EVENT_PARENT" if market.event_ticker in invalid_events else "MISSING_EVENT"
            )
        series_ticker = (
            event.series_ticker if event is not None else _text(market.raw.get("series_ticker"))
        )
        series = series_map.get(series_ticker) if series_ticker is not None else None
        if series_ticker is None:
            blockers.append("MISSING_SERIES_IDENTITY")
        elif series is None:
            blockers.append(
                "INVALID_SERIES_PARENT" if series_ticker in invalid_series else "MISSING_SERIES"
            )
        product = _product_type(market)
        if (product_blocker := _product_blocker(product)) is not None:
            unsupported.append(product_blocker)
        spec: ContractSpecification | None = None
        if event is not None and series is not None:
            spec = self._semantic_parser.parse(
                SemanticsInputBundle.build(market.raw, event.raw, series.raw),
                now=capture.captured_at,
            )
            blockers.extend(_semantic_blockers(market, spec))
            unsupported.extend(spec.unsupported_features)
        settlement_identity = _settlement_source_identity(spec)
        blockers = sorted(set(blockers))
        unsupported = sorted(set(unsupported))
        state = (
            LifecycleState.SEMANTICALLY_UNDERSTOOD
            if product is ProductType.BINARY_EVENT
            and spec is not None
            and spec.semantic_status is SemanticStatus.VALID
            and not blockers
            and not unsupported
            else LifecycleState.DISCOVERED
        )
        if state is LifecycleState.DISCOVERED and not blockers and not unsupported:
            blockers.append("SEMANTIC_PROOF_INCOMPLETE")
        parent_hash = (
            stable_hash((event.metadata_hash, series.metadata_hash))
            if event is not None and series is not None
            else None
        )
        semantic_material_hash = stable_hash(
            (
                ROUTER_POLICY_VERSION,
                market.rules_hash,
                market.metadata_hash,
                event.metadata_hash if event is not None else None,
                series.metadata_hash if series is not None else None,
                product.value,
                settlement_identity,
                spec.semantic_hash if spec is not None else None,
            )
        )
        route_reasons = tuple(reason.value for reason in route.reasons)
        if item.quote_issue is not None:
            route_reasons = (*route_reasons, item.quote_issue)
        current = MarketLifecycleRecord(
            capture_id=capture.capture_id,
            market_input_hash=item.input_hash,
            market_id=_text(market.raw.get("market_id")),
            market_ticker=market.ticker,
            event_id=_text(event.raw.get("event_id")) if event is not None else None,
            event_ticker=market.event_ticker,
            series_id=_text(series.raw.get("series_id")) if series is not None else None,
            series_ticker=series_ticker,
            product_type=product,
            payout_model=spec.payout_model.value if spec is not None else _fallback_payout(product),
            state=state,
            rules_hash=market.rules_hash,
            metadata_hash=market.metadata_hash,
            parent_evidence_hash=parent_hash,
            settlement_source_identity=settlement_identity,
            specialist_route_id=route.route_id,
            specialist_route_state=route.state.value,
            specialist_route_reasons=route_reasons,
            advisory_family=_advisory_family(market, event, series).value,
            semantic_status=spec.semantic_status.value if spec is not None else None,
            semantic_proof_ids=_semantic_proof_ids(spec),
            semantic_blockers=tuple(blockers),
            unsupported_reasons=tuple(unsupported),
            semantic_material_hash=semantic_material_hash,
        )
        return _with_supersession(current, previous)


def _with_supersession(
    current: MarketLifecycleRecord, previous: MarketLifecycleRecord | None
) -> MarketLifecycleRecord:
    if previous is None:
        return current
    if previous.market_ticker != current.market_ticker:
        raise UniverseCensusError("previous lifecycle record market identity mismatch")
    if previous.semantic_material_hash == current.semantic_material_hash:
        if previous.state is not current.state:
            raise UniverseCensusError("semantic state changed without material semantic evidence")
        return current
    return replace(current, supersedes_record_id=previous.lifecycle_record_id)


def _m27b_routes(
    parsed: list[_ParsedMarket], events: Mapping[str, Event], source_authority: str
) -> dict[str, StructuralRoute]:
    scan = scan_structural_markets(
        (item.market for item in parsed),
        events=events,
        discovery_quotes={item.market.ticker: item.quote for item in parsed},
        source_authority=source_authority,
    )
    routes = {route.market_ticker: route for route in scan.routes}
    expected = {item.market.ticker for item in parsed}
    if set(routes) != expected:
        raise UniverseCensusError("M27B failed to return exactly one route per canonical market")
    return routes


def _coverage_descriptor(
    item: _ParsedMarket,
    record: MarketLifecycleRecord,
    events: Mapping[str, Event],
    series_map: Mapping[str, Series],
    route: StructuralRoute,
) -> MarketCoverageDescriptor:
    market = item.market
    event = events.get(market.event_ticker)
    series = series_map.get(event.series_ticker) if event is not None else None
    values: dict[str, Decimal | None] = {}
    issues: list[str] = []
    for field_name in (
        "volume_24h_fp",
        "liquidity_dollars",
        "yes_bid_dollars",
        "yes_ask_dollars",
        "yes_bid_size_fp",
        "yes_ask_size_fp",
    ):
        values[field_name], issue = _optional_exact(market.raw, field_name)
        if issue is not None:
            issues.append(issue)
    reasons = (
        *record.semantic_blockers,
        *record.unsupported_reasons,
        *record.specialist_route_reasons,
    )
    return MarketCoverageDescriptor(
        market_ticker=market.ticker,
        exchange_category=event.category if event is not None else None,
        series_ticker=record.series_ticker,
        recurrence=series.frequency if series is not None else None,
        product_type=record.product_type,
        payout_model=record.payout_model,
        strike_type=_text(market.raw.get("strike_type")),
        semantic_status=record.semantic_status,
        settlement_source_identity=record.settlement_source_identity,
        specialist_route_state=route.state.value,
        major_reasons=reasons,
        volume=market.volume,
        volume_24h=values["volume_24h_fp"],
        open_interest=market.open_interest,
        liquidity=values["liquidity_dollars"],
        yes_bid=values["yes_bid_dollars"],
        yes_ask=values["yes_ask_dollars"],
        yes_bid_size=values["yes_bid_size_fp"],
        yes_ask_size=values["yes_ask_size_fp"],
        descriptor_issues=tuple(issues),
    )


def _semantic_blockers(market: Market, spec: ContractSpecification) -> tuple[str, ...]:
    blockers = [issue.issue_type.value for issue in spec.issues if issue.blocking]
    if spec.market_ticker != market.ticker or spec.event_ticker != market.event_ticker:
        blockers.append("SEMANTIC_IDENTITY_MISMATCH")
    if spec.market_rules_hash != market.rules_hash:
        blockers.append("SEMANTIC_RULES_HASH_MISMATCH")
    if spec.market_metadata_hash != market.metadata_hash:
        blockers.append("SEMANTIC_METADATA_HASH_MISMATCH")
    rules = " ".join(
        text
        for text in (
            str(market.raw.get("rules_primary", "")).strip(),
            str(market.raw.get("rules_secondary", "")).strip(),
        )
        if text
    )
    comparator = parse_comparison(rules)[0]
    if comparator is Comparator.NONE:
        blockers.append("RULES_COMPARATOR_UNPROVEN")
    elif comparator is not spec.comparator:
        blockers.append("RULES_COMPARATOR_MISMATCH")
    if classify("", rules) is Family.WEATHER and _text(market.raw.get("station_code")) is None:
        blockers.append("WEATHER_STATION_MISSING")
    if spec.semantic_status is not SemanticStatus.VALID:
        blockers.append(f"SEMANTIC_STATUS_{spec.semantic_status.value}")
    return tuple(sorted(set(blockers)))


def _semantic_proof_ids(spec: ContractSpecification | None) -> tuple[str, ...]:
    if spec is None:
        return ()
    proof = stable_hash(
        (
            spec.source_input_hash,
            spec.semantic_hash,
            spec.deterministic_parser_version,
            spec.semantic_status.value,
            tuple(sorted(issue.issue_type.value for issue in spec.issues if issue.blocking)),
        )
    )
    return tuple(sorted((proof, spec.semantic_hash, spec.source_input_hash)))


def _settlement_source_identity(spec: ContractSpecification | None) -> str | None:
    if spec is None or not spec.settlement_sources:
        return None
    return stable_hash(
        tuple(
            sorted(
                (source.source_hash, source.normalized_name, source.url or "", source.origin.value)
                for source in spec.settlement_sources
            )
        )
    )


def _advisory_family(market: Market, event: Event | None, series: Series | None) -> Family:
    category = (
        event.category
        if event is not None and event.category
        else series.category
        if series is not None
        else ""
    )
    title = " ".join(
        text
        for text in (
            market.title or "",
            event.title if event is not None else "",
            series.title if series is not None else "",
        )
        if text
    )
    return classify(category or "", title)


def _product_type(market: Market) -> ProductType:
    explicit = " ".join(
        value.lower()
        for field_name in ("product_type", "instrument_type", "contract_type", "product")
        if (value := _text(market.raw.get(field_name))) is not None
    )
    if any(token in explicit for token in ("perpetual", "future", "futures")):
        return ProductType.NON_EVENT
    if market.multivariate:
        return ProductType.MULTIVARIATE_EVENT
    settlement = market.raw.get("settlement_value_dollars")
    binary_values = {None, "0", "0.0", "0.00", "1", "1.0", "1.00"}
    if settlement not in binary_values:
        return ProductType.SCALAR_OR_PARTIAL
    if market.market_type.lower() == "binary":
        return ProductType.BINARY_EVENT
    if market.market_type.lower() in {"scalar", "partial"}:
        return ProductType.SCALAR_OR_PARTIAL
    return ProductType.UNKNOWN


def _product_blocker(product: ProductType) -> str | None:
    return {
        ProductType.BINARY_EVENT: None,
        ProductType.MULTIVARIATE_EVENT: "MULTIVARIATE_PRODUCT",
        ProductType.SCALAR_OR_PARTIAL: "SCALAR_OR_PARTIAL_PRODUCT",
        ProductType.NON_EVENT: "NON_EVENT_PRODUCT_OUT_OF_DOMAIN",
        ProductType.UNKNOWN: "UNKNOWN_PRODUCT",
    }[product]


def _fallback_payout(product: ProductType) -> str:
    return {
        ProductType.BINARY_EVENT: "SIMPLE_BINARY",
        ProductType.MULTIVARIATE_EVENT: "MULTIVARIATE",
        ProductType.SCALAR_OR_PARTIAL: "SCALAR_OR_PARTIAL",
        ProductType.NON_EVENT: "NON_EVENT",
        ProductType.UNKNOWN: "UNKNOWN",
    }[product]


def _discovery_quote(raw: dict[str, Any]) -> tuple[DiscoveryQuotes | None, str | None]:
    if not all(field_name in raw for field_name in _QUOTE_FIELDS):
        return None, None
    try:
        return DiscoveryQuotes.parse(raw), None
    except UniverseValidationError:
        return None, "INVALID_DISCOVERY_QUOTE_INPUT"


def _optional_exact(raw: Mapping[str, Any], field_name: str) -> tuple[Decimal | None, str | None]:
    if field_name not in raw or raw[field_name] is None:
        return None, None
    try:
        return exact(raw[field_name], field_name), None
    except UniverseValidationError:
        return None, f"INVALID_DESCRIPTOR_{field_name.upper()}"


def _parse_events(
    rows: tuple[dict[str, Any], ...],
) -> tuple[dict[str, Event], dict[str, str]]:
    valid: dict[str, Event] = {}
    invalid: dict[str, str] = {}
    for ticker, row in _index(rows, "event_ticker").items():
        try:
            valid[ticker] = Event.parse(row)
        except UniverseValidationError as exc:
            invalid[ticker] = str(exc)
    return valid, invalid


def _parse_series(
    rows: tuple[dict[str, Any], ...],
) -> tuple[dict[str, Series], dict[str, str]]:
    valid: dict[str, Series] = {}
    invalid: dict[str, str] = {}
    for ticker, row in _index(rows, "ticker").items():
        try:
            valid[ticker] = Series.parse(row)
        except UniverseValidationError as exc:
            invalid[ticker] = str(exc)
    return valid, invalid


def _index(rows: tuple[dict[str, Any], ...], key: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        identity = _text(row.get(key))
        if identity is None:
            continue
        if identity in result:
            raise UniverseCensusError(f"duplicate parent identity for {key}: {identity}")
        result[identity] = row
    return result


def _reject_duplicate_market_identity(rows: tuple[dict[str, Any], ...]) -> None:
    identities = [identity for row in rows if (identity := _text(row.get("ticker")))]
    duplicates = sorted(identity for identity, count in Counter(identities).items() if count > 1)
    if duplicates:
        raise UniverseCensusError(f"duplicate market identity: {','.join(duplicates)}")


def _row_hash(row: Mapping[str, Any]) -> str:
    try:
        return stable_hash(dict(row))
    except (TypeError, ValueError) as exc:
        raise UniverseCensusError("market row is not deterministic JSON evidence") from exc


def _counts(values: Iterable[str]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(Counter(values).items()))


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
