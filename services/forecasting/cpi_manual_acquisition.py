"""Research-only manual BLS CPI acquisition provenance for CPI-E1-P5A.

This lane exists because BLS/Akamai currently rejects the reviewed P4 automated
request from tested environments. It does not impersonate P4 transport and does
not prove HTTP provenance. A human operator explicitly attests that one local file
was saved from the exact P1-authorized BLS archive locator in a normal browser.
The importer then binds the exact bytes, P1 authority, and importer-observed UTC
instant under a permanently distinct MANUAL_BROWSER_ATTESTED policy.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from services.forecasting.cpi_source_authority import (
    BLS_HTTPS_ORIGIN,
    CPISourceInterface,
    CPISourceProfile,
    CPISourceRole,
    ReviewedCPISourceAuthority,
    resolve_cpi_source_authority,
    validate_cpi_source_authority,
)
from services.forecasting.cpi_source_authority import (
    POLICY_IDENTITY as CANONICAL_P1_POLICY_IDENTITY,
)
from services.market_universe.domain import stable_hash

MANUAL_POLICY_VERSION = "cpi-e1-p5a-manual-browser-attested-v1"
MANUAL_SCHEMA_VERSION = "cpi-e1-p5a-manual-acquisition-evidence-v1"
ACQUISITION_MODE = "MANUAL_BROWSER_ATTESTED"
OPERATOR_ATTESTATION = (
    "I attest that this exact file was saved from the exact official BLS locator "
    "in a normal browser."
)
MAX_MANUAL_ARTIFACT_BYTES = 8_000_000
ZERO = Decimal("0")
_PROFILE = CPISourceProfile.CPI_U_US_CITY_AVERAGE_ALL_ITEMS_SA_MOM_INITIAL_RELEASE
_ROLE = CPISourceRole.HISTORICAL_INITIAL_RELEASE_DOCUMENT
_MANUAL_EVIDENCE_CAPABILITY = object()
_ISSUED_MANUAL_FINGERPRINTS: dict[int, str] = {}

MANUAL_POLICY_IDENTITY = stable_hash(
    (
        MANUAL_POLICY_VERSION,
        ACQUISITION_MODE,
        BLS_HTTPS_ORIGIN,
        CANONICAL_P1_POLICY_IDENTITY,
        MAX_MANUAL_ARTIFACT_BYTES,
        OPERATOR_ATTESTATION,
        "HUMAN_ATTESTED_NOT_CRYPTOGRAPHIC_HTTP_PROVENANCE",
        "IMPORT_TIME_IS_ISSUER_OBSERVED_NOT_CALLER_SUPPLIED",
        "RESEARCH_ONLY",
        str(ZERO),
    )
)


class CPIManualAcquisitionError(ValueError):
    """Manual BLS import provenance or immutable evidence failed closed."""


def _utc_now() -> datetime:
    """Narrow clock seam; production callers cannot supply the import timestamp."""
    return datetime.now(UTC)


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise CPIManualAcquisitionError(f"{field_name} must be an exact aware datetime")
    return value.astimezone(UTC)


def _reviewed_authority(locator: str) -> ReviewedCPISourceAuthority:
    if type(locator) is not str or not locator or locator != locator.strip():
        raise CPIManualAcquisitionError("exact BLS source locator is required")
    authority = resolve_cpi_source_authority(profile=_PROFILE, role=_ROLE, locator=locator)
    validate_cpi_source_authority(authority)
    if authority.source_interface is not CPISourceInterface.BLS_ARCHIVED_CPI_NEWS_RELEASE_HTML:
        raise CPIManualAcquisitionError("only reviewed archived CPI HTML may be imported")
    if authority.policy_identity != CANONICAL_P1_POLICY_IDENTITY:
        raise CPIManualAcquisitionError("canonical P1 source policy identity moved")
    return authority


def _read_exact_local_file(file_path: str | Path) -> bytes:
    path = Path(file_path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise CPIManualAcquisitionError(f"manual BLS artifact cannot be opened: {exc}") from exc
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise CPIManualAcquisitionError("manual BLS artifact must be a regular file")
        if metadata.st_size <= 0:
            raise CPIManualAcquisitionError("manual BLS artifact must be non-empty")
        if metadata.st_size > MAX_MANUAL_ARTIFACT_BYTES:
            raise CPIManualAcquisitionError("manual BLS artifact exceeded bounded size")
        chunks: list[bytes] = []
        remaining = MAX_MANUAL_ARTIFACT_BYTES + 1
        while remaining > 0:
            chunk = os.read(fd, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        body = b"".join(chunks)
    finally:
        os.close(fd)
    if not body:
        raise CPIManualAcquisitionError("manual BLS artifact must be non-empty")
    if len(body) > MAX_MANUAL_ARTIFACT_BYTES:
        raise CPIManualAcquisitionError("manual BLS artifact exceeded bounded size")
    return body


def _manual_digest_values(values: dict[str, object]) -> str:
    profile = values["profile"]
    role = values["source_role"]
    interface = values["source_interface"]
    imported_at = values["imported_at"]
    if type(profile) is not CPISourceProfile:
        raise CPIManualAcquisitionError("manual acquisition profile type is invalid")
    if type(role) is not CPISourceRole:
        raise CPIManualAcquisitionError("manual acquisition role type is invalid")
    if type(interface) is not CPISourceInterface:
        raise CPIManualAcquisitionError("manual acquisition interface type is invalid")
    if type(imported_at) is not datetime:
        raise CPIManualAcquisitionError("manual acquisition import timestamp type is invalid")
    return stable_hash(
        (
            MANUAL_SCHEMA_VERSION,
            MANUAL_POLICY_IDENTITY,
            ACQUISITION_MODE,
            profile.value,
            role.value,
            interface.value,
            values["source_locator"],
            BLS_HTTPS_ORIGIN,
            values["raw_body_sha256"],
            values["byte_count"],
            imported_at.isoformat(),
            values["operator_attestation"],
            values["p1_authority_identity"],
            values["p1_policy_identity"],
            True,
            str(ZERO),
        )
    )


@dataclass(frozen=True, slots=True, init=False)
class CPIBLSManualAcquisitionEvidence:
    """Issuer-controlled evidence for one explicitly human-attested BLS file import."""

    profile: CPISourceProfile
    source_role: CPISourceRole
    source_interface: CPISourceInterface
    source_locator: str
    reviewed_origin: str
    raw_body: bytes
    raw_body_sha256: str
    byte_count: int
    imported_at: datetime
    acquisition_mode: str
    operator_attestation: str
    manual_policy_identity: str
    p1_authority_identity: str
    p1_policy_identity: str
    schema_version: str
    evidence_id: str
    content_hash: str
    research_only: bool
    production_influence: Decimal

    def __init__(
        self,
        *,
        source_locator: str,
        raw_body: bytes,
        imported_at: datetime,
        authority: ReviewedCPISourceAuthority,
        operator_attestation: str,
        _capability: object | None = None,
    ) -> None:
        if _capability is not _MANUAL_EVIDENCE_CAPABILITY:
            raise CPIManualAcquisitionError(
                "manual BLS acquisition evidence requires importer capability"
            )
        validate_cpi_source_authority(authority)
        canonical = _reviewed_authority(source_locator)
        if authority is not canonical:
            raise CPIManualAcquisitionError("caller-stamped P1 source authority is forbidden")
        if type(raw_body) is not bytes or not raw_body:
            raise CPIManualAcquisitionError("exact non-empty manual BLS bytes are required")
        if len(raw_body) > MAX_MANUAL_ARTIFACT_BYTES:
            raise CPIManualAcquisitionError("manual BLS artifact exceeded bounded size")
        if type(operator_attestation) is not str or operator_attestation != OPERATOR_ATTESTATION:
            raise CPIManualAcquisitionError("exact manual-browser operator attestation is required")
        imported = _aware_utc(imported_at, "manual BLS import timestamp")
        raw_hash = sha256(raw_body).hexdigest()
        values: dict[str, object] = {
            "profile": canonical.profile,
            "source_role": canonical.source_role,
            "source_interface": canonical.source_interface,
            "source_locator": source_locator,
            "reviewed_origin": BLS_HTTPS_ORIGIN,
            "raw_body": raw_body,
            "raw_body_sha256": raw_hash,
            "byte_count": len(raw_body),
            "imported_at": imported,
            "acquisition_mode": ACQUISITION_MODE,
            "operator_attestation": operator_attestation,
            "manual_policy_identity": MANUAL_POLICY_IDENTITY,
            "p1_authority_identity": canonical.authority_identity,
            "p1_policy_identity": canonical.policy_identity,
            "schema_version": MANUAL_SCHEMA_VERSION,
            "research_only": True,
            "production_influence": ZERO,
        }
        digest = _manual_digest_values(values)
        values["evidence_id"] = digest
        values["content_hash"] = digest
        for name, value in values.items():
            object.__setattr__(self, name, value)
        _ISSUED_MANUAL_FINGERPRINTS[id(self)] = digest


def _manual_digest(evidence: CPIBLSManualAcquisitionEvidence) -> str:
    return _manual_digest_values(
        {
            "profile": evidence.profile,
            "source_role": evidence.source_role,
            "source_interface": evidence.source_interface,
            "source_locator": evidence.source_locator,
            "raw_body_sha256": evidence.raw_body_sha256,
            "byte_count": evidence.byte_count,
            "imported_at": evidence.imported_at,
            "operator_attestation": evidence.operator_attestation,
            "p1_authority_identity": evidence.p1_authority_identity,
            "p1_policy_identity": evidence.p1_policy_identity,
        }
    )


def validate_cpi_bls_manual_acquisition_evidence(
    evidence: CPIBLSManualAcquisitionEvidence,
) -> None:
    """Revalidate manual provenance without upgrading it to automated HTTP proof."""
    if type(evidence) is not CPIBLSManualAcquisitionEvidence:
        raise CPIManualAcquisitionError("manual CPI acquisition evidence must have exact issued type")
    authority = _reviewed_authority(evidence.source_locator)
    raw_hash = sha256(evidence.raw_body).hexdigest() if type(evidence.raw_body) is bytes else None
    imported = _aware_utc(evidence.imported_at, "manual BLS import timestamp")
    expected = _manual_digest(evidence)
    exact = (
        type(evidence.profile) is CPISourceProfile and evidence.profile is _PROFILE,
        type(evidence.source_role) is CPISourceRole and evidence.source_role is _ROLE,
        type(evidence.source_interface) is CPISourceInterface
        and evidence.source_interface is CPISourceInterface.BLS_ARCHIVED_CPI_NEWS_RELEASE_HTML,
        type(evidence.reviewed_origin) is str and evidence.reviewed_origin == BLS_HTTPS_ORIGIN,
        type(evidence.raw_body) is bytes and bool(evidence.raw_body),
        len(evidence.raw_body) <= MAX_MANUAL_ARTIFACT_BYTES,
        type(evidence.raw_body_sha256) is str and evidence.raw_body_sha256 == raw_hash,
        type(evidence.byte_count) is int
        and not isinstance(evidence.byte_count, bool)
        and evidence.byte_count == len(evidence.raw_body),
        evidence.imported_at == imported and evidence.imported_at.tzinfo is UTC,
        type(evidence.acquisition_mode) is str and evidence.acquisition_mode == ACQUISITION_MODE,
        type(evidence.operator_attestation) is str
        and evidence.operator_attestation == OPERATOR_ATTESTATION,
        type(evidence.manual_policy_identity) is str
        and evidence.manual_policy_identity == MANUAL_POLICY_IDENTITY,
        type(evidence.p1_authority_identity) is str
        and evidence.p1_authority_identity == authority.authority_identity,
        type(evidence.p1_policy_identity) is str
        and evidence.p1_policy_identity == authority.policy_identity == CANONICAL_P1_POLICY_IDENTITY,
        type(evidence.schema_version) is str and evidence.schema_version == MANUAL_SCHEMA_VERSION,
        type(evidence.evidence_id) is str and evidence.evidence_id == expected,
        type(evidence.content_hash) is str and evidence.content_hash == expected,
        type(evidence.research_only) is bool and evidence.research_only is True,
        type(evidence.production_influence) is Decimal and evidence.production_influence == ZERO,
    )
    if not all(exact):
        raise CPIManualAcquisitionError("manual CPI acquisition evidence failed revalidation")
    if _ISSUED_MANUAL_FINGERPRINTS.get(id(evidence)) != expected:
        raise CPIManualAcquisitionError("unissued, reconstructed, or mutated manual acquisition")


def attest_and_import_manual_bls_cpi_release(
    source_locator: str,
    file_path: str | Path,
    *,
    operator_attestation: str,
) -> CPIBLSManualAcquisitionEvidence:
    """Import exact local bytes under explicit human-attested browser provenance."""
    authority = _reviewed_authority(source_locator)
    if type(operator_attestation) is not str or operator_attestation != OPERATOR_ATTESTATION:
        raise CPIManualAcquisitionError("exact manual-browser operator attestation is required")
    body = _read_exact_local_file(file_path)
    evidence = CPIBLSManualAcquisitionEvidence(
        source_locator=source_locator,
        raw_body=body,
        imported_at=_utc_now(),
        authority=authority,
        operator_attestation=operator_attestation,
        _capability=_MANUAL_EVIDENCE_CAPABILITY,
    )
    validate_cpi_bls_manual_acquisition_evidence(evidence)
    return evidence
