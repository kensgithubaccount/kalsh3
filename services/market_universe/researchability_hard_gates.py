"""KU-A3.1 fail-closed researchability hard gates over canonical KU-A2.2 results.

The checkpoint is deliberately research-only.  It consumes an already-canonical A2.2
research-family result, preserves its exact A2.2 -> A2.1 -> A1 binding, and emits exactly
seven hard-gate decisions for each family represented by that result.  It performs no
network I/O and grants no lifecycle, economics, account, risk, signer, order, or execution
authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from .domain import stable_hash
from .lifecycle import ZERO_INFLUENCE
from .research_family_coverage import (
    ResearchFamily,
    ResearchFamilyCoverageError,
    ResearchFamilyCoverageResult,
)

A31_POLICY_VERSION = "ku-a3-1-researchability-hard-gates-v1"
A31_FAMILY_RECEIPT_SCHEMA_VERSION = "ku-a3-1-family-hard-gate-receipt-v1"
A31_RESULT_SCHEMA_VERSION = "ku-a3-1-researchability-hard-gate-result-v1"
A31_EMPIRICAL_ARTIFACT_STATUS = "EMPIRICAL_ARTIFACT_UNAVAILABLE"
_A31_ISSUANCE_CAPABILITY = object()


class ResearchabilityHardGateError(ValueError):
    """KU-A3.1 could not prove a conservative canonical hard-gate result."""


class ResearchabilityGate(StrEnum):
    G1_SETTLEMENT_PROOF = "G1_SETTLEMENT_PROOF"
    G2_PERMITTED_SOURCE = "G2_PERMITTED_SOURCE"
    G3_HISTORICAL_TRUTH = "G3_HISTORICAL_TRUTH"
    G4_POINT_IN_TIME_RECONSTRUCTION = "G4_POINT_IN_TIME_RECONSTRUCTION"
    G5_EVIDENCE_UNIT_POLICY = "G5_EVIDENCE_UNIT_POLICY"
    G6_ECONOMICS_OBSERVABILITY = "G6_ECONOMICS_OBSERVABILITY"
    G7_AUTHORITY_ISOLATION = "G7_AUTHORITY_ISOLATION"


HARD_GATE_ORDER: tuple[ResearchabilityGate, ...] = tuple(ResearchabilityGate)


class GateState(StrEnum):
    PASS = "PASS"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class GateReason(StrEnum):
    CANONICAL_PROOF = "CANONICAL_PROOF"
    CANONICAL_BLOCKER = "CANONICAL_BLOCKER"
    MISSING_CANONICAL_EVIDENCE = "MISSING_CANONICAL_EVIDENCE"
    EMPIRICAL_ARTIFACT_UNAVAILABLE = "EMPIRICAL_ARTIFACT_UNAVAILABLE"
    UNSUPPORTED_FAMILY = "UNSUPPORTED_FAMILY"
    UNKNOWN_UNMAPPED = "UNKNOWN_UNMAPPED"
    SETTLEMENT_PROOF_UNPROVEN = "SETTLEMENT_PROOF_UNPROVEN"
    SOURCE_POLICY_UNPROVEN = "SOURCE_POLICY_UNPROVEN"
    HISTORICAL_TRUTH_UNPROVEN = "HISTORICAL_TRUTH_UNPROVEN"
    PIT_UNPROVEN = "PIT_UNPROVEN"
    EVIDENCE_UNIT_UNPROVEN = "EVIDENCE_UNIT_UNPROVEN"
    ECONOMICS_OBSERVABILITY_UNPROVEN = "ECONOMICS_OBSERVABILITY_UNPROVEN"
    AUTHORITY_ISOLATION_PROVEN = "AUTHORITY_ISOLATION_PROVEN"


@dataclass(frozen=True, slots=True)
class GateDecision:
    gate: ResearchabilityGate
    state: GateState
    reason_codes: tuple[GateReason, ...]
    evidence_provenance: tuple[str, ...]


@dataclass(frozen=True, slots=True, init=False)
class ResearchabilityFamilyReceipt:
    family: ResearchFamily
    a22_result_id: str
    a22_report_id: str
    a22_mapping_ids: tuple[str, ...]
    a2_result_id: str
    a2_manifest_id: str
    census_manifest_id: str
    coverage_manifest_id: str
    capture_id: str
    gates: tuple[GateDecision, ...]
    empirical_artifact_status: str
    schema_version: str
    receipt_id: str
    content_hash: str
    research_only: bool
    production_influence: Decimal

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("ResearchabilityFamilyReceipt is canonical-A3.1-issued only")

    @classmethod
    def _issue(
        cls,
        *,
        capability: object,
        a22: ResearchFamilyCoverageResult,
        family: ResearchFamily,
        mapping_ids: tuple[str, ...],
    ) -> ResearchabilityFamilyReceipt:
        if capability is not _A31_ISSUANCE_CAPABILITY:
            raise ResearchabilityHardGateError("A3.1 family receipt issuance capability is invalid")
        gates = _gate_decisions(a22=a22, family=family, mapping_ids=mapping_ids)
        material = _family_receipt_identity_material(
            a22=a22,
            family=family,
            mapping_ids=mapping_ids,
            gates=gates,
        )
        digest = stable_hash(material)
        self = object.__new__(cls)
        for name, value in (
            ("family", family),
            ("a22_result_id", a22.result_id),
            ("a22_report_id", a22.report.report_id),
            ("a22_mapping_ids", mapping_ids),
            ("a2_result_id", a22.a2_result.result_id),
            ("a2_manifest_id", a22.a2_result.manifest.manifest_id),
            ("census_manifest_id", a22.a2_result.census.manifest.manifest_id),
            ("coverage_manifest_id", a22.a2_result.census.coverage_manifest.manifest_id),
            ("capture_id", a22.a2_result.census.capture.capture_id),
            ("gates", gates),
            ("empirical_artifact_status", A31_EMPIRICAL_ARTIFACT_STATUS),
        ):
            object.__setattr__(self, name, value)
        object.__setattr__(self, "schema_version", A31_FAMILY_RECEIPT_SCHEMA_VERSION)
        object.__setattr__(self, "receipt_id", digest)
        object.__setattr__(self, "content_hash", digest)
        object.__setattr__(self, "research_only", True)
        object.__setattr__(self, "production_influence", ZERO_INFLUENCE)
        return self

    def _validate_canonical_identity(self) -> None:
        if type(self) is not ResearchabilityFamilyReceipt:
            raise ResearchabilityHardGateError("A3.1 family receipt concrete type is not canonical")
        if self.schema_version != A31_FAMILY_RECEIPT_SCHEMA_VERSION:
            raise ResearchabilityHardGateError("A3.1 family receipt schema identity is invalid")
        if self.research_only is not True or self.production_influence != ZERO_INFLUENCE:
            raise ResearchabilityHardGateError("A3.1 family receipt authority boundary is invalid")
        if self.empirical_artifact_status != A31_EMPIRICAL_ARTIFACT_STATUS:
            raise ResearchabilityHardGateError("A3.1 empirical-artifact posture is invalid")
        if tuple(decision.gate for decision in self.gates) != HARD_GATE_ORDER:
            raise ResearchabilityHardGateError("A3.1 receipt must contain exactly G1-G7 once each")
        if any(type(decision.state) is not GateState for decision in self.gates):
            raise ResearchabilityHardGateError("A3.1 gate state is not canonical")
        if any(
            type(reason) is not GateReason
            for decision in self.gates
            for reason in decision.reason_codes
        ):
            raise ResearchabilityHardGateError("A3.1 gate reason is not canonical")
        expected = stable_hash(_family_receipt_identity_material_from_receipt(self))
        if self.receipt_id != expected or self.content_hash != expected:
            raise ResearchabilityHardGateError(
                "A3.1 family receipt content-addressed identity mismatch"
            )


@dataclass(frozen=True, slots=True, init=False)
class ResearchabilityHardGateResult:
    a22_result: ResearchFamilyCoverageResult
    family_receipts: tuple[ResearchabilityFamilyReceipt, ...]
    empirical_artifact_status: str
    schema_version: str
    result_id: str
    content_hash: str
    research_only: bool
    production_influence: Decimal
    _a22_object_identity_seal: tuple[object, ...]

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("ResearchabilityHardGateResult is canonical-A3.1-issued only")

    @classmethod
    def _issue(
        cls,
        *,
        capability: object,
        a22: ResearchFamilyCoverageResult,
        family_receipts: tuple[ResearchabilityFamilyReceipt, ...],
    ) -> ResearchabilityHardGateResult:
        if capability is not _A31_ISSUANCE_CAPABILITY:
            raise ResearchabilityHardGateError("A3.1 result issuance capability is invalid")
        _validate_a22_receipt(a22)
        _validate_family_receipt_conservation(a22, family_receipts)
        receipt_ids = tuple(receipt.receipt_id for receipt in family_receipts)
        material = (
            A31_RESULT_SCHEMA_VERSION,
            A31_POLICY_VERSION,
            A31_EMPIRICAL_ARTIFACT_STATUS,
            a22.result_id,
            a22.report.report_id,
            receipt_ids,
            "RESEARCH_ONLY_NO_PROMOTION_OR_RANKING",
            "0",
        )
        digest = stable_hash(material)
        self = object.__new__(cls)
        object.__setattr__(self, "a22_result", a22)
        object.__setattr__(self, "family_receipts", family_receipts)
        object.__setattr__(self, "empirical_artifact_status", A31_EMPIRICAL_ARTIFACT_STATUS)
        object.__setattr__(self, "schema_version", A31_RESULT_SCHEMA_VERSION)
        object.__setattr__(self, "result_id", digest)
        object.__setattr__(self, "content_hash", digest)
        object.__setattr__(self, "research_only", True)
        object.__setattr__(self, "production_influence", ZERO_INFLUENCE)
        object.__setattr__(self, "_a22_object_identity_seal", _a22_object_identity(a22))
        return self

    def _validate_canonical_identity(self) -> None:
        if type(self) is not ResearchabilityHardGateResult:
            raise ResearchabilityHardGateError("A3.1 result concrete type is not canonical")
        if self.schema_version != A31_RESULT_SCHEMA_VERSION:
            raise ResearchabilityHardGateError("A3.1 result schema identity is invalid")
        if self.research_only is not True or self.production_influence != ZERO_INFLUENCE:
            raise ResearchabilityHardGateError("A3.1 result authority boundary is invalid")
        if self.empirical_artifact_status != A31_EMPIRICAL_ARTIFACT_STATUS:
            raise ResearchabilityHardGateError("A3.1 result empirical-artifact posture is invalid")
        if self._a22_object_identity_seal != _a22_object_identity(self.a22_result):
            raise ResearchabilityHardGateError("A3.1 exact A2.2 object binding is invalid")
        _validate_a22_receipt(self.a22_result)
        _validate_family_receipt_conservation(self.a22_result, self.family_receipts)
        receipt_ids = tuple(receipt.receipt_id for receipt in self.family_receipts)
        expected = stable_hash(
            (
                A31_RESULT_SCHEMA_VERSION,
                A31_POLICY_VERSION,
                A31_EMPIRICAL_ARTIFACT_STATUS,
                self.a22_result.result_id,
                self.a22_result.report.report_id,
                receipt_ids,
                "RESEARCH_ONLY_NO_PROMOTION_OR_RANKING",
                "0",
            )
        )
        if self.result_id != expected or self.content_hash != expected:
            raise ResearchabilityHardGateError("A3.1 result content-addressed identity mismatch")


def build_researchability_hard_gate_result(
    a22_result: ResearchFamilyCoverageResult,
) -> ResearchabilityHardGateResult:
    """Evaluate fail-closed G1-G7 states over one already-canonical KU-A2.2 result."""

    _validate_a22_receipt(a22_result)
    families = _family_mapping_groups(a22_result)
    receipts = tuple(
        ResearchabilityFamilyReceipt._issue(
            capability=_A31_ISSUANCE_CAPABILITY,
            a22=a22_result,
            family=family,
            mapping_ids=mapping_ids,
        )
        for family, mapping_ids in families
    )
    return ResearchabilityHardGateResult._issue(
        capability=_A31_ISSUANCE_CAPABILITY,
        a22=a22_result,
        family_receipts=receipts,
    )


def _gate_decisions(
    *,
    a22: ResearchFamilyCoverageResult,
    family: ResearchFamily,
    mapping_ids: tuple[str, ...],
) -> tuple[GateDecision, ...]:
    base_provenance = (
        f"A2.2_RESULT:{a22.result_id}",
        f"A2.2_REPORT:{a22.report.report_id}",
        *(f"A2.2_MAPPING:{mapping_id}" for mapping_id in mapping_ids),
        f"A2.1_RESULT:{a22.a2_result.result_id}",
        f"A2.1_MANIFEST:{a22.a2_result.manifest.manifest_id}",
        f"A1_CENSUS_MANIFEST:{a22.a2_result.census.manifest.manifest_id}",
        f"A1_COVERAGE_MANIFEST:{a22.a2_result.census.coverage_manifest.manifest_id}",
        f"A1_CAPTURE:{a22.a2_result.census.capture.capture_id}",
    )
    unmapped_reason = (
        (GateReason.UNKNOWN_UNMAPPED,) if family is ResearchFamily.UNKNOWN_UNMAPPED else ()
    )
    empirical_provenance = (
        *base_provenance,
        f"CHECKPOINT:{A31_EMPIRICAL_ARTIFACT_STATUS}",
    )
    return (
        GateDecision(
            ResearchabilityGate.G1_SETTLEMENT_PROOF,
            GateState.UNKNOWN,
            (
                *unmapped_reason,
                GateReason.SETTLEMENT_PROOF_UNPROVEN,
                GateReason.MISSING_CANONICAL_EVIDENCE,
            ),
            base_provenance,
        ),
        GateDecision(
            ResearchabilityGate.G2_PERMITTED_SOURCE,
            GateState.UNKNOWN,
            (*unmapped_reason, GateReason.SOURCE_POLICY_UNPROVEN),
            base_provenance,
        ),
        GateDecision(
            ResearchabilityGate.G3_HISTORICAL_TRUTH,
            GateState.UNKNOWN,
            (
                *unmapped_reason,
                GateReason.HISTORICAL_TRUTH_UNPROVEN,
                GateReason.EMPIRICAL_ARTIFACT_UNAVAILABLE,
            ),
            empirical_provenance,
        ),
        GateDecision(
            ResearchabilityGate.G4_POINT_IN_TIME_RECONSTRUCTION,
            GateState.UNKNOWN,
            (*unmapped_reason, GateReason.PIT_UNPROVEN, GateReason.EMPIRICAL_ARTIFACT_UNAVAILABLE),
            empirical_provenance,
        ),
        GateDecision(
            ResearchabilityGate.G5_EVIDENCE_UNIT_POLICY,
            GateState.UNKNOWN,
            (*unmapped_reason, GateReason.EVIDENCE_UNIT_UNPROVEN),
            base_provenance,
        ),
        GateDecision(
            ResearchabilityGate.G6_ECONOMICS_OBSERVABILITY,
            GateState.UNKNOWN,
            (
                *unmapped_reason,
                GateReason.ECONOMICS_OBSERVABILITY_UNPROVEN,
                GateReason.EMPIRICAL_ARTIFACT_UNAVAILABLE,
            ),
            empirical_provenance,
        ),
        GateDecision(
            ResearchabilityGate.G7_AUTHORITY_ISOLATION,
            GateState.PASS,
            (GateReason.CANONICAL_PROOF, GateReason.AUTHORITY_ISOLATION_PROVEN),
            (
                *base_provenance,
                "A3.1_STATIC_AUTHORITY_BOUNDARY:RESEARCH_ONLY_ZERO_INFLUENCE",
            ),
        ),
    )


def _family_mapping_groups(
    a22: ResearchFamilyCoverageResult,
) -> tuple[tuple[ResearchFamily, tuple[str, ...]], ...]:
    grouped: dict[ResearchFamily, list[str]] = {}
    for mapping in a22.mappings:
        grouped.setdefault(mapping.family, []).append(mapping.mapping_id)
    return tuple(
        (family, tuple(sorted(grouped[family])))
        for family in sorted(grouped, key=lambda item: item.value)
    )


def _validate_a22_receipt(a22: ResearchFamilyCoverageResult) -> None:
    if type(a22) is not ResearchFamilyCoverageResult:
        raise ResearchabilityHardGateError("A2.2 result concrete type is not canonical")
    try:
        a22._validate_canonical_identity()
    except ResearchFamilyCoverageError as exc:
        raise ResearchabilityHardGateError(
            "A2.2 receipt is foreign, tampered, or mismatched"
        ) from exc
    if a22.research_only is not True or a22.production_influence != ZERO_INFLUENCE:
        raise ResearchabilityHardGateError("A2.2 authority boundary is invalid")


def _validate_family_receipt_conservation(
    a22: ResearchFamilyCoverageResult,
    receipts: tuple[ResearchabilityFamilyReceipt, ...],
) -> None:
    expected_groups = _family_mapping_groups(a22)
    if len(receipts) != len(expected_groups):
        raise ResearchabilityHardGateError("A3.1 family receipt conservation is invalid")
    if tuple(receipt.family for receipt in receipts) != tuple(
        family for family, _ in expected_groups
    ):
        raise ResearchabilityHardGateError("A3.1 family identity conservation is invalid")
    for receipt, (family, mapping_ids) in zip(receipts, expected_groups, strict=True):
        receipt._validate_canonical_identity()
        if receipt.family is not family or receipt.a22_mapping_ids != mapping_ids:
            raise ResearchabilityHardGateError("A3.1 family/A2.2 mapping binding is invalid")
        if (
            receipt.a22_result_id != a22.result_id
            or receipt.a22_report_id != a22.report.report_id
            or receipt.a2_result_id != a22.a2_result.result_id
            or receipt.a2_manifest_id != a22.a2_result.manifest.manifest_id
            or receipt.census_manifest_id != a22.a2_result.census.manifest.manifest_id
            or receipt.coverage_manifest_id != a22.a2_result.census.coverage_manifest.manifest_id
            or receipt.capture_id != a22.a2_result.census.capture.capture_id
        ):
            raise ResearchabilityHardGateError("A3.1 exact A2.2/A2.1/A1 binding is invalid")
        expected_gates = _gate_decisions(a22=a22, family=family, mapping_ids=mapping_ids)
        if receipt.gates != expected_gates:
            raise ResearchabilityHardGateError("A3.1 gate decisions do not match canonical policy")
    expected_mapping_ids = tuple(sorted(mapping.mapping_id for mapping in a22.mappings))
    actual_mapping_ids = tuple(
        sorted(mapping_id for receipt in receipts for mapping_id in receipt.a22_mapping_ids)
    )
    if actual_mapping_ids != expected_mapping_ids or len(set(actual_mapping_ids)) != len(
        actual_mapping_ids
    ):
        raise ResearchabilityHardGateError("A3.1 A2.2 mapping conservation is invalid")


def _family_receipt_identity_material(
    *,
    a22: ResearchFamilyCoverageResult,
    family: ResearchFamily,
    mapping_ids: tuple[str, ...],
    gates: tuple[GateDecision, ...],
) -> tuple[object, ...]:
    return (
        A31_FAMILY_RECEIPT_SCHEMA_VERSION,
        A31_POLICY_VERSION,
        A31_EMPIRICAL_ARTIFACT_STATUS,
        family.value,
        a22.result_id,
        a22.report.report_id,
        mapping_ids,
        a22.a2_result.result_id,
        a22.a2_result.manifest.manifest_id,
        a22.a2_result.census.manifest.manifest_id,
        a22.a2_result.census.coverage_manifest.manifest_id,
        a22.a2_result.census.capture.capture_id,
        _gate_identity_material(gates),
        "RESEARCH_ONLY_NO_PROMOTION_OR_RANKING",
        "0",
    )


def _family_receipt_identity_material_from_receipt(
    receipt: ResearchabilityFamilyReceipt,
) -> tuple[object, ...]:
    return (
        A31_FAMILY_RECEIPT_SCHEMA_VERSION,
        A31_POLICY_VERSION,
        A31_EMPIRICAL_ARTIFACT_STATUS,
        receipt.family.value,
        receipt.a22_result_id,
        receipt.a22_report_id,
        receipt.a22_mapping_ids,
        receipt.a2_result_id,
        receipt.a2_manifest_id,
        receipt.census_manifest_id,
        receipt.coverage_manifest_id,
        receipt.capture_id,
        _gate_identity_material(receipt.gates),
        "RESEARCH_ONLY_NO_PROMOTION_OR_RANKING",
        "0",
    )


def _gate_identity_material(gates: tuple[GateDecision, ...]) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            decision.gate.value,
            decision.state.value,
            tuple(reason.value for reason in decision.reason_codes),
            decision.evidence_provenance,
        )
        for decision in gates
    )


def _a22_object_identity(a22: ResearchFamilyCoverageResult) -> tuple[object, ...]:
    return (
        id(a22),
        id(a22.a2_result),
        id(a22.report),
        tuple(id(mapping) for mapping in a22.mappings),
        id(a22.a2_result.census),
        id(a22.a2_result.census.capture),
        id(a22.a2_result.census.manifest),
        id(a22.a2_result.census.coverage_manifest),
        id(a22.a2_result.manifest),
        tuple(id(record) for record in a22.a2_result.records),
        tuple(id(quarantine) for quarantine in a22.a2_result.quarantines),
    )
