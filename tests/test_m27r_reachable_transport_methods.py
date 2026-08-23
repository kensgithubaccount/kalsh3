"""Exercise the concrete read transports reachable from M27R without real network access."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.request import Request

import pytest

from services.kalshi_account_gateway import client as account_client
from services.market_universe import public_read


class _PublicResponse:
    status = 200

    def read(self, limit: int) -> bytes:
        assert limit > 2
        return b"{}"


class _PublicConnection:
    calls: list[tuple[str, str, Mapping[str, str]]] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def request(self, method: str, path: str, headers: Mapping[str, str]) -> None:
        self.calls.append((method, path, headers))

    def getresponse(self) -> _PublicResponse:
        return _PublicResponse()

    def close(self) -> None:
        pass


def test_public_m27r_transport_emits_only_get(monkeypatch: pytest.MonkeyPatch) -> None:
    _PublicConnection.calls.clear()
    monkeypatch.setattr(public_read.http.client, "HTTPSConnection", _PublicConnection)

    evidence = public_read.get(public_read.BASE + "/exchange/status")

    assert evidence["classification"] == "SUCCESS"
    assert len(_PublicConnection.calls) == 1
    method, path, headers = _PublicConnection.calls[0]
    assert method == "GET"
    assert path == public_read.BASE + "/exchange/status"
    assert "Authorization" not in headers


class _AccountHeaders:
    def get_content_type(self) -> str:
        return "application/json"

    def get(self, name: str) -> str | None:
        if name == "Content-Length":
            return "2"
        return None


class _AccountResponse:
    status = 200
    headers = _AccountHeaders()

    def __enter__(self) -> _AccountResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, limit: int) -> bytes:
        assert limit > 2
        return b"{}"


class _AccountOpener:
    def __init__(self) -> None:
        self.requests: list[Request] = []

    def open(self, request: Request, *, timeout: float) -> _AccountResponse:
        assert timeout > 0
        self.requests.append(request)
        return _AccountResponse()


def test_authenticated_m27r_transport_emits_only_get(monkeypatch: pytest.MonkeyPatch) -> None:
    opener = _AccountOpener()

    def build_opener(*_handlers: Any) -> _AccountOpener:
        return opener

    monkeypatch.setattr(account_client.urllib.request, "build_opener", build_opener)

    response = account_client.UrllibReadTransport().get(
        "/trade-api/v2/portfolio/balance",
        {"synthetic-auth-header": "test-only"},
        timeout_seconds=1,
    )

    assert response.status == 200
    assert len(opener.requests) == 1
    request = opener.requests[0]
    assert request.get_method() == "GET"
    assert request.full_url == account_client.BASE_URL + "/trade-api/v2/portfolio/balance"
