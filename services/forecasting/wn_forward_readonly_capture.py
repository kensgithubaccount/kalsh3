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

import json
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


def _snapshot(envelope: dict[str, object]) -> bytes:
    """Copy an entire JSON envelope into owned immutable bytes, rejecting exotic values."""
    return json.dumps(envelope, sort_keys=True, allow_nan=False, separators=(",", ":")).encode(
        "utf-8"
    )


def _restore(snapshot: bytes) -> dict[str, object]:
    parsed = json.loads(snapshot)
    if not isinstance(parsed, dict):
        raise CaptureError("immutable envelope snapshot is not a JSON object")
    return parsed


def _unique_snapshot_envelopes(
    rows: tuple[tuple[str, bytes], ...], *, label: str
) -> dict[str, dict[str, object]]:
    """Never silently discard a retained row via dict key overwrite.

    All six originally captured siblings must remain independently present,
    even after a caller uses dataclasses.replace on the frozen container.
    """
    if type(rows) is not tuple or len(rows) != 6:
        raise CaptureError(f"{label} snapshot family must contain exactly six entries")
    result: dict[str, dict[str, object]] = {}
    for row in rows:
        if (
            type(row) is not tuple
            or len(row) != 2
            or type(row[0]) is not str
            or type(row[1]) is not bytes
        ):
            raise CaptureError(f"{label} snapshot row has invalid types")
        ticker, raw = row
        if ticker in result:
            raise CaptureError(f"{label} snapshot family contains a duplicate ticker")
        result[ticker] = _restore(raw)
    if len(result) != 6:
        raise CaptureError(f"{label} snapshot family is incomplete")
    return result


@dataclass(frozen=True, slots=True)
class ReadOnlyUntrustedBundle:
    """Immutable owned snapshots; caller mutations cannot invalidate saved replay.

    The only public views are fresh decoded copies, and the replay is
    regenerated from these *same* bytes. Direct dataclass substitution cannot
    preserve an outdated PASS replay against changed source/rules/book bytes.
    """

    series: str
    target: date
    evaluated_at_utc: datetime
    _event_snapshot: bytes
    _series_snapshot: bytes
    _market_snapshots: tuple[tuple[str, bytes], ...]
    _book_snapshots: tuple[tuple[str, bytes], ...]

    @property
    def replay(self) -> UntrustedReplay:
        return replay_untrusted_six_sibling_bundle(
            series_ticker=self.series,
            target_local_date=self.target,
            evaluated_at=self.evaluated_at_utc,
            event_response=_restore(self._event_snapshot),
            series_response=_restore(self._series_snapshot),
            market_responses=_unique_snapshot_envelopes(self._market_snapshots, label="market"),
            orderbook_responses=_unique_snapshot_envelopes(self._book_snapshots, label="book"),
        )

    @property
    def event(self) -> dict[str, object]:
        _ = self.replay
        return _restore(self._event_snapshot)

    @property
    def series_response(self) -> dict[str, object]:
        _ = self.replay
        return _restore(self._series_snapshot)

    @property
    def markets(self) -> tuple[tuple[str, dict[str, object]], ...]:
        _ = self.replay
        return tuple((ticker, _restore(raw)) for ticker, raw in self._market_snapshots)

    @property
    def books(self) -> tuple[tuple[str, dict[str, object]], ...]:
        _ = self.replay
        return tuple((ticker, _restore(raw)) for ticker, raw in self._book_snapshots)

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
            response = get(path)
        else:
            response = public_read.get_with_timeout(path, timeout_seconds=TIMEOUT_SECONDS)
        # Snapshot each response immediately: getter-owned dictionaries may be
        # mutated after this call. Never retain an alias to caller input.
        if not isinstance(response, dict):
            raise CaptureError("public response envelope is not an object")
        return _restore(_snapshot(response))

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
        replay_untrusted_six_sibling_bundle(
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

    result = ReadOnlyUntrustedBundle(
        series=series,
        target=target,
        evaluated_at_utc=decision,
        _event_snapshot=_snapshot(event),
        _series_snapshot=_snapshot(series_response),
        _market_snapshots=tuple((ticker, _snapshot(row)) for ticker, row in market_rows),
        _book_snapshots=tuple((ticker, _snapshot(row)) for ticker, row in book_rows),
    )
    # Reject any disagreement between the returned owned bytes and replay.
    _ = result.replay
    return result
