from __future__ import annotations

import base64
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from services.kalshi_account_gateway.auth import (
    AuthenticationError,
    RequestSigner,
    signature_message,
)
from services.kalshi_account_gateway.client import (
    AccountGatewayError,
    AuthenticationRejected,
    HttpResponse,
    KalshiAccountClient,
    PaginationError,
    RateLimited,
    UpstreamUnavailable,
)
from services.kalshi_account_gateway.models import SnapshotValidationError


@pytest.fixture
def private_key(tmp_path: Path) -> bytes:
    path = tmp_path / "key.pem"
    subprocess.run(
        [
            "/usr/bin/openssl",
            "genpkey",
            "-algorithm",
            "RSA",
            "-pkeyopt",
            "rsa_keygen_bits:2048",
            "-out",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    value = path.read_bytes()
    path.unlink()
    return value


def test_signature_contract_rejects_mutations_and_urls() -> None:
    assert (
        signature_message(123, "get", "/trade-api/v2/api_keys?q=x")
        == b"123GET/trade-api/v2/api_keys"
    )
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        with pytest.raises(AuthenticationError):
            signature_message(123, method, "/trade-api/v2/x")
    with pytest.raises(AuthenticationError):
        signature_message(123, "GET", "https://example.test/x")


def test_signature_is_rsa_pss_and_key_omitted_from_repr(private_key: bytes, tmp_path: Path) -> None:
    signer = RequestSigner("id", private_key)
    assert "PRIVATE" not in repr(signer)
    headers = signer.headers(123, "GET", "/trade-api/v2/api_keys")
    sig = tmp_path / "sig"
    msg = tmp_path / "msg"
    key = tmp_path / "key"
    public = tmp_path / "public"
    sig.write_bytes(base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"]))
    msg.write_bytes(b"123GET/trade-api/v2/api_keys")
    key.write_bytes(private_key)
    subprocess.run(
        ["/usr/bin/openssl", "pkey", "-in", str(key), "-pubout", "-out", str(public)],
        check=True,
        capture_output=True,
    )
    result = subprocess.run(
        [
            "/usr/bin/openssl",
            "dgst",
            "-sha256",
            "-sigopt",
            "rsa_padding_mode:pss",
            "-sigopt",
            "rsa_pss_saltlen:digest",
            "-verify",
            str(public),
            "-signature",
            str(sig),
            str(msg),
        ],
        capture_output=True,
    )
    assert result.returncode == 0


class Transport:
    def __init__(self, overrides: dict[str, list[HttpResponse | Exception]] | None = None) -> None:
        self.overrides = overrides or {}
        self.paths: list[str] = []

    def get(self, path: str, headers: Mapping[str, str], *, timeout_seconds: float) -> HttpResponse:
        self.paths.append(path)
        assert timeout_seconds == 2
        for needle, values in self.overrides.items():
            if needle in path and values:
                value = values.pop(0)
                if isinstance(value, Exception):
                    raise value
                return value
        if path.endswith("api_keys"):
            return HttpResponse(200, {"api_keys": [{"id": "reader", "scopes": ["read"]}]})
        if "balance" in path:
            return HttpResponse(
                200, {"balance_dollars": "1000.00", "portfolio_value_dollars": "1001.25"}
            )
        if "limits" in path:
            return HttpResponse(
                200,
                {
                    "usage_tier": "basic",
                    "read_refill_rate": 10,
                    "read_capacity": 20,
                    "write_refill_rate": 5,
                    "write_capacity": 10,
                },
            )
        field = (
            "market_positions"
            if "positions" in path
            else next(x for x in ("orders", "fills", "settlements") if x in path)
        )
        return HttpResponse(200, {field: [], "cursor": ""})


def client(private_key: bytes, transport: Transport, retries: int = 2) -> KalshiAccountClient:
    return KalshiAccountClient(
        RequestSigner("reader", private_key),
        transport,
        timeout_seconds=2,
        max_retries=retries,
        sleep=lambda _: None,
        clock_ms=lambda: 123,
    )


def test_complete_account_zero_snapshot_and_decimal_normalization(private_key: bytes) -> None:
    transport = Transport(
        {
            "settlements?": [
                HttpResponse(
                    200,
                    {
                        "settlements": [{"ticker": "T", "yes_total_cost_dollars": "1.230"}],
                        "cursor": "next",
                    },
                )
            ]
        }
    )
    snap = client(private_key, transport).refresh("reader")
    assert (
        snap.cash.as_tuple().exponent == -2
        and snap.settlements[0]["yes_total_cost_dollars"].as_tuple().exponent == -3
    )
    assert snap.subaccount == 0 and all(
        "subaccount=0" in path for path in transport.paths if "/portfolio/" in path
    )
    assert any("cursor=next" in path for path in transport.paths)


@pytest.mark.parametrize("scopes", [[], ["write"], ["read", "write"], None])
def test_scope_must_be_exactly_read(private_key: bytes, scopes: Any) -> None:
    transport = Transport(
        {"api_keys": [HttpResponse(200, {"api_keys": [{"id": "reader", "scopes": scopes}]})]}
    )
    with pytest.raises(AuthenticationRejected):
        client(private_key, transport).refresh("reader")


def test_page_two_failure_never_returns_page_one(private_key: bytes) -> None:
    transport = Transport(
        {
            "orders?": [
                HttpResponse(200, {"orders": [{}], "cursor": "two"}),
                HttpResponse(500, {}),
                HttpResponse(500, {}),
                HttpResponse(500, {}),
            ]
        }
    )
    with pytest.raises(UpstreamUnavailable):
        client(private_key, transport).refresh("reader")


def test_repeated_cursor_fails_closed(private_key: bytes) -> None:
    transport = Transport(
        {
            "fills?": [
                HttpResponse(200, {"fills": [], "cursor": "same"}),
                HttpResponse(200, {"fills": [], "cursor": "same"}),
            ]
        }
    )
    with pytest.raises(PaginationError):
        client(private_key, transport).refresh("reader")


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, AuthenticationRejected),
        (429, RateLimited),
        (503, UpstreamUnavailable),
        (400, AccountGatewayError),
    ],
)
def test_http_failures(private_key: bytes, status: int, error: type[Exception]) -> None:
    count = 3 if status in (429, 503) else 1
    with pytest.raises(error):
        client(
            private_key, Transport({"api_keys": [HttpResponse(status, {}) for _ in range(count)]})
        ).refresh("reader")


def test_timeout_is_bounded_and_counted(private_key: bytes) -> None:
    gateway = client(
        private_key, Transport({"api_keys": [TimeoutError(), TimeoutError(), TimeoutError()]})
    )
    with pytest.raises(UpstreamUnavailable):
        gateway.refresh("reader")
    assert gateway.read_budget.requests == 3 and gateway.read_budget.retries == 2


def test_malformed_and_legacy_money_fail_closed(private_key: bytes) -> None:
    for row in ({"yes_total_cost_dollars": 123}, {"yes_total_cost": 123}):
        transport = Transport(
            {"settlements?": [HttpResponse(200, {"settlements": [row], "cursor": ""})]}
        )
        with pytest.raises(SnapshotValidationError):
            client(private_key, transport).refresh("reader")
