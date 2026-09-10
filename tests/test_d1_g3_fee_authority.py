"""Network-free tests for the prospective KXGDP fee authority."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from services.opportunity_engine.fees import FeeType, calculate_fee
from services.production_gdp_strategy import fee_authority as module

NOW = datetime(2026, 9, 10, 21, tzinfo=UTC)


def _pdf(*, row: str = "KXGDP 1 1", suffix: str = "") -> bytes:
    text = (
        "%PDF-1.7\nKalshi Fee Schedule\nLast updated and effective: July 7, 2026\n"
        "The current general fee is determined by the following formula.\n"
        "fees = round up (M * 0.07 * C * P * (1-P))\n"
        "Maker Fees\n"
        "fees = round up (M * 0.0175 * C * P * (1-P))\n"
        "P is contract price in dollars and C is contract quantity\n"
        "Maker multiplier Taker multiplier " + row + "\n" + suffix + "\n%%EOF"
    )
    return text.encode()


def _evidence(url: str, body: bytes, content_type: str) -> module._RawEvidence:
    return module._RawEvidence(url, 200, content_type, NOW, body, hashlib.sha256(body).hexdigest())


def _schedule(body: bytes | None = None) -> module._FeeSchedule:
    return module._parse_fee_schedule(
        _evidence(module.PDF_URL, _pdf() if body is None else body, "application/pdf")
    )


def _changes(records: list[dict[str, object]]) -> module._FeeChangeBatch:
    body = json.dumps({"series_fee_change_arr": records}, separators=(",", ":")).encode()
    return module._parse_fee_changes(_evidence(module.FEE_CHANGES_URL, body, "application/json"))


def _record(
    *,
    change_id: str = "change-a",
    series_ticker: str = "KXGDP",
    scheduled_ts: str = "2026-08-01T00:00:00Z",
    fee_type: str = "quadratic",
    fee_multiplier: str = "2",
) -> dict[str, object]:
    return {
        "id": change_id,
        "series_ticker": series_ticker,
        "scheduled_ts": scheduled_ts,
        "fee_type": fee_type,
        "fee_multiplier": fee_multiplier,
    }


def test_audited_july_fixture_issues_marketable_taker_authority() -> None:
    result = module._resolve_fee_authority(NOW, _schedule(), _changes([]))
    assert result.status is module.FeeAuthorityStatus.COMPLETE
    assert result.policy is not None
    assert result.policy.fee_type is FeeType.QUADRATIC
    assert result.policy.fee_multiplier == Decimal("1")
    assert result.policy.effective_at == datetime(2026, 7, 7, tzinfo=UTC)
    assert [
        calculate_fee(result.policy, p, Decimal("1"), maker=False).total_fee
        for p in (Decimal("0.01"), Decimal("0.50"), Decimal("0.99"))
    ] == [Decimal("0.0007"), Decimal("0.0175"), Decimal("0.0007")]


def test_exact_rounding_boundary_is_ceiling_not_nearest() -> None:
    result = module._resolve_fee_authority(NOW, _schedule(), _changes([]))
    assert result.policy is not None
    assert calculate_fee(result.policy, Decimal("0.0001"), Decimal("1")).total_fee == Decimal(
        "0.0001"
    )
    assert calculate_fee(result.policy, Decimal("0.0002"), Decimal("1")).total_fee == Decimal(
        "0.0001"
    )


@pytest.mark.parametrize(
    ("at", "expected"),
    [
        (datetime(2026, 7, 31, tzinfo=UTC), Decimal("1")),
        (datetime(2026, 8, 1, tzinfo=UTC), Decimal("2")),
    ],
)
def test_scheduled_change_time_resolution(at: datetime, expected: Decimal) -> None:
    result = module._resolve_fee_authority(at, _schedule(), _changes([_record()]))
    assert result.status is module.FeeAuthorityStatus.COMPLETE
    assert result.policy is not None and result.policy.fee_multiplier == expected


def test_change_exactly_at_decision_applies() -> None:
    result = module._resolve_fee_authority(
        NOW, _schedule(), _changes([_record(scheduled_ts=NOW.isoformat())])
    )
    assert result.policy is not None and result.policy.fee_multiplier == Decimal("2")


def test_conflicting_overlapping_changes_fail_closed() -> None:
    result = module._resolve_fee_authority(
        NOW,
        _schedule(),
        _changes(
            [
                _record(change_id="a", fee_multiplier="2"),
                _record(change_id="b", scheduled_ts="2026-08-01T00:00:00Z", fee_multiplier="3"),
            ]
        ),
    )
    assert result.status is module.FeeAuthorityStatus.INCOMPLETE


@pytest.mark.parametrize(
    "body",
    [
        _pdf(row="KXGDP 1 1 KXGDP 1 1"),
        _pdf(row="KXGDP 1"),
        _pdf(row="KXGNP 1 1"),
        _pdf().replace(b"July 7, 2026", b"tomorrow"),
        _pdf().replace(b"Maker multiplier", b"Taker multiplier Maker multiplier"),
        _pdf(suffix="fees = round up (M * 0.07 * C * P * (1-P))"),
        _pdf(suffix="Last updated and effective: August 1, 2026"),
    ],
)
def test_pdf_identity_semantics_and_layout_are_strict(body: bytes) -> None:
    with pytest.raises(module.FeeAuthorityError):
        module._parse_fee_schedule(_evidence(module.PDF_URL, body, "application/pdf"))


def test_pdf_coefficients_are_derived_and_match_reviewed_semantics() -> None:
    parsed = module._parse_fee_schedule(_evidence(module.PDF_URL, _pdf(), "application/pdf"))
    assert parsed.taker_coefficient == Decimal("0.07")
    assert parsed.maker_coefficient == Decimal("0.0175")
    with pytest.raises(module.FeeAuthorityError):
        module._parse_fee_schedule(
            _evidence(module.PDF_URL, _pdf().replace(b"0.07", b"0.08"), "application/pdf")
        )


def test_pdf_ascii_multiplication_layout_is_deterministic() -> None:
    body = _pdf().replace(b"*", b"x")
    parsed = module._parse_fee_schedule(_evidence(module.PDF_URL, body, "application/pdf"))
    assert parsed.taker_coefficient == Decimal("0.07")


@pytest.mark.parametrize(
    "status,content_type,body",
    [
        (429, "text/html", b"challenge"),
        (200, "text/html", b"<html>challenge</html>"),
        (302, "application/pdf", b"redirect"),
    ],
)
def test_pdf_transport_failures_are_incomplete(status: int, content_type: str, body: bytes) -> None:
    evidence = module._RawEvidence(module.PDF_URL, status, content_type, NOW, body, "x")
    with pytest.raises(module.FeeAuthorityError):
        module._parse_fee_schedule(evidence)


def test_empty_fee_change_array_is_valid_and_exhaustive() -> None:
    batch = _changes([])
    assert batch.records == ()
    assert batch.exhaustive_proven


@pytest.mark.parametrize(
    "record",
    [
        _record(series_ticker="OTHER"),
        _record(scheduled_ts="not-a-date"),
        _record(change_id=""),
        _record(fee_multiplier="NaN"),
        _record(fee_type="flat"),
    ],
)
def test_fee_change_binding_schema_and_values_fail_closed(record: dict[str, object]) -> None:
    with pytest.raises(module.FeeAuthorityError):
        _changes([record])


def test_duplicate_ids_and_same_effective_records_fail_closed() -> None:
    with pytest.raises(module.FeeAuthorityError, match="duplicated"):
        _changes(
            [
                _record(change_id="same"),
                _record(change_id="same", scheduled_ts="2026-09-01T00:00:00Z"),
            ]
        )
    result = module._resolve_fee_authority(
        NOW,
        _schedule(),
        _changes([_record(change_id="a"), _record(change_id="b")]),
    )
    assert result.status is module.FeeAuthorityStatus.INCOMPLETE


def test_ambiguous_fee_change_schema_fails_closed() -> None:
    body = json.dumps(
        {"series_fee_change_arr": [], "fee_changes": []}, separators=(",", ":")
    ).encode()
    with pytest.raises(module.FeeAuthorityError):
        module._parse_fee_changes(_evidence(module.FEE_CHANGES_URL, body, "application/json"))


def test_reconstructed_or_substituted_body_cannot_claim_issued_hash() -> None:
    evidence = _evidence(module.PDF_URL, _pdf(), "application/pdf")
    tampered = module._RawEvidence(
        evidence.source_url,
        evidence.status,
        evidence.content_type,
        evidence.acquired_at,
        evidence.body.replace(b"KXGDP 1 1", b"KXGDP 0 0"),
        evidence.body_sha256,
    )
    with pytest.raises(module.FeeAuthorityError, match="hash mismatch"):
        module._parse_fee_schedule(tampered)


def test_public_api_owns_fixed_acquisition(monkeypatch: pytest.MonkeyPatch) -> None:
    pdf = _evidence(module.PDF_URL, _pdf(), "application/pdf")
    changes = _evidence(module.FEE_CHANGES_URL, b'{"series_fee_change_arr":[]}', "application/json")

    def fake_fetch(url: str, *, accept: str) -> module._RawEvidence:
        assert url in {module.PDF_URL, module.FEE_CHANGES_URL}
        assert accept in {"application/pdf", "application/json"}
        return pdf if url == module.PDF_URL else changes

    monkeypatch.setattr(module, "_fetch", fake_fetch)
    result = module.acquire_fee_authority(NOW)
    assert result.status is module.FeeAuthorityStatus.COMPLETE
    assert result.pdf_evidence is pdf and result.fee_change_evidence is changes
    assert module.__all__ == ["FeeAuthority", "FeeAuthorityStatus", "acquire_fee_authority"]


def test_public_constructor_and_replace_attacks_fail() -> None:
    with pytest.raises(TypeError):
        module.FeeAuthority(
            status=module.FeeAuthorityStatus.COMPLETE,
            reason=None,
            policy=None,
            pdf_evidence=None,
            fee_change_evidence=None,
            applicable_change_ids=(),
            resolver_id="caller",
        )
    result = module._issue(
        module._resolve_fee_authority(NOW, _schedule(), _changes([])),
        _evidence(module.PDF_URL, _pdf(), "application/pdf"),
        _evidence(module.FEE_CHANGES_URL, b'{"series_fee_change_arr":[]}', "application/json"),
    )
    with pytest.raises(TypeError):
        replace(result, status=module.FeeAuthorityStatus.COMPLETE)  # type: ignore[type-var]
    with pytest.raises(AttributeError):
        result._status = module.FeeAuthorityStatus.COMPLETE


def test_ordinary_public_api_cannot_accept_authority_facts() -> None:
    assert not hasattr(module, "RawEvidence")
    assert not hasattr(module, "FeeSchedule")
    assert not hasattr(module, "FeeChange")
    assert not hasattr(module, "parse_fee_schedule")
    assert not hasattr(module, "parse_fee_changes")
    assert not hasattr(module, "resolve_fee_authority")
    with pytest.raises(TypeError):
        module.acquire_fee_authority(NOW, raw_pdf=b"caller-authored")  # type: ignore[call-arg]
    with pytest.raises(AttributeError):
        object.__new__(module.FeeAuthority).calculate_one_contract(Decimal("0.5"))


def test_currentness_and_override_completeness_are_required() -> None:
    schedule = replace(_schedule(), currentness_proven=False)
    batch = _changes([])
    result = module._resolve_fee_authority(NOW, schedule, batch)
    assert result.status is module.FeeAuthorityStatus.INCOMPLETE
    assert batch.exhaustive_proven
