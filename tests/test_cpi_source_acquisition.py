from __future__ import annotations

import inspect
import re
from dataclasses import replace
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import services.forecasting.cpi_evidence_issuer as issuer
import services.forecasting.cpi_pit_availability as pit
import services.forecasting.cpi_publication_timing as timing
import services.forecasting.cpi_source_acquisition as acquisition
from services.forecasting.cpi_source_authority import CPISourceAuthorityError, CPISourceProfile
from services.historical_replay.domain import AvailabilityBasis, AvailabilityQuality

LOCATOR = "https://www.bls.gov/news.release/archives/cpi_08122025.htm"
OTHER_LOCATOR = "https://www.bls.gov/news.release/archives/cpi_08132025.htm"
NY = ZoneInfo("America/New_York")
PROFILE = CPISourceProfile.CPI_U_US_CITY_AVERAGE_ALL_ITEMS_SA_MOM_INITIAL_RELEASE


def html(hour: str = "8:30") -> bytes:
    return (
        "<!doctype html><html><body><p>Transmission of material in this release is "
        f"embargoed until {hour} a.m. (ET) Tuesday, August 12, 2025</p></body></html>"
    ).encode("ascii")


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
) -> acquisition.CPIBLSAcquisitionEvidence:
    connection = FakeConnection(FakeResponse(status=status, body=body, headers=headers), failure)
    patch = pytest.MonkeyPatch()

    def factory(host: str, *, timeout: float, context: object) -> FakeConnection:
        del context
        assert host == acquisition.BLS_HOST
        assert timeout == acquisition.TIMEOUT_SECONDS
        return connection

    patch.setattr(acquisition.http.client, "HTTPSConnection", factory)
    try:
        evidence = acquisition.acquire_bls_cpi_release(LOCATOR)
    finally:
        patch.undo()
    assert connection.closed
    assert connection.requested is not None
    method, path, request_headers = connection.requested
    assert method == "GET"
    assert path == "/news.release/archives/cpi_08122025.htm"
    assert "Authorization" not in request_headers
    assert "Cookie" not in request_headers
    return evidence


def result(**changes: object) -> acquisition._TransportResult:
    values: dict[str, object] = {
        "requested_locator": LOCATOR,
        "final_locator": LOCATOR,
        "method": "GET",
        "status": 200,
        "raw_body": html(),
        "acquired_at": datetime(2026, 8, 28, 17, 0, tzinfo=UTC),
        "diagnostic_headers": (),
    }
    values.update(changes)
    return acquisition._TransportResult(**values)  # type: ignore[arg-type]


def test_public_api_cannot_accept_caller_bytes_url_authority_or_timing() -> None:
    assert tuple(inspect.signature(acquisition.acquire_bls_cpi_release).parameters) == (
        "source_locator",
    )
    with pytest.raises(TypeError):
        acquisition.acquire_bls_cpi_release(LOCATOR, raw_body=html())  # type: ignore[call-arg]
    for locator in (
        "https://example.com/news.release/archives/cpi_08122025.htm",
        "http://www.bls.gov/news.release/archives/cpi_08122025.htm",
    ):
        with pytest.raises(CPISourceAuthorityError):
            acquisition.acquire_bls_cpi_release(locator)
    assert tuple(inspect.signature(issuer.issue_acquisition_bound_cpi_evidence).parameters) == (
        "acquisition_evidence",
    )
    evidence = acquire()
    for kwargs in (
        {"source_publish_at": datetime.now(UTC)},
        {"timing_evidence_identity": "caller"},
    ):
        with pytest.raises(TypeError):
            issuer.issue_acquisition_bound_cpi_evidence(evidence, **kwargs)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"final_locator": "https://example.com/cpi.htm"}, "off-origin"),
        ({"method": "POST"}, "GET"),
        ({"status": 503}, "non-success"),
        ({"raw_body": b"x" * (acquisition.MAX_RESPONSE_BYTES + 1)}, "bounded size"),
        ({"requested_locator": OTHER_LOCATOR}, "request locator"),
    ],
)
def test_transport_invariants_fail_closed(changes: dict[str, object], message: str) -> None:
    with pytest.raises(acquisition.CPISourceAcquisitionError, match=message):
        acquisition._validate_transport_result(result(**changes), LOCATOR)


def test_transport_is_fixed_https_get_no_credentials_redirects_or_unbounded_io() -> None:
    headers = {name.casefold(): value for name, value in acquisition.REQUEST_HEADERS}
    assert "authorization" not in headers
    assert "cookie" not in headers
    assert acquisition.BLS_HOST == "www.bls.gov"
    assert acquisition.HTTP_METHOD == "GET"
    assert acquisition.TIMEOUT_SECONDS > 0
    assert acquisition.MAX_RESPONSE_BYTES > 0
    source = inspect.getsource(acquisition._fixed_origin_https_get)
    assert "HTTPSConnection" in source
    assert "HTTPConnection" not in source
    assert "urlopen" not in source
    assert "MAX_RESPONSE_BYTES + 1" in source


def test_timeout_size_and_non_success_fail_closed_through_public_path() -> None:
    with pytest.raises(acquisition.CPISourceAcquisitionError, match="bounded BLS HTTPS GET failed"):
        acquire(failure=TimeoutError("fixture timeout"))
    with pytest.raises(acquisition.CPISourceAcquisitionError, match="bounded size"):
        acquire(body=b"x" * (acquisition.MAX_RESPONSE_BYTES + 1))
    with pytest.raises(acquisition.CPISourceAcquisitionError, match="non-success"):
        acquire(status=404)


def test_exact_response_and_diagnostic_headers_are_bound_but_not_timing_authority() -> None:
    raw = html()
    evidence = acquire(
        body=raw,
        headers=(
            ("Date", "Fri, 28 Aug 2026 17:00:00 GMT"),
            ("Last-Modified", "Fri, 28 Aug 2026 16:00:00 GMT"),
            ("Authorization", "not-evidence"),
        ),
    )
    assert evidence.raw_body == raw
    assert evidence.raw_body_sha256 == sha256(raw).hexdigest()
    assert evidence.byte_count == len(raw)
    assert evidence.http_status == 200
    assert evidence.http_method == "GET"
    assert evidence.source_locator == LOCATOR
    assert evidence.reviewed_origin == "https://www.bls.gov"
    assert evidence.transport_policy_identity == acquisition.TRANSPORT_POLICY_IDENTITY
    assert [name for name, _ in evidence.diagnostic_headers] == ["Date", "Last-Modified"]
    issued = issuer.issue_acquisition_bound_cpi_evidence(evidence)
    historical = datetime(2025, 8, 12, 8, 30, tzinfo=NY)
    assert issued.publication_evidence.source_publish_at == historical
    assert evidence.acquired_at != historical


@pytest.mark.parametrize(
    "field_name,value",
    [
        ("raw_body", b"mutated"),
        ("raw_body_sha256", "0" * 64),
        ("p1_authority_identity", "forged"),
        ("p1_policy_identity", "forged"),
        ("http_method", "POST"),
    ],
)
def test_mutation_of_acquisition_evidence_is_rejected(field_name: str, value: object) -> None:
    evidence = acquire()
    original = getattr(evidence, field_name)
    try:
        object.__setattr__(evidence, field_name, value)
        with pytest.raises(acquisition.CPISourceAcquisitionError):
            acquisition.validate_cpi_bls_acquisition_evidence(evidence)
    finally:
        object.__setattr__(evidence, field_name, original)
    acquisition.validate_cpi_bls_acquisition_evidence(evidence)


def test_direct_replace_object_new_and_mutate_rehash_cannot_mint_evidence() -> None:
    authority = acquisition._reviewed_authority(LOCATOR)
    with pytest.raises(acquisition.CPISourceAcquisitionError, match="capability"):
        acquisition.CPIBLSAcquisitionEvidence(result=result(), authority=authority)
    evidence = acquire()
    with pytest.raises((TypeError, acquisition.CPISourceAcquisitionError)):
        replace(evidence, http_status=201)
    forged = object.__new__(acquisition.CPIBLSAcquisitionEvidence)
    with pytest.raises((AttributeError, acquisition.CPISourceAcquisitionError)):
        acquisition.validate_cpi_bls_acquisition_evidence(forged)
    old_locator, old_id, old_hash = evidence.source_locator, evidence.evidence_id, evidence.content_hash
    try:
        object.__setattr__(evidence, "source_locator", OTHER_LOCATOR)
        redigest = acquisition._acquisition_digest(evidence)
        object.__setattr__(evidence, "evidence_id", redigest)
        object.__setattr__(evidence, "content_hash", redigest)
        with pytest.raises(acquisition.CPISourceAcquisitionError):
            acquisition.validate_cpi_bls_acquisition_evidence(evidence)
    finally:
        object.__setattr__(evidence, "source_locator", old_locator)
        object.__setattr__(evidence, "evidence_id", old_id)
        object.__setattr__(evidence, "content_hash", old_hash)


class EqualMethod(StrEnum):
    GET = "GET"


def test_equal_valued_foreign_runtime_type_is_rejected() -> None:
    evidence = acquire()
    original = evidence.http_method
    try:
        object.__setattr__(evidence, "http_method", EqualMethod.GET)
        with pytest.raises(acquisition.CPISourceAcquisitionError):
            acquisition.validate_cpi_bls_acquisition_evidence(evidence)
    finally:
        object.__setattr__(evidence, "http_method", original)


def test_p1_p3_and_p2_bind_to_exact_acquired_artifact() -> None:
    evidence = acquire()
    issued = issuer.issue_acquisition_bound_cpi_evidence(evidence)
    artifact = issued.release_artifact
    parsed = issued.parsed_timing
    proof = issued.publication_evidence
    assert artifact.raw_artifact == evidence.raw_body
    assert artifact.raw_artifact_sha256 == evidence.raw_body_sha256 == parsed.raw_artifact_sha256
    assert parsed.source_artifact_id == artifact.artifact_id == proof.source_artifact_id
    assert parsed.source_locator == artifact.source_locator == evidence.source_locator
    assert parsed.p1_authority_identity == artifact.p1_authority_identity == evidence.p1_authority_identity
    assert parsed.p1_policy_identity == artifact.p1_policy_identity == evidence.p1_policy_identity
    assert type(proof) is pit.CPIActualPublicationEvidence
    assert proof.source_publish_at == parsed.publication_instant
    assert proof.timing_evidence_identity == issued.timing_evidence_identity
    pit.validate_cpi_publication_evidence(proof)


def test_cross_artifact_parser_reuse_and_fake_caller_artifact_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = acquire(body=html("8:30"))
    second = acquire(body=html("9:00"))
    second_artifact = issuer._artifact_from_acquisition(second)
    second_parsed = timing.parse_cpi_publication_timing(second_artifact)
    monkeypatch.setattr(issuer, "parse_cpi_publication_timing", lambda _: second_parsed)
    with pytest.raises(issuer.CPIEvidenceIssuanceError, match="exact acquired artifact"):
        issuer.issue_acquisition_bound_cpi_evidence(first)
    fake = pit.CPIHistoricalReleaseArtifact(
        profile=PROFILE,
        source_locator=LOCATOR,
        actual_bot_ingest_at=datetime.now(UTC),
        raw_artifact=html(),
    )
    with pytest.raises((TypeError, acquisition.CPISourceAcquisitionError)):
        issuer.issue_acquisition_bound_cpi_evidence(fake)  # type: ignore[arg-type]


def test_three_times_and_conservative_availability_remain_distinct() -> None:
    issued = issuer.issue_acquisition_bound_cpi_evidence(acquire())
    availability = issued.availability
    publication = datetime(2025, 8, 12, 8, 30, tzinfo=NY)
    replay = datetime(2025, 8, 12, 23, 59, 59, 999999, tzinfo=NY)
    assert issued.parsed_timing.publication_instant == publication
    assert availability.source_publish_at == publication
    assert availability.replay_available_at == replay
    assert availability.assumed_latency == replay - publication
    assert availability.actual_bot_ingest_at == issued.acquisition_evidence.acquired_at
    assert availability.actual_bot_ingest_at >= replay
    assert availability.basis is AvailabilityBasis.RECONSTRUCTED_PRIMARY_SOURCE
    assert availability.quality is AvailabilityQuality.CONSERVATIVE_ASSUMPTION


def test_p3_stays_parser_only_and_private_p2_seam_allowlist_is_exact() -> None:
    assert "_issue_actual_cpi_publication_evidence" not in inspect.getsource(timing)
    assert "_PUBLICATION_AUTHORITY_CAPABILITY" not in inspect.getsource(timing)
    root = Path(__file__).resolve().parents[1]
    allowed = {
        Path("services/forecasting/cpi_pit_availability.py"),
        Path("services/forecasting/cpi_evidence_issuer.py"),
    }
    names = ("_issue_actual_cpi_publication_evidence", "_PUBLICATION_AUTHORITY_CAPABILITY")
    seen: set[Path] = set()
    violations: list[str] = []
    for path in sorted((root / "services").rglob("*.py")):
        relative = path.relative_to(root)
        source = path.read_text(encoding="utf-8")
        if any(name in source for name in names):
            seen.add(relative)
            if relative not in allowed:
                violations.append(str(relative))
    assert violations == []
    assert seen == allowed
    for path in sorted((root / "scripts").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert all(name not in source for name in names)


def test_no_gate_or_trading_dependency_and_mandatory_ci_is_offline() -> None:
    source = inspect.getsource(acquisition) + inspect.getsource(issuer)
    assert re.findall(r"(?m)^\s*G[1-6]\s*=", source) == []
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
    )
    assert all(value not in source for value in forbidden)
    root = Path(__file__).resolve().parents[1]
    needles = ("acquire_bls_cpi_release(", "acquire_and_issue_cpi_evidence(")
    for path in sorted((root / ".github/workflows").glob("*.yml")):
        workflow = path.read_text(encoding="utf-8")
        assert all(needle not in workflow for needle in needles)
