"""Synthetic only: P12 independent Reuters raw-source transport/authority checks."""

from __future__ import annotations

import base64
import hashlib
import http.client
import io
import json
import os
import stat
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts import capture_cpi_p12_reuters_raw as cli
from services.forecasting import cpi_p12_decoupled_collection as runtime
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


def _fake_http_response(wire: bytes, *, begin: bool = True) -> http.client.HTTPResponse:
    class Socket:
        def makefile(self, _: str):
            return io.BytesIO(wire)

    response = raw._StrictHTTPResponse(Socket())
    if begin:
        response.begin()
    return response


def _patch_chunked_transport(monkeypatch: pytest.MonkeyPatch, body: bytes) -> None:
    response = _fake_http_response(
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Type: text/html\r\n\r\n" + body
    )

    class Connection:
        def __init__(self, host: str, timeout: int, context: object):
            assert host == "www.reuters.com"
            assert timeout == 8

        def request(self, method: str, path: str, headers: dict[str, str]) -> None:
            assert method == "GET"

        def getresponse(self) -> http.client.HTTPResponse:
            return response

        def close(self) -> None:
            pass

    monkeypatch.setattr(raw.http.client, "HTTPSConnection", Connection)


@pytest.mark.parametrize(
    "bad_body",
    [
        b"5\r\nhello\r\n0\r\n",  # zero chunk without terminal CRLF
        b"5\r\nhello\r\n0\r\nETag: synthetic",  # unfinished trailer
        b"5\r\nhelloXX0\r\n\r\n",  # invalid data/chunk separator
        b"5\r\nhello\r\n0\r\nETag: synthetic\r\n",  # unfinished trailer section
        b"5\r\nhello\r\n0\r\nNo-colon\r\n\r\n",  # malformed trailer
        b"5\r\nhello\r\n0\r\nBad Name: x\r\n\r\n",
        b"5\r\nhello\r\n",  # missing terminal zero-size chunk
        b"5\nhello\r\n0\r\n\r\n",  # non-CRLF size delimiter
        b"5\r\nhell",  # incomplete data
        b"Z\r\nhello\r\n0\r\n\r\n",  # invalid size
        b"5;extension=x\r\nhello\r\n0\r\n\r\n",  # unsupported extension
        b"100000\r\n",  # size exceeds byte cap, no unbounded read
    ],
)
def test_malformed_chunked_http_is_stage_local_transport_failure(
    monkeypatch: pytest.MonkeyPatch, bad_body: bytes
) -> None:
    _patch_chunked_transport(monkeypatch, bad_body)
    with pytest.raises(raw.P12ReutersRawAcquisitionError, match="transport failure"):
        _run(source_urls=(URL1,), getter=raw._https_get)


@pytest.mark.parametrize(
    "valid_body",
    [
        b"5\r\nhello\r\n0\r\n\r\n",
        b"2\r\nhe\r\n3\r\nllo\r\n0\r\n\r\n",
        b"5\r\nhello\r\n0\r\nETag: synthetic\r\n\r\n",
    ],
)
def test_complete_chunked_http_preserves_exact_entity_bytes(
    monkeypatch: pytest.MonkeyPatch, valid_body: bytes
) -> None:
    _patch_chunked_transport(monkeypatch, valid_body)
    result = _run(source_urls=(URL1,), getter=raw._https_get)
    observed = result["observations"][0]
    assert base64.b64decode(observed["raw_body_b64"], validate=True) == b"hello"
    assert observed["raw_body_sha256"] == hashlib.sha256(b"hello").hexdigest()
    assert observed["byte_count"] == 5
    assert observed["publication_time_verified"] is False
    assert result["scientific_evidence_authorized"] is False
    assert result["p12_frozen"] is False


def test_malformed_chunked_http_cannot_create_cli_pass_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "experiment"
    _patch_chunked_transport(monkeypatch, b"5\r\nhelloXX0\r\n\r\n")
    monkeypatch.setattr(
        cli,
        "capture_reuters_raw_sources",
        lambda **kwargs: raw.capture_reuters_raw_sources(
            **kwargs, getter=raw._https_get, clock=lambda: NOW
        ),
    )
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
    assert cli.main() == 4
    paths = P12IsolationPaths(root)
    receipt = json.loads(paths.stage_receipt(P12CollectorStage.REUTERS_ACQUISITION).read_text())
    assert receipt["status"] == "FAILURE"
    assert receipt["scientific_evidence_authorized"] is False
    assert not (root / "reuters/raw_source_observations.json").exists()
    assert not paths.stage_receipt(P12CollectorStage.BLS_SCHEDULE_AUTHORITY).exists()
    assert not paths.stage_receipt(P12CollectorStage.KALSHI_MARKET_CAPTURE).exists()


@pytest.mark.parametrize(
    "headers,body",
    [
        (b"Bad header\r\nContent-Length: 100\r\n", b"short"),
        (b"Content-Length: 100\r\nBad header\r\n", b"short"),
        (b"Bad header\r\nTransfer-Encoding: chunked\r\n", b"5\r\nhello\r\n0\r\n\r\n"),
        (b"Transfer-Encoding: chunked\r\nBad header\r\n", b"5\r\nhello\r\n0\r\n\r\n"),
        (b"Bad header\r\nContent-Length: 5\r\n", b"hello"),
        (b"X-Note: synthetic\r\n folded\r\nContent-Length: 5\r\n", b"hello"),
        (b"X: x\r\rContent-Length: 100\r\n", b"short"),
        (b"X: x\r\rTransfer-Encoding: chunked\r\n", b"5\r\nhello\r\n0\r\n\r\n"),
        (b"X: x\x00\r\nContent-Length: 5\r\n", b"hello"),
        (b"X: x\x01\r\nContent-Length: 5\r\n", b"hello"),
        (b"X: x\x7f\r\nContent-Length: 5\r\n", b"hello"),
        (b"X: x\v\r\nContent-Length: 5\r\n", b"hello"),
        (b"X: x\rContent-Length: 5\r\n", b"hello"),
    ],
)
def test_malformed_http_headers_fail_closed_at_transport_and_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, headers: bytes, body: bytes
) -> None:
    wire = b"HTTP/1.1 200 OK\r\n" + headers + b"\r\n" + body

    def patch_connection() -> None:
        response = _fake_http_response(wire, begin=False)

        class Connection:
            def __init__(self, host: str, timeout: int, context: object):
                assert host == "www.reuters.com"
                assert timeout == 8

            def request(self, method: str, path: str, headers: dict[str, str]) -> None:
                assert method == "GET"

            def getresponse(self) -> http.client.HTTPResponse:
                response.begin()
                return response

            def close(self) -> None:
                pass

        monkeypatch.setattr(raw.http.client, "HTTPSConnection", Connection)

    patch_connection()
    with pytest.raises(raw.P12ReutersRawAcquisitionError, match="HTTP header"):
        raw._https_get(URL1)

    patch_connection()
    with pytest.raises(raw.P12ReutersRawAcquisitionError):
        _run(source_urls=(URL1,), getter=raw._https_get)

    # A malformed header must produce a Reuters-only FAILURE receipt,
    # not success even when the Python HTTP parser hides Content-Length.
    patch_connection()
    root = tmp_path / "experiment"
    monkeypatch.setattr(
        cli,
        "capture_reuters_raw_sources",
        lambda **kwargs: raw.capture_reuters_raw_sources(
            **kwargs, getter=raw._https_get, clock=lambda: NOW
        ),
    )
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
    assert cli.main() == 4
    paths = P12IsolationPaths(root)
    receipt = json.loads(paths.stage_receipt(P12CollectorStage.REUTERS_ACQUISITION).read_text())
    assert receipt["status"] == "FAILURE"
    assert receipt["scientific_evidence_authorized"] is False
    assert receipt["p12_frozen"] is False
    assert receipt["production_influence"] == "0"
    assert not (root / "reuters/raw_source_observations.json").exists()
    assert not paths.stage_receipt(P12CollectorStage.BLS_SCHEDULE_AUTHORITY).exists()
    assert not paths.stage_receipt(P12CollectorStage.KALSHI_MARKET_CAPTURE).exists()


def _patch_closing_transport(monkeypatch: pytest.MonkeyPatch, wire: bytes) -> None:
    """Exercise begin() cleanup and close() as a real HTTP connection does."""

    class Connection:
        def __init__(self, host: str, timeout: int, context: object) -> None:
            assert host == "www.reuters.com"
            self.response = _fake_http_response(wire, begin=False)

        def request(self, method: str, path: str, headers: dict[str, str]) -> None:
            assert method == "GET"

        def getresponse(self) -> http.client.HTTPResponse:
            try:
                self.response.begin()
            except BaseException:
                self.response.close()
                raise
            return self.response

        def close(self) -> None:
            self.response.close()

    monkeypatch.setattr(raw.http.client, "HTTPSConnection", Connection)


@pytest.mark.parametrize(
    "wire",
    [
        b"HTTP/1.1 200 OK\r\nBad header\r\nContent-Length: 100\r\n\r\nshort",
        b"HTTP/1.1 200 OK\r\nX: x\r\rContent-Length: 100\r\n\r\nshort",
        b"HTTP/1.1 200 OK\r\nX: x\x00\r\nContent-Length: 5\r\n\r\nhello",
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhelloXX0\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n",
    ],
)
def test_strict_response_close_preserves_stage_failure_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, wire: bytes
) -> None:
    _patch_closing_transport(monkeypatch, wire)
    root = tmp_path / "experiment"
    paths = P12IsolationPaths(root)
    sentinels: dict[Path, bytes] = {}
    for stage in (
        P12CollectorStage.BLS_SCHEDULE_AUTHORITY,
        P12CollectorStage.KALSHI_MARKET_CAPTURE,
    ):
        sentinel = paths.stage_receipt(stage)
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_bytes(b"preexisting-stage-sentinel\n")
        sentinels[sentinel] = sentinel.read_bytes()

    monkeypatch.setattr(
        cli,
        "capture_reuters_raw_sources",
        lambda **kwargs: raw.capture_reuters_raw_sources(
            **kwargs, getter=raw._https_get, clock=lambda: NOW
        ),
    )
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
    assert cli.main() == 4
    receipt = json.loads(paths.stage_receipt(P12CollectorStage.REUTERS_ACQUISITION).read_text())
    assert receipt["status"] == "FAILURE"
    assert receipt["scientific_evidence_authorized"] is False
    assert not (root / "reuters/raw_source_observations.json").exists()
    for sentinel, contents in sentinels.items():
        assert sentinel.read_bytes() == contents


@pytest.mark.parametrize(
    "wire",
    [
        b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello",
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n",
    ],
)
def test_strict_response_close_keeps_valid_cli_capture_working(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, wire: bytes
) -> None:
    _patch_closing_transport(monkeypatch, wire)
    root = tmp_path / "experiment"
    monkeypatch.setattr(
        cli,
        "capture_reuters_raw_sources",
        lambda **kwargs: raw.capture_reuters_raw_sources(
            **kwargs, getter=raw._https_get, clock=lambda: NOW
        ),
    )
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
    assert cli.main() == 0
    paths = P12IsolationPaths(root)
    receipt = json.loads(paths.stage_receipt(P12CollectorStage.REUTERS_ACQUISITION).read_text())
    assert receipt["status"] == "PASS"
    output = json.loads((root / "reuters/raw_source_observations.json").read_text())
    assert output["scientific_evidence_authorized"] is False
    assert output["p12_frozen"] is False
    assert output["production_influence"] == "0"
    observation = output["observations"][0]
    assert base64.b64decode(observation["raw_body_b64"], validate=True) == b"hello"
    assert observation["byte_count"] == 5
    assert observation["raw_body_sha256"] == hashlib.sha256(b"hello").hexdigest()


@pytest.mark.parametrize(
    "wire",
    [
        b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nshort",
        b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\nshort",
        b"HTTP/1.0 200 OK\r\n\r\nshort",
    ],
)
def test_unframed_http_body_fails_closed_at_transport_acquisition_and_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, wire: bytes
) -> None:
    _patch_closing_transport(monkeypatch, wire)
    with pytest.raises(raw.P12ReutersRawAcquisitionError, match="framing"):
        raw._https_get(URL1)

    _patch_closing_transport(monkeypatch, wire)
    with pytest.raises(raw.P12ReutersRawAcquisitionError, match="framing"):
        _run(source_urls=(URL1,), getter=raw._https_get)

    _patch_closing_transport(monkeypatch, wire)
    root = tmp_path / "experiment"
    paths = P12IsolationPaths(root)
    sentinels: dict[Path, bytes] = {}
    for stage in (
        P12CollectorStage.BLS_SCHEDULE_AUTHORITY,
        P12CollectorStage.KALSHI_MARKET_CAPTURE,
    ):
        sentinel = paths.stage_receipt(stage)
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_bytes(b"unchanged-independent-stage\n")
        sentinels[sentinel] = sentinel.read_bytes()

    monkeypatch.setattr(
        cli,
        "capture_reuters_raw_sources",
        lambda **kwargs: raw.capture_reuters_raw_sources(
            **kwargs, getter=raw._https_get, clock=lambda: NOW
        ),
    )
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
    assert cli.main() == 4
    receipt = json.loads(paths.stage_receipt(P12CollectorStage.REUTERS_ACQUISITION).read_text())
    assert receipt["status"] == "FAILURE"
    assert receipt["scientific_evidence_authorized"] is False
    assert receipt["p12_frozen"] is False
    assert receipt["production_influence"] == "0"
    assert not (root / "reuters/raw_source_observations.json").exists()
    for sentinel, value in sentinels.items():
        assert sentinel.read_bytes() == value


@pytest.mark.parametrize("failure_point", ["fsync", "link"])
def test_raw_artifact_persistence_error_is_atomic_stage_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure_point: str
) -> None:
    root = tmp_path / "experiment"
    paths = P12IsolationPaths(root)
    sentinels: dict[Path, bytes] = {}
    for stage in (
        P12CollectorStage.BLS_SCHEDULE_AUTHORITY,
        P12CollectorStage.KALSHI_MARKET_CAPTURE,
    ):
        sentinel = paths.stage_receipt(stage)
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_bytes(b"preexisting-independent-stage\n")
        sentinels[sentinel] = sentinel.read_bytes()

    monkeypatch.setattr(cli, "capture_reuters_raw_sources", lambda **kwargs: _run())
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

    if failure_point == "fsync":
        original_fsync = os.fsync
        calls = 0

        def fail_first_fsync(fd: int) -> None:
            nonlocal calls
            if stat.S_ISREG(os.fstat(fd).st_mode):
                calls += 1
                if calls == 1:
                    raise OSError("synthetic evidence fsync failure")
            original_fsync(fd)

        monkeypatch.setattr(os, "fsync", fail_first_fsync)
    else:
        original_link = os.link

        def fail_artifact_link(source: Path, destination: Path) -> None:
            if destination.name == "raw_source_observations.json":
                raise OSError("synthetic evidence hard-link publication failure")
            original_link(source, destination)

        monkeypatch.setattr(os, "link", fail_artifact_link)

    assert cli.main() == 4
    receipt = json.loads(paths.stage_receipt(P12CollectorStage.REUTERS_ACQUISITION).read_text())
    assert receipt["status"] == "FAILURE"
    assert receipt["scientific_evidence_authorized"] is False
    assert receipt["production_influence"] == "0"
    assert not (root / "reuters/raw_source_observations.json").exists()
    assert not list((root / "reuters").glob(".reuters-p12-*"))
    for sentinel, value in sentinels.items():
        assert sentinel.read_bytes() == value
    # A failed prospective one-shot run retains its FAILURE receipt,
    # rather than silently re-fetching after an acquisition/persistence error.
    assert cli.main() == 4


def test_success_syncs_reuters_artifact_and_stage_receipt_directories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "experiment"
    synced: list[tuple[int, int]] = []
    original_fsync = os.fsync

    def track_fsync(fd: int) -> None:
        metadata = os.fstat(fd)
        if stat.S_ISDIR(metadata.st_mode):
            synced.append((metadata.st_dev, metadata.st_ino))
        original_fsync(fd)

    monkeypatch.setattr(os, "fsync", track_fsync)
    monkeypatch.setattr(cli, "capture_reuters_raw_sources", lambda **kwargs: _run())
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

    assert cli.main() == 0
    paths = P12IsolationPaths(root)
    assert (root / "reuters/raw_source_observations.json").is_file()
    assert paths.stage_receipt(P12CollectorStage.REUTERS_ACQUISITION).is_file()
    for directory in (root / "reuters", root / "orchestration" / "stages"):
        metadata = directory.stat()
        assert (metadata.st_dev, metadata.st_ino) in synced


@pytest.mark.parametrize("failure_point", ["directory_fsync", "temporary_cleanup"])
def test_post_link_failure_rolls_back_without_false_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure_point: str
) -> None:
    root = tmp_path / "experiment"
    artifact_directory = root / "reuters"
    artifact_directory.mkdir(parents=True)
    paths = P12IsolationPaths(root)
    sentinels: dict[Path, bytes] = {}
    for stage in (
        P12CollectorStage.BLS_SCHEDULE_AUTHORITY,
        P12CollectorStage.KALSHI_MARKET_CAPTURE,
    ):
        sentinel = paths.stage_receipt(stage)
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_bytes(b"isolated-stage-sentinel\n")
        sentinels[sentinel] = sentinel.read_bytes()

    monkeypatch.setattr(cli, "capture_reuters_raw_sources", lambda **kwargs: _run())
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
    if failure_point == "directory_fsync":
        original_fsync = os.fsync
        directory_metadata = artifact_directory.stat()
        failures = 0

        def fail_artifact_dir_fsync(fd: int) -> None:
            nonlocal failures
            metadata = os.fstat(fd)
            if (
                stat.S_ISDIR(metadata.st_mode)
                and (metadata.st_dev, metadata.st_ino)
                == (directory_metadata.st_dev, directory_metadata.st_ino)
                and failures == 0
            ):
                failures += 1
                raise OSError("synthetic Reuters directory fsync failure")
            original_fsync(fd)

        monkeypatch.setattr(os, "fsync", fail_artifact_dir_fsync)
    else:
        original_unlink = runtime.Path.unlink
        failures = 0

        def fail_temporary_unlink(path: Path, missing_ok: bool = False) -> None:
            nonlocal failures
            if path.name.startswith(".p12-durable-") and failures == 0:
                failures += 1
                raise OSError("synthetic temporary unlink failure")
            original_unlink(path, missing_ok=missing_ok)

        monkeypatch.setattr(runtime.Path, "unlink", fail_temporary_unlink)

    assert cli.main() == 4
    receipt = json.loads(paths.stage_receipt(P12CollectorStage.REUTERS_ACQUISITION).read_text())
    assert receipt["status"] == "FAILURE"
    assert receipt["scientific_evidence_authorized"] is False
    assert not (artifact_directory / "raw_source_observations.json").exists()
    assert not list(artifact_directory.glob(".p12-durable-*"))
    for sentinel, data in sentinels.items():
        assert sentinel.read_bytes() == data


def test_receipt_dir_sync_failure_cannot_pass_or_refetch_orphan_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "experiment"
    paths = P12IsolationPaths(root)
    stage_receipt = paths.stage_receipt(P12CollectorStage.REUTERS_ACQUISITION)
    stage_receipt.parent.mkdir(parents=True)
    receipt_directory_metadata = stage_receipt.parent.stat()
    capture_count = 0

    def capture_once(**kwargs: object) -> dict[str, object]:
        nonlocal capture_count
        capture_count += 1
        return _run()

    monkeypatch.setattr(cli, "capture_reuters_raw_sources", capture_once)
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

    original_fsync = os.fsync
    sync_failures = 0

    def fail_receipt_sync_once(fd: int) -> None:
        nonlocal sync_failures
        metadata = os.fstat(fd)
        if (
            stat.S_ISDIR(metadata.st_mode)
            and (metadata.st_dev, metadata.st_ino)
            == (receipt_directory_metadata.st_dev, receipt_directory_metadata.st_ino)
            and sync_failures == 0
        ):
            sync_failures += 1
            raise OSError("synthetic stage receipt directory sync failure")
        original_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_receipt_sync_once)
    assert cli.main() == 4
    artifact = root / "reuters" / "raw_source_observations.json"
    assert artifact.is_file()
    assert not stage_receipt.exists()
    assert sync_failures == 1
    assert capture_count == 1

    # Recovery is fail-closed: do not acquire a second time when a complete
    # artifact exists but its prior PASS receipt was not durably committed.
    assert cli.main() == 4
    assert capture_count == 1
    assert not stage_receipt.exists()
    assert runtime._pending_receipt_path(stage_receipt).is_file()
    assert artifact.is_file()
    assert not list(stage_receipt.parent.glob(".p12-durable-*"))


@pytest.mark.parametrize("failure_point", ["directory_sync", "final_file_sync"])
def test_failed_receipt_rollback_cannot_promote_provisional_pass_on_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure_point: str
) -> None:
    root = tmp_path / "experiment"
    paths = P12IsolationPaths(root)
    stage_receipt = paths.stage_receipt(P12CollectorStage.REUTERS_ACQUISITION)
    stage_receipt.parent.mkdir(parents=True)
    stage_dir_meta = stage_receipt.parent.stat()
    called = 0

    def capture_once(**kwargs: object) -> dict[str, object]:
        nonlocal called
        called += 1
        return _run()

    monkeypatch.setattr(cli, "capture_reuters_raw_sources", capture_once)
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

    original_fsync = os.fsync
    original_unlink = Path.unlink
    armed = True
    sync_failures = 0
    directory_sync_calls = 0
    unlink_failures = 0

    def fail_receipt_sync(fd: int) -> None:
        nonlocal sync_failures, directory_sync_calls
        metadata = os.fstat(fd)
        receipt_file_sync = (
            failure_point == "final_file_sync"
            and stat.S_ISREG(metadata.st_mode)
            and stage_receipt.exists()
            and metadata.st_ino == stage_receipt.stat().st_ino
            and metadata.st_dev == stage_receipt.stat().st_dev
        )
        receipt_directory_sync = (
            failure_point == "directory_sync"
            and stat.S_ISDIR(metadata.st_mode)
            and (metadata.st_dev, metadata.st_ino) == (stage_dir_meta.st_dev, stage_dir_meta.st_ino)
        )
        if armed and receipt_directory_sync:
            directory_sync_calls += 1
        if (
            armed
            and sync_failures == 0
            and (receipt_file_sync or (receipt_directory_sync and directory_sync_calls == 2))
        ):
            sync_failures += 1
            raise OSError("synthetic receipt durability failure")
        original_fsync(fd)

    def fail_rollback_unlink(path: Path, missing_ok: bool = False) -> None:
        nonlocal unlink_failures
        if armed and unlink_failures == 0 and path == stage_receipt:
            unlink_failures += 1
            raise OSError("synthetic receipt rollback unlink failure")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(os, "fsync", fail_receipt_sync)
    monkeypatch.setattr(Path, "unlink", fail_rollback_unlink)

    assert cli.main() == 4
    assert sync_failures == 1
    assert unlink_failures == 1
    assert called == 1
    assert stage_receipt.is_file()
    assert runtime._pending_receipt_path(stage_receipt).is_file()
    provisional = stage_receipt.read_bytes()
    assert provisional
    assert set(provisional) == {0}
    assert not list(stage_receipt.parent.glob(".p12-durable-*"))
    assert (root / "reuters/raw_source_observations.json").is_file()

    armed = False
    # This is the key counterexample: surviving uncertain receipt MUST NOT
    # become operational PASS on the next process invocation.
    assert cli.main() == 4
    assert called == 1
    assert stage_receipt.read_bytes() == provisional


def test_final_fsync_poison_reopen_denied_and_concurrent_reader_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "experiment"
    paths = P12IsolationPaths(root)
    receipt = paths.stage_receipt(P12CollectorStage.REUTERS_ACQUISITION)
    receipt.parent.mkdir(parents=True)
    captures = 0

    def capture_once(**kwargs: object) -> dict[str, object]:
        nonlocal captures
        captures += 1
        return _run()

    monkeypatch.setattr(cli, "capture_reuters_raw_sources", capture_once)
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

    original_fsync = os.fsync
    original_unlink = Path.unlink
    original_open = Path.open
    opens = 0
    failed_fsync = False
    armed = True

    def failed_final_fsync(fd: int) -> None:
        nonlocal failed_fsync
        metadata = os.fstat(fd)
        if (
            armed
            and not failed_fsync
            and receipt.is_file()
            and stat.S_ISREG(metadata.st_mode)
            and (metadata.st_dev, metadata.st_ino) == (receipt.stat().st_dev, receipt.stat().st_ino)
        ):
            failed_fsync = True
            # Simulate an independent reader while final JSON is entirely
            # visible but its fsync is still outstanding.
            assert receipt.read_bytes().startswith(b"{")
            with pytest.raises(runtime.P12IsolationAuthorityError, match="uncommitted"):
                runtime.validate_stage_receipt(paths, P12CollectorStage.REUTERS_ACQUISITION)
            raise OSError("synthetic unsynced final receipt")
        original_fsync(fd)

    def failed_rollback_unlink(path: Path, missing_ok: bool = False) -> None:
        if armed and path == receipt:
            raise PermissionError("synthetic unlink denied")
        original_unlink(path, missing_ok=missing_ok)

    def failed_poison_open(path: Path, *args: object, **kwargs: object):
        nonlocal opens
        if armed and path == receipt and args and args[0] == "r+b":
            opens += 1
            if opens == 2:
                raise PermissionError("synthetic poison reopen denied")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "fsync", failed_final_fsync)
    monkeypatch.setattr(Path, "unlink", failed_rollback_unlink)
    monkeypatch.setattr(Path, "open", failed_poison_open)

    assert cli.main() == 4
    assert failed_fsync
    assert opens == 2
    assert captures == 1
    assert receipt.is_file()
    assert receipt.read_bytes().startswith(b"{")
    assert runtime._pending_receipt_path(receipt).is_file()
    armed = False
    assert cli.main() == 4
    assert captures == 1
    with pytest.raises(runtime.P12IsolationAuthorityError, match="uncommitted"):
        runtime.validate_stage_receipt(paths, P12CollectorStage.REUTERS_ACQUISITION)


@pytest.mark.parametrize(
    "extra_headers,body",
    [
        ("Content-Length: 22\r\n", b"too short"),
        ("Content-Length: 5\r\nContent-Length: 5\r\n", b"12345"),
        ("Content-Length: 5\r\nTransfer-Encoding: chunked\r\n", b"12345"),
        ("Content-Length: 1x\r\n", b"12345"),
        ("Transfer-Encoding: gzip\r\n", b"unrecognized encoding"),
        ("Transfer-Encoding: chunked, identity\r\n", b"5\r\nhello\r\n0\r\n\r\n"),
        (
            "Transfer-Encoding: chunked\r\nTransfer-Encoding: chunked\r\n",
            b"5\r\nhello\r\n0\r\n\r\n",
        ),
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


def test_truncated_chunked_http_body_is_a_stage_local_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire = (
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Type: text/html\r\n\r\n5\r\nabc"
    )
    response = _fake_http_response(wire)

    class Connection:
        def __init__(self, host: str, timeout: int, context: object):
            assert host == "www.reuters.com"

        def request(self, method: str, path: str, headers: dict[str, str]) -> None:
            assert method == "GET"

        def getresponse(self) -> http.client.HTTPResponse:
            return response

        def close(self) -> None:
            pass

    monkeypatch.setattr(raw.http.client, "HTTPSConnection", Connection)
    with pytest.raises(raw.P12ReutersRawAcquisitionError, match="transport failure"):
        _run(source_urls=(URL1,), getter=raw._https_get)


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
