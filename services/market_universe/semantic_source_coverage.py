"""KU-A2.1 research-only semantic and settlement-source coverage projection.

This module consumes the canonical KU-A1 router's private routing context. It performs no
network I/O, does not parse semantics a second time, and confers no readiness, economic,
source-permission, or production authority.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

from services.contract_intelligence.specification import (
    ContractSpecification,
    SettlementSourceRecord,
)

from .domain import stable_hash
from .lifecycle import (
    ZERO_INFLUENCE,
    LifecycleError,
    MarketLifecycleRecord,
    UniverseCaptureEvidence,
)
from .router import (
    _ROUTER_ISSUANCE_CAPABILITY,
    CensusQuarantineRecord,
    FamilyCoverageManifest,
    MarketCoverageDescriptor,
    MarketUniverseRouter,
    UniverseCensusError,
    UniverseCensusManifest,
    UniverseCensusResult,
    _CensusRoutingContext,
    _RoutedMarket,
    _routing_context_object_identity,
    _semantic_proof_ids,
)

A2_RECORD_SCHEMA_VERSION = "ku-a2-1-semantic-source-coverage-record-v1"
A2_QUARANTINE_SCHEMA_VERSION = "ku-a2-1-semantic-source-quarantine-v1"
A2_MANIFEST_SCHEMA_VERSION = "ku-a2-1-semantic-source-coverage-manifest-v1"
A2_RESULT_SCHEMA_VERSION = "ku-a2-1-semantic-source-coverage-result-v1"
_A2_ISSUANCE_CAPABILITY = object()


class SemanticSourceCoverageError(ValueError):
    """KU-A2.1 projection could not prove a conservative canonical binding."""


class ReasonOrigin(StrEnum):
    PARENT = "PARENT"
    SEMANTIC_BLOCKER = "SEMANTIC_BLOCKER"
    UNSUPPORTED_FEATURE = "UNSUPPORTED_FEATURE"
    PRODUCT = "PRODUCT"
    M27B_ADVISORY = "M27B_ADVISORY"
    DESCRIPTOR_ISSUE = "DESCRIPTOR_ISSUE"
    QUARANTINE = "QUARANTINE"


@dataclass(frozen=True, slots=True)
class CoverageReason:
    origin: ReasonOrigin
    code: str


@dataclass(frozen=True, slots=True)
class SettlementSourceProjection:
    source_id: str
    normalized_name: str
    exchange_name: str
    url: str | None
    hostname: str | None
    origin: str
    classification: str
    source_hash: str
    current: bool
    first_seen: datetime | None
    last_seen: datetime | None


@dataclass(frozen=True, slots=True, init=False)
class MarketSemanticSourceCoverageRecord:
    census_manifest_id: str
    capture_id: str
    market_input_hash: str
    lifecycle_record_id: str
    descriptor_id: str
    market_ticker: str
    lifecycle_state: str
    product_type: str
    payout_model: str
    exchange_category: str | None
    series_ticker: str | None
    recurrence: str | None
    strike_type: str | None
    specialist_route_state: str | None
    deterministic_parser_version: str | None
    source_input_hash: str | None
    semantic_hash: str | None
    semantic_status: str | None
    semantic_proof_ids: tuple[str, ...]
    blocking_issue_codes: tuple[str, ...]
    unsupported_features: tuple[str, ...]
    comparator: str | None
    threshold_value: Decimal | None
    lower_bound: Decimal | None
    upper_bound: Decimal | None
    inclusivity: str | None
    provenance_ids: tuple[str, ...]
    settlement_sources: tuple[SettlementSourceProjection, ...]
    settlement_source_identity: str | None
    reasons: tuple[CoverageReason, ...]
    schema_version: str
    record_id: str
    content_hash: str
    research_only: bool
    production_influence: Decimal

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("MarketSemanticSourceCoverageRecord is canonical-A2-issued only")

    @classmethod
    def _issue(
        cls,
        *,
        capability: object,
        context: _CensusRoutingContext,
        routed: _RoutedMarket,
    ) -> MarketSemanticSourceCoverageRecord:
        if capability is not _A2_ISSUANCE_CAPABILITY:
            raise SemanticSourceCoverageError("A2 record issuance capability is invalid")
        _validate_context_identity(context)
        if not any(item is routed for item in context.routed_markets):
            raise SemanticSourceCoverageError("A2 routed market is not canonical context evidence")
        record = routed.outcome.record
        descriptor = routed.descriptor
        spec = routed.outcome.specification
        if not any(item is record for item in context.result.records):
            raise SemanticSourceCoverageError("A2 lifecycle evidence is not exact KU-A1 evidence")
        if not any(item is descriptor for item in context.result.coverage_descriptors):
            raise SemanticSourceCoverageError("A2 descriptor evidence is not exact KU-A1 evidence")
        if routed.parsed.input_hash != record.market_input_hash:
            raise SemanticSourceCoverageError("A2 market-input identity does not bind KU-A1")
        if descriptor.lifecycle_record_id != record.lifecycle_record_id:
            raise SemanticSourceCoverageError("A2 descriptor does not bind KU-A1 lifecycle")
        expected_semantic_proofs = _semantic_proof_ids(spec)
        if expected_semantic_proofs != record.semantic_proof_ids:
            raise SemanticSourceCoverageError(
                "A2 semantic proof does not bind exact KU-A1 specification"
            )
        expected_semantic_status = spec.semantic_status.value if spec is not None else None
        if expected_semantic_status != record.semantic_status:
            raise SemanticSourceCoverageError("A2 semantic status does not bind KU-A1")
        expected_identity = _recompute_settlement_source_identity(spec)
        if expected_identity != record.settlement_source_identity:
            raise SemanticSourceCoverageError(
                "settlement-source aggregate identity does not bind KU-A1"
            )
        sources: tuple[SettlementSourceProjection, ...] = ()
        if spec is not None:
            sources = tuple(
                sorted(
                    (_source_projection(source) for source in spec.settlement_sources),
                    key=lambda source: (source.source_hash, source.origin, source.source_id),
                )
            )
        reasons = tuple(
            CoverageReason(ReasonOrigin(reason.origin.value), reason.code)
            for reason in routed.reasons
        )
        semantic_values = _semantic_values(spec)
        canonical_reasons = _canonical_reasons(reasons)
        material = _record_identity_material(
            census_manifest_id=context.result.manifest.manifest_id,
            capture_id=record.capture_id,
            market_input_hash=record.market_input_hash,
            lifecycle_record_id=record.lifecycle_record_id,
            descriptor_id=descriptor.descriptor_id,
            market_ticker=record.market_ticker,
            lifecycle_state=record.state.value,
            product_type=record.product_type.value,
            payout_model=record.payout_model,
            exchange_category=descriptor.exchange_category,
            series_ticker=record.series_ticker,
            recurrence=descriptor.recurrence,
            strike_type=descriptor.strike_type,
            specialist_route_state=record.specialist_route_state,
            semantic_values=semantic_values,
            semantic_proof_ids=expected_semantic_proofs,
            settlement_sources=sources,
            settlement_source_identity=expected_identity,
            reasons=canonical_reasons,
        )
        digest = stable_hash(material)
        self = object.__new__(cls)
        values: tuple[tuple[str, object], ...] = (
            ("census_manifest_id", context.result.manifest.manifest_id),
            ("capture_id", record.capture_id),
            ("market_input_hash", record.market_input_hash),
            ("lifecycle_record_id", record.lifecycle_record_id),
            ("descriptor_id", descriptor.descriptor_id),
            ("market_ticker", record.market_ticker),
            ("lifecycle_state", record.state.value),
            ("product_type", record.product_type.value),
            ("payout_model", record.payout_model),
            ("exchange_category", descriptor.exchange_category),
            ("series_ticker", record.series_ticker),
            ("recurrence", descriptor.recurrence),
            ("strike_type", descriptor.strike_type),
            ("specialist_route_state", record.specialist_route_state),
            ("semantic_proof_ids", expected_semantic_proofs),
            ("settlement_sources", sources),
            ("settlement_source_identity", expected_identity),
            ("reasons", canonical_reasons),
        )
        for name, value in values:
            object.__setattr__(self, name, value)
        for name, value in _semantic_field_items(spec):
            object.__setattr__(self, name, value)
        object.__setattr__(self, "schema_version", A2_RECORD_SCHEMA_VERSION)
        object.__setattr__(self, "record_id", digest)
        object.__setattr__(self, "content_hash", digest)
        object.__setattr__(self, "research_only", True)
        object.__setattr__(self, "production_influence", ZERO_INFLUENCE)
        return self

    def _validate_canonical_identity(self) -> None:
        if type(self) is not MarketSemanticSourceCoverageRecord:
            raise SemanticSourceCoverageError("A2 record concrete type is not canonical")
        if self.schema_version != A2_RECORD_SCHEMA_VERSION:
            raise SemanticSourceCoverageError("A2 record schema identity is invalid")
        if self.research_only is not True or self.production_influence != ZERO_INFLUENCE:
            raise SemanticSourceCoverageError("A2 record authority boundary is invalid")
        reasons = _canonical_reasons(self.reasons)
        if reasons != self.reasons:
            raise SemanticSourceCoverageError("A2 record reason collection is not canonical")
        if self.semantic_proof_ids != tuple(sorted(set(self.semantic_proof_ids))):
            raise SemanticSourceCoverageError("A2 semantic proof collection is not canonical")
        semantic_values = _semantic_values_from_record(self)
        expected = stable_hash(
            _record_identity_material(
                census_manifest_id=self.census_manifest_id,
                capture_id=self.capture_id,
                market_input_hash=self.market_input_hash,
                lifecycle_record_id=self.lifecycle_record_id,
                descriptor_id=self.descriptor_id,
                market_ticker=self.market_ticker,
                lifecycle_state=self.lifecycle_state,
                product_type=self.product_type,
                payout_model=self.payout_model,
                exchange_category=self.exchange_category,
                series_ticker=self.series_ticker,
                recurrence=self.recurrence,
                strike_type=self.strike_type,
                specialist_route_state=self.specialist_route_state,
                semantic_values=semantic_values,
                semantic_proof_ids=self.semantic_proof_ids,
                settlement_sources=self.settlement_sources,
                settlement_source_identity=self.settlement_source_identity,
                reasons=reasons,
            )
        )
        if self.record_id != expected or self.content_hash != expected:
            raise SemanticSourceCoverageError("A2 record content-addressed identity mismatch")


@dataclass(frozen=True, slots=True, init=False)
class SemanticSourceQuarantineRecord:
    census_manifest_id: str
    capture_id: str
    market_input_hash: str
    quarantine_id: str
    observed_market_ticker: str | None
    reason: str
    detail: str
    schema_version: str
    record_id: str
    content_hash: str
    research_only: bool
    production_influence: Decimal

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("SemanticSourceQuarantineRecord is canonical-A2-issued only")

    @classmethod
    def _issue(
        cls,
        *,
        capability: object,
        context: _CensusRoutingContext,
        quarantine: CensusQuarantineRecord,
    ) -> SemanticSourceQuarantineRecord:
        if capability is not _A2_ISSUANCE_CAPABILITY:
            raise SemanticSourceCoverageError("A2 quarantine issuance capability is invalid")
        _validate_context_identity(context)
        if not any(item is quarantine for item in context.quarantines):
            raise SemanticSourceCoverageError("A2 quarantine is not exact KU-A1 evidence")
        material = _quarantine_identity_material(context.result.manifest.manifest_id, quarantine)
        digest = stable_hash(material)
        self = object.__new__(cls)
        for name, value in (
            ("census_manifest_id", context.result.manifest.manifest_id),
            ("capture_id", quarantine.capture_id),
            ("market_input_hash", quarantine.market_input_hash),
            ("quarantine_id", quarantine.quarantine_id),
            ("observed_market_ticker", quarantine.observed_market_ticker),
            ("reason", quarantine.reason),
            ("detail", quarantine.detail),
        ):
            object.__setattr__(self, name, value)
        object.__setattr__(self, "schema_version", A2_QUARANTINE_SCHEMA_VERSION)
        object.__setattr__(self, "record_id", digest)
        object.__setattr__(self, "content_hash", digest)
        object.__setattr__(self, "research_only", True)
        object.__setattr__(self, "production_influence", ZERO_INFLUENCE)
        return self

    def _validate_canonical_identity(self) -> None:
        if type(self) is not SemanticSourceQuarantineRecord:
            raise SemanticSourceCoverageError("A2 quarantine concrete type is not canonical")
        if self.schema_version != A2_QUARANTINE_SCHEMA_VERSION:
            raise SemanticSourceCoverageError("A2 quarantine schema identity is invalid")
        if self.research_only is not True or self.production_influence != ZERO_INFLUENCE:
            raise SemanticSourceCoverageError("A2 quarantine authority boundary is invalid")
        expected = stable_hash(
            (
                A2_QUARANTINE_SCHEMA_VERSION,
                self.census_manifest_id,
                self.capture_id,
                self.market_input_hash,
                self.quarantine_id,
                self.observed_market_ticker,
                self.reason,
                self.detail,
                "RESEARCH_ONLY_NO_INVENTED_SEMANTICS",
                "0",
            )
        )
        if self.record_id != expected or self.content_hash != expected:
            raise SemanticSourceCoverageError("A2 quarantine content-addressed identity mismatch")


@dataclass(frozen=True, slots=True, init=False)
class SemanticSourceCoverageManifest:
    census_manifest_id: str
    coverage_manifest_id: str
    input_market_count: int
    accounted_market_count: int
    parsed_market_count: int
    quarantine_count: int
    record_ids: tuple[str, ...]
    quarantine_record_ids: tuple[str, ...]
    lifecycle_record_ids: tuple[str, ...]
    quarantine_ids: tuple[str, ...]
    lifecycle_state_counts: tuple[tuple[str, int], ...]
    semantic_status_counts: tuple[tuple[str, int], ...]
    reason_origin_counts: tuple[tuple[str, int], ...]
    product_counts: tuple[tuple[str, int], ...]
    payout_counts: tuple[tuple[str, int], ...]
    strike_type_counts: tuple[tuple[str, int], ...]
    category_counts: tuple[tuple[str, int], ...]
    series_counts: tuple[tuple[str, int], ...]
    recurrence_counts: tuple[tuple[str, int], ...]
    settlement_source_name_counts: tuple[tuple[str, int], ...]
    settlement_source_host_counts: tuple[tuple[str, int], ...]
    settlement_source_origin_counts: tuple[tuple[str, int], ...]
    specialist_route_state_counts: tuple[tuple[str, int], ...]
    unknown_unavailable_counts: tuple[tuple[str, int], ...]
    schema_version: str
    manifest_id: str
    content_hash: str
    research_only: bool
    production_influence: Decimal

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("SemanticSourceCoverageManifest is canonical-A2-issued only")

    @classmethod
    def _issue(
        cls,
        *,
        capability: object,
        context: _CensusRoutingContext,
        records: tuple[MarketSemanticSourceCoverageRecord, ...],
        quarantines: tuple[SemanticSourceQuarantineRecord, ...],
    ) -> SemanticSourceCoverageManifest:
        if capability is not _A2_ISSUANCE_CAPABILITY:
            raise SemanticSourceCoverageError("A2 manifest issuance capability is invalid")
        _validate_a1_context(context)
        census = context.result
        for record in records:
            record._validate_canonical_identity()
        for quarantine in quarantines:
            quarantine._validate_canonical_identity()
        _validate_projection_bindings(census, records, quarantines)
        lifecycle_ids = tuple(sorted(record.lifecycle_record_id for record in records))
        quarantine_ids = tuple(sorted(record.quarantine_id for record in quarantines))
        record_ids = tuple(sorted(record.record_id for record in records))
        quarantine_record_ids = tuple(sorted(record.record_id for record in quarantines))
        if len(set(lifecycle_ids)) != len(lifecycle_ids):
            raise SemanticSourceCoverageError("duplicate A2 lifecycle identity")
        if len(set(quarantine_ids)) != len(quarantine_ids):
            raise SemanticSourceCoverageError("duplicate A2 quarantine identity")
        if lifecycle_ids != census.manifest.lifecycle_record_ids:
            raise SemanticSourceCoverageError(
                "A2 lifecycle identities do not conserve KU-A1 census"
            )
        if quarantine_ids != census.manifest.quarantine_ids:
            raise SemanticSourceCoverageError(
                "A2 quarantine identities do not conserve KU-A1 census"
            )
        if len(set(record_ids)) != len(record_ids):
            raise SemanticSourceCoverageError("duplicate A2 parsed record identity")
        if len(set(quarantine_record_ids)) != len(quarantine_record_ids):
            raise SemanticSourceCoverageError("duplicate A2 quarantine record identity")
        accounted = len(records) + len(quarantines)
        if accounted != census.manifest.input_market_count:
            raise SemanticSourceCoverageError(
                "A2 projection does not account for every market input"
            )
        values = _manifest_aggregates(records, quarantines)
        material = _manifest_identity_material(
            census_manifest_id=census.manifest.manifest_id,
            coverage_manifest_id=census.coverage_manifest.manifest_id,
            input_market_count=census.manifest.input_market_count,
            accounted_market_count=accounted,
            parsed_market_count=len(records),
            quarantine_count=len(quarantines),
            record_ids=record_ids,
            quarantine_record_ids=quarantine_record_ids,
            lifecycle_record_ids=lifecycle_ids,
            quarantine_ids=quarantine_ids,
            aggregates=values,
        )
        digest = stable_hash(material)
        self = object.__new__(cls)
        for name, value in (
            ("census_manifest_id", census.manifest.manifest_id),
            ("coverage_manifest_id", census.coverage_manifest.manifest_id),
            ("input_market_count", census.manifest.input_market_count),
            ("accounted_market_count", accounted),
            ("parsed_market_count", len(records)),
            ("quarantine_count", len(quarantines)),
            ("record_ids", record_ids),
            ("quarantine_record_ids", quarantine_record_ids),
            ("lifecycle_record_ids", lifecycle_ids),
            ("quarantine_ids", quarantine_ids),
        ):
            object.__setattr__(self, name, value)
        aggregate_names = (
            "lifecycle_state_counts",
            "semantic_status_counts",
            "reason_origin_counts",
            "product_counts",
            "payout_counts",
            "strike_type_counts",
            "category_counts",
            "series_counts",
            "recurrence_counts",
            "settlement_source_name_counts",
            "settlement_source_host_counts",
            "settlement_source_origin_counts",
            "specialist_route_state_counts",
            "unknown_unavailable_counts",
        )
        for name, value in zip(aggregate_names, values, strict=True):
            object.__setattr__(self, name, value)
        object.__setattr__(self, "schema_version", A2_MANIFEST_SCHEMA_VERSION)
        object.__setattr__(self, "manifest_id", digest)
        object.__setattr__(self, "content_hash", digest)
        object.__setattr__(self, "research_only", True)
        object.__setattr__(self, "production_influence", ZERO_INFLUENCE)
        return self

    def _validate_canonical_identity(self) -> None:
        if type(self) is not SemanticSourceCoverageManifest:
            raise SemanticSourceCoverageError("A2 manifest concrete type is not canonical")
        if self.schema_version != A2_MANIFEST_SCHEMA_VERSION:
            raise SemanticSourceCoverageError("A2 manifest schema identity is invalid")
        if self.research_only is not True or self.production_influence != ZERO_INFLUENCE:
            raise SemanticSourceCoverageError("A2 manifest authority boundary is invalid")
        aggregates = tuple(
            getattr(self, name)
            for name in (
                "lifecycle_state_counts",
                "semantic_status_counts",
                "reason_origin_counts",
                "product_counts",
                "payout_counts",
                "strike_type_counts",
                "category_counts",
                "series_counts",
                "recurrence_counts",
                "settlement_source_name_counts",
                "settlement_source_host_counts",
                "settlement_source_origin_counts",
                "specialist_route_state_counts",
                "unknown_unavailable_counts",
            )
        )
        expected = stable_hash(
            _manifest_identity_material(
                census_manifest_id=self.census_manifest_id,
                coverage_manifest_id=self.coverage_manifest_id,
                input_market_count=self.input_market_count,
                accounted_market_count=self.accounted_market_count,
                parsed_market_count=self.parsed_market_count,
                quarantine_count=self.quarantine_count,
                record_ids=self.record_ids,
                quarantine_record_ids=self.quarantine_record_ids,
                lifecycle_record_ids=self.lifecycle_record_ids,
                quarantine_ids=self.quarantine_ids,
                aggregates=aggregates,
            )
        )
        if self.manifest_id != expected or self.content_hash != expected:
            raise SemanticSourceCoverageError("A2 manifest content-addressed identity mismatch")


@dataclass(frozen=True, slots=True, init=False)
class SemanticSourceCoverageResult:
    census: UniverseCensusResult
    records: tuple[MarketSemanticSourceCoverageRecord, ...]
    quarantines: tuple[SemanticSourceQuarantineRecord, ...]
    manifest: SemanticSourceCoverageManifest
    schema_version: str
    result_id: str
    content_hash: str
    research_only: bool
    production_influence: Decimal

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("SemanticSourceCoverageResult is canonical-A2-issued only")

    @classmethod
    def _issue(
        cls,
        *,
        capability: object,
        context: _CensusRoutingContext,
        records: tuple[MarketSemanticSourceCoverageRecord, ...],
        quarantines: tuple[SemanticSourceQuarantineRecord, ...],
        manifest: SemanticSourceCoverageManifest,
    ) -> SemanticSourceCoverageResult:
        if capability is not _A2_ISSUANCE_CAPABILITY:
            raise SemanticSourceCoverageError("A2 result issuance capability is invalid")
        _validate_a1_context(context)
        census = context.result
        for record in records:
            record._validate_canonical_identity()
        for quarantine in quarantines:
            quarantine._validate_canonical_identity()
        manifest._validate_canonical_identity()
        _validate_projection_bindings(census, records, quarantines)
        expected_manifest = SemanticSourceCoverageManifest._issue(
            capability=_A2_ISSUANCE_CAPABILITY,
            context=context,
            records=records,
            quarantines=quarantines,
        )
        if manifest != expected_manifest:
            raise SemanticSourceCoverageError("A2 result manifest is not canonical for records")
        record_ids = tuple(sorted(record.record_id for record in records))
        quarantine_record_ids = tuple(sorted(record.record_id for record in quarantines))
        material = _result_identity_material(
            census_manifest_id=census.manifest.manifest_id,
            coverage_manifest_id=census.coverage_manifest.manifest_id,
            manifest_id=manifest.manifest_id,
            record_ids=record_ids,
            quarantine_record_ids=quarantine_record_ids,
        )
        digest = stable_hash(material)
        self = object.__new__(cls)
        object.__setattr__(self, "census", census)
        object.__setattr__(self, "records", records)
        object.__setattr__(self, "quarantines", quarantines)
        object.__setattr__(self, "manifest", manifest)
        object.__setattr__(self, "schema_version", A2_RESULT_SCHEMA_VERSION)
        object.__setattr__(self, "result_id", digest)
        object.__setattr__(self, "content_hash", digest)
        object.__setattr__(self, "research_only", True)
        object.__setattr__(self, "production_influence", ZERO_INFLUENCE)
        return self

    def _validate_canonical_identity(self) -> None:
        if type(self) is not SemanticSourceCoverageResult:
            raise SemanticSourceCoverageError("A2 result concrete type is not canonical")
        if self.schema_version != A2_RESULT_SCHEMA_VERSION:
            raise SemanticSourceCoverageError("A2 result schema identity is invalid")
        if self.research_only is not True or self.production_influence != ZERO_INFLUENCE:
            raise SemanticSourceCoverageError("A2 result authority boundary is invalid")
        self.manifest._validate_canonical_identity()
        for record in self.records:
            record._validate_canonical_identity()
        for quarantine in self.quarantines:
            quarantine._validate_canonical_identity()
        record_ids = tuple(sorted(record.record_id for record in self.records))
        quarantine_record_ids = tuple(sorted(record.record_id for record in self.quarantines))
        if self.manifest.record_ids != record_ids:
            raise SemanticSourceCoverageError("A2 result parsed-record identity mismatch")
        if self.manifest.quarantine_record_ids != quarantine_record_ids:
            raise SemanticSourceCoverageError("A2 result quarantine-record identity mismatch")
        if self.manifest.census_manifest_id != self.census.manifest.manifest_id:
            raise SemanticSourceCoverageError("A2 result/census manifest identity mismatch")
        if self.manifest.coverage_manifest_id != self.census.coverage_manifest.manifest_id:
            raise SemanticSourceCoverageError("A2 result/coverage manifest identity mismatch")
        expected = stable_hash(
            _result_identity_material(
                census_manifest_id=self.census.manifest.manifest_id,
                coverage_manifest_id=self.census.coverage_manifest.manifest_id,
                manifest_id=self.manifest.manifest_id,
                record_ids=record_ids,
                quarantine_record_ids=quarantine_record_ids,
            )
        )
        if self.result_id != expected or self.content_hash != expected:
            raise SemanticSourceCoverageError("A2 result content-addressed identity mismatch")


class SemanticSourceCoverageProjector:
    """Project KU-A2.1 from the single-parse KU-A1 routing execution."""

    def __init__(self, router: MarketUniverseRouter | None = None) -> None:
        self._router = router or MarketUniverseRouter()

    def project(
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
    ) -> SemanticSourceCoverageResult:
        context = self._router._census_with_context(
            market_rows=market_rows,
            event_rows=event_rows,
            series_rows=series_rows,
            source_authority=source_authority,
            request_locator=request_locator,
            response_sha256=response_sha256,
            captured_at=captured_at,
            previous_records=previous_records,
        )
        _validate_a1_context(context)
        records = tuple(
            sorted(
                (_project_routed(context, item) for item in context.routed_markets),
                key=lambda item: item.market_ticker,
            )
        )
        quarantines = tuple(
            sorted(
                (
                    SemanticSourceQuarantineRecord._issue(
                        capability=_A2_ISSUANCE_CAPABILITY,
                        context=context,
                        quarantine=item,
                    )
                    for item in context.quarantines
                ),
                key=lambda item: item.quarantine_id,
            )
        )
        manifest = SemanticSourceCoverageManifest._issue(
            capability=_A2_ISSUANCE_CAPABILITY,
            context=context,
            records=records,
            quarantines=quarantines,
        )
        return SemanticSourceCoverageResult._issue(
            capability=_A2_ISSUANCE_CAPABILITY,
            context=context,
            records=records,
            quarantines=quarantines,
            manifest=manifest,
        )


def project_semantic_source_coverage(
    *,
    market_rows: Iterable[Mapping[str, Any]],
    event_rows: Iterable[Mapping[str, Any]],
    series_rows: Iterable[Mapping[str, Any]],
    source_authority: str,
    request_locator: str,
    response_sha256: str,
    captured_at: datetime,
    previous_records: Mapping[str, MarketLifecycleRecord] | None = None,
) -> SemanticSourceCoverageResult:
    return SemanticSourceCoverageProjector().project(
        market_rows=market_rows,
        event_rows=event_rows,
        series_rows=series_rows,
        source_authority=source_authority,
        request_locator=request_locator,
        response_sha256=response_sha256,
        captured_at=captured_at,
        previous_records=previous_records,
    )


def _project_routed(
    context: _CensusRoutingContext, routed: _RoutedMarket
) -> MarketSemanticSourceCoverageRecord:
    return MarketSemanticSourceCoverageRecord._issue(
        capability=_A2_ISSUANCE_CAPABILITY,
        context=context,
        routed=routed,
    )


def _validate_context_identity(context: _CensusRoutingContext) -> None:
    if type(context) is not _CensusRoutingContext:
        raise SemanticSourceCoverageError("KU-A1 routing context concrete type is not canonical")
    expected = _routing_context_object_identity(
        context.result, context.routed_markets, context.quarantines
    )
    if context._object_identity_seal != expected:
        raise SemanticSourceCoverageError("KU-A1 routing context exact-object identity is invalid")


def _validate_a1_context(context: _CensusRoutingContext) -> None:
    _validate_context_identity(context)
    census = context.result
    if type(census) is not UniverseCensusResult:
        raise SemanticSourceCoverageError("KU-A1 census concrete type is not canonical")
    if type(census.capture) is not UniverseCaptureEvidence:
        raise SemanticSourceCoverageError("KU-A1 capture concrete type is not canonical")
    if type(census.manifest) is not UniverseCensusManifest:
        raise SemanticSourceCoverageError("KU-A1 census manifest concrete type is not canonical")
    if type(census.coverage_manifest) is not FamilyCoverageManifest:
        raise SemanticSourceCoverageError("KU-A1 coverage manifest concrete type is not canonical")
    try:
        expected_capture = UniverseCaptureEvidence(
            source_authority=census.capture.source_authority,
            request_locator=census.capture.request_locator,
            response_sha256=census.capture.response_sha256,
            captured_at=census.capture.captured_at,
        )
    except LifecycleError as exc:
        raise SemanticSourceCoverageError("KU-A1 capture identity is invalid") from exc
    if expected_capture != census.capture:
        raise SemanticSourceCoverageError("KU-A1 capture identity is invalid")
    for record in census.records:
        try:
            MarketLifecycleRecord._validate_canonical_identity(record)
        except LifecycleError as exc:
            raise SemanticSourceCoverageError("KU-A1 lifecycle identity is invalid") from exc
    expected_quarantines = tuple(_rebuild_quarantine(item) for item in census.quarantines)
    if expected_quarantines != census.quarantines:
        raise SemanticSourceCoverageError("KU-A1 quarantine identity is invalid")
    expected_descriptors = tuple(_rebuild_descriptor(item) for item in census.coverage_descriptors)
    if expected_descriptors != census.coverage_descriptors:
        raise SemanticSourceCoverageError("KU-A1 descriptor identity is invalid")
    try:
        expected_manifest = UniverseCensusManifest._issue(
            capability=_ROUTER_ISSUANCE_CAPABILITY,
            capture=census.capture,
            input_market_count=census.manifest.input_market_count,
            records=census.records,
            quarantines=census.quarantines,
        )
        expected_coverage = FamilyCoverageManifest._issue(
            capability=_ROUTER_ISSUANCE_CAPABILITY,
            census_manifest=expected_manifest,
            records=census.records,
            descriptors=census.coverage_descriptors,
        )
        expected_result = UniverseCensusResult._issue(
            capability=_ROUTER_ISSUANCE_CAPABILITY,
            capture=census.capture,
            records=census.records,
            quarantines=census.quarantines,
            manifest=expected_manifest,
            coverage_descriptors=census.coverage_descriptors,
            coverage_manifest=expected_coverage,
        )
    except UniverseCensusError as exc:
        raise SemanticSourceCoverageError("KU-A1 census receipt is not canonical") from exc
    if expected_result != census:
        raise SemanticSourceCoverageError("KU-A1 census receipt is not canonical")

    routed_lifecycle_ids = tuple(
        sorted(item.outcome.record.lifecycle_record_id for item in context.routed_markets)
    )
    if len(set(routed_lifecycle_ids)) != len(routed_lifecycle_ids):
        raise SemanticSourceCoverageError(
            "KU-A1 private routing context duplicates lifecycle identity"
        )
    if routed_lifecycle_ids != census.manifest.lifecycle_record_ids:
        raise SemanticSourceCoverageError("KU-A1 private routing context does not bind census")
    descriptor_ids = tuple(sorted(item.descriptor.descriptor_id for item in context.routed_markets))
    if len(set(descriptor_ids)) != len(descriptor_ids):
        raise SemanticSourceCoverageError("KU-A1 private descriptor context duplicates identity")
    if descriptor_ids != census.coverage_manifest.descriptor_ids:
        raise SemanticSourceCoverageError("KU-A1 private descriptor context does not bind census")
    if len(context.quarantines) != len(census.quarantines) or any(
        left is not right
        for left, right in zip(context.quarantines, census.quarantines, strict=True)
    ):
        raise SemanticSourceCoverageError("KU-A1 private quarantine context does not bind census")

    record_by_id = {record.lifecycle_record_id: record for record in census.records}
    descriptor_by_lifecycle = {
        descriptor.lifecycle_record_id: descriptor for descriptor in census.coverage_descriptors
    }
    if len(record_by_id) != len(census.records):
        raise SemanticSourceCoverageError("KU-A1 census duplicates lifecycle identity")
    if len(descriptor_by_lifecycle) != len(census.coverage_descriptors):
        raise SemanticSourceCoverageError("KU-A1 census duplicates descriptor lifecycle binding")
    for item in context.routed_markets:
        record = item.outcome.record
        canonical_record = record_by_id.get(record.lifecycle_record_id)
        if canonical_record is not record:
            raise SemanticSourceCoverageError(
                "KU-A1 routed lifecycle object is not exact census evidence"
            )
        canonical_descriptor = descriptor_by_lifecycle.get(record.lifecycle_record_id)
        if canonical_descriptor is not item.descriptor:
            raise SemanticSourceCoverageError(
                "KU-A1 routed descriptor object is not exact census evidence"
            )
        if item.parsed.input_hash != record.market_input_hash:
            raise SemanticSourceCoverageError("KU-A1 market-input identity mismatch")
        if item.descriptor.lifecycle_record_id != record.lifecycle_record_id:
            raise SemanticSourceCoverageError("KU-A1 descriptor/lifecycle identity mismatch")
        spec = item.outcome.specification
        if _recompute_settlement_source_identity(spec) != record.settlement_source_identity:
            raise SemanticSourceCoverageError("KU-A1 settlement-source identity mismatch")
        expected_proofs = _semantic_proof_ids(spec)
        if expected_proofs != record.semantic_proof_ids:
            raise SemanticSourceCoverageError(
                "KU-A1 semantic proof does not bind exact specification"
            )
        expected_status = spec.semantic_status.value if spec is not None else None
        if expected_status != record.semantic_status:
            raise SemanticSourceCoverageError("KU-A1 semantic status/specification mismatch")
        if spec is not None and (
            spec.market_ticker != record.market_ticker
            or spec.event_ticker != record.event_ticker
            or spec.series_ticker != record.series_ticker
        ):
            raise SemanticSourceCoverageError(
                "KU-A1 semantic specification parent identity mismatch"
            )


def _validate_projection_bindings(
    census: UniverseCensusResult,
    records: tuple[MarketSemanticSourceCoverageRecord, ...],
    quarantines: tuple[SemanticSourceQuarantineRecord, ...],
) -> None:
    record_by_id = {record.lifecycle_record_id: record for record in census.records}
    descriptor_by_lifecycle = {
        descriptor.lifecycle_record_id: descriptor for descriptor in census.coverage_descriptors
    }
    quarantine_by_id = {item.quarantine_id: item for item in census.quarantines}
    if len(record_by_id) != len(census.records) or len(quarantine_by_id) != len(census.quarantines):
        raise SemanticSourceCoverageError("KU-A1 census identity set is not unique")
    for projected in records:
        if projected.census_manifest_id != census.manifest.manifest_id:
            raise SemanticSourceCoverageError("A2 record/census manifest identity mismatch")
        canonical = record_by_id.get(projected.lifecycle_record_id)
        if canonical is None:
            raise SemanticSourceCoverageError("A2 record inserts foreign lifecycle identity")
        descriptor = descriptor_by_lifecycle.get(projected.lifecycle_record_id)
        if descriptor is None or descriptor.descriptor_id != projected.descriptor_id:
            raise SemanticSourceCoverageError("A2 record descriptor identity mismatch")
        if (
            projected.capture_id != canonical.capture_id
            or projected.market_input_hash != canonical.market_input_hash
            or projected.market_ticker != canonical.market_ticker
            or projected.lifecycle_state != canonical.state.value
            or projected.product_type != canonical.product_type.value
            or projected.payout_model != canonical.payout_model
            or projected.series_ticker != canonical.series_ticker
            or projected.specialist_route_state != canonical.specialist_route_state
            or projected.semantic_status != canonical.semantic_status
            or projected.semantic_proof_ids != canonical.semantic_proof_ids
            or projected.settlement_source_identity != canonical.settlement_source_identity
        ):
            raise SemanticSourceCoverageError("A2 record content does not bind canonical lifecycle")
    for projected in quarantines:
        if projected.census_manifest_id != census.manifest.manifest_id:
            raise SemanticSourceCoverageError("A2 quarantine/census manifest identity mismatch")
        canonical = quarantine_by_id.get(projected.quarantine_id)
        if canonical is None:
            raise SemanticSourceCoverageError("A2 quarantine inserts foreign identity")
        if (
            projected.capture_id != canonical.capture_id
            or projected.market_input_hash != canonical.market_input_hash
            or projected.observed_market_ticker != canonical.observed_market_ticker
            or projected.reason != canonical.reason
            or projected.detail != canonical.detail
        ):
            raise SemanticSourceCoverageError(
                "A2 quarantine content does not bind canonical evidence"
            )


def _rebuild_quarantine(item: CensusQuarantineRecord) -> CensusQuarantineRecord:
    if type(item) is not CensusQuarantineRecord:
        raise SemanticSourceCoverageError("KU-A1 quarantine concrete type is not canonical")
    return CensusQuarantineRecord(
        item.capture_id,
        item.market_input_hash,
        item.observed_market_ticker,
        item.occurrence_ordinal,
        item.reason,
        item.detail,
    )


def _rebuild_descriptor(item: MarketCoverageDescriptor) -> MarketCoverageDescriptor:
    if type(item) is not MarketCoverageDescriptor:
        raise SemanticSourceCoverageError("KU-A1 descriptor concrete type is not canonical")
    return MarketCoverageDescriptor(
        lifecycle_record_id=item.lifecycle_record_id,
        market_ticker=item.market_ticker,
        exchange_category=item.exchange_category,
        series_ticker=item.series_ticker,
        recurrence=item.recurrence,
        product_type=item.product_type,
        payout_model=item.payout_model,
        strike_type=item.strike_type,
        semantic_status=item.semantic_status,
        settlement_source_identity=item.settlement_source_identity,
        specialist_route_state=item.specialist_route_state,
        major_reasons=item.major_reasons,
        volume=item.volume,
        volume_24h=item.volume_24h,
        open_interest=item.open_interest,
        liquidity=item.liquidity,
        yes_bid=item.yes_bid,
        yes_ask=item.yes_ask,
        yes_bid_size=item.yes_bid_size,
        yes_ask_size=item.yes_ask_size,
        descriptor_issues=item.descriptor_issues,
    )


def _canonical_reasons(reasons: tuple[CoverageReason, ...]) -> tuple[CoverageReason, ...]:
    if any(
        type(reason) is not CoverageReason or type(reason.origin) is not ReasonOrigin
        for reason in reasons
    ):
        raise SemanticSourceCoverageError("A2 reason evidence is not canonical")
    return tuple(sorted(set(reasons), key=lambda item: (item.origin.value, item.code)))


def _semantic_values_from_record(
    record: MarketSemanticSourceCoverageRecord,
) -> tuple[object, ...]:
    return tuple(
        _identity_value(value)
        for value in (
            record.deterministic_parser_version,
            record.source_input_hash,
            record.semantic_hash,
            record.semantic_status,
            record.blocking_issue_codes,
            record.unsupported_features,
            record.comparator,
            record.threshold_value,
            record.lower_bound,
            record.upper_bound,
            record.inclusivity,
            record.provenance_ids,
        )
    )


def _record_identity_material(
    *,
    census_manifest_id: str,
    capture_id: str,
    market_input_hash: str,
    lifecycle_record_id: str,
    descriptor_id: str,
    market_ticker: str,
    lifecycle_state: str,
    product_type: str,
    payout_model: str,
    exchange_category: str | None,
    series_ticker: str | None,
    recurrence: str | None,
    strike_type: str | None,
    specialist_route_state: str | None,
    semantic_values: tuple[object, ...],
    semantic_proof_ids: tuple[str, ...],
    settlement_sources: tuple[SettlementSourceProjection, ...],
    settlement_source_identity: str | None,
    reasons: tuple[CoverageReason, ...],
) -> tuple[object, ...]:
    return (
        A2_RECORD_SCHEMA_VERSION,
        census_manifest_id,
        capture_id,
        market_input_hash,
        lifecycle_record_id,
        descriptor_id,
        market_ticker,
        lifecycle_state,
        product_type,
        payout_model,
        exchange_category,
        series_ticker,
        recurrence,
        strike_type,
        specialist_route_state,
        semantic_values,
        semantic_proof_ids,
        tuple(_source_material(source) for source in settlement_sources),
        settlement_source_identity,
        tuple((reason.origin.value, reason.code) for reason in reasons),
        "RESEARCH_ONLY_DESCRIPTIVE_NOT_READINESS_ECONOMICS_OR_PERMISSION",
        "0",
    )


def _quarantine_identity_material(
    census_manifest_id: str, quarantine: CensusQuarantineRecord
) -> tuple[object, ...]:
    return (
        A2_QUARANTINE_SCHEMA_VERSION,
        census_manifest_id,
        quarantine.capture_id,
        quarantine.market_input_hash,
        quarantine.quarantine_id,
        quarantine.observed_market_ticker,
        quarantine.reason,
        quarantine.detail,
        "RESEARCH_ONLY_NO_INVENTED_SEMANTICS",
        "0",
    )


def _manifest_identity_material(
    *,
    census_manifest_id: str,
    coverage_manifest_id: str,
    input_market_count: int,
    accounted_market_count: int,
    parsed_market_count: int,
    quarantine_count: int,
    record_ids: tuple[str, ...],
    quarantine_record_ids: tuple[str, ...],
    lifecycle_record_ids: tuple[str, ...],
    quarantine_ids: tuple[str, ...],
    aggregates: tuple[tuple[tuple[str, int], ...], ...],
) -> tuple[object, ...]:
    return (
        A2_MANIFEST_SCHEMA_VERSION,
        census_manifest_id,
        coverage_manifest_id,
        input_market_count,
        accounted_market_count,
        parsed_market_count,
        quarantine_count,
        record_ids,
        quarantine_record_ids,
        lifecycle_record_ids,
        quarantine_ids,
        *aggregates,
        "DESCRIPTIVE_ONLY_NO_READINESS_RANK_OR_ECONOMICS",
        "0",
    )


def _result_identity_material(
    *,
    census_manifest_id: str,
    coverage_manifest_id: str,
    manifest_id: str,
    record_ids: tuple[str, ...],
    quarantine_record_ids: tuple[str, ...],
) -> tuple[object, ...]:
    return (
        A2_RESULT_SCHEMA_VERSION,
        census_manifest_id,
        coverage_manifest_id,
        manifest_id,
        record_ids,
        quarantine_record_ids,
        "RESEARCH_ONLY",
        "0",
    )


def _semantic_values(spec: ContractSpecification | None) -> tuple[object, ...]:
    return tuple(_identity_value(value) for _, value in _semantic_field_items(spec))


def _identity_value(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, tuple):
        return tuple(_identity_value(item) for item in value)
    return value


def _semantic_field_items(spec: ContractSpecification | None) -> tuple[tuple[str, object], ...]:
    if spec is None:
        return (
            ("deterministic_parser_version", None),
            ("source_input_hash", None),
            ("semantic_hash", None),
            ("semantic_status", None),
            ("blocking_issue_codes", ()),
            ("unsupported_features", ()),
            ("comparator", None),
            ("threshold_value", None),
            ("lower_bound", None),
            ("upper_bound", None),
            ("inclusivity", None),
            ("provenance_ids", ()),
        )
    blocking = tuple(sorted(issue.issue_type.value for issue in spec.issues if issue.blocking))
    unsupported = tuple(sorted(set(spec.unsupported_features)))
    provenance_ids = tuple(
        sorted(
            stable_hash(
                (
                    item.field_name,
                    item.source_layer.value,
                    item.source_field,
                    item.source_document_id,
                    item.source_locator,
                    item.original_value,
                    item.parser,
                    item.parser_version,
                )
            )
            for item in spec.provenance
        )
    )
    return (
        ("deterministic_parser_version", spec.deterministic_parser_version),
        ("source_input_hash", spec.source_input_hash),
        ("semantic_hash", spec.semantic_hash),
        ("semantic_status", spec.semantic_status.value),
        ("blocking_issue_codes", blocking),
        ("unsupported_features", unsupported),
        ("comparator", spec.comparator.value),
        ("threshold_value", spec.threshold_value),
        ("lower_bound", spec.lower_bound),
        ("upper_bound", spec.upper_bound),
        ("inclusivity", spec.inclusivity),
        ("provenance_ids", provenance_ids),
    )


def _source_projection(source: SettlementSourceRecord) -> SettlementSourceProjection:
    return SettlementSourceProjection(
        source_id=source.source_id,
        normalized_name=source.normalized_name,
        exchange_name=source.exchange_name,
        url=source.url,
        hostname=_safe_hostname(source.url),
        origin=source.origin.value,
        classification=source.classification.value,
        source_hash=source.source_hash,
        current=source.current,
        first_seen=source.first_seen,
        last_seen=source.last_seen,
    )


def _source_material(source: SettlementSourceProjection) -> tuple[object, ...]:
    return (
        source.source_id,
        source.normalized_name,
        source.exchange_name,
        source.url,
        source.hostname,
        source.origin,
        source.classification,
        source.source_hash,
        source.current,
        source.first_seen.isoformat() if source.first_seen is not None else None,
        source.last_seen.isoformat() if source.last_seen is not None else None,
    )


def _safe_hostname(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"}:
            return None
        return parsed.hostname.lower() if parsed.hostname else None
    except ValueError:
        return None


def _recompute_settlement_source_identity(spec: ContractSpecification | None) -> str | None:
    if spec is None or not spec.settlement_sources:
        return None
    return stable_hash(
        tuple(
            sorted(
                (
                    source.source_hash,
                    source.normalized_name,
                    source.url or "",
                    source.origin.value,
                )
                for source in spec.settlement_sources
            )
        )
    )


def _counts(values: Iterable[str]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(Counter(values).items()))


def _manifest_aggregates(
    records: tuple[MarketSemanticSourceCoverageRecord, ...],
    quarantines: tuple[SemanticSourceQuarantineRecord, ...],
) -> tuple[tuple[tuple[str, int], ...], ...]:
    unknown = Counter[str]()
    for record in records:
        if record.semantic_status is None:
            unknown["SEMANTIC_STATUS_UNAVAILABLE"] += 1
        if not record.settlement_sources:
            unknown["SETTLEMENT_SOURCES_UNAVAILABLE"] += 1
        if record.exchange_category is None:
            unknown["CATEGORY_UNKNOWN"] += 1
        if record.series_ticker is None:
            unknown["SERIES_UNKNOWN"] += 1
        if record.recurrence is None:
            unknown["RECURRENCE_UNKNOWN"] += 1
        if record.strike_type is None:
            unknown["STRIKE_TYPE_UNKNOWN"] += 1
    unknown["QUARANTINED_INPUT"] += len(quarantines)
    unknown["QUARANTINE_TICKER_UNKNOWN"] += sum(
        item.observed_market_ticker is None for item in quarantines
    )
    reasons = [reason for record in records for reason in record.reasons]
    reasons.extend(CoverageReason(ReasonOrigin.QUARANTINE, item.reason) for item in quarantines)
    sources = [source for record in records for source in record.settlement_sources]
    return (
        _counts(record.lifecycle_state for record in records),
        _counts(record.semantic_status or "UNAVAILABLE" for record in records),
        _counts(reason.origin.value for reason in reasons),
        _counts(record.product_type for record in records),
        _counts(record.payout_model for record in records),
        _counts(record.strike_type or "UNKNOWN" for record in records),
        _counts(record.exchange_category or "UNKNOWN" for record in records),
        _counts(record.series_ticker or "UNKNOWN" for record in records),
        _counts(record.recurrence or "UNKNOWN" for record in records),
        _counts(source.normalized_name for source in sources),
        _counts(source.hostname or "UNKNOWN" for source in sources),
        _counts(source.origin for source in sources),
        _counts(record.specialist_route_state or "UNAVAILABLE" for record in records),
        tuple(sorted(unknown.items())),
    )
