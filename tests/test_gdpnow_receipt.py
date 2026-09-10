from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

import services.forecasting.gdpnow_parsing as parsing
import services.forecasting.gdpnow_source_acquisition as acquisition


class FakeResponse:
    def __init__(self, *, body: bytes) -> None:
        self.status = 200
        self.body = body

    def read(self, limit: int) -> bytes:
        return self.body

    def getheaders(self) -> list[tuple[str, str]]:
        return []


class FakeConnection:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response

    def request(self, method: str, path: str, *, headers: dict[str, str]) -> None:
        pass

    def getresponse(self) -> FakeResponse:
        return self.response

    def close(self) -> None:
        pass


_BODY = (
    b"<!doctype html><html><body><div><h2>September 3, 2026</h2>"
    b"<p>The GDPNow model estimate for real GDP growth (seasonally adjusted "
    b"annual rate) in the third quarter of 2026 is <strong>4.7 percent</strong> "
    b"on September 3, <strong>down from 4.8 percent</strong> on September 1.</p>"
    b"</div></body></html>"
)


def _evidence_and_vintage(
    monkeypatch: pytest.MonkeyPatch, body: bytes = _BODY
) -> tuple[acquisition.GDPNowAcquisitionEvidence, parsing.ParsedGDPNowVintage]:
    connection = FakeConnection(FakeResponse(body=body))
    monkeypatch.setattr(
        acquisition.http.client,
        "HTTPSConnection",
        lambda host, *, timeout, context: connection,
    )
    evidence = acquisition.acquire_gdpnow_commentary_page()
    vintage = parsing.parse_gdpnow_commentary(evidence)
    return evidence, vintage


def _valid_kwargs(
    evidence: acquisition.GDPNowAcquisitionEvidence, vintage: parsing.ParsedGDPNowVintage
) -> dict[str, object]:
    """The exact public kwargs a genuine receipt would be built from, minus capability."""
    return {
        "acquisition_evidence_id": evidence.content_hash,
        "raw_body_sha256": evidence.raw_body_sha256,
        "byte_count": evidence.byte_count,
        "acquired_at": evidence.acquired_at.isoformat(),
        "transport_policy_identity": evidence.transport_policy_identity,
        "parser_version": vintage.parser_version,
        "target_quarter": vintage.target_quarter,
        "gdpnow_value": str(vintage.gdpnow_value),
        "publisher_stated_date": vintage.publisher_stated_date.isoformat(),
        "research_only": True,
        "production_influence": "0",
    }


def test_receipt_binds_all_required_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    evidence, vintage = _evidence_and_vintage(monkeypatch)
    receipt = parsing.build_gdpnow_capture_receipt(evidence, vintage)
    assert receipt.acquisition_evidence_id == evidence.content_hash
    assert receipt.raw_body_sha256 == evidence.raw_body_sha256
    assert receipt.byte_count == evidence.byte_count
    assert receipt.acquired_at == evidence.acquired_at.isoformat()
    assert receipt.transport_policy_identity == acquisition.TRANSPORT_POLICY_IDENTITY
    assert receipt.parser_version == parsing.PARSER_VERSION
    assert receipt.target_quarter == "2026-Q3"
    assert receipt.gdpnow_value == "4.7"
    assert receipt.publisher_stated_date == "2026-09-03"
    assert receipt.research_only is True
    assert receipt.production_influence == "0"
    parsing.validate_gdpnow_capture_receipt(receipt)


def test_receipt_rejects_mismatched_evidence_and_vintage_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_a, _ = _evidence_and_vintage(monkeypatch, body=_BODY)
    other_body = _BODY.replace(
        b"4.7 percent</strong> on September 3", b"4.9 percent</strong> on September 3"
    )
    _, vintage_b = _evidence_and_vintage(monkeypatch, body=other_body)
    with pytest.raises(parsing.GDPNowParsingError, match="does not bind"):
        parsing.build_gdpnow_capture_receipt(evidence_a, vintage_b)


# ---------------------------------------------------------------------------
# Blocker 1 regression: forged capture receipts.
#
# Every test below proves that a caller who controls only public inputs -- no
# access to the module-private issuance capability -- cannot mint or persist a
# GDPNowCaptureReceipt that validates or is accepted for persistence. Each of
# these attacks previously succeeded against reviewed head
# 6e4916af4ac9846c5c28f22b1170c13ff66d5e0d.
# ---------------------------------------------------------------------------


def test_public_construction_of_self_consistent_forged_receipt_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence, vintage = _evidence_and_vintage(monkeypatch)
    with pytest.raises(parsing.GDPNowParsingError, match="capability"):
        parsing.GDPNowCaptureReceipt(**_valid_kwargs(evidence, vintage))  # type: ignore[call-arg]


def test_recomputed_receipt_id_still_fails_without_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even a caller who knows and recomputes the exact digest formula cannot
    mint a receipt through the public constructor -- receipt_id is never an
    accepted argument at all."""
    evidence, vintage = _evidence_and_vintage(monkeypatch)
    kwargs = _valid_kwargs(evidence, vintage)
    assert "receipt_id" not in parsing.GDPNowCaptureReceipt.__init__.__annotations__
    with pytest.raises(parsing.GDPNowParsingError, match="capability"):
        parsing.GDPNowCaptureReceipt(**kwargs)  # type: ignore[call-arg]


def test_caller_authored_raw_bytes_with_matching_sha_cannot_be_persisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The writer no longer accepts raw bytes at all: only `evidence.raw_body`
    (already bound and re-validated) is ever persisted."""
    evidence, vintage = _evidence_and_vintage(monkeypatch)
    forged_bytes = b"caller-authored bytes, never acquired"
    forged_sha = hashlib.sha256(forged_bytes).hexdigest()
    assert forged_sha != evidence.raw_body_sha256
    assert "raw_body" not in parsing.write_gdpnow_capture_receipt.__annotations__
    with pytest.raises(TypeError):
        parsing.write_gdpnow_capture_receipt(  # type: ignore[call-arg]
            evidence, vintage, forged_bytes, directory=tmp_path
        )


def test_forged_acquired_at_field_fails_receipt_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence, vintage = _evidence_and_vintage(monkeypatch)
    receipt = parsing.build_gdpnow_capture_receipt(evidence, vintage)
    old = receipt.acquired_at
    try:
        object.__setattr__(receipt, "acquired_at", datetime(2020, 1, 1, tzinfo=UTC).isoformat())
        with pytest.raises(parsing.GDPNowParsingError):
            parsing.validate_gdpnow_capture_receipt(receipt)
    finally:
        object.__setattr__(receipt, "acquired_at", old)
    parsing.validate_gdpnow_capture_receipt(receipt)


def test_forged_parser_version_fails_semantic_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence, vintage = _evidence_and_vintage(monkeypatch)
    kwargs = _valid_kwargs(evidence, vintage)
    kwargs["parser_version"] = "attacker-controlled-parser-v0"
    with pytest.raises(parsing.GDPNowParsingError, match="capability"):
        parsing.GDPNowCaptureReceipt(**kwargs)  # type: ignore[call-arg]
    # Even bypassing the capability check entirely (module-internal access),
    # the semantic validator alone must reject a non-canonical parser version.
    with pytest.raises(parsing.GDPNowParsingError, match="parser_version"):
        parsing._validate_receipt_semantic_fields(kwargs)


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("target_quarter", "2026-Q5", "target_quarter"),
        ("target_quarter", "26-Q1", "target_quarter"),
        ("gdpnow_value", "04.7", "gdpnow_value"),
        ("gdpnow_value", "+4.7", "gdpnow_value"),
        ("gdpnow_value", "not-a-number", "gdpnow_value"),
        ("publisher_stated_date", "2026-13-40", "publisher_stated_date"),
        ("publisher_stated_date", "09/03/2026", "publisher_stated_date"),
        ("transport_policy_identity", "forged-policy-identity", "transport_policy_identity"),
        ("acquisition_evidence_id", "not-a-sha256", "acquisition_evidence_id"),
        ("raw_body_sha256", "0" * 63, "raw_body_sha256"),
        ("byte_count", 0, "byte_count"),
        ("byte_count", -1, "byte_count"),
        ("research_only", False, "research_only"),
        ("production_influence", "1", "production_influence"),
    ],
)
def test_malformed_semantic_field_fails_closed(
    field: str, value: object, match: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence, vintage = _evidence_and_vintage(monkeypatch)
    kwargs = _valid_kwargs(evidence, vintage)
    kwargs[field] = value
    with pytest.raises(parsing.GDPNowParsingError, match=match):
        parsing._validate_receipt_semantic_fields(kwargs)


def test_mismatched_acquisition_evidence_id_is_syntactically_valid_but_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence, vintage = _evidence_and_vintage(monkeypatch)
    kwargs = _valid_kwargs(evidence, vintage)
    kwargs["acquisition_evidence_id"] = "f" * 64  # syntactically valid, but not real
    fields = parsing._validate_receipt_semantic_fields(kwargs)
    # Semantic shape passes (it's a well-formed sha256 hex string), but this can
    # never be paired with genuine evidence: build_gdpnow_capture_receipt always
    # derives acquisition_evidence_id from evidence.content_hash itself, so a
    # mismatched id can only arise from bypassing that issuer entirely -- which
    # the capability check already forecloses (see tests above).
    assert fields["acquisition_evidence_id"] == "f" * 64


def test_receipt_paired_with_raw_bytes_from_different_acquisition_is_structurally_impossible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence_a, vintage_a = _evidence_and_vintage(monkeypatch, body=_BODY)
    other_body = _BODY.replace(b"September 3, 2026", b"September 5, 2026").replace(
        b"on September 3,", b"on September 5,"
    )
    evidence_b, _vintage_b = _evidence_and_vintage(monkeypatch, body=other_body)

    # write_gdpnow_capture_receipt(evidence, vintage, ...) always persists
    # evidence.raw_body for that exact evidence -- there is no parameter through
    # which raw bytes from evidence_b could be attached to evidence_a's receipt.
    path_a = parsing.write_gdpnow_capture_receipt(evidence_a, vintage_a, directory=tmp_path)
    raw_dir_contents = {p.name for p in (tmp_path / "raw").iterdir()}
    assert f"{evidence_a.raw_body_sha256}.html" in raw_dir_contents
    assert f"{evidence_b.raw_body_sha256}.html" not in raw_dir_contents
    assert path_a.exists()


def test_dataclasses_replace_on_receipt_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    evidence, vintage = _evidence_and_vintage(monkeypatch)
    receipt = parsing.build_gdpnow_capture_receipt(evidence, vintage)
    with pytest.raises((TypeError, parsing.GDPNowParsingError)):
        replace(receipt, gdpnow_value="9.9")


def test_reconstructed_receipt_object_fails_revalidation() -> None:
    forged = object.__new__(parsing.GDPNowCaptureReceipt)
    with pytest.raises((AttributeError, parsing.GDPNowParsingError)):
        parsing.validate_gdpnow_capture_receipt(forged)


def test_mutated_and_rehashed_receipt_fails_issuance_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence, vintage = _evidence_and_vintage(monkeypatch)
    receipt = parsing.build_gdpnow_capture_receipt(evidence, vintage)
    old_value, old_id = receipt.gdpnow_value, receipt.receipt_id
    try:
        object.__setattr__(receipt, "gdpnow_value", "9.9")
        fields = parsing._validate_receipt_semantic_fields(
            {name: getattr(receipt, name) for name in parsing._RECEIPT_FIELD_NAMES}
        )
        redigest = parsing._receipt_digest_values(fields)
        object.__setattr__(receipt, "receipt_id", redigest)
        with pytest.raises(parsing.GDPNowParsingError, match="unissued"):
            parsing.validate_gdpnow_capture_receipt(receipt)
    finally:
        object.__setattr__(receipt, "gdpnow_value", old_value)
        object.__setattr__(receipt, "receipt_id", old_id)
    parsing.validate_gdpnow_capture_receipt(receipt)


def test_persistence_attempt_using_forged_receipt_object_type_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The writer's signature no longer accepts a GDPNowCaptureReceipt at all --
    passing one where `vintage` is expected fails on type, closing the exact
    reviewed-head attack where a hand-built receipt could be handed straight to
    the persistence API."""
    evidence, vintage = _evidence_and_vintage(monkeypatch)
    receipt = parsing.build_gdpnow_capture_receipt(evidence, vintage)
    with pytest.raises((TypeError, AttributeError, parsing.GDPNowParsingError)):
        parsing.write_gdpnow_capture_receipt(evidence, receipt, directory=tmp_path)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# End Blocker 1 regression tests.
# ---------------------------------------------------------------------------


def test_write_receipt_is_content_addressed_and_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence, vintage = _evidence_and_vintage(monkeypatch)
    receipt = parsing.build_gdpnow_capture_receipt(evidence, vintage)

    path_first = parsing.write_gdpnow_capture_receipt(evidence, vintage, directory=tmp_path)
    assert path_first.exists()
    payload = json.loads(path_first.read_text(encoding="utf-8"))
    assert payload["receipt_id"] == receipt.receipt_id
    assert payload["research_only"] is True
    assert payload["production_influence"] == "0"

    raw_path = tmp_path / "raw" / f"{receipt.raw_body_sha256}.html"
    assert raw_path.read_bytes() == evidence.raw_body

    # Re-writing identical content is a no-op, not an overwrite error.
    path_second = parsing.write_gdpnow_capture_receipt(evidence, vintage, directory=tmp_path)
    assert path_second == path_first

    # No mutable "latest.json" is ever created.
    assert not (tmp_path / "latest.json").exists()


def test_write_receipt_refuses_overwrite_with_different_content_at_same_path(
    tmp_path: Path,
) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    fake_sha = "0" * 64
    (raw_dir / f"{fake_sha}.html").write_bytes(b"original content")
    with pytest.raises(parsing.GDPNowParsingError, match="refusing to overwrite"):
        parsing._write_content_addressed(raw_dir / f"{fake_sha}.html", b"different content")


def test_two_captures_produce_two_distinct_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence_a, vintage_a = _evidence_and_vintage(monkeypatch, body=_BODY)
    other_body = _BODY.replace(b"September 3, 2026", b"September 5, 2026").replace(
        b"on September 3,", b"on September 5,"
    )
    evidence_b, vintage_b = _evidence_and_vintage(monkeypatch, body=other_body)

    receipt_a = parsing.build_gdpnow_capture_receipt(evidence_a, vintage_a)
    receipt_b = parsing.build_gdpnow_capture_receipt(evidence_b, vintage_b)
    assert receipt_a.receipt_id != receipt_b.receipt_id

    path_a = parsing.write_gdpnow_capture_receipt(evidence_a, vintage_a, directory=tmp_path)
    path_b = parsing.write_gdpnow_capture_receipt(evidence_b, vintage_b, directory=tmp_path)
    assert path_a != path_b
    assert len(list((tmp_path / "receipts").glob("*.json"))) == 2


# ---------------------------------------------------------------------------
# Durable reopen semantics.
# ---------------------------------------------------------------------------


def test_reopen_verifies_a_genuinely_persisted_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence, vintage = _evidence_and_vintage(monkeypatch)
    receipt = parsing.build_gdpnow_capture_receipt(evidence, vintage)
    parsing.write_gdpnow_capture_receipt(evidence, vintage, directory=tmp_path)

    reopened = parsing.reopen_gdpnow_capture_receipt(tmp_path, receipt.receipt_id)
    assert reopened["target_quarter"] == "2026-Q3"
    assert reopened["gdpnow_value"] == "4.7"
    assert reopened["raw_body_sha256"] == evidence.raw_body_sha256


def test_reopen_rejects_tampered_receipt_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence, vintage = _evidence_and_vintage(monkeypatch)
    receipt = parsing.build_gdpnow_capture_receipt(evidence, vintage)
    parsing.write_gdpnow_capture_receipt(evidence, vintage, directory=tmp_path)

    receipt_path = tmp_path / "receipts" / f"{receipt.receipt_id}.json"
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["gdpnow_value"] = "9.9"
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(parsing.GDPNowParsingError, match="reopen verification"):
        parsing.reopen_gdpnow_capture_receipt(tmp_path, receipt.receipt_id)


def test_reopen_rejects_tampered_raw_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence, vintage = _evidence_and_vintage(monkeypatch)
    receipt = parsing.build_gdpnow_capture_receipt(evidence, vintage)
    parsing.write_gdpnow_capture_receipt(evidence, vintage, directory=tmp_path)

    raw_path = tmp_path / "raw" / f"{evidence.raw_body_sha256}.html"
    raw_path.write_bytes(b"tampered raw artifact bytes")

    with pytest.raises(parsing.GDPNowParsingError, match="does not match its bound SHA-256"):
        parsing.reopen_gdpnow_capture_receipt(tmp_path, receipt.receipt_id)


def test_reopen_does_not_claim_authorship_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hand-authored, self-consistent receipt+artifact pair (never issued by
    build_gdpnow_capture_receipt) reopens successfully: reopen only checks
    internal consistency, never issuance provenance, and its docstring says so."""
    assert "does not and cannot prove" in (parsing.reopen_gdpnow_capture_receipt.__doc__ or "")

    evidence, vintage = _evidence_and_vintage(monkeypatch)
    fields = parsing._validate_receipt_semantic_fields(_valid_kwargs(evidence, vintage))
    receipt_id = parsing._receipt_digest_values(fields)
    payload = {**fields, "receipt_id": receipt_id, "schema_version": parsing.RECEIPT_SCHEMA_VERSION}

    (tmp_path / "receipts").mkdir()
    (tmp_path / "raw").mkdir()
    (tmp_path / "receipts" / f"{receipt_id}.json").write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "raw" / f"{evidence.raw_body_sha256}.html").write_bytes(evidence.raw_body)

    reopened = parsing.reopen_gdpnow_capture_receipt(tmp_path, receipt_id)
    assert reopened["receipt_id"] == receipt_id
