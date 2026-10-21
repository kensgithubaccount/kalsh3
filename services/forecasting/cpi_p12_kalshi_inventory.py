"""Research-only P12 Kalshi CPI inventory capture, isolated from BLS and Reuters.

This collector preserves fixed-origin public exchange responses using the existing
reviewed GET-only transport. It does not collect prices, verify close times, grant
scientific authority, arm schedules, inspect Reuters, or freeze the P12 event.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, cast

from services.historical_replay.cpi_price_evidence import strict_json_loads
from services.market_universe import public_read

RECORD_TYPE = "CPI-P12-KALSHI-INVENTORY-CAPTURE-v1"
CPI_SERIES = "KXCPI"
_EVENT_RE = re.compile(r"^KXCPI-[A-Z0-9][A-Z0-9_.-]{0,100}$")
MAX_RAW_BYTES = public_read.MAX_RESPONSE_BYTES


class P12KalshiCaptureError(ValueError):
    """P12 research inventory transport or structural validation failed closed."""


def _get_and_bind(
    path: str,
    *,
    getter: Callable[[str], dict[str, object]],
) -> tuple[dict[str, object], dict[str, object]]:
    try:
        envelope = getter(path)
    except public_read.PublicReadFailure as exc:
        raise P12KalshiCaptureError("Kalshi public transport failed") from exc
    if type(envelope) is not dict:
        raise P12KalshiCaptureError("Kalshi response envelope is not an object")
    if envelope.get("path") != path:
        raise P12KalshiCaptureError("Kalshi response path changed")
    if type(envelope.get("status")) is not int or envelope["status"] != 200:
        raise P12KalshiCaptureError("Kalshi public response was not HTTP 200")
    if envelope.get("classification") != "SUCCESS":
        raise P12KalshiCaptureError("Kalshi public response was not a success")
    encoded = envelope.get("raw_body_b64")
    if type(encoded) is not str:
        raise P12KalshiCaptureError("Kalshi raw response bytes are absent")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise P12KalshiCaptureError("Kalshi raw response encoding is invalid") from exc
    if not raw or len(raw) > MAX_RAW_BYTES:
        raise P12KalshiCaptureError("Kalshi raw response size is invalid")
    digest = sha256(raw).hexdigest()
    if envelope.get("body_sha256") != digest:
        raise P12KalshiCaptureError("Kalshi response digest mismatch")
    if type(envelope.get("bytes")) is not int or envelope["bytes"] != len(raw):
        raise P12KalshiCaptureError("Kalshi response byte count mismatch")
    stamp = envelope.get("observed_at")
    if type(stamp) is not str:
        raise P12KalshiCaptureError("Kalshi response observation time is missing")
    try:
        observed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise P12KalshiCaptureError("Kalshi observation time is malformed") from exc
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise P12KalshiCaptureError("Kalshi observation time is not timezone aware")
    observed_utc = observed.astimezone(UTC)
    try:
        parsed: object = strict_json_loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise P12KalshiCaptureError("Kalshi raw response is not strict JSON") from exc
    if type(parsed) is not dict:
        raise P12KalshiCaptureError("Kalshi raw JSON is not an object")
    payload = cast(dict[str, object], parsed)
    selected = {
        "source_origin": f"https://{public_read.HOST}",
        "path": path,
        "status": 200,
        "observed_at_utc": observed_utc.isoformat().replace("+00:00", "Z"),
        "raw_body_b64": encoded,
        "raw_body_sha256": digest,
        "byte_count": len(raw),
    }
    return selected, payload


def capture_kalshi_cpi_inventory(
    event_ticker: str,
    *,
    getter: Callable[[str], dict[str, object]] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Capture independent raw Kalshi inventory; no BLS/reuters access or admissions.

    Injected getters support fault-injection tests only. Successful capture is not
    proof that an event satisfies the prospective scientific protocol.
    """
    if type(event_ticker) is not str or _EVENT_RE.fullmatch(event_ticker) is None:
        raise P12KalshiCaptureError("CPI event ticker is malformed")
    transport = getter if getter is not None else public_read.get
    if not callable(transport):
        raise P12KalshiCaptureError("Kalshi transport is not callable")
    paths = (
        f"{public_read.BASE}/series/{CPI_SERIES}",
        f"{public_read.BASE}/events/{event_ticker}",
        f"{public_read.BASE}/markets?event_ticker={event_ticker}&limit=100",
    )
    envelopes: list[dict[str, object]] = []
    bodies: list[dict[str, object]] = []
    for path in paths:
        envelope, body = _get_and_bind(path, getter=transport)
        envelopes.append(envelope)
        bodies.append(body)

    series = bodies[0].get("series")
    event = bodies[1].get("event")
    markets = bodies[2].get("markets")
    if type(series) is not dict or series.get("ticker") != CPI_SERIES:
        raise P12KalshiCaptureError("Kalshi CPI series identity is invalid")
    if type(event) is not dict or event.get("ticker") != event_ticker:
        raise P12KalshiCaptureError("Kalshi event identity is invalid")
    if event.get("series_ticker") != CPI_SERIES:
        raise P12KalshiCaptureError("Kalshi event is outside the CPI series")
    if type(markets) is not list or not markets:
        raise P12KalshiCaptureError("Kalshi event market inventory is absent")
    if bodies[2].get("cursor") not in (None, ""):
        raise P12KalshiCaptureError("Kalshi inventory has an uncollected next page")
    tickers: set[str] = set()
    for market in markets:
        if type(market) is not dict or market.get("event_ticker") != event_ticker:
            raise P12KalshiCaptureError("Kalshi market is outside the target event")
        ticker = market.get("ticker")
        if (
            type(ticker) is not str
            or not ticker.startswith(event_ticker + "-")
            or ticker in tickers
        ):
            raise P12KalshiCaptureError("Kalshi market ticker is malformed or duplicated")
        tickers.add(ticker)

    completed = (clock or (lambda: datetime.now(UTC)))()
    if type(completed) is not datetime or completed.tzinfo is None:
        raise P12KalshiCaptureError("completion clock must supply aware UTC time")
    if completed.utcoffset() is None:
        raise P12KalshiCaptureError("completion clock is naive")
    return {
        "record_type": RECORD_TYPE,
        "stage": "KALSHI_CPI_INVENTORY_RAW",
        "requested_event_ticker": event_ticker,
        "requested_series_ticker": CPI_SERIES,
        "source_origin": f"https://{public_read.HOST}",
        "completed_at_utc": completed.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "acquisition_mode": (
            "PUBLIC_GET" if getter is None else "INJECTED_DIAGNOSTIC_GETTER"
        ),
        "observations": envelopes,
        "market_row_count": len(markets),
        "scope": "RAW_INVENTORY_ONLY_NO_MARKET_QUOTES_OR_REUTERS",
        "scientific_evidence_authorized": False,
        "phase1_economic_test_authorized": False,
        "promotion_authority": "NONE",
        "production_influence": "0",
        "research_only": True,
        "p12_frozen": False,
    }
