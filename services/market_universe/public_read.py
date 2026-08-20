"""Shared bounded, unauthenticated, fixed-origin public GET transport for the Kalshi exchange.

This is the SINGLE reviewed transport implementation for every public (no credential) read this
repository performs against the exchange -- the M27E exchange-status/series/markets acceptance
script, M27J's authoritative market-snapshot acquisition, and the orderbook snapshot layer
(:mod:`services.market_universe.orderbook_snapshot`) all depend on this module, never on each
other or on a second transport configuration.

Dependency direction: ``scripts/`` may import this ``services`` module; this module (and no other
``services`` module) must never import anything from ``scripts/``.

PUBLIC GET only: no credentials, no Authorization header, fixed production host, TLS, no
redirects (``http.client`` never follows them on its own -- a 3xx response simply surfaces as its
own HTTP status), bounded timeout, bounded response size. ``get``/``get_market``/
``get_market_with_body`` are one path segment for a market ticker only (no query strings, no path
traversal). ``get_orderbook_with_body`` is the one deliberate exception: it issues exactly one
fixed query parameter (``tickers=<exact ticker>``) against the batch orderbook endpoint for
exactly one ticker -- never a caller-supplied or multi-ticker query, never a second HTTP
implementation, and every other guarantee (fixed host, GET-only, no redirects, bounded size)
still applies identically.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import re
import ssl
from datetime import UTC, datetime
from urllib.parse import urlencode

HOST = "external-api.kalshi.com"
BASE = "/trade-api/v2"
MAX_RESPONSE_BYTES = 8_000_000

# Exact-market tickers only: no path traversal, no query string, no slashes, no control
# characters (the character class below admits none).
_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.-]{0,127}$")


class PublicReadFailure(RuntimeError):
    pass


def _get_raw(path: str) -> tuple[bytes, int, datetime]:
    """Bounded, GET-only, TLS, no-redirect fetch of ``path`` on the fixed production host.

    The sole transport primitive: every public GET in this repository -- M27E's exchange
    status/series/markets pagination and M27J's exact-market snapshot acquisition -- goes
    through this one function.
    """
    if not path.startswith(BASE + "/") or ".." in path.split("/") or "//" in path:
        raise PublicReadFailure("path is outside fixed public read authority")
    if "\r" in path or "\n" in path:
        raise PublicReadFailure("path contains a control character")
    connection = http.client.HTTPSConnection(HOST, timeout=10, context=ssl.create_default_context())
    observed_at = datetime.now(UTC)
    try:
        connection.request("GET", path, headers={"Accept": "application/json"})
        response = connection.getresponse()
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise PublicReadFailure("response exceeded bounded size")
        return body, response.status, observed_at
    except (OSError, TimeoutError, http.client.HTTPException) as exc:
        raise PublicReadFailure(f"HTTP/network failure: {exc}") from exc
    finally:
        connection.close()


def _evidence_from_body(
    path: str, body: bytes, status: int, observed_at: datetime
) -> dict[str, object]:
    evidence: dict[str, object] = {
        "path": path,
        "observed_at": observed_at.isoformat(),
        "status": status,
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "bytes": len(body),
    }
    if status != 200:
        evidence["classification"] = "HTTP_OR_NETWORK_FAILURE"
        evidence["body_preview"] = body[:200].decode("utf-8", errors="replace")
        return evidence
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublicReadFailure(f"schema failure: invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise PublicReadFailure("schema failure: response is not an object")
    evidence["classification"] = "SUCCESS"
    evidence["payload"] = payload
    return evidence


def get(path: str) -> dict[str, object]:
    body, status, observed_at = _get_raw(path)
    return _evidence_from_body(path, body, status, observed_at)


def get_market_with_body(ticker: str) -> tuple[dict[str, object], bytes]:
    """Bounded GET of the exact single-market endpoint; returns evidence and the exact raw bytes.

    Exact-market read, GET-only, reusing :func:`_get_raw` -- never a second transport
    configuration. The raw bytes are returned (in addition to the same evidence shape ``get()``
    produces) only so a caller can cryptographically bind its own downstream parsing to the exact
    bytes received; this function itself never parses market rules.
    """
    if not _TICKER_RE.fullmatch(ticker):
        raise PublicReadFailure("ticker is not a well-formed exact market ticker")
    path = f"{BASE}/markets/{ticker}"
    body, status, observed_at = _get_raw(path)
    return _evidence_from_body(path, body, status, observed_at), body


def get_market(ticker: str) -> dict[str, object]:
    evidence, _body = get_market_with_body(ticker)
    return evidence


def get_orderbook_with_body(ticker: str) -> tuple[dict[str, object], bytes]:
    """Bounded GET of the batch orderbook endpoint for exactly one ticker; returns evidence and
    the exact raw bytes, mirroring :func:`get_market_with_body`'s exact pattern.

    The wire endpoint (``/markets/orderbooks``) is batch-shaped, but this function only ever
    requests exactly one ticker via one fixed query parameter it constructs itself -- no
    caller-supplied query, no alternate host, no second HTTP implementation. Reuses
    ``_TICKER_RE``/``_get_raw``/``_evidence_from_body`` verbatim, exactly like
    :func:`get_market_with_body` does; this function itself never parses orderbook levels.
    """
    if not _TICKER_RE.fullmatch(ticker):
        raise PublicReadFailure("ticker is not a well-formed exact market ticker")
    path = f"{BASE}/markets/orderbooks?" + urlencode({"tickers": ticker})
    body, status, observed_at = _get_raw(path)
    return _evidence_from_body(path, body, status, observed_at), body
