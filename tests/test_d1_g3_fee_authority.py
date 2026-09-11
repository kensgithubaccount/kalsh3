"""Adversarial tests for the issuer-controlled KXGDP fee boundary."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from services.production_gdp_strategy import fee_authority as module

NOW = datetime(2026, 9, 10, 21, tzinfo=UTC)


def _pdf(*, row: str = "KXGDP 1 1", suffix: str = "") -> bytes:
    return (
        "%PDF-1.7\nKalshi Fee Schedule\nLast updated and effective: July 7, 2026\n"
        "The current general fee is determined by the following formula.\n"
        "fees = round up (M * 0.07 * C * P * (1-P))\nMaker Fees\n"
        "fees = round up (M * 0.0175 * C * P * (1-P))\n"
        "P is contract price in dollars and C is contract quantity\n"
        "Maker multiplier Taker multiplier " + row + "\n" + suffix + "\n%%EOF"
    ).encode()


def _issued(url: str, body: bytes, content_type: str) -> module._RawEvidence:
    return module._RawEvidence(
        url,
        "GET",
        200,
        content_type,
        NOW,
        body,
        hashlib.sha256(body).hexdigest(),
        _capability=module._RAW_EVIDENCE_CAPABILITY,
    )


def _schedule() -> module._FeeSchedule:
    return module._parse_fee_schedule(_issued(module.PDF_URL, _pdf(), "application/pdf"))


def _changes(records: list[dict[str, object]]) -> module._FeeChangeBatch:
    body = json.dumps({"series_fee_change_arr": records}, separators=(",", ":")).encode()
    return module._parse_fee_changes(_issued(module.FEE_CHANGES_URL, body, "application/json"))


def _record() -> dict[str, object]:
    return {
        "id": "change-a",
        "series_ticker": "KXGDP",
        "scheduled_ts": "2026-08-01T00:00:00Z",
        "fee_type": "quadratic",
        "fee_multiplier": "2",
    }


def test_forged_authority_candidate_cannot_issue_complete() -> None:
    with pytest.raises((module.FeeAuthorityError, TypeError)):
        module._AuthorityCandidate(
            status=module.FeeAuthorityStatus.COMPLETE,
            reason=None,
            policy=None,
            applicable_change_ids=(),
            resolver_id=module.RESOLVER_ID,
        )  # type: ignore[call-arg]


def test_caller_created_policy_cannot_become_positive_authority() -> None:
    policy = module._FeePolicy(
        "caller",
        module._FeeType.QUADRATIC,
        Decimal("1"),
        NOW,
        None,
        module.FORMULA_VERSION,
        "caller",
        True,
        quadratic_coefficient=Decimal("0.07"),
    )
    with pytest.raises(module.FeeAuthorityError):
        module._AuthorityCandidate(
            capability=module._RESOLVER_CAPABILITY,
            status=module.FeeAuthorityStatus.COMPLETE,
            reason=None,
            policy=policy,
            applicable_change_ids=(),
            resolver_id=module.RESOLVER_ID,
        )


def test_caller_created_exhaustive_batch_cannot_make_resolver_complete() -> None:
    batch = module._FeeChangeBatch(
        capability=module._PARSER_CAPABILITY,
        records=(),
        raw=_issued(module.FEE_CHANGES_URL, b'{"series_fee_change_arr":[]}', "application/json"),
        exhaustive_proven=True,
    )
    assert (
        module._resolve_fee_authority(NOW, _schedule(), batch).status
        is module.FeeAuthorityStatus.INCOMPLETE
    )


def test_issue_style_bypass_is_disabled_even_for_a_forged_candidate() -> None:
    candidate = object.__new__(module._AuthorityCandidate)
    for name, value in {
        "status": module.FeeAuthorityStatus.COMPLETE,
        "reason": None,
        "policy": None,
        "applicable_change_ids": (),
        "resolver_id": module.RESOLVER_ID,
    }.items():
        object.__setattr__(candidate, name, value)
    module._register(candidate)
    with pytest.raises(module.FeeAuthorityError):
        module._issue(candidate, None, None)


def _forged_complete_candidate() -> module._AuthorityCandidate:
    candidate = object.__new__(module._AuthorityCandidate)
    for name, value in {
        "status": module.FeeAuthorityStatus.COMPLETE,
        "reason": None,
        "policy": None,
        "applicable_change_ids": (),
        "resolver_id": module.RESOLVER_ID,
    }.items():
        object.__setattr__(candidate, name, value)
    module._register(candidate)
    return candidate


def test_direct_constructor_rejects_registered_complete_candidate() -> None:
    with pytest.raises(module.FeeAuthorityError):
        module.FeeAuthority(_candidate=_forged_complete_candidate(), _pdf=None, _changes=None)


def test_object_new_authority_is_unusable_for_all_public_operations() -> None:
    forged = object.__new__(module.FeeAuthority)
    for name, value in {
        "_status": module.FeeAuthorityStatus.COMPLETE,
        "_reason": None,
        "_policy": None,
        "_pdf_evidence": None,
        "_fee_change_evidence": None,
        "_applicable_change_ids": (),
        "_resolver_id": module.RESOLVER_ID,
        "_sealed": True,
    }.items():
        object.__setattr__(forged, name, value)
    for operation in (
        lambda: forged.status,
        lambda: forged.policy,
        lambda: forged.calculate_one_contract(Decimal("0.50")),
    ):
        with pytest.raises(module.FeeAuthorityError):
            operation()


def test_direct_authority_registration_rejects_complete() -> None:
    forged = object.__new__(module.FeeAuthority)
    for name, value in {
        "_status": module.FeeAuthorityStatus.COMPLETE,
        "_reason": None,
        "_policy": None,
        "_pdf_evidence": None,
        "_fee_change_evidence": None,
        "_applicable_change_ids": (),
        "_resolver_id": module.RESOLVER_ID,
        "_sealed": True,
    }.items():
        object.__setattr__(forged, name, value)
    with pytest.raises(module.FeeAuthorityError):
        module._register_authority(forged, _capability=module._AUTHORITY_REGISTRATION_CAPABILITY)


def _incomplete_candidate() -> module._AuthorityCandidate:
    return module._candidate_incomplete("test")


def test_direct_init_requires_pending_constructor_object() -> None:
    forged = object.__new__(module.FeeAuthority)
    with pytest.raises(module.FeeAuthorityError):
        module.FeeAuthority.__init__(
            forged, _candidate=_incomplete_candidate(), _pdf=None, _changes=None
        )


def test_pending_constructor_is_one_shot_and_helper_cannot_pre_register() -> None:
    candidate = _incomplete_candidate()
    pending = module.FeeAuthority.__new__(module.FeeAuthority, _candidate=candidate)
    with pytest.raises(module.FeeAuthorityError):
        module._register_authority(pending, _capability=module._AUTHORITY_REGISTRATION_CAPABILITY)
    module.FeeAuthority.__init__(pending, _candidate=candidate, _pdf=None, _changes=None)
    assert pending.status is module.FeeAuthorityStatus.INCOMPLETE
    with pytest.raises(module.FeeAuthorityError):
        module.FeeAuthority.__init__(pending, _candidate=candidate, _pdf=None, _changes=None)


def test_failed_init_clears_pending_constructor() -> None:
    candidate = _incomplete_candidate()
    pending = module.FeeAuthority.__new__(module.FeeAuthority, _candidate=candidate)
    with pytest.raises(module.FeeAuthorityError):
        module.FeeAuthority.__init__(
            pending,
            _candidate=candidate,
            _pdf=object(),
            _changes=None,  # type: ignore[arg-type]
        )
    assert id(pending) not in module._PENDING_AUTHORITIES
    with pytest.raises(module.FeeAuthorityError):
        module._register_authority(pending, _capability=module._AUTHORITY_REGISTRATION_CAPABILITY)


def test_complete_candidate_rejected_by_new_and_pending_init() -> None:
    complete = _forged_complete_candidate()
    with pytest.raises(module.FeeAuthorityError):
        module.FeeAuthority.__new__(module.FeeAuthority, _candidate=complete)
    pending = module.FeeAuthority.__new__(module.FeeAuthority, _candidate=_incomplete_candidate())
    with pytest.raises(module.FeeAuthorityError):
        module.FeeAuthority.__init__(pending, _candidate=complete, _pdf=None, _changes=None)


def test_caller_created_raw_evidence_with_valid_hash_is_rejected() -> None:
    body = _pdf()
    with pytest.raises(module.FeeAuthorityError):
        module._RawEvidence(
            module.PDF_URL,
            "GET",
            200,
            "application/pdf",
            NOW,
            body,
            hashlib.sha256(body).hexdigest(),
        )


def test_reconstructed_parsed_object_is_rejected() -> None:
    evidence = _issued(module.PDF_URL, _pdf(), "application/pdf")
    forged = object.__new__(module._RawEvidence)
    for name, value in {
        "source_url": evidence.source_url,
        "method": "GET",
        "status": 200,
        "content_type": "application/pdf",
        "acquired_at": NOW,
        "body": evidence.body,
        "body_sha256": evidence.body_sha256,
    }.items():
        object.__setattr__(forged, name, value)
    with pytest.raises(module.FeeAuthorityError):
        module._parse_fee_schedule(forged)


def test_empty_fee_change_array_is_not_exhaustive_and_is_incomplete() -> None:
    batch = _changes([])
    assert not batch.exhaustive_proven
    assert (
        module._resolve_fee_authority(NOW, _schedule(), batch).status
        is module.FeeAuthorityStatus.INCOMPLETE
    )


def test_missing_market_and_event_override_proof_is_incomplete() -> None:
    result = module._resolve_fee_authority(NOW, _schedule(), _changes([_record()]))
    assert result.status is module.FeeAuthorityStatus.INCOMPLETE
    assert "override" in (result.reason or "")


def test_contradictory_duplicate_fee_formulas_fail_closed() -> None:
    with pytest.raises(module.FeeAuthorityError):
        module._parse_fee_schedule(
            _issued(
                module.PDF_URL,
                _pdf(suffix="fees = round up (M * 0.07 * C * P * (1-P))"),
                "application/pdf",
            )
        )


def test_contradictory_kxgdp_table_rows_fail_closed() -> None:
    with pytest.raises(module.FeeAuthorityError):
        module._parse_fee_schedule(
            _issued(module.PDF_URL, _pdf(row="KXGDP 1 1 KXGDP 2 2"), "application/pdf")
        )


def test_contradictory_kxgdp_row_before_table_header_fails_closed() -> None:
    body = _pdf().replace(
        b"Maker multiplier Taker multiplier", b"KXGDP 2 2\nMaker multiplier Taker multiplier", 1
    )
    with pytest.raises(module.FeeAuthorityError):
        module._parse_fee_schedule(_issued(module.PDF_URL, body, "application/pdf"))


def test_date_only_effective_information_is_not_midnight_utc() -> None:
    schedule = _schedule()
    assert schedule.effective_date.isoformat() == "2026-07-07"
    assert not hasattr(schedule, "effective_at")
    result = module._resolve_fee_authority(
        datetime(2026, 7, 7, 1, tzinfo=UTC), schedule, _changes([])
    )
    assert result.status is module.FeeAuthorityStatus.INCOMPLETE


def test_unsupported_economic_case_remains_incomplete() -> None:
    result = module._resolve_fee_authority(NOW, _schedule(), _changes([]))
    assert result.status is module.FeeAuthorityStatus.INCOMPLETE


def test_mutation_fails_provenance_validation() -> None:
    evidence = _issued(module.PDF_URL, _pdf(), "application/pdf")
    original = evidence.body
    object.__setattr__(evidence, "body", b"changed")
    with pytest.raises(module.FeeAuthorityError):
        module._parse_fee_schedule(evidence)
    object.__setattr__(evidence, "body", original)


def test_public_fixed_acquisition_is_incomplete_without_override_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = _issued(module.PDF_URL, _pdf(), "application/pdf")
    changes = _issued(module.FEE_CHANGES_URL, b'{"series_fee_change_arr":[]}', "application/json")
    monkeypatch.setattr(
        module, "_fetch", lambda url, *, accept: pdf if url == module.PDF_URL else changes
    )
    result = module.acquire_fee_authority(NOW)
    assert result.status is module.FeeAuthorityStatus.INCOMPLETE
    assert result.policy is None


def test_issued_incomplete_authority_evidence_accessors_revalidate_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = _issued(module.PDF_URL, _pdf(), "application/pdf")
    changes = _issued(module.FEE_CHANGES_URL, b'{"series_fee_change_arr":[]}', "application/json")
    monkeypatch.setattr(
        module, "_fetch", lambda url, *, accept: pdf if url == module.PDF_URL else changes
    )
    authority = module.acquire_fee_authority(NOW)
    object.__setattr__(pdf, "body", b"changed")
    with pytest.raises(module.FeeAuthorityError):
        _ = authority.pdf_evidence
    object.__setattr__(pdf, "body", _pdf())
    object.__setattr__(changes, "body", b"changed")
    with pytest.raises(module.FeeAuthorityError):
        _ = authority.fee_change_evidence
    with pytest.raises(module.FeeAuthorityError):
        authority.calculate_one_contract(Decimal("0.50"))


def test_public_constructor_is_not_an_issuer() -> None:
    with pytest.raises(TypeError):
        module.FeeAuthority(status=module.FeeAuthorityStatus.COMPLETE)  # type: ignore[call-arg]
