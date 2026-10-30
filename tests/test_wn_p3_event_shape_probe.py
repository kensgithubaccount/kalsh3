"""Synthetic P3c schema guards. Tests never call the real Kalshi endpoint."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, date, datetime
from typing import Any

import pytest

from services.forecasting.wn_p3_event_shape_probe import (
    MAX_TIMEOUT_SECONDS,
    EventShape,
    UntrustedEventShapeProbe,
    acquire_one_event_shape_probe,
    event_shape_path,
    inspect_untrusted_event_shape,
)
from services.forecasting.wn_p3_sibling_preflight import (
    CITY_SERIES,
    PreflightError,
    event_ticker_for,
)
from services.market_universe import public_read

TARGET = date(2026, 11, 1)
SERIES = "KXHIGHTBOS"
NOW = datetime(2026, 10, 29, 18, 0, tzinfo=UTC)
SPECS = (
    ("LT", "less", None, 70),
    ("B70", "between", 70, 71),
    ("B72", "between", 72, 73),
    ("B74", "between", 74, 75),
    ("B76", "between", 76, 77),
    ("GT", "greater", 77, None),
)


def wrap(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "path": path,
        "observed_at": NOW.isoformat(),
        "status": 200,
        "classification": "SUCCESS",
        "payload": payload,
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "raw_body_b64": base64.b64encode(body).decode("ascii"),
        "bytes": len(body),
    }


def event_fixture(
    series: str = SERIES, *, top_level: bool = False
) -> dict[str, Any]:
    event = event_ticker_for(series, TARGET)
    markets: list[dict[str, Any]] = [
        {
            "ticker": f"{event}-{suffix}",
            "event_ticker": event,
            "market_type": "binary",
            "status": "active",
            "strike_type": kind,
            "floor_strike": floor,
            "cap_strike": cap,
            "rules_primary": "Synthetic, not verified weather station semantics",
        }
        for suffix, kind, floor, cap in SPECS
    ]
    payload: dict[str, Any] = {
        "event": {
            "event_ticker": event,
            "series_ticker": series,
            "markets": markets,
        }
    }
    if top_level:
        payload["markets"] = markets
    return wrap(event_shape_path(series, TARGET), payload)


def inspect(evidence: object, *, series: str = SERIES) -> UntrustedEventShapeProbe:
    return inspect_untrusted_event_shape(
        series_ticker=series, target_local_date=TARGET, response=evidence
    )


@pytest.mark.parametrize("series", sorted(CITY_SERIES))
@pytest.mark.parametrize("top_level", [False, True])
def test_five_city_documented_and_legacy_shapes(
    series: str, top_level: bool
) -> None:
    evidence = event_fixture(series, top_level=top_level)
    result = inspect(evidence, series=series)
    expected = (
        EventShape.MATCHES_BOTH
        if top_level
        else EventShape.NESTED_ONLY_P3B_INCOMPATIBLE
    )
    assert result.shape is expected
    assert len(result.untrusted_sibling_tickers) == 6
    assert result.byte_count == evidence["bytes"]
    assert result.raw_body_b64 == evidence["raw_body_b64"]
    assert result.body_sha256 == evidence["body_sha256"]
    assert result.observed_at_utc == NOW
    assert not result.source_attested
    assert not result.full_exchange_roster_authorized
    assert not result.settlement_station_authorized
    assert not result.fee_depth_authorized
    assert not result.probability_authorized
    assert not result.paper_authorized
    assert result.trading_authority == "NONE"
    assert result.research_only


def test_exact_path_and_future_date_only() -> None:
    assert event_shape_path(SERIES, TARGET) == (
        "/trade-api/v2/events/KXHIGHTBOS-26NOV01?with_nested_markets=true"
    )
    with pytest.raises(PreflightError):
        event_shape_path(SERIES, date(2026, 10, 31))
    with pytest.raises(PreflightError):
        event_shape_path("KXHIGHCHI", TARGET)


@pytest.mark.parametrize("field", ["body_sha256", "bytes", "raw_body_b64"])
def test_forged_retained_body_envelope_rejected(field: str) -> None:
    evidence = event_fixture()
    evidence[field] = "bad" if field != "bytes" else 1
    result = inspect(evidence)
    assert result.shape is EventShape.ABSTAIN
    assert not result.source_attested
    assert result.raw_body_b64 is None


def test_mutation_without_rehash_rejected() -> None:
    evidence = event_fixture()
    evidence["payload"]["event"]["markets"].pop()
    assert inspect(evidence).shape is EventShape.ABSTAIN


@pytest.mark.parametrize("path_change", ["/other", "&extra=1"])
def test_path_does_not_allow_other_endpoint_or_query(path_change: str) -> None:
    evidence = event_fixture()
    evidence["path"] += path_change
    assert inspect(evidence).shape is EventShape.ABSTAIN


@pytest.mark.parametrize("change", ["missing", "duplicate", "extra", "wrong-date"])
def test_bad_nested_sibling_roster_abstains_even_with_valid_sha(change: str) -> None:
    evidence = event_fixture()
    payload = evidence["payload"]
    markets = payload["event"]["markets"]
    if change == "missing":
        markets.pop()
    elif change == "extra":
        markets.append(markets[-1])
    elif change == "duplicate":
        markets[1] = markets[0]
    else:
        payload["event"]["event_ticker"] = "KXHIGHTBOS-26NOV02"
    repacked = wrap(evidence["path"], payload)
    assert inspect(repacked).shape is EventShape.ABSTAIN


def test_no_silent_copy_from_legacy_top_level_into_nested() -> None:
    evidence = event_fixture(top_level=True)
    payload = evidence["payload"]
    payload["event"].pop("markets")
    assert inspect(wrap(evidence["path"], payload)).shape is EventShape.ABSTAIN


def test_no_silent_copy_from_nested_into_conflicting_top_level() -> None:
    evidence = event_fixture(top_level=True)
    payload = evidence["payload"]
    payload["markets"] = payload["markets"][:-1]
    assert inspect(wrap(evidence["path"], payload)).shape is EventShape.ABSTAIN


def test_rehashed_forgery_can_pass_but_never_attests_source() -> None:
    evidence = event_fixture(top_level=True)
    evidence["payload"]["event"]["title"] = "Caller-authored provenance is not attestation"
    forgery = wrap(evidence["path"], evidence["payload"])
    result = inspect(forgery)
    assert result.shape is EventShape.MATCHES_BOTH
    assert result.source_attested is False
    assert result.paper_authorized is False


def test_one_exact_bounded_public_get_and_no_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, float]] = []
    evidence = event_fixture()

    def fake_get(path: str, *, timeout_seconds: float) -> dict[str, Any]:
        calls.append((path, timeout_seconds))
        return evidence

    monkeypatch.setattr(public_read, "get_with_timeout", fake_get)
    result = acquire_one_event_shape_probe(series_ticker=SERIES, target_local_date=TARGET)
    assert calls == [(event_shape_path(SERIES, TARGET), MAX_TIMEOUT_SECONDS)]
    assert result.shape is EventShape.NESTED_ONLY_P3B_INCOMPATIBLE
    assert result.raw_body_b64 == evidence["raw_body_b64"]
    assert result.source_attested is False


def test_network_error_is_single_failed_attempt_no_fabricated_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_get(path: str, *, timeout_seconds: float) -> dict[str, Any]:
        calls.append(path)
        raise public_read.PublicReadFailure("Synthetic network failure")

    monkeypatch.setattr(public_read, "get_with_timeout", fake_get)
    result = acquire_one_event_shape_probe(series_ticker=SERIES, target_local_date=TARGET)
    assert calls == [event_shape_path(SERIES, TARGET)]
    assert result.shape is EventShape.ABSTAIN
    assert result.reason == "PUBLIC_GET_FAILURE_NO_RETRY"
    assert result.raw_body_b64 is None
    assert result.body_sha256 is None
    assert result.observed_at_utc is None


def test_failed_http_status_is_abstention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = event_fixture()
    evidence["status"] = 429
    evidence["classification"] = "HTTP_OR_NETWORK_FAILURE"

    def fake_get(path: str, *, timeout_seconds: float) -> dict[str, Any]:
        return evidence

    monkeypatch.setattr(public_read, "get_with_timeout", fake_get)
    result = acquire_one_event_shape_probe(series_ticker=SERIES, target_local_date=TARGET)
    assert result.shape is EventShape.ABSTAIN
    assert result.source_attested is False


def test_direct_construction_and_properties_are_not_authority() -> None:
    original = inspect(event_fixture())
    assert not original.source_attested
    with pytest.raises(AttributeError):
        object.__setattr__(original, "paper_authorized", True)
    with pytest.raises(TypeError):
        UntrustedEventShapeProbe(**{"source_attested": True})
