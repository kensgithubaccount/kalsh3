from __future__ import annotations

import json
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


def test_receipt_tampering_fails_revalidation(monkeypatch: pytest.MonkeyPatch) -> None:
    evidence, vintage = _evidence_and_vintage(monkeypatch)
    receipt = parsing.build_gdpnow_capture_receipt(evidence, vintage)
    tampered = parsing.GDPNowCaptureReceipt(
        receipt_id=receipt.receipt_id,
        schema_version=receipt.schema_version,
        acquisition_evidence_id=receipt.acquisition_evidence_id,
        raw_body_sha256=receipt.raw_body_sha256,
        byte_count=receipt.byte_count,
        acquired_at=receipt.acquired_at,
        transport_policy_identity=receipt.transport_policy_identity,
        parser_version=receipt.parser_version,
        target_quarter=receipt.target_quarter,
        gdpnow_value="9.9",
        publisher_stated_date=receipt.publisher_stated_date,
        research_only=receipt.research_only,
        production_influence=receipt.production_influence,
    )
    with pytest.raises(parsing.GDPNowParsingError, match="revalidation"):
        parsing.validate_gdpnow_capture_receipt(tampered)


def test_write_receipt_is_content_addressed_and_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence, vintage = _evidence_and_vintage(monkeypatch)
    receipt = parsing.build_gdpnow_capture_receipt(evidence, vintage)

    path_first = parsing.write_gdpnow_capture_receipt(
        receipt, evidence.raw_body, directory=tmp_path
    )
    assert path_first.exists()
    payload = json.loads(path_first.read_text(encoding="utf-8"))
    assert payload["receipt_id"] == receipt.receipt_id
    assert payload["research_only"] is True
    assert payload["production_influence"] == "0"

    raw_path = tmp_path / "raw" / f"{receipt.raw_body_sha256}.html"
    assert raw_path.read_bytes() == evidence.raw_body

    # Re-writing identical content is a no-op, not an overwrite error.
    path_second = parsing.write_gdpnow_capture_receipt(
        receipt, evidence.raw_body, directory=tmp_path
    )
    assert path_second == path_first

    # No mutable "latest.json" is ever created.
    assert not (tmp_path / "latest.json").exists()


def test_write_receipt_refuses_raw_bytes_not_matching_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence, vintage = _evidence_and_vintage(monkeypatch)
    receipt = parsing.build_gdpnow_capture_receipt(evidence, vintage)
    with pytest.raises(parsing.GDPNowParsingError, match="do not match"):
        parsing.write_gdpnow_capture_receipt(
            receipt, b"different bytes entirely", directory=tmp_path
        )


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

    path_a = parsing.write_gdpnow_capture_receipt(
        receipt_a, evidence_a.raw_body, directory=tmp_path
    )
    path_b = parsing.write_gdpnow_capture_receipt(
        receipt_b, evidence_b.raw_body, directory=tmp_path
    )
    assert path_a != path_b
    assert len(list((tmp_path / "receipts").glob("*.json"))) == 2
