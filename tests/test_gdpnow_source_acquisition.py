from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256

import pytest

import services.forecasting.gdpnow_source_acquisition as acquisition

LOCATOR = acquisition.SOURCE_LOCATOR
OTHER_LOCATOR = "https://www.atlantafed.org/other-page"


def html() -> bytes:
    return (
        b"<!doctype html><html><body><div><h2>September 3, 2026</h2>"
        b"<p>The GDPNow model estimate for real GDP growth (seasonally adjusted "
        b"annual rate) in the third quarter of 2026 is <strong>4.7 percent</strong> "
        b"on September 3, <strong>down from 4.8 percent</strong> on September 1.</p>"
        b"</div></body></html>"
    )


class FakeResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        body: bytes | None = None,
        headers: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self.status = status
        self.body = html() if body is None else body
        self.headers = headers

    def read(self, limit: int) -> bytes:
        assert limit == acquisition.MAX_RESPONSE_BYTES + 1
        return self.body

    def getheaders(self) -> list[tuple[str, str]]:
        return list(self.headers)


class FakeConnection:
    def __init__(self, response: FakeResponse, failure: BaseException | None = None) -> None:
        self.response = response
        self.failure = failure
        self.closed = False
        self.requested: tuple[str, str, dict[str, str]] | None = None

    def request(self, method: str, path: str, *, headers: dict[str, str]) -> None:
        self.requested = (method, path, headers)

    def getresponse(self) -> FakeResponse:
        if self.failure is not None:
            raise self.failure
        return self.response

    def close(self) -> None:
        self.closed = True


def acquire(
    *,
    status: int = 200,
    body: bytes | None = None,
    headers: tuple[tuple[str, str], ...] = (),
    failure: BaseException | None = None,
) -> acquisition.GDPNowAcquisitionEvidence:
    connection = FakeConnection(FakeResponse(status=status, body=body, headers=headers), failure)
    patch = pytest.MonkeyPatch()

    def factory(host: str, *, timeout: float, context: object) -> FakeConnection:
        del context
        assert host == acquisition.ATLANTA_FED_HOST
        assert timeout == acquisition.TIMEOUT_SECONDS
        return connection

    patch.setattr(acquisition.http.client, "HTTPSConnection", factory)
    try:
        evidence = acquisition.acquire_gdpnow_commentary_page()
    finally:
        patch.undo()
    assert connection.closed
    assert connection.requested is not None
    method, path, request_headers = connection.requested
    assert method == "GET"
    assert path == acquisition.ATLANTA_FED_PATH
    assert "?" not in path
    assert "Authorization" not in request_headers
    assert "Cookie" not in request_headers
    return evidence


def result(**changes: object) -> acquisition._GDPNowTransportResult:
    values: dict[str, object] = {
        "requested_locator": LOCATOR,
        "final_locator": LOCATOR,
        "method": "GET",
        "status": 200,
        "raw_body": html(),
        "acquired_at": datetime(2026, 9, 3, 17, 0, tzinfo=UTC),
        "diagnostic_headers": (),
    }
    values.update(changes)
    return acquisition._GDPNowTransportResult(**values)  # type: ignore[arg-type]


def test_public_api_takes_no_arguments_and_cannot_accept_caller_bytes_or_locator() -> None:
    assert tuple(inspect.signature(acquisition.acquire_gdpnow_commentary_page).parameters) == ()
    with pytest.raises(TypeError):
        acquisition.acquire_gdpnow_commentary_page(source_locator=LOCATOR)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        acquisition.acquire_gdpnow_commentary_page(raw_body=html())  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"requested_locator": OTHER_LOCATOR}, "request locator"),
        (
            {"requested_locator": "http://www.atlantafed.org" + acquisition.ATLANTA_FED_PATH},
            "request locator",
        ),
        (
            {"requested_locator": "https://example.com" + acquisition.ATLANTA_FED_PATH},
            "request locator",
        ),
        ({"requested_locator": LOCATOR + "?x=1"}, "request locator"),
        ({"final_locator": "https://example.com/gdpnow.htm"}, "off-origin"),
        ({"method": "POST"}, "GET"),
        ({"status": 503}, "non-success"),
        ({"status": 301}, "non-success"),
        ({"raw_body": b""}, "non-empty"),
        ({"raw_body": b"x" * (acquisition.MAX_RESPONSE_BYTES + 1)}, "bounded size"),
    ],
)
def test_transport_invariants_fail_closed(changes: dict[str, object], message: str) -> None:
    with pytest.raises(acquisition.GDPNowAcquisitionError, match=message):
        acquisition._validate_transport_result(result(**changes))


def test_transport_is_fixed_https_get_no_credentials_redirects_or_unbounded_io() -> None:
    headers = {name.casefold(): value for name, value in acquisition.REQUEST_HEADERS}
    assert "authorization" not in headers
    assert "cookie" not in headers
    assert acquisition.ATLANTA_FED_HOST == "www.atlantafed.org"
    assert acquisition.HTTP_METHOD == "GET"
    assert acquisition.TIMEOUT_SECONDS > 0
    assert acquisition.MAX_RESPONSE_BYTES > 0
    assert "?" not in acquisition.SOURCE_LOCATOR
    source = inspect.getsource(acquisition._fixed_origin_https_get)
    assert "HTTPSConnection" in source
    assert "HTTPConnection" not in source
    assert "urlopen" not in source
    assert "MAX_RESPONSE_BYTES + 1" in source


def test_timeout_size_and_non_success_fail_closed_through_public_path() -> None:
    with pytest.raises(
        acquisition.GDPNowAcquisitionError, match="bounded Atlanta Fed HTTPS GET failed"
    ):
        acquire(failure=TimeoutError("fixture timeout"))
    with pytest.raises(acquisition.GDPNowAcquisitionError, match="bounded size"):
        acquire(body=b"x" * (acquisition.MAX_RESPONSE_BYTES + 1))
    with pytest.raises(acquisition.GDPNowAcquisitionError, match="non-success"):
        acquire(status=404)


def test_declared_oversized_content_length_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    class OversizedLengthResponse(FakeResponse):
        length = acquisition.MAX_RESPONSE_BYTES + 1

    connection = FakeConnection(OversizedLengthResponse())
    monkeypatch.setattr(
        acquisition.http.client,
        "HTTPSConnection",
        lambda host, *, timeout, context: connection,
    )
    with pytest.raises(
        acquisition.GDPNowAcquisitionError, match="declared Atlanta Fed response length"
    ):
        acquisition.acquire_gdpnow_commentary_page()


def test_truncated_body_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    body = html()

    class TruncatingResponse(FakeResponse):
        length = len(body) + 50

    connection = FakeConnection(TruncatingResponse(body=body))
    monkeypatch.setattr(
        acquisition.http.client,
        "HTTPSConnection",
        lambda host, *, timeout, context: connection,
    )
    with pytest.raises(acquisition.GDPNowAcquisitionError, match="truncated or incomplete"):
        acquisition.acquire_gdpnow_commentary_page()


def test_http_exception_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import http.client as http_client

    connection = FakeConnection(FakeResponse(), failure=http_client.HTTPException("boom"))
    monkeypatch.setattr(
        acquisition.http.client,
        "HTTPSConnection",
        lambda host, *, timeout, context: connection,
    )
    with pytest.raises(
        acquisition.GDPNowAcquisitionError, match="bounded Atlanta Fed HTTPS GET failed"
    ):
        acquisition.acquire_gdpnow_commentary_page()
    assert connection.closed


def test_exact_response_and_diagnostic_headers_are_bound() -> None:
    raw = html()
    evidence = acquire(
        body=raw,
        headers=(
            ("Date", "Thu, 03 Sep 2026 17:00:00 GMT"),
            ("Last-Modified", "Thu, 03 Sep 2026 16:00:00 GMT"),
            ("Authorization", "not-evidence"),
            ("X-Unreviewed-Custom-Header", "server-may-send-anything"),
        ),
    )
    assert evidence.raw_body == raw
    assert evidence.raw_body_sha256 == sha256(raw).hexdigest()
    assert evidence.byte_count == len(raw)
    assert evidence.http_status == 200
    assert evidence.http_method == "GET"
    assert evidence.source_locator == LOCATOR
    assert evidence.reviewed_origin == "https://www.atlantafed.org"
    assert evidence.transport_policy_identity == acquisition.TRANSPORT_POLICY_IDENTITY
    assert evidence.research_only is True
    assert evidence.production_influence == Decimal("0")
    # Server may send arbitrary additional headers; only allowlisted names enter evidence.
    assert [name for name, _ in evidence.diagnostic_headers] == ["Date", "Last-Modified"]
    acquisition.validate_gdpnow_acquisition_evidence(evidence)


def test_unreviewed_header_manually_inserted_into_evidence_fails_validation() -> None:
    evidence = acquire()
    original = evidence.diagnostic_headers
    try:
        object.__setattr__(evidence, "diagnostic_headers", (*original, ("X-Unreviewed", "value")))
        with pytest.raises(acquisition.GDPNowAcquisitionError, match="unreviewed response header"):
            acquisition.validate_gdpnow_acquisition_evidence(evidence)
    finally:
        object.__setattr__(evidence, "diagnostic_headers", original)
    acquisition.validate_gdpnow_acquisition_evidence(evidence)


@pytest.mark.parametrize(
    "field_name,value",
    [
        ("raw_body", b"mutated"),
        ("raw_body_sha256", "0" * 64),
        ("source_locator", OTHER_LOCATOR),
        ("http_method", "POST"),
        ("transport_policy_identity", "forged"),
        ("research_only", False),
    ],
)
def test_mutation_of_acquisition_evidence_is_rejected(field_name: str, value: object) -> None:
    evidence = acquire()
    original = getattr(evidence, field_name)
    try:
        object.__setattr__(evidence, field_name, value)
        with pytest.raises(acquisition.GDPNowAcquisitionError):
            acquisition.validate_gdpnow_acquisition_evidence(evidence)
    finally:
        object.__setattr__(evidence, field_name, original)
    acquisition.validate_gdpnow_acquisition_evidence(evidence)


def test_forged_acquired_at_cannot_create_issued_evidence() -> None:
    evidence = acquire()
    original = evidence.acquired_at
    try:
        object.__setattr__(evidence, "acquired_at", datetime(2020, 1, 1, tzinfo=UTC))
        with pytest.raises(acquisition.GDPNowAcquisitionError):
            acquisition.validate_gdpnow_acquisition_evidence(evidence)
    finally:
        object.__setattr__(evidence, "acquired_at", original)
    acquisition.validate_gdpnow_acquisition_evidence(evidence)


def test_naive_and_non_utc_acquired_at_fail_closed() -> None:
    with pytest.raises(acquisition.GDPNowAcquisitionError, match="aware"):
        acquisition._validate_transport_result(result(acquired_at=datetime(2026, 9, 3, 17, 0)))
    from zoneinfo import ZoneInfo

    ny = datetime(2026, 9, 3, 13, 0, tzinfo=ZoneInfo("America/New_York"))
    acquired = acquisition._validate_transport_result(result(acquired_at=ny))
    assert acquired.tzinfo is UTC


def test_caller_authored_bytes_cannot_become_canonical_evidence_even_with_correct_locator() -> None:
    """Part G #1/#2: caller bytes, even paired with the exact reviewed locator,
    cannot gain positive acquisition authority through any public surface."""
    forged_result = result(raw_body=b"<html>caller-authored, never acquired</html>")
    with pytest.raises(acquisition.GDPNowAcquisitionError, match="capability"):
        acquisition.GDPNowAcquisitionEvidence(result=forged_result)


def test_direct_replace_object_new_and_mutate_rehash_cannot_mint_evidence() -> None:
    with pytest.raises(acquisition.GDPNowAcquisitionError, match="capability"):
        acquisition.GDPNowAcquisitionEvidence(result=result())
    evidence = acquire()
    with pytest.raises((TypeError, acquisition.GDPNowAcquisitionError)):
        replace(evidence, http_status=201)
    forged = object.__new__(acquisition.GDPNowAcquisitionEvidence)
    with pytest.raises((AttributeError, acquisition.GDPNowAcquisitionError)):
        acquisition.validate_gdpnow_acquisition_evidence(forged)
    old_body, old_id, old_hash = evidence.raw_body, evidence.evidence_id, evidence.content_hash
    try:
        object.__setattr__(evidence, "raw_body", b"mutated bytes")
        recomputed_hash = sha256(b"mutated bytes").hexdigest()
        object.__setattr__(evidence, "raw_body_sha256", recomputed_hash)
        object.__setattr__(evidence, "byte_count", len(b"mutated bytes"))
        redigest = acquisition._acquisition_digest(evidence)
        object.__setattr__(evidence, "evidence_id", redigest)
        object.__setattr__(evidence, "content_hash", redigest)
        with pytest.raises(acquisition.GDPNowAcquisitionError, match="unissued"):
            acquisition.validate_gdpnow_acquisition_evidence(evidence)
    finally:
        object.__setattr__(evidence, "raw_body", old_body)
        object.__setattr__(evidence, "raw_body_sha256", sha256(old_body).hexdigest())
        object.__setattr__(evidence, "byte_count", len(old_body))
        object.__setattr__(evidence, "evidence_id", old_id)
        object.__setattr__(evidence, "content_hash", old_hash)
    acquisition.validate_gdpnow_acquisition_evidence(evidence)


class EqualMethod(StrEnum):
    GET = "GET"


def test_equal_valued_foreign_runtime_type_is_rejected() -> None:
    evidence = acquire()
    original = evidence.http_method
    try:
        object.__setattr__(evidence, "http_method", EqualMethod.GET)
        with pytest.raises(acquisition.GDPNowAcquisitionError):
            acquisition.validate_gdpnow_acquisition_evidence(evidence)
    finally:
        object.__setattr__(evidence, "http_method", original)


def test_no_gate_or_trading_dependency() -> None:
    source = inspect.getsource(acquisition)
    forbidden = (
        "services.market_universe.modelability",
        "services.market_universe.empirical_researchability",
        "model_tournament",
        "historical_economics",
        "production_execution",
        "RiskIntent",
        "DecisionReceipt",
        "TradeCandidate",
        "signer",
        "KXGDP",
        "orderbook",
    )
    assert all(value not in source for value in forbidden)
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            assert "kalshi" not in stripped.lower()
