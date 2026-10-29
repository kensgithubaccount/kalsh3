"""Offline WN-P3 structural six-sibling preflight; deliberately NOT market evidence.

Consumes UNTRUSTED, caller-supplied event/market dictionaries. This cannot attest that
any bytes came from Kalshi, verify the textual station/rule/settlement semantics, or
calculate executable depth and fees. A successful result is *never* a pass for market
acquisition, probability evaluation, paper signaling, or trading. The later P3 issuer
must obtain and independently revalidate exact raw public-GET response bodies, bind
station/rules/fee policy, and preserve all five city failures before granting even a
research market-economics observation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

CITY_SERIES = frozenset(
    {"KXHIGHTBOS", "KXHIGHMIA", "KXHIGHDEN", "KXHIGHLAX", "KXHIGHTSEA"}
)
FIRST_TARGET = date(2026, 11, 1)
_POLICY = "wn-p3-untrusted-integer-fahrenheit-six-sibling-preflight-v1"
_NUMBER = re.compile(r"\d+(?:\.\d+)?\Z")
_MARKET_SUFFIX = re.compile(r"[A-Z0-9][A-Z0-9_.-]*\Z")


class PreflightError(ValueError):
    """Reject a malformed or incomplete UNTRUSTED structural fixture."""


@dataclass(frozen=True, slots=True)
class StructuralRoster:
    series_ticker: str
    event_ticker: str
    target_local_date: date
    market_tickers: tuple[str, ...]
    integer_fahrenheit_bins: tuple[tuple[int, int], ...]
    policy: str = _POLICY
    input_source_attested: bool = False
    rules_and_station_authorized: bool = False
    fee_depth_authorized: bool = False
    probability_authorized: bool = False
    paper_authorized: bool = False
    trading_authority: str = "NONE"
    research_only: bool = True


def event_ticker_for(series_ticker: str, target_local_date: date) -> str:
    """Known date pattern, not proof the event exists at the exchange."""
    if series_ticker not in CITY_SERIES:
        raise PreflightError("series outside frozen five-city roster")
    if (
        type(target_local_date) is not date
        or not FIRST_TARGET <= target_local_date <= date(2099, 12, 31)
    ):
        raise PreflightError("target local date outside frozen prospective scope")
    return f"{series_ticker}-{target_local_date.strftime('%y%b%d').upper()}"


def _integer_strike(value: object, field: str) -> int:
    if type(value) is int:
        parsed = Decimal(value)
    elif type(value) is str and _NUMBER.fullmatch(value) is not None:
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:
            raise PreflightError(f"invalid {field}") from exc
    else:
        raise PreflightError(f"{field} must be a fixed-point integer Fahrenheit value")
    if not parsed.is_finite() or parsed != parsed.to_integral_value():
        raise PreflightError(f"{field} is not an exact integer Fahrenheit bound")
    if not -200 <= parsed <= 200:
        raise PreflightError(f"{field} outside conservative temperature bound")
    return int(parsed)


def validate_untrusted_roster(
    *,
    series_ticker: str,
    target_local_date: date,
    event_payload: object,
) -> StructuralRoster:
    """Require exactly six, identity-consistent, nonoverlapping integer-F bins.

    The caller must NOT interpret success as acceptance of the actual Kalshi roster.
    Exact source-body validation and authoritative rule-text parsing are not here.
    """
    expected = event_ticker_for(series_ticker, target_local_date)
    if not isinstance(event_payload, dict):
        raise PreflightError("event payload must be a dictionary")
    event = event_payload.get("event")
    markets = event_payload.get("markets")
    if not isinstance(event, dict) or not isinstance(markets, list) or len(markets) != 6:
        raise PreflightError("event and exactly six siblings required")
    if event.get("event_ticker") != expected or event.get("series_ticker") != series_ticker:
        raise PreflightError("event identity or date mismatch")

    rows: dict[str, list[tuple[str, int, int]]] = {"less": [], "between": [], "greater": []}
    tickers: set[str] = set()
    for raw in markets:
        if not isinstance(raw, dict):
            raise PreflightError("market is not a dictionary")
        ticker = raw.get("ticker")
        if not isinstance(ticker, str) or not ticker.startswith(expected + "-"):
            raise PreflightError("market ticker outside exact event")
        suffix = ticker[len(expected) + 1 :]
        if _MARKET_SUFFIX.fullmatch(suffix) is None or ticker in tickers:
            raise PreflightError("malformed or duplicate ticker")
        tickers.add(ticker)
        if raw.get("event_ticker") != expected:
            raise PreflightError("market references different event")
        if raw.get("status") != "active" or raw.get("market_type") != "binary":
            raise PreflightError("non-active or non-binary market")
        if raw.get("is_provisional", False) is not False:
            raise PreflightError("provisional market")
        if raw.get("mve_collection_ticker") or raw.get("multivariate_contract"):
            raise PreflightError("multivariate market unsupported")
        if not isinstance(raw.get("rules_primary"), str) or not raw["rules_primary"]:
            raise PreflightError("missing rules text")
        if raw.get("functional_strike") is not None or raw.get("custom_strike") is not None:
            raise PreflightError("unsupported functional/custom strike")
        kind = raw.get("strike_type")
        if not isinstance(kind, str) or kind not in rows:
            raise PreflightError("unsupported strike_type")
        lower = raw.get("floor_strike")
        upper = raw.get("cap_strike")
        if kind == "less":
            if lower is not None:
                raise PreflightError("less tail has unexpected lower bound")
            cap = _integer_strike(upper, "less cap")
            rows[kind].append((ticker, cap, cap))
        elif kind == "greater":
            if upper is not None:
                raise PreflightError("greater tail has unexpected upper bound")
            floor = _integer_strike(lower, "greater floor")
            rows[kind].append((ticker, floor, floor))
        else:
            floor = _integer_strike(lower, "between floor")
            cap = _integer_strike(upper, "between cap")
            if floor > cap:
                raise PreflightError("inverted between range")
            rows[kind].append((ticker, floor, cap))

    if (len(rows["less"]), len(rows["between"]), len(rows["greater"])) != (1, 4, 1):
        raise PreflightError("expected one less, four between, one greater")
    bands = sorted(rows["between"], key=lambda row: row[1])
    less = rows["less"][0]
    greater = rows["greater"][0]
    if less[1] != bands[0][1] or greater[1] != bands[-1][2]:
        raise PreflightError("tails do not meet bounded endpoints")
    for left, right in zip(bands, bands[1:], strict=False):
        if left[2] + 1 != right[1]:
            raise PreflightError("gap or overlap on integer Fahrenheit lattice")
    ordered = (less, *bands, greater)
    return StructuralRoster(
        series_ticker=series_ticker,
        event_ticker=expected,
        target_local_date=target_local_date,
        market_tickers=tuple(item[0] for item in ordered),
        integer_fahrenheit_bins=tuple((item[1], item[2]) for item in bands),
    )
