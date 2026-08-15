"""M26G repository-reviewed partitions of verified exchange events.

Exchange-event identity is not statistical independence.  This module can only
establish distinct descriptive evidence units under an explicit reviewed policy.
It has no inference, execution, allocation, governance, or network dependency.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from types import SimpleNamespace
from typing import cast

from services.market_universe.archive import UniverseObservationArchive

from .domain import ZERO_INFLUENCE
from .event_evidence import (
    EventEvidenceAssessment,
    EventEvidenceManifest,
    ExchangeEventIdentityState,
    IndependenceState,
    ReviewEligibility,
    _assessment_identity,
    _validate_manifest_bindings,
    assess_manifest,
)

EVIDENCE_UNIT_ASSIGNMENT_VERSION = "m26g-evidence-unit-assignment-v1"
REVIEWED_REGISTRY_VERSION = "m26g-reviewed-evidence-unit-registry-v1"
PARTITION_POLICY_VERSION = "m26g-reviewed-evidence-units-v1"
ASSESSMENT_POLICY_VERSION = "m26g-reviewed-evidence-unit-assessment-v1"
HUMAN_REVIEW_SUFFICIENCY_POLICY_VERSION = "m26g-human-review-evidence-sufficiency-v1"
EVIDENCE_UNIT_ASSESSMENT_VERSION = "m26g-evidence-unit-assessment-v1"
MINIMUM_REVIEWED_DISTINCT_EVIDENCE_UNITS = 50

_UNIT_ID = re.compile(r"^[A-Z0-9][A-Z0-9._:-]{0,127}$")


class EvidenceUnitError(ValueError):
    """Reviewed evidence-unit authority is missing, malformed, or inconsistent."""


@dataclass(frozen=True, slots=True, order=True)
class VerifiedEventAuthority:
    """Exact M26F historical event authority consumed by one assignment."""

    archive_authority_id: str
    event_observation_id: str
    event_source_hash: str
    event_ticker: str
    series_ticker: str
    production_influence: Decimal = ZERO_INFLUENCE

    def __post_init__(self) -> None:
        if not all(
            (
                self.archive_authority_id,
                self.event_observation_id,
                self.event_source_hash,
                self.event_ticker,
                self.series_ticker,
            )
        ):
            raise EvidenceUnitError("verified event authority is incomplete")
        if self.production_influence != ZERO_INFLUENCE:
            raise EvidenceUnitError("event authority has nonzero production influence")

    def material(self) -> dict[str, str]:
        return {
            "archive_authority_id": self.archive_authority_id,
            "event_observation_id": self.event_observation_id,
            "event_source_hash": self.event_source_hash,
            "event_ticker": self.event_ticker,
            "production_influence": "0",
            "series_ticker": self.series_ticker,
        }


@dataclass(frozen=True, slots=True)
class EvidenceUnitAssignment:
    assignment_id: str
    event_authority: VerifiedEventAuthority
    evidence_unit_id: str
    assignment_policy_version: str = EVIDENCE_UNIT_ASSIGNMENT_VERSION
    partition_policy_version: str = PARTITION_POLICY_VERSION
    production_influence: Decimal = ZERO_INFLUENCE

    def __post_init__(self) -> None:
        if not _UNIT_ID.fullmatch(self.evidence_unit_id):
            raise EvidenceUnitError("evidence unit ID is malformed")
        if self.production_influence != ZERO_INFLUENCE:
            raise EvidenceUnitError("assignment has nonzero production influence")
        if self.assignment_policy_version != EVIDENCE_UNIT_ASSIGNMENT_VERSION:
            raise EvidenceUnitError("assignment policy version is unsupported")
        if self.partition_policy_version != PARTITION_POLICY_VERSION:
            raise EvidenceUnitError("partition policy version is unsupported")
        if self.assignment_id != _assignment_identity(self):
            raise EvidenceUnitError("assignment identity mismatch")


def _assignment_identity(value: EvidenceUnitAssignment) -> str:
    return _hash(
        {
            "assignment_policy_version": value.assignment_policy_version,
            "domain": EVIDENCE_UNIT_ASSIGNMENT_VERSION,
            "event_authority": value.event_authority.material(),
            "evidence_unit_id": value.evidence_unit_id,
            "partition_policy_version": value.partition_policy_version,
            "production_influence": "0",
        }
    )


def _make_assignment(
    event_authority: VerifiedEventAuthority, evidence_unit_id: str
) -> EvidenceUnitAssignment:
    draft = cast(
        EvidenceUnitAssignment,
        SimpleNamespace(
            event_authority=event_authority,
            evidence_unit_id=evidence_unit_id,
            assignment_policy_version=EVIDENCE_UNIT_ASSIGNMENT_VERSION,
            partition_policy_version=PARTITION_POLICY_VERSION,
            production_influence=ZERO_INFLUENCE,
        ),
    )
    return EvidenceUnitAssignment(_assignment_identity(draft), event_authority, evidence_unit_id)


@dataclass(frozen=True, slots=True)
class EvidenceUnitAuthorityManifest:
    authority_manifest_id: str
    registry_version: str
    reviewed_manifest_version: str
    partition_policy_version: str
    assignments: tuple[EvidenceUnitAssignment, ...]
    production_influence: Decimal = ZERO_INFLUENCE

    def __post_init__(self) -> None:
        if self.registry_version != REVIEWED_REGISTRY_VERSION:
            raise EvidenceUnitError("reviewed registry version is unsupported")
        if not self.reviewed_manifest_version:
            raise EvidenceUnitError("reviewed manifest version is required")
        if self.partition_policy_version != PARTITION_POLICY_VERSION:
            raise EvidenceUnitError("reviewed partition policy is unsupported")
        if self.production_influence != ZERO_INFLUENCE:
            raise EvidenceUnitError("reviewed manifest has nonzero production influence")
        canonical = tuple(sorted(self.assignments, key=lambda row: row.assignment_id))
        if self.assignments != canonical:
            raise EvidenceUnitError("reviewed assignments are not canonical")
        if len({row.assignment_id for row in self.assignments}) != len(self.assignments):
            raise EvidenceUnitError("reviewed manifest contains duplicate assignments")
        authorities = [row.event_authority for row in self.assignments]
        if len(set(authorities)) != len(authorities):
            raise EvidenceUnitError("one event authority has multiple assignments")
        if self.authority_manifest_id != _authority_manifest_identity(self):
            raise EvidenceUnitError("reviewed authority manifest identity mismatch")


def _authority_manifest_identity(value: EvidenceUnitAuthorityManifest) -> str:
    return _hash(
        {
            "assignment_ids": [row.assignment_id for row in value.assignments],
            "domain": REVIEWED_REGISTRY_VERSION,
            "partition_policy_version": value.partition_policy_version,
            "production_influence": "0",
            "registry_version": value.registry_version,
            "reviewed_manifest_version": value.reviewed_manifest_version,
        }
    )


def _make_reviewed_authority_manifest(
    assignments: tuple[EvidenceUnitAssignment, ...], *, reviewed_manifest_version: str
) -> EvidenceUnitAuthorityManifest:
    """Repository-maintainer/test fixture builder; does not itself confer authority."""
    unique = {row.assignment_id: row for row in assignments}
    canonical = tuple(sorted(unique.values(), key=lambda row: row.assignment_id))
    draft = cast(
        EvidenceUnitAuthorityManifest,
        SimpleNamespace(
            registry_version=REVIEWED_REGISTRY_VERSION,
            reviewed_manifest_version=reviewed_manifest_version,
            partition_policy_version=PARTITION_POLICY_VERSION,
            assignments=canonical,
            production_influence=ZERO_INFLUENCE,
        ),
    )
    return EvidenceUnitAuthorityManifest(
        _authority_manifest_identity(draft),
        REVIEWED_REGISTRY_VERSION,
        reviewed_manifest_version,
        PARTITION_POLICY_VERSION,
        canonical,
    )


# Repository review is the trust root.  M26G ships with no real assignments.
# Synthetic tests replace this immutable tuple only inside their process.
_REPOSITORY_REVIEWED_AUTHORITIES: tuple[EvidenceUnitAuthorityManifest, ...] = ()


@dataclass(frozen=True, slots=True)
class ReviewedEvidenceUnitAssessment:
    assessment_id: str
    source_event_assessment_id: str
    source_event_manifest_id: str
    authority_manifest_id: str | None
    registry_version: str
    partition_policy_version: str
    assessment_policy_version: str
    human_review_policy_version: str
    verified_event_authorities: tuple[VerifiedEventAuthority, ...]
    assignment_ids: tuple[str, ...]
    unresolved_event_authorities: tuple[VerifiedEventAuthority, ...]
    proven_exchange_event_count: int | None
    dependence_cluster_count: int | None
    proven_independent_evidence_unit_count: int | None
    independence_state: IndependenceState
    review_eligibility: ReviewEligibility
    explanation: str
    interval: None = None
    production_influence: Decimal = ZERO_INFLUENCE

    def __post_init__(self) -> None:
        if self.production_influence != ZERO_INFLUENCE or self.interval is not None:
            raise EvidenceUnitError("M26G has zero influence and no statistical interval")
        if self.assessment_id != _assessment_identity_m26g(self):
            raise EvidenceUnitError("M26G assessment identity mismatch")


def _assessment_identity_m26g(value: ReviewedEvidenceUnitAssessment) -> str:
    return _hash(
        {
            "assessment_policy_version": value.assessment_policy_version,
            "assignment_ids": value.assignment_ids,
            "authority_manifest_id": value.authority_manifest_id,
            "dependence_cluster_count": value.dependence_cluster_count,
            "domain": EVIDENCE_UNIT_ASSESSMENT_VERSION,
            "explanation": value.explanation,
            "human_review_policy_version": value.human_review_policy_version,
            "independence_state": value.independence_state.value,
            "partition_policy_version": value.partition_policy_version,
            "production_influence": "0",
            "proven_exchange_event_count": value.proven_exchange_event_count,
            "proven_independent_evidence_unit_count": (
                value.proven_independent_evidence_unit_count
            ),
            "registry_version": value.registry_version,
            "review_eligibility": value.review_eligibility.value,
            "source_event_assessment_id": value.source_event_assessment_id,
            "source_event_manifest_id": value.source_event_manifest_id,
            "unresolved_event_authorities": [
                row.material() for row in value.unresolved_event_authorities
            ],
            "verified_event_authorities": [
                row.material() for row in value.verified_event_authorities
            ],
        }
    )


def _verified_event_authorities(
    manifest: EventEvidenceManifest,
) -> tuple[VerifiedEventAuthority, ...]:
    by_ticker: dict[str, VerifiedEventAuthority] = {}
    for binding in manifest.bindings:
        if binding.state is not ExchangeEventIdentityState.PROVEN:
            raise EvidenceUnitError("complete M26F exchange-event proof is required")
        receipt = binding.archive_receipt
        if receipt is None or binding.event_ticker is None or binding.series_ticker is None:
            raise EvidenceUnitError("proven event binding has incomplete archive authority")
        authority = VerifiedEventAuthority(
            receipt.archive_authority_id,
            receipt.event_observation_id,
            receipt.event_source_hash,
            binding.event_ticker,
            binding.series_ticker,
        )
        prior = by_ticker.setdefault(binding.event_ticker, authority)
        if prior != authority:
            raise EvidenceUnitError("one exchange event resolves to conflicting archive authority")
    return tuple(sorted(by_ticker.values()))


def _selected_repository_authority() -> EvidenceUnitAuthorityManifest | None:
    matches = tuple(
        row
        for row in _REPOSITORY_REVIEWED_AUTHORITIES
        if row.registry_version == REVIEWED_REGISTRY_VERSION
        and row.partition_policy_version == PARTITION_POLICY_VERSION
    )
    if not matches:
        return None
    if len(matches) != 1:
        raise EvidenceUnitError("reviewed repository authority is ambiguous")
    row = matches[0]
    # Re-run construction checks against object-model mutation/forgery.
    row.__post_init__()
    for assignment in row.assignments:
        assignment.event_authority.__post_init__()
        assignment.__post_init__()
    return row


def assess_reviewed_evidence_units(
    manifest: EventEvidenceManifest,
    source_assessment: EventEvidenceAssessment,
    *,
    archive: UniverseObservationArchive,
) -> ReviewedEvidenceUnitAssessment:
    """Apply only the repository-reviewed authority to the complete M26E universe."""
    _validate_manifest_bindings(manifest.bindings, archive, require_archive=True)
    expected = assess_manifest(manifest, archive=archive)
    if (
        source_assessment.assessment_id != _assessment_identity(source_assessment)
        or source_assessment != expected
        or source_assessment.manifest_id != manifest.manifest_id
    ):
        raise EvidenceUnitError("source M26E assessment is forged, stale, or mismatched")

    verified: tuple[VerifiedEventAuthority, ...] = ()
    authority = _selected_repository_authority()
    assignments: tuple[EvidenceUnitAssignment, ...] = ()
    unresolved: tuple[VerifiedEventAuthority, ...] = ()
    count: int | None = None
    clusters: int | None = None
    state = IndependenceState.NOT_PROVEN
    eligibility = ReviewEligibility.NOT_ELIGIBLE
    authority_id: str | None = None

    if (
        source_assessment.exchange_event_identity_state is not ExchangeEventIdentityState.PROVEN
        or source_assessment.proven_exchange_event_count is None
    ):
        explanation = "Reviewed distinct evidence units unavailable: M26F proof is incomplete."
    else:
        verified = _verified_event_authorities(manifest)
        if authority is None:
            unresolved = verified
            explanation = "Independent evidence authority is not configured."
        else:
            authority_id = authority.authority_manifest_id
            by_event = {row.event_authority: row for row in authority.assignments}
            assignments = tuple(by_event[row] for row in verified if row in by_event)
            unresolved = tuple(row for row in verified if row not in by_event)
            if unresolved:
                explanation = (
                    "Reviewed authority coverage is incomplete; no survivor-only count is valid."
                )
            else:
                units = {row.evidence_unit_id for row in assignments}
                count = clusters = len(units)
                event_count = len(verified)
                state = (
                    IndependenceState.DEPENDENT
                    if count < event_count
                    else IndependenceState.PROVEN_DISTINCT_UNDER_POLICY
                )
                eligibility = (
                    ReviewEligibility.ELIGIBLE
                    if count >= MINIMUM_REVIEWED_DISTINCT_EVIDENCE_UNITS
                    else ReviewEligibility.NOT_ELIGIBLE
                )
                explanation = (
                    f"{count} distinct evidence units under reviewed policy; this is a "
                    "descriptive partition, not mathematical independence."
                )

    assignment_ids = tuple(sorted(row.assignment_id for row in assignments))
    values = dict(
        source_event_assessment_id=source_assessment.assessment_id,
        source_event_manifest_id=manifest.manifest_id,
        authority_manifest_id=authority_id,
        registry_version=REVIEWED_REGISTRY_VERSION,
        partition_policy_version=PARTITION_POLICY_VERSION,
        assessment_policy_version=ASSESSMENT_POLICY_VERSION,
        human_review_policy_version=HUMAN_REVIEW_SUFFICIENCY_POLICY_VERSION,
        verified_event_authorities=verified,
        assignment_ids=assignment_ids,
        unresolved_event_authorities=tuple(sorted(unresolved)),
        proven_exchange_event_count=source_assessment.proven_exchange_event_count,
        dependence_cluster_count=clusters,
        proven_independent_evidence_unit_count=count,
        independence_state=state,
        review_eligibility=eligibility,
        explanation=explanation,
    )
    draft = cast(
        ReviewedEvidenceUnitAssessment,
        SimpleNamespace(**values, interval=None, production_influence=ZERO_INFLUENCE),
    )
    return ReviewedEvidenceUnitAssessment(
        assessment_id=_assessment_identity_m26g(draft),
        source_event_assessment_id=source_assessment.assessment_id,
        source_event_manifest_id=manifest.manifest_id,
        authority_manifest_id=authority_id,
        registry_version=REVIEWED_REGISTRY_VERSION,
        partition_policy_version=PARTITION_POLICY_VERSION,
        assessment_policy_version=ASSESSMENT_POLICY_VERSION,
        human_review_policy_version=HUMAN_REVIEW_SUFFICIENCY_POLICY_VERSION,
        verified_event_authorities=verified,
        assignment_ids=assignment_ids,
        unresolved_event_authorities=tuple(sorted(unresolved)),
        proven_exchange_event_count=source_assessment.proven_exchange_event_count,
        dependence_cluster_count=clusters,
        proven_independent_evidence_unit_count=count,
        independence_state=state,
        review_eligibility=eligibility,
        explanation=explanation,
    )


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
