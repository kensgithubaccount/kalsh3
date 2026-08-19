"""Gemini M27I delta repair section 14: candidate-exposure pagination coverage.

M27I's own review already ACCEPTED ``candidate_exposure_check.py``'s pagination design; this
file adds the missing test coverage the delta repair called out, without changing that module's
source at all.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from services.kalshi_account_gateway.client import HttpResponse, KalshiAccountClient
from services.supervised_canary.candidate_exposure_check import check_candidate_market_exposure

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


class FakeSigner:
    def __init__(self, key_id: str, private_key_pem: bytes) -> None:
        self.key_id = key_id

    def headers(self, timestamp_ms: int, method: str, request_target: str) -> dict[str, str]:
        return {"synthetic-auth": f"{self.key_id}:{timestamp_ms}:{method}:{request_target}"}


class SequencedTransport:
    """Fake account-read transport; overrides key by path substring, popped in call order."""

    def __init__(self, overrides: dict[str, list[HttpResponse | Exception]] | None = None) -> None:
        self.overrides = overrides or {}
        self.paths: list[str] = []

    def get(self, path: str, headers: Mapping[str, str], *, timeout_seconds: float) -> HttpResponse:
        self.paths.append(path)
        for needle, values in self.overrides.items():
            if needle in path and values:
                value = values.pop(0)
                if isinstance(value, Exception):
                    raise value
                return value
        field = "market_positions" if "positions" in path else "orders"
        return HttpResponse(200, {field: [], "cursor": ""})


def _client(overrides: dict[str, list[HttpResponse | Exception]]) -> KalshiAccountClient:
    return KalshiAccountClient(
        FakeSigner("candidate", b"unused"),
        SequencedTransport(overrides),
        clock_ms=lambda: 1_700_000_000_000,
        max_retries=0,
    )


def test_matching_open_order_on_page_two() -> None:
    client = _client(
        {
            "orders?": [
                HttpResponse(200, {"orders": [], "cursor": "c1"}),
                HttpResponse(
                    200,
                    {"orders": [{"ticker": "M", "status": "resting"}], "cursor": ""},
                ),
            ],
            "positions?": [HttpResponse(200, {"market_positions": [], "cursor": ""})],
        }
    )
    evidence = check_candidate_market_exposure(client=client, market_ticker="M", clock=lambda: NOW)
    assert evidence.succeeded
    assert evidence.open_order_count == 1


def test_matching_position_on_page_two() -> None:
    client = _client(
        {
            "orders?": [HttpResponse(200, {"orders": [], "cursor": ""})],
            "positions?": [
                HttpResponse(200, {"market_positions": [], "cursor": "c1"}),
                HttpResponse(
                    200,
                    {"market_positions": [{"ticker": "M", "position": 3}], "cursor": ""},
                ),
            ],
        }
    )
    evidence = check_candidate_market_exposure(client=client, market_ticker="M", clock=lambda: NOW)
    assert evidence.succeeded
    assert evidence.position_nonzero is True


def test_resting_order_counts_but_filled_order_does_not() -> None:
    client = _client(
        {
            "orders?": [
                HttpResponse(
                    200,
                    {
                        "orders": [
                            {"ticker": "M", "status": "resting"},
                            {"ticker": "M", "status": "filled"},
                        ],
                        "cursor": "",
                    },
                )
            ],
            "positions?": [HttpResponse(200, {"market_positions": [], "cursor": ""})],
        }
    )
    evidence = check_candidate_market_exposure(client=client, market_ticker="M", clock=lambda: NOW)
    assert evidence.succeeded
    assert evidence.open_order_count == 1


def test_truncated_pagination_fails_closed() -> None:
    client = _client(
        {
            # A page that advances the cursor but omits the "orders" field entirely is
            # malformed/truncated -- KalshiAccountClient must raise, not silently short-read.
            "orders?": [HttpResponse(200, {"cursor": ""})],
            "positions?": [HttpResponse(200, {"market_positions": [], "cursor": ""})],
        }
    )
    evidence = check_candidate_market_exposure(client=client, market_ticker="M", clock=lambda: NOW)
    assert not evidence.succeeded
    assert evidence.classification == "BLOCKED"
    assert evidence.reason == "orders read did not complete"


def test_malformed_cursor_fails_closed() -> None:
    client = _client(
        {
            "orders?": [HttpResponse(200, {"orders": [], "cursor": 12345})],
            "positions?": [HttpResponse(200, {"market_positions": [], "cursor": ""})],
        }
    )
    evidence = check_candidate_market_exposure(client=client, market_ticker="M", clock=lambda: NOW)
    assert not evidence.succeeded
    assert evidence.classification == "BLOCKED"


def test_repeated_cursor_pagination_loop_fails_closed() -> None:
    client = _client(
        {
            "orders?": [
                HttpResponse(200, {"orders": [], "cursor": "loop"}),
                HttpResponse(200, {"orders": [], "cursor": "loop"}),
            ],
            "positions?": [HttpResponse(200, {"market_positions": [], "cursor": ""})],
        }
    )
    evidence = check_candidate_market_exposure(client=client, market_ticker="M", clock=lambda: NOW)
    assert not evidence.succeeded
    assert evidence.classification == "BLOCKED"


def test_second_page_http_failure_fails_closed() -> None:
    client = _client(
        {
            "orders?": [
                HttpResponse(200, {"orders": [], "cursor": "c1"}),
                HttpResponse(401, {}),
            ],
            "positions?": [HttpResponse(200, {"market_positions": [], "cursor": ""})],
        }
    )
    evidence = check_candidate_market_exposure(client=client, market_ticker="M", clock=lambda: NOW)
    assert not evidence.succeeded
    assert evidence.classification == "BLOCKED"
    assert evidence.reason == "orders read did not complete"


def test_unexpected_order_row_shape_fails_closed() -> None:
    client = _client(
        {
            "orders?": [HttpResponse(200, {"orders": [{"status": "resting"}], "cursor": ""})],
            "positions?": [HttpResponse(200, {"market_positions": [], "cursor": ""})],
        }
    )
    evidence = check_candidate_market_exposure(client=client, market_ticker="M", clock=lambda: NOW)
    assert not evidence.succeeded
    assert evidence.classification == "BLOCKED"


def test_unexpected_position_row_shape_fails_closed() -> None:
    client = _client(
        {
            "orders?": [HttpResponse(200, {"orders": [], "cursor": ""})],
            "positions?": [
                HttpResponse(200, {"market_positions": [{"ticker": "M"}], "cursor": ""})
            ],
        }
    )
    evidence = check_candidate_market_exposure(client=client, market_ticker="M", clock=lambda: NOW)
    assert not evidence.succeeded
    assert evidence.classification == "BLOCKED"
