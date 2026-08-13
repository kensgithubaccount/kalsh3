from __future__ import annotations

import io
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest

from services.web_dashboard.app import CSS, DashboardApp, _layout
from services.web_dashboard.product import (
    SURFACES,
    GlobalProductState,
    derive_global_state,
    dollars,
)
from services.web_dashboard.security import SecretBox, hash_password, totp
from services.web_dashboard.store import StateStore


class AuditParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.landmarks: set[str] = set()
        self.h1_count = 0
        self.current_pages = 0
        self.disabled_buttons = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag in {"header", "nav", "main", "footer"}:
            self.landmarks.add(tag)
        if tag == "h1":
            self.h1_count += 1
        if values.get("aria-current") == "page":
            self.current_pages += 1
        if tag == "button" and "disabled" in values:
            self.disabled_buttons += 1


def configured(tmp_path: Path) -> tuple[StateStore, DashboardApp, str]:
    store = StateStore(tmp_path / "state.db")
    box = SecretBox(b"k" * 32)
    store.set_config("owner", "owner")
    store.set_config("password_hash", hash_password("LongProduction9Password"))
    store.set_config("vault", box.seal(b"read-only"))
    store.set_config("totp_secret", box.seal(b"JBSWY3DPEHPK3PXP"))
    token, _ = store.create_session(int(time.time()))
    return store, DashboardApp(store, box), token


def call(
    app: DashboardApp,
    path: str,
    token: str,
    method: str = "GET",
    body: str = "",
) -> tuple[str, bytes]:
    captured: dict[str, Any] = {}

    def start(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status

    result = b"".join(
        app(
            {
                "PATH_INFO": path,
                "QUERY_STRING": "",
                "REQUEST_METHOD": method,
                "HTTP_COOKIE": f"session={token}",
                "CONTENT_LENGTH": str(len(body)),
                "wsgi.input": io.BytesIO(body.encode()),
                "REMOTE_ADDR": "127.0.0.1",
            },
            start,
        )
    )
    return str(captured["status"]), result


def test_deep_surface_inventory_matches_information_architecture() -> None:
    """M23B replaced the flat 12-link top nav with 5 top-level sections plus a
    per-section secondary nav (see test_m23b_dashboard_simplification.py for
    that positive coverage). SURFACES itself is now an inventory of every deep
    page — checked here as a set, since its declaration order no longer
    determines what's rendered in the primary nav.
    """
    expected = {
        "Dashboard",
        "Opportunities",
        "Breaking Now",
        "Markets",
        "Activity",
        "Portfolio",
        "Orders & Trades",
        "Reports",
        "Strategy",
        "Learning",
        "Sources",
        "Advanced",
        "System",
        "Risk & Safety",
    }
    assert {surface.label for surface in SURFACES} == expected
    page = _layout("Markets", "<h1>Markets</h1>", current_path="/markets").decode()
    audit = AuditParser()
    audit.feed(page)
    assert audit.landmarks == {"header", "nav", "main", "footer"}
    assert audit.h1_count == 1
    assert audit.current_pages >= 1


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"account_status": "healthy", "stale": False, "unresolved_gaps": 0}, "LEARNING"),
        ({"account_status": "error", "stale": True, "unresolved_gaps": 1}, "NEEDS ATTENTION"),
        (
            {
                "account_status": "healthy",
                "stale": False,
                "unresolved_gaps": 0,
                "compliance_hold": True,
            },
            "HALTED",
        ),
    ],
)
def test_global_state_is_single_and_fail_closed(kwargs: dict[str, Any], expected: str) -> None:
    assert derive_global_state(**kwargs) == expected
    assert derive_global_state(**kwargs) not in {
        GlobalProductState.TRADING,
        GlobalProductState.READY_FOR_APPROVAL,
        GlobalProductState.AWAITING_APPROVAL,
    }


def test_financial_units_use_decimal_and_unknown_is_not_zero() -> None:
    assert dollars("1000") == "$1,000.00"
    assert dollars("0.125") == "$0.12"
    assert dollars(None) == "Unavailable"
    assert dollars("not-money") == "Unavailable"


def test_every_primary_surface_renders_honest_empty_state(tmp_path: Path) -> None:
    _, app, token = configured(tmp_path)
    expected = {
        "/": (b"Reported portfolio value", b"Bot P", b"No qualified opportunities yet"),
        "/opportunities": (b"INSUFFICIENT REAL FORECAST EVIDENCE", b"No trade has been authorized"),
        "/breaking": (b"SHADOW RESEARCH ONLY", b"never authorize"),
        "/markets": (b"READ-ONLY DISCOVERY", b"No markets match"),
        "/sources": (b"No external sources configured",),
        "/learning": (b"RESEARCH GOVERNANCE ONLY", b"PRODUCTION INFLUENCE: NONE"),
        "/portfolio": (b"Unresolved exposure", b"Unavailable"),
        "/orders": (b"No order can be proposed", b"Unknown / reconciliation required"),
        "/reports": (b"Daily operating brief", b"NOT SCHEDULED"),
        "/risk": (b"Production activation", b"UNAVAILABLE", b"No production-write credential"),
        "/system": (b"API compatibility", b"NOT VERIFIED"),
        "/advanced": (b"ADVANCED RESEARCH DIAGNOSTICS", b"Raw JSON"),
    }
    for path, snippets in expected.items():
        status, body = call(app, path, token)
        assert status == "200 OK", path
        assert all(snippet in body for snippet in snippets), path
        assert b"PRODUCTION WRITES: <strong>OFF</strong>" in body
        assert b'aria-current="page"' in body


def test_unavailable_activation_is_status_not_a_dead_control(tmp_path: Path) -> None:
    _, app, token = configured(tmp_path)
    _, body = call(app, "/risk", token)
    page = body.decode()
    audit = AuditParser()
    audit.feed(page)
    assert audit.disabled_buttons == 1
    assert "Arm trading" not in page
    assert "Production activation</h2><strong>UNAVAILABLE" in page
    assert "aria-describedby=reset-reason" in page


def test_m15_production_status_is_truthful_and_has_no_arm_route(tmp_path: Path) -> None:
    _, app, token = configured(tmp_path)
    status, body = call(app, "/system", token)
    assert status == "200 OK"
    for value in (
        b"IMPLEMENTED / OFFLINE VERIFIED",
        b"NOT INSTALLED",
        b"DISARMED",
        b"Production orders</dt><dd>DISABLED",
        b"Real-money order executed</dt><dd>NO",
    ):
        assert value in body
    assert b"ARM PRODUCTION" not in body


def test_authenticated_global_halt_is_durable_and_does_not_claim_cancel(tmp_path: Path) -> None:
    store, app, token = configured(tmp_path)
    csrf = store.session_csrf(token, int(time.time()))
    assert csrf is not None
    body = urlencode(
        {
            "csrf": csrf,
            "reason": "account state uncertain",
            "confirmation": "HALT NEW RISK",
            "password": "LongProduction9Password",
            "totp": totp("JBSWY3DPEHPK3PXP", int(time.time())),
        }
    )
    status, _ = call(app, "/risk/halt", token, "POST", body)
    assert status == "303 See Other"
    restarted = DashboardApp(store, app.box)
    _, page = call(restarted, "/risk", token)
    assert b"HALTED" in page and b"account state uncertain" in page
    assert b"does not cancel exchange orders" in page
    assert b"M13 cannot cancel an external Kalshi order" in page


def test_risk_evaluation_fixture_explains_pass_without_order_approval(tmp_path: Path) -> None:
    store, app, token = configured(tmp_path)
    store.seed_risk_evaluation_fixture(
        {
            "evaluation_id": "risk-1",
            "market_ticker": "WEATHER",
            "intended_maximum_loss": "8",
            "existing_market_risk": "1",
            "projected_market_risk": "9",
            "market_limit": "10",
            "projected_event_risk": "19",
            "event_limit": "25",
            "projected_aggregate_risk": "72",
            "aggregate_limit": "100",
            "reserve_state": "PRESERVED",
            "result": "RISK CHECK PASSED · PASS_NEXT_GATE",
            "reason_codes": "",
            "data_mode": "SYNTHETIC TEST",
        }
    )
    _, page = call(app, "/risk", token)
    assert b"$8.00" in page and b"$9.00" in page and b"$72.00" in page
    assert b"RISK CHECK PASSED" in page and b"PASS_NEXT_GATE" in page
    assert b"This does not authorize an order" in page
    assert b"ORDER APPROVED" not in page and b"APPROVED TRADE" not in page


def test_responsive_css_has_touch_focus_mobile_and_overflow_guards() -> None:
    assert "min-height:44px" in CSS
    assert ":focus-visible" in CSS
    assert "overflow-wrap:anywhere" in CSS
    assert "@media(max-width:900px)" in CSS
    assert "@media(max-width:650px)" in CSS
    assert "prefers-reduced-motion" in CSS
    assert "grid-template-columns:1fr" in CSS


def test_unknown_route_is_a_real_404(tmp_path: Path) -> None:
    _, app, token = configured(tmp_path)
    status, body = call(app, "/does-not-exist", token)
    assert status == "404 Not Found"
    assert b"This page does not exist" in body


def test_dashboard_namespace_stays_non_mutating() -> None:
    source = "\n".join(path.read_text() for path in Path("services/web_dashboard").glob("*.py"))
    forbidden = (
        "submit_order(",
        "place_order(",
        "cancel_order(",
        "amend_order(",
        "arm_trading(",
        "authorize_new_risk(",
    )
    assert not any(term in source for term in forbidden)
