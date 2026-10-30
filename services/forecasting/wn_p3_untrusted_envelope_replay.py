"""WN-P3b offline replay of *untrusted* retained Kalshi GET envelopes.

No network calls, credentials, scheduling, pricing, station authority, or orders.
This does NOT attest where response bytes came from: callers can manufacture a
self-consistent raw body, timestamp and SHA256. Successful replay cannot issue
a market-economics observation or upgrade any paper/model/trading permissions.

One source event must include its complete six-market set. Documented
event.markets is preferred; legacy synthetic top-level markets is accepted
only when event.markets is absent. Both fields must agree if present. Six
standalone markets and books must match. This is NOT live source evidence.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from services.market_universe.domain import METADATA_FIELDS, RULE_FIELDS
from services.market_universe.m27e_public_acceptance import validate_response_evidence
from services.market_universe.public_read import BASE, PublicReadFailure

from .wn_p3_sibling_preflight import (
    StructuralRoster,
    event_ticker_for,
    validate_untrusted_roster,
)

MAX_AGE = timedelta(seconds=120)
POLICY = "wn-p3d-untrusted-retained-envelope-replay-shape-v2"
_COMPARE_FIELDS = frozenset(
    (
        "ticker",
        "event_ticker",
        "status",
        "market_type",
        "strike_type",
        "multivariate_contract",
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


def strict_json_value_equal(left: object, right: object) -> bool:
    """Compare JSON structures without Python bool/int or float/int coercion."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        if not isinstance(right, dict) or left.keys() != right.keys():
            return False
        return all(strict_json_value_equal(value, right[key]) for key, value in left.items())
    if isinstance(left, list):
        return (
            isinstance(right, list)
            and len(left) == len(right)
            and all(strict_json_value_equal(a, b) for a, b in zip(left, right, strict=True))
        )
    return left == right


def validate_strict_untrusted_response(
    response: object, *, expected_path: str
) -> tuple[dict[str, Any], datetime]:
    """Add type-exact body/payload identity to inherited raw-hash verification.

    The shared M27E validator uses Python equality, which treats 70 == 70.0
    and False == 0 as true. This offline research consumer re-parses retained
    bytes and rejects those JSON-type mismatches without changing M27E.
    """
    payload, observed = validate_response_evidence(response, expected_path=expected_path)
    if not isinstance(response, dict):
        raise PublicReadFailure("retained response envelope missing")
    encoded = response.get("raw_body_b64")
    if not isinstance(encoded, str):
        raise PublicReadFailure("retained raw body missing")
    raw = json.loads(base64.b64decode(encoded, validate=True))
    if not strict_json_value_equal(raw, payload):
        raise PublicReadFailure("retained JSON body/payload types mismatch")
    return payload, observed


def _verify(
    response: object, expected_path: str, evaluated_at_utc: datetime
) -> tuple[dict[str, Any], UntrustedResponseReceipt]:
    payload, observed = validate_strict_untrusted_response(response, expected_path=expected_path)
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
    if not isinstance(event, dict):
        raise ReplayError("event object missing")
    top_level = event_payload.get("markets")
    # Parse a validated in-memory view only: never change raw bytes, base64,
    # SHA256, response payload, or the retained response evidence itself.
    if "markets" in event:
        nested_markets = event["markets"]
        if not isinstance(nested_markets, list):
            raise ReplayError("documented event.markets must be an array")
        if "markets" in event_payload and not strict_json_value_equal(top_level, nested_markets):
            raise ReplayError("documented and legacy sibling arrays conflict")
    else:
        nested_markets = top_level
        if not isinstance(nested_markets, list):
            raise ReplayError("complete six-sibling event array missing")
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
        if any(
            not strict_json_value_equal(raw_market.get(field), nested.get(field))
            for field in _COMPARE_FIELDS
        ):
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
        if row.get("ticker") != ticker:
            raise ReplayError("retained orderbook ticker missing or mismatched")
        receipts.append(receipt)

    return UntrustedReplay(roster=roster, evaluated_at_utc=decision, responses=tuple(receipts))
