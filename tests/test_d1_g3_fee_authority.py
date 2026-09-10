"""Network-free tests for the prospective KXGDP fee authority."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from services.production_gdp_strategy import fee_authority as module

NOW = datetime(2026, 9, 10, 21, tzinfo=UTC)


def _pdf(*, row: str = "KXGDP 1 1", suffix: str = "") -> bytes:
    text = (
        "%PDF-1.7\nKalshi Fee Schedule\nLast updated and effective: July 7, 2026\n"
        "fees = round up (M * 0.07 * C * P * (1-P))\n"
        "fees = round up (M * 0.0175 * C * P * (1-P))\n"
        "P is contract price in dollars and C is contract quantity\n"
        "Maker multiplier Taker multiplier " + row + "\n" + suffix + "\n%%EOF"
    )
    return text.encode()


def _evidence(url: str, body: bytes, content_type: str) -> module.RawEvidence:
    return module.RawEvidence(url, 200, content_type, NOW, body, hashlib.sha256(body).hexdigest())


def _schedule(body: bytes | None = None) -> module.FeeSchedule:
    return module.parse_fee_schedule(
        _evidence(module.PDF_URL, _pdf() if body is None else body, "application/pdf")
    )


def _changes(records: list[dict[str, object]]) -> tuple[module.FeeChange, ...]:
    body = json.dumps({"fee_changes": records}, separators=(",", ":")).encode()
    return module.parse_fee_changes(_evidence(module.FEE_CHANGES_URL, body, "application/json"))


def test_audited_july_fixture_issues_marketable_taker_authority() -> None:
    result = module.resolve_fee_authority(NOW, _schedule(), ())
    assert result.status is module.FeeAuthorityStatus.COMPLETE
    assert result.policy is not None
    assert result.policy.fee_type is module.FeeType.QUADRATIC
    assert result.policy.fee_multiplier == Decimal("1")
    assert result.policy.effective_at == datetime(2026, 7, 7, tzinfo=UTC)
    assert result.resolver_id == module.RESOLVER_ID
    assert [
        result.calculate_one_contract(p).total_fee
        for p in (Decimal("0.01"), Decimal("0.50"), Decimal("0.99"))
    ] == [Decimal("0.0007"), Decimal("0.0175"), Decimal("0.0007")]


def test_exact_rounding_boundary_is_ceiling_not_nearest() -> None:
    result = module.resolve_fee_authority(NOW, _schedule(), ())
    assert result.calculate_one_contract(Decimal("0.0001")).total_fee == Decimal("0.0001")
    assert result.calculate_one_contract(Decimal("0.0002")).total_fee == Decimal("0.0001")


@pytest.mark.parametrize(
    ("at", "expected"),
    [
        (datetime(2026, 9, 9, tzinfo=UTC), Decimal("1")),
        (datetime(2026, 9, 10, tzinfo=UTC), Decimal("2")),
    ],
)
def test_scheduled_change_time_resolution(at: datetime, expected: Decimal) -> None:
    changes = _changes(
        [
            {
                "change_id": "synthetic-future-change",
                "series_ticker": "KXGDP",
                "effective_at": "2026-09-10T00:00:00Z",
                "fee_type": "quadratic",
                "multiplier": "2",
            }
        ]
    )
    result = module.resolve_fee_authority(at, _schedule(), changes)
    assert result.status is module.FeeAuthorityStatus.COMPLETE
    assert result.policy is not None and result.policy.fee_multiplier == expected


def test_change_exactly_at_decision_applies() -> None:
    change = _changes(
        [
            {
                "change_id": "synthetic-boundary",
                "series_ticker": "KXGDP",
                "effective_at": NOW.isoformat(),
                "fee_type": "quadratic",
                "multiplier": "2",
            }
        ]
    )
    result = module.resolve_fee_authority(NOW, _schedule(), change)
    assert result.policy is not None and result.policy.fee_multiplier == Decimal("2")


def test_conflicting_overlapping_changes_fail_closed() -> None:
    changes = _changes(
        [
            {
                "change_id": "synthetic-a",
                "series_ticker": "KXGDP",
                "effective_at": "2026-08-01T00:00:00Z",
                "fee_type": "quadratic",
                "multiplier": "2",
            },
            {
                "change_id": "synthetic-b",
                "series_ticker": "KXGDP",
                "effective_at": "2026-08-02T00:00:00Z",
                "fee_type": "quadratic",
                "multiplier": "3",
            },
        ]
    )
    result = module.resolve_fee_authority(NOW, _schedule(), changes)
    assert result.status is module.FeeAuthorityStatus.INCOMPLETE


@pytest.mark.parametrize(
    "body",
    [
        _pdf(row="KXGDP 1 1 KXGDP 1 1"),
        _pdf(row="KXGDP 1"),
        _pdf(row="KXGNP 1 1"),
        _pdf().replace(b"July 7, 2026", b"tomorrow"),
        _pdf().replace(b"Maker multiplier", b"Maker columns"),
    ],
)
def test_pdf_identity_row_date_and_layout_are_strict(body: bytes) -> None:
    with pytest.raises(module.FeeAuthorityError):
        module.parse_fee_schedule(_evidence(module.PDF_URL, body, "application/pdf"))


@pytest.mark.parametrize(
    "status,content_type,body",
    [
        (429, "text/html", b"challenge"),
        (200, "text/html", b"<html>challenge</html>"),
        (302, "application/pdf", b"redirect"),
    ],
)
def test_pdf_transport_failures_are_incomplete(status: int, content_type: str, body: bytes) -> None:
    evidence = module.RawEvidence(module.PDF_URL, status, content_type, NOW, body, "x")
    with pytest.raises(module.FeeAuthorityError):
        module.parse_fee_schedule(evidence)


def test_empty_fee_change_array_is_valid_no_scheduled_change() -> None:
    assert _changes([]) == ()


def test_fee_change_binding_and_malformed_dates_fail_closed() -> None:
    for record in [
        {
            "series_ticker": "OTHER",
            "effective_at": "2026-08-01T00:00:00Z",
            "fee_type": "quadratic",
            "multiplier": "1",
        },
        {
            "series_ticker": "KXGDP",
            "effective_at": "not-a-date",
            "fee_type": "quadratic",
            "multiplier": "1",
        },
    ]:
        with pytest.raises(module.FeeAuthorityError):
            _changes([record])


def test_reconstructed_or_substituted_body_cannot_claim_issued_hash() -> None:
    evidence = _evidence(module.PDF_URL, _pdf(), "application/pdf")
    tampered = module.RawEvidence(
        evidence.source_url,
        evidence.status,
        evidence.content_type,
        evidence.acquired_at,
        evidence.body.replace(b"KXGDP 1 1", b"KXGDP 0 0"),
        evidence.body_sha256,
    )
    with pytest.raises(module.FeeAuthorityError, match="hash mismatch"):
        module.parse_fee_schedule(tampered)


def test_public_api_owns_acquisition_and_returns_fixed_source_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = _evidence(module.PDF_URL, _pdf(), "application/pdf")
    changes = _evidence(
        module.FEE_CHANGES_URL,
        b'{"fee_changes":[]}',
        "application/json",
    )

    def fake_fetch(url: str, *, accept: str) -> module.RawEvidence:
        assert url in {module.PDF_URL, module.FEE_CHANGES_URL}
        assert accept in {"application/pdf", "application/json"}
        return pdf if url == module.PDF_URL else changes

    monkeypatch.setattr(module, "_fetch", fake_fetch)
    result = module.acquire_fee_authority(NOW)
    assert result.status is module.FeeAuthorityStatus.COMPLETE
    assert result.pdf_evidence is pdf and result.fee_change_evidence is changes
    assert module.__all__ == ["FeeAuthority", "FeeAuthorityStatus", "acquire_fee_authority"]
