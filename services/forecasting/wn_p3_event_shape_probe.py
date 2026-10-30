"""WN-P3c one-call documented-event schema probe: research-only, no authority.

This module makes NO request on import. An explicit call uses exactly one
bounded, unauthenticated GET through the reviewed fixed-origin public_read.
It does NOT attempt a six-book/economics read, retry, persist a claim, or
attest source custody. Callable overrides are not offered at this boundary;
unit tests replace the existing transport function with synthetic data.

Kalshi documents with_nested_markets=true as placing markets in
event.markets. The P3d untrusted replay accepts that documented shape
without moving/copying fields into retained evidence. This is not proof
that a future Kalshi API response will match.
A schema mismatch is a negative result, never evidence of an exchange edge.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum

from services.market_universe import public_read
from .wn_p3_untrusted_envelope_replay import (
    strict_json_value_equal,
    validate_strict_untrusted_response,
)

from .wn_p3_sibling_preflight import PreflightError, event_ticker_for, validate_untrusted_roster

POLICY = "wn-p3c-one-call-untrusted-event-shape-probe-v1"
MAX_TIMEOUT_SECONDS = 3.0


class EventShape(StrEnum):
    MATCHES_BOTH = "MATCHES_BOTH"
    DOCUMENTED_NESTED_ONLY = "DOCUMENTED_NESTED_ONLY"
    ABSTAIN = "ABSTAIN"


@dataclass(frozen=True, slots=True)
class UntrustedEventShapeProbe:
    series_ticker: str
    target_local_date: date
    event_ticker: str
    expected_path: str
    shape: EventShape
    reason: str
    observed_at_utc: datetime | None
    raw_body_b64: str | None
    body_sha256: str | None
    byte_count: int | None
    untrusted_sibling_tickers: tuple[str, ...]
    policy: str = POLICY

    @property
    def source_attested(self) -> bool:
        return False

    @property
    def full_exchange_roster_authorized(self) -> bool:
        return False

    @property
    def settlement_station_authorized(self) -> bool:
        return False

    @property
    def fee_depth_authorized(self) -> bool:
        return False

    @property
    def probability_authorized(self) -> bool:
        return False

    @property
    def paper_authorized(self) -> bool:
        return False

    @property
    def trading_authority(self) -> str:
        return "NONE"

    @property
    def research_only(self) -> bool:
        return True


def event_shape_path(series_ticker: str, target_local_date: date) -> str:
    """Exact event endpoint; only the frozen five-city future-date namespace."""
    ticker = event_ticker_for(series_ticker, target_local_date)
    return public_read.BASE + "/events/" + ticker + "?with_nested_markets=true"


def inspect_untrusted_event_shape(
    *, series_ticker: str, target_local_date: date, response: object
) -> UntrustedEventShapeProbe:
    """Independently rehash exact raw bytes and inspect nested-vs-top-level shape.

    Response is caller controlled; even a complete structural match cannot
    establish actual network origin or issuance. No response data is rewritten.
    """
    ticker = event_ticker_for(series_ticker, target_local_date)
    path = event_shape_path(series_ticker, target_local_date)

    def abstain(reason: str) -> UntrustedEventShapeProbe:
        return UntrustedEventShapeProbe(
            series_ticker=series_ticker,
            target_local_date=target_local_date,
            event_ticker=ticker,
            expected_path=path,
            shape=EventShape.ABSTAIN,
            reason=reason,
            observed_at_utc=None,
            raw_body_b64=None,
            body_sha256=None,
            byte_count=None,
            untrusted_sibling_tickers=(),
        )

    try:
        payload, observed = validate_strict_untrusted_response(
            response, expected_path=path
        )
    except public_read.PublicReadFailure:
        return abstain("INVALID_OR_FAILED_PUBLIC_RESPONSE")

    if not isinstance(response, dict):
        return abstain("INVALID_PUBLIC_RESPONSE")
    body_b64 = response.get("raw_body_b64")
    digest = response.get("body_sha256")
    count = response.get("bytes")
    if not isinstance(body_b64, str) or not isinstance(digest, str) or type(count) is not int:
        return abstain("INVALID_RETAINED_BODY")

    event = payload.get("event")
    if not isinstance(event, dict):
        return abstain("MISSING_EVENT")
    nested = event.get("markets")
    if not isinstance(nested, list):
        return abstain("MISSING_DOCUMENTED_NESTED_MARKETS")
    try:
        roster = validate_untrusted_roster(
            series_ticker=series_ticker,
            target_local_date=target_local_date,
            event_payload={"event": event, "markets": nested},
        )
    except PreflightError:
        return abstain("UNTRUSTED_SIX_SIBLING_PREFLIGHT_FAILED")

    top_level = payload.get("markets")
    if "markets" in payload and not strict_json_value_equal(top_level, nested):
        return abstain("NESTED_AND_TOP_LEVEL_ROSTERS_DISAGREE")
    shape = (
        EventShape.MATCHES_BOTH
        if isinstance(top_level, list)
        else EventShape.DOCUMENTED_NESTED_ONLY
    )
    return UntrustedEventShapeProbe(
        series_ticker=series_ticker,
        target_local_date=target_local_date,
        event_ticker=ticker,
        expected_path=path,
        shape=shape,
        reason="STRUCTURAL_ONLY_NOT_SOURCE_ATTESTED",
        observed_at_utc=observed.astimezone(UTC),
        raw_body_b64=body_b64,
        body_sha256=digest,
        byte_count=count,
        untrusted_sibling_tickers=roster.market_tickers,
    )


def acquire_one_event_shape_probe(
    *, series_ticker: str, target_local_date: date
) -> UntrustedEventShapeProbe:
    """One fixed-origin GET only, no retries; no persistence or positive authority.

    Explicit invocation only. Capture errors are returned as negative results
    without replacing missing or failed bytes with fabricated observations.
    This does NOT implement the Issue #244 five-city attempt denominator.
    """
    path = event_shape_path(series_ticker, target_local_date)
    try:
        response = public_read.get_with_timeout(path, timeout_seconds=MAX_TIMEOUT_SECONDS)
    except public_read.PublicReadFailure:
        ticker = event_ticker_for(series_ticker, target_local_date)
        return UntrustedEventShapeProbe(
            series_ticker=series_ticker,
            target_local_date=target_local_date,
            event_ticker=ticker,
            expected_path=path,
            shape=EventShape.ABSTAIN,
            reason="PUBLIC_GET_FAILURE_NO_RETRY",
            observed_at_utc=None,
            raw_body_b64=None,
            body_sha256=None,
            byte_count=None,
            untrusted_sibling_tickers=(),
        )
    return inspect_untrusted_event_shape(
        series_ticker=series_ticker, target_local_date=target_local_date, response=response
    )
