"""Synthetic hostile tests: no live GET, source capture, or economics authority."""

from __future__ import annotations

import base64
import hashlib
import json
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from services.forecasting.wn_p3_sibling_preflight import (
    CITY_SERIES,
    PreflightError,
    event_ticker_for,
)
from services.forecasting.wn_p3_untrusted_envelope_replay import (
    ReplayError,
    UntrustedReplay,
    replay_untrusted_six_sibling_bundle,
)
from services.market_universe.public_read import BASE, PublicReadFailure

DATE = date(2026, 11, 1)
NOW = datetime(2026, 10, 31, 9, 0, tzinfo=UTC)
SERIES = "KXHIGHTBOS"
SPECS = (
    ("T70", "less", None, 70),
    ("B70", "between", 70, 71),
    ("B72", "between", 72, 73),
    ("B74", "between", 74, 75),
    ("B76", "between", 76, 77),
    ("T77", "greater", 77, None),
)


def wrap(path: str, payload: dict[str, Any], *, observed: datetime = NOW) -> dict[str, Any]:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return {
        "path": path,
        "observed_at": observed.isoformat(),
        "classification": "SUCCESS",
        "status": 200,
        "bytes": len(body),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "raw_body_b64": base64.b64encode(body).decode("ascii"),
        "payload": payload,
    }


def sample(series: str = SERIES) -> dict[str, Any]:
    event = event_ticker_for(series, DATE)
    markets: list[dict[str, Any]] = [
        {
            "ticker": f"{event}-{suffix}",
            "event_ticker": event,
            "market_type": "binary",
            "status": "active",
            "strike_type": strike,
            "floor_strike": floor,
            "cap_strike": cap,
            "rules_primary": "Synthetic-only rule, NOT source authority",
        }
        for suffix, strike, floor, cap in SPECS
    ]
    event_path = BASE + "/events/" + event + "?with_nested_markets=true"
    bundle: dict[str, Any] = {
        "series_ticker": series,
        "target_local_date": DATE,
        "evaluated_at": NOW,
        "event_response": wrap(
            event_path,
            {"event": {"event_ticker": event, "series_ticker": series}, "markets": markets},
        ),
        "series_response": wrap(BASE + "/series/" + series, {"series": {"ticker": series}}),
        "market_responses": {},
        "orderbook_responses": {},
    }
    for market in markets:
        ticker = market["ticker"]
        bundle["market_responses"][ticker] = wrap(
            BASE + "/markets/" + ticker, {"market": deepcopy(market)}
        )
        bundle["orderbook_responses"][ticker] = wrap(
            BASE + "/markets/orderbooks?tickers=" + ticker,
            {"orderbooks": [{"ticker": ticker, "orderbook_fp": {}}]},
        )
    return bundle


def run(bundle: dict[str, Any]) -> UntrustedReplay:
    return replay_untrusted_six_sibling_bundle(**bundle)


def repack(envelope: dict[str, Any], payload: dict[str, Any]) -> None:
    envelope.update(wrap(envelope["path"], payload))


@pytest.mark.parametrize("series", sorted(CITY_SERIES))
def test_five_cities_full_roster_replays_without_authority(series: str) -> None:
    result = run(sample(series))
    assert len(result.roster.market_tickers) == 6
    assert len(result.responses) == 14
    assert len({r.path for r in result.responses}) == 14
    assert all(len(r.raw_body_sha256) == 64 for r in result.responses)
    assert result.evaluated_at_utc == NOW
    assert not result.input_source_attested
    assert not result.rules_and_station_authorized
    assert not result.fee_depth_authorized
    assert not result.probability_authorized
    assert not result.paper_authorized
    assert result.trading_authority == "NONE"
    assert result.research_only


def test_order_invariant() -> None:
    bundle = sample()
    expected_tickers = run(bundle).roster.market_tickers
    payload = bundle["event_response"]["payload"]
    payload["markets"].reverse()
    repack(bundle["event_response"], payload)
    bundle["market_responses"] = dict(reversed(list(bundle["market_responses"].items())))
    bundle["orderbook_responses"] = dict(reversed(list(bundle["orderbook_responses"].items())))
    assert run(bundle).roster.market_tickers == expected_tickers


@pytest.mark.parametrize("kind", ["market", "orderbook"])
@pytest.mark.parametrize("operation", ["missing", "extra"])
def test_full_denominator_no_silent_sibling_dropping(kind: str, operation: str) -> None:
    bundle = sample()
    field = "market_responses" if kind == "market" else "orderbook_responses"
    responses = bundle[field]
    if operation == "missing":
        responses.pop(next(iter(responses)))
    else:
        responses["UNRELATED-TICKER"] = next(iter(responses.values()))
    with pytest.raises(ReplayError):
        run(bundle)


def test_event_nested_missing_extra_duplicate_or_wrong_date_rejected() -> None:
    for change in ("missing", "extra", "duplicate", "wrong-date"):
        bundle = sample()
        env = bundle["event_response"]
        payload = env["payload"]
        markets = payload["markets"]
        if change == "missing":
            markets.pop()
        elif change == "extra":
            markets.append(deepcopy(markets[0]))
        elif change == "duplicate":
            markets[1] = deepcopy(markets[0])
        else:
            payload["event"]["event_ticker"] = "KXHIGHTBOS-26NOV02"
        repack(env, payload)
        with pytest.raises((ReplayError, PreflightError)):
            run(bundle)


@pytest.mark.parametrize("field", ["event_response", "series_response"])
def test_event_and_series_path_mismatch(field: str) -> None:
    bundle = sample()
    bundle[field]["path"] += "/forged"
    with pytest.raises(PublicReadFailure):
        run(bundle)


@pytest.mark.parametrize("field", ["market_responses", "orderbook_responses"])
def test_market_and_book_endpoint_path_mismatch(field: str) -> None:
    bundle = sample()
    first = next(iter(bundle[field].values()))
    first["path"] += "&forged=true"
    with pytest.raises(PublicReadFailure):
        run(bundle)


@pytest.mark.parametrize("field", ["event_response", "market_responses", "orderbook_responses"])
def test_raw_body_hash_mismatch_rejected(field: str) -> None:
    bundle = sample()
    env = bundle[field] if field == "event_response" else next(iter(bundle[field].values()))
    env["body_sha256"] = "0" * 64
    with pytest.raises(PublicReadFailure):
        run(bundle)


def test_raw_payload_mismatch_rejected() -> None:
    bundle = sample()
    first = next(iter(bundle["market_responses"].values()))
    first["payload"]["market"]["rules_primary"] = "forged after hash"
    with pytest.raises(PublicReadFailure):
        run(bundle)


@pytest.mark.parametrize("field", ["event_response", "series_response", "orderbook_responses"])
def test_future_or_stale_response_rejected(field: str) -> None:
    for offset in (-121, 1):
        bundle = sample()
        env = (
            next(iter(bundle[field].values())) if field == "orderbook_responses" else bundle[field]
        )
        env["observed_at"] = (NOW + timedelta(seconds=offset)).isoformat()
        with pytest.raises(ReplayError):
            run(bundle)


def test_120_seconds_inclusive_and_timezone_normalized() -> None:
    bundle = sample()
    bundle["event_response"]["observed_at"] = (NOW - timedelta(seconds=120)).isoformat()
    assert run(bundle).evaluated_at_utc == NOW


def test_naive_clock_rejected() -> None:
    bundle = sample()
    bundle["evaluated_at"] = NOW.replace(tzinfo=None)
    with pytest.raises(ReplayError):
        run(bundle)


def test_nested_market_rule_conflict_rejected_even_when_rehashed() -> None:
    bundle = sample()
    env = next(iter(bundle["market_responses"].values()))
    payload = env["payload"]
    payload["market"]["rules_primary"] = "forged independent rule"
    repack(env, payload)
    with pytest.raises(ReplayError):
        run(bundle)


def test_book_ticker_conflict_rejected_even_when_rehashed() -> None:
    bundle = sample()
    env = next(iter(bundle["orderbook_responses"].values()))
    payload = env["payload"]
    payload["orderbooks"][0]["ticker"] = "OTHER"
    repack(env, payload)
    with pytest.raises(ReplayError):
        run(bundle)


@pytest.mark.parametrize("flag", [True, False])
def test_rehashed_standalone_multivariate_field_conflict_rejected(flag: bool) -> None:
    bundle = sample()
    env = next(iter(bundle["market_responses"].values()))
    payload = env["payload"]
    payload["market"]["multivariate_contract"] = flag
    repack(env, payload)
    with pytest.raises(ReplayError):
        run(bundle)


def test_rehashed_nested_multivariate_field_rejected_by_preflight() -> None:
    bundle = sample()
    env = bundle["event_response"]
    payload = env["payload"]
    payload["markets"][0]["multivariate_contract"] = True
    repack(env, payload)
    with pytest.raises(PreflightError):
        run(bundle)


@pytest.mark.parametrize("forged_ticker", [None, "", "OTHER"])
def test_rehashed_book_row_must_identify_exact_sibling(
    forged_ticker: str | None,
) -> None:
    bundle = sample()
    env = next(iter(bundle["orderbook_responses"].values()))
    payload = env["payload"]
    row = payload["orderbooks"][0]
    if forged_ticker is None:
        row.pop("ticker")
    else:
        row["ticker"] = forged_ticker
    repack(env, payload)
    with pytest.raises(ReplayError):
        run(bundle)


def test_book_missing_or_multirow_rejected() -> None:
    bad_rows: tuple[list[dict[str, Any]], ...] = (
        [],
        [{"orderbook_fp": {}}, {"orderbook_fp": {}}],
    )
    for rows in bad_rows:
        bundle = sample()
        env = next(iter(bundle["orderbook_responses"].values()))
        repack(env, {"orderbooks": rows})
        with pytest.raises(ReplayError):
            run(bundle)


def test_book_with_missing_depth_cannot_grant_fee_authority() -> None:
    result = run(sample())
    assert not result.fee_depth_authorized
    assert not result.paper_authorized


def test_fully_forged_self_consistent_bytes_are_not_source_attestation() -> None:
    bundle = sample()
    event = bundle["event_response"]
    event["payload"]["event"]["title"] = "Caller-authored fake title"
    repack(event, event["payload"])
    result = run(bundle)
    assert result.input_source_attested is False
    assert result.rules_and_station_authorized is False


def test_cannot_override_positive_authority_properties() -> None:
    result = run(sample())
    with pytest.raises(AttributeError):
        object.__setattr__(result, "paper_authorized", True)
    with pytest.raises(AttributeError):
        object.__setattr__(result, "input_source_attested", True)


def test_rejects_bad_status_and_bad_base64() -> None:
    for field, value in (("status", 403), ("raw_body_b64", "not base64!")):
        bundle = sample()
        bundle["series_response"][field] = value
        with pytest.raises(PublicReadFailure):
            run(bundle)
