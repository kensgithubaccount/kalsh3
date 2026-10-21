"""Synthetic only: P12 independent Reuters raw-source transport/authority checks."""

from __future__ import annotations

import base64
import hashlib
import http.client
import io
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts import capture_cpi_p12_reuters_raw as cli
from services.forecasting import cpi_p12_reuters_raw_acquisition as raw
from services.forecasting.cpi_p12_decoupled_collection import (
    P12CollectorStage,
    P12IsolationPaths,
)

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
START = NOW - timedelta(minutes=1)
DEADLINE = NOW + timedelta(minutes=1)
URL1 = "https://www.reuters.com/world/synthetic-source-example"
URL2 = "https://finance.yahoo.com/news/synthetic-reuters-example"
BYTES = b"<html><body>synthetic unrelated wire bytes only</body></html>"


def _getter(_: str) -> tuple[int, bytes, dict[str, str]]:
    return 200, BYTES, {"content-type": "text/html"}


def _run(**changes):
    args = {
        "source_urls": (URL1, URL2),
        "window_start": START,
        "deadline": DEADLINE,
        "getter": _getter,
        "clock": lambda: NOW,
    }
    args.update(changes)
    return raw.capture_reuters_raw_sources(**args)


def test_raw_bytes_and_metadata_are_preserved_without_forecast_selection() -> None:
    result = _run()
    assert result["source_count"] == 2
    assert result["transport_mode"] == "INJECTED_TEST_GETTER"
    assert result["scientific_evidence_authorized"] is False
    assert result["reuters_forecast_authorized"] is False
    assert result["p12_frozen"] is False
    assert result["promotion_authority"] == "NONE"
    assert result["production_influence"] == "0"
    assert [x["operator_group_unverified"] for x in result["observations"]] == [
        "reuters",
        "yahoo",
    ]
    for entry in result["observations"]:
        decoded = base64.b64decode(entry["raw_body_b64"], validate=True)
        assert decoded == BYTES
        assert entry["raw_body_sha256"] == hashlib.sha256(BYTES).hexdigest()
        assert entry["byte_count"] == len(BYTES)
        assert entry["publication_time_verified"] is False
        assert entry["reuters_attribution_verified"] is False
        assert entry["forecast_value_extracted"] is False


@pytest.mark.parametrize(
    "urls",
    [
        (),
        (URL1, URL1),
        ("http://www.reuters.com/example",),
        ("https://evil.example/reuters",),
        ("https://www.reuters.com.evil.example/foo",),
        ("https://www.reuters.com:444/foo",),
        ("https://alice@www.reuters.com/foo",),
        ("https://www.reuters.com/foo?token=secret",),
        ("https://www.reuters.com/../secret",),
        ("https://www.reuters.com//evil",),
        ("https://www.reuters.com/café",),
        ("https://www.reuters.com/path\x7f",),
        tuple(URL1 + str(i) for i in range(9)),
    ],
)
def test_invalid_source_list_fails_before_any_network(urls: tuple[str, ...]) -> None:
    called: list[str] = []

    def getter(url: str) -> tuple[int, bytes, dict[str, str]]:
        called.append(url)
        return _getter(url)

    with pytest.raises(raw.P12ReutersRawAcquisitionError):
        _run(source_urls=urls, getter=getter)
    assert not called


@pytest.mark.parametrize("status", [301, 302, 403, 429, 500])
def test_redirect_and_http_failures_are_nonpass(status: int) -> None:
    with pytest.raises(raw.P12ReutersRawAcquisitionError, match="HTTP 200"):
        _run(getter=lambda _: (status, BYTES, {}))


@pytest.mark.parametrize("body", [b"", b"a" * (raw.MAX_RESPONSE_BYTES + 1)])
def test_empty_and_oversize_responses_fail_closed(body: bytes) -> None:
    with pytest.raises(raw.P12ReutersRawAcquisitionError, match="size"):
        _run(getter=lambda _: (200, body, {}))


def test_invalid_headers_fail_closed() -> None:
    with pytest.raises(raw.P12ReutersRawAcquisitionError, match="header"):
        _run(getter=lambda _: (200, BYTES, {"etag": "bad\nheader"}))


def test_network_failure_isolated_to_reuters() -> None:
    def blocked(_: str):
        raise TimeoutError("synthetic Reuters timeout")

    with pytest.raises(raw.P12ReutersRawAcquisitionError, match="transport"):
        _run(getter=blocked)


def test_no_out_of_window_start() -> None:
    with pytest.raises(raw.P12ReutersRawAcquisitionError, match="outside window"):
        _run(clock=lambda: START - timedelta(seconds=1))


def test_deadline_crossed_during_request() -> None:
    calls = 0

    def clock() -> datetime:
        nonlocal calls
        calls += 1
        return DEADLINE if calls >= 3 else NOW

    with pytest.raises(raw.P12ReutersRawAcquisitionError, match="deadline"):
        _run(clock=clock)


def _fake_http_response(wire: bytes) -> http.client.HTTPResponse:
    class Socket:
        def makefile(self, _: str):
            return io.BytesIO(wire)

    response = http.client.HTTPResponse(Socket())
    response.begin()
    return response


@pytest.mark.parametrize(
    "extra_headers,body",
    [
        ("Content-Length: 22\r\n", b"too short"),
        ("Content-Length: 5\r\nContent-Length: 5\r\n", b"12345"),
        ("Content-Length: 5\r\nTransfer-Encoding: chunked\r\n", b"12345"),
        ("Content-Length: 1x\r\n", b"12345"),
    ],
)
def test_real_http_reader_rejects_incomplete_or_ambiguous_framing(
    monkeypatch: pytest.MonkeyPatch, extra_headers: str, body: bytes
) -> None:
    wire = (
        b"HTTP/1.1 200 OK\r\n"
        + extra_headers.encode("ascii")
        + b"Content-Type: text/html\r\nConnection: close\r\n\r\n"
        + body
    )
    response = _fake_http_response(wire)

    class Connection:
        def __init__(self, host: str, timeout: int, context: object):
            assert host == "www.reuters.com"
            assert timeout == 8

        def request(self, method: str, path: str, headers: dict[str, str]) -> None:
            assert method == "GET"
            assert path == "/world/synthetic-source-example"

        def getresponse(self) -> http.client.HTTPResponse:
            return response

        def close(self) -> None:
            pass

    monkeypatch.setattr(raw.http.client, "HTTPSConnection", Connection)
    with pytest.raises(raw.P12ReutersRawAcquisitionError):
        raw._https_get(URL1)


def test_clock_rollback_between_source_requests_fails_closed() -> None:
    sequence = iter(
        [
            NOW,
            NOW,
            NOW + timedelta(seconds=10),
            NOW - timedelta(seconds=1),
        ]
    )
    with pytest.raises(raw.P12ReutersRawAcquisitionError, match="clock moved backwards"):
        _run(clock=lambda: next(sequence))


def test_clock_rollback_after_last_response_fails_closed() -> None:
    sequence = iter(
        [
            NOW,
            NOW,
            NOW + timedelta(seconds=10),
            NOW + timedelta(seconds=5),
        ]
    )
    with pytest.raises(raw.P12ReutersRawAcquisitionError, match="finished outside window"):
        _run(source_urls=(URL1,), clock=lambda: next(sequence))


def test_cli_failure_receipt_is_local_to_reuters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "experiment"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "capture_cpi_p12_reuters_raw.py",
            "--root",
            str(root),
            "--source-url",
            URL1,
            "--window-start",
            START.isoformat(),
            "--deadline",
            DEADLINE.isoformat(),
            "--research-only",
        ],
    )

    def mocked_capture(**kwargs):
        return _run(getter=lambda _: (403, BYTES, {}))

    monkeypatch.setattr(cli, "capture_reuters_raw_sources", mocked_capture)
    assert cli.main() == 4
    paths = P12IsolationPaths(root)
    receipt = json.loads(paths.stage_receipt(P12CollectorStage.REUTERS_ACQUISITION).read_text())
    assert receipt["status"] == "FAILURE"
    assert receipt["scientific_evidence_authorized"] is False
    assert not (root / "reuters/raw_source_observations.json").exists()
    assert not paths.stage_receipt(P12CollectorStage.BLS_SCHEDULE_AUTHORITY).exists()
    assert not paths.stage_receipt(P12CollectorStage.KALSHI_MARKET_CAPTURE).exists()


def test_cli_success_is_raw_only_and_create_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "experiment"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "capture_cpi_p12_reuters_raw.py",
            "--root",
            str(root),
            "--source-url",
            URL1,
            "--window-start",
            START.isoformat(),
            "--deadline",
            DEADLINE.isoformat(),
            "--research-only",
        ],
    )
    monkeypatch.setattr(cli, "capture_reuters_raw_sources", lambda **kwargs: _run())
    assert cli.main() == 0
    saved = root / "reuters/raw_source_observations.json"
    assert saved.is_file()
    record = json.loads(saved.read_text())
    assert record["scientific_evidence_authorized"] is False
    assert record["source_publication_verified"] is False
    assert cli.main() == 0  # reads existing stage receipt; never re-fetches


def test_cli_requires_research_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "capture_cpi_p12_reuters_raw.py",
            "--root",
            str(tmp_path),
            "--source-url",
            URL1,
            "--window-start",
            START.isoformat(),
            "--deadline",
            DEADLINE.isoformat(),
        ],
    )
    with pytest.raises(SystemExit, match="research-only"):
        cli.main()
