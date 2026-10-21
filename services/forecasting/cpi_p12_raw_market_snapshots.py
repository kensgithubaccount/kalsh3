"""Research-only, BLS-independent P12 Kalshi single-market raw snapshot capture.

Uses the already-reviewed unauthenticated fixed-origin public GET transport. Captures
exact API response bytes, not a forecast or executable price determination. The caller's
window, event and ticker list have *no* scientific authority until a separate P12
protocol freezes the complete market cohort and its cutoffs.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from services.forecasting.cpi_p12_kalshi_inventory import (
    P12KalshiCaptureError,
    _get_and_bind,
)
from services.market_universe import public_read

RECORD_TYPE = "CPI-P12-RAW-KALSHI-MARKET-SNAPSHOTS-v1"
_EVENT_RE = re.compile(r"^KXCPI-[A-Z0-9][A-Z0-9_.-]{0,100}$")
_MARKET_RE = re.compile(r"^KXCPI-[A-Z0-9][A-Z0-9_.-]{0,125}$")
MAX_MARKETS_PER_CAPTURE = 32


class P12SnapshotCaptureError(ValueError):
    """An independent Kalshi snapshot or time-bound check failed closed."""


def _utc(value: datetime, field: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise P12SnapshotCaptureError(f"{field} must be an aware datetime")
    return value.astimezone(UTC)


def capture_kalshi_raw_market_snapshots(
    *,
    event_ticker: str,
    market_tickers: tuple[str, ...],
    window_start: datetime,
    deadline: datetime,
    getter: Callable[[str], dict[str, object]] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Capture bounded exact market responses, with time checks before/after each GET.

    Market identity, market count and source URL choices are caller inputs pending
    prospective protocol freeze. SUCCESS here means raw transport observations only.
    """
    if type(event_ticker) is not str or _EVENT_RE.fullmatch(event_ticker) is None:
        raise P12SnapshotCaptureError("CPI event ticker is malformed")
    if type(market_tickers) is not tuple or not (\n        1 <= len(market_tickers) <= MAX_MARKETS_PER_CAPTURE\n    ):
        raise P12SnapshotCaptureError("market ticker cohort size is invalid")
    if any(
        type(ticker) is not str
        or _MARKET_RE.fullmatch(ticker) is None
        or not ticker.startswith(event_ticker + "-")
        for ticker in market_tickers
    ):
        raise P12SnapshotCaptureError("market ticker is outside requested event")
    if len(set(market_tickers)) != len(market_tickers):
        raise P12SnapshotCaptureError("market ticker cohort contains duplicates")

    start = _utc(window_start, "window_start")
    cutoff = _utc(deadline, "deadline")
    if start >= cutoff:
        raise P12SnapshotCaptureError("capture window is empty")
    now = clock or (lambda: datetime.now(UTC))
    opened_at = _utc(now(), "capture start")
    if not start <= opened_at < cutoff:
        raise P12SnapshotCaptureError("capture started outside the requested window")
    transport = public_read.get if getter is None else getter
    if not callable(transport):
        raise P12SnapshotCaptureError("Kalshi getter is not callable")

    captured: list[dict[str, object]] = []
    for ticker in market_tickers:
        if not start <= _utc(now(), "before request") < cutoff:
            raise P12SnapshotCaptureError("capture window expired before a request")
        path = f"{public_read.BASE}/markets/{ticker}"
        try:
            envelope, payload = _get_and_bind(path, getter=transport)
        except P12KalshiCaptureError as exc:
            raise P12SnapshotCaptureError(\n                "Kalshi market snapshot transport or body rejected"\n            ) from exc
        observed_text = envelope["observed_at_utc"]
        if type(observed_text) is not str:
            raise P12SnapshotCaptureError("Kalshi observation timestamp missing")
        observed = _utc(datetime.fromisoformat(observed_text.replace("Z", "+00:00")), "observed_at")
        finished_request = _utc(now(), "after request")
        if not start <= observed <= finished_request < cutoff:
            raise P12SnapshotCaptureError("Kalshi response is not within prospective window")
        market = payload.get("market")
        if type(market) is not dict or market.get("ticker") != ticker:
            raise P12SnapshotCaptureError("Kalshi snapshot market identity mismatch")
        if market.get("event_ticker") != event_ticker:
            raise P12SnapshotCaptureError("Kalshi snapshot event identity mismatch")
        captured.append({**envelope, "market_ticker": ticker})

    finished = _utc(now(), "capture completion")
    if finished >= cutoff or finished < opened_at:
        raise P12SnapshotCaptureError("capture finished outside requested window")
    return {
        "record_type": RECORD_TYPE,
        "stage": "KALSHI_MARKET_CAPTURE",
        "event_ticker": event_ticker,
        "market_tickers": list(market_tickers),
        "market_count": len(captured),
        "observations": captured,
        "window_start_utc": start.isoformat().replace("+00:00", "Z"),
        "deadline_utc": cutoff.isoformat().replace("+00:00", "Z"),
        "capture_started_at_utc": opened_at.isoformat().replace("+00:00", "Z"),
        "capture_completed_at_utc": finished.isoformat().replace("+00:00", "Z"),
        "acquisition_mode": "PUBLIC_GET" if getter is None else "INJECTED_TEST_GETTER",
        "scope": "RAW_MARKET_RESPONSES_ONLY_NO_PRICE_SELECTION_OR_ADMISSIBILITY",
        "cohort_authority": "CALLER_PROVIDED_NOT_FROZEN",
        "scientific_evidence_authorized": False,
        "phase1_economic_test_authorized": False,
        "promotion_authority": "NONE",
        "production_influence": "0",
        "p12_frozen": False,
    }
