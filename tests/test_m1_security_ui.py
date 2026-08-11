from __future__ import annotations

import io
import json
import time
from pathlib import Path
from typing import Any

import pytest

from services.reporting_service.support_snapshot import support_snapshot_json
from services.web_dashboard.app import SECURITY_HEADERS, DashboardApp
from services.web_dashboard.security import (
    SecretBox,
    SecurityError,
    consume_recovery_code,
    hash_password,
    recovery_codes,
    totp,
    verify_password,
    verify_totp,
)
from services.web_dashboard.store import StateStore


def test_password_totp_recovery_and_encrypted_vault() -> None:
    encoded = hash_password("LongProduction9Password")
    assert verify_password("LongProduction9Password", encoded)
    assert not verify_password("wrong", encoded)
    assert "LongProduction9Password" not in encoded
    secret = "JBSWY3DPEHPK3PXP"
    assert verify_totp(secret, totp(secret, 1_700_000_000), 1_700_000_000)
    clear, hashes = recovery_codes()
    ok, remaining = consume_recovery_code(clear[0], hashes)
    assert ok and len(remaining) == 7 and not consume_recovery_code(clear[0], remaining)[0]
    box = SecretBox(b"x" * 32)
    sealed = box.seal(b"private credential")
    second = box.seal(b"private credential")
    assert sealed != second
    assert "private credential" not in sealed and box.open(sealed) == b"private credential"
    with pytest.raises(SecurityError):
        box.open(sealed[:-2] + "AA")
    assert "xxxxxxxx" not in repr(box)


def call(
    app: DashboardApp, path: str, method: str = "GET", cookie: str = "", body: str = ""
) -> tuple[str, dict[str, str], bytes]:
    captured: dict[str, Any] = {}

    def start(status: str, headers: list[tuple[str, str]]) -> None:
        captured.update(status=status, headers=dict(headers))

    route, _, query = path.partition("?")
    output = b"".join(
        app(
            {
                "PATH_INFO": route,
                "QUERY_STRING": query,
                "REQUEST_METHOD": method,
                "HTTP_COOKIE": cookie,
                "CONTENT_LENGTH": str(len(body)),
                "wsgi.input": io.BytesIO(body.encode()),
                "REMOTE_ADDR": "127.0.0.1",
            },
            start,
        )
    )
    return captured["status"], captured["headers"], output


def configured_store(tmp_path: Path) -> tuple[StateStore, SecretBox, str, str]:
    store, box = StateStore(tmp_path / "state.db"), SecretBox(b"k" * 32)
    store.set_config("owner", "owner")
    store.set_config("password_hash", hash_password("LongProduction9Password"))
    store.set_config("totp_secret", box.seal(b"JBSWY3DPEHPK3PXP"))
    store.set_config("recovery_hashes", "[]")
    store.set_config("vault", box.seal(b"secret"))
    token, csrf = store.create_session(int(time.time()))
    return store, box, token, csrf


def test_ui_security_headers_csrf_stale_state_and_downloads(tmp_path: Path) -> None:
    store, box, token, csrf = configured_store(tmp_path)
    app = DashboardApp(store, box)
    status, headers, body = call(app, "/", cookie=f"session={token}")
    assert status == "200 OK" and b"REAL ACCOUNT CONNECTED" in body and b"stale" in body
    assert all(name in headers for name, _ in SECURITY_HEADERS)
    assert headers["Cache-Control"] == "no-store"
    status, _, _ = call(app, "/logout", "POST", f"session={token}", "csrf=wrong")
    assert status == "403 Forbidden"
    status, headers, body = call(app, "/reports/support.json", cookie=f"session={token}")
    assert (
        status == "200 OK" and "attachment" in headers["Content-Disposition"] and json.loads(body)
    )
    status, _, _ = call(app, "/logout", "POST", f"session={token}", f"csrf={csrf}")
    assert status == "303 See Other"


def test_support_snapshot_excludes_every_sensitive_class() -> None:
    raw = {
        "private_key": "PEM",
        "api_key_id": "id",
        "signature": "sig",
        "password_hash": "hash",
        "totp_secret": "totp",
        "recovery_codes": ["r"],
        "encryption_key": "enc",
        "session_secret": "sess",
        "authenticated_response": {"order_id": "OID", "fill_id": "FID"},
    }
    exported = support_snapshot_json(raw, raw)
    parsed = json.loads(exported)

    def values(value: Any) -> list[Any]:
        if isinstance(value, dict):
            return [item for nested in value.values() for item in values(nested)]
        if isinstance(value, list):
            return [item for nested in value for item in values(nested)]
        return [value]

    exposed = values(parsed)
    for secret in ("PEM", "id", "sig", "hash", "totp", "r", "enc", "sess", "OID", "FID"):
        assert secret not in exposed


def test_m1_has_no_external_mutation_capability() -> None:
    source = (
        Path("services/kalshi_account_gateway").read_text()
        if Path("services/kalshi_account_gateway").is_file()
        else "".join(
            path.read_text() for path in Path("services/kalshi_account_gateway").glob("*.py")
        )
    )
    for verb in ('method="POST"', 'method="PUT"', 'method="PATCH"', 'method="DELETE"'):
        assert verb not in source


def test_m2_market_ui_filters_large_persisted_universe(tmp_path: Path) -> None:
    store, box, token, _ = configured_store(tmp_path)
    markets = [
        {
            "ticker": f"M{i}",
            "title": ("A very long weather market title " * 8) + str(i),
            "family": "weather" if i % 2 else "macro",
            "series_ticker": f"S{i % 20}",
            "status": "active",
            "closes_at": "2026-12-01T00:00:00Z",
            "provisional": int(i % 11 == 0),
            "mve": int(i % 13 == 0),
            "quality_healthy": int(i % 7 != 0),
            "best_bid": "0.400",
            "best_ask": "0.450",
            "quote_observed_at": "2026-01-01T00:00:00Z",
            "rules_hash": f"hash-{i}",
        }
        for i in range(3000)
    ]
    store.seed_universe_fixture(
        markets,
        {
            "status": "PARTIAL",
            "last_baseline": "2026-01-01T00:00:00Z",
            "last_incremental": None,
            "watermark": "2026-01-01T00:00:00Z",
            "historical_cutoff": "2025-01-01T00:00:00Z",
            "series_count": 20,
            "event_count": 200,
        },
    )
    app = DashboardApp(store, box)
    status, _, body = call(app, "/markets", cookie=f"session={token}")
    assert status == "200 OK" and b"3000" in body and body.count(b"market-card") <= 50
    status, _, body = call(app, "/markets?family=weather&q=2999", cookie=f"session={token}")
    assert (
        b"2999" in body and b"2998" not in body and b"Indexed, active, strategy-supported" in body
    )


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("HEALTHY", b"REAL-TIME"),
        ("CONNECTING", b"NOT REAL-TIME: CONNECTING"),
        ("FAILED", b"NOT REAL-TIME: FAILED"),
        ("GAP_DETECTED", b"NOT REAL-TIME: GAP_DETECTED"),
        ("RECONNECTING", b"NOT REAL-TIME: RECONNECTING"),
        ("STALE", b"NOT REAL-TIME: STALE"),
        ("BACKPRESSURED", b"NOT REAL-TIME: BACKPRESSURED"),
    ],
)
def test_m3_ui_never_labels_unhealthy_feed_realtime(
    tmp_path: Path, state: str, expected: bytes
) -> None:
    store, box, token, _ = configured_store(tmp_path)
    store.seed_realtime_fixture(
        {
            "state": state,
            "epoch": "epoch-1",
            "current_books": 3 if state == "HEALTHY" else 0,
            "stale_books": 4,
            "unresolved_gaps": 1 if state in {"GAP_DETECTED", "BACKPRESSURED"} else 0,
            "subscription_count": 5,
            "depth_watch_count": 2,
            "archive_state": "HEALTHY",
        }
    )
    app = DashboardApp(store, box)
    _, _, markets = call(app, "/markets", cookie=f"session={token}")
    _, _, system = call(app, "/system", cookie=f"session={token}")
    assert expected in markets and state.encode() in system and b"No write key" in system


def test_m4_market_detail_and_semantic_health_are_honest(tmp_path: Path) -> None:
    store, box, token, _ = configured_store(tmp_path)
    store.seed_semantic_fixture(
        {
            "market_ticker": "M",
            "yes_proposition": "Final NWS high is at least 90 F",
            "no_proposition": "Final NWS high is below 90 F",
            "authority": "NWS",
            "sources": "NWS climate report",
            "measured_value": "KNYC daily maximum temperature",
            "threshold": ">= 90 F",
            "deadline": "2026-08-11 23:59 ET",
            "timezone": "America/New_York",
            "revision_rules": "final report",
            "correction_rules": "published correction controls",
            "early_close_rules": "none",
            "cancellation_rules": "exchange rules",
            "postponement_rules": "not applicable",
            "payout_model": "SIMPLE_BINARY",
            "semantic_status": "AMBIGUOUS",
            "rules_version": "r1",
            "interpretation_version": "v2",
            "issues": "Weather station identifier needs confirmation",
            "semantic_hash": "semantic-hash",
            "parser_versions": "deterministic-v2",
            "provenance_summary": "threshold <- MARKET.rules_primary",
        }
    )
    app = DashboardApp(store, box)
    _, _, detail = call(app, "/markets/M", cookie=f"session={token}")
    assert (
        b"HOW THIS MARKET SETTLES" in detail
        and b"YES means" in detail
        and b"must fail closed" in detail
    )
    assert b"probability" in detail and b"edge" in detail and b"semantic-hash" in detail
    _, _, report = call(app, "/reports/support.json", cookie=f"session={token}")
    parsed = json.loads(report)
    assert parsed["config"]["semantics"]["ambiguous"] == 1
