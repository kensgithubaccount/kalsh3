"""Bounded research-only Kalshi GET bundle, never a source/model/economics issuer.

Only an explicit call may make 14 fixed-origin public GET requests: event,
series, six individually bound markets and six one-ticker orderbooks. Response
bytes are retained in the transport envelopes and cross-checked by the
existing P3 strict raw-body replay. A caller can inject fake envelopes in
tests; no return value grants Kalshi custody, fee/depth, paper or trade rights.

The Kalshi endpoint may return 401/403 or change shape: fail closed without
retry, without fabricated missing siblings, and without order authority.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime

from services.market_universe import public_read

from .wn_p3_sibling_preflight import CITY_SERIES, event_ticker_for, validate_untrusted_roster
from .wn_p3_untrusted_envelope_replay import (
    UntrustedReplay,
    replay_untrusted_six_sibling_bundle,
    validate_strict_untrusted_response,
)

MAX_CALLS = 14
TIMEOUT_SECONDS = 3.0
GET = Callable[[str], dict[str, object]]


class CaptureError(ValueError):
    """One missing, inaccessible, invalid or late source element abstains."""


@dataclass(frozen=True, slots=True)
class ReadOnlyUntrustedBundle:
    """Raw byte envelopes retained, but not independently attested source custody."""

    series: str
    target: date
    event: dict[str, object]
    series_response: dict[str, object]
    markets: tuple[tuple[str, dict[str, object]], ...]
    books: tuple[tuple[str, dict[str, object]], ...]
    replay: UntrustedReplay

    @property
    def source_attested(self) -> bool:
        return False

    @property
    def rules_and_station_authorized(self) -> bool:
        return False

    @property
    def fee_depth_authorized(self) -> bool:
        return False

    @property
    def model_probability_authorized(self) -> bool:
        return False

    @property
    def paper_authorized(self) -> bool:
        return False

    @property
    def trading_authority(self) -> str:
        return "NONE"


def capture_untrusted_six_sibling_bundle(
    *,
    series: str,
    target: date,
    evaluated_at: datetime,
    get: GET | None = None,
) -> ReadOnlyUntrustedBundle:
    """One-shot bounded read-only research capture; no writes or live use on import.

    The request override is expressly a synthetic/untrusted test seam. No
    positive source attestation is possible even with default fixed-host GET.
    """
    if series not in CITY_SERIES:
        raise CaptureError("city outside five-city roster")
    event_ticker = event_ticker_for(series, target)
    if (
        type(evaluated_at) is not datetime
        or evaluated_at.tzinfo is None
        or evaluated_at.utcoffset() is None
    ):
        raise CaptureError("evaluation timestamp must be timezone-aware")
    try:
        decision = evaluated_at.astimezone(UTC)
    except OverflowError as exc:
        raise CaptureError("evaluation timestamp outside UTC range") from exc

    def request(path: str) -> dict[str, object]:
        # One attempt only; never refetch on rejected/raw/stale data.
        if get is not None:
            return get(path)
        return public_read.get_with_timeout(path, timeout_seconds=TIMEOUT_SECONDS)

    try:
        event_path = public_read.BASE + "/events/" + event_ticker + "?with_nested_markets=true"
        event = request(event_path)
        payload, _ = validate_strict_untrusted_response(event, expected_path=event_path)
        event_value = payload.get("event")
        if not isinstance(event_value, dict):
            raise CaptureError("event response lacks event object")
        nested = event_value.get("markets")
        if not isinstance(nested, list):
            raise CaptureError("documented event.markets array required")
        roster = validate_untrusted_roster(
            series_ticker=series,
            target_local_date=target,
            event_payload={"event": event_value, "markets": nested},
        )
        series_response = request(public_read.BASE + "/series/" + series)
        market_rows: list[tuple[str, dict[str, object]]] = []
        book_rows: list[tuple[str, dict[str, object]]] = []
        for ticker in roster.market_tickers:
            market_rows.append((ticker, request(public_read.BASE + "/markets/" + ticker)))
        for ticker in roster.market_tickers:
            book_rows.append(
                (ticker, request(public_read.BASE + "/markets/orderbooks?tickers=" + ticker))
            )
        if 2 + len(market_rows) + len(book_rows) != MAX_CALLS:
            raise CaptureError("incomplete one-shot public response family")
        replay = replay_untrusted_six_sibling_bundle(
            series_ticker=series,
            target_local_date=target,
            evaluated_at=decision,
            event_response=event,
            series_response=series_response,
            market_responses=dict(market_rows),
            orderbook_responses=dict(book_rows),
        )
    except (ValueError, TypeError, KeyError, OverflowError, public_read.PublicReadFailure) as exc:
        raise CaptureError("Kalshi research bundle invalid or unavailable; abstain") from exc

    return ReadOnlyUntrustedBundle(
        series, target, event, series_response, tuple(market_rows), tuple(book_rows), replay
    )
