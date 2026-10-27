"""Synthetic, no-network tests for P12 CPI contract-identity observation boundaries."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from hashlib import sha256

import pytest

from services.forecasting.cpi_p12_contract_identity_evidence import (
    P12ContractIdentityError,
    capture_p12_kalshi_contract_identity,
)
from services.market_universe import public_read

EVENT = "KXCPI-26OCT"
NOW = datetime(2026, 10, 27, 18, 0, tzinfo=UTC)


def market(suffix: str) -> dict[str, object]:
    return {
        "ticker": EVENT + "-" + suffix,
        "event_ticker": EVENT,
        "market_type": "binary",
        "rules_primary": "Synthetic condition (not actual CPI rules)",
        "rules_secondary": "Synthetic source (not authority)",
        "strike_type": "greater",
        "floor_strike": "0.2",
        "close_time": "2026-11-10T13:30:00Z",
    }


def body(path: str) -> dict[str, object]:
    if "/series/" in path:
        return {"series": {"ticker": "KXCPI"}}
    if "/events/" in path:
        return {
            "event": {
                "ticker": EVENT,
                "series_ticker": "KXCPI",
                "settlement_sources": [
                    {"name": "Synthetic source, not attested", "url": "https://example.org/"}
                ],
            }
        }
    if "&cursor=" in path:
        return {"markets": [market("T0.3")], "cursor": ""}
    return {"markets": [market("T0.2")], "cursor": "next-page"}


def envelope(path: str, payload: dict[str, object]) -> dict[str, object]:
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    return public_read._evidence_from_body(path, raw, 200, NOW)


def capture(*, change=None, max_pages: int = 8):
    calls: list[str] = []

    def getter(path: str) -> dict[str, object]:
        calls.append(path)
        response = envelope(path, body(path))
        return change(path, response) if change else response

    return (
        capture_p12_kalshi_contract_identity(
            EVENT, getter=getter, clock=lambda: NOW, max_pages=max_pages
        ),
        calls,
    )


def test_paginated_raw_identity_observations_never_promote_authority() -> None:
    record, paths = capture()
    assert paths == [
        "/trade-api/v2/series/KXCPI",
        "/trade-api/v2/events/KXCPI-26OCT",
        "/trade-api/v2/markets?event_ticker=KXCPI-26OCT&limit=100",
        "/trade-api/v2/markets?event_ticker=KXCPI-26OCT&limit=100&cursor=next-page",
    ]
    assert record["market_page_count"] == 2
    assert record["market_row_count"] == 2
    assert record["observed_sibling_tickers"] == [EVENT + "-T0.2", EVENT + "-T0.3"]
    assert record["metadata_gaps"] == {}
    assert record["readiness"] == "UNKNOWN_NOT_READY"
    assert record["cohort_authority"] == "OBSERVED_PAGES_ONLY_NOT_FROZEN"
    assert record["reference_month_authority"] == "UNKNOWN"
    assert record["settlement_authority"] == "UNVERIFIED"
    assert record["scientific_evidence_authorized"] is False
    assert record["phase1_economic_test_authorized"] is False
    assert record["reuters_forecast_authorized"] is False
    assert record["production_influence"] == "0"
    assert record["promotion_authority"] == "NONE"
    assert record["p12_frozen"] is False
    assert record["acquisition_mode"] == "INJECTED_TEST_GETTER"
    for observation in record["observations"]:
        raw = base64.b64decode(observation["raw_body_b64"], validate=True)
        assert sha256(raw).hexdigest() == observation["raw_body_sha256"]
        assert len(raw) == observation["byte_count"]
        assert observation["source_origin"] == "https://external-api.kalshi.com"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda x: {**x, "markets": [market("T0.2"), market("T0.2")]},
        lambda x: {**x, "markets": [market("T0.2")], "cursor": "../escape"},
        lambda x: {**x, "markets": [market("T0.2")], "cursor": 7},
        lambda x: {**x, "markets": [{**market("T0.2"), "event_ticker": "OTHER"}]},
        lambda x: {**x, "markets": []},
        lambda x: {**x, "markets": [market("T0.2")] * 101},
    ],
)
def test_invalid_page_shape_never_publishes_success(mutate) -> None:
    def change(path: str, response: dict[str, object]) -> dict[str, object]:
        if "&cursor=" in path:
            return response
        if "/markets?" in path:
            return envelope(path, mutate(body(path)))
        return response

    with pytest.raises(P12ContractIdentityError):
        capture(change=change)


def test_page_bound_requires_terminal_cursor() -> None:
    with pytest.raises(P12ContractIdentityError, match="not exhausted"):
        capture(max_pages=1)


def test_repeated_cursor_rejected() -> None:
    def change(path: str, response: dict[str, object]) -> dict[str, object]:
        if "&cursor=" in path:
            return envelope(path, {"markets": [market("T0.3")], "cursor": "next-page"})
        return response

    with pytest.raises(P12ContractIdentityError, match="cursor"):
        capture(change=change)


def test_duplicate_sibling_across_pages_rejected() -> None:
    def change(path: str, response: dict[str, object]) -> dict[str, object]:
        if "&cursor=" in path:
            return envelope(path, {"markets": [market("T0.2")], "cursor": ""})
        return response

    with pytest.raises(P12ContractIdentityError, match="duplicate"):
        capture(change=change)


@pytest.mark.parametrize("field,wrong", [("body_sha256", "0" * 64), ("bytes", 999), ("status", 403)])
def test_tampered_envelope_rejected(field: str, wrong: object) -> None:
    def change(path: str, response: dict[str, object]) -> dict[str, object]:
        if "/events/" in path:
            return {**response, field: wrong}
        return response

    with pytest.raises(P12ContractIdentityError):
        capture(change=change)


def test_unknown_rule_fields_or_source_remain_not_ready() -> None:
    def change(path: str, response: dict[str, object]) -> dict[str, object]:
        payload = body(path)
        if "/events/" in path:
            payload["event"]["settlement_sources"] = []
        if "/markets?" in path and "&cursor=" not in path:
            payload["markets"][0].pop("rules_primary")
            payload["markets"][0]["strike_type"] = "not-a-known-comparator"
        return envelope(path, payload)

    record, _ = capture(change=change)
    assert record["metadata_field_coverage"] == "UNKNOWN"
    assert record["metadata_gaps"]["__event__"] == ["settlement_sources_unverified"]
    assert "primary_rule_unknown" in record["metadata_gaps"][EVENT + "-T0.2"]
    assert "comparator_unknown" in record["metadata_gaps"][EVENT + "-T0.2"]
    assert record["readiness"] == "UNKNOWN_NOT_READY"


def test_missing_cpi_binding_rejected() -> None:
    def change(path: str, response: dict[str, object]) -> dict[str, object]:
        if "/events/" in path:
            return envelope(path, {"event": {"ticker": EVENT, "series_ticker": "WRONG"}})
        return response

    with pytest.raises(P12ContractIdentityError, match="binding"):
        capture(change=change)


def test_invalid_ticker_and_page_bound_rejected_before_transport() -> None:
    invoked: list[str] = []

    def forbidden(path: str) -> dict[str, object]:
        invoked.append(path)
        raise AssertionError("must not call transport")

    for name, limit in [(EVENT + "/..", 8), (EVENT, 0), (EVENT, 9)]:
        with pytest.raises(P12ContractIdentityError):
            capture_p12_kalshi_contract_identity(name, getter=forbidden, max_pages=limit)
    assert not invoked


def test_source_error_cannot_call_bls_reuters_or_create_artifacts() -> None:
    def failure(path: str) -> dict[str, object]:
        raise public_read.PublicReadFailure("synthetic 429")

    with pytest.raises(P12ContractIdentityError, match="raw response"):
        capture_p12_kalshi_contract_identity(EVENT, getter=failure, clock=lambda: NOW)
