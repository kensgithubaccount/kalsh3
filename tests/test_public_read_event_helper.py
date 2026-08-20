"""services.market_universe.public_read.get_event_with_body -- no-network tests.

Monkeypatches the module's private `_get_raw` transport primitive (never the network) so the
REAL `get_event_with_body` code runs end-to-end, exactly proving what it sends and how it fails,
without ever touching a socket. Mirrors tests/test_public_read_orderbook_helper.py's exact
pattern.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from services.market_universe import public_read

NOW = datetime(2026, 8, 20, 3, 0, 0, tzinfo=UTC)


def test_get_event_with_body_uses_exact_fixed_single_event_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_paths: list[str] = []

    def fake_get_raw(path: str) -> tuple[bytes, int, datetime]:
        seen_paths.append(path)
        body = b'{"event":{"event_ticker":"E","series_ticker":"CLIMDW","title":"t"}}'
        return body, 200, NOW

    monkeypatch.setattr(public_read, "_get_raw", fake_get_raw)
    evidence, body = public_read.get_event_with_body("E")

    assert seen_paths == [f"{public_read.BASE}/events/E"]
    assert evidence["path"] == f"{public_read.BASE}/events/E"
    assert evidence["status"] == 200
    assert evidence["classification"] == "SUCCESS"
    assert body.startswith(b"{")


def test_get_event_with_body_never_admits_a_caller_supplied_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An attempted injection through the ticker itself (extra query params, alternate path,
    path traversal) must be rejected by `_TICKER_RE` before `_get_raw` is ever called -- no
    network attempt, no alternate/unauthorized endpoint reachable through this function."""
    calls = 0

    def fake_get_raw(path: str) -> tuple[bytes, int, datetime]:
        nonlocal calls
        calls += 1
        raise AssertionError("must never be called for a malformed ticker")

    monkeypatch.setattr(public_read, "_get_raw", fake_get_raw)

    for malicious_ticker in (
        "E&tickers=OTHER",
        "E?evil=1",
        "../../secret",
        "E,OTHER",
        "E OTHER",
        "",
        "E\r\nX-Injected: 1",
        "e",  # lowercase forbidden by the exact ticker grammar
    ):
        with pytest.raises(public_read.PublicReadFailure):
            public_read.get_event_with_body(malicious_ticker)
    assert calls == 0


def test_get_event_with_body_reuses_get_raw_and_evidence_from_body_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proves no second HTTP implementation exists: the same `_get_raw`/`_evidence_from_body`
    primitives `get_market_with_body` uses are the only ones this function calls."""
    calls: list[str] = []
    real_get_raw = public_read._get_raw
    real_evidence_from_body = public_read._evidence_from_body

    def tracking_get_raw(path: str) -> tuple[bytes, int, datetime]:
        calls.append("_get_raw")
        return b'{"event":{}}', 200, NOW

    def tracking_evidence_from_body(
        path: str, body: bytes, status: int, observed_at: datetime
    ) -> dict[str, object]:
        calls.append("_evidence_from_body")
        return real_evidence_from_body(path, body, status, observed_at)

    monkeypatch.setattr(public_read, "_get_raw", tracking_get_raw)
    monkeypatch.setattr(public_read, "_evidence_from_body", tracking_evidence_from_body)
    public_read.get_event_with_body("E")
    assert calls == ["_get_raw", "_evidence_from_body"]
    assert public_read._get_raw is not real_get_raw  # confirms the monkeypatch was in effect


def test_get_event_with_body_surfaces_non_200_like_get_market_with_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get_raw(path: str) -> tuple[bytes, int, datetime]:
        return b"not found", 404, NOW

    monkeypatch.setattr(public_read, "_get_raw", fake_get_raw)
    evidence, body = public_read.get_event_with_body("E")
    assert evidence["status"] == 404
    assert evidence["classification"] == "HTTP_OR_NETWORK_FAILURE"
    assert body == b"not found"


def test_get_event_with_body_does_not_alter_market_or_orderbook_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Additive-only proof: get_market_with_body and get_orderbook_with_body still build the
    exact same paths they always did."""

    def fake_get_raw(path: str) -> tuple[bytes, int, datetime]:
        return b"{}", 200, NOW

    monkeypatch.setattr(public_read, "_get_raw", fake_get_raw)
    market_evidence, _ = public_read.get_market_with_body("M")
    orderbook_evidence, _ = public_read.get_orderbook_with_body("M")
    assert market_evidence["path"] == f"{public_read.BASE}/markets/M"
    assert orderbook_evidence["path"] == f"{public_read.BASE}/markets/orderbooks?tickers=M"
