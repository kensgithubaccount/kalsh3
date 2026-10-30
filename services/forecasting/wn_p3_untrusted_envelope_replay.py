"""WN-P3b offline replay of *untrusted* retained Kalshi GET envelopes.

No network calls, credentials, scheduling, pricing, station authority, or orders.
This does NOT attest where response bytes came from: callers can manufacture a
self-consistent raw body, timestamp and SHA256. Successful replay cannot issue
a market-economics observation or upgrade any paper/model/trading permissions.

One source event must include its complete nested market set; six standalone
market envelopes and six book envelopes must match that exact roster. This is
a synthetic schema contract, not proof the real endpoint supplies that shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from services.market_universe.domain import METADATA_FIELDS, RULE_FIELDS
from services.market_universe.m27e_public_acceptance import validate_response_evidence
from services.market_universe.public_read import BASE

from .wn_p3_sibling_preflight import (
    StructuralRoster,
    event_ticker_for,
    validate_untrusted_roster,
)

MAX_AGE = timedelta(seconds=120)
POLICY = "wn-p3b-untrusted-retained-envelope-replay-v1"
_COMPARE_FIELDS = frozenset(
    (
        "ticker",
        "event_ticker",
        "status",
        "market_type",
        "strike_type",
        *RULE_FIELDS,
        *METADATA_FIELDS,
    )
)


class ReplayError(ValueError):
    """Unsafe, incomplete, inconsistent or stale caller-supplied replay bundle."""


@dataclass(frozen=True, slots=True)
class UntrustedResponseReceipt:
    path: str
    raw_body_sha256: str
    observed_at_utc: datetime


@dataclass(frozen=True, slots=True)
class UntrustedReplay:
    roster: StructuralRoster
    evaluated_at_utc: datetime
    responses: tuple[UntrustedResponseReceipt, ...]
    policy: str = POLICY

    @property
    def input_source_attested(self) -> bool:
        return False

    @property
    def rules_and_station_authorized(self) -> bool:
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


def _verify(
    response: object, expected_path: str, evaluated_at_utc: datetime
) -> tuple[dict[str, Any], UntrustedResponseReceipt]:
    payload, observed = validate_response_evidence(response, expected_path=expected_path)
    if observed > evaluated_at_utc or evaluated_at_utc - observed > MAX_AGE:
        raise ReplayError("retained response timestamp is future or older than 120 seconds")
    if not isinstance(response, dict):
        raise ReplayError("retained response envelope missing")
    digest = response.get("body_sha256")
    if not isinstance(digest, str):
        raise ReplayError("retained body digest missing")
    return payload, UntrustedResponseReceipt(expected_path, digest, observed)


def replay_untrusted_six_sibling_bundle(
    *,
    series_ticker: str,
    target_local_date: date,
    evaluated_at: datetime,
    event_response: object,
    series_response: object,
    market_responses: object,
    orderbook_responses: object,
) -> UntrustedReplay:
    """Check offline envelopes without issuing source, economics or paper authority.

    Exact source-body hashes, paths, JSON bytes, 14 individual observation times,
    nested-event membership and critical market metadata must agree. This is not
    an acquisition method or a positive proof of public-source provenance.
    """
    if not isinstance(evaluated_at, datetime):
        raise ReplayError("evaluation clock missing")
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ReplayError("evaluation clock must be timezone-aware")
    decision = evaluated_at.astimezone(UTC)
    expected_event = event_ticker_for(series_ticker, target_local_date)

    event_path = BASE + "/events/" + expected_event + "?with_nested_markets=true"
    event_payload, event_receipt = _verify(event_response, event_path, decision)
    event = event_payload.get("event")
    nested_markets = event_payload.get("markets")
    if not isinstance(event, dict) or not isinstance(nested_markets, list):
        raise ReplayError("complete nested-event six-sibling array required")
    roster = validate_untrusted_roster(
        series_ticker=series_ticker,
        target_local_date=target_local_date,
        event_payload={"event": event, "markets": nested_markets},
    )

    series_path = BASE + "/series/" + series_ticker
    series_payload, series_receipt = _verify(series_response, series_path, decision)
    series = series_payload.get("series")
    if not isinstance(series, dict) or series.get("ticker") != series_ticker:
        raise ReplayError("series identity mismatch")

    expected = set(roster.market_tickers)
    if not isinstance(market_responses, dict) or set(market_responses) != expected:
        raise ReplayError("missing, extra or duplicate single-market evidence")
    if not isinstance(orderbook_responses, dict) or set(orderbook_responses) != expected:
        raise ReplayError("missing, extra or duplicate orderbook evidence")

    nested_by_ticker = {m["ticker"]: m for m in nested_markets}
    receipts = [event_receipt, series_receipt]
    for ticker in roster.market_tickers:
        path = BASE + "/markets/" + ticker
        market_payload, receipt = _verify(market_responses[ticker], path, decision)
        raw_market = market_payload.get("market")
        if not isinstance(raw_market, dict) or raw_market.get("ticker") != ticker:
            raise ReplayError("standalone market ticker mismatch")
        nested = nested_by_ticker[ticker]
        if any(raw_market.get(field) != nested.get(field) for field in _COMPARE_FIELDS):
            raise ReplayError("nested and single-market metadata or rules conflict")
        receipts.append(receipt)

    for ticker in roster.market_tickers:
        path = BASE + "/markets/orderbooks?tickers=" + ticker
        book_payload, receipt = _verify(orderbook_responses[ticker], path, decision)
        books = book_payload.get("orderbooks")
        if not isinstance(books, list) or len(books) != 1:
            raise ReplayError("exactly one retained book per sibling required")
        row = books[0]
        if not isinstance(row, dict) or not isinstance(row.get("orderbook_fp"), dict):
            raise ReplayError("retained book shape unsupported")
        if row.get("ticker") is not None and row["ticker"] != ticker:
            raise ReplayError("retained orderbook ticker mismatch")
        receipts.append(receipt)

    return UntrustedReplay(
        roster=roster, evaluated_at_utc=decision, responses=tuple(receipts)
    )
