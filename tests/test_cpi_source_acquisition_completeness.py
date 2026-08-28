from __future__ import annotations

import http.client
from collections.abc import Callable

import pytest

import services.forecasting.cpi_evidence_issuer as issuer
import services.forecasting.cpi_pit_availability as pit
import services.forecasting.cpi_source_acquisition as acquisition

LOCATOR = "https://www.bls.gov/news.release/archives/cpi_08122025.htm"


def valid_embargo_html() -> bytes:
    return (
        "<!doctype html><html><body><p>Transmission of material in this release is "
        "embargoed until 8:30 a.m. (ET) Tuesday, August 12, 2025</p></body></html>"
    ).encode("ascii")


class FakeResponse:
    def __init__(
        self,
        *,
        body: bytes,
        length: int | None,
        status: int = 200,
        read_error: http.client.HTTPException | None = None,
    ) -> None:
        self.body = body
        self.length = length
        self.status = status
        self.read_error = read_error
        self.read_calls = 0

    def read(self, limit: int) -> bytes:
        assert limit == acquisition.MAX_RESPONSE_BYTES + 1
        self.read_calls += 1
        if self.read_error is not None:
            raise self.read_error
        return self.body

    def getheaders(self) -> list[tuple[str, str]]:
        return []


class FakeConnection:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.closed = False
        self.requested: tuple[str, str, dict[str, str]] | None = None

    def request(self, method: str, path: str, *, headers: dict[str, str]) -> None:
        self.requested = (method, path, headers)

    def getresponse(self) -> FakeResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


def install_response(
    monkeypatch: pytest.MonkeyPatch,
    response: FakeResponse,
) -> FakeConnection:
    connection = FakeConnection(response)

    def factory(host: str, *, timeout: float, context: object) -> FakeConnection:
        del context
        assert host == acquisition.BLS_HOST
        assert timeout == acquisition.TIMEOUT_SECONDS
        return connection

    monkeypatch.setattr(acquisition.http.client, "HTTPSConnection", factory)
    return connection


def test_declared_content_length_exactly_matching_received_bytes_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = valid_embargo_html()
    response = FakeResponse(body=body, length=len(body))
    connection = install_response(monkeypatch, response)

    evidence = acquisition.acquire_bls_cpi_release(LOCATOR)

    acquisition.validate_cpi_bls_acquisition_evidence(evidence)
    assert evidence.raw_body == body
    assert evidence.byte_count == len(body)
    assert response.read_calls == 1
    assert connection.closed is True


def test_declared_content_length_larger_than_received_bytes_is_rejected_as_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = valid_embargo_html()
    response = FakeResponse(body=body, length=len(body) + 97)
    connection = install_response(monkeypatch, response)
    issued_before = len(acquisition._ISSUED_ACQUISITION_FINGERPRINTS)

    with pytest.raises(acquisition.CPISourceAcquisitionError, match="truncated or incomplete"):
        acquisition.acquire_bls_cpi_release(LOCATOR)

    assert response.read_calls == 1
    assert connection.closed is True
    assert len(acquisition._ISSUED_ACQUISITION_FINGERPRINTS) == issued_before


def test_declared_content_length_over_size_cap_is_rejected_before_body_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = FakeResponse(body=b"not-read", length=acquisition.MAX_RESPONSE_BYTES + 1)
    connection = install_response(monkeypatch, response)
    issued_before = len(acquisition._ISSUED_ACQUISITION_FINGERPRINTS)

    with pytest.raises(acquisition.CPISourceAcquisitionError, match="declared.*bounded size"):
        acquisition.acquire_bls_cpi_release(LOCATOR)

    assert response.read_calls == 0
    assert connection.closed is True
    assert len(acquisition._ISSUED_ACQUISITION_FINGERPRINTS) == issued_before


def test_incomplete_chunked_transfer_fails_closed_without_positive_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    partial = valid_embargo_html()
    response = FakeResponse(
        body=partial,
        length=None,
        read_error=http.client.IncompleteRead(partial, expected=64),
    )
    connection = install_response(monkeypatch, response)
    issued_before = len(acquisition._ISSUED_ACQUISITION_FINGERPRINTS)

    with pytest.raises(acquisition.CPISourceAcquisitionError, match="bounded BLS HTTPS GET failed"):
        acquisition.acquire_bls_cpi_release(LOCATOR)

    assert response.read_calls == 1
    assert connection.closed is True
    assert len(acquisition._ISSUED_ACQUISITION_FINGERPRINTS) == issued_before


def test_valid_embargo_prefix_in_truncated_response_never_reaches_p3_or_p2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid_prefix = valid_embargo_html()
    response = FakeResponse(body=valid_prefix, length=len(valid_prefix) + 256)
    install_response(monkeypatch, response)
    issued_before = len(acquisition._ISSUED_ACQUISITION_FINGERPRINTS)
    parser_called = False
    p2_called = False

    def forbidden_parser(*args: object, **kwargs: object) -> object:
        nonlocal parser_called
        parser_called = True
        raise AssertionError("truncated response reached P3")

    original_p2: Callable[..., object] = pit._issue_actual_cpi_publication_evidence

    def forbidden_p2(*args: object, **kwargs: object) -> object:
        nonlocal p2_called
        p2_called = True
        return original_p2(*args, **kwargs)

    monkeypatch.setattr(issuer, "parse_cpi_publication_timing", forbidden_parser)
    monkeypatch.setattr(pit, "_issue_actual_cpi_publication_evidence", forbidden_p2)

    with pytest.raises(acquisition.CPISourceAcquisitionError, match="truncated or incomplete"):
        issuer.acquire_and_issue_cpi_evidence(LOCATOR)

    assert parser_called is False
    assert p2_called is False
    assert response.read_calls == 1
    assert len(acquisition._ISSUED_ACQUISITION_FINGERPRINTS) == issued_before
