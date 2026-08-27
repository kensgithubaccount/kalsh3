"""KU-A3.2 empirical researchability investigation over canonical KU-A3.1 receipts.

This checkpoint is deliberately fail-closed.  It conserves every exact KU-A2.2 mapping
behind the supplied canonical KU-A3.1 result, but does not infer an evidence-homogeneous
domain from structural family, title, category, hostname, or advisory routing.  No
repository-canonical empirical artifact currently closes that join, so G1-G6 remain
UNKNOWN and G7's research-only authority isolation remains PASS.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from .domain import stable_hash
from .lifecycle import ZERO_INFLUENCE
from .research_family_coverage import ResearchFamily, ResearchFamilyMappingRecord
from .researchability_hard_gates import (
    A31_EMPIRICAL_ARTIFACT_STATUS,
    HARD_GATE_ORDER,
    GateState,
    ResearchabilityFamilyReceipt,
    ResearchabilityGate,
    ResearchabilityHardGateError,
    ResearchabilityHardGateResult,
)

A32_POLICY_VERSION = "ku-a3-2-empirical-researchability-investigation-v1"
A32_DOMAIN_RECEIPT_SCHEMA_VERSION = "ku-a3-2-evidence-domain-receipt-v1"
A32_RESULT_SCHEMA_VERSION = "ku-a3-2-evidence-resolution-result-v1"
_A32_ISSUANCE_CAPABILITY = object()


class EmpiricalResearchabilityError(ValueError):
    """KU-A3.2 could not prove a conservative canonical evidence resolution."""


class EvidenceDomain(StrEnum):
    """A3.2 evidence-homogeneous domain identity.

    UNASSIGNED is intentional: current canonical A3.1/A2.2/A2.1/A1 receipts do not
    positively bind a reviewed empirical evidence domain without re-inferring semantics.
    """

    UNASSIGNED = "UNASSIGNED"


class EvidenceProofKind(StrEnum):
    STRUCTURAL = "STRUCTURAL"
    EMPIRICAL = "EMPIRICAL"


@dataclass(frozen=True, slots=True)
class GateResolution:
    gate: ResearchabilityGate
    prior_state: GateState
    resolved_state: GateState
    evidence_provenance: tuple[str, ...]
    proof_kind: EvidenceProofKind | None
    missing_evidence: tuple[str, ...]
    blocker_evidence: tuple[str, ...]


_MISSING_BY_GATE: dict[ResearchabilityGate, tuple[str, ...]] = {
    ResearchabilityGate.G1_SETTLEMENT_PROOF: (
        "MISSING:EXACT_SETTLEMENT_TARGET_DOMAIN_BINDING",
    ),
    ResearchabilityGate.G2_PERMITTED_SOURCE: (
        "MISSING:EXPLICIT_DOMAIN_SOURCE_PERMISSION",
    ),
    ResearchabilityGate.G3_HISTORICAL_TRUTH: (
        "MISSING:REPOSITORY_CANONICAL_HISTORICAL_SETTLEMENT_TRUTH",
    ),
    ResearchabilityGate.G4_POINT_IN_TIME_RECONSTRUCTION: (
        "MISSING:REPOSITORY_CANONICAL_POINT_IN_TIME_VINTAGES",
    ),
    ResearchabilityGate.G5_EVIDENCE_UNIT_POLICY: (
        "MISSING:REVIEWED_DOMAIN_EVIDENCE_UNIT_POLICY",
    ),
    ResearchabilityGate.G6_ECONOMICS_OBSERVABILITY: (
        "MISSING:COMPLETE_HISTORICAL_AFTER_COST_OBSERVABILITY_EVIDENCE",
        "MISSING:M28D_R2_OR_EQUIVALENT_COMPLETION",
    ),
    ResearchabilityGate.G7_AUTHORITY_ISOLATION: (),
}


@dataclass(frozen=True, slots=True, init=False)
class EvidenceDomainReceipt:
    family: ResearchFamily
    evidence_domain: EvidenceDomain
    domain_evidence_provenance: tuple[str, ...]
    a31_result_id: str
    a31_receipt_id: str
    a22_result_id: str
    a22_report_id: str
    a22_mapping_id: str
    a2_result_id: str
    a2_manifest_id: str
    census_manifest_id: str
    coverage_manifest_id: str
    capture_id: str
    source_record_id: str
    market_ticker: str | None
    gates: tuple[GateResolution, ...]
    empirical_artifact_status: str
    schema_version: str
    receipt_id: str
    content_hash: str
    research_only: bool
    production_influence: Decimal

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("EvidenceDomainReceipt is canonical-A3.2-issued only")

    @classmethod
    def _issue(
        cls,
        *,
        capability: object,
        a31: ResearchabilityHardGateResult,
        mapping: ResearchFamilyMappingRecord,
        prior: ResearchabilityFamilyReceipt,
    ) -> EvidenceDomainReceipt:
        if capability is not _A32_ISSUANCE_CAPABILITY:
            raise EmpiricalResearchabilityError("A3.2 receipt issuance capability is invalid")
        if mapping.mapping_id not in prior.a22_mapping_ids or mapping.family is not prior.family:
            raise EmpiricalResearchabilityError(
                "A3.2 mapping does not bind exact A3.1 family receipt"
            )
        domain = EvidenceDomain.UNASSIGNED
        domain_provenance: tuple[str, ...] = ()
        gates = _gate_resolutions(a31=a31, mapping=mapping, prior=prior, domain=domain)
        material = _receipt_identity_material(
            a31=a31,
            mapping=mapping,
            prior=prior,
            domain=domain,
            domain_provenance=domain_provenance,
            gates=gates,
        )
        digest = stable_hash(material)
        self = object.__new__(cls)
        for name, value in (
            ("family", mapping.family),
            ("evidence_domain", domain),
            ("domain_evidence_provenance", domain_provenance),
            ("a31_result_id", a31.result_id),
            ("a31_receipt_id", prior.receipt_id),
            ("a22_result_id", a31.a22_result.result_id),
            ("a22_report_id", a31.a22_result.report.report_id),
            ("a22_mapping_id", mapping.mapping_id),
            ("a2_result_id", a31.a22_result.a2_result.result_id),
            ("a2_manifest_id", a31.a22_result.a2_result.manifest.manifest_id),
            ("census_manifest_id", a31.a22_result.a2_result.census.manifest.manifest_id),
            (
                "coverage_manifest_id",
                a31.a22_result.a2_result.census.coverage_manifest.manifest_id,
            ),
            ("capture_id", a31.a22_result.a2_result.census.capture.capture_id),
            ("source_record_id", mapping.source_record_id),
            ("market_ticker", mapping.market_ticker),
            ("gates", gates),
            ("empirical_artifact_status", A31_EMPIRICAL_ARTIFACT_STATUS),
        ):
            object.__setattr__(self, name, value)
        object.__setattr__(self, "schema_version", A32_DOMAIN_RECEIPT_SCHEMA_VERSION)
        object.__setattr__(self, "receipt_id", digest)
        object.__setattr__(self, "content_hash", digest)
        object.__setattr__(self, "research_only", True)
        object.__setattr__(self, "production_influence", ZERO_INFLUENCE)
        return self

    def _validate_canonical_identity(self) -> None:
        if type(self) is not EvidenceDomainReceipt:
            raise EmpiricalResearchabilityError("A3.2 receipt concrete type is not canonical")
        if self.schema_version != A32_DOMAIN_RECEIPT_SCHEMA_VERSION:
            raise EmpiricalResearchabilityError("A3.2 receipt schema identity is invalid")
        if self.research_only is not True or self.production_influence != ZERO_INFLUENCE:
            raise EmpiricalResearchabilityError("A3.2 receipt authority boundary is invalid")
        if self.empirical_artifact_status != A31_EMPIRICAL_ARTIFACT_STATUS:
            raise EmpiricalResearchabilityError("A3.2 empirical-artifact posture is invalid")
        if self.evidence_domain is EvidenceDomain.UNASSIGNED and self.domain_evidence_provenance:
            raise EmpiricalResearchabilityError("unassigned A3.2 domain cannot carry domain proof")
        _validate_gate_resolutions(self.gates, self.evidence_domain)


@dataclass(frozen=True, slots=True, init=False)
class EvidenceResolutionResult:
    a31_result: ResearchabilityHardGateResult
    domain_receipts: tuple[EvidenceDomainReceipt, ...]
    empirical_artifact_status: str
    schema_version: str
    result_id: str
    content_hash: str
    research_only: bool
    production_influence: Decimal
    _a31_object_identity_seal: tuple[object, ...]

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("EvidenceResolutionResult is canonical-A3.2-issued only")

    @classmethod
    def _issue(
        cls,
        *,
        capability: object,
        a31: ResearchabilityHardGateResult,
        receipts: tuple[EvidenceDomainReceipt, ...],
    ) -> EvidenceResolutionResult:
        if capability is not _A32_ISSUANCE_CAPABILITY:
            raise EmpiricalResearchabilityError("A3.2 result issuance capability is invalid")
        _validate_a31_result(a31)
        _validate_receipt_conservation(a31, receipts)
        receipt_ids = tuple(receipt.receipt_id for receipt in receipts)
        material = (
            A32_RESULT_SCHEMA_VERSION,
            A32_POLICY_VERSION,
            A31_EMPIRICAL_ARTIFACT_STATUS,
            a31.result_id,
            a31.a22_result.result_id,
            receipt_ids,
            "RESEARCH_ONLY_NO_LIFECYCLE_ECONOMICS_OR_EXECUTION_AUTHORITY",
            "0",
        )
        digest = stable_hash(material)
        self = object.__new__(cls)
        object.__setattr__(self, "a31_result", a31)
        object.__setattr__(self, "domain_receipts", receipts)
        object.__setattr__(self, "empirical_artifact_status", A31_EMPIRICAL_ARTIFACT_STATUS)
        object.__setattr__(self, "schema_version", A32_RESULT_SCHEMA_VERSION)
        object.__setattr__(self, "result_id", digest)
        object.__setattr__(self, "content_hash", digest)
        object.__setattr__(self, "research_only", True)
        object.__setattr__(self, "production_influence", ZERO_INFLUENCE)
        object.__setattr__(self, "_a31_object_identity_seal", _a31_object_identity(a31))
        return self

    def _validate_canonical_identity(self) -> None:
        if type(self) is not EvidenceResolutionResult:
            raise EmpiricalResearchabilityError("A3.2 result concrete type is not canonical")
        if self.schema_version != A32_RESULT_SCHEMA_VERSION:
            raise EmpiricalResearchabilityError("A3.2 result schema identity is invalid")
        if self.research_only is not True or self.production_influence != ZERO_INFLUENCE:
            raise EmpiricalResearchabilityError("A3.2 result authority boundary is invalid")
        if self.empirical_artifact_status != A31_EMPIRICAL_ARTIFACT_STATUS:
            raise EmpiricalResearchabilityError("A3.2 empirical-artifact posture is invalid")
        if self._a31_object_identity_seal != _a31_object_identity(self.a31_result):
            raise EmpiricalResearchabilityError("A3.2 exact A3.1 object binding is invalid")
        _validate_a31_result(self.a31_result)
        _validate_receipt_conservation(self.a31_result, self.domain_receipts)
        receipt_ids = tuple(receipt.receipt_id for receipt in self.domain_receipts)
        expected = stable_hash(
            (
                A32_RESULT_SCHEMA_VERSION,
                A32_POLICY_VERSION,
                A31_EMPIRICAL_ARTIFACT_STATUS,
                self.a31_result.result_id,
                self.a31_result.a22_result.result_id,
                receipt_ids,
                "RESEARCH_ONLY_NO_LIFECYCLE_ECONOMICS_OR_EXECUTION_AUTHORITY",
                "0",
            )
        )
        if self.result_id != expected or self.content_hash != expected:
            raise EmpiricalResearchabilityError("A3.2 result content-addressed identity mismatch")


def build_evidence_resolution_result(
    a31_result: ResearchabilityHardGateResult,
) -> EvidenceResolutionResult:
    """Conserve canonical A3.1 mappings and resolve only positively proven A3.2 gates."""

    _validate_a31_result(a31_result)
    prior_by_mapping = _prior_receipts_by_mapping(a31_result)
    receipts = tuple(
        EvidenceDomainReceipt._issue(
            capability=_A32_ISSUANCE_CAPABILITY,
            a31=a31_result,
            mapping=mapping,
            prior=prior_by_mapping[mapping.mapping_id],
        )
        for mapping in sorted(a31_result.a22_result.mappings, key=lambda item: item.mapping_id)
    )
    return EvidenceResolutionResult._issue(
        capability=_A32_ISSUANCE_CAPABILITY,
        a31=a31_result,
        receipts=receipts,
    )


def _gate_resolutions(
    *,
    a31: ResearchabilityHardGateResult,
    mapping: ResearchFamilyMappingRecord,
    prior: ResearchabilityFamilyReceipt,
    domain: EvidenceDomain,
) -> tuple[GateResolution, ...]:
    decisions = {decision.gate: decision for decision in prior.gates}
    resolutions: list[GateResolution] = []
    for gate_id in HARD_GATE_ORDER:
        decision = decisions[gate_id]
        base_provenance = (
            f"A3.1_RESULT:{a31.result_id}",
            f"A3.1_RECEIPT:{prior.receipt_id}",
            f"A2.2_RESULT:{a31.a22_result.result_id}",
            f"A2.2_MAPPING:{mapping.mapping_id}",
            f"A2.1_RESULT:{a31.a22_result.a2_result.result_id}",
            f"A2.1_SOURCE_RECORD:{mapping.source_record_id}",
            f"A1_CAPTURE:{a31.a22_result.a2_result.census.capture.capture_id}",
        )
        if gate_id is ResearchabilityGate.G7_AUTHORITY_ISOLATION:
            if decision.state is not GateState.PASS:
                raise EmpiricalResearchabilityError(
                    "A3.2 cannot weaken A3.1 G7 authority isolation"
                )
            resolutions.append(
                GateResolution(
                    gate=gate_id,
                    prior_state=decision.state,
                    resolved_state=GateState.PASS,
                    evidence_provenance=(
                        *base_provenance,
                        "A3.2_STATIC_AUTHORITY_BOUNDARY:RESEARCH_ONLY_ZERO_INFLUENCE",
                    ),
                    proof_kind=EvidenceProofKind.STRUCTURAL,
                    missing_evidence=(),
                    blocker_evidence=(),
                )
            )
            continue
        if decision.state is not GateState.UNKNOWN:
            raise EmpiricalResearchabilityError(
                "A3.2 v1 expects canonical A3.1 G1-G6 to remain UNKNOWN before resolution"
            )
        resolutions.append(
            GateResolution(
                gate=gate_id,
                prior_state=decision.state,
                resolved_state=GateState.UNKNOWN,
                evidence_provenance=(
                    *base_provenance,
                    f"EVIDENCE_DOMAIN:{domain.value}",
                    f"CHECKPOINT:{A31_EMPIRICAL_ARTIFACT_STATUS}",
                ),
                proof_kind=None,
                missing_evidence=_MISSING_BY_GATE[gate_id],
                blocker_evidence=(),
            )
        )
    return tuple(resolutions)


def _prior_receipts_by_mapping(
    a31: ResearchabilityHardGateResult,
) -> dict[str, ResearchabilityFamilyReceipt]:
    prior: dict[str, ResearchabilityFamilyReceipt] = {}
    for receipt in a31.family_receipts:
        for mapping_id in receipt.a22_mapping_ids:
            if mapping_id in prior:
                raise EmpiricalResearchabilityError(
                    "A3.1 mapping appears in multiple family receipts"
                )
            prior[mapping_id] = receipt
    expected = {mapping.mapping_id for mapping in a31.a22_result.mappings}
    if set(prior) != expected:
        raise EmpiricalResearchabilityError(
            "A3.1 family receipts do not conserve exact A2.2 mappings"
        )
    return prior


def _validate_gate_resolutions(
    gates: tuple[GateResolution, ...],
    domain: EvidenceDomain,
) -> None:
    if tuple(resolution.gate for resolution in gates) != HARD_GATE_ORDER:
        raise EmpiricalResearchabilityError("A3.2 receipt must contain exactly G1-G7 once each")
    for resolution in gates:
        if (
            type(resolution.prior_state) is not GateState
            or type(resolution.resolved_state) is not GateState
        ):
            raise EmpiricalResearchabilityError("A3.2 gate state is not canonical")
        if resolution.resolved_state is GateState.BLOCKED and not resolution.blocker_evidence:
            raise EmpiricalResearchabilityError(
                "A3.2 BLOCKED requires positive canonical blocker evidence"
            )
        if resolution.resolved_state is GateState.PASS and resolution.proof_kind is None:
            raise EmpiricalResearchabilityError("A3.2 PASS requires positive canonical proof")
        if (
            resolution.gate is not ResearchabilityGate.G7_AUTHORITY_ISOLATION
            and domain is EvidenceDomain.UNASSIGNED
            and resolution.resolved_state is not GateState.UNKNOWN
        ):
            raise EmpiricalResearchabilityError("unassigned evidence domain cannot promote G1-G6")
        if resolution.gate is ResearchabilityGate.G7_AUTHORITY_ISOLATION:
            if (
                resolution.prior_state is not GateState.PASS
                or resolution.resolved_state is not GateState.PASS
            ):
                raise EmpiricalResearchabilityError("A3.2 cannot weaken G7 authority isolation")
            if resolution.proof_kind is not EvidenceProofKind.STRUCTURAL:
                raise EmpiricalResearchabilityError("A3.2 G7 proof must remain structural")


def _receipt_identity_material(
    *,
    a31: ResearchabilityHardGateResult,
    mapping: ResearchFamilyMappingRecord,
    prior: ResearchabilityFamilyReceipt,
    domain: EvidenceDomain,
    domain_provenance: tuple[str, ...],
    gates: tuple[GateResolution, ...],
) -> tuple[object, ...]:
    return (
        A32_DOMAIN_RECEIPT_SCHEMA_VERSION,
        A32_POLICY_VERSION,
        mapping.family.value,
        domain.value,
        domain_provenance,
        a31.result_id,
        prior.receipt_id,
        a31.a22_result.result_id,
        a31.a22_result.report.report_id,
        mapping.mapping_id,
        a31.a22_result.a2_result.result_id,
        a31.a22_result.a2_result.manifest.manifest_id,
        a31.a22_result.a2_result.census.manifest.manifest_id,
        a31.a22_result.a2_result.census.coverage_manifest.manifest_id,
        a31.a22_result.a2_result.census.capture.capture_id,
        mapping.source_record_id,
        mapping.market_ticker,
        tuple(
            (
                resolution.gate.value,
                resolution.prior_state.value,
                resolution.resolved_state.value,
                resolution.evidence_provenance,
                resolution.proof_kind.value if resolution.proof_kind is not None else None,
                resolution.missing_evidence,
                resolution.blocker_evidence,
            )
            for resolution in gates
        ),
        A31_EMPIRICAL_ARTIFACT_STATUS,
        "RESEARCH_ONLY",
        "0",
    )


def _validate_receipt_conservation(
    a31: ResearchabilityHardGateResult,
    receipts: tuple[EvidenceDomainReceipt, ...],
) -> None:
    expected_mappings = tuple(
        sorted(a31.a22_result.mappings, key=lambda item: item.mapping_id)
    )
    if len(receipts) != len(expected_mappings):
        raise EmpiricalResearchabilityError("A3.2 domain mapping silently dropped or added inputs")
    if tuple(receipt.a22_mapping_id for receipt in receipts) != tuple(
        mapping.mapping_id for mapping in expected_mappings
    ):
        raise EmpiricalResearchabilityError(
            "A3.2 domain mapping does not conserve exact A2.2 inputs"
        )
    prior_by_mapping = _prior_receipts_by_mapping(a31)
    for receipt, mapping in zip(receipts, expected_mappings, strict=True):
        receipt._validate_canonical_identity()
        prior = prior_by_mapping[mapping.mapping_id]
        if (
            receipt.family is not mapping.family
            or receipt.a31_result_id != a31.result_id
            or receipt.a31_receipt_id != prior.receipt_id
            or receipt.a22_result_id != a31.a22_result.result_id
            or receipt.a22_report_id != a31.a22_result.report.report_id
            or receipt.a2_result_id != a31.a22_result.a2_result.result_id
            or receipt.a2_manifest_id != a31.a22_result.a2_result.manifest.manifest_id
            or receipt.census_manifest_id != a31.a22_result.a2_result.census.manifest.manifest_id
            or receipt.coverage_manifest_id
            != a31.a22_result.a2_result.census.coverage_manifest.manifest_id
            or receipt.capture_id != a31.a22_result.a2_result.census.capture.capture_id
            or receipt.source_record_id != mapping.source_record_id
            or receipt.market_ticker != mapping.market_ticker
        ):
            raise EmpiricalResearchabilityError(
                "A3.2 receipt does not preserve exact upstream binding"
            )
        expected_hash = stable_hash(
            _receipt_identity_material(
                a31=a31,
                mapping=mapping,
                prior=prior,
                domain=receipt.evidence_domain,
                domain_provenance=receipt.domain_evidence_provenance,
                gates=receipt.gates,
            )
        )
        if receipt.receipt_id != expected_hash or receipt.content_hash != expected_hash:
            raise EmpiricalResearchabilityError("A3.2 receipt content-addressed identity mismatch")


def _validate_a31_result(a31: ResearchabilityHardGateResult) -> None:
    if type(a31) is not ResearchabilityHardGateResult:
        raise EmpiricalResearchabilityError("A3.1 result concrete type is not canonical")
    try:
        a31._validate_canonical_identity()
    except ResearchabilityHardGateError as exc:
        raise EmpiricalResearchabilityError(
            "A3.1/A2.2 evidence is foreign, tampered, or mismatched"
        ) from exc


def _a31_object_identity(a31: ResearchabilityHardGateResult) -> tuple[object, ...]:
    return (
        a31,
        a31.a22_result,
        a31.a22_result.a2_result,
        a31.a22_result.a2_result.census,
        a31.a22_result.a2_result.census.capture,
        a31.a22_result.a2_result.census.manifest,
        a31.a22_result.a2_result.census.coverage_manifest,
        a31.a22_result.report,
        *a31.family_receipts,
        *a31.a22_result.mappings,
    )
