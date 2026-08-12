from __future__ import annotations

import base64
import hashlib
import io
import subprocess
from collections.abc import Mapping
from decimal import Decimal
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
from services.web_dashboard.app import DashboardApp
from services.web_dashboard.security import SecretBox
from services.web_dashboard.setup import SetupService
from services.web_dashboard.store import StateStore


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
            return HttpResponse(
                200,
                {
                    "api_keys": [
                        {
                            "api_key_id": "reader",
                            "name": "account reader",
                            "scopes": ["read"],
                            "subaccount": 0,
                        }
                    ]
                },
            )
        if "balance" in path:
            return HttpResponse(
                200,
                {
                    "balance": 100000,
                    "balance_dollars": "1000.00",
                    "portfolio_value": 100125,
                    "updated_ts": 1_700_000_000,
                    "balance_breakdown": [{"exchange_index": 0, "balance": "1000.00"}],
                },
            )
        if "limits" in path:
            return HttpResponse(
                200,
                {
                    "usage_tier": "basic",
                    "read": {"refill_rate": 10, "bucket_capacity": 20},
                    "write": {"refill_rate": 5, "bucket_capacity": 10},
                    "grants": [{"level": 1, "source": "account", "expires_ts": 1_800_000_000}],
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
        snap.cash == Decimal("1000")
        and snap.portfolio_value == Decimal("1001.25")
        and snap.settlements[0]["yes_total_cost_dollars"].as_tuple().exponent == -3
    )
    assert snap.subaccount == 0 and all(
        "subaccount=0" in path for path in transport.paths if "/portfolio/" in path
    )
    assert any("cursor=next" in path for path in transport.paths)


@pytest.mark.parametrize("scopes", [[], ["write"], ["read", "write"], None])
def test_scope_must_be_exactly_read(private_key: bytes, scopes: Any) -> None:
    transport = Transport(
        {
            "api_keys": [
                HttpResponse(
                    200,
                    {"api_keys": [{"api_key_id": "reader", "scopes": scopes, "subaccount": 0}]},
                )
            ]
        }
    )
    with pytest.raises(AuthenticationRejected):
        client(private_key, transport).refresh("reader")


def test_scope_match_is_unambiguous_and_uses_current_api_key_id(private_key: bytes) -> None:
    for keys in (
        [{"id": "reader", "scopes": ["read"], "subaccount": 0}],
        [
            {"api_key_id": "reader", "scopes": ["read"], "subaccount": 0},
            {"api_key_id": "reader", "scopes": ["read"], "subaccount": 0},
        ],
    ):
        transport = Transport({"api_keys": [HttpResponse(200, {"api_keys": keys})]})
        with pytest.raises(AuthenticationRejected):
            client(private_key, transport).refresh("reader")


@pytest.mark.parametrize("subaccount", [1, -1, True, False, "0", "", [], {}, [0], {"v": 0}, 0.0])
def test_read_key_with_incompatible_subaccount_is_rejected(
    private_key: bytes, subaccount: Any
) -> None:
    key = {"api_key_id": "reader", "scopes": ["read"], "subaccount": subaccount}
    transport = Transport({"api_keys": [HttpResponse(200, {"api_keys": [key]})]})
    with pytest.raises(AuthenticationRejected):
        client(private_key, transport).refresh("reader")


def test_read_key_missing_subaccount_key_entirely_is_accepted(private_key: bytes) -> None:
    key = {"api_key_id": "reader", "scopes": ["read"]}
    transport = Transport({"api_keys": [HttpResponse(200, {"api_keys": [key]})]})
    client(private_key, transport).refresh("reader")


@pytest.mark.parametrize("subaccount", [None, 0])
def test_read_key_with_null_or_zero_subaccount_is_accepted(
    private_key: bytes, subaccount: Any
) -> None:
    key = {"api_key_id": "reader", "scopes": ["read"], "subaccount": subaccount}
    transport = Transport({"api_keys": [HttpResponse(200, {"api_keys": [key]})]})
    snap = client(private_key, transport).refresh("reader")
    assert snap.subaccount == 0


@pytest.mark.parametrize(
    ("needle", "field"),
    [
        ("positions?", "market_positions"),
        ("orders?", "orders"),
        ("fills?", "fills"),
        ("settlements?", "settlements"),
    ],
)
def test_current_collections_accept_empty_and_paginated_pages(
    private_key: bytes, needle: str, field: str
) -> None:
    transport = Transport(
        {
            needle: [
                HttpResponse(200, {field: [], "cursor": "next-page"}),
                HttpResponse(200, {field: [], "cursor": ""}),
            ]
        }
    )
    client(private_key, transport).refresh("reader")
    paths = [path for path in transport.paths if needle.removesuffix("?") in path]
    assert paths == [
        f"/trade-api/v2/portfolio/{needle}subaccount=0",
        f"/trade-api/v2/portfolio/{needle}subaccount=0&cursor=next-page",
    ]


def test_current_balance_and_nested_limits_reject_float_or_legacy_shapes(
    private_key: bytes,
) -> None:
    bad_responses = {
        "balance": HttpResponse(
            200,
            {
                "balance": 100.0,
                "balance_dollars": "1.00",
                "portfolio_value": 100,
                "updated_ts": 1,
                "balance_breakdown": [],
            },
        ),
        "limits": HttpResponse(
            200,
            {
                "usage_tier": "basic",
                "read_refill_rate": 10,
                "read_capacity": 20,
                "write_refill_rate": 5,
                "write_capacity": 10,
            },
        ),
    }
    for endpoint, response in bad_responses.items():
        with pytest.raises(SnapshotValidationError):
            client(private_key, Transport({endpoint: [response]})).refresh("reader")


def test_balance_breakdown_documented_list_and_permitted_omission_succeed(
    private_key: bytes,
) -> None:
    for balance in (
        {
            "balance": 123,
            "balance_dollars": "1.23",
            "portfolio_value": 456,
            "updated_ts": 1_700_000_000,
            "balance_breakdown": [{"exchange_index": 0, "balance": "1.23"}],
        },
        {
            "balance": 123,
            "balance_dollars": "1.23",
            "portfolio_value": 456,
            "updated_ts": 1_700_000_000,
            "balance_breakdown": [],
        },
        {
            "balance": 123,
            "balance_dollars": "1.23",
            "portfolio_value": 456,
            "updated_ts": 1_700_000_000,
        },
    ):
        snapshot = client(
            private_key, Transport({"balance": [HttpResponse(200, balance)]})
        ).refresh("reader")
        assert snapshot.cash == Decimal("1.23")


@pytest.mark.parametrize(
    "breakdown",
    [
        {},
        "invalid",
        1.5,
        ["not-an-object"],
        [{"exchange_index": 0, "balance": "1.23"}, "invalid"],
    ],
)
def test_malformed_balance_breakdown_fails_closed(private_key: bytes, breakdown: Any) -> None:
    response = HttpResponse(
        200,
        {
            "balance": 123,
            "balance_dollars": "1.23",
            "portfolio_value": 456,
            "updated_ts": 1_700_000_000,
            "balance_breakdown": breakdown,
        },
    )
    with pytest.raises(SnapshotValidationError):
        client(private_key, Transport({"balance": [response]})).refresh("reader")


@pytest.mark.parametrize(
    "grants",
    [
        ["not-an-object"],
        [{"level": 1, "source": "account", "expires_ts": 1_800_000_000}, "invalid"],
    ],
)
def test_malformed_limit_grants_fail_closed(private_key: bytes, grants: Any) -> None:
    response = HttpResponse(
        200,
        {
            "usage_tier": "basic",
            "read": {"refill_rate": 10, "bucket_capacity": 20},
            "write": {"refill_rate": 5, "bucket_capacity": 10},
            "grants": grants,
        },
    )
    with pytest.raises(SnapshotValidationError):
        client(private_key, Transport({"limits": [response]})).refresh("reader")


def test_grant_objects_do_not_require_undocumented_child_fields(private_key: bytes) -> None:
    response = HttpResponse(
        200,
        {
            "usage_tier": "basic",
            "read": {"refill_rate": 10, "bucket_capacity": 20},
            "write": {"refill_rate": 5, "bucket_capacity": 10},
            "grants": [{"level": 1, "source": "account", "expires_ts": 1_800_000_000}, {}],
        },
    )
    client(private_key, Transport({"limits": [response]})).refresh("reader")


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


class RejectingSetup:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def complete(self, **kwargs: Any) -> tuple[str, ...]:
        del kwargs
        raise self.error


class RejectingValidator:
    def validate(self, signer: RequestSigner, key_id: str) -> Any:
        del signer, key_id
        raise AuthenticationRejected("rejected")


def test_setup_service_persists_nothing_when_live_validation_fails(
    tmp_path: Path, private_key: bytes
) -> None:
    store = StateStore(tmp_path / "transaction.db")
    box = SecretBox(b"v" * 32)
    token = "one-use-token"
    store.set_config("setup_token_hash", hashlib.sha256(token.encode()).hexdigest())
    setup = SetupService(store, RejectingValidator(), box)  # type: ignore[arg-type]
    submitted_password = "StrongPassword123!"
    submitted_totp = "totp-fixture"
    with pytest.raises(AuthenticationRejected):
        setup.complete(
            setup_token=token,
            username="owner",
            password=submitted_password,
            totp_secret=submitted_totp,
            totp_code_valid=True,
            key_id="reader",
            private_key_pem=private_key,
        )
    assert store.config("owner") is None
    assert store.config("vault") is None
    assert store.config("setup_token_hash") != "used"


@pytest.mark.parametrize(
    "error",
    [
        AuthenticationRejected("sensitive upstream detail"),
        RateLimited("sensitive upstream detail"),
        UpstreamUnavailable("sensitive upstream detail"),
        AccountGatewayError("sensitive upstream detail"),
        SnapshotValidationError("sensitive upstream detail"),
    ],
)
def test_live_setup_gateway_failures_are_safe_and_never_persist(
    tmp_path: Path,
    private_key: bytes,
    error: Exception,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = StateStore(tmp_path / "setup.db")
    box = SecretBox(b"x" * 32)
    store.set_config("setup_token_hash", "unused-by-route-test")
    app = DashboardApp(store, box, RejectingSetup(error))  # type: ignore[arg-type]
    secret_values = ("key-id-must-not-leak", "setup-token-must-not-leak", "totp-must-not-leak")
    form = {
        "setup_token": secret_values[1].encode(),
        "username": b"owner",
        "password": b"StrongPassword123!",
        "totp_secret": secret_values[2].encode(),
        "totp_code": b"000000",
        "key_id": secret_values[0].encode(),
        "pem": private_key,
    }
    monkeypatch.setattr(app, "_multipart", lambda environ: form)
    monkeypatch.setattr("services.web_dashboard.app.verify_totp", lambda *args: True)
    captured: dict[str, Any] = {}

    def start(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = headers

    body = b"".join(
        app(
            {
                "PATH_INFO": "/setup",
                "REQUEST_METHOD": "POST",
                "CONTENT_LENGTH": "0",
                "wsgi.input": io.BytesIO(),
                "REMOTE_ADDR": "127.0.0.1",
            },
            start,
        )
    )
    assert captured["status"] == "400 Bad Request"
    assert b"No credential or configuration was stored" in body
    assert b"sensitive upstream detail" not in body
    assert all(value.encode() not in body for value in secret_values)
    assert store.config("owner") is None and store.config("vault") is None
