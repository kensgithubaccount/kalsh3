"""Authoritative exact-orderbook snapshot: the acquisition/envelope-provenance boundary for
executable YES/NO orderbook evidence.

Mirrors :mod:`services.market_universe.market_snapshot`'s exact shape and trust model, but for
orderbook data rather than market/rules data: an :class:`AuthoritativeOrderbookSnapshot` is
produced by a single bounded, unauthenticated PUBLIC GET of the batch orderbook endpoint for
exactly one requested ticker, with the exact response bytes retained and hash-bound, and
structural re-validation rerun at every independent validation -- never trusted merely because a
field says so.

Scope boundary (be precise about what this module does NOT do): it does not, and cannot,
produce the final ladder-bound canonical book
(:class:`services.opportunity_engine.live_economics.BookObservation`) that
:func:`services.opportunity_engine.live_economics.normalize_live_orderbook` builds -- that
requires a :class:`services.market_universe.pricing.PriceLadder` and a ``market_rules_hash``,
both market-rules-specific context this pure acquisition/envelope layer does not have and must
not fabricate. What this module DOES independently guarantee, without that context: exact
ticker/path/origin binding, raw body integrity (hash-bound), structurally valid JSON shape (both
sides present, well-formed 2-element levels, numeric price/size via the same
:func:`services.market_universe.domain.exact` coercion the real economics builder uses, no
duplicate price levels per side, no ambiguous multi-entry response), and freshness. A caller
passes the validated, re-derived ``raw_orderbook`` dict this module hands back straight into the
EXISTING, UNMODIFIED :func:`services.opportunity_engine.live_economics.normalize_live_orderbook`
/ :func:`services.opportunity_engine.authoritative_economics.build_authoritative_market_economics`
-- exactly where a test's hand-built ``book_raw`` dict goes today. No change to either of those
files is required; this module is strictly a new, better-provenanced SOURCE for their existing
``raw_orderbook`` parameter.

Trust model: identical to ``market_snapshot``'s -- SHA-256 proves the integrity of the *recorded*
bytes, not their server origin by itself; origin authority comes only from the reviewed
acquisition boundary (:mod:`services.market_universe.public_read`) having actually made the
request.

Transport: this module reuses ONLY :mod:`services.market_universe.public_read`'s primitives for
the envelope constants, failure type, and the real production transport -- it never constructs a
second generic HTTP stack. :func:`acquire_orderbook_snapshot`'s default ``transport`` is
:func:`services.market_universe.public_read.get_orderbook_with_body`, the one additive helper
that module exposes for exactly this purpose (reuses its ``_TICKER_RE``/``_get_raw``/
``_evidence_from_body`` verbatim; no second HTTP implementation, no alternate host, no
caller-supplied query beyond the one fixed ``tickers=<exact ticker>`` parameter it builds
itself). The fake-transport seam remains fully available: callers (and every test in this
package) may still pass an explicit ``transport`` to override the default deterministically,
with zero live network I/O.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from services.market_universe.domain import UniverseValidationError, exact
from services.market_universe.market_snapshot import FRESHNESS
from services.market_universe.public_read import BASE, PublicReadFailure, get_orderbook_with_body
from services.market_universe.public_read import HOST as _RAW_HOST

SCHEMA = "kalsh3.market_universe.authoritative-orderbook-snapshot.v1"
PARSER_VERSION = "kalsh3.market_universe.orderbook_snapshot.parse/1"
HOST = "https://" + _RAW_HOST

# A single-ticker orderbook response is always small; mirrors market_snapshot's own bound for a
# single market object, a defense-in-depth limit distinct from M27E's general pagination cap.
MAX_ORDERBOOK_BODY_BYTES = 200_000
_MAX_BODY_B64_CHARS = 4 * ((MAX_ORDERBOOK_BODY_BYTES // 3) + 1) * 2

_SIDES: tuple[str, ...] = ("yes_dollars", "no_dollars")
_Level = tuple[str, str]


class OrderbookAcquisitionError(RuntimeError):
    """A structurally-relevant claim in orderbook evidence failed independent validation."""


def _canonical_levels(rows: object, *, field: str) -> tuple[_Level, ...]:
    """Structurally validate and canonicalize one side's levels.

    Every claim here is independently re-derivable from the raw bytes alone -- this never trusts
    a caller-stamped level list. Uses the exact same
    :func:`services.market_universe.domain.exact` numeric coercion
    :func:`services.opportunity_engine.live_economics.normalize_live_orderbook` itself uses, so
    a value this function accepts is never a value the real economics builder would reject on
    numeric-shape grounds alone (ladder-membership is, correctly, still that builder's job).
    """
    if not isinstance(rows, list):
        raise OrderbookAcquisitionError(f"{field} side is missing")
    seen: set[Decimal] = set()
    canonical: list[_Level] = []
    for row in rows:
        if not isinstance(row, list) or len(row) != 2:
            raise OrderbookAcquisitionError(f"{field} level is malformed")
        try:
            price = exact(row[0], f"{field} price")
            quantity = exact(row[1], f"{field} quantity")
        except UniverseValidationError as exc:
            raise OrderbookAcquisitionError(
                f"{field} level has an invalid price/size: {exc}"
            ) from exc
        if not (Decimal(0) < price < Decimal(1)):
            raise OrderbookAcquisitionError(f"{field} price is outside the open (0,1) domain")
        if quantity < 0:
            raise OrderbookAcquisitionError(f"{field} quantity is negative")
        if price in seen:
            raise OrderbookAcquisitionError(f"{field} has a duplicate/ambiguous price level")
        seen.add(price)
        canonical.append((str(price), str(quantity)))
    canonical.sort(key=lambda level: Decimal(level[0]))
    return tuple(canonical)


def _orderbook_identity(
    ticker: str, yes_levels: tuple[_Level, ...], no_levels: tuple[_Level, ...]
) -> str:
    material = {"ticker": ticker, "yes_dollars": list(yes_levels), "no_dollars": list(no_levels)}
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _canonical_raw_body(
    body: bytes, *, expected_ticker: str
) -> tuple[tuple[_Level, ...], tuple[_Level, ...], str]:
    """Re-parse raw bytes end-to-end: JSON shape, single unambiguous entry, ticker binding,
    structurally valid levels. Raises :class:`OrderbookAcquisitionError` on any violation."""
    try:
        parsed = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OrderbookAcquisitionError(f"raw body is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("orderbooks"), list):
        raise OrderbookAcquisitionError("response envelope does not contain an orderbooks list")
    entries = parsed["orderbooks"]
    if len(entries) != 1:
        raise OrderbookAcquisitionError(
            f"response is ambiguous: expected exactly one orderbook entry, got {len(entries)}"
        )
    entry = entries[0]
    if not isinstance(entry, dict) or entry.get("ticker") != expected_ticker:
        raise OrderbookAcquisitionError(
            "orderbook entry ticker does not match the expected candidate"
        )
    book = entry.get("orderbook_fp")
    if not isinstance(book, dict):
        raise OrderbookAcquisitionError("orderbook entry is missing orderbook_fp")
    yes_levels = _canonical_levels(book.get("yes_dollars"), field="yes_dollars")
    no_levels = _canonical_levels(book.get("no_dollars"), field="no_dollars")
    return yes_levels, no_levels, entry["ticker"]


@dataclass(frozen=True, slots=True)
class AuthoritativeOrderbookSnapshot:
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
    parsed_ticker: str | None
    yes_levels: tuple[_Level, ...]
    no_levels: tuple[_Level, ...]
    orderbook_identity: str | None
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
            "parsed_ticker": self.parsed_ticker,
            "yes_levels": [list(level) for level in self.yes_levels],
            "no_levels": [list(level) for level in self.no_levels],
            "orderbook_identity": self.orderbook_identity,
            "parser_version": self.parser_version,
            "classification": self.classification,
            "reason": self.reason,
        }

    @property
    def succeeded(self) -> bool:
        return self.classification == "SUCCESS"

    def raw_orderbook_for_economics(self) -> dict[str, Any]:
        """The exact ``raw_orderbook`` shape
        :func:`services.opportunity_engine.live_economics.normalize_live_orderbook` /
        :func:`services.opportunity_engine.authoritative_economics.build_authoritative_market_economics`
        already accept, unmodified -- built from THIS object's independently-validated levels,
        never from unvalidated caller input. Only call this on a ``succeeded`` snapshot.
        """
        if not self.succeeded:
            raise OrderbookAcquisitionError("cannot expose book material from a failed snapshot")
        return {
            "ticker": self.ticker,
            "orderbook_fp": {
                "yes_dollars": [list(level) for level in self.yes_levels],
                "no_dollars": [list(level) for level in self.no_levels],
            },
        }


_SNAPSHOT_FIELDS = frozenset(AuthoritativeOrderbookSnapshot.__dataclass_fields__)


def _expected_path(ticker: str) -> str:
    return f"{BASE}/markets/orderbooks?" + urlencode({"tickers": ticker})


def _failed(
    ticker: str,
    observed_at: datetime,
    classification: str,
    reason: str,
    *,
    path: str | None = None,
    http_status: int | None = None,
    body_sha256: str | None = None,
) -> AuthoritativeOrderbookSnapshot:
    return AuthoritativeOrderbookSnapshot(
        SCHEMA,
        "kalsh3.market_universe.orderbook_snapshot/1",
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
        (),
        (),
        None,
        PARSER_VERSION,
        classification,
        reason,
    )


def acquire_orderbook_snapshot(
    ticker: str,
    *,
    transport: Callable[[str], tuple[dict[str, object], bytes]] = get_orderbook_with_body,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> AuthoritativeOrderbookSnapshot:
    """Single bounded PUBLIC GET of the batch orderbook endpoint for exactly one ``ticker``.

    ``transport`` defaults to :func:`services.market_universe.public_read.get_orderbook_with_body`
    -- the real production transport, doing a live network call when actually invoked with no
    override. Every test in this package passes an explicit fake ``transport`` instead, so no
    test in this repository ever performs live network I/O through this function.

    Never trusts a caller-supplied price/size/"executable" claim: every level is independently
    re-derived from the exact received raw bytes via :func:`_canonical_raw_body`, which itself
    delegates numeric coercion to :func:`services.market_universe.domain.exact` -- the same
    function the real economics builder uses. The raw response bytes are retained (bounded,
    base64) so :func:`validate_orderbook_snapshot` can independently re-derive both the body hash
    and every level from a deserialized artifact, rather than trusting any stamped value.
    """
    started = clock()
    try:
        evidence, body = transport(ticker)
    except PublicReadFailure as exc:
        return _failed(ticker, started, "ACQUISITION_FAILURE", str(exc))

    observed_at = _parse_timestamp(evidence.get("observed_at")) or started
    expected_path = _expected_path(ticker)
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
    if len(body) > MAX_ORDERBOOK_BODY_BYTES:
        return _failed(
            ticker,
            observed_at,
            "OVERSIZED_BODY",
            "orderbook response exceeded the bounded single-ticker size",
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
    try:
        yes_levels, no_levels, parsed_ticker = _canonical_raw_body(body, expected_ticker=ticker)
    except OrderbookAcquisitionError as exc:
        return _failed(
            ticker,
            observed_at,
            "MALFORMED_ENVELOPE",
            str(exc),
            path=path_str,
            http_status=http_status,
            body_sha256=sha_str,
        )

    return AuthoritativeOrderbookSnapshot(
        SCHEMA,
        "kalsh3.market_universe.orderbook_snapshot/1",
        "PRODUCTION",
        HOST,
        expected_path,
        ticker,
        200,
        observed_at,
        observed_at + FRESHNESS,
        sha_str,
        base64.b64encode(body).decode("ascii"),
        parsed_ticker,
        yes_levels,
        no_levels,
        _orderbook_identity(ticker, yes_levels, no_levels),
        PARSER_VERSION,
        "SUCCESS",
        None,
    )


@dataclass(frozen=True, slots=True)
class OrderbookSnapshotValidation:
    classification: str
    reason: str | None = None
    ticker: str | None = None
    observed_at: datetime | None = None
    body_sha256: str | None = None
    yes_levels: tuple[_Level, ...] = ()
    no_levels: tuple[_Level, ...] = ()
    orderbook_identity: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.classification == "PASS"

    def raw_orderbook_for_economics(self) -> dict[str, Any]:
        if not self.succeeded or self.ticker is None:
            raise OrderbookAcquisitionError("cannot expose book material from a failed validation")
        return {
            "ticker": self.ticker,
            "orderbook_fp": {
                "yes_dollars": [list(level) for level in self.yes_levels],
                "no_dollars": [list(level) for level in self.no_levels],
            },
        }


def validate_orderbook_snapshot(
    payload: object, *, expected_ticker: str, now: datetime
) -> OrderbookSnapshotValidation:
    """Independently re-validate a serialized :class:`AuthoritativeOrderbookSnapshot` payload.

    Unlike ``market_snapshot.validate_market_snapshot`` (which defers ``now``-relative freshness
    to its caller), this function takes ``now`` directly and rejects future-timestamped or stale
    evidence itself -- the calling instruction for this module requires both to be rejected here,
    not deferred.

    Never trusts any stamped field in isolation: re-derives the body hash, re-parses the raw JSON,
    re-derives every price/size level via the same structural rules
    :func:`acquire_orderbook_snapshot` used, recomputes ``orderbook_identity``, and rejects if any
    of those independently-recomputed values disagree with what the payload claims.
    """
    if not isinstance(payload, dict) or set(payload) != _SNAPSHOT_FIELDS:
        return OrderbookSnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE",
            "orderbook snapshot payload has unexpected or missing fields",
        )
    if payload.get("schema") != SCHEMA:
        return OrderbookSnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "orderbook snapshot schema mismatch"
        )
    if payload.get("classification") != "SUCCESS":
        return OrderbookSnapshotValidation(
            "SOURCE_AUTHORITY_MISMATCH", "orderbook snapshot acquisition did not succeed"
        )
    if payload.get("environment") != "PRODUCTION":
        return OrderbookSnapshotValidation(
            "SOURCE_AUTHORITY_MISMATCH", "orderbook snapshot environment is not PRODUCTION"
        )
    if payload.get("host") != HOST:
        return OrderbookSnapshotValidation(
            "SOURCE_AUTHORITY_MISMATCH",
            "orderbook snapshot host is not the fixed production origin",
        )
    if payload.get("parser_version") != PARSER_VERSION:
        return OrderbookSnapshotValidation(
            "SOURCE_AUTHORITY_MISMATCH",
            "orderbook snapshot parser version does not match this reviewer",
        )
    if payload.get("ticker") != expected_ticker:
        return OrderbookSnapshotValidation(
            "MARKET_IDENTITY_MISMATCH",
            "orderbook snapshot ticker does not match the expected candidate",
        )
    expected_path = _expected_path(expected_ticker)
    if payload.get("path") != expected_path:
        return OrderbookSnapshotValidation(
            "SOURCE_AUTHORITY_MISMATCH",
            "orderbook snapshot path is not the exact expected orderbook path",
        )
    if payload.get("http_status") != 200:
        return OrderbookSnapshotValidation(
            "SOURCE_AUTHORITY_MISMATCH", "orderbook snapshot HTTP status was not 200"
        )

    observed_at = _parse_timestamp(payload.get("observed_at"))
    expires_at = _parse_timestamp(payload.get("expires_at"))
    if observed_at is None or expires_at is None:
        return OrderbookSnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "orderbook snapshot timestamps are malformed or naive"
        )
    if expires_at <= observed_at or expires_at > observed_at + FRESHNESS:
        return OrderbookSnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE",
            "orderbook snapshot expiry does not satisfy the fixed freshness bound",
        )
    if observed_at > now:
        return OrderbookSnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "orderbook snapshot observed_at is in the future"
        )
    if not (observed_at <= now <= expires_at):
        return OrderbookSnapshotValidation(
            "ORDERBOOK_EVIDENCE_STALE", "orderbook snapshot is not fresh at consumption time"
        )

    body_sha256 = payload.get("body_sha256")
    raw_body_b64 = payload.get("raw_body_b64")
    if (
        not isinstance(body_sha256, str)
        or not body_sha256
        or not isinstance(raw_body_b64, str)
        or not raw_body_b64
    ):
        return OrderbookSnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "orderbook snapshot is missing raw body material"
        )
    if len(raw_body_b64) > _MAX_BODY_B64_CHARS:
        return OrderbookSnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "orderbook snapshot raw body exceeds the bounded size"
        )
    try:
        body = base64.b64decode(raw_body_b64, validate=True)
    except (binascii.Error, ValueError):
        return OrderbookSnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "orderbook snapshot raw body is not valid base64"
        )
    if len(body) > MAX_ORDERBOOK_BODY_BYTES:
        return OrderbookSnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE", "orderbook snapshot raw body exceeds the bounded size"
        )
    if hashlib.sha256(body).hexdigest() != body_sha256:
        return OrderbookSnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE",
            "orderbook snapshot raw body does not match its recorded body_sha256",
        )

    try:
        yes_levels, no_levels, parsed_ticker = _canonical_raw_body(
            body, expected_ticker=expected_ticker
        )
    except OrderbookAcquisitionError as exc:
        return OrderbookSnapshotValidation("MALFORMED_SNAPSHOT_EVIDENCE", str(exc))

    identity = _orderbook_identity(expected_ticker, yes_levels, no_levels)
    if (
        payload.get("parsed_ticker") != parsed_ticker
        or payload.get("orderbook_identity") != identity
        or [list(level) for level in yes_levels] != payload.get("yes_levels")
        or [list(level) for level in no_levels] != payload.get("no_levels")
    ):
        return OrderbookSnapshotValidation(
            "MALFORMED_SNAPSHOT_EVIDENCE",
            "stamped orderbook fields do not match an independent re-parse of the raw body",
        )

    return OrderbookSnapshotValidation(
        "PASS",
        None,
        ticker=expected_ticker,
        observed_at=observed_at,
        body_sha256=body_sha256,
        yes_levels=yes_levels,
        no_levels=no_levels,
        orderbook_identity=identity,
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


def read_orderbook_snapshot_evidence(path: Path) -> dict[str, Any]:
    """Read a file-backed serialized orderbook snapshot, rejecting symlink/path indirection.

    Refuses a symlinked path outright, and refuses if the resolved real path differs from the
    path as given (any indirection through a symlinked parent directory) -- serialized evidence
    must be exactly the file the caller named, never something substituted underneath it.
    """
    if path.is_symlink():
        raise OrderbookAcquisitionError("evidence path is a symlink, refusing to read")
    resolved = path.resolve(strict=True)
    if resolved != path.absolute():
        raise OrderbookAcquisitionError(
            "evidence path resolves through indirection, refusing to read"
        )
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise OrderbookAcquisitionError("evidence file does not contain a JSON object")
    return payload
