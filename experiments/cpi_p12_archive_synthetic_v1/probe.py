"""Pure invented-only archive failure classification for CPI P12 research.

No URLs, source bytes, filesystem, network, GCS operations, credentials,
production imports, real evidence validation or scientific authorization.
Even the internally consistent invented case is INCONCLUSIVE.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

INVENTED_MARKER = "CPI-P12-INVENTED-ARCHIVE-ONLY-v1"


@dataclass(frozen=True, slots=True)
class InventedArchiveCase:
    """Only boolean assertions about an invented archive transaction."""

    marker: str = INVENTED_MARKER
    protocol_frozen: bool = True
    retrieval_retention_rights: bool = True
    required_stages_present: bool = True
    local_receipts_committed: bool = True
    local_pending_guard: bool = False
    local_hashes_match: bool = True
    post_cutoff_source_refetch: bool = False
    target_access_requested: bool = False
    destination_identity_valid: bool = True
    stage_namespaces_disjoint: bool = True
    historical_name_reuse: bool = False
    create_if_no_live_object: bool = True
    precondition_race_resolved: bool = True
    upload_complete: bool = True
    remote_read_authorized: bool = True
    generation_pinned: bool = True
    uploaded_generation: str = "INVENTED-GENERATION-A"
    readback_generation: str = "INVENTED-GENERATION-A"
    full_readback_complete: bool = True
    remote_hash_matches: bool = True
    remote_length_matches: bool = True
    archive_receipt_committed: bool = True
    archive_pending_guard: bool = False
    archive_final_fsync_ok: bool = True


@dataclass(frozen=True, slots=True)
class InventedDisposition:
    """No synthetic input can promote archive or scientific authority."""

    reason: str
    scientific_disposition: Literal["INCONCLUSIVE"] = "INCONCLUSIVE"
    synthetic_only: Literal[True] = True
    remote_archive_authorized: Literal[False] = False
    scientific_evidence_authorized: Literal[False] = False
    phase1_economic_test_authorized: Literal[False] = False
    p12_frozen: Literal[False] = False
    production_influence: Literal["0"] = "0"
    promotion_authority: Literal["NONE"] = "NONE"
    trading_authority: Literal["NONE"] = "NONE"


def classify_invented_archive(case: InventedArchiveCase) -> InventedDisposition:
    """Test proposed fail-closed precedence, not a real storage validator."""
    if type(case) is not InventedArchiveCase:
        return InventedDisposition("INVALID_SYNTHETIC_INPUT")
    if type(case.marker) is not str or case.marker != INVENTED_MARKER:
        return InventedDisposition("INVALID_SYNTHETIC_INPUT")

    flags = (
        case.protocol_frozen,
        case.retrieval_retention_rights,
        case.required_stages_present,
        case.local_receipts_committed,
        case.local_pending_guard,
        case.local_hashes_match,
        case.post_cutoff_source_refetch,
        case.target_access_requested,
        case.destination_identity_valid,
        case.stage_namespaces_disjoint,
        case.historical_name_reuse,
        case.create_if_no_live_object,
        case.precondition_race_resolved,
        case.upload_complete,
        case.remote_read_authorized,
        case.generation_pinned,
        case.full_readback_complete,
        case.remote_hash_matches,
        case.remote_length_matches,
        case.archive_receipt_committed,
        case.archive_pending_guard,
        case.archive_final_fsync_ok,
    )
    if (
        any(type(flag) is not bool for flag in flags)
        or type(case.uploaded_generation) is not str
        or type(case.readback_generation) is not str
        or not case.uploaded_generation
        or not case.readback_generation
        or len(case.uploaded_generation) > 128
        or len(case.readback_generation) > 128
    ):
        return InventedDisposition("INVALID_SYNTHETIC_INPUT")

    if not case.protocol_frozen:
        return InventedDisposition("ARCHIVE_PROTOCOL_UNFROZEN")
    if not case.retrieval_retention_rights:
        return InventedDisposition("SOURCE_RETENTION_RIGHTS_UNVERIFIED")
    if case.target_access_requested or case.post_cutoff_source_refetch:
        return InventedDisposition("FORBIDDEN_TARGET_OR_SOURCE_REFETCH")
    if not case.required_stages_present:
        return InventedDisposition("REQUIRED_STAGE_INCOMPLETE")
    if not case.local_receipts_committed or case.local_pending_guard:
        return InventedDisposition("LOCAL_RECEIPT_NOT_COMMITTED")
    if not case.local_hashes_match:
        return InventedDisposition("LOCAL_ARTIFACT_CHANGED")
    if not case.destination_identity_valid or not case.stage_namespaces_disjoint:
        return InventedDisposition("DESTINATION_OR_NAMESPACE_INVALID")
    if case.historical_name_reuse:
        # A GCS no-live-object precondition alone cannot rule this out.
        return InventedDisposition("HISTORICAL_OBJECT_IDENTITY_AMBIGUOUS")
    if not case.create_if_no_live_object or not case.precondition_race_resolved:
        return InventedDisposition("CREATE_ONCE_PRECONDITION_UNPROVED")
    if not case.upload_complete:
        return InventedDisposition("REMOTE_UPLOAD_INCOMPLETE")
    if not case.remote_read_authorized:
        return InventedDisposition("REMOTE_READ_UNAVAILABLE")
    if not case.generation_pinned or case.readback_generation != case.uploaded_generation:
        return InventedDisposition("REMOTE_GENERATION_UNPROVED")
    if not case.full_readback_complete:
        return InventedDisposition("REMOTE_BYTES_UNVERIFIED")
    if not case.remote_length_matches or not case.remote_hash_matches:
        return InventedDisposition("REMOTE_CONTENT_MISMATCH")
    if (
        not case.archive_receipt_committed
        or case.archive_pending_guard
        or not case.archive_final_fsync_ok
    ):
        return InventedDisposition("ARCHIVE_RECEIPT_UNCOMMITTED")
    return InventedDisposition("CONSISTENT_INVENTED_CASE_ONLY")
