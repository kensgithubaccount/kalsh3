"""Fail-closed reconciliation of P6 CPI observations to historical Kalshi finals.

Positive exchange authority can only enter through the reviewed public Kalshi
GET boundary below. Frozen response bodies are content-addressed copies of that
boundary for deterministic CI and audit replay; arbitrary JSON is never an
acquisition API.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit
from weakref import WeakValueDictionary

from services.contract_intelligence.settlement import (
    DeterminationState,
    ExchangeDetermination,
    ReconciliationStatus,
    SettlementRecord,
)
from services.contract_intelligence.specification import (
    Comparator,
    ContractSpecification,
    ContractSpecificationParser,
    PayoutModel,
    SemanticsInputBundle,
    SemanticStatus,
)
from services.forecasting.cpi_initial_release_value import (
    CPIBasket,
    CPIGeography,
    CPIHorizon,
    CPIInitialReleaseObservation,
    CPIPopulation,
    CPISeasonalBasis,
    CPIUnit,
    validate_cpi_initial_release_observation,
)
from services.market_universe.domain import material_hashes, stable_hash

ZERO = Decimal("0")
ONE = Decimal("1")
SUPPORTED_COMPARATORS = frozenset(
    {Comparator.GT, Comparator.GTE, Comparator.LT, Comparator.LTE, Comparator.EQ}
)
POLICY_VERSION = "cpi-e1-p7-settlement-reconciliation-v2"
KALSHI_HOST = "external-api.kalshi.com"
KALSHI_BASE = f"https://{KALSHI_HOST}/trade-api/v2"
HTTP_METHOD = "GET"
SUCCESS_STATUS = 200
MAX_RESPONSE_BYTES = 2_000_000
KALSHI_ACQUISITION_SCHEMA_VERSION = "cpi-e1-p7-kalshi-acquisition-v1"
CONTRACT_TERMS_SHA256 = "2317b1d8e823082b409f6ff3415fb135804d9682681f9f92f640b3681b29a872"
CONTRACT_TERMS_URL = "https://assets.kalshi.com/contract_terms/CPI.pdf"
HISTORICAL_RULES_VERSION_PREFIX = "historical-market-rules-v1:"
_ACQUISITION_CAPABILITY = object()
# Identity-safe issuance registry: keyed by id() (a plain int, safe to hash
# normally) but the accept/reject decision is an `is` identity check against
# the weakly-held value, never a value-equality or bare-address comparison.
# A WeakValueDictionary entry is dropped the instant its referent is
# garbage-collected, so no stale entry can ever outlive the object it was
# issued for. Because CPython only guarantees a unique id() among objects
# with overlapping lifetimes, any object alive at validation time can only
# find its own entry (or none) here -- never another live object's entry,
# and never a resurrected dead one. See docs/reviews/CPI_E1_P7R_ISSUANCE_IDENTITY_REPAIR.md.
_ISSUED_KALSHI_ACQUISITION_EVIDENCE: WeakValueDictionary[
    int, KalshiHistoricalAcquisitionEvidence
] = WeakValueDictionary()


class CPISettlementReconciliationError(ValueError):
    """The exact semantic, transport, or exchange evidence failed closed."""


class ExpectedBinaryResult(StrEnum):
    YES = "YES"
    NO = "NO"


class KalshiEndpointRole(StrEnum):
    HISTORICAL_MARKET = "historical_market"
    EVENT = "event"
    SERIES = "series"
    CONTRACT_TERMS = "contract_terms"


@dataclass(frozen=True, slots=True)
class KXCPIReviewedSemanticPolicy:
    """Content-addressed bridge from reviewed Kalshi terms to the P6 domain."""

    policy_version: str
    contract_terms_url: str
    contract_terms_sha256: str
    measured_event_or_value: str
    subject_entities: tuple[str, ...]
    geographic_scope: str
    basket: str
    seasonal_basis: str
    horizon: str
    threshold_unit: str
    rounding_rules: str
    revision_rules: str
    correction_rules: str
    settlement_authority_name: str
    settlement_authority_url: str
    payout_model: str
    settlement_type: str
    timezone: str


KXCPI_SEMANTIC_POLICY = KXCPIReviewedSemanticPolicy(
    policy_version=POLICY_VERSION,
    contract_terms_url=CONTRACT_TERMS_URL,
    contract_terms_sha256=CONTRACT_TERMS_SHA256,
    measured_event_or_value=(
        "CPI-U U.S. city average all items seasonally adjusted change from preceding month"
    ),
    subject_entities=("CPI-U",),
    geographic_scope="U.S. city average",
    basket="all items",
    seasonal_basis="seasonally adjusted",
    horizon="month-over-month",
    threshold_unit="percent",
    rounding_rules="one decimal initial release",
    revision_rules="first-sentence initial release; no post-expiration revision",
    correction_rules="authoritative finalized exchange result; latest final for amendment",
    settlement_authority_name="Bureau of Labor Statistics",
    settlement_authority_url="https://www.bls.gov/cpi/",
    payout_model="simple_binary",
    settlement_type="binary",
    timezone="UTC",
)


def _semantic_policy_identity(policy: KXCPIReviewedSemanticPolicy) -> str:
    return stable_hash(tuple(getattr(policy, field) for field in policy.__dataclass_fields__))


KXCPI_SEMANTIC_POLICY_IDENTITY = _semantic_policy_identity(KXCPI_SEMANTIC_POLICY)


def _validated_policy() -> KXCPIReviewedSemanticPolicy:
    policy = KXCPI_SEMANTIC_POLICY
    if _semantic_policy_identity(policy) != KXCPI_SEMANTIC_POLICY_IDENTITY:
        raise CPISettlementReconciliationError("reviewed KXCPI semantic policy identity changed")
    return policy


def _policy_population(policy: KXCPIReviewedSemanticPolicy) -> CPIPopulation:
    if policy.subject_entities == ("CPI-U",):
        return CPIPopulation.CPI_U
    raise CPISettlementReconciliationError("reviewed policy has an unsupported population")


def _policy_geography(policy: KXCPIReviewedSemanticPolicy) -> CPIGeography:
    if policy.geographic_scope == "U.S. city average":
        return CPIGeography.US_CITY_AVERAGE
    raise CPISettlementReconciliationError("reviewed policy has an unsupported geography")


def _policy_basket(policy: KXCPIReviewedSemanticPolicy) -> CPIBasket:
    if policy.basket == "all items":
        return CPIBasket.ALL_ITEMS
    raise CPISettlementReconciliationError("reviewed policy has an unsupported basket")


def _policy_seasonal_basis(policy: KXCPIReviewedSemanticPolicy) -> CPISeasonalBasis:
    if policy.seasonal_basis == "seasonally adjusted":
        return CPISeasonalBasis.SA
    raise CPISettlementReconciliationError("reviewed policy has an unsupported seasonal basis")


def _policy_horizon(policy: KXCPIReviewedSemanticPolicy) -> CPIHorizon:
    if policy.horizon == "month-over-month":
        return CPIHorizon.MOM
    raise CPISettlementReconciliationError("reviewed policy has an unsupported horizon")


def _policy_unit(policy: KXCPIReviewedSemanticPolicy) -> CPIUnit:
    if policy.threshold_unit == "percent":
        return CPIUnit.PERCENT
    raise CPISettlementReconciliationError("reviewed policy has an unsupported unit")


def _policy_payout_model(policy: KXCPIReviewedSemanticPolicy) -> PayoutModel:
    if policy.payout_model == "simple_binary":
        return PayoutModel.SIMPLE_BINARY
    raise CPISettlementReconciliationError("reviewed policy has an unsupported payout model")


@dataclass(frozen=True, slots=True)
class _PublicHTTPResponse:
    request_url: str
    method: str
    status: int
    raw_body: bytes
    acquired_at: datetime
    redirected: bool = False
    used_credentials: bool = False
    used_cookies: bool = False


@dataclass(frozen=True, slots=True)
class _FixtureManifest:
    fixture_id: str
    role: KalshiEndpointRole
    url: str
    raw_sha256: str
    acquired_at: datetime
    ticker: str | None = None
    event_ticker: str | None = None
    series_ticker: str = "KXCPI"


def _manifest_entry(
    fixture_id: str,
    role: KalshiEndpointRole,
    path: str,
    raw_sha256: str,
    acquired_at: str,
    *,
    ticker: str | None = None,
    event_ticker: str | None = None,
) -> _FixtureManifest:
    return _FixtureManifest(
        fixture_id,
        role,
        f"{KALSHI_BASE}{path}",
        raw_sha256,
        datetime.fromisoformat(acquired_at.replace("Z", "+00:00")),
        ticker,
        event_ticker,
    )


_FIXTURES = {
    "market-jul": _manifest_entry(
        "market-jul",
        KalshiEndpointRole.HISTORICAL_MARKET,
        "/historical/markets/KXCPI-25JUL-T0.1",
        "5531efbd8268f779f5db2bb10b158e772878da9819be497022d6cd4d1b758ae7",
        "2026-08-31T19:54:20Z",
        ticker="KXCPI-25JUL-T0.1",
        event_ticker="KXCPI-25JUL",
    ),
    "market-dec": _manifest_entry(
        "market-dec",
        KalshiEndpointRole.HISTORICAL_MARKET,
        "/historical/markets/KXCPI-25DEC-T0.2",
        "0b8142a93ed9b739e3f685366c7167371e1607228497f206bbd1bfec506bfcfc",
        "2026-08-31T19:54:21Z",
        ticker="KXCPI-25DEC-T0.2",
        event_ticker="KXCPI-25DEC",
    ),
    "market-jan": _manifest_entry(
        "market-jan",
        KalshiEndpointRole.HISTORICAL_MARKET,
        "/historical/markets/KXCPI-26JAN-T0.1",
        "de5164c582f534a608fad0771a369417146281902a01ef1fd031b3ea6f3f2d79",
        "2026-08-31T19:54:23Z",
        ticker="KXCPI-26JAN-T0.1",
        event_ticker="KXCPI-26JAN",
    ),
    "event-jul": _manifest_entry(
        "event-jul",
        KalshiEndpointRole.EVENT,
        "/events/KXCPI-25JUL",
        "b63394b1e12c7750d40277662f3309187601328ec73b413d4a4366bd7e992770",
        "2026-08-31T19:54:24Z",
        event_ticker="KXCPI-25JUL",
    ),
    "event-dec": _manifest_entry(
        "event-dec",
        KalshiEndpointRole.EVENT,
        "/events/KXCPI-25DEC",
        "7ba1eb6e3971520916dcf7a890fd7c7be81d07eaea2941fb19f7f0c1e6effb6b",
        "2026-08-31T19:54:28Z",
        event_ticker="KXCPI-25DEC",
    ),
    "event-jan": _manifest_entry(
        "event-jan",
        KalshiEndpointRole.EVENT,
        "/events/KXCPI-26JAN",
        "7b7947dc42f6ea7e1b0dd4ec7917e97672775a3e21d7417bc0a60f567d9c7ea8",
        "2026-08-31T19:54:44Z",
        event_ticker="KXCPI-26JAN",
    ),
    "series": _manifest_entry(
        "series",
        KalshiEndpointRole.SERIES,
        "/series/KXCPI",
        "f5c410bc20a280d5fc14e33d1b028777a3a88aa6a955938eeee03cc481866e60",
        "2026-08-31T19:55:19Z",
    ),
    "contract-terms": _FixtureManifest(
        "contract-terms",
        KalshiEndpointRole.CONTRACT_TERMS,
        "https://assets.kalshi.com/contract_terms/CPI.pdf",
        CONTRACT_TERMS_SHA256,
        datetime.fromisoformat("2026-08-31T17:33:02+00:00"),
    ),
}


def _utc(value: object, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise CPISettlementReconciliationError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _decimal(value: object, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise CPISettlementReconciliationError(f"{name} must be a decimal value")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise CPISettlementReconciliationError(f"{name} is malformed") from exc
    if not result.is_finite():
        raise CPISettlementReconciliationError(f"{name} is not finite")
    return result


def _time(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise CPISettlementReconciliationError(f"{name} is missing")
    try:
        return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")), name)
    except ValueError as exc:
        raise CPISettlementReconciliationError(f"{name} is malformed") from exc


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CPISettlementReconciliationError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> Any:
    raise CPISettlementReconciliationError(f"non-standard JSON constant: {value}")


def _payload(raw: bytes, role: KalshiEndpointRole) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CPISettlementReconciliationError("raw Kalshi artifact is not valid JSON") from exc
    if type(value) is not dict:
        raise CPISettlementReconciliationError("raw Kalshi artifact must be a JSON object")
    wrapper = {
        KalshiEndpointRole.HISTORICAL_MARKET: "market",
        KalshiEndpointRole.EVENT: "event",
        KalshiEndpointRole.SERIES: "series",
    }.get(role)
    if wrapper is None:
        raise CPISettlementReconciliationError("JSON payload role is unsupported")
    allowed_keys = {wrapper}
    if role is KalshiEndpointRole.EVENT:
        allowed_keys.add("markets")
    if set(value) != allowed_keys or type(value.get(wrapper)) is not dict:
        raise CPISettlementReconciliationError("raw Kalshi artifact wrapper is ambiguous")
    if role is KalshiEndpointRole.EVENT and type(value.get("markets")) is not list:
        raise CPISettlementReconciliationError("event response markets field is malformed")
    return cast(dict[str, Any], value[wrapper])


def _fixture_path(entry: _FixtureManifest) -> Path:
    filename = (
        "CPI-contract-terms.pdf"
        if entry.role is KalshiEndpointRole.CONTRACT_TERMS
        else f"{entry.fixture_id}.json"
    )
    return Path(__file__).with_name("fixtures") / "cpi_p7_public" / filename


def _canonical_url(
    role: KalshiEndpointRole,
    *,
    expected_ticker: str | None,
    expected_event_ticker: str | None,
    expected_series_ticker: str,
) -> str:
    if role is KalshiEndpointRole.CONTRACT_TERMS:
        return CONTRACT_TERMS_URL
    if role is KalshiEndpointRole.HISTORICAL_MARKET:
        if expected_ticker is None or not re.fullmatch(r"KXCPI-[A-Za-z0-9_.-]+", expected_ticker):
            raise CPISettlementReconciliationError("historical market selector is invalid")
        return f"{KALSHI_BASE}/historical/markets/{expected_ticker}"
    if role is KalshiEndpointRole.EVENT:
        if expected_event_ticker is None or not re.fullmatch(
            r"KXCPI-[A-Za-z0-9_.-]+", expected_event_ticker
        ):
            raise CPISettlementReconciliationError("event selector is invalid")
        return f"{KALSHI_BASE}/events/{expected_event_ticker}"
    if role is KalshiEndpointRole.SERIES and expected_series_ticker == "KXCPI":
        return f"{KALSHI_BASE}/series/KXCPI"
    raise CPISettlementReconciliationError("KXCPI series selector is invalid")


def _validate_response(
    response: _PublicHTTPResponse,
    role: KalshiEndpointRole,
    *,
    expected_ticker: str | None,
    expected_event_ticker: str | None,
    expected_series_ticker: str,
) -> None:
    if type(response) is not _PublicHTTPResponse:
        raise CPISettlementReconciliationError("unreviewed HTTP response type")
    parsed = urlsplit(response.request_url)
    if parsed.username is not None or parsed.password is not None:
        raise CPISettlementReconciliationError("userinfo is forbidden in Kalshi URLs")
    if parsed.port is not None or parsed.query or parsed.fragment:
        raise CPISettlementReconciliationError("noncanonical Kalshi URL components are forbidden")
    canonical_url = _canonical_url(
        role,
        expected_ticker=expected_ticker,
        expected_event_ticker=expected_event_ticker,
        expected_series_ticker=expected_series_ticker,
    )
    if response.request_url != canonical_url:
        raise CPISettlementReconciliationError("Kalshi URL does not match reviewed endpoint")
    if response.method != HTTP_METHOD or response.status != SUCCESS_STATUS:
        raise CPISettlementReconciliationError("only reviewed successful GET responses are allowed")
    if response.redirected or response.used_credentials or response.used_cookies:
        raise CPISettlementReconciliationError("redirected or authenticated response is forbidden")
    if (
        type(response.raw_body) is not bytes
        or not response.raw_body
        or len(response.raw_body) > MAX_RESPONSE_BYTES
    ):
        raise CPISettlementReconciliationError("response bytes are empty or exceed the size bound")
    _utc(response.acquired_at, "acquisition timestamp")
    if role is KalshiEndpointRole.CONTRACT_TERMS:
        return


@dataclass(frozen=True, slots=True, init=False, weakref_slot=True)
class KalshiHistoricalAcquisitionEvidence:
    """Issuer-controlled exact response from the reviewed public Kalshi API."""

    fixture_id: str | None
    request_url: str
    method: str
    http_status: int
    raw_response: bytes
    raw_artifact_hash: str
    byte_count: int
    acquired_at: datetime
    endpoint_role: KalshiEndpointRole
    expected_ticker: str | None
    expected_event_ticker: str | None
    expected_series_ticker: str
    research_only: bool
    production_influence: Decimal
    issuance_fingerprint: str
    evidence_id: str

    def __init__(
        self,
        *,
        response: _PublicHTTPResponse,
        role: KalshiEndpointRole,
        fixture_id: str | None = None,
        expected_ticker: str | None = None,
        expected_event_ticker: str | None = None,
        expected_series_ticker: str = "KXCPI",
        _capability: object | None = None,
    ) -> None:
        if _capability is not _ACQUISITION_CAPABILITY:
            raise CPISettlementReconciliationError(
                "Kalshi acquisition requires reviewed transport capability"
            )
        _validate_response(
            response,
            role,
            expected_ticker=expected_ticker,
            expected_event_ticker=expected_event_ticker,
            expected_series_ticker=expected_series_ticker,
        )
        digest = hashlib.sha256(response.raw_body).hexdigest()
        for name, value in {
            "fixture_id": fixture_id,
            "request_url": response.request_url,
            "method": response.method,
            "http_status": response.status,
            "raw_response": response.raw_body,
            "raw_artifact_hash": digest,
            "byte_count": len(response.raw_body),
            "acquired_at": response.acquired_at.astimezone(UTC),
            "endpoint_role": role,
            "expected_ticker": expected_ticker,
            "expected_event_ticker": expected_event_ticker,
            "expected_series_ticker": expected_series_ticker,
            "research_only": True,
            "production_influence": ZERO,
        }.items():
            object.__setattr__(self, name, value)
        fingerprint = _kalshi_acquisition_fingerprint(self)
        object.__setattr__(self, "issuance_fingerprint", fingerprint)
        object.__setattr__(self, "evidence_id", fingerprint)
        _ISSUED_KALSHI_ACQUISITION_EVIDENCE[id(self)] = self


def _kalshi_acquisition_fingerprint(
    evidence: KalshiHistoricalAcquisitionEvidence,
) -> str:
    return stable_hash(
        (
            KALSHI_ACQUISITION_SCHEMA_VERSION,
            evidence.fixture_id,
            evidence.request_url,
            evidence.method,
            evidence.http_status,
            evidence.raw_artifact_hash,
            evidence.byte_count,
            evidence.acquired_at.astimezone(UTC).isoformat(),
            evidence.endpoint_role.value,
            evidence.expected_ticker,
            evidence.expected_event_ticker,
            evidence.expected_series_ticker,
            evidence.research_only,
            str(evidence.production_influence),
        )
    )


def _issue_reviewed_public_get(
    response: _PublicHTTPResponse, role: KalshiEndpointRole, **kwargs: Any
) -> KalshiHistoricalAcquisitionEvidence:
    return KalshiHistoricalAcquisitionEvidence(
        response=response, role=role, _capability=_ACQUISITION_CAPABILITY, **kwargs
    )


def _read_complete_response(response: Any) -> bytes:
    """Read bounded HTTP bytes only when stdlib length/transfer is complete."""
    expected = getattr(response, "length", None)
    if expected is not None:
        if type(expected) is not int or isinstance(expected, bool):
            raise CPISettlementReconciliationError("HTTP response length metadata is invalid")
        if expected < 0:
            raise CPISettlementReconciliationError("HTTP response length metadata is invalid")
        if expected > MAX_RESPONSE_BYTES:
            raise CPISettlementReconciliationError("declared response length exceeded bound")
    try:
        body = response.read(MAX_RESPONSE_BYTES + 1)
    except http.client.IncompleteRead as exc:
        raise CPISettlementReconciliationError("HTTP response was incomplete") from exc
    except http.client.HTTPException as exc:
        raise CPISettlementReconciliationError("HTTP response could not be read") from exc
    if type(body) is not bytes or not body:
        raise CPISettlementReconciliationError("HTTP response body is empty")
    if expected is not None and len(body) != expected:
        raise CPISettlementReconciliationError("HTTP response length does not match body")
    if len(body) > MAX_RESPONSE_BYTES:
        raise CPISettlementReconciliationError("HTTP response exceeded bound")
    return body


def acquire_kalshi_historical_get(
    request_url: str,
    role: KalshiEndpointRole,
    *,
    expected_ticker: str | None = None,
    expected_event_ticker: str | None = None,
) -> KalshiHistoricalAcquisitionEvidence:
    """Perform one reviewed, unauthenticated Kalshi HTTPS GET.

    The caller supplies only a reviewed endpoint selector. Raw bytes, status,
    redirects, cookies, and acquisition time come from this transport seam.
    """
    canonical_url = _canonical_url(
        role,
        expected_ticker=expected_ticker,
        expected_event_ticker=expected_event_ticker,
        expected_series_ticker="KXCPI",
    )
    if request_url != canonical_url:
        raise CPISettlementReconciliationError("request escaped the reviewed Kalshi GET policy")
    parsed = urlsplit(canonical_url)
    if parsed.hostname is None:
        raise CPISettlementReconciliationError("reviewed Kalshi URL has no hostname")
    allowed_terms = role is KalshiEndpointRole.CONTRACT_TERMS
    connection = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=10.0)
    try:
        connection.request(
            "GET",
            parsed.path,
            headers={
                "Accept": "application/pdf" if allowed_terms else "application/json",
                "User-Agent": "kalsh3-cpi-e1-p7/1.0",
            },
        )
        response = connection.getresponse()
        status = response.status
        body = _read_complete_response(response)
    except (
        OSError,
        ValueError,
        http.client.HTTPException,
        CPISettlementReconciliationError,
    ) as exc:
        raise CPISettlementReconciliationError("Kalshi public GET transport failed") from exc
    finally:
        connection.close()
    return _issue_reviewed_public_get(
        _PublicHTTPResponse(
            request_url, HTTP_METHOD, status, body, datetime.now(UTC), status in range(300, 400)
        ),
        role,
        expected_ticker=expected_ticker,
        expected_event_ticker=expected_event_ticker,
        expected_series_ticker="KXCPI",
    )


def load_frozen_kalshi_acquisition(fixture_id: str) -> KalshiHistoricalAcquisitionEvidence:
    """Load only a fixed, content-addressed response from reviewed acquisition."""
    entry = _FIXTURES.get(fixture_id)
    if entry is None:
        raise CPISettlementReconciliationError("unknown reviewed Kalshi fixture")
    try:
        raw = _fixture_path(entry).read_bytes()
    except OSError as exc:
        raise CPISettlementReconciliationError("durable Kalshi fixture is unavailable") from exc
    if hashlib.sha256(raw).hexdigest() != entry.raw_sha256:
        raise CPISettlementReconciliationError("durable Kalshi fixture hash changed")
    return _issue_reviewed_public_get(
        _PublicHTTPResponse(entry.url, HTTP_METHOD, SUCCESS_STATUS, raw, entry.acquired_at),
        entry.role,
        fixture_id=entry.fixture_id,
        expected_ticker=entry.ticker,
        expected_event_ticker=entry.event_ticker,
        expected_series_ticker=entry.series_ticker,
    )


def validate_kalshi_acquisition(evidence: KalshiHistoricalAcquisitionEvidence) -> None:
    if (
        type(evidence) is not KalshiHistoricalAcquisitionEvidence
        or evidence.research_only is not True
        or evidence.production_influence != ZERO
    ):
        raise CPISettlementReconciliationError(
            "Kalshi acquisition type or safety flags are invalid"
        )
    try:
        expected_fingerprint = _kalshi_acquisition_fingerprint(evidence)
    except (AttributeError, TypeError, ValueError) as exc:
        raise CPISettlementReconciliationError(
            "Kalshi acquisition fingerprint cannot be recomputed"
        ) from exc
    if (
        evidence.issuance_fingerprint != expected_fingerprint
        or evidence.evidence_id != expected_fingerprint
        or _ISSUED_KALSHI_ACQUISITION_EVIDENCE.get(id(evidence)) is not evidence
    ):
        raise CPISettlementReconciliationError(
            "unissued, reconstructed, or mutated Kalshi acquisition evidence"
        )
    _validate_response(
        _PublicHTTPResponse(
            evidence.request_url,
            evidence.method,
            evidence.http_status,
            evidence.raw_response,
            evidence.acquired_at,
        ),
        evidence.endpoint_role,
        expected_ticker=evidence.expected_ticker,
        expected_event_ticker=evidence.expected_event_ticker,
        expected_series_ticker=evidence.expected_series_ticker,
    )
    if hashlib.sha256(evidence.raw_response).hexdigest() != evidence.raw_artifact_hash:
        raise CPISettlementReconciliationError("Kalshi raw artifact hash changed")
    if type(evidence.byte_count) is not int or evidence.byte_count != len(evidence.raw_response):
        raise CPISettlementReconciliationError("Kalshi raw artifact byte count changed")
    if evidence.fixture_id is not None:
        entry = _FIXTURES.get(evidence.fixture_id)
        if entry is None or (
            evidence.request_url,
            evidence.endpoint_role,
            evidence.raw_artifact_hash,
            evidence.acquired_at,
            evidence.expected_ticker,
            evidence.expected_event_ticker,
        ) != (
            entry.url,
            entry.role,
            entry.raw_sha256,
            entry.acquired_at,
            entry.ticker,
            entry.event_ticker,
        ):
            raise CPISettlementReconciliationError("Kalshi fixture identity changed")
    if (
        evidence.endpoint_role
        in {
            KalshiEndpointRole.EVENT,
            KalshiEndpointRole.SERIES,
        }
        and evidence.expected_series_ticker != "KXCPI"
    ):
        raise CPISettlementReconciliationError("KXCPI series selector changed")
    if evidence.endpoint_role is KalshiEndpointRole.CONTRACT_TERMS:
        if evidence.raw_artifact_hash != KXCPI_SEMANTIC_POLICY.contract_terms_sha256:
            raise CPISettlementReconciliationError("official CPI contract terms hash changed")
        return
    payload = _payload(evidence.raw_response, evidence.endpoint_role)
    if evidence.endpoint_role is KalshiEndpointRole.HISTORICAL_MARKET and (
        payload.get("ticker") != evidence.expected_ticker
        or payload.get("event_ticker") != evidence.expected_event_ticker
    ):
        raise CPISettlementReconciliationError("historical market selector mismatch")
    if evidence.endpoint_role is KalshiEndpointRole.EVENT and (
        payload.get("event_ticker") != evidence.expected_event_ticker
        or payload.get("series_ticker") != evidence.expected_series_ticker
    ):
        raise CPISettlementReconciliationError("event selector mismatch")
    if evidence.endpoint_role is KalshiEndpointRole.SERIES and (
        payload.get("ticker") != evidence.expected_series_ticker
    ):
        raise CPISettlementReconciliationError("series selector mismatch")


def _reference_period(text: str) -> tuple[int, int]:
    match = re.search(
        r"in\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})",
        text,
        re.I,
    )
    if match is None:
        raise CPISettlementReconciliationError("historical rules lack an exact reference month")
    months = [
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
    ]
    return int(match.group(2)), months.index(match.group(1).lower()) + 1


def _semantic_fields(spec: ContractSpecification) -> tuple[object, ...]:
    return (
        spec.market_ticker,
        spec.event_ticker,
        spec.series_ticker,
        spec.rules_version_id,
        spec.metadata_version_id,
        spec.market_rules_hash,
        spec.market_metadata_hash,
        spec.yes_proposition,
        spec.no_proposition,
        spec.settlement_type,
        spec.payout_model.value,
        spec.measured_event_or_value,
        spec.subject_entities,
        spec.geographic_scope,
        spec.comparator.value,
        spec.threshold_value,
        spec.threshold_unit,
        spec.occurrence_time,
        spec.expected_expiration,
        spec.settlement_authority,
        tuple(x.source_hash for x in spec.settlement_sources),
        spec.source_precedence_status,
        spec.rounding_rules,
        spec.revision_rules,
        spec.correction_rules,
        spec.strike_type,
        spec.functional_strike,
        spec.custom_strike,
        spec.semantic_status.value,
        spec.source_input_hash,
        spec.semantic_hash,
    )


def _build_specification(
    market_evidence: KalshiHistoricalAcquisitionEvidence,
    event_evidence: KalshiHistoricalAcquisitionEvidence,
    series_evidence: KalshiHistoricalAcquisitionEvidence,
    terms_evidence: KalshiHistoricalAcquisitionEvidence,
) -> ContractSpecification:
    validate_kalshi_acquisition(terms_evidence)
    if terms_evidence.endpoint_role is not KalshiEndpointRole.CONTRACT_TERMS:
        raise CPISettlementReconciliationError("official KXCPI contract terms are required")
    market, event, series = (
        dict(_payload(market_evidence.raw_response, KalshiEndpointRole.HISTORICAL_MARKET)),
        dict(_payload(event_evidence.raw_response, KalshiEndpointRole.EVENT)),
        dict(_payload(series_evidence.raw_response, KalshiEndpointRole.SERIES)),
    )
    if (
        market.get("event_ticker") != event.get("event_ticker")
        or event.get("series_ticker") != series.get("ticker")
        or series.get("ticker") != "KXCPI"
    ):
        raise CPISettlementReconciliationError("historical market/event/series identities conflict")
    if terms_evidence.raw_artifact_hash != KXCPI_SEMANTIC_POLICY.contract_terms_sha256:
        raise CPISettlementReconciliationError(
            "official CPI contract terms are not the reviewed artifact"
        )
    policy = _validated_policy()
    if series.get("contract_terms_url") != policy.contract_terms_url:
        raise CPISettlementReconciliationError(
            "KXCPI series does not bind the reviewed official contract terms"
        )
    rules_primary = str(market.get("rules_primary", ""))
    rules_secondary = str(market.get("rules_secondary", ""))
    required_primary_terms = (
        "consumer price index",
        "increases",
        "single-decimal",
        "%",
    )
    if any(term not in rules_primary.casefold() for term in required_primary_terms):
        raise CPISettlementReconciliationError(
            "historical market rules do not prove the reviewed KXCPI observation domain"
        )
    if "single-decimal" not in rules_secondary.casefold():
        raise CPISettlementReconciliationError(
            "historical market rules do not prove the settlement precision"
        )
    event_sources = event.get("settlement_sources")
    series_sources = series.get("settlement_sources")
    if event_sources != series_sources or not event_sources:
        raise CPISettlementReconciliationError(
            "historical settlement authority conflicts with KXCPI series authority"
        )
    if not any(
        type(source) is dict
        and source.get("name") == policy.settlement_authority_name
        and source.get("url") == policy.settlement_authority_url
        for source in event_sources
    ):
        raise CPISettlementReconciliationError("historical BLS settlement authority is unproven")
    year, month = _reference_period(str(market.get("rules_primary", "")))
    rules_hash, metadata_hash = material_hashes(market)
    market.update(
        {
            "rules_version_id": HISTORICAL_RULES_VERSION_PREFIX + rules_hash,
            "metadata_version_id": "historical-market-metadata-v1:" + metadata_hash,
            "measured_event_or_value": policy.measured_event_or_value,
            "subject_entities": list(policy.subject_entities),
            "geographic_scope": policy.geographic_scope,
            "threshold_unit": policy.threshold_unit,
            "rounding_rules": policy.rounding_rules,
            "revision_rules": policy.revision_rules,
            "correction_rules": policy.correction_rules,
            "settlement_type": policy.settlement_type,
            "payout_model": policy.payout_model,
            "timezone": policy.timezone,
            "occurrence_datetime": f"{year:04d}-{month:02d}-01T00:00:00Z",
            "settlement_sources": event.get("settlement_sources", []),
            "settlement_value_dollars": None,
        }
    )
    event["timezone"] = policy.timezone
    series["timezone"] = policy.timezone
    spec = ContractSpecificationParser().parse(
        SemanticsInputBundle.build(market, event, series), datetime(2026, 8, 31, 12, tzinfo=UTC)
    )
    from dataclasses import replace

    return replace(
        spec,
        market_rules_hash=rules_hash,
        market_metadata_hash=metadata_hash,
        rules_version_id=HISTORICAL_RULES_VERSION_PREFIX + rules_hash,
        metadata_version_id="historical-market-metadata-v1:" + metadata_hash,
        settlement_type=policy.settlement_type,
        payout_model=_policy_payout_model(policy),
        timezone=policy.timezone,
        semantic_status=SemanticStatus.VALID,
    )


@dataclass(frozen=True, slots=True, init=False)
class CPIHistoricalSemanticEvidence:
    market: KalshiHistoricalAcquisitionEvidence
    event: KalshiHistoricalAcquisitionEvidence
    series: KalshiHistoricalAcquisitionEvidence
    contract_terms: KalshiHistoricalAcquisitionEvidence
    specification: ContractSpecification
    semantic_evidence_id: str

    def __init__(
        self,
        *,
        market: KalshiHistoricalAcquisitionEvidence,
        event: KalshiHistoricalAcquisitionEvidence,
        series: KalshiHistoricalAcquisitionEvidence,
        contract_terms: KalshiHistoricalAcquisitionEvidence,
        specification: ContractSpecification,
        _capability: object | None = None,
    ) -> None:
        if _capability is not _ACQUISITION_CAPABILITY:
            raise CPISettlementReconciliationError(
                "semantic evidence requires reviewed acquisition inputs"
            )
        for item in (market, event, series, contract_terms):
            validate_kalshi_acquisition(item)
        rebuilt = _build_specification(market, event, series, contract_terms)
        if _semantic_fields(specification) != _semantic_fields(rebuilt):
            raise CPISettlementReconciliationError(
                "caller-authored semantic specification rejected"
            )
        for name, value in {
            "market": market,
            "event": event,
            "series": series,
            "contract_terms": contract_terms,
            "specification": rebuilt,
            "semantic_evidence_id": stable_hash(
                (
                    market.evidence_id,
                    event.evidence_id,
                    series.evidence_id,
                    contract_terms.evidence_id,
                    rebuilt.semantic_hash,
                    KXCPI_SEMANTIC_POLICY_IDENTITY,
                )
            ),
        }.items():
            object.__setattr__(self, name, value)


def build_historical_semantic_evidence(
    market: KalshiHistoricalAcquisitionEvidence,
    event: KalshiHistoricalAcquisitionEvidence,
    series: KalshiHistoricalAcquisitionEvidence,
    contract_terms: KalshiHistoricalAcquisitionEvidence,
) -> CPIHistoricalSemanticEvidence:
    return CPIHistoricalSemanticEvidence(
        market=market,
        event=event,
        series=series,
        contract_terms=contract_terms,
        specification=_build_specification(market, event, series, contract_terms),
        _capability=_ACQUISITION_CAPABILITY,
    )


@dataclass(frozen=True, slots=True)
class KalshiFinalizedEvidence:
    acquisition: KalshiHistoricalAcquisitionEvidence
    raw_response: bytes
    raw_artifact_hash: str
    determination: ExchangeDetermination
    research_only: bool = True
    production_influence: Decimal = ZERO

    @classmethod
    def from_acquisition(
        cls, acquisition: KalshiHistoricalAcquisitionEvidence
    ) -> KalshiFinalizedEvidence:
        validate_kalshi_acquisition(acquisition)
        if acquisition.endpoint_role is not KalshiEndpointRole.HISTORICAL_MARKET:
            raise CPISettlementReconciliationError(
                "only historical market responses determine settlement"
            )
        payload = _payload(acquisition.raw_response, KalshiEndpointRole.HISTORICAL_MARKET)
        if (
            any(
                key not in payload
                for key in (
                    "ticker",
                    "status",
                    "result",
                    "settlement_value_dollars",
                    "settlement_ts",
                )
            )
            or str(payload["status"]).casefold() != "finalized"
        ):
            raise CPISettlementReconciliationError("historical market is not explicitly finalized")
        result = str(payload["result"]).upper()
        value = _decimal(payload["settlement_value_dollars"], "settlement_value_dollars")
        if result not in {"YES", "NO"} or value != (ONE if result == "YES" else ZERO):
            raise CPISettlementReconciliationError("historical binary result/value is invalid")
        determined = _time(payload["settlement_ts"], "settlement_ts")
        if determined > acquisition.acquired_at:
            raise CPISettlementReconciliationError("historical determination follows acquisition")
        determination = ExchangeDetermination(
            str(payload["ticker"]) + "-settlement",
            str(payload["ticker"]),
            DeterminationState.FINALIZED,
            result,
            value,
            determined,
            acquisition.acquired_at,
            acquisition.raw_artifact_hash,
            None,
        )
        return cls(
            acquisition, acquisition.raw_response, acquisition.raw_artifact_hash, determination
        )


def validate_exchange_evidence(evidence: KalshiFinalizedEvidence) -> None:
    if type(evidence) is not KalshiFinalizedEvidence:
        raise CPISettlementReconciliationError("exchange evidence has wrong runtime type")
    validate_kalshi_acquisition(evidence.acquisition)
    if evidence != KalshiFinalizedEvidence.from_acquisition(evidence.acquisition):
        raise CPISettlementReconciliationError("caller-mutated finalized exchange evidence")


def _validate_observation(observation: CPIInitialReleaseObservation) -> None:
    policy = _validated_policy()
    try:
        validate_cpi_initial_release_observation(observation)
    except ValueError as exc:
        raise CPISettlementReconciliationError(
            "P6 observation failed transitive validation"
        ) from exc
    if (
        observation.unit is not _policy_unit(policy)
        or observation.seasonal_basis is not _policy_seasonal_basis(policy)
        or observation.horizon is not _policy_horizon(policy)
        or observation.basket is not _policy_basket(policy)
        or observation.population is not _policy_population(policy)
        or observation.geography is not _policy_geography(policy)
    ):
        raise CPISettlementReconciliationError("P6 observation is outside the exact CPI domain")


def _validate_specification(
    observation: CPIInitialReleaseObservation, spec: ContractSpecification
) -> None:
    policy = _validated_policy()
    _validate_observation(observation)
    if (
        type(spec) is not ContractSpecification
        or spec.semantic_status is not SemanticStatus.VALID
        or spec.payout_model is not _policy_payout_model(policy)
    ):
        raise CPISettlementReconciliationError(
            "contract semantics are not valid simple binary semantics"
        )
    if (
        spec.series_ticker != "KXCPI"
        or not spec.market_ticker.startswith("KXCPI-")
        or not spec.event_ticker.startswith("KXCPI-")
    ):
        raise CPISettlementReconciliationError("contract is not the intended KXCPI family")
    if (
        spec.comparator not in SUPPORTED_COMPARATORS
        or spec.threshold_value is None
        or spec.threshold_unit != policy.threshold_unit
    ):
        raise CPISettlementReconciliationError("contract comparator or threshold is unsupported")
    if (
        spec.measured_event_or_value != policy.measured_event_or_value
        or spec.subject_entities != policy.subject_entities
        or spec.geographic_scope != policy.geographic_scope
    ):
        raise CPISettlementReconciliationError("contract measured domain is not the P6 CPI domain")
    if (
        spec.rounding_rules != policy.rounding_rules
        or spec.revision_rules != policy.revision_rules
        or spec.correction_rules != policy.correction_rules
        or spec.settlement_type != policy.settlement_type
        or spec.timezone != policy.timezone
    ):
        raise CPISettlementReconciliationError("contract policy is not exact")
    if (
        spec.ambiguities
        or spec.contradictions
        or spec.unsupported_features
        or not spec.settlement_authority
        or not spec.settlement_sources
    ):
        raise CPISettlementReconciliationError("contract contains unresolved semantic issues")
    if spec.occurrence_time and (observation.reference_year, observation.reference_month) != (
        spec.occurrence_time.year,
        spec.occurrence_time.month,
    ):
        raise CPISettlementReconciliationError("P6 reference month conflicts with contract")


def expected_binary_result(
    observation: CPIInitialReleaseObservation, semantic: CPIHistoricalSemanticEvidence
) -> ExpectedBinaryResult:
    if type(semantic) is not CPIHistoricalSemanticEvidence:
        raise CPISettlementReconciliationError("trusted historical semantic evidence is required")
    rebuilt = _build_specification(
        semantic.market, semantic.event, semantic.series, semantic.contract_terms
    )
    if _semantic_fields(semantic.specification) != _semantic_fields(rebuilt):
        raise CPISettlementReconciliationError("semantic evidence was mutated")
    spec = rebuilt
    _validate_specification(observation, spec)
    if spec.threshold_value is None:
        raise CPISettlementReconciliationError("contract threshold is missing")
    yes = {
        Comparator.GT: observation.value > spec.threshold_value,
        Comparator.GTE: observation.value >= spec.threshold_value,
        Comparator.LT: observation.value < spec.threshold_value,
        Comparator.LTE: observation.value <= spec.threshold_value,
        Comparator.EQ: observation.value == spec.threshold_value,
    }[spec.comparator]
    return ExpectedBinaryResult.YES if yes else ExpectedBinaryResult.NO


def reconcile_cpi_settlement(
    observation: CPIInitialReleaseObservation,
    semantic: CPIHistoricalSemanticEvidence,
    exchange: KalshiFinalizedEvidence,
) -> SettlementRecord:
    if type(semantic) is not CPIHistoricalSemanticEvidence:
        raise CPISettlementReconciliationError("trusted historical semantic evidence is required")
    validate_exchange_evidence(exchange)
    spec = _build_specification(
        semantic.market, semantic.event, semantic.series, semantic.contract_terms
    )
    if (
        _semantic_fields(semantic.specification) != _semantic_fields(spec)
        or exchange.acquisition.raw_artifact_hash != semantic.market.raw_artifact_hash
    ):
        raise CPISettlementReconciliationError(
            "semantic or exchange evidence was not transitively bound"
        )
    _validate_specification(observation, spec)
    if (
        exchange.determination.result is None
        or exchange.determination.settlement_value_dollars is None
    ):
        raise CPISettlementReconciliationError("historical determination is incomplete")
    status = (
        ReconciliationStatus.MATCHED
        if exchange.determination.result == expected_binary_result(observation, semantic)
        else ReconciliationStatus.MISMATCH
    )
    return SettlementRecord(
        market_ticker=spec.market_ticker,
        rules_version=spec.rules_version_id,
        semantic_spec_id=spec.semantic_hash,
        result=exchange.determination.result,
        settlement_value_dollars=exchange.determination.settlement_value_dollars,
        determined_at=exchange.determination.exchange_at,
        finalized_at=exchange.determination.exchange_at,
        exchange_record_hash=exchange.raw_artifact_hash,
        source_observation_id=observation.observation_id,
        reconciliation_status=status,
    )
