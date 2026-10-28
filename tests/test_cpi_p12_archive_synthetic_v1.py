"""Invented metadata only: archive classification is never real evidence authority."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import pytest

from experiments.cpi_p12_archive_synthetic_v1.probe import (
    INVENTED_MARKER,
    InventedArchiveCase,
    InventedDisposition,
    classify_invented_archive,
)


def _assert_no_authority(outcome: InventedDisposition) -> None:
    assert outcome.scientific_disposition == "INCONCLUSIVE"
    assert outcome.synthetic_only is True
    assert outcome.remote_archive_authorized is False
    assert outcome.scientific_evidence_authorized is False
    assert outcome.phase1_economic_test_authorized is False
    assert outcome.p12_frozen is False
    assert outcome.production_influence == "0"
    assert outcome.promotion_authority == "NONE"
    assert outcome.trading_authority == "NONE"


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"protocol_frozen": False}, "ARCHIVE_PROTOCOL_UNFROZEN"),
        ({"retrieval_retention_rights": False}, "SOURCE_RETENTION_RIGHTS_UNVERIFIED"),
        ({"post_cutoff_source_refetch": True}, "FORBIDDEN_TARGET_OR_SOURCE_REFETCH"),
        ({"target_access_requested": True}, "FORBIDDEN_TARGET_OR_SOURCE_REFETCH"),
        ({"required_stages_present": False}, "REQUIRED_STAGE_INCOMPLETE"),
        ({"local_receipts_committed": False}, "LOCAL_RECEIPT_NOT_COMMITTED"),
        ({"local_pending_guard": True}, "LOCAL_RECEIPT_NOT_COMMITTED"),
        ({"local_hashes_match": False}, "LOCAL_ARTIFACT_CHANGED"),
        ({"destination_identity_valid": False}, "DESTINATION_OR_NAMESPACE_INVALID"),
        ({"stage_namespaces_disjoint": False}, "DESTINATION_OR_NAMESPACE_INVALID"),
        ({"historical_name_reuse": True}, "HISTORICAL_OBJECT_IDENTITY_AMBIGUOUS"),
        ({"create_if_no_live_object": False}, "CREATE_ONCE_PRECONDITION_UNPROVED"),
        ({"precondition_race_resolved": False}, "CREATE_ONCE_PRECONDITION_UNPROVED"),
        ({"upload_complete": False}, "REMOTE_UPLOAD_INCOMPLETE"),
        ({"remote_read_authorized": False}, "REMOTE_READ_UNAVAILABLE"),
        ({"generation_pinned": False}, "REMOTE_GENERATION_UNPROVED"),
        ({"readback_generation": "INVENTED-GENERATION-B"}, "REMOTE_GENERATION_UNPROVED"),
        ({"full_readback_complete": False}, "REMOTE_BYTES_UNVERIFIED"),
        ({"remote_hash_matches": False}, "REMOTE_CONTENT_MISMATCH"),
        ({"remote_length_matches": False}, "REMOTE_CONTENT_MISMATCH"),
        ({"archive_receipt_committed": False}, "ARCHIVE_RECEIPT_UNCOMMITTED"),
        ({"archive_pending_guard": True}, "ARCHIVE_RECEIPT_UNCOMMITTED"),
        ({"archive_final_fsync_ok": False}, "ARCHIVE_RECEIPT_UNCOMMITTED"),
        ({"protocol_frozen": 1}, "INVALID_SYNTHETIC_INPUT"),
        ({"historical_name_reuse": 0}, "INVALID_SYNTHETIC_INPUT"),
        ({"generation_pinned": "yes"}, "INVALID_SYNTHETIC_INPUT"),
        ({"uploaded_generation": 10}, "INVALID_SYNTHETIC_INPUT"),
        ({"readback_generation": None}, "INVALID_SYNTHETIC_INPUT"),
        ({"uploaded_generation": ""}, "INVALID_SYNTHETIC_INPUT"),
        ({"readback_generation": "X" * 129}, "INVALID_SYNTHETIC_INPUT"),
        ({"marker": "ACTUAL_SOURCE"}, "INVALID_SYNTHETIC_INPUT"),
    ],
)
def test_invented_failure_cases_are_non_authorizing(
    changes: dict[str, object], reason: str
) -> None:
    case = replace(InventedArchiveCase(), **cast(Any, changes))
    result = classify_invented_archive(case)
    assert result.reason == reason
    _assert_no_authority(result)


def test_nominal_invented_case_never_confers_archive_or_scientific_authority() -> None:
    outcome = classify_invented_archive(InventedArchiveCase())
    assert outcome.reason == "CONSISTENT_INVENTED_CASE_ONLY"
    _assert_no_authority(outcome)


def test_noncurrent_historical_version_is_not_hidden_by_no_live_object() -> None:
    case = InventedArchiveCase(
        historical_name_reuse=True,
        create_if_no_live_object=True,
        upload_complete=True,
        generation_pinned=True,
    )
    result = classify_invented_archive(case)
    assert result.reason == "HISTORICAL_OBJECT_IDENTITY_AMBIGUOUS"
    _assert_no_authority(result)


def test_existing_matching_generation_is_not_proven_if_receipt_uncommitted() -> None:
    case = InventedArchiveCase(archive_pending_guard=True)
    result = classify_invented_archive(case)
    assert result.reason == "ARCHIVE_RECEIPT_UNCOMMITTED"
    _assert_no_authority(result)


def test_source_rights_and_refetch_veto_even_when_remote_checks_look_successful() -> None:
    case = InventedArchiveCase(
        retrieval_retention_rights=False,
        remote_hash_matches=True,
        full_readback_complete=True,
        archive_receipt_committed=True,
    )
    assert classify_invented_archive(case).reason == "SOURCE_RETENTION_RIGHTS_UNVERIFIED"
    _assert_no_authority(classify_invented_archive(case))
    case = replace(case, retrieval_retention_rights=True, post_cutoff_source_refetch=True)
    assert classify_invented_archive(case).reason == "FORBIDDEN_TARGET_OR_SOURCE_REFETCH"
    _assert_no_authority(classify_invented_archive(case))


def test_generation_identifiers_are_opaque_not_numerically_sorted() -> None:
    case = InventedArchiveCase(
        uploaded_generation="GEN-Z",
        readback_generation="GEN-Z",
    )
    result = classify_invented_archive(case)
    assert result.reason == "CONSISTENT_INVENTED_CASE_ONLY"
    _assert_no_authority(result)


class _DeceptiveMarker:
    def __eq__(self, other: object) -> bool:
        raise AssertionError("caller-controlled marker equality must not be invoked")


class _MarkerSubclass(str):
    def __eq__(self, other: object) -> bool:
        return True


def test_malformed_marker_does_not_invoke_caller_equality() -> None:
    for marker in (_DeceptiveMarker(), _MarkerSubclass(INVENTED_MARKER), 0, b"fake"):
        case = replace(InventedArchiveCase(), marker=cast(Any, marker))
        result = classify_invented_archive(case)
        assert result.reason == "INVALID_SYNTHETIC_INPUT"
        _assert_no_authority(result)


def test_wrong_object_type_cannot_invoke_classifier() -> None:
    for item in (None, {}, [], "fixture"):
        result = classify_invented_archive(cast(Any, item))
        assert result.reason == "INVALID_SYNTHETIC_INPUT"
        _assert_no_authority(result)


def test_caller_cannot_inject_positive_authority_fields() -> None:
    with pytest.raises(TypeError):
        InventedArchiveCase(scientific_evidence_authorized=True)  # type: ignore[call-arg]

    result = classify_invented_archive(InventedArchiveCase())
    assert result.remote_archive_authorized is False
    assert result.scientific_evidence_authorized is False
    _assert_no_authority(result)
