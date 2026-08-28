"""Acquisition-bound BLS CPI source evidence for CPI-E1-P4.

The public API performs one bounded, unauthenticated HTTPS GET against the exact
P1-authorized archived CPI HTML locator. Exact response bytes are retained and
bound to the reviewed origin, transport policy, acquisition timestamp, and P1
identities. Caller-provided bytes are never accepted by the public acquisition API.
"""

from __future__ import annotations

import http.client
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from urllib.parse import urlsplit

from services.forecasting.cpi_source_authority import (
    BLS_HTTPS_ORIGIN,
    CPISourceInterface,
    CPISourceProfile,
    CPISourceRole,
    ReviewedCPISourceAuthority,
    resolve_cpi_source_authority,
    validate_cpi_source_authority,
)
from services.forecasting.cpi_source_authority import (
    POLICY_IDENTITY as CANONICAL_P1_POLICY_IDENTITY,
)
from services.market_universe.domain import stable_hash

TRANSPORT_POLICY_VERSION = "cpi-e1-p4-bounded-bls-https-get-v2"
ACQUISITION_SCHEMA_VERSION = "cpi-e1-p4-bls-acquisition-evidence-v1"
BLS_HOST = "www.bls.gov"
HTTP_METHOD = "GET"
SUCCESS_STATUS = 200
TIMEOUT_SECONDS = 10.0
MAX_RESPONSE_BYTES = 8_000_000
REQUEST_HEADERS: tuple[tuple[str, str], ...] = (
    ("Accept", "text/html"),
    ("User-Agent", "kalsh3-cpi-e1-p4/1.0"),
)
_DIAGNOSTIC_HEADER_NAMES = frozenset({"content-type", "date", "etag", "last-modified"})
_PROFILE = CPISourceProfile.CPI_U_US_CITY_AVERAGE_ALL_ITEMS_SA_MOM_INITIAL_RELEASE
_ROLE = CPISourceRole.HISTORICAL_INITIAL_RELEASE_DOCUMENT
ZERO = Decimal("0")

TRANSPORT_POLICY_IDENTITY = stable_hash(
    (
        TRANSPORT_POLICY_VERSION,
        BLS_HTTPS_ORIGIN,
        BLS_HOST,
        HTTP_METHOD,
        TIMEOUT_SECONDS,
        MAX_RESPONSE_BYTES,
        REQUEST_HEADERS,
        "NO_CREDENTIALS",
        "NO_AUTHORIZATION",
        "NO_COOKIES_OR_SESSION_AUTHORITY",
        "NO_REDIRECTS",
        tuple(sorted(_DIAGNOSTIC_HEADER_NAMES)),
    )
)

_ACQUISITION_EVIDENCE_CAPABILITY = object()
_ISSUED_ACQUISITION_FINGERPRINTS: dict[int, str] = {}


class CPISourceAcquisitionError(ValueError):
    """Reviewed BLS acquisition or exact-response evidence failed closed."""


@dataclass(frozen=True, slots=True)
class _TransportResult:
    requested_locator: str
    final_locator: str
    method: str
    status: int
    raw_body: bytes
    acquired_at: datetime
    diagnostic_headers: tuple[tuple[str, str], ...] = ()


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise CPISourceAcquisitionError(f"{field_name} must be an exact aware datetime")
    return value.astimezone(UTC)


def _reviewed_authority(locator: str) -> ReviewedCPISourceAuthority:
    if type(locator) is not str or not locator or locator != locator.strip():
        raise CPISourceAcquisitionError("exact BLS source locator is required")
    authority = resolve_cpi_source_authority(profile=_PROFILE, role=_ROLE, locator=locator)
    validate_cpi_source_authority(authority)
    if authority.source_interface is not CPISourceInterface.BLS_ARCHIVED_CPI_NEWS_RELEASE_HTML:
        raise CPISourceAcquisitionError("only reviewed archived CPI HTML may be acquired")
    if authority.policy_identity != CANONICAL_P1_POLICY_IDENTITY:
        raise CPISourceAcquisitionError("canonical P1 source policy identity moved")
    parsed = urlsplit(locator)
    if parsed.scheme != "https" or parsed.netloc != BLS_HOST:
        raise CPISourceAcquisitionError("BLS source locator escaped the reviewed HTTPS origin")
    return authority


def _validate_diagnostic_headers(headers: tuple[tuple[str, str], ...]) -> None:
    if type(headers) is not tuple:
        raise CPISourceAcquisitionError("diagnostic response headers must have exact tuple type")
    for item in headers:
        if type(item) is not tuple or len(item) != 2:
            raise CPISourceAcquisitionError("diagnostic response header is malformed")
        name, value = item
        if type(name) is not str or type(value) is not str:
            raise CPISourceAcquisitionError("diagnostic response header types are invalid")
        if name.casefold() not in _DIAGNOSTIC_HEADER_NAMES:
            raise CPISourceAcquisitionError(
                "unreviewed response header entered acquisition evidence"
            )


def _validate_transport_result(result: _TransportResult, locator: str) -> datetime:
    if type(result) is not _TransportResult:
        raise CPISourceAcquisitionError("reviewed transport returned an invalid result type")
    if type(result.requested_locator) is not str or result.requested_locator != locator:
        raise CPISourceAcquisitionError("transport request locator does not match reviewed locator")
    if type(result.final_locator) is not str or result.final_locator != locator:
        raise CPISourceAcquisitionError("redirected or off-origin BLS response is forbidden")
    if type(result.method) is not str or result.method != HTTP_METHOD:
        raise CPISourceAcquisitionError("only reviewed GET transport may issue CPI source evidence")
    if type(result.status) is not int or isinstance(result.status, bool):
        raise CPISourceAcquisitionError("HTTP status has invalid runtime type")
    if result.status != SUCCESS_STATUS:
        raise CPISourceAcquisitionError("non-success BLS response cannot enter positive evidence")
    if type(result.raw_body) is not bytes or not result.raw_body:
        raise CPISourceAcquisitionError("exact non-empty BLS response bytes are required")
    if len(result.raw_body) > MAX_RESPONSE_BYTES:
        raise CPISourceAcquisitionError("BLS response exceeded bounded size")
    _validate_diagnostic_headers(result.diagnostic_headers)
    return _aware_utc(result.acquired_at, "BLS acquisition timestamp")


def _acquisition_digest_values(values: dict[str, object]) -> str:
    profile = values["profile"]
    role = values["source_role"]
    interface = values["source_interface"]
    acquired = values["acquired_at"]
    headers = values["diagnostic_headers"]
    if type(profile) is not CPISourceProfile:
        raise CPISourceAcquisitionError("acquisition profile type is invalid")
    if type(role) is not CPISourceRole:
        raise CPISourceAcquisitionError("acquisition source-role type is invalid")
    if type(interface) is not CPISourceInterface:
        raise CPISourceAcquisitionError("acquisition source-interface type is invalid")
    if type(acquired) is not datetime:
        raise CPISourceAcquisitionError("acquisition timestamp type is invalid")
    if type(headers) is not tuple:
        raise CPISourceAcquisitionError("acquisition diagnostic headers type is invalid")
    return stable_hash(
        (
            ACQUISITION_SCHEMA_VERSION,
            TRANSPORT_POLICY_IDENTITY,
            profile.value,
            role.value,
            interface.value,
            values["source_locator"],
            values["reviewed_origin"],
            values["http_method"],
            values["http_status"],
            values["raw_body_sha256"],
            values["byte_count"],
            acquired.isoformat(),
            headers,
            values["p1_authority_identity"],
            values["p1_policy_identity"],
            True,
            str(ZERO),
        )
    )


@dataclass(frozen=True, slots=True, init=False)
class CPIBLSAcquisitionEvidence:
    """Issuer-controlled proof of one exact successful reviewed BLS response."""

    profile: CPISourceProfile
    source_role: CPISourceRole
    source_interface: CPISourceInterface
    source_locator: str
    reviewed_origin: str
    http_method: str
    http_status: int
    raw_body: bytes
    raw_body_sha256: str
    byte_count: int
    acquired_at: datetime
    diagnostic_headers: tuple[tuple[str, str], ...]
    transport_policy_identity: str
    p1_authority_identity: str
    p1_policy_identity: str
    schema_version: str
    evidence_id: str
    content_hash: str
    research_only: bool
    production_influence: Decimal

    def __init__(
        self,
        *,
        result: _TransportResult,
        authority: ReviewedCPISourceAuthority,
        _capability: object | None = None,
    ) -> None:
        if _capability is not _ACQUISITION_EVIDENCE_CAPABILITY:
            raise CPISourceAcquisitionError(
                "BLS acquisition evidence requires reviewed transport capability"
            )
        validate_cpi_source_authority(authority)
        canonical = _reviewed_authority(result.requested_locator)
        if authority is not canonical:
            raise CPISourceAcquisitionError("caller-stamped P1 source authority is forbidden")
        acquired = _validate_transport_result(result, result.requested_locator)
        raw_hash = sha256(result.raw_body).hexdigest()
        values: dict[str, object] = {
            "profile": canonical.profile,
            "source_role": canonical.source_role,
            "source_interface": canonical.source_interface,
            "source_locator": result.requested_locator,
            "reviewed_origin": BLS_HTTPS_ORIGIN,
            "http_method": result.method,
            "http_status": result.status,
            "raw_body": result.raw_body,
            "raw_body_sha256": raw_hash,
            "byte_count": len(result.raw_body),
            "acquired_at": acquired,
            "diagnostic_headers": result.diagnostic_headers,
            "transport_policy_identity": TRANSPORT_POLICY_IDENTITY,
            "p1_authority_identity": canonical.authority_identity,
            "p1_policy_identity": canonical.policy_identity,
            "schema_version": ACQUISITION_SCHEMA_VERSION,
            "research_only": True,
            "production_influence": ZERO,
        }
        digest = _acquisition_digest_values(values)
        values["evidence_id"] = digest
        values["content_hash"] = digest
        for name, value in values.items():
            object.__setattr__(self, name, value)
        _ISSUED_ACQUISITION_FINGERPRINTS[id(self)] = digest


def _acquisition_digest(evidence: CPIBLSAcquisitionEvidence) -> str:
    return _acquisition_digest_values(
        {
            "profile": evidence.profile,
            "source_role": evidence.source_role,
            "source_interface": evidence.source_interface,
            "source_locator": evidence.source_locator,
            "reviewed_origin": evidence.reviewed_origin,
            "http_method": evidence.http_method,
            "http_status": evidence.http_status,
            "raw_body_sha256": evidence.raw_body_sha256,
            "byte_count": evidence.byte_count,
            "acquired_at": evidence.acquired_at,
            "diagnostic_headers": evidence.diagnostic_headers,
            "p1_authority_identity": evidence.p1_authority_identity,
            "p1_policy_identity": evidence.p1_policy_identity,
        }
    )


def validate_cpi_bls_acquisition_evidence(evidence: CPIBLSAcquisitionEvidence) -> None:
    """Revalidate exact bytes, transport policy, P1 authority, and issuance provenance."""
    if type(evidence) is not CPIBLSAcquisitionEvidence:
        raise CPISourceAcquisitionError("CPI acquisition evidence must have exact issued type")
    if type(evidence.profile) is not CPISourceProfile or evidence.profile is not _PROFILE:
        raise CPISourceAcquisitionError("CPI acquisition profile is not canonical")
    if type(evidence.source_role) is not CPISourceRole or evidence.source_role is not _ROLE:
        raise CPISourceAcquisitionError("CPI acquisition source role is not canonical")
    if (
        type(evidence.source_interface) is not CPISourceInterface
        or evidence.source_interface is not CPISourceInterface.BLS_ARCHIVED_CPI_NEWS_RELEASE_HTML
    ):
        raise CPISourceAcquisitionError("CPI acquisition source interface is not canonical")
    if type(evidence.source_locator) is not str:
        raise CPISourceAcquisitionError("CPI acquisition locator type is invalid")
    authority = _reviewed_authority(evidence.source_locator)
    if type(evidence.reviewed_origin) is not str or evidence.reviewed_origin != BLS_HTTPS_ORIGIN:
        raise CPISourceAcquisitionError("CPI acquisition reviewed origin changed")
    if type(evidence.http_method) is not str or evidence.http_method != HTTP_METHOD:
        raise CPISourceAcquisitionError("CPI acquisition HTTP method changed")
    if (
        type(evidence.http_status) is not int
        or isinstance(evidence.http_status, bool)
        or evidence.http_status != SUCCESS_STATUS
    ):
        raise CPISourceAcquisitionError("CPI acquisition success status changed")
    if type(evidence.raw_body) is not bytes or not evidence.raw_body:
        raise CPISourceAcquisitionError("CPI acquisition raw response bytes changed")
    if len(evidence.raw_body) > MAX_RESPONSE_BYTES:
        raise CPISourceAcquisitionError("CPI acquisition raw response exceeded size bound")
    raw_hash = sha256(evidence.raw_body).hexdigest()
    if type(evidence.raw_body_sha256) is not str or evidence.raw_body_sha256 != raw_hash:
        raise CPISourceAcquisitionError("CPI acquisition raw response hash mismatch")
    if (
        type(evidence.byte_count) is not int
        or isinstance(evidence.byte_count, bool)
        or evidence.byte_count != len(evidence.raw_body)
    ):
        raise CPISourceAcquisitionError("CPI acquisition byte count mismatch")
    acquired = _aware_utc(evidence.acquired_at, "BLS acquisition timestamp")
    if evidence.acquired_at != acquired or evidence.acquired_at.tzinfo is not UTC:
        raise CPISourceAcquisitionError("CPI acquisition timestamp lost canonical UTC semantics")
    _validate_diagnostic_headers(evidence.diagnostic_headers)
    expected = _acquisition_digest(evidence)
    exact = (
        type(evidence.transport_policy_identity) is str,
        evidence.transport_policy_identity == TRANSPORT_POLICY_IDENTITY,
        type(evidence.p1_authority_identity) is str,
        evidence.p1_authority_identity == authority.authority_identity,
        type(evidence.p1_policy_identity) is str,
        evidence.p1_policy_identity == authority.policy_identity == CANONICAL_P1_POLICY_IDENTITY,
        type(evidence.schema_version) is str,
        evidence.schema_version == ACQUISITION_SCHEMA_VERSION,
        type(evidence.evidence_id) is str,
        evidence.evidence_id == expected,
        type(evidence.content_hash) is str,
        evidence.content_hash == expected,
        type(evidence.research_only) is bool and evidence.research_only is True,
        type(evidence.production_influence) is Decimal,
        evidence.production_influence == ZERO,
    )
    if not all(exact):
        raise CPISourceAcquisitionError("CPI acquisition evidence failed canonical revalidation")
    if _ISSUED_ACQUISITION_FINGERPRINTS.get(id(evidence)) != expected:
        raise CPISourceAcquisitionError("unissued, reconstructed, or mutated acquisition evidence")


def _fixed_origin_https_get(locator: str) -> _TransportResult:
    _reviewed_authority(locator)
    path = urlsplit(locator).path
    connection = http.client.HTTPSConnection(
        BLS_HOST,
        timeout=TIMEOUT_SECONDS,
        context=ssl.create_default_context(),
    )
    try:
        connection.request(HTTP_METHOD, path, headers=dict(REQUEST_HEADERS))
        response = connection.getresponse()
        expected_body_length = response.length
        if expected_body_length is not None and expected_body_length > MAX_RESPONSE_BYTES:
            raise CPISourceAcquisitionError("declared BLS response length exceeded bounded size")
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if expected_body_length is not None and len(body) != expected_body_length:
            raise CPISourceAcquisitionError("BLS response was truncated or incomplete")
        acquired_at = datetime.now(UTC)
        diagnostic_headers = tuple(
            (name, value)
            for name, value in response.getheaders()
            if name.casefold() in _DIAGNOSTIC_HEADER_NAMES
        )
    except (OSError, TimeoutError, http.client.HTTPException) as exc:
        raise CPISourceAcquisitionError(f"bounded BLS HTTPS GET failed: {exc}") from exc
    finally:
        connection.close()
    if len(body) > MAX_RESPONSE_BYTES:
        raise CPISourceAcquisitionError("BLS response exceeded bounded size")
    return _TransportResult(
        requested_locator=locator,
        final_locator=locator,
        method=HTTP_METHOD,
        status=int(response.status),
        raw_body=body,
        acquired_at=acquired_at,
        diagnostic_headers=diagnostic_headers,
    )


def acquire_bls_cpi_release(source_locator: str) -> CPIBLSAcquisitionEvidence:
    """Acquire one exact P1-authorized archived CPI HTML response from BLS."""
    authority = _reviewed_authority(source_locator)
    result = _fixed_origin_https_get(source_locator)
    evidence = CPIBLSAcquisitionEvidence(
        result=result,
        authority=authority,
        _capability=_ACQUISITION_EVIDENCE_CAPABILITY,
    )
    validate_cpi_bls_acquisition_evidence(evidence)
    return evidence
