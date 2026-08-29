"""Reviewed CPI bridges from source acquisition provenance to P2 replay evidence.

P4 automated HTTPS acquisition remains unchanged. CPI-E1-P5A adds a separate,
research-only manual-browser-attested lane for periods where BLS/Akamai blocks the
reviewed automated request. Manual provenance never masquerades as P4 transport.
This module remains the sole production issuer allowed to consume P2's private
publication-evidence capability.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import services.forecasting.cpi_pit_availability as pit
from services.forecasting.cpi_manual_acquisition import (
    CPIBLSManualAcquisitionEvidence,
    attest_and_import_manual_bls_cpi_release,
    validate_cpi_bls_manual_acquisition_evidence,
)
from services.forecasting.cpi_publication_timing import (
    PARSER_POLICY_VERSION,
    ParsedCPIPublicationTiming,
    parse_cpi_publication_timing,
)
from services.forecasting.cpi_source_acquisition import (
    CPIBLSAcquisitionEvidence,
    acquire_bls_cpi_release,
    validate_cpi_bls_acquisition_evidence,
)
from services.historical_replay.domain import Availability
from services.market_universe.domain import stable_hash

ISSUER_POLICY_VERSION = "cpi-e1-p4-acquisition-parser-p2-issuer-v1"
TIMING_BINDING_SCHEMA_VERSION = "cpi-e1-p4-acquisition-parser-binding-v1"
MANUAL_ISSUER_POLICY_VERSION = "cpi-e1-p5a-manual-acquisition-parser-p2-issuer-v1"
MANUAL_TIMING_BINDING_SCHEMA_VERSION = "cpi-e1-p5a-manual-parser-binding-v1"
ZERO = Decimal("0")


class CPIEvidenceIssuanceError(ValueError):
    """Acquisition, parser, or P2 publication binding failed closed."""


@dataclass(frozen=True, slots=True)
class CPIAcquisitionBoundIssuance:
    """Validated components of one complete P4 automated acquisition issuance."""

    acquisition_evidence: CPIBLSAcquisitionEvidence
    release_artifact: pit.CPIHistoricalReleaseArtifact
    parsed_timing: ParsedCPIPublicationTiming
    publication_evidence: pit.CPIActualPublicationEvidence
    availability: Availability
    timing_evidence_identity: str


@dataclass(frozen=True, slots=True)
class CPIManualAcquisitionBoundIssuance:
    """Validated components of one complete P5A manual-attested issuance."""

    acquisition_evidence: CPIBLSManualAcquisitionEvidence
    release_artifact: pit.CPIHistoricalReleaseArtifact
    parsed_timing: ParsedCPIPublicationTiming
    publication_evidence: pit.CPIActualPublicationEvidence
    availability: Availability
    timing_evidence_identity: str


def _artifact_from_exact_acquisition(
    *,
    profile: object,
    source_locator: str,
    ingest_at: object,
    raw_body: bytes,
    raw_body_sha256: str,
    p1_authority_identity: str,
    p1_policy_identity: str,
) -> pit.CPIHistoricalReleaseArtifact:
    artifact = pit.CPIHistoricalReleaseArtifact(
        profile=profile,  # type: ignore[arg-type]
        source_locator=source_locator,
        actual_bot_ingest_at=ingest_at,  # type: ignore[arg-type]
        raw_artifact=raw_body,
    )
    pit.validate_cpi_release_artifact(artifact)
    bindings = (
        artifact.source_locator == source_locator,
        artifact.raw_artifact == raw_body,
        artifact.raw_artifact_sha256 == raw_body_sha256,
        artifact.p1_authority_identity == p1_authority_identity,
        artifact.p1_policy_identity == p1_policy_identity,
        artifact.actual_bot_ingest_at == ingest_at,
    )
    if not all(bindings):
        raise CPIEvidenceIssuanceError("P2 structural artifact is not bound to exact acquisition")
    return artifact


def _artifact_from_acquisition(
    acquisition: CPIBLSAcquisitionEvidence,
) -> pit.CPIHistoricalReleaseArtifact:
    validate_cpi_bls_acquisition_evidence(acquisition)
    return _artifact_from_exact_acquisition(
        profile=acquisition.profile,
        source_locator=acquisition.source_locator,
        ingest_at=acquisition.acquired_at,
        raw_body=acquisition.raw_body,
        raw_body_sha256=acquisition.raw_body_sha256,
        p1_authority_identity=acquisition.p1_authority_identity,
        p1_policy_identity=acquisition.p1_policy_identity,
    )


def _artifact_from_manual_acquisition(
    acquisition: CPIBLSManualAcquisitionEvidence,
) -> pit.CPIHistoricalReleaseArtifact:
    validate_cpi_bls_manual_acquisition_evidence(acquisition)
    return _artifact_from_exact_acquisition(
        profile=acquisition.profile,
        source_locator=acquisition.source_locator,
        ingest_at=acquisition.imported_at,
        raw_body=acquisition.raw_body,
        raw_body_sha256=acquisition.raw_body_sha256,
        p1_authority_identity=acquisition.p1_authority_identity,
        p1_policy_identity=acquisition.p1_policy_identity,
    )


def _validate_parser_binding_values(
    parsed: ParsedCPIPublicationTiming,
    artifact: pit.CPIHistoricalReleaseArtifact,
    *,
    profile: object,
    source_role: object,
    source_locator: str,
    raw_body_sha256: str,
    p1_authority_identity: str,
    p1_policy_identity: str,
) -> None:
    if type(parsed) is not ParsedCPIPublicationTiming:
        raise CPIEvidenceIssuanceError("P3 timing must have exact parser output type")
    bindings = (
        parsed.profile is artifact.profile is profile,
        parsed.source_role is artifact.source_role is source_role,
        parsed.source_locator == artifact.source_locator == source_locator,
        parsed.source_artifact_id == artifact.artifact_id,
        parsed.raw_artifact_sha256 == artifact.raw_artifact_sha256 == raw_body_sha256,
        parsed.p1_authority_identity == artifact.p1_authority_identity == p1_authority_identity,
        parsed.p1_policy_identity == artifact.p1_policy_identity == p1_policy_identity,
        type(parsed.parser_policy_version) is str,
        parsed.parser_policy_version == PARSER_POLICY_VERSION,
        type(parsed.observation_identity) is str,
        bool(parsed.observation_identity),
        type(parsed.research_only) is bool and parsed.research_only is True,
        type(parsed.production_influence) is Decimal,
        parsed.production_influence == ZERO,
    )
    if not all(bindings):
        raise CPIEvidenceIssuanceError("P3 timing is not bound to the exact acquired artifact")


def _validate_parser_binding(
    parsed: ParsedCPIPublicationTiming,
    artifact: pit.CPIHistoricalReleaseArtifact,
    acquisition: CPIBLSAcquisitionEvidence,
) -> None:
    _validate_parser_binding_values(
        parsed,
        artifact,
        profile=acquisition.profile,
        source_role=acquisition.source_role,
        source_locator=acquisition.source_locator,
        raw_body_sha256=acquisition.raw_body_sha256,
        p1_authority_identity=acquisition.p1_authority_identity,
        p1_policy_identity=acquisition.p1_policy_identity,
    )


def _validate_manual_parser_binding(
    parsed: ParsedCPIPublicationTiming,
    artifact: pit.CPIHistoricalReleaseArtifact,
    acquisition: CPIBLSManualAcquisitionEvidence,
) -> None:
    _validate_parser_binding_values(
        parsed,
        artifact,
        profile=acquisition.profile,
        source_role=acquisition.source_role,
        source_locator=acquisition.source_locator,
        raw_body_sha256=acquisition.raw_body_sha256,
        p1_authority_identity=acquisition.p1_authority_identity,
        p1_policy_identity=acquisition.p1_policy_identity,
    )


def _timing_evidence_identity(
    acquisition: CPIBLSAcquisitionEvidence,
    artifact: pit.CPIHistoricalReleaseArtifact,
    parsed: ParsedCPIPublicationTiming,
) -> str:
    _validate_parser_binding(parsed, artifact, acquisition)
    return stable_hash(
        (
            ISSUER_POLICY_VERSION,
            TIMING_BINDING_SCHEMA_VERSION,
            acquisition.evidence_id,
            acquisition.transport_policy_identity,
            acquisition.source_locator,
            acquisition.raw_body_sha256,
            acquisition.acquired_at.isoformat(),
            artifact.artifact_id,
            artifact.raw_artifact_sha256,
            parsed.parser_policy_version,
            parsed.parser_schema_version,
            parsed.text_normalization_schema,
            parsed.observation_identity,
            parsed.publication_instant.isoformat(),
            acquisition.p1_authority_identity,
            acquisition.p1_policy_identity,
        )
    )


def _manual_timing_evidence_identity(
    acquisition: CPIBLSManualAcquisitionEvidence,
    artifact: pit.CPIHistoricalReleaseArtifact,
    parsed: ParsedCPIPublicationTiming,
) -> str:
    _validate_manual_parser_binding(parsed, artifact, acquisition)
    return stable_hash(
        (
            MANUAL_ISSUER_POLICY_VERSION,
            MANUAL_TIMING_BINDING_SCHEMA_VERSION,
            acquisition.evidence_id,
            acquisition.manual_policy_identity,
            acquisition.acquisition_mode,
            acquisition.operator_attestation,
            acquisition.source_locator,
            acquisition.raw_body_sha256,
            acquisition.imported_at.isoformat(),
            artifact.artifact_id,
            artifact.raw_artifact_sha256,
            parsed.parser_policy_version,
            parsed.parser_schema_version,
            parsed.text_normalization_schema,
            parsed.observation_identity,
            parsed.publication_instant.isoformat(),
            acquisition.p1_authority_identity,
            acquisition.p1_policy_identity,
        )
    )


def _validate_complete_bound_issuance(
    issuance: CPIAcquisitionBoundIssuance | CPIManualAcquisitionBoundIssuance,
    *,
    manual: bool,
) -> None:
    """Revalidate a public wrapper's complete transitive P4/P5A evidence chain."""
    if manual:
        if type(issuance) is not CPIManualAcquisitionBoundIssuance:
            raise CPIEvidenceIssuanceError("manual CPI issuance has wrong exact wrapper type")
        acquisition = issuance.acquisition_evidence
        validate_cpi_bls_manual_acquisition_evidence(acquisition)
        canonical_artifact = _artifact_from_manual_acquisition(acquisition)
        canonical_parsed = parse_cpi_publication_timing(canonical_artifact)
        _validate_manual_parser_binding(canonical_parsed, canonical_artifact, acquisition)
        canonical_timing_identity = _manual_timing_evidence_identity(
            acquisition, canonical_artifact, canonical_parsed
        )
    else:
        if type(issuance) is not CPIAcquisitionBoundIssuance:
            raise CPIEvidenceIssuanceError("automated CPI issuance has wrong exact wrapper type")
        automated_acquisition = issuance.acquisition_evidence
        validate_cpi_bls_acquisition_evidence(automated_acquisition)
        canonical_artifact = _artifact_from_acquisition(automated_acquisition)
        canonical_parsed = parse_cpi_publication_timing(canonical_artifact)
        _validate_parser_binding(canonical_parsed, canonical_artifact, automated_acquisition)
        canonical_timing_identity = _timing_evidence_identity(
            automated_acquisition, canonical_artifact, canonical_parsed
        )
    artifact = issuance.release_artifact
    publication = issuance.publication_evidence
    pit.validate_cpi_release_artifact(artifact)
    pit.validate_cpi_publication_evidence(publication)
    if artifact != canonical_artifact:
        raise CPIEvidenceIssuanceError("issuance artifact is not canonically bound to acquisition")
    if issuance.parsed_timing != canonical_parsed:
        raise CPIEvidenceIssuanceError("issuance timing is not canonically bound to acquisition")
    if issuance.timing_evidence_identity != canonical_timing_identity:
        raise CPIEvidenceIssuanceError("issuance timing identity is not canonical")
    if publication.timing_evidence_identity != canonical_timing_identity:
        raise CPIEvidenceIssuanceError("publication timing identity is not canonical")
    publication_bindings = (
        publication.profile is artifact.profile,
        publication.source_role is artifact.source_role,
        publication.source_locator == artifact.source_locator,
        publication.source_artifact_id == artifact.artifact_id,
        publication.raw_artifact_sha256 == artifact.raw_artifact_sha256,
        publication.p1_authority_identity == artifact.p1_authority_identity,
        publication.p1_policy_identity == artifact.p1_policy_identity,
    )
    if not all(publication_bindings):
        raise CPIEvidenceIssuanceError("publication evidence is not bound to exact artifact")
    expected_availability = pit.build_cpi_reconstructed_availability(
        artifact, publication_evidence=publication
    )
    if issuance.availability != expected_availability:
        raise CPIEvidenceIssuanceError("issuance Availability is not canonically derived")
    if (
        type(artifact.research_only) is not bool
        or artifact.research_only is not True
        or artifact.production_influence != ZERO
        or type(publication.research_only) is not bool
        or publication.research_only is not True
        or publication.production_influence != ZERO
    ):
        raise CPIEvidenceIssuanceError("CPI issuance safety flags are invalid")


def validate_acquisition_bound_cpi_issuance(
    issuance: CPIAcquisitionBoundIssuance,
) -> None:
    """Validate every constituent and binding of a P4 automated issuance wrapper."""
    _validate_complete_bound_issuance(issuance, manual=False)


def validate_manual_acquisition_bound_cpi_issuance(
    issuance: CPIManualAcquisitionBoundIssuance,
) -> None:
    """Validate every constituent and binding of a P5A manual issuance wrapper."""
    _validate_complete_bound_issuance(issuance, manual=True)


def _issue_p2_from_parsed(
    *,
    artifact: pit.CPIHistoricalReleaseArtifact,
    parsed: ParsedCPIPublicationTiming,
    timing_identity: str,
) -> tuple[pit.CPIActualPublicationEvidence, Availability]:
    publication = pit._issue_actual_cpi_publication_evidence(
        artifact=artifact,
        source_publish_at=parsed.publication_instant,
        timing_semantics=pit.CPIPublicationTimingSemantics.ACTUAL_RELEASE_OR_EMBARGO,
        timing_evidence_identity=timing_identity,
        _capability=pit._PUBLICATION_AUTHORITY_CAPABILITY,
    )
    pit.validate_cpi_publication_evidence(publication)
    availability = pit.build_cpi_reconstructed_availability(
        artifact,
        publication_evidence=publication,
    )
    return publication, availability


def issue_acquisition_bound_cpi_evidence(
    acquisition_evidence: CPIBLSAcquisitionEvidence,
) -> CPIAcquisitionBoundIssuance:
    """Issue canonical P2 evidence only from validated P4 acquisition + internal P3 parsing."""
    validate_cpi_bls_acquisition_evidence(acquisition_evidence)
    artifact = _artifact_from_acquisition(acquisition_evidence)
    parsed = parse_cpi_publication_timing(artifact)
    _validate_parser_binding(parsed, artifact, acquisition_evidence)
    timing_identity = _timing_evidence_identity(acquisition_evidence, artifact, parsed)
    publication, availability = _issue_p2_from_parsed(
        artifact=artifact,
        parsed=parsed,
        timing_identity=timing_identity,
    )
    return CPIAcquisitionBoundIssuance(
        acquisition_evidence=acquisition_evidence,
        release_artifact=artifact,
        parsed_timing=parsed,
        publication_evidence=publication,
        availability=availability,
        timing_evidence_identity=timing_identity,
    )


def issue_manual_acquisition_bound_cpi_evidence(
    acquisition_evidence: CPIBLSManualAcquisitionEvidence,
) -> CPIManualAcquisitionBoundIssuance:
    """Issue P2 research evidence from a validated, distinctly manual P5A import."""
    validate_cpi_bls_manual_acquisition_evidence(acquisition_evidence)
    artifact = _artifact_from_manual_acquisition(acquisition_evidence)
    parsed = parse_cpi_publication_timing(artifact)
    _validate_manual_parser_binding(parsed, artifact, acquisition_evidence)
    timing_identity = _manual_timing_evidence_identity(acquisition_evidence, artifact, parsed)
    publication, availability = _issue_p2_from_parsed(
        artifact=artifact,
        parsed=parsed,
        timing_identity=timing_identity,
    )
    return CPIManualAcquisitionBoundIssuance(
        acquisition_evidence=acquisition_evidence,
        release_artifact=artifact,
        parsed_timing=parsed,
        publication_evidence=publication,
        availability=availability,
        timing_evidence_identity=timing_identity,
    )


def acquire_and_issue_cpi_evidence(source_locator: str) -> CPIAcquisitionBoundIssuance:
    """Perform the complete reviewed live P4 chain for one exact archived CPI locator."""
    return issue_acquisition_bound_cpi_evidence(acquire_bls_cpi_release(source_locator))


def attest_import_and_issue_manual_cpi_evidence(
    source_locator: str,
    file_path: str | Path,
    *,
    operator_attestation: str,
) -> CPIManualAcquisitionBoundIssuance:
    """Perform the complete P5A human-attested local-file research chain."""
    evidence = attest_and_import_manual_bls_cpi_release(
        source_locator,
        file_path,
        operator_attestation=operator_attestation,
    )
    return issue_manual_acquisition_bound_cpi_evidence(evidence)
