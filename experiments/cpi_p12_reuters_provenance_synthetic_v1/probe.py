"""Pure offline probe of proposed P12 Reuters provenance failure classifications.

NOT a source selector, source reader, scientific validator, target forecast parser,
or an authorization mechanism. No external I/O or production imports.
Every outcome is scientifically INCONCLUSIVE, including the synthetic good case.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

SYNTHETIC_MARKER = "CPI-P12-REUTERS-INVENTED-FIXTURE-ONLY-v1"


@dataclass(frozen=True)
class InventedEvidence:
    """Invented assertions about a fake publication, never real URLs/bytes."""

    marker: str = SYNTHETIC_MARKER
    method_frozen: bool = True
    retrieval_and_retention_rights: bool = True
    locator_authorized: bool = True
    status_code: int = 200
    transport_complete: bool = True
    receipt_committed: bool = True
    clock_error_ms: int = 0
    request_started_at: datetime = datetime(2026, 1, 2, 11, 0, tzinfo=UTC)
    response_finished_at: datetime = datetime(2026, 1, 2, 11, 1, tzinfo=UTC)
    cutoff_at: datetime = datetime(2026, 1, 2, 11, 5, tzinfo=UTC)
    claimed_first_publication_at: datetime | None = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)
    issuer_attribution_verified: bool = True
    first_publication_independently_verified: bool = True
    revision_history_verified: bool = True
    has_conflicting_candidates: bool = False
    candidate_count: int = 1
    deterministic_selection_before_exposure: bool = True
    corroboration_required: bool = True
    corroborator_independent: bool = True
    corroborator_same_wire: bool = False


@dataclass(frozen=True)
class ResearchOnlyDisposition:
    """Explicit absence of evidence authority in every synthetic result."""

    reason: str
    scientific_disposition: Literal["INCONCLUSIVE"] = "INCONCLUSIVE"
    research_only: Literal[True] = True
    scientific_evidence_authorized: Literal[False] = False
    phase1_economic_test_authorized: Literal[False] = False
    reuters_forecast_authorized: Literal[False] = False
    p12_frozen: Literal[False] = False
    production_influence: Literal["0"] = "0"
    promotion_authority: Literal["NONE"] = "NONE"
    trading_authority: Literal["NONE"] = "NONE"


def _aware(value: object) -> bool:
    return (
        type(value) is datetime
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def classify_invented_case(case: InventedEvidence) -> ResearchOnlyDisposition:
    """Exercise conservative ordering, NEVER confer scientific authority.

    This intentionally cannot accept real source bytes, URLs, forecasts, or
    issuer attestations. A green synthetic case cannot establish provenance.
    """
    if type(case) is not InventedEvidence or case.marker != SYNTHETIC_MARKER:
        return ResearchOnlyDisposition("INVALID_SYNTHETIC_INPUT")

    flags = (
        case.method_frozen,
        case.retrieval_and_retention_rights,
        case.locator_authorized,
        case.transport_complete,
        case.receipt_committed,
        case.issuer_attribution_verified,
        case.first_publication_independently_verified,
        case.revision_history_verified,
        case.has_conflicting_candidates,
        case.deterministic_selection_before_exposure,
        case.corroboration_required,
        case.corroborator_independent,
        case.corroborator_same_wire,
    )
    if (
        any(type(flag) is not bool for flag in flags)
        or type(case.status_code) is not int
        or type(case.clock_error_ms) is not int
        or type(case.candidate_count) is not int
        or case.clock_error_ms < 0
        or case.clock_error_ms > 3_600_000
        or case.candidate_count < 0
        or case.candidate_count > 100
    ):
        return ResearchOnlyDisposition("INVALID_SYNTHETIC_INPUT")
    if not all(_aware(t) for t in (
        case.request_started_at, case.response_finished_at, case.cutoff_at,
    )) or (
        case.claimed_first_publication_at is not None
        and not _aware(case.claimed_first_publication_at)
    ):
        return ResearchOnlyDisposition("INVALID_SYNTHETIC_INPUT")

    if not case.method_frozen or not case.locator_authorized:
        return ResearchOnlyDisposition("METHOD_OR_LOCATOR_UNFROZEN")
    if not case.retrieval_and_retention_rights:
        return ResearchOnlyDisposition("ACCESS_RIGHTS_UNVERIFIED")
    if case.status_code in (403, 429, 500, 503):
        return ResearchOnlyDisposition("SOURCE_UNAVAILABLE")
    if case.status_code != 200 or not case.transport_complete:
        return ResearchOnlyDisposition("TRANSPORT_INVALID")
    if not case.receipt_committed:
        return ResearchOnlyDisposition("PERSISTENCE_UNCONFIRMED")
    if not case.deterministic_selection_before_exposure:
        return ResearchOnlyDisposition("RETROSPECTIVE_SELECTION")
    error = timedelta(milliseconds=case.clock_error_ms)
    if (
        case.response_finished_at < case.request_started_at
        or case.request_started_at + error >= case.cutoff_at
        or case.response_finished_at + error >= case.cutoff_at
    ):
        return ResearchOnlyDisposition("LATE_OR_UNPROVABLE_TIMING")
    if case.candidate_count == 0:
        return ResearchOnlyDisposition("NO_CANDIDATE")
    if case.candidate_count != 1 or case.has_conflicting_candidates:
        return ResearchOnlyDisposition("CANDIDATE_CONFLICT")
    if not case.issuer_attribution_verified:
        return ResearchOnlyDisposition("ISSUER_ATTRIBUTION_UNPROVED")
    if (
        case.claimed_first_publication_at is None
        or not case.first_publication_independently_verified
    ):
        return ResearchOnlyDisposition("PUBLICATION_TIME_UNPROVED")
    if case.claimed_first_publication_at + error >= case.cutoff_at:
        return ResearchOnlyDisposition("LATE_OR_UNPROVABLE_PUBLICATION")
    if not case.revision_history_verified:
        return ResearchOnlyDisposition("REVISION_HISTORY_UNPROVED")
    if case.corroboration_required and case.corroborator_same_wire:
        return ResearchOnlyDisposition("DUPLICATED_WIRE_NOT_INDEPENDENT")
    if case.corroboration_required and not case.corroborator_independent:
        return ResearchOnlyDisposition("CORROBORATION_UNPROVED")
    return ResearchOnlyDisposition("CONSISTENT_INVENTED_CASE_ONLY")
