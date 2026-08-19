from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from scripts import m27e_public_read_acceptance as public_reads


def _query(path: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(path).query)


def test_http_failure_is_not_empty_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        public_reads,
        "get",
        lambda path: {"classification": "HTTP_OR_NETWORK_FAILURE", "status": 503},
    )
    result = public_reads.paged_markets()
    assert result["classification"] == "HTTP_OR_NETWORK_FAILURE"
    assert result["pagination_complete"] is False
    assert "market_count" not in result


def test_repeated_cursor_is_incomplete_not_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        public_reads,
        "get",
        lambda path: {
            "classification": "SUCCESS",
            "payload": {"markets": [], "cursor": "same"},
        },
    )
    with pytest.raises(public_reads.PublicReadFailure, match="repeated cursor"):
        public_reads.paged_markets()


def test_missing_markets_array_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        public_reads,
        "get",
        lambda path: {"classification": "SUCCESS", "payload": {"cursor": ""}},
    )
    with pytest.raises(public_reads.PublicReadFailure, match="markets array missing"):
        public_reads.paged_markets()


def test_query_uses_kxhighchi_series(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[str] = []

    def fake_get(path: str) -> dict[str, object]:
        captured.append(path)
        return {"classification": "SUCCESS", "payload": {"markets": [], "cursor": ""}}

    monkeypatch.setattr(public_reads, "get", fake_get)
    public_reads.paged_markets()
    assert len(captured) == 1
    assert _query(captured[0])["series_ticker"] == ["KXHIGHCHI"]


def test_query_never_uses_stale_climdw(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[str] = []

    def fake_get(path: str) -> dict[str, object]:
        captured.append(path)
        return {"classification": "SUCCESS", "payload": {"markets": [], "cursor": ""}}

    monkeypatch.setattr(public_reads, "get", fake_get)
    public_reads.paged_markets()
    assert "CLIMDW" not in captured[0]


def test_query_never_assumes_status_open_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    """The status=open API filter form has NOT been live-validated -- discovery must always
    fetch the complete unfiltered page set and derive market_count client-side."""
    captured: list[str] = []

    def fake_get(path: str) -> dict[str, object]:
        captured.append(path)
        return {"classification": "SUCCESS", "payload": {"markets": [], "cursor": ""}}

    monkeypatch.setattr(public_reads, "get", fake_get)
    public_reads.paged_markets()
    assert "status" not in _query(captured[0])


def test_cursor_page_reuses_kxhighchi_series(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        {
            "classification": "SUCCESS",
            "payload": {"markets": [{"ticker": "A", "status": "active"}], "cursor": "next"},
        },
        {
            "classification": "SUCCESS",
            "payload": {"markets": [{"ticker": "B", "status": "active"}], "cursor": ""},
        },
    ]
    captured: list[str] = []

    def fake_get(path: str) -> dict[str, object]:
        captured.append(path)
        return pages[len(captured) - 1]

    monkeypatch.setattr(public_reads, "get", fake_get)
    result = public_reads.paged_markets()
    assert len(captured) == 2
    for path in captured:
        query = _query(path)
        assert query["series_ticker"] == ["KXHIGHCHI"]
        assert "status" not in query
    assert _query(captured[1])["cursor"] == ["next"]
    assert result["market_count"] == 2


def test_active_markets_are_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        public_reads,
        "get",
        lambda path: {
            "classification": "SUCCESS",
            "payload": {
                "markets": [
                    {"ticker": "A", "status": "active"},
                    {"ticker": "B", "status": "active"},
                ],
                "cursor": "",
            },
        },
    )
    result = public_reads.paged_markets()
    assert result["classification"] == "SUCCESS"
    assert result["pagination_complete"] is True
    assert result["market_count"] == 2
    assert result["total_returned"] == 2


def test_finalized_markets_are_not_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        public_reads,
        "get",
        lambda path: {
            "classification": "SUCCESS",
            "payload": {
                "markets": [
                    {"ticker": "A", "status": "finalized"},
                    {"ticker": "B", "status": "finalized"},
                ],
                "cursor": "",
            },
        },
    )
    result = public_reads.paged_markets()
    assert result["classification"] == "SUCCESS"
    assert result["market_count"] == 0
    assert result["total_returned"] == 2


def test_mixed_active_and_finalized_pagination_counts_correctly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [
        {
            "classification": "SUCCESS",
            "payload": {
                "markets": [
                    {"ticker": "A", "status": "active"},
                    {"ticker": "B", "status": "finalized"},
                ],
                "cursor": "next",
            },
        },
        {
            "classification": "SUCCESS",
            "payload": {
                "markets": [
                    {"ticker": "C", "status": "active"},
                    {"ticker": "D", "status": "finalized"},
                    {"ticker": "E", "status": "finalized"},
                ],
                "cursor": "",
            },
        },
    ]
    calls = iter(pages)
    monkeypatch.setattr(public_reads, "get", lambda path: next(calls))
    result = public_reads.paged_markets()
    assert result["classification"] == "SUCCESS"
    assert result["pagination_complete"] is True
    assert result["market_count"] == 2
    assert result["total_returned"] == 5


def test_page_raw_payload_is_preserved_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """paged_markets() must never mutate the raw page evidence it stores -- market_count is a
    derived field alongside the pages, not a rewrite of them."""
    raw_markets = [
        {"ticker": "A", "status": "active", "extra": "keep-me"},
        {"ticker": "B", "status": "finalized", "extra": "keep-me-too"},
    ]
    monkeypatch.setattr(
        public_reads,
        "get",
        lambda path: {
            "classification": "SUCCESS",
            "payload": {"markets": raw_markets, "cursor": ""},
        },
    )
    result = public_reads.paged_markets()
    pages = result["pages"]
    assert isinstance(pages, list)
    stored_markets = pages[0]["payload"]["markets"]
    assert stored_markets == raw_markets
    assert stored_markets[0]["status"] == "active"
    assert stored_markets[1]["status"] == "finalized"


def test_incomplete_pagination_never_becomes_empty_market_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        public_reads,
        "get",
        lambda path: {
            "classification": "SUCCESS",
            "payload": {"markets": [], "cursor": "same"},
        },
    )
    with pytest.raises(public_reads.PublicReadFailure):
        public_reads.paged_markets()


def test_failed_page_mid_pagination_fails_closed_not_partial_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [
        {
            "classification": "SUCCESS",
            "payload": {"markets": [{"ticker": "A", "status": "active"}], "cursor": "next"},
        },
        {"classification": "HTTP_OR_NETWORK_FAILURE", "status": 503},
    ]
    calls = iter(pages)
    monkeypatch.setattr(public_reads, "get", lambda path: next(calls))
    result = public_reads.paged_markets()
    assert result["classification"] == "HTTP_OR_NETWORK_FAILURE"
    assert result["pagination_complete"] is False
    assert "market_count" not in result
