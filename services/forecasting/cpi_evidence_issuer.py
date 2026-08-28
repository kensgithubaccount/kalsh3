"""Reviewed CPI-E1-P4 bridge from acquisition provenance to P2 replay evidence.

This module is the sole new production issuer allowed to consume P2's private
publication-evidence capability. It accepts only validated P4 acquisition evidence,
rebuilds the canonical P2 structural artifact from the exact acquired bytes, invokes
P3 internally, binds every identity, and only then issues and validates P2 evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import services.forecasting.cpi_pit_availability as pit
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
ZERO = Decimal("0")


class CPIEvidenceIssuanceError(ValueError):
    """Acquisition, parser, or P2 publication binding failed closed."""


@dataclass(frozen=True, slots=True)
class CPIAcquisitionBoundIssuance:
    """Validated components of one complete P4 acquisition-to-replay issuance."""

    acquisition_evidence: CPIBLSAcquisitionEvidence
    release_artifact: pit.CPIHistoricalReleaseArtifact
    parsed_timing: ParsedCPIPublicationTiming
    publication_evidence: pit.CPIActualPublicationEvidence
    availability: Availability
    timing_evidence_identity: str


def _artifact_from_acquisition(
    acquisition: CPIBLSAcquisitionEvidence,
) -> pit.CPIHistoricalReleaseArtifact:
    validate_cpi_bls_acquisition_evidence(acquisition)
    artifact = pit.CPIHistoricalReleaseArtifact(
        profile=acquisition.profile,
        source_locator=acquisition.source_locator,
        actual_bot_ingest_at=acquisition.acquired_at,
        raw_artifact=acquisition.raw_body,
    )
    pit.validate_cpi_release_artifact(artifact)
    bindings = (
        artifact.source_locator == acquisition.source_locator,
        artifact.raw_artifact == acquisition.raw_body,
        artifact.raw_artifact_sha256 == acquisition.raw_body_sha256,
        artifact.p1_authority_identity == acquisition.p1_authority_identity,
        artifact.p1_policy_identity == acquisition.p1_policy_identity,
        artifact.actual_bot_ingest_at == acquisition.acquired_at,
    )
    if not all(bindings):
        raise CPIEvidenceIssuanceError("P2 structural artifact is not bound to exact acquisition")
    return artifact


def _validate_parser_binding(
    parsed: ParsedCPIPublicationTiming,
    artifact: pit.CPIHistoricalReleaseArtifact,
    acquisition: CPIBLSAcquisitionEvidence,
) -> None:
    if type(parsed) is not ParsedCPIPublicationTiming:
        raise CPIEvidenceIssuanceError("P3 timing must have exact parser output type")
    bindings = (
        parsed.profile is artifact.profile is acquisition.profile,
        parsed.source_role is artifact.source_role is acquisition.source_role,
        parsed.source_locator == artifact.source_locator == acquisition.source_locator,
        parsed.source_artifact_id == artifact.artifact_id,
        parsed.raw_artifact_sha256 == artifact.raw_artifact_sha256 == acquisition.raw_body_sha256,
        parsed.p1_authority_identity
        == artifact.p1_authority_identity
        == acquisition.p1_authority_identity,
        parsed.p1_policy_identity == artifact.p1_policy_identity == acquisition.p1_policy_identity,
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


def issue_acquisition_bound_cpi_evidence(
    acquisition_evidence: CPIBLSAcquisitionEvidence,
) -> CPIAcquisitionBoundIssuance:
    """Issue canonical P2 evidence only from validated P4 acquisition + internal P3 parsing."""
    validate_cpi_bls_acquisition_evidence(acquisition_evidence)
    artifact = _artifact_from_acquisition(acquisition_evidence)
    parsed = parse_cpi_publication_timing(artifact)
    _validate_parser_binding(parsed, artifact, acquisition_evidence)
    timing_identity = _timing_evidence_identity(acquisition_evidence, artifact, parsed)
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
    return CPIAcquisitionBoundIssuance(
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
