"""Shared bounded, unauthenticated, fixed-origin public GET transport for the Kalshi exchange.

This is the SINGLE reviewed transport implementation for every public (no credential) read this
repository performs against the exchange. PUBLIC GET only: no credentials, no Authorization
header, fixed production host, TLS, no redirects, bounded timeout, bounded response size.

Every response evidence envelope now also retains the exact bounded raw body as base64. This lets
later consumers independently recompute ``body_sha256`` and re-parse the exact bytes instead of
trusting a stamped hash or caller-reconstructed JSON object.
"""

from __future__ import annotations

import base64
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

_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.-]{0,127}$")


class PublicReadFailure(RuntimeError):
    pass


def _get_raw(path: str) -> tuple[bytes, int, datetime]:
    """Bounded, GET-only, TLS, no-redirect fetch of ``path`` on the fixed production host."""
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
        "raw_body_b64": base64.b64encode(body).decode("ascii"),
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
    """Bounded GET of the exact single-market endpoint; returns evidence and exact raw bytes."""
    if not _TICKER_RE.fullmatch(ticker):
        raise PublicReadFailure("ticker is not a well-formed exact market ticker")
    path = f"{BASE}/markets/{ticker}"
    body, status, observed_at = _get_raw(path)
    return _evidence_from_body(path, body, status, observed_at), body


def get_market(ticker: str) -> dict[str, object]:
    evidence, _body = get_market_with_body(ticker)
    return evidence


def get_event_with_body(event_ticker: str) -> tuple[dict[str, object], bytes]:
    """Bounded GET of the exact single-event endpoint; returns evidence and exact raw bytes."""
    if not _TICKER_RE.fullmatch(event_ticker):
        raise PublicReadFailure("ticker is not a well-formed exact event ticker")
    path = f"{BASE}/events/{event_ticker}"
    body, status, observed_at = _get_raw(path)
    return _evidence_from_body(path, body, status, observed_at), body


def get_orderbook_with_body(ticker: str) -> tuple[dict[str, object], bytes]:
    """Bounded GET of the orderbook endpoint for exactly one ticker."""
    if not _TICKER_RE.fullmatch(ticker):
        raise PublicReadFailure("ticker is not a well-formed exact market ticker")
    path = f"{BASE}/markets/orderbooks?" + urlencode({"tickers": ticker})
    body, status, observed_at = _get_raw(path)
    return _evidence_from_body(path, body, status, observed_at), body
