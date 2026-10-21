"""No-network, fault-injected tests of the independent P12 Kalshi inventory lane."""

from __future__ import annotations

import base64
import json
import sys
from datetime import UTC, datetime
from hashlib import sha256

import pytest

from scripts import capture_cpi_p12_kalshi_inventory as cli
from services.forecasting.cpi_p12_kalshi_inventory import (
    P12KalshiCaptureError,
    capture_kalshi_cpi_inventory,
)
from services.market_universe import public_read

EVENT = "KXCPI-26OCT"
OBSERVED = datetime(2026, 10, 21, 16, 30, tzinfo=UTC)
COMPLETED = datetime(2026, 10, 21, 16, 31, tzinfo=UTC)


def payload_for(path: str) -> dict[str, object]:
    if "/series/" in path:
        return {"series": {"ticker": "KXCPI", "category": "Economics"}}
    if "/events/" in path:
        return {"event": {"ticker": EVENT, "series_ticker": "KXCPI"}}
    return {
        "markets": [
            {"ticker": EVENT + "-T0.2", "event_ticker": EVENT},
            {"ticker": EVENT + "-T0.3", "event_ticker": EVENT},
        ],
        "cursor": "",
    }


def envelope(path: str, payload: dict[str, object]) -> dict[str, object]:
    raw = json.dumps(payload, sort_keys=True).encode()
    return public_read._evidence_from_body(path, raw, 200, OBSERVED)


def collect_with_fake(
    *,
    mutate=None,
) -> tuple[dict[str, object], list[str]]:
    observed_paths: list[str] = []

    def getter(path: str) -> dict[str, object]:
        observed_paths.append(path)
        result = envelope(path, payload_for(path))
        if mutate is not None:
            result = mutate(path, result)
        return result

    result = capture_kalshi_cpi_inventory(
        EVENT,
        getter=getter,
        clock=lambda: COMPLETED,
    )
    return result, observed_paths


def test_three_exact_first_party_paths_and_raw_byte_hashes() -> None:
    result, observed_paths = collect_with_fake()
    assert observed_paths == [
        "/trade-api/v2/series/KXCPI",
        "/trade-api/v2/events/KXCPI-26OCT",
        "/trade-api/v2/markets?event_ticker=KXCPI-26OCT&limit=100",
    ]
    assert result["acquisition_mode"] == "INJECTED_DIAGNOSTIC_GETTER"
    assert result["stage"] == "KALSHI_CPI_INVENTORY_RAW"
    assert result["market_row_count"] == 2
    for observation in result["observations"]:
        raw = base64.b64decode(observation["raw_body_b64"], validate=True)
        assert observation["raw_body_sha256"] == sha256(raw).hexdigest()
        assert observation["byte_count"] == len(raw)
        assert observation["source_origin"] == "https://external-api.kalshi.com"
        assert observation["observed_at_utc"] == "2026-10-21T16:30:00Z"
    assert result["scientific_evidence_authorized"] is False
    assert result["phase1_economic_test_authorized"] is False
    assert result["promotion_authority"] == "NONE"
    assert result["production_influence"] == "0"
    assert result["p12_frozen"] is False
    assert result["scope"] == "RAW_INVENTORY_ONLY_NO_MARKET_QUOTES_OR_REUTERS"


@pytest.mark.parametrize(
    "broken",
    [
        {"body_sha256": "0" * 64},
        {"bytes": 7},
        {"path": "/trade-api/v2/events/WRONG"},
        {"status": 403},
        {"classification": "HTTP_OR_NETWORK_FAILURE"},
        {"raw_body_b64": "not base64!"},
        {"observed_at": "no timestamp"},
    ],
)
def test_bounded_source_envelope_fail_closed(broken: dict[str, object]) -> None:
    def mutate(path: str, response: dict[str, object]) -> dict[str, object]:
        if "/events/" in path:
            return {**response, **broken}
        return response

    with pytest.raises(P12KalshiCaptureError):
        collect_with_fake(mutate=mutate)


@pytest.mark.parametrize(
    "bad",
    ["", "KXCPI-26OCT?limit=1", "https://evil.example", "KXCPI-26OCT/../", "NOTCPI-26OCT"],
)
def test_event_ticker_is_fixed_to_cpi_series(bad: str) -> None:
    invoked: list[str] = []

    def getter(path: str) -> dict[str, object]:
        invoked.append(path)
        raise AssertionError("invalid CPI identity reached transport")

    with pytest.raises(P12KalshiCaptureError):
        capture_kalshi_cpi_inventory(bad, getter=getter)
    assert invoked == []


@pytest.mark.parametrize(
    "bad_market_body",
    [
        {"markets": [], "cursor": ""},
        {"markets": [{"ticker": EVENT + "-A", "event_ticker": "WRONG"}], "cursor": ""},
        {"markets": [{"ticker": EVENT + "-A", "event_ticker": EVENT}], "cursor": "next"},
        {
            "markets": [
                {"ticker": EVENT + "-A", "event_ticker": EVENT},
                {"ticker": EVENT + "-A", "event_ticker": EVENT},
            ],
            "cursor": "",
        },
    ],
)
def test_wrong_market_cohort_or_pagination_fails(bad_market_body: dict[str, object]) -> None:
    def mutate(path: str, response: dict[str, object]) -> dict[str, object]:
        if "/markets?" in path:
            return envelope(path, bad_market_body)
        return response

    with pytest.raises(P12KalshiCaptureError):
        collect_with_fake(mutate=mutate)


def test_wrong_event_binding_fails() -> None:
    def mutate(path: str, response: dict[str, object]) -> dict[str, object]:
        if "/events/" in path:
            return envelope(path, {"event": {"ticker": EVENT, "series_ticker": "WRONG"}})
        return response

    with pytest.raises(P12KalshiCaptureError):
        collect_with_fake(mutate=mutate)


def test_kalshi_transport_failure_stays_in_its_own_stage() -> None:
    called: list[str] = []

    def getter(path: str) -> dict[str, object]:
        called.append(path)
        raise public_read.PublicReadFailure("simulated 403; no BLS invoked")

    with pytest.raises(P12KalshiCaptureError, match="transport failed"):
        capture_kalshi_cpi_inventory(EVENT, getter=getter)
    assert called == ["/trade-api/v2/series/KXCPI"]


def test_public_transport_mode_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(public_read, "get", lambda path: envelope(path, payload_for(path)))
    result = capture_kalshi_cpi_inventory(EVENT, clock=lambda: COMPLETED)
    assert result["acquisition_mode"] == "PUBLIC_GET"
    assert result["scientific_evidence_authorized"] is False


def test_create_once_cli_and_no_overwrite(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    record, _ = collect_with_fake()
    output = tmp_path / "inventory.json"
    monkeypatch.setattr(cli, "capture_kalshi_cpi_inventory", lambda _: record)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "capture_cpi_p12_kalshi_inventory.py",
            "--event-ticker",
            EVENT,
            "--output",
            str(output),
            "--research-only",
        ],
    )
    assert cli.main() == 0
    assert json.loads(output.read_bytes())["p12_frozen"] is False
    original = output.read_bytes()
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        cli.main()
    assert output.read_bytes() == original


def test_cli_requires_explicit_research_acknowledgement(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "capture_cpi_p12_kalshi_inventory.py",
            "--event-ticker",
            EVENT,
            "--output",
            str(tmp_path / "x.json"),
        ],
    )
    with pytest.raises(SystemExit, match="--research-only"):
        cli.main()
