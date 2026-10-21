"""No-network tests for independent Kalshi P12 raw market snapshot collection."""

from __future__ import annotations

import base64
import json
import sys
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from scripts import capture_cpi_p12_kalshi_snapshots as cli
from services.forecasting import cpi_p12_raw_market_snapshots as snapshots
from services.market_universe import public_read

NOW = datetime(2026, 10, 21, 16, 30, tzinfo=UTC)
START = NOW - timedelta(minutes=1)
DEADLINE = NOW + timedelta(minutes=1)
EVENT = "KXCPI-26OCT"
TICKERS = (EVENT + "-T0.2", EVENT + "-T0.3")


def envelope(path: str, *, event: str = EVENT, ticker: str | None = None) -> dict[str, object]:
    market_ticker = ticker or path.rsplit("/", 1)[-1]
    body = json.dumps(
        {"market": {"ticker": market_ticker, "event_ticker": event, "yes_ask_dollars": "0.45"}},
        sort_keys=True,
    ).encode()
    return public_read._evidence_from_body(path, body, 200, NOW)


def capture(*, getter=None, clock=None, tickers=TICKERS):
    return snapshots.capture_kalshi_raw_market_snapshots(
        event_ticker=EVENT,
        market_tickers=tickers,
        window_start=START,
        deadline=DEADLINE,
        getter=getter or envelope,
        clock=clock or (lambda: NOW),
    )


def test_raw_snapshots_preserve_exact_bytes_and_source_identity() -> None:
    called: list[str] = []

    def getter(path: str) -> dict[str, object]:
        called.append(path)
        return envelope(path)

    record = capture(getter=getter)
    assert called == [
        "/trade-api/v2/markets/KXCPI-26OCT-T0.2",
        "/trade-api/v2/markets/KXCPI-26OCT-T0.3",
    ]
    assert record["stage"] == "KALSHI_MARKET_CAPTURE"
    assert record["market_count"] == 2
    assert record["cohort_authority"] == "CALLER_PROVIDED_NOT_FROZEN"
    assert record["acquisition_mode"] == "INJECTED_TEST_GETTER"
    assert record["scientific_evidence_authorized"] is False
    assert record["p12_frozen"] is False
    assert record["promotion_authority"] == "NONE"
    for obs in record["observations"]:
        raw = base64.b64decode(obs["raw_body_b64"], validate=True)
        assert sha256(raw).hexdigest() == obs["raw_body_sha256"]
        assert len(raw) == obs["byte_count"]
        assert obs["source_origin"] == "https://external-api.kalshi.com"


@pytest.mark.parametrize(
    "tickers",
    [
        (),
        (TICKERS[0], TICKERS[0]),
        ("WRONG-EVENT-T0.3",),
        (EVENT + "-T0.1?foo=x",),
        tuple(EVENT + f"-T{i}" for i in range(33)),
    ],
)
def test_invalid_cohort_cannot_invoke_network(tickers: tuple[str, ...]) -> None:
    calls: list[str] = []

    def getter(path: str) -> dict[str, object]:
        calls.append(path)
        return envelope(path)

    with pytest.raises(snapshots.P12SnapshotCaptureError):
        capture(getter=getter, tickers=tickers)
    assert not calls


@pytest.mark.parametrize("mode", ["sha", "status", "market", "event", "timestamp"])
def test_tampered_response_fails_closed(mode: str) -> None:
    def getter(path: str) -> dict[str, object]:
        record = envelope(path)
        if mode == "sha":
            record["body_sha256"] = "0" * 64
        elif mode == "status":
            record["status"] = 403
        elif mode == "market":
            return envelope(path, ticker="KXCPI-26OCT-WRONG")
        elif mode == "event":
            return envelope(path, event="KXCPI-OTHER")
        else:
            record["observed_at"] = (NOW - timedelta(days=1)).isoformat()
        return record

    with pytest.raises(snapshots.P12SnapshotCaptureError):
        capture(getter=getter)


def test_request_completion_after_deadline_is_rejected() -> None:
    calls = 0

    def clock() -> datetime:
        nonlocal calls
        calls += 1
        return DEADLINE if calls >= 3 else NOW

    with pytest.raises(snapshots.P12SnapshotCaptureError, match="prospective window"):
        capture(clock=clock)


def test_network_failure_fails_only_kalshi_stage(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    def failure(path: str) -> dict[str, object]:
        raise public_read.PublicReadFailure("simulated source HTTP 403")

    monkeypatch.setattr(public_read, "get", failure)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "capture_cpi_p12_kalshi_snapshots.py",
            "--root",
            str(tmp_path / "run"),
            "--event-ticker",
            EVENT,
            "--market-ticker",
            TICKERS[0],
            "--window-start",
            START.isoformat(),
            "--deadline",
            DEADLINE.isoformat(),
            "--research-only",
        ],
    )
    # Production clock is outside the 2026-10-21 synthetic test window.
    # Force a transport attempt with a caller-injected test clock.
    real = cli.capture_kalshi_raw_market_snapshots

    def with_clock(**kwargs):
        return real(**kwargs, clock=lambda: NOW)

    monkeypatch.setattr(cli, "capture_kalshi_raw_market_snapshots", with_clock)
    assert cli.main() == 4
    receipt = json.loads((tmp_path / "run/orchestration/stages/kalshi_market_capture.json").read_text())
    assert receipt["status"] == "FAILURE"
    assert receipt["scientific_evidence_authorized"] is False
    assert not (tmp_path / "run/kalshi/raw_market_snapshots.json").exists()
    assert not (tmp_path / "run/orchestration/stages/bls_schedule_authority.json").exists()
    assert not (tmp_path / "run/orchestration/stages/reuters_acquisition.json").exists()


def test_cli_requires_research_only(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["capture_cpi_p12_kalshi_snapshots.py", "--root", str(tmp_path), "--event-ticker", EVENT],
    )
    with pytest.raises(SystemExit):
        cli.main()
