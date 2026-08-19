"""Authoritative exact-market snapshot: shared by M27J (current side) and M27A's authoritative
economics builder (expected side).

An :class:`AuthoritativeMarketSnapshot` is produced by exactly one acquisition path -- a single
bounded, unauthenticated PUBLIC GET of the exact-market endpoint
(:func:`services.market_universe.public_read.get_market_with_body`) -- and its rules/metadata
hashes are always derived by running the exact received bytes through the canonical
:meth:`services.market_universe.domain.Market.parse`. Nothing here invents a second hash
algorithm.

Trust model (be precise): neither this snapshot nor anything downstream of it is server-signed.
The guarantee is narrower: this is operator-supervised evidence acquired through a fixed,
reviewed, public GET transport, with the exact response bytes retained and hash-bound, and the
canonical Market parser rerun at every validation -- never trusted merely because a field says
so. SHA-256 proves the integrity of the *recorded* bytes, not their server origin by itself,
origin authority comes only from the reviewed acquisition boundary
(:mod:`services.market_universe.public_read`) having actually been the one that made the request.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from services.market_universe.domain import Market, UniverseValidationError
from services.market_universe.public_read import (
    BASE,
    PublicReadFailure,
    get_market_with_body,
)
from services.market_universe.public_read import (
    HOST as _RAW_HOST,
)

SCHEMA = "kalsh3.market_universe.authoritative-market-snapshot.v1"
PARSER_VERSION = "kalsh3.market_universe.domain.Market.parse/1"
HOST = "https://" + _RAW_HOST

# Ephemeral live evidence: a short, fixed bound reused for two distinct purposes by two
# different callers -- how stale current-side evidence may be relative to preflight consumption
# time (see services.supervised_canary.m27j), and the maximum acquisition/evaluation skew allowed
# between an expected-side snapshot and the economics evaluation it backs (see
# services.opportunity_engine.authoritative_economics). Both reuse this one reviewed number
# rather than each inventing their own window.
FRESHNESS = timedelta(seconds=30)

# A single market object is always small; this is a defense-in-depth bound distinct from (and
# far tighter than) M27E's general 8MB pagination-response cap.
MAX_MARKET_BODY_BYTES = 200_000
_MAX_BODY_B64_CHARS = 4 * ((MAX_MARKET_BODY_BYTES // 3) + 1) * 2  # generous pre-decode guard


@dataclass(frozen=True, slots=True)
class AuthoritativeMarketSnapshot:
    schema: str
    software_version: str
    environment: str
    host: str
    path: str | None
    ticker: str
    http_status: int | None
    observed_at: datetime
    expires_at: datetime
    body_sha256: str | None
    raw_body_b64: str | None
    parsed_market_ticker: str | None
    parsed_event_ticker: str | None
    source_updated_at: datetime | None
    rules_hash: str | None
    metadata_hash: str | None
    parser_version: str
    classification: str
    reason: str | None

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "software_version": self.software_version,
            "environment": self.environment,
            "host": self.host,
            "path": self.path,
            "ticker": self.ticker,
            "http_status": self.http_status,
            "observed_at": self.observed_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "body_sha256": self.body_sha256,
            "raw_body_b64": self.raw_body_b64,
            "parsed_market_ticker": self.parsed_market_ticker,
            "parsed_event_ticker": self.parsed_event_ticker,
            "source_updated_at": (
                self.source_updated_at.isoformat() if self.source_updated_at is not None else None
            ),
            "rules_hash": self.rules_hash,
            "metadata_hash": self.metadata_hash,
            "parser_version": self.parser_version,
            "classification": self.classification,
            "reason": self.reason,
        }

    @property
    def succeeded(self) -> bool:
        return self.classification == "SUCCESS"


_SNAPSHOT_FIELDS = frozenset(AuthoritativeMarketSnapshot.__dataclass_fields__)


def _failed(
    ticker: str,
    observed_at: datetime,
    classification: str,
    reason: str,
    *,
    path: str | None = None,
    http_status: int | None = None,
    body_sha256: str | None = None,
) -> AuthoritativeMarketSnapshot:
    return AuthoritativeMarketSnapshot(
        SCHEMA,
        "kalsh3.market_universe.market_snapshot/1",
        "PRODUCTION",
        HOST,
        path,
        ticker,
        http_status,
        observed_at,
        observed_at + FRESHNESS,
        body_sha256,
        None,
        None,
        None,
        None,
        None,
        None,
        PARSER_VERSION,
        classification,
        reason,
    )


def acquire_market_snapshot(
    ticker: str,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    transport: Callable[[str], tuple[dict[str, object], bytes]] = get_market_with_body,
) -> AuthoritativeMarketSnapshot:
    """Single bounded PUBLIC GET of the exact-market endpoint for ``ticker``.

    PUBLIC GET only: no credentials, fixed production origin, exact candidate ticker path, TLS,
    no redirects, bounded timeout, bounded response size, HTTP 200 only, exact JSON envelope, one
    market only.

    Never trusts a caller-supplied rules hash: ``rules_hash``/``metadata_hash`` are always
    derived by running the exact received raw market object through
    :meth:`services.market_universe.domain.Market.parse`. The exact raw response bytes are
    retained (bounded, base64) so :func:`validate_market_snapshot` can independently re-derive
    both the body hash and the rules hash from a deserialized artifact, rather than trusting
    either stamped value in isolation.
    """
    started = clock()
    try:
        evidence, body = transport(ticker)
    except PublicReadFailure as exc:
        return _failed(ticker, started, "ACQUISITION_FAILURE", str(exc))

    observed_at = _parse_timestamp(evidence.get("observed_at")) or started
    expected_path = f"{BASE}/markets/{ticker}"
    path = evidence.get("path")
    status = evidence.get("status")
    body_sha256 = evidence.get("body_sha256")
    http_status = status if isinstance(status, int) else None
    path_str = path if isinstance(path, str) else None
    sha_str = body_sha256 if isinstance(body_sha256, str) else None

    if evidence.get("classification") != "SUCCESS" or status != 200 or path != expected_path:
        return _failed(
            ticker,
            observed_at,
            "HTTP_OR_NETWORK_FAILURE",
            f"acquisition did not succeed (classification={evidence.get('classification')!r}, "
            f"status={status!r})",
            path=path_str,
            http_status=http_status,
            body_sha256=sha_str,
        )
    if len(body) > MAX_MARKET_BODY_BYTES:
        return _failed(
            ticker,
            observed_at,
            "OVERSIZED_BODY",
            "market response exceeded the bounded single-market size",
            path=path_str,
            http_status=http_status,
        )
    if sha_str is None or hashlib.sha256(body).hexdigest() != sha_str:
        return _failed(
            ticker,
            observed_at,
            "MALFORMED_ENVELOPE",
            "acquisition body hash did not match the exact received bytes",
            path=path_str,
            http_status=http_status,
        )
    payload = evidence.get("payload")
    if not isinstance(payload, dict) or not isinstance(payload.get("market"), dict):
        return _failed(
            ticker,
            observed_at,
            "MALFORMED_ENVELOPE",
            "response envelope did not contain a single market object",
            path=path_str,
            http_status=http_status,
            body_sha256=sha_str,
        )
    raw_market = payload["market"]
    if raw_market.get("ticker") != ticker:
        return _failed(
            ticker,
            observed_at,
            "MARKET_IDENTITY_MISMATCH",
            "response market ticker did not match the requested ticker",
            path=path_str,
            http_status=http_status,
            body_sha256=sha_str,
        )
    try:
        market = Market.parse(raw_market)
    except UniverseValidationError as exc:
        return _failed(
            ticker,
            observed_at,
            "MALFORMED_ENVELOPE",
            f"market payload failed canonical parse: {exc}",
            path=path_str,
            http_status=http_status,
            body_sha256=sha_str,
        )
    if market.ticker != ticker:
        return _failed(
            ticker,
            observed_at,
            "MARKET_IDENTITY_MISMATCH",
            "parsed market ticker did not match the requested ticker",
            path=path_str,
            http_status=http_status,
            body_sha256=sha_str,
        )

    return AuthoritativeMarketSnapshot(
        SCHEMA,
        "kalsh3.market_universe.market_snapshot/1",
        "PRODUCTION",
        HOST,
        expected_path,
        ticker,
        200,
        observed_at,
        observed_at + FRESHNESS,
        sha_str,
        base64.b64encode(body).decode("ascii"),
        market.ticker,
        market.event_ticker,
        market.source_updated_at,
        market.rules_hash,
        market.metadata_hash,
        PARSER_VERSION,
        "SUCCESS",
        None,
    )


@dataclass(frozen=True, slots=True)
class SnapshotValidation:
    classification: str
    reason: str | None = None
    rules_hash: str | None = None
    metadata_hash: str | None = None
    ticker: str | None = None
    event_ticker: str | None = None
    observed_at: datetime | None = None
    body_sha256: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.classification == "PASS"


def validate_market_snapshot(
    payload: object,
    *,
    expected_ticker: str,
    expected_event_ticker: str,
) -> SnapshotValidation:
    """Independently re-validate a serialized :class:`AuthoritativeMarketSnapshot` payload.

    This is the ONE shared validation routine for both the current side (M27J,
    ``now``-relative freshness layered on top by the caller) and the expected side (M27A's
    authoritative economics binding, acquisition/evaluation-skew freshness layered on top by the
    caller) -- it deliberately does *not* itself compare against ``now``, because "fresh relative
    to what" differs by caller. What it *does* always independently re-derive and cross-check:
    exact schema/origin/path/envelope, the raw body hash, and the rules/metadata hashes recomputed
    from a fresh :meth:`Market.parse` of the exact retained bytes -- never the merely stamped
    values in isolation.
    """
    if not isinstance(payload, dict) or set(payload) != _SNAPSHOT_FIELDS:
        return SnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "snapshot payload has unexpected or missing fields"
        )
    if payload.get("schema") != SCHEMA:
        return SnapshotValidation("MALFORMED_SNAPSHOT_EVIDENCE", "snapshot schema mismatch")
    if payload.get("classification") != "SUCCESS":
        return SnapshotValidation(
            "SOURCE_AUTHORITY_MISMATCH", "snapshot acquisition did not succeed"
        )
    if payload.get("environment") != "PRODUCTION":
        return SnapshotValidation(
            "SOURCE_AUTHORITY_MISMATCH", "snapshot environment is not PRODUCTION"
        )
    if payload.get("host") != HOST:
        return SnapshotValidation(
            "SOURCE_AUTHORITY_MISMATCH", "snapshot host is not the fixed production origin"
        )
    if payload.get("parser_version") != PARSER_VERSION:
        return SnapshotValidation(
            "SOURCE_AUTHORITY_MISMATCH", "snapshot parser version does not match this reviewer"
        )
    if payload.get("ticker") != expected_ticker:
        return SnapshotValidation(
            "MARKET_IDENTITY_MISMATCH", "snapshot ticker does not match the expected candidate"
        )
    expected_path = f"{BASE}/markets/{expected_ticker}"
    if payload.get("path") != expected_path:
        return SnapshotValidation(
            "SOURCE_AUTHORITY_MISMATCH", "snapshot path is not the exact expected market path"
        )
    if payload.get("http_status") != 200:
        return SnapshotValidation("SOURCE_AUTHORITY_MISMATCH", "snapshot HTTP status was not 200")

    observed_at = _parse_timestamp(payload.get("observed_at"))
    expires_at = _parse_timestamp(payload.get("expires_at"))
    if observed_at is None or expires_at is None:
        return SnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "snapshot timestamps are malformed or naive"
        )
    if expires_at <= observed_at or expires_at > observed_at + FRESHNESS:
        return SnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE",
            "snapshot expiry does not satisfy the fixed freshness bound",
        )

    body_sha256 = payload.get("body_sha256")
    raw_body_b64 = payload.get("raw_body_b64")
    if (
        not isinstance(body_sha256, str)
        or not body_sha256
        or not isinstance(raw_body_b64, str)
        or not raw_body_b64
    ):
        return SnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "snapshot is missing raw body material"
        )
    if len(raw_body_b64) > _MAX_BODY_B64_CHARS:
        return SnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "snapshot raw body exceeds the bounded size"
        )
    try:
        body = base64.b64decode(raw_body_b64, validate=True)
    except (binascii.Error, ValueError):
        return SnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "snapshot raw body is not valid base64"
        )
    if len(body) > MAX_MARKET_BODY_BYTES:
        return SnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "snapshot raw body exceeds the bounded size"
        )
    if hashlib.sha256(body).hexdigest() != body_sha256:
        return SnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE",
            "snapshot raw body does not match its recorded body_sha256",
        )

    try:
        parsed_body = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return SnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "snapshot raw body is not valid JSON"
        )
    if not isinstance(parsed_body, dict) or not isinstance(parsed_body.get("market"), dict):
        return SnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE",
            "snapshot raw body envelope does not contain a single market object",
        )
    raw_market = parsed_body["market"]
    if raw_market.get("ticker") != expected_ticker:
        return SnapshotValidation(
            "MARKET_IDENTITY_MISMATCH",
            "raw body market ticker does not match the expected candidate",
        )
    try:
        market = Market.parse(raw_market)
    except UniverseValidationError:
        return SnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "raw body market payload failed canonical parse"
        )
    if market.ticker != expected_ticker:
        return SnapshotValidation(
            "MARKET_IDENTITY_MISMATCH",
            "re-parsed market ticker does not match the expected candidate",
        )
    if market.event_ticker != expected_event_ticker:
        return SnapshotValidation(
            "MARKET_IDENTITY_MISMATCH",
            "re-parsed market event ticker does not match the expected candidate event",
        )
    if (
        payload.get("parsed_market_ticker") != market.ticker
        or payload.get("parsed_event_ticker") != market.event_ticker
        or payload.get("rules_hash") != market.rules_hash
        or payload.get("metadata_hash") != market.metadata_hash
    ):
        return SnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE",
            "stamped snapshot fields do not match an independent re-parse of the raw body",
        )

    return SnapshotValidation(
        "PASS",
        None,
        rules_hash=market.rules_hash,
        metadata_hash=market.metadata_hash,
        ticker=market.ticker,
        event_ticker=market.event_ticker,
        observed_at=observed_at,
        body_sha256=body_sha256,
    )


def _parse_timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(UTC)
