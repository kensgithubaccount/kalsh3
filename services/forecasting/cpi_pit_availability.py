"""Conservative historical CPI point-in-time availability policy for CPI-E1-P2.

Pure/offline, research-only policy. Positive replay requires an exact P1-authorized
archived BLS release artifact plus separately capability-issued proof of an exact
ACTUAL release/publication/embargo instant. Caller timestamps and scheduled timing
cannot mint positive historical authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from zoneinfo import ZoneInfo

from services.forecasting.cpi_source_authority import (
    POLICY_IDENTITY as CANONICAL_P1_POLICY_IDENTITY,
)
from services.forecasting.cpi_source_authority import (
    CPISourceProfile,
    CPISourceRole,
    ReviewedCPISourceAuthority,
    resolve_cpi_source_authority,
)
from services.historical_replay.domain import Availability, AvailabilityBasis, AvailabilityQuality
from services.market_universe.domain import stable_hash

POLICY_VERSION = "cpi-e1-p2-conservative-pit-policy-v1"
P1_POLICY_IDENTITY = "fea29def84dcfc71f1ce86f268a25f038d02b8482a220e219fe88a2cea2bc3f1"
TIMEZONE_NAME = "America/New_York"
NEW_YORK = ZoneInfo(TIMEZONE_NAME)
ZERO = Decimal("0")
_ARTIFACT_SCHEMA = "cpi-e1-p2-historical-release-artifact-v1"
_PUBLICATION_SCHEMA = "cpi-e1-p2-publication-timing-evidence-v1"
_PUBLICATION_AUTHORITY_CAPABILITY = object()
_ISSUED_PUBLICATION_FINGERPRINTS: dict[int, str] = {}


class CPIPITAvailabilityError(ValueError):
    """CPI PIT policy invariant or authority binding failed."""


class CPIPublicationTimingSemantics(StrEnum):
    ACTUAL_RELEASE_OR_EMBARGO = "ACTUAL_RELEASE_OR_EMBARGO"
    SCHEDULED_RELEASE = "SCHEDULED_RELEASE"


class CPIArtifactVintage(StrEnum):
    INITIAL_RELEASE = "INITIAL_RELEASE"


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise CPIPITAvailabilityError(f"{field_name} must be an exact aware datetime")
    return value.astimezone(UTC)


def _exact_new_york(value: datetime, field_name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise CPIPITAvailabilityError(f"{field_name} must be an exact aware datetime")
    zone = value.tzinfo
    if type(zone) is not ZoneInfo or zone.key != TIMEZONE_NAME:
        raise CPIPITAvailabilityError(
            f"{field_name} must use canonical {TIMEZONE_NAME} zoneinfo semantics"
        )
    return value


def _p1_authority(profile: CPISourceProfile, locator: str) -> ReviewedCPISourceAuthority:
    if CANONICAL_P1_POLICY_IDENTITY != P1_POLICY_IDENTITY:
        raise CPIPITAvailabilityError("canonical P1 policy identity moved")
    return resolve_cpi_source_authority(
        profile=profile,
        role=CPISourceRole.HISTORICAL_INITIAL_RELEASE_DOCUMENT,
        locator=locator,
    )


def _artifact_digest(
    profile: CPISourceProfile,
    locator: str,
    raw_hash: str,
    authority_identity: str,
) -> str:
    return stable_hash(
        (
            POLICY_VERSION,
            _ARTIFACT_SCHEMA,
            profile.value,
            CPISourceRole.HISTORICAL_INITIAL_RELEASE_DOCUMENT.value,
            locator,
            raw_hash,
            authority_identity,
            P1_POLICY_IDENTITY,
            CPIArtifactVintage.INITIAL_RELEASE.value,
        )
    )


@dataclass(frozen=True, slots=True)
class CPIHistoricalReleaseArtifact:
    """Exact archived BLS release bytes; structural only, never PIT authority alone."""

    profile: CPISourceProfile
    source_locator: str
    actual_bot_ingest_at: datetime
    raw_artifact: bytes
    source_role: CPISourceRole = field(
        init=False, default=CPISourceRole.HISTORICAL_INITIAL_RELEASE_DOCUMENT
    )
    vintage: CPIArtifactVintage = field(init=False, default=CPIArtifactVintage.INITIAL_RELEASE)
    p1_authority_identity: str = field(init=False)
    p1_policy_identity: str = field(init=False)
    raw_artifact_sha256: str = field(init=False)
    artifact_id: str = field(init=False)
    content_hash: str = field(init=False)
    schema_version: str = field(init=False, default=_ARTIFACT_SCHEMA)
    research_only: bool = field(init=False, default=True)
    production_influence: Decimal = field(init=False, default=ZERO)

    def __post_init__(self) -> None:
        if type(self.profile) is not CPISourceProfile:
            raise CPIPITAvailabilityError("CPI source profile must have exact enum identity")
        if type(self.source_locator) is not str or not self.source_locator:
            raise CPIPITAvailabilityError("exact CPI source locator is required")
        if type(self.raw_artifact) is not bytes or not self.raw_artifact:
            raise CPIPITAvailabilityError("exact raw archived CPI release bytes are required")
        authority = _p1_authority(self.profile, self.source_locator)
        ingest = _aware_utc(self.actual_bot_ingest_at, "actual bot ingest time")
        raw_hash = sha256(self.raw_artifact).hexdigest()
        authority_identity = authority.authority_identity
        policy_identity = authority.policy_identity
        if policy_identity != P1_POLICY_IDENTITY:
            raise CPIPITAvailabilityError("artifact is not bound to canonical P1 policy")
        digest = _artifact_digest(
            self.profile,
            self.source_locator,
            raw_hash,
            authority_identity,
        )
        object.__setattr__(self, "actual_bot_ingest_at", ingest)
        object.__setattr__(self, "p1_authority_identity", authority_identity)
        object.__setattr__(self, "p1_policy_identity", policy_identity)
        object.__setattr__(self, "raw_artifact_sha256", raw_hash)
        object.__setattr__(self, "artifact_id", digest)
        object.__setattr__(self, "content_hash", digest)


def validate_cpi_release_artifact(artifact: CPIHistoricalReleaseArtifact) -> None:
    if type(artifact) is not CPIHistoricalReleaseArtifact:
        raise CPIPITAvailabilityError("CPI release artifact must have exact runtime type")
    if type(artifact.profile) is not CPISourceProfile:
        raise CPIPITAvailabilityError("CPI source profile must have exact enum identity")
    if artifact.source_role is not CPISourceRole.HISTORICAL_INITIAL_RELEASE_DOCUMENT:
        raise CPIPITAvailabilityError("CPI release artifact has wrong P1 source role")
    if type(artifact.vintage) is not CPIArtifactVintage:
        raise CPIPITAvailabilityError("CPI release artifact vintage type is invalid")
    if artifact.vintage is not CPIArtifactVintage.INITIAL_RELEASE:
        raise CPIPITAvailabilityError("CPI release artifact must remain initial-release evidence")
    if type(artifact.raw_artifact) is not bytes or not artifact.raw_artifact:
        raise CPIPITAvailabilityError("exact raw archived CPI release bytes are required")
    if type(artifact.actual_bot_ingest_at) is not datetime:
        raise CPIPITAvailabilityError("actual bot ingest time has wrong type")
    if artifact.actual_bot_ingest_at.tzinfo is not UTC:
        raise CPIPITAvailabilityError("actual bot ingest time failed UTC normalization")
    authority = _p1_authority(artifact.profile, artifact.source_locator)
    authority_identity = authority.authority_identity
    policy_identity = authority.policy_identity
    raw_hash = sha256(artifact.raw_artifact).hexdigest()
    expected = _artifact_digest(
        artifact.profile,
        artifact.source_locator,
        raw_hash,
        authority_identity,
    )
    exact = (
        artifact.p1_authority_identity == authority_identity,
        artifact.p1_policy_identity == policy_identity == P1_POLICY_IDENTITY,
        artifact.raw_artifact_sha256 == raw_hash,
        artifact.artifact_id == expected,
        artifact.content_hash == expected,
        artifact.schema_version == _ARTIFACT_SCHEMA,
        type(artifact.research_only) is bool and artifact.research_only is True,
        type(artifact.production_influence) is Decimal,
        artifact.production_influence == ZERO,
    )
    if not all(exact):
        raise CPIPITAvailabilityError("CPI release artifact failed canonical revalidation")


@dataclass(frozen=True, slots=True, init=False)
class CPIActualPublicationEvidence:
    """Issuer-controlled proof of one exact actual BLS publication/embargo instant."""

    profile: CPISourceProfile
    source_role: CPISourceRole
    source_locator: str
    source_artifact_id: str
    raw_artifact_sha256: str
    p1_authority_identity: str
    p1_policy_identity: str
    timing_semantics: CPIPublicationTimingSemantics
    source_publish_at: datetime
    timing_evidence_identity: str
    schema_version: str
    evidence_id: str
    content_hash: str
    research_only: bool
    production_influence: Decimal

    def __init__(
        self,
        *,
        artifact: CPIHistoricalReleaseArtifact,
        source_publish_at: datetime,
        timing_semantics: CPIPublicationTimingSemantics,
        timing_evidence_identity: str,
        _capability: object | None = None,
    ) -> None:
        if _capability is not _PUBLICATION_AUTHORITY_CAPABILITY:
            raise CPIPITAvailabilityError(
                "publication evidence requires reviewed issuer capability"
            )
        validate_cpi_release_artifact(artifact)
        if type(timing_semantics) is not CPIPublicationTimingSemantics:
            raise CPIPITAvailabilityError("CPI timing semantics must have exact enum identity")
        if timing_semantics is not CPIPublicationTimingSemantics.ACTUAL_RELEASE_OR_EMBARGO:
            raise CPIPITAvailabilityError("scheduled CPI timing cannot mint actual-release proof")
        published = _exact_new_york(source_publish_at, "source publication time")
        if type(timing_evidence_identity) is not str or not timing_evidence_identity.strip():
            raise CPIPITAvailabilityError("independent CPI timing evidence identity is required")
        if timing_evidence_identity != timing_evidence_identity.strip():
            raise CPIPITAvailabilityError("CPI timing evidence identity must be exact")
        values: dict[str, object] = {
            "profile": artifact.profile,
            "source_role": artifact.source_role,
            "source_locator": artifact.source_locator,
            "source_artifact_id": artifact.artifact_id,
            "raw_artifact_sha256": artifact.raw_artifact_sha256,
            "p1_authority_identity": artifact.p1_authority_identity,
            "p1_policy_identity": artifact.p1_policy_identity,
            "timing_semantics": timing_semantics,
            "source_publish_at": published,
            "timing_evidence_identity": timing_evidence_identity,
            "schema_version": _PUBLICATION_SCHEMA,
            "research_only": True,
            "production_influence": ZERO,
        }
        digest = _publication_digest_values(values)
        values["evidence_id"] = digest
        values["content_hash"] = digest
        for name, value in values.items():
            object.__setattr__(self, name, value)
        _ISSUED_PUBLICATION_FINGERPRINTS[id(self)] = digest


def _publication_digest_values(values: dict[str, object]) -> str:
    profile = values["profile"]
    role = values["source_role"]
    semantics = values["timing_semantics"]
    published = values["source_publish_at"]
    if type(profile) is not CPISourceProfile:
        raise CPIPITAvailabilityError("publication digest profile type is invalid")
    if type(role) is not CPISourceRole:
        raise CPIPITAvailabilityError("publication digest role type is invalid")
    if type(semantics) is not CPIPublicationTimingSemantics:
        raise CPIPITAvailabilityError("publication digest timing semantics type is invalid")
    if type(published) is not datetime:
        raise CPIPITAvailabilityError("publication digest timestamp type is invalid")
    return stable_hash(
        (
            POLICY_VERSION,
            _PUBLICATION_SCHEMA,
            profile.value,
            role.value,
            values["source_locator"],
            values["source_artifact_id"],
            values["raw_artifact_sha256"],
            values["p1_authority_identity"],
            values["p1_policy_identity"],
            semantics.value,
            published.isoformat(),
            values["timing_evidence_identity"],
            True,
            str(ZERO),
        )
    )


def _publication_digest(evidence: CPIActualPublicationEvidence) -> str:
    return _publication_digest_values(
        {
            "profile": evidence.profile,
            "source_role": evidence.source_role,
            "source_locator": evidence.source_locator,
            "source_artifact_id": evidence.source_artifact_id,
            "raw_artifact_sha256": evidence.raw_artifact_sha256,
            "p1_authority_identity": evidence.p1_authority_identity,
            "p1_policy_identity": evidence.p1_policy_identity,
            "timing_semantics": evidence.timing_semantics,
            "source_publish_at": evidence.source_publish_at,
            "timing_evidence_identity": evidence.timing_evidence_identity,
        }
    )


def validate_cpi_publication_evidence(evidence: CPIActualPublicationEvidence) -> None:
    if type(evidence) is not CPIActualPublicationEvidence:
        raise CPIPITAvailabilityError("CPI publication evidence must have exact issued type")
    if type(evidence.profile) is not CPISourceProfile:
        raise CPIPITAvailabilityError("CPI publication profile must have exact enum identity")
    if evidence.source_role is not CPISourceRole.HISTORICAL_INITIAL_RELEASE_DOCUMENT:
        raise CPIPITAvailabilityError("CPI publication evidence has wrong P1 source role")
    if type(evidence.timing_semantics) is not CPIPublicationTimingSemantics:
        raise CPIPITAvailabilityError("CPI timing semantics must have exact enum identity")
    if evidence.timing_semantics is not CPIPublicationTimingSemantics.ACTUAL_RELEASE_OR_EMBARGO:
        raise CPIPITAvailabilityError("scheduled CPI timing cannot become actual-release proof")
    _exact_new_york(evidence.source_publish_at, "source publication time")
    authority = _p1_authority(evidence.profile, evidence.source_locator)
    expected = _publication_digest(evidence)
    exact = (
        evidence.p1_authority_identity == authority.authority_identity,
        evidence.p1_policy_identity == authority.policy_identity == P1_POLICY_IDENTITY,
        evidence.schema_version == _PUBLICATION_SCHEMA,
        evidence.evidence_id == expected,
        evidence.content_hash == expected,
        type(evidence.research_only) is bool and evidence.research_only is True,
        type(evidence.production_influence) is Decimal,
        evidence.production_influence == ZERO,
        type(evidence.timing_evidence_identity) is str,
        bool(evidence.timing_evidence_identity),
    )
    if not all(exact):
        raise CPIPITAvailabilityError("CPI publication evidence failed canonical revalidation")
    if _ISSUED_PUBLICATION_FINGERPRINTS.get(id(evidence)) != expected:
        raise CPIPITAvailabilityError("unissued, reconstructed, or mutated publication evidence")


def conservative_replay_boundary(source_publish_at: datetime) -> datetime:
    """Final representable instant of the actual BLS release date in New York."""
    published = _exact_new_york(source_publish_at, "source publication time")
    return datetime.combine(published.date(), time.max, tzinfo=NEW_YORK)


def build_cpi_reconstructed_availability(
    artifact: CPIHistoricalReleaseArtifact,
    *,
    publication_evidence: CPIActualPublicationEvidence,
) -> Availability:
    """Build positive replay only from exact source artifact and issued ACTUAL timing proof."""
    validate_cpi_release_artifact(artifact)
    validate_cpi_publication_evidence(publication_evidence)
    bindings = (
        publication_evidence.profile == artifact.profile,
        publication_evidence.source_role == artifact.source_role,
        publication_evidence.source_locator == artifact.source_locator,
        publication_evidence.source_artifact_id == artifact.artifact_id,
        publication_evidence.raw_artifact_sha256 == artifact.raw_artifact_sha256,
        publication_evidence.p1_authority_identity == artifact.p1_authority_identity,
        publication_evidence.p1_policy_identity == artifact.p1_policy_identity,
    )
    if not all(bindings):
        raise CPIPITAvailabilityError("publication evidence is not bound to exact CPI artifact")
    source_publish_at = publication_evidence.source_publish_at
    replay_available_at = conservative_replay_boundary(source_publish_at)
    assumed_latency = replay_available_at - source_publish_at
    if assumed_latency < timedelta(0):
        raise CPIPITAvailabilityError("derived conservative latency cannot be negative")
    if artifact.actual_bot_ingest_at < replay_available_at:
        raise CPIPITAvailabilityError("actual bot ingest precedes conservative replay boundary")
    availability = Availability.reconstructed(
        AvailabilityBasis.RECONSTRUCTED_PRIMARY_SOURCE,
        source_event_at=None,
        source_publish_at=source_publish_at,
        actual_bot_ingest_at=artifact.actual_bot_ingest_at,
        assumed_latency=assumed_latency,
        quality=AvailabilityQuality.CONSERVATIVE_ASSUMPTION,
    )
    valid = (
        availability.basis is AvailabilityBasis.RECONSTRUCTED_PRIMARY_SOURCE,
        availability.quality is AvailabilityQuality.CONSERVATIVE_ASSUMPTION,
        availability.replay_available_at == replay_available_at,
        availability.assumed_latency == assumed_latency,
        availability.replay_available_at == source_publish_at + assumed_latency,
        availability.actual_bot_ingest_at >= replay_available_at,
    )
    if not all(valid):
        raise CPIPITAvailabilityError("constructed CPI availability failed revalidation")
    return availability


def build_unknown_cpi_availability(*, actual_bot_ingest_at: datetime) -> Availability:
    """Represent insufficient evidence as UNKNOWN without causal-replay eligibility."""
    return Availability.unknown(
        source_event_at=None,
        source_publish_at=None,
        actual_bot_ingest_at=_aware_utc(actual_bot_ingest_at, "actual bot ingest time"),
    )


def _issue_actual_cpi_publication_evidence(
    *,
    artifact: CPIHistoricalReleaseArtifact,
    source_publish_at: datetime,
    timing_semantics: CPIPublicationTimingSemantics,
    timing_evidence_identity: str,
    _capability: object | None = None,
) -> CPIActualPublicationEvidence:
    """Private synthetic/review seam for a future independently evidenced empirical parser."""
    return CPIActualPublicationEvidence(
        artifact=artifact,
        source_publish_at=source_publish_at,
        timing_semantics=timing_semantics,
        timing_evidence_identity=timing_evidence_identity,
        _capability=_capability,
    )
