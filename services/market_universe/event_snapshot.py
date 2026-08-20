"""Authoritative exact-event snapshot: the acquisition/envelope-provenance boundary for Event
evidence.

Mirrors :mod:`services.market_universe.market_snapshot`'s exact shape and trust model, but for
Event data rather than Market data: an :class:`AuthoritativeEventSnapshot` is produced by exactly
one acquisition path -- a single bounded, unauthenticated PUBLIC GET of the exact-event endpoint
(:func:`services.market_universe.public_read.get_event_with_body`) -- and its metadata hash is
always derived by running the exact received bytes through the canonical
:meth:`services.market_universe.domain.Event.parse`. Nothing here invents a second hash
algorithm.

Trust model (be precise, identical to ``market_snapshot``'s): neither this snapshot nor anything
downstream of it is server-signed. The guarantee is narrower: this is operator-supervised
evidence acquired through a fixed, reviewed, public GET transport, with the exact response bytes
retained and hash-bound, and the canonical Event parser rerun at every validation -- never
trusted merely because a field says so. SHA-256 proves the integrity of the *recorded* bytes, not
their server origin by itself; origin authority comes only from the reviewed acquisition boundary
(:mod:`services.market_universe.public_read`) having actually been the one that made the request.

Freshness: reuses :data:`services.market_universe.market_snapshot.FRESHNESS` verbatim as a
self-consistency bound on this snapshot's own ``expires_at`` -- this module does not invent a new
number. Unlike :mod:`services.market_universe.orderbook_snapshot` (which enforces now-relative
freshness itself inside its validator, because orderbook levels are actively live), this module's
:func:`validate_event_snapshot` mirrors :func:`services.market_universe.market_snapshot.
validate_market_snapshot`'s choice to defer any now-relative freshness check entirely to the
caller: Event metadata (title, category, series_ticker, the settlement-relevant raw fields
:func:`services.forecasting.daily_temperature.route_daily_temperature` reads) is, if anything,
less volatile than Market rules, and no existing reviewed consumer requires now-relative event
freshness gating today. Adding one here would be inventing a security property nothing currently
relies on, not reusing one.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from services.market_universe.domain import Event, UniverseValidationError
from services.market_universe.market_snapshot import FRESHNESS, MAX_MARKET_BODY_BYTES
from services.market_universe.public_read import (
    BASE,
    PublicReadFailure,
    get_event_with_body,
)
from services.market_universe.public_read import (
    HOST as _RAW_HOST,
)

SCHEMA = "kalsh3.market_universe.authoritative-event-snapshot.v1"
PARSER_VERSION = "kalsh3.market_universe.domain.Event.parse/1"
HOST = "https://" + _RAW_HOST

# A single event object is always small, exactly like a single market object -- reuses
# market_snapshot's own reviewed bound rather than inventing a second arbitrary number for the
# same class of single-object envelope.
MAX_EVENT_BODY_BYTES = MAX_MARKET_BODY_BYTES
_MAX_BODY_B64_CHARS = 4 * ((MAX_EVENT_BODY_BYTES // 3) + 1) * 2  # generous pre-decode guard


@dataclass(frozen=True, slots=True)
class AuthoritativeEventSnapshot:
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
    parsed_event_ticker: str | None
    parsed_series_ticker: str | None
    source_updated_at: datetime | None
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
            "parsed_event_ticker": self.parsed_event_ticker,
            "parsed_series_ticker": self.parsed_series_ticker,
            "source_updated_at": (
                self.source_updated_at.isoformat() if self.source_updated_at is not None else None
            ),
            "metadata_hash": self.metadata_hash,
            "parser_version": self.parser_version,
            "classification": self.classification,
            "reason": self.reason,
        }

    @property
    def succeeded(self) -> bool:
        return self.classification == "SUCCESS"


_SNAPSHOT_FIELDS = frozenset(AuthoritativeEventSnapshot.__dataclass_fields__)


def _failed(
    ticker: str,
    observed_at: datetime,
    classification: str,
    reason: str,
    *,
    path: str | None = None,
    http_status: int | None = None,
    body_sha256: str | None = None,
) -> AuthoritativeEventSnapshot:
    return AuthoritativeEventSnapshot(
        SCHEMA,
        "kalsh3.market_universe.event_snapshot/1",
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
        PARSER_VERSION,
        classification,
        reason,
    )


def acquire_event_snapshot(
    ticker: str,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    transport: Callable[[str], tuple[dict[str, object], bytes]] = get_event_with_body,
) -> AuthoritativeEventSnapshot:
    """Single bounded PUBLIC GET of the exact-event endpoint for ``ticker``.

    PUBLIC GET only: no credentials, fixed production origin, exact candidate ticker path, TLS,
    no redirects, bounded timeout, bounded response size, HTTP 200 only, exact JSON envelope, one
    event only. Mirrors :func:`services.market_universe.market_snapshot.acquire_market_snapshot`
    exactly.

    Never trusts a caller-supplied metadata hash: ``metadata_hash`` is always derived by running
    the exact received raw event object through :meth:`services.market_universe.domain.
    Event.parse`. The exact raw response bytes are retained (bounded, base64) so
    :func:`validate_event_snapshot` can independently re-derive both the body hash and the
    metadata hash from a deserialized artifact, rather than trusting either stamped value in
    isolation.
    """
    started = clock()
    try:
        evidence, body = transport(ticker)
    except PublicReadFailure as exc:
        return _failed(ticker, started, "ACQUISITION_FAILURE", str(exc))

    observed_at = _parse_timestamp(evidence.get("observed_at")) or started
    expected_path = f"{BASE}/events/{ticker}"
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
    if len(body) > MAX_EVENT_BODY_BYTES:
        return _failed(
            ticker,
            observed_at,
            "OVERSIZED_BODY",
            "event response exceeded the bounded single-event size",
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
    if not isinstance(payload, dict) or not isinstance(payload.get("event"), dict):
        return _failed(
            ticker,
            observed_at,
            "MALFORMED_ENVELOPE",
            "response envelope did not contain a single event object",
            path=path_str,
            http_status=http_status,
            body_sha256=sha_str,
        )
    raw_event = payload["event"]
    if raw_event.get("event_ticker") != ticker:
        return _failed(
            ticker,
            observed_at,
            "EVENT_IDENTITY_MISMATCH",
            "response event ticker did not match the requested ticker",
            path=path_str,
            http_status=http_status,
            body_sha256=sha_str,
        )
    try:
        event = Event.parse(raw_event)
    except UniverseValidationError as exc:
        return _failed(
            ticker,
            observed_at,
            "MALFORMED_ENVELOPE",
            f"event payload failed canonical parse: {exc}",
            path=path_str,
            http_status=http_status,
            body_sha256=sha_str,
        )
    if event.ticker != ticker:
        return _failed(
            ticker,
            observed_at,
            "EVENT_IDENTITY_MISMATCH",
            "parsed event ticker did not match the requested ticker",
            path=path_str,
            http_status=http_status,
            body_sha256=sha_str,
        )

    return AuthoritativeEventSnapshot(
        SCHEMA,
        "kalsh3.market_universe.event_snapshot/1",
        "PRODUCTION",
        HOST,
        expected_path,
        ticker,
        200,
        observed_at,
        observed_at + FRESHNESS,
        sha_str,
        base64.b64encode(body).decode("ascii"),
        event.ticker,
        event.series_ticker,
        event.source_updated_at,
        event.metadata_hash,
        PARSER_VERSION,
        "SUCCESS",
        None,
    )


@dataclass(frozen=True, slots=True)
class EventSnapshotValidation:
    classification: str
    reason: str | None = None
    metadata_hash: str | None = None
    ticker: str | None = None
    series_ticker: str | None = None
    observed_at: datetime | None = None
    body_sha256: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.classification == "PASS"


def validate_event_snapshot(
    payload: object,
    *,
    expected_ticker: str,
) -> EventSnapshotValidation:
    """Independently re-validate a serialized :class:`AuthoritativeEventSnapshot` payload.

    Mirrors :func:`services.market_universe.market_snapshot.validate_market_snapshot` exactly:
    it deliberately does *not* itself compare against ``now`` (see the module docstring's
    freshness discussion) -- what it always independently re-derives and cross-checks is exact
    schema/origin/path/envelope, the raw body hash, and the metadata hash recomputed from a fresh
    :meth:`services.market_universe.domain.Event.parse` of the exact retained bytes -- never the
    merely stamped values in isolation.
    """
    if not isinstance(payload, dict) or set(payload) != _SNAPSHOT_FIELDS:
        return EventSnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "snapshot payload has unexpected or missing fields"
        )
    if payload.get("schema") != SCHEMA:
        return EventSnapshotValidation("MALFORMED_SNAPSHOT_EVIDENCE", "snapshot schema mismatch")
    if payload.get("classification") != "SUCCESS":
        return EventSnapshotValidation(
            "SOURCE_AUTHORITY_MISMATCH", "snapshot acquisition did not succeed"
        )
    if payload.get("environment") != "PRODUCTION":
        return EventSnapshotValidation(
            "SOURCE_AUTHORITY_MISMATCH", "snapshot environment is not PRODUCTION"
        )
    if payload.get("host") != HOST:
        return EventSnapshotValidation(
            "SOURCE_AUTHORITY_MISMATCH", "snapshot host is not the fixed production origin"
        )
    if payload.get("parser_version") != PARSER_VERSION:
        return EventSnapshotValidation(
            "SOURCE_AUTHORITY_MISMATCH", "snapshot parser version does not match this reviewer"
        )
    if payload.get("ticker") != expected_ticker:
        return EventSnapshotValidation(
            "EVENT_IDENTITY_MISMATCH", "snapshot ticker does not match the expected candidate"
        )
    expected_path = f"{BASE}/events/{expected_ticker}"
    if payload.get("path") != expected_path:
        return EventSnapshotValidation(
            "SOURCE_AUTHORITY_MISMATCH", "snapshot path is not the exact expected event path"
        )
    if payload.get("http_status") != 200:
        return EventSnapshotValidation(
            "SOURCE_AUTHORITY_MISMATCH", "snapshot HTTP status was not 200"
        )

    observed_at = _parse_timestamp(payload.get("observed_at"))
    expires_at = _parse_timestamp(payload.get("expires_at"))
    if observed_at is None or expires_at is None:
        return EventSnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "snapshot timestamps are malformed or naive"
        )
    if expires_at <= observed_at or expires_at > observed_at + FRESHNESS:
        return EventSnapshotValidation(
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
        return EventSnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "snapshot is missing raw body material"
        )
    if len(raw_body_b64) > _MAX_BODY_B64_CHARS:
        return EventSnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "snapshot raw body exceeds the bounded size"
        )
    try:
        body = base64.b64decode(raw_body_b64, validate=True)
    except (binascii.Error, ValueError):
        return EventSnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "snapshot raw body is not valid base64"
        )
    if len(body) > MAX_EVENT_BODY_BYTES:
        return EventSnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "snapshot raw body exceeds the bounded size"
        )
    if hashlib.sha256(body).hexdigest() != body_sha256:
        return EventSnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE",
            "snapshot raw body does not match its recorded body_sha256",
        )

    try:
        parsed_body = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return EventSnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "snapshot raw body is not valid JSON"
        )
    if not isinstance(parsed_body, dict) or not isinstance(parsed_body.get("event"), dict):
        return EventSnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE",
            "snapshot raw body envelope does not contain a single event object",
        )
    raw_event = parsed_body["event"]
    if raw_event.get("event_ticker") != expected_ticker:
        return EventSnapshotValidation(
            "EVENT_IDENTITY_MISMATCH",
            "raw body event ticker does not match the expected candidate",
        )
    try:
        event = Event.parse(raw_event)
    except UniverseValidationError:
        return EventSnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "raw body event payload failed canonical parse"
        )
    if event.ticker != expected_ticker:
        return EventSnapshotValidation(
            "EVENT_IDENTITY_MISMATCH",
            "re-parsed event ticker does not match the expected candidate",
        )
    if (
        payload.get("parsed_event_ticker") != event.ticker
        or payload.get("parsed_series_ticker") != event.series_ticker
        or payload.get("metadata_hash") != event.metadata_hash
    ):
        return EventSnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE",
            "stamped snapshot fields do not match an independent re-parse of the raw body",
        )

    return EventSnapshotValidation(
        "PASS",
        None,
        metadata_hash=event.metadata_hash,
        ticker=event.ticker,
        series_ticker=event.series_ticker,
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
