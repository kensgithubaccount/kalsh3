from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import pytest

from scripts import m27e_public_read_acceptance as public_reads
from services.market_universe import m27e_public_acceptance as acceptance
from services.market_universe.public_read import BASE, HOST


def _query(path: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(path).query)


def _response(path: str, payload: dict[str, object]) -> dict[str, object]:
    body = json.dumps(payload, sort_keys=True).encode()
    return {
        "path": path,
        "observed_at": datetime(2026, 8, 20, 3, 0, tzinfo=UTC).isoformat(),
        "status": 200,
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "raw_body_b64": base64.b64encode(body).decode("ascii"),
        "bytes": len(body),
        "classification": "SUCCESS",
        "payload": payload,
    }


def _acceptance_with_rows(rows: list[object]) -> dict[str, object]:
    page_path = BASE + "/markets?series_ticker=KXHIGHCHI&limit=1000"
    observed_at = datetime(2026, 8, 20, 3, 0, tzinfo=UTC).isoformat()
    return {
        "schema": acceptance.SCHEMA,
        "host": "https://" + HOST,
        "started_at": observed_at,
        "exchange_status": _response(BASE + "/exchange/status", {"active": True}),
        "series": _response(BASE + "/series/KXHIGHCHI", {"series": {"ticker": "KXHIGHCHI"}}),
        "markets": {
            "classification": "SUCCESS",
            "pagination_complete": True,
            "pages": [_response(page_path, {"markets": rows, "cursor": ""})],
        },
    }


def _active_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "ticker": "KXHIGHCHI-26AUG20-T84",
        "event_ticker": "KXHIGHCHI-26AUG20",
        "status": "active",
    }
    row.update(overrides)
    return row


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


@pytest.mark.parametrize(
    "bad_row",
    [
        "garbage",
        {"ticker": "KXHIGHCHI-26AUG20-T82", "event_ticker": "KXHIGHCHI-26AUG20"},
        _active_row(status=123),
        _active_row(status=""),
    ],
    ids=["non-dict", "missing-status", "non-string-status", "empty-status"],
)
def test_unclassifiable_market_row_fails_closed(bad_row: object) -> None:
    with pytest.raises(public_reads.PublicReadFailure):
        acceptance.active_market_payloads(_acceptance_with_rows([_active_row(), bad_row]))


@pytest.mark.parametrize(
    "bad_row",
    [
        _active_row(ticker=None),
        _active_row(ticker="bad/ticker"),
        _active_row(event_ticker=None),
        _active_row(event_ticker="bad/event"),
    ],
    ids=["missing-ticker", "malformed-ticker", "missing-event", "malformed-event"],
)
def test_active_market_identity_must_be_reconstructible(bad_row: object) -> None:
    with pytest.raises(public_reads.PublicReadFailure):
        acceptance.active_market_payloads(_acceptance_with_rows([bad_row]))


def test_duplicate_active_market_ticker_fails_closed() -> None:
    with pytest.raises(public_reads.PublicReadFailure, match="appeared more than once"):
        acceptance.active_market_payloads(_acceptance_with_rows([_active_row(), _active_row()]))


def test_well_formed_inactive_market_is_filterable() -> None:
    evidence = _acceptance_with_rows(
        [_active_row(), {"ticker": "KXHIGHCHI-26AUG20-T82", "status": "finalized"}]
    )
    active = acceptance.active_market_payloads(evidence)
    assert [row["ticker"] for row in active] == ["KXHIGHCHI-26AUG20-T84"]


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
