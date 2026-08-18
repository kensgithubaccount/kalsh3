from __future__ import annotations

import pytest

from scripts import m27e_public_read_acceptance as public_reads


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
