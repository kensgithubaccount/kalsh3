from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from services.breaking_signals.adapters import AdapterError, FeedResponse, OfficialFeedAdapter
from services.kalshi_account_gateway.client import (
    MAX_RESPONSE_BYTES,
    AccountGatewayError,
    UrllibReadTransport,
    _NoRedirect,
)
from services.production_execution.boundary import ProductionExecutionBoundary
from services.production_execution.domain import canonical_json
from services.risk_engine.policy import RiskPolicy
from services.supervised_canary.store import CanaryStore
from services.web_dashboard.security import verify_password, verify_totp


class FeedTransport:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def get(self, url: str, **kwargs: object) -> FeedResponse:
        del kwargs
        return FeedResponse(200, self.content, "application/xml", None, None, url)


class Response:
    def __init__(self, body: bytes, content_type: str = "application/json") -> None:
        self.body = body
        self.status = 200
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def read(self, size: int) -> bytes:
        return self.body[:size]


class Opener:
    def __init__(self, response: Response) -> None:
        self.response = response
        self.requests: list[Any] = []

    def open(self, request: Any, *, timeout: float) -> Response:
        assert timeout == 1
        self.requests.append(request)
        return self.response


def test_xml_dtd_and_entity_expansion_are_rejected_before_parser() -> None:
    malicious = b'<!DOCTYPE x [<!ENTITY boom "expanded">]><rss><item>&boom;</item></rss>'
    adapter = OfficialFeedAdapter(FeedTransport(malicious), frozenset({"agency.gov"}))
    with pytest.raises(AdapterError, match="declarations"):
        adapter.fetch("https://agency.gov/feed")


def test_production_read_transport_rejects_redirect_and_path_confusion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert (
        _NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://evil.test") is None
    )  # type: ignore[arg-type]
    transport = UrllibReadTransport()
    for path in (
        "https://evil.test/trade-api/v2/portfolio/balance",
        "//evil.test/trade-api/v2/portfolio/balance",
        "/trade-api/v2/../api_keys",
        "/trade-api/v2/%2e%2e/api_keys",
        "/trade-api/v2//portfolio/balance",
        "/not-trade-api/v2/portfolio/balance",
    ):
        with pytest.raises(AccountGatewayError, match="non-canonical"):
            transport.get(path, {}, timeout_seconds=1)

    opener = Opener(Response(b'{"balance":1}'))
    seen_handlers: list[object] = []

    def build_opener(handler: object) -> Opener:
        seen_handlers.append(handler)
        return opener

    monkeypatch.setattr("urllib.request.build_opener", build_opener)
    result = transport.get("/trade-api/v2/portfolio/balance", {}, timeout_seconds=1)
    assert result.status == 200 and result.payload == {"balance": 1}
    assert isinstance(seen_handlers[0], _NoRedirect)


def test_production_read_transport_rejects_oversize_and_wrong_content_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = UrllibReadTransport()
    for response, reason in (
        (Response(b"x" * (MAX_RESPONSE_BYTES + 1)), "size"),
        (Response(b"{}", "text/html"), "content type"),
    ):

        def build_opener(*args: object, selected: Response = response) -> Opener:
            del args
            return Opener(selected)

        monkeypatch.setattr("urllib.request.build_opener", build_opener)
        with pytest.raises(AccountGatewayError, match=reason):
            transport.get("/trade-api/v2/portfolio/balance", {}, timeout_seconds=1)


def test_nested_float_can_never_enter_production_canonical_body() -> None:
    for body in (
        {"outer": ["safe", 0.42]},
        {"outer": {"price": 0.42}},
        {"outer": ("safe", {"quantity": 1.0})},
    ):
        with pytest.raises(TypeError, match="floats"):
            canonical_json(body)
    assert canonical_json({"price": "0.4200", "quantity": "1.00"}) == (
        b'{"price":"0.4200","quantity":"1.00"}'
    )


def test_authentication_rejects_hostile_parameters_and_malformed_totp() -> None:
    hostile = "scrypt$1073741824$8$1$YWJjZA==$YWJjZA=="
    assert verify_password("anything", hostile) is False
    assert verify_password("anything", "scrypt$32768$8$1$bad$bad") is False
    for code in (
        "",
        "12345",
        "123456",
        "1234567",
        "\uff11\uff12\uff13\uff14\uff15\uff16",
        "abcdef",
    ):
        assert verify_totp("not-valid-base32!", code, 1_700_000_000) is False


def _legacy_canary_database(path: Path, filled: str = "0.40", remaining: str = "0.60") -> None:
    with sqlite3.connect(path) as db:
        db.executescript("""
          CREATE TABLE canary_sessions(
            session_id TEXT PRIMARY KEY, preview_id TEXT NOT NULL UNIQUE,
            approval_id TEXT NOT NULL UNIQUE, client_order_id TEXT NOT NULL UNIQUE,
            state TEXT NOT NULL, filled_quantity TEXT NOT NULL DEFAULT '0',
            remaining_quantity TEXT NOT NULL DEFAULT '1.00', reconciliation_version TEXT,
            possibly_submitted INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
            resolved_at TEXT,
            CHECK(CAST(filled_quantity AS REAL)+CAST(remaining_quantity AS REAL)=1.0));
          CREATE UNIQUE INDEX one_unresolved_canary ON canary_sessions((1))
            WHERE state IN ('RECONCILING');
        """)
        db.execute(
            "INSERT INTO canary_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("s", "p", "a", "c", "RECONCILING", filled, remaining, None, 0, "now", None),
        )


def test_legacy_canary_quantities_migrate_to_exact_integer_atoms(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    _legacy_canary_database(path)
    CanaryStore(path)
    with sqlite3.connect(path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(canary_sessions)")}
        row = db.execute(
            "SELECT filled_atoms,remaining_atoms FROM canary_sessions WHERE session_id='s'"
        ).fetchone()
    assert "filled_quantity" not in columns
    assert row == (400_000, 600_000)


def test_corrupt_legacy_canary_quantity_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.db"
    _legacy_canary_database(path, "0.3333333", "0.6666667")
    with pytest.raises(RuntimeError, match="cannot be migrated exactly"):
        CanaryStore(path)


def test_duplicate_and_regressing_cumulative_fill_do_not_double_count(tmp_path: Path) -> None:
    store = CanaryStore(tmp_path / "fills.db")
    assert store.open_session(
        session_id="s",
        preview_id="p",
        approval_id="a",
        client_order_id="c",
        now=datetime.now(UTC),
    )
    store.record_fill("s", filled=Decimal("0.40"), mode="REAL_PRODUCTION")
    store.record_fill("s", filled=Decimal("0.40"), mode="REAL_PRODUCTION")
    with pytest.raises(ValueError, match="cannot decrease"):
        store.record_fill("s", filled=Decimal("0.39"), mode="REAL_PRODUCTION")
    with sqlite3.connect(store.path) as db:
        count = db.execute("SELECT real_fill_count FROM production_fill_counter").fetchone()
    assert count == (1,)


def test_final_architecture_has_no_research_to_signer_import_path() -> None:
    for package in (
        "forecasting",
        "learning",
        "document_intelligence",
        "breaking_signals",
        "opportunity_engine",
    ):
        source = "\n".join(path.read_text() for path in Path("services", package).glob("*.py"))
        assert "production_execution" not in source
        assert "ProductionWriteCredential" not in source
        assert "SignAndSendBoundary" not in source
    health = ProductionExecutionBoundary().health()
    assert health["production_state"] == "DISARMED"
    assert health["credential_installed"] is False
    assert health["scope_validated"] is False
    assert not hasattr(ProductionExecutionBoundary(), "signer")


def test_m13_capital_limits_are_unchanged_and_decimal() -> None:
    policy = RiskPolicy()
    assert str(policy.bankroll) == "1000"
    assert str(policy.protected_reserve) == "700"
    assert str(policy.active_capital) == "300"
    assert str(policy.aggregate_open_risk_limit) == "100"
    assert str(policy.market_loss_limit) == "10"
    assert str(policy.related_event_risk_limit) == "25"
    assert all(
        isinstance(value, Decimal)
        for value in (
            policy.bankroll,
            policy.protected_reserve,
            policy.active_capital,
            policy.aggregate_open_risk_limit,
            policy.market_loss_limit,
            policy.related_event_risk_limit,
        )
    )


def test_repository_has_no_activation_environment_or_write_secret() -> None:
    source = "\n".join(
        path.read_text(errors="ignore")
        for root in (Path("services"), Path("deploy"))
        for path in root.rglob("*")
        if path.is_file()
    )
    assert "PRODUCTION_TRADING=true" not in source
    assert "AUTONOMY=true" not in source
    assert "KALSHI_WRITE_PEM" not in source
    assert "BEGIN RSA PRIVATE KEY" not in source
