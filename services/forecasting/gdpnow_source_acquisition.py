"""Acquisition-bound Atlanta Fed GDPNow source evidence for D1-G1.

The public API performs one bounded, unauthenticated HTTPS GET against the exact
reviewed Atlanta Fed GDPNow commentary locator. There is no caller-supplied source
locator: the reviewed origin, host, and path are frozen constants. Exact response
bytes are retained and bound to the reviewed origin, transport policy, and
acquisition timestamp. Caller-provided bytes are never accepted by the public
acquisition API.

This module establishes GDPNow acquisition provenance only. It does not acquire
Kalshi quotes, compute scores, or establish any predictive or economic authority.
research_only is always True and production_influence is always zero.
"""

from __future__ import annotations

import http.client
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256

from services.market_universe.domain import stable_hash

TRANSPORT_POLICY_VERSION = "d1-g1-gdpnow-bounded-atlantafed-https-get-v1"
ACQUISITION_SCHEMA_VERSION = "d1-g1-gdpnow-acquisition-evidence-v1"

ATLANTA_FED_HOST = "www.atlantafed.org"
ATLANTA_FED_HTTPS_ORIGIN = f"https://{ATLANTA_FED_HOST}"
ATLANTA_FED_PATH = "/research-and-data/data/gdpnow/current-and-past-gdpnow-commentaries"
SOURCE_LOCATOR = f"{ATLANTA_FED_HTTPS_ORIGIN}{ATLANTA_FED_PATH}"

HTTP_METHOD = "GET"
SUCCESS_STATUS = 200
TIMEOUT_SECONDS = 10.0
# The reviewed commentary page is ~120KB. This bound stays far below that and,
# just as importantly, far below the excluded >10MB historical Excel workbook
# (see D1-G1 Part M), so an oversized/wrong response fails closed rather than
# silently ingesting the workbook or another unreviewed artifact.
MAX_RESPONSE_BYTES = 4_000_000
REQUEST_HEADERS: tuple[tuple[str, str], ...] = (
    ("Accept", "text/html"),
    ("User-Agent", "kalsh3-d1-g1-gdpnow/1.0"),
)
_DIAGNOSTIC_HEADER_NAMES = frozenset({"content-type", "date", "etag", "last-modified"})
ZERO = Decimal("0")

TRANSPORT_POLICY_IDENTITY = stable_hash(
    (
        TRANSPORT_POLICY_VERSION,
        SOURCE_LOCATOR,
        ATLANTA_FED_HTTPS_ORIGIN,
        ATLANTA_FED_HOST,
        ATLANTA_FED_PATH,
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

_GDPNOW_EVIDENCE_ISSUANCE_CAPABILITY = object()
_ISSUED_GDPNOW_ACQUISITION_FINGERPRINTS: dict[int, str] = {}


class GDPNowAcquisitionError(ValueError):
    """Reviewed Atlanta Fed acquisition or exact-response evidence failed closed."""


@dataclass(frozen=True, slots=True)
class _GDPNowTransportResult:
    requested_locator: str
    final_locator: str
    method: str
    status: int
    raw_body: bytes
    acquired_at: datetime
    diagnostic_headers: tuple[tuple[str, str], ...] = ()


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise GDPNowAcquisitionError(f"{field_name} must be an exact aware datetime")
    return value.astimezone(UTC)


def _validate_diagnostic_headers(headers: tuple[tuple[str, str], ...]) -> None:
    if type(headers) is not tuple:
        raise GDPNowAcquisitionError("diagnostic response headers must have exact tuple type")
    for item in headers:
        if type(item) is not tuple or len(item) != 2:
            raise GDPNowAcquisitionError("diagnostic response header is malformed")
        name, value = item
        if type(name) is not str or type(value) is not str:
            raise GDPNowAcquisitionError("diagnostic response header types are invalid")
        if name.casefold() not in _DIAGNOSTIC_HEADER_NAMES:
            raise GDPNowAcquisitionError("unreviewed response header entered acquisition evidence")


def _validate_transport_result(result: _GDPNowTransportResult) -> datetime:
    if type(result) is not _GDPNowTransportResult:
        raise GDPNowAcquisitionError("reviewed transport returned an invalid result type")
    if type(result.requested_locator) is not str or result.requested_locator != SOURCE_LOCATOR:
        raise GDPNowAcquisitionError(
            "transport request locator does not match the reviewed GDPNow locator"
        )
    if type(result.final_locator) is not str or result.final_locator != result.requested_locator:
        raise GDPNowAcquisitionError("redirected or off-origin GDPNow response is forbidden")
    if type(result.method) is not str or result.method != HTTP_METHOD:
        raise GDPNowAcquisitionError("only reviewed GET transport may issue GDPNow source evidence")
    if type(result.status) is not int or isinstance(result.status, bool):
        raise GDPNowAcquisitionError("HTTP status has invalid runtime type")
    if result.status != SUCCESS_STATUS:
        raise GDPNowAcquisitionError(
            "non-success Atlanta Fed response cannot enter positive evidence"
        )
    if type(result.raw_body) is not bytes or not result.raw_body:
        raise GDPNowAcquisitionError("exact non-empty Atlanta Fed response bytes are required")
    if len(result.raw_body) > MAX_RESPONSE_BYTES:
        raise GDPNowAcquisitionError("Atlanta Fed response exceeded bounded size")
    _validate_diagnostic_headers(result.diagnostic_headers)
    return _aware_utc(result.acquired_at, "GDPNow acquisition timestamp")


def _acquisition_digest_values(values: dict[str, object]) -> str:
    acquired = values["acquired_at"]
    headers = values["diagnostic_headers"]
    if type(acquired) is not datetime:
        raise GDPNowAcquisitionError("acquisition timestamp type is invalid")
    if type(headers) is not tuple:
        raise GDPNowAcquisitionError("acquisition diagnostic headers type is invalid")
    return stable_hash(
        (
            ACQUISITION_SCHEMA_VERSION,
            TRANSPORT_POLICY_IDENTITY,
            values["source_locator"],
            values["reviewed_origin"],
            values["http_method"],
            values["http_status"],
            values["raw_body_sha256"],
            values["byte_count"],
            acquired.isoformat(),
            headers,
            True,
            str(ZERO),
        )
    )


@dataclass(frozen=True, slots=True, init=False)
class GDPNowAcquisitionEvidence:
    """Issuer-controlled proof of one exact successful reviewed Atlanta Fed response."""

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
    schema_version: str
    evidence_id: str
    content_hash: str
    research_only: bool
    production_influence: Decimal

    def __init__(
        self,
        *,
        result: _GDPNowTransportResult,
        _capability: object | None = None,
    ) -> None:
        if _capability is not _GDPNOW_EVIDENCE_ISSUANCE_CAPABILITY:
            raise GDPNowAcquisitionError(
                "GDPNow acquisition evidence requires reviewed transport capability"
            )
        acquired = _validate_transport_result(result)
        raw_hash = sha256(result.raw_body).hexdigest()
        values: dict[str, object] = {
            "source_locator": result.requested_locator,
            "reviewed_origin": ATLANTA_FED_HTTPS_ORIGIN,
            "http_method": result.method,
            "http_status": result.status,
            "raw_body": result.raw_body,
            "raw_body_sha256": raw_hash,
            "byte_count": len(result.raw_body),
            "acquired_at": acquired,
            "diagnostic_headers": result.diagnostic_headers,
            "transport_policy_identity": TRANSPORT_POLICY_IDENTITY,
            "schema_version": ACQUISITION_SCHEMA_VERSION,
            "research_only": True,
            "production_influence": ZERO,
        }
        digest = _acquisition_digest_values(values)
        values["evidence_id"] = digest
        values["content_hash"] = digest
        for name, value in values.items():
            object.__setattr__(self, name, value)
        _ISSUED_GDPNOW_ACQUISITION_FINGERPRINTS[id(self)] = digest


def _acquisition_digest(evidence: GDPNowAcquisitionEvidence) -> str:
    return _acquisition_digest_values(
        {
            "source_locator": evidence.source_locator,
            "reviewed_origin": evidence.reviewed_origin,
            "http_method": evidence.http_method,
            "http_status": evidence.http_status,
            "raw_body_sha256": evidence.raw_body_sha256,
            "byte_count": evidence.byte_count,
            "acquired_at": evidence.acquired_at,
            "diagnostic_headers": evidence.diagnostic_headers,
        }
    )


def validate_gdpnow_acquisition_evidence(evidence: GDPNowAcquisitionEvidence) -> None:
    """Revalidate exact bytes, transport policy, and issuance provenance."""
    if type(evidence) is not GDPNowAcquisitionEvidence:
        raise GDPNowAcquisitionError("GDPNow acquisition evidence must have exact issued type")
    if type(evidence.source_locator) is not str or evidence.source_locator != SOURCE_LOCATOR:
        raise GDPNowAcquisitionError("GDPNow acquisition locator is not the reviewed locator")
    if (
        type(evidence.reviewed_origin) is not str
        or evidence.reviewed_origin != ATLANTA_FED_HTTPS_ORIGIN
    ):
        raise GDPNowAcquisitionError("GDPNow acquisition reviewed origin changed")
    if type(evidence.http_method) is not str or evidence.http_method != HTTP_METHOD:
        raise GDPNowAcquisitionError("GDPNow acquisition HTTP method changed")
    if (
        type(evidence.http_status) is not int
        or isinstance(evidence.http_status, bool)
        or evidence.http_status != SUCCESS_STATUS
    ):
        raise GDPNowAcquisitionError("GDPNow acquisition success status changed")
    if type(evidence.raw_body) is not bytes or not evidence.raw_body:
        raise GDPNowAcquisitionError("GDPNow acquisition raw response bytes changed")
    if len(evidence.raw_body) > MAX_RESPONSE_BYTES:
        raise GDPNowAcquisitionError("GDPNow acquisition raw response exceeded size bound")
    raw_hash = sha256(evidence.raw_body).hexdigest()
    if type(evidence.raw_body_sha256) is not str or evidence.raw_body_sha256 != raw_hash:
        raise GDPNowAcquisitionError("GDPNow acquisition raw response hash mismatch")
    if (
        type(evidence.byte_count) is not int
        or isinstance(evidence.byte_count, bool)
        or evidence.byte_count != len(evidence.raw_body)
    ):
        raise GDPNowAcquisitionError("GDPNow acquisition byte count mismatch")
    acquired = _aware_utc(evidence.acquired_at, "GDPNow acquisition timestamp")
    if evidence.acquired_at != acquired or evidence.acquired_at.tzinfo is not UTC:
        raise GDPNowAcquisitionError("GDPNow acquisition timestamp lost canonical UTC semantics")
    _validate_diagnostic_headers(evidence.diagnostic_headers)
    expected = _acquisition_digest(evidence)
    exact = (
        type(evidence.transport_policy_identity) is str,
        evidence.transport_policy_identity == TRANSPORT_POLICY_IDENTITY,
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
        raise GDPNowAcquisitionError("GDPNow acquisition evidence failed canonical revalidation")
    if _ISSUED_GDPNOW_ACQUISITION_FINGERPRINTS.get(id(evidence)) != expected:
        raise GDPNowAcquisitionError("unissued, reconstructed, or mutated acquisition evidence")


def _fixed_origin_https_get() -> _GDPNowTransportResult:
    connection = http.client.HTTPSConnection(
        ATLANTA_FED_HOST,
        timeout=TIMEOUT_SECONDS,
        context=ssl.create_default_context(),
    )
    try:
        connection.request(HTTP_METHOD, ATLANTA_FED_PATH, headers=dict(REQUEST_HEADERS))
        response = connection.getresponse()
        expected_body_length = getattr(response, "length", None)
        if expected_body_length is not None and expected_body_length > MAX_RESPONSE_BYTES:
            raise GDPNowAcquisitionError(
                "declared Atlanta Fed response length exceeded bounded size"
            )
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if expected_body_length is not None and len(body) != expected_body_length:
            raise GDPNowAcquisitionError("Atlanta Fed response was truncated or incomplete")
        acquired_at = datetime.now(UTC)
        diagnostic_headers = tuple(
            (name, value)
            for name, value in response.getheaders()
            if name.casefold() in _DIAGNOSTIC_HEADER_NAMES
        )
    except (OSError, TimeoutError, http.client.HTTPException) as exc:
        raise GDPNowAcquisitionError(f"bounded Atlanta Fed HTTPS GET failed: {exc}") from exc
    finally:
        connection.close()
    if len(body) > MAX_RESPONSE_BYTES:
        raise GDPNowAcquisitionError("Atlanta Fed response exceeded bounded size")
    return _GDPNowTransportResult(
        requested_locator=SOURCE_LOCATOR,
        final_locator=SOURCE_LOCATOR,
        method=HTTP_METHOD,
        status=int(response.status),
        raw_body=body,
        acquired_at=acquired_at,
        diagnostic_headers=diagnostic_headers,
    )


def acquire_gdpnow_commentary_page() -> GDPNowAcquisitionEvidence:
    """Acquire one exact reviewed Atlanta Fed GDPNow commentary response.

    Takes no arguments: the reviewed source locator is a frozen constant, not a
    caller-supplied value.
    """
    result = _fixed_origin_https_get()
    evidence = GDPNowAcquisitionEvidence(
        result=result,
        _capability=_GDPNOW_EVIDENCE_ISSUANCE_CAPABILITY,
    )
    validate_gdpnow_acquisition_evidence(evidence)
    return evidence
