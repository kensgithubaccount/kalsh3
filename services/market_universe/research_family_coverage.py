"""KU-A2.2 research-family mapping and offline descriptive coverage report.

Consumes canonical KU-A2.1 receipts only.  Family labels describe reviewed structural
research families; they do not imply readiness, source authority, economics, ranking,
or production permission.  This module performs no network I/O.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from .domain import stable_hash
from .lifecycle import ZERO_INFLUENCE
from .semantic_source_coverage import (
    MarketSemanticSourceCoverageRecord,
    SemanticSourceCoverageError,
    SemanticSourceCoverageResult,
    SemanticSourceQuarantineRecord,
    _validate_projection_bindings,
)

A22_RULESET_VERSION = "ku-a2-2-reviewed-family-rules-v1"
A22_MAPPING_SCHEMA_VERSION = "ku-a2-2-research-family-mapping-v1"
A22_REPORT_SCHEMA_VERSION = "ku-a2-2-offline-coverage-report-v1"
A22_RESULT_SCHEMA_VERSION = "ku-a2-2-family-coverage-result-v1"
_A22_ISSUANCE_CAPABILITY = object()
_UNAVAILABLE = "UNAVAILABLE"


class ResearchFamilyCoverageError(ValueError):
    """KU-A2.2 could not prove a conservative canonical research-only result."""


class ResearchFamily(StrEnum):
    """Reviewed structural families; none is a research-readiness decision."""

    BINARY_THRESHOLD = "BINARY_THRESHOLD"
    BINARY_INTERVAL = "BINARY_INTERVAL"
    BINARY_PROPOSITION = "BINARY_PROPOSITION"
    SCALAR_OR_PARTIAL = "SCALAR_OR_PARTIAL"
    UNKNOWN_UNMAPPED = "UNKNOWN_UNMAPPED"


class FamilyMappingStatus(StrEnum):
    MAPPED = "MAPPED"
    UNMAPPED = "UNMAPPED"


class SourceOutcomeKind(StrEnum):
    PARSED = "PARSED"
    QUARANTINE = "QUARANTINE"


@dataclass(frozen=True, slots=True)
class ReviewedFamilyRule:
    family: ResearchFamily
    rule_code: str
    required_evidence_fields: tuple[str, ...]
    rule_id: str

    @classmethod
    def build(
        cls,
        family: ResearchFamily,
        rule_code: str,
        required_evidence_fields: tuple[str, ...],
    ) -> ReviewedFamilyRule:
        fields = tuple(sorted(required_evidence_fields))
        rule_id = stable_hash((A22_RULESET_VERSION, family.value, rule_code, fields))
        return cls(family, rule_code, fields, rule_id)


_BINARY_THRESHOLD_RULE = ReviewedFamilyRule.build(
    ResearchFamily.BINARY_THRESHOLD,
    "VALID_BINARY_SINGLE_THRESHOLD",
    ("semantic_status", "product_type", "payout_model", "comparator", "threshold_value"),
)
_BINARY_INTERVAL_RULE = ReviewedFamilyRule.build(
    ResearchFamily.BINARY_INTERVAL,
    "VALID_BINARY_BOUNDED_INTERVAL",
    ("semantic_status", "product_type", "payout_model", "comparator", "lower_bound", "upper_bound"),
)
_BINARY_PROPOSITION_RULE = ReviewedFamilyRule.build(
    ResearchFamily.BINARY_PROPOSITION,
    "VALID_BINARY_PROPOSITION_WITHOUT_NUMERIC_STRIKE",
    ("semantic_status", "product_type", "payout_model", "comparator"),
)
_SCALAR_RULE = ReviewedFamilyRule.build(
    ResearchFamily.SCALAR_OR_PARTIAL,
    "VALID_SCALAR_OR_PARTIAL_PAYOUT",
    ("semantic_status", "product_type", "payout_model"),
)
_UNMAPPED_RULE = ReviewedFamilyRule.build(
    ResearchFamily.UNKNOWN_UNMAPPED,
    "NO_REVIEWED_RULE_PROVEN",
    (),
)
REVIEWED_FAMILY_RULES: tuple[ReviewedFamilyRule, ...] = (
    _BINARY_THRESHOLD_RULE,
    _BINARY_INTERVAL_RULE,
    _BINARY_PROPOSITION_RULE,
    _SCALAR_RULE,
    _UNMAPPED_RULE,
)


@dataclass(frozen=True, slots=True, init=False)
class ResearchFamilyMappingRecord:
    a2_result_id: str
    a2_manifest_id: str
    census_manifest_id: str
    coverage_manifest_id: str
    capture_id: str
    source_record_id: str
    source_kind: SourceOutcomeKind
    lifecycle_record_id: str | None
    quarantine_id: str | None
    market_ticker: str | None
    family: ResearchFamily
    mapping_status: FamilyMappingStatus
    rule_id: str
    rule_code: str
    evidence_fields: tuple[str, ...]
    schema_version: str
    mapping_id: str
    content_hash: str
    research_only: bool
    production_influence: Decimal

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("ResearchFamilyMappingRecord is canonical-A2.2-issued only")

    @classmethod
    def _issue_for_record(
        cls,
        *,
        capability: object,
        a2: SemanticSourceCoverageResult,
        record: MarketSemanticSourceCoverageRecord,
    ) -> ResearchFamilyMappingRecord:
        if capability is not _A22_ISSUANCE_CAPABILITY:
            raise ResearchFamilyCoverageError("A2.2 mapping issuance capability is invalid")
        if not any(item is record for item in a2.records):
            raise ResearchFamilyCoverageError("A2.2 parsed evidence is not exact A2.1 evidence")
        family, rule = _map_family(record)
        status = (
            FamilyMappingStatus.UNMAPPED
            if family is ResearchFamily.UNKNOWN_UNMAPPED
            else FamilyMappingStatus.MAPPED
        )
        return cls._issue(
            capability=capability,
            a2=a2,
            source_record_id=record.record_id,
            source_kind=SourceOutcomeKind.PARSED,
            lifecycle_record_id=record.lifecycle_record_id,
            quarantine_id=None,
            market_ticker=record.market_ticker,
            capture_id=record.capture_id,
            family=family,
            status=status,
            rule=rule,
        )

    @classmethod
    def _issue_for_quarantine(
        cls,
        *,
        capability: object,
        a2: SemanticSourceCoverageResult,
        quarantine: SemanticSourceQuarantineRecord,
    ) -> ResearchFamilyMappingRecord:
        if capability is not _A22_ISSUANCE_CAPABILITY:
            raise ResearchFamilyCoverageError("A2.2 mapping issuance capability is invalid")
        if not any(item is quarantine for item in a2.quarantines):
            raise ResearchFamilyCoverageError("A2.2 quarantine is not exact A2.1 evidence")
        return cls._issue(
            capability=capability,
            a2=a2,
            source_record_id=quarantine.record_id,
            source_kind=SourceOutcomeKind.QUARANTINE,
            lifecycle_record_id=None,
            quarantine_id=quarantine.quarantine_id,
            market_ticker=quarantine.observed_market_ticker,
            capture_id=quarantine.capture_id,
            family=ResearchFamily.UNKNOWN_UNMAPPED,
            status=FamilyMappingStatus.UNMAPPED,
            rule=_UNMAPPED_RULE,
        )

    @classmethod
    def _issue(
        cls,
        *,
        capability: object,
        a2: SemanticSourceCoverageResult,
        source_record_id: str,
        source_kind: SourceOutcomeKind,
        lifecycle_record_id: str | None,
        quarantine_id: str | None,
        market_ticker: str | None,
        capture_id: str,
        family: ResearchFamily,
        status: FamilyMappingStatus,
        rule: ReviewedFamilyRule,
    ) -> ResearchFamilyMappingRecord:
        if capability is not _A22_ISSUANCE_CAPABILITY:
            raise ResearchFamilyCoverageError("A2.2 mapping issuance capability is invalid")
        material = _mapping_identity_material(
            a2=a2,
            source_record_id=source_record_id,
            source_kind=source_kind,
            lifecycle_record_id=lifecycle_record_id,
            quarantine_id=quarantine_id,
            market_ticker=market_ticker,
            capture_id=capture_id,
            family=family,
            status=status,
            rule=rule,
        )
        digest = stable_hash(material)
        self = object.__new__(cls)
        for name, value in (
            ("a2_result_id", a2.result_id),
            ("a2_manifest_id", a2.manifest.manifest_id),
            ("census_manifest_id", a2.census.manifest.manifest_id),
            ("coverage_manifest_id", a2.census.coverage_manifest.manifest_id),
            ("capture_id", capture_id),
            ("source_record_id", source_record_id),
            ("source_kind", source_kind),
            ("lifecycle_record_id", lifecycle_record_id),
            ("quarantine_id", quarantine_id),
            ("market_ticker", market_ticker),
            ("family", family),
            ("mapping_status", status),
            ("rule_id", rule.rule_id),
            ("rule_code", rule.rule_code),
            ("evidence_fields", rule.required_evidence_fields),
        ):
            object.__setattr__(self, name, value)
        object.__setattr__(self, "schema_version", A22_MAPPING_SCHEMA_VERSION)
        object.__setattr__(self, "mapping_id", digest)
        object.__setattr__(self, "content_hash", digest)
        object.__setattr__(self, "research_only", True)
        object.__setattr__(self, "production_influence", ZERO_INFLUENCE)
        return self

    def _validate_canonical_identity(self) -> None:
        if type(self) is not ResearchFamilyMappingRecord:
            raise ResearchFamilyCoverageError("A2.2 mapping concrete type is not canonical")
        if self.schema_version != A22_MAPPING_SCHEMA_VERSION:
            raise ResearchFamilyCoverageError("A2.2 mapping schema identity is invalid")
        if self.research_only is not True or self.production_influence != ZERO_INFLUENCE:
            raise ResearchFamilyCoverageError("A2.2 mapping authority boundary is invalid")
        if (self.family is ResearchFamily.UNKNOWN_UNMAPPED) != (
            self.mapping_status is FamilyMappingStatus.UNMAPPED
        ):
            raise ResearchFamilyCoverageError("A2.2 mapped/unmapped status is inconsistent")
        rule = _rule_by_id(self.rule_id)
        if rule.family is not self.family or rule.rule_code != self.rule_code:
            raise ResearchFamilyCoverageError("A2.2 mapping reviewed-rule identity mismatch")
        if rule.required_evidence_fields != self.evidence_fields:
            raise ResearchFamilyCoverageError("A2.2 mapping evidence-field identity mismatch")
        expected = stable_hash(
            _mapping_identity_material_from_record(self, rule)
        )
        if self.mapping_id != expected or self.content_hash != expected:
            raise ResearchFamilyCoverageError("A2.2 mapping content-addressed identity mismatch")


@dataclass(frozen=True, slots=True, init=False)
class OfflineCoverageReport:
    a2_result_id: str
    a2_manifest_id: str
    census_manifest_id: str
    coverage_manifest_id: str
    capture_id: str
    mapping_ids: tuple[str, ...]
    total_input_count: int
    mapped_count: int
    unmapped_count: int
    family_counts: tuple[tuple[str, int], ...]
    mapping_status_counts: tuple[tuple[str, int], ...]
    settlement_source_presence_counts: tuple[tuple[str, int], ...]
    a2_lifecycle_state_counts: tuple[tuple[str, int], ...]
    a2_semantic_status_counts: tuple[tuple[str, int], ...]
    a2_reason_origin_counts: tuple[tuple[str, int], ...]
    a2_product_counts: tuple[tuple[str, int], ...]
    a2_payout_counts: tuple[tuple[str, int], ...]
    a2_strike_type_counts: tuple[tuple[str, int], ...]
    a2_category_counts: tuple[tuple[str, int], ...]
    a2_series_counts: tuple[tuple[str, int], ...]
    a2_recurrence_counts: tuple[tuple[str, int], ...]
    a2_settlement_source_origin_counts: tuple[tuple[str, int], ...]
    a2_unknown_unavailable_counts: tuple[tuple[str, int], ...]
    schema_version: str
    report_id: str
    content_hash: str
    research_only: bool
    production_influence: Decimal

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("OfflineCoverageReport is canonical-A2.2-issued only")

    @classmethod
    def _issue(
        cls,
        *,
        capability: object,
        a2: SemanticSourceCoverageResult,
        mappings: tuple[ResearchFamilyMappingRecord, ...],
    ) -> OfflineCoverageReport:
        if capability is not _A22_ISSUANCE_CAPABILITY:
            raise ResearchFamilyCoverageError("A2.2 report issuance capability is invalid")
        _validate_a2_receipt(a2)
        _validate_mapping_conservation(a2, mappings)
        for mapping in mappings:
            mapping._validate_canonical_identity()
        mapping_ids = tuple(sorted(item.mapping_id for item in mappings))
        family_counts = _counts(item.family.value for item in mappings)
        status_counts = _counts(item.mapping_status.value for item in mappings)
        presence_counts = _settlement_source_presence_counts(a2)
        mapped_count = sum(
            1 for item in mappings if item.mapping_status is FamilyMappingStatus.MAPPED
        )
        unmapped_count = len(mappings) - mapped_count
        manifest = a2.manifest
        a2_aggregates = (
            manifest.lifecycle_state_counts,
            manifest.semantic_status_counts,
            manifest.reason_origin_counts,
            manifest.product_counts,
            manifest.payout_counts,
            manifest.strike_type_counts,
            manifest.category_counts,
            manifest.series_counts,
            manifest.recurrence_counts,
            manifest.settlement_source_origin_counts,
            manifest.unknown_unavailable_counts,
        )
        material = (
            A22_REPORT_SCHEMA_VERSION,
            A22_RULESET_VERSION,
            a2.result_id,
            manifest.manifest_id,
            a2.census.manifest.manifest_id,
            a2.census.coverage_manifest.manifest_id,
            a2.census.capture.capture_id,
            mapping_ids,
            manifest.input_market_count,
            mapped_count,
            unmapped_count,
            family_counts,
            status_counts,
            presence_counts,
            *a2_aggregates,
            "DESCRIPTIVE_ONLY_NO_READINESS_OR_RANKING",
            "0",
        )
        digest = stable_hash(material)
        self = object.__new__(cls)
        for name, value in (
            ("a2_result_id", a2.result_id),
            ("a2_manifest_id", manifest.manifest_id),
            ("census_manifest_id", a2.census.manifest.manifest_id),
            ("coverage_manifest_id", a2.census.coverage_manifest.manifest_id),
            ("capture_id", a2.census.capture.capture_id),
            ("mapping_ids", mapping_ids),
            ("total_input_count", manifest.input_market_count),
            ("mapped_count", mapped_count),
            ("unmapped_count", unmapped_count),
            ("family_counts", family_counts),
            ("mapping_status_counts", status_counts),
            ("settlement_source_presence_counts", presence_counts),
        ):
            object.__setattr__(self, name, value)
        aggregate_names = (
            "a2_lifecycle_state_counts",
            "a2_semantic_status_counts",
            "a2_reason_origin_counts",
            "a2_product_counts",
            "a2_payout_counts",
            "a2_strike_type_counts",
            "a2_category_counts",
            "a2_series_counts",
            "a2_recurrence_counts",
            "a2_settlement_source_origin_counts",
            "a2_unknown_unavailable_counts",
        )
        for name, value in zip(aggregate_names, a2_aggregates, strict=True):
            object.__setattr__(self, name, value)
        object.__setattr__(self, "schema_version", A22_REPORT_SCHEMA_VERSION)
        object.__setattr__(self, "report_id", digest)
        object.__setattr__(self, "content_hash", digest)
        object.__setattr__(self, "research_only", True)
        object.__setattr__(self, "production_influence", ZERO_INFLUENCE)
        return self

    def _validate_canonical_identity(self) -> None:
        if type(self) is not OfflineCoverageReport:
            raise ResearchFamilyCoverageError("A2.2 report concrete type is not canonical")
        if self.schema_version != A22_REPORT_SCHEMA_VERSION:
            raise ResearchFamilyCoverageError("A2.2 report schema identity is invalid")
        if self.research_only is not True or self.production_influence != ZERO_INFLUENCE:
            raise ResearchFamilyCoverageError("A2.2 report authority boundary is invalid")
        if self.total_input_count != self.mapped_count + self.unmapped_count:
            raise ResearchFamilyCoverageError("A2.2 report does not conserve every input")
        expected = stable_hash(
            (
                A22_REPORT_SCHEMA_VERSION,
                A22_RULESET_VERSION,
                self.a2_result_id,
                self.a2_manifest_id,
                self.census_manifest_id,
                self.coverage_manifest_id,
                self.capture_id,
                self.mapping_ids,
                self.total_input_count,
                self.mapped_count,
                self.unmapped_count,
                self.family_counts,
                self.mapping_status_counts,
                self.settlement_source_presence_counts,
                self.a2_lifecycle_state_counts,
                self.a2_semantic_status_counts,
                self.a2_reason_origin_counts,
                self.a2_product_counts,
                self.a2_payout_counts,
                self.a2_strike_type_counts,
                self.a2_category_counts,
                self.a2_series_counts,
                self.a2_recurrence_counts,
                self.a2_settlement_source_origin_counts,
                self.a2_unknown_unavailable_counts,
                "DESCRIPTIVE_ONLY_NO_READINESS_OR_RANKING",
                "0",
            )
        )
        if self.report_id != expected or self.content_hash != expected:
            raise ResearchFamilyCoverageError("A2.2 report content-addressed identity mismatch")


@dataclass(frozen=True, slots=True, init=False)
class ResearchFamilyCoverageResult:
    a2_result: SemanticSourceCoverageResult
    mappings: tuple[ResearchFamilyMappingRecord, ...]
    report: OfflineCoverageReport
    schema_version: str
    result_id: str
    content_hash: str
    research_only: bool
    production_influence: Decimal
    _a2_object_identity_seal: tuple[object, ...]

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("ResearchFamilyCoverageResult is canonical-A2.2-issued only")

    @classmethod
    def _issue(
        cls,
        *,
        capability: object,
        a2: SemanticSourceCoverageResult,
        mappings: tuple[ResearchFamilyMappingRecord, ...],
        report: OfflineCoverageReport,
    ) -> ResearchFamilyCoverageResult:
        if capability is not _A22_ISSUANCE_CAPABILITY:
            raise ResearchFamilyCoverageError("A2.2 result issuance capability is invalid")
        _validate_a2_receipt(a2)
        _validate_mapping_conservation(a2, mappings)
        for mapping in mappings:
            mapping._validate_canonical_identity()
        report._validate_canonical_identity()
        expected_report = OfflineCoverageReport._issue(
            capability=_A22_ISSUANCE_CAPABILITY,
            a2=a2,
            mappings=mappings,
        )
        if report != expected_report:
            raise ResearchFamilyCoverageError("A2.2 report is not canonical for mappings")
        mapping_ids = tuple(sorted(item.mapping_id for item in mappings))
        material = (
            A22_RESULT_SCHEMA_VERSION,
            A22_RULESET_VERSION,
            a2.result_id,
            a2.manifest.manifest_id,
            report.report_id,
            mapping_ids,
            "RESEARCH_ONLY",
            "0",
        )
        digest = stable_hash(material)
        self = object.__new__(cls)
        object.__setattr__(self, "a2_result", a2)
        object.__setattr__(self, "mappings", mappings)
        object.__setattr__(self, "report", report)
        object.__setattr__(self, "schema_version", A22_RESULT_SCHEMA_VERSION)
        object.__setattr__(self, "result_id", digest)
        object.__setattr__(self, "content_hash", digest)
        object.__setattr__(self, "research_only", True)
        object.__setattr__(self, "production_influence", ZERO_INFLUENCE)
        object.__setattr__(self, "_a2_object_identity_seal", _a2_object_identity(a2))
        return self

    def _validate_canonical_identity(self) -> None:
        if type(self) is not ResearchFamilyCoverageResult:
            raise ResearchFamilyCoverageError("A2.2 result concrete type is not canonical")
        if self.schema_version != A22_RESULT_SCHEMA_VERSION:
            raise ResearchFamilyCoverageError("A2.2 result schema identity is invalid")
        if self.research_only is not True or self.production_influence != ZERO_INFLUENCE:
            raise ResearchFamilyCoverageError("A2.2 result authority boundary is invalid")
        if self._a2_object_identity_seal != _a2_object_identity(self.a2_result):
            raise ResearchFamilyCoverageError("A2.2 exact A2.1 object binding is invalid")
        _validate_a2_receipt(self.a2_result)
        _validate_mapping_conservation(self.a2_result, self.mappings)
        for mapping in self.mappings:
            mapping._validate_canonical_identity()
        self.report._validate_canonical_identity()
        expected_report = OfflineCoverageReport._issue(
            capability=_A22_ISSUANCE_CAPABILITY,
            a2=self.a2_result,
            mappings=self.mappings,
        )
        if self.report != expected_report:
            raise ResearchFamilyCoverageError("A2.2 result/report binding is invalid")
        mapping_ids = tuple(sorted(item.mapping_id for item in self.mappings))
        expected = stable_hash(
            (
                A22_RESULT_SCHEMA_VERSION,
                A22_RULESET_VERSION,
                self.a2_result.result_id,
                self.a2_result.manifest.manifest_id,
                self.report.report_id,
                mapping_ids,
                "RESEARCH_ONLY",
                "0",
            )
        )
        if self.result_id != expected or self.content_hash != expected:
            raise ResearchFamilyCoverageError("A2.2 result content-addressed identity mismatch")


def build_research_family_offline_report(
    a2_result: SemanticSourceCoverageResult,
) -> ResearchFamilyCoverageResult:
    """Map and describe one already-canonical KU-A2.1 result without acquisition."""

    _validate_a2_receipt(a2_result)
    mappings = tuple(
        sorted(
            (
                *(
                    ResearchFamilyMappingRecord._issue_for_record(
                        capability=_A22_ISSUANCE_CAPABILITY,
                        a2=a2_result,
                        record=record,
                    )
                    for record in a2_result.records
                ),
                *(
                    ResearchFamilyMappingRecord._issue_for_quarantine(
                        capability=_A22_ISSUANCE_CAPABILITY,
                        a2=a2_result,
                        quarantine=quarantine,
                    )
                    for quarantine in a2_result.quarantines
                ),
            ),
            key=lambda item: item.source_record_id,
        )
    )
    report = OfflineCoverageReport._issue(
        capability=_A22_ISSUANCE_CAPABILITY,
        a2=a2_result,
        mappings=mappings,
    )
    return ResearchFamilyCoverageResult._issue(
        capability=_A22_ISSUANCE_CAPABILITY,
        a2=a2_result,
        mappings=mappings,
        report=report,
    )


def _map_family(
    record: MarketSemanticSourceCoverageRecord,
) -> tuple[ResearchFamily, ReviewedFamilyRule]:
    """Apply only reviewed structural rules; descriptive metadata is intentionally ignored."""

    if record.semantic_status != "VALID":
        return ResearchFamily.UNKNOWN_UNMAPPED, _UNMAPPED_RULE
    if record.product_type == "BINARY_EVENT" and record.payout_model == "SIMPLE_BINARY":
        if (
            record.comparator == "between"
            and record.lower_bound is not None
            and record.upper_bound is not None
        ):
            return ResearchFamily.BINARY_INTERVAL, _BINARY_INTERVAL_RULE
        if record.comparator in {">", ">=", "<", "<=", "="} and record.threshold_value is not None:
            return ResearchFamily.BINARY_THRESHOLD, _BINARY_THRESHOLD_RULE
        if (
            record.comparator in {None, "none"}
            and record.threshold_value is None
            and record.lower_bound is None
            and record.upper_bound is None
        ):
            return ResearchFamily.BINARY_PROPOSITION, _BINARY_PROPOSITION_RULE
    if record.product_type == "SCALAR_OR_PARTIAL" and record.payout_model == "SCALAR_OR_PARTIAL":
        return ResearchFamily.SCALAR_OR_PARTIAL, _SCALAR_RULE
    return ResearchFamily.UNKNOWN_UNMAPPED, _UNMAPPED_RULE


def _validate_a2_receipt(a2: SemanticSourceCoverageResult) -> None:
    if type(a2) is not SemanticSourceCoverageResult:
        raise ResearchFamilyCoverageError("A2.1 result concrete type is not canonical")
    try:
        a2._validate_canonical_identity()
        _validate_projection_bindings(a2.census, a2.records, a2.quarantines)
    except SemanticSourceCoverageError as exc:
        raise ResearchFamilyCoverageError("A2.1 receipt is foreign or tampered") from exc
    capture_id = a2.census.capture.capture_id
    if a2.census.manifest.capture_id != capture_id:
        raise ResearchFamilyCoverageError("A2.1 census/capture binding is invalid")
    if any(record.capture_id != capture_id for record in a2.records):
        raise ResearchFamilyCoverageError("A2.1 parsed capture binding is invalid")
    if any(record.capture_id != capture_id for record in a2.quarantines):
        raise ResearchFamilyCoverageError("A2.1 quarantine capture binding is invalid")
    if a2.manifest.input_market_count != len(a2.records) + len(a2.quarantines):
        raise ResearchFamilyCoverageError("A2.1 receipt does not conserve every input")


def _validate_mapping_conservation(
    a2: SemanticSourceCoverageResult,
    mappings: tuple[ResearchFamilyMappingRecord, ...],
) -> None:
    if len(mappings) != a2.manifest.input_market_count:
        raise ResearchFamilyCoverageError("A2.2 mapping silently drops or inserts an input")
    expected_sources = tuple(
        sorted((*a2.manifest.record_ids, *a2.manifest.quarantine_record_ids))
    )
    actual_sources = tuple(sorted(item.source_record_id for item in mappings))
    if len(set(actual_sources)) != len(actual_sources) or actual_sources != expected_sources:
        raise ResearchFamilyCoverageError("A2.2 mapping source conservation is invalid")
    for mapping in mappings:
        if (
            mapping.a2_result_id != a2.result_id
            or mapping.a2_manifest_id != a2.manifest.manifest_id
        ):
            raise ResearchFamilyCoverageError("A2.2 mapping does not bind exact A2.1 receipt")
        if mapping.census_manifest_id != a2.census.manifest.manifest_id:
            raise ResearchFamilyCoverageError("A2.2 mapping/census binding is invalid")
        if mapping.coverage_manifest_id != a2.census.coverage_manifest.manifest_id:
            raise ResearchFamilyCoverageError("A2.2 mapping/coverage binding is invalid")
        if mapping.capture_id != a2.census.capture.capture_id:
            raise ResearchFamilyCoverageError("A2.2 mapping/capture binding is invalid")


def _settlement_source_presence_counts(
    a2: SemanticSourceCoverageResult,
) -> tuple[tuple[str, int], ...]:
    values = ["PRESENT" if record.settlement_sources else "ABSENT" for record in a2.records]
    values.extend(_UNAVAILABLE for _ in a2.quarantines)
    return _counts(values)


def _mapping_identity_material(
    *,
    a2: SemanticSourceCoverageResult,
    source_record_id: str,
    source_kind: SourceOutcomeKind,
    lifecycle_record_id: str | None,
    quarantine_id: str | None,
    market_ticker: str | None,
    capture_id: str,
    family: ResearchFamily,
    status: FamilyMappingStatus,
    rule: ReviewedFamilyRule,
) -> tuple[object, ...]:
    return (
        A22_MAPPING_SCHEMA_VERSION,
        A22_RULESET_VERSION,
        a2.result_id,
        a2.manifest.manifest_id,
        a2.census.manifest.manifest_id,
        a2.census.coverage_manifest.manifest_id,
        capture_id,
        source_record_id,
        source_kind.value,
        lifecycle_record_id,
        quarantine_id,
        market_ticker,
        family.value,
        status.value,
        rule.rule_id,
        rule.rule_code,
        rule.required_evidence_fields,
        "RESEARCH_ONLY_NO_READINESS",
        "0",
    )


def _mapping_identity_material_from_record(
    record: ResearchFamilyMappingRecord,
    rule: ReviewedFamilyRule,
) -> tuple[object, ...]:
    return (
        A22_MAPPING_SCHEMA_VERSION,
        A22_RULESET_VERSION,
        record.a2_result_id,
        record.a2_manifest_id,
        record.census_manifest_id,
        record.coverage_manifest_id,
        record.capture_id,
        record.source_record_id,
        record.source_kind.value,
        record.lifecycle_record_id,
        record.quarantine_id,
        record.market_ticker,
        record.family.value,
        record.mapping_status.value,
        rule.rule_id,
        rule.rule_code,
        rule.required_evidence_fields,
        "RESEARCH_ONLY_NO_READINESS",
        "0",
    )


def _rule_by_id(rule_id: str) -> ReviewedFamilyRule:
    for rule in REVIEWED_FAMILY_RULES:
        if rule.rule_id == rule_id:
            return rule
    raise ResearchFamilyCoverageError("A2.2 mapping references an unknown reviewed rule")


def _counts(values: Iterable[str]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(Counter(values).items()))


def _a2_object_identity(a2: SemanticSourceCoverageResult) -> tuple[object, ...]:
    return (
        id(a2),
        id(a2.census),
        id(a2.census.capture),
        id(a2.census.manifest),
        id(a2.census.coverage_manifest),
        id(a2.manifest),
        tuple(id(record) for record in a2.records),
        tuple(id(quarantine) for quarantine in a2.quarantines),
    )
