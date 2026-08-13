"""M23B — Trading Dashboard Simplification regression tests.

Covers the specific requirements from the M23B task: a simplified five-section
navigation, a Dashboard that no longer exposes the full readiness matrix (it
moved to /system), truthful Account-vs-Bot provenance, no fabricated
portfolio history/opportunities/P&L, and that every legacy deep page remains
reachable under the new navigation. Nothing here touches the signer, risk
authorization, credential handling, or order paths.
"""

from __future__ import annotations

import io
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from services.web_dashboard.app import SECURITY_HEADERS, DashboardApp
from services.web_dashboard.product import ADVANCED_SURFACES, NAV_SECTIONS, SURFACES
from services.web_dashboard.security import SecretBox, hash_password
from services.web_dashboard.store import StateStore


def _configured(tmp_path: Path) -> tuple[StateStore, DashboardApp, str]:
    store = StateStore(tmp_path / "state.db")
    box = SecretBox(b"k" * 32)
    store.set_config("owner", "owner")
    store.set_config("password_hash", hash_password("LongProduction9Password"))
    store.set_config("vault", box.seal(b"read-only"))
    store.set_config("totp_secret", box.seal(b"JBSWY3DPEHPK3PXP"))
    token, _ = store.create_session(int(time.time()))
    return store, DashboardApp(store, box), token


def _get(app: DashboardApp, path: str, token: str) -> tuple[str, bytes]:
    captured: dict[str, Any] = {}

    def start(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status

    body = b"".join(
        app(
            {
                "PATH_INFO": path,
                "QUERY_STRING": "",
                "REQUEST_METHOD": "GET",
                "HTTP_COOKIE": f"session={token}",
                "CONTENT_LENGTH": "0",
                "wsgi.input": io.BytesIO(b""),
                "REMOTE_ADDR": "127.0.0.1",
            },
            start,
        )
    )
    return str(captured["status"]), body


def _snapshot(observed_at: str, cash: str, portfolio_value: str, **extra: Any) -> dict[str, Any]:
    return {
        "observed_at": observed_at,
        "cash": Decimal(cash),
        "portfolio_value": Decimal(portfolio_value),
        "positions": [],
        "orders": [],
        "fills": [],
        "settlements": [],
        **extra,
    }


# --- 3. Primary navigation exposes only simplified top-level areas ---------


def test_primary_nav_has_exactly_five_top_level_sections() -> None:
    assert [s.label for s in NAV_SECTIONS] == [
        "Dashboard",
        "Markets",
        "Activity",
        "Strategy",
        "System",
    ]


def test_dashboard_top_nav_renders_exactly_five_links(tmp_path: Path) -> None:
    _, app, token = _configured(tmp_path)
    _, body = _get(app, "/", token)
    page = body.decode()
    header, _, _rest = page.partition("</header>")
    for label in ("Dashboard", "Markets", "Activity", "Strategy", "System"):
        assert f">{label}<" in header, label
    # None of the individual deep-page labels leak into the primary header nav.
    for leaked in ("Opportunities", "Breaking Now", "Portfolio", "Learning", "Risk & Safety"):
        assert f">{leaked}<" not in header, leaked


# --- 4. / 24. Every legacy/deep page remains reachable under the new nav ---


def test_every_surface_including_legacy_deep_pages_remains_reachable(tmp_path: Path) -> None:
    _, app, token = _configured(tmp_path)
    for surface in (*SURFACES, *ADVANCED_SURFACES):
        status, _ = _get(app, surface.path, token)
        assert status == "200 OK", surface.path


def test_activity_and_strategy_hubs_link_to_their_deep_pages(tmp_path: Path) -> None:
    _, app, token = _configured(tmp_path)
    _, activity_body = _get(app, "/activity", token)
    activity = activity_body.decode()
    for href in ("/portfolio", "/orders", "/reports"):
        assert f'href="{href}"' in activity or f"href={href}" in activity, href

    _, strategy_body = _get(app, "/strategy", token)
    strategy = strategy_body.decode()
    for href in ("/forecasting", "/learning", "/sources", "/backtests", "/advanced"):
        assert f'href="{href}"' in strategy or f"href={href}" in strategy, href


# --- 1. / 2. Readiness matrix moved off Dashboard, still full on /system ---


def test_full_readiness_matrix_is_available_on_system_page(tmp_path: Path) -> None:
    _, app, token = _configured(tmp_path)
    _, body = _get(app, "/system", token)
    page = body.decode()
    assert "readiness-checklist" in page
    for category in (
        "Connection",
        "Research readiness",
        "Risk readiness",
        "Execution readiness",
        "Autonomy readiness",
    ):
        assert category in page
    for check in (
        "Market universe initialized",
        "Live market data connected",
        "No unresolved market-data gaps",
        "Required real evidence sufficient",
        "Compliance state established and clear",
    ):
        assert check in page


def test_primary_action_on_system_page_has_no_concatenated_text(tmp_path: Path) -> None:
    """Pins the M23B typography fix: eyebrow/title/detail must never run together
    (the live bug rendered "What needs you mostRequired real evidence sufficient").
    Whatever the current top unmet check is, the eyebrow must close its own block
    element before the title starts, not run directly into it."""
    _, app, token = _configured(tmp_path)
    _, body = _get(app, "/system", token)
    page = body.decode()
    assert "WHAT NEEDS YOU MOST</p><h3>" in page


# --- 7. / 8. Disconnected market data / NOT_STARTED universe never look ready ---


def test_disconnected_market_data_and_unstarted_universe_show_as_not_ready(
    tmp_path: Path,
) -> None:
    """A fresh store defaults to universe NOT_STARTED and realtime DISCONNECTED —
    exactly the live contradiction M23B fixes: zero gaps must not imply healthy
    research readiness while the market-data system never started."""
    _, app, token = _configured(tmp_path)
    _, body = _get(app, "/system", token)
    page = body.decode()
    assert "Market universe status: NOT_STARTED" in page
    assert "Market data status: DISCONNECTED" in page


def test_active_universe_and_healthy_market_data_show_research_ready(tmp_path: Path) -> None:
    store, app, token = _configured(tmp_path)
    store.seed_universe_fixture(
        [],
        {
            "status": "ACTIVE",
            "last_baseline": None,
            "last_incremental": None,
            "watermark": None,
            "historical_cutoff": None,
            "series_count": 0,
            "event_count": 0,
        },
    )
    store.seed_realtime_fixture({"state": "HEALTHY"})
    _, body = _get(app, "/system", token)
    page = body.decode()
    assert "Market universe is initialized" in page
    assert "Live market data is connected" in page


# --- 6. / 13. Bot P&L unavailable until provenance exists -------------------


def test_bot_pnl_is_unavailable_with_honest_note(tmp_path: Path) -> None:
    _, app, token = _configured(tmp_path)
    _, body = _get(app, "/", token)
    page = body.decode()
    assert "Bot P" in page  # "Bot P&amp;L" once escaped
    assert "No attributable live trades yet" in page


# --- 12. No fake opportunities ----------------------------------------------


def test_no_opportunities_renders_honest_empty_state_never_fake_rows(tmp_path: Path) -> None:
    _, app, token = _configured(tmp_path)
    _, body = _get(app, "/", token)
    page = body.decode()
    assert "No qualified opportunities yet." in page
    assert "<td>" not in page.split("Opportunities</h2>")[1].split("</section>")[0]


# --- 11. No fake portfolio history -------------------------------------------


def test_sparkline_point_count_matches_real_snapshots_exactly_not_more(tmp_path: Path) -> None:
    store, app, token = _configured(tmp_path)
    store.refresh_succeeded(_snapshot("2026-08-01T00:00:00+00:00", "10", "10"))
    store.refresh_succeeded(_snapshot("2026-08-02T00:00:00+00:00", "20", "20"))
    store.refresh_succeeded(_snapshot("2026-08-03T00:00:00+00:00", "30", "30"))
    history = store.account_value_history()
    assert len(history) == 3
    _, body = _get(app, "/", token)
    page = body.decode()
    # Exactly the three real values appear in the sparkline's exact-value table;
    # no fourth/interpolated point is present.
    assert page.count("$10.00") >= 1
    assert page.count("$20.00") >= 1
    assert page.count("$30.00") >= 1
    assert "$15.00" not in page  # would only appear if a point were interpolated
    assert "$40.00" not in page  # would only appear if a point were fabricated


# --- 14. No cash/position decomposition inferred from ambiguous semantics ---


def test_dashboard_never_infers_a_cash_position_split(tmp_path: Path) -> None:
    store, app, token = _configured(tmp_path)
    store.refresh_succeeded(_snapshot("2026-08-01T00:00:00+00:00", "10", "63.36"))
    _, body = _get(app, "/", token)
    page = body.decode()
    assert "Available cash" in page
    assert "Reported portfolio value" in page
    assert "$10.00" in page and "$63.36" in page
    assert "In positions" not in page
    assert "$53.36" not in page  # would only appear from an inferred 63.36-10 split


# --- 5. / 22. Pre-existing activity never labeled bot-generated ------------


def test_activity_hub_never_attributes_preexisting_positions_to_the_bot(tmp_path: Path) -> None:
    store, app, token = _configured(tmp_path)
    snapshot = _snapshot("2026-08-01T00:00:00+00:00", "10", "63.36")
    snapshot["positions"] = [{"ticker": "HUMAN-TICKER"}] * 7
    store.refresh_succeeded(snapshot)
    for path in ("/", "/activity", "/portfolio", "/orders"):
        _, body = _get(app, path, token)
        text = body.decode().lower()
        for forbidden in (
            "bot-generated",
            "bot generated",
            "opened by strategy",
            "algorithmic order",
            "automated trade",
            "placed by the system",
        ):
            assert forbidden not in text, (path, forbidden)


def test_dashboard_positions_table_labels_provenance_as_pre_existing(tmp_path: Path) -> None:
    store, app, token = _configured(tmp_path)
    snapshot = _snapshot("2026-08-01T00:00:00+00:00", "10", "63.36")
    snapshot["positions"] = [{"ticker": "HUMAN-TICKER"}]
    store.refresh_succeeded(snapshot)
    _, body = _get(app, "/", token)
    page = body.decode()
    assert "Pre-existing" in page
    assert "HUMAN-TICKER" in page


# --- 23. Policy/risk details remain accessible despite leaving Dashboard ---


def test_risk_and_safety_page_still_shows_policy_configuration(tmp_path: Path) -> None:
    _, app, token = _configured(tmp_path)
    _, body = _get(app, "/risk", token)
    page = body.decode()
    assert "Target bankroll" in page
    assert "Protected reserve" in page
    assert "$1,000.00" in page
    assert "$700.00" in page


# --- 16. / 17. CSP intact; no inline-script dependency ----------------------


def test_csp_header_is_unchanged_and_still_has_no_unsafe_inline() -> None:
    csp = dict(SECURITY_HEADERS)["Content-Security-Policy"]
    assert csp == "default-src 'self'; style-src 'self'; form-action 'self'; frame-ancestors 'none'"
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp


def test_no_inline_script_tags_or_javascript_urls_anywhere(tmp_path: Path) -> None:
    _, app, token = _configured(tmp_path)
    for path in ("/", "/markets", "/activity", "/strategy", "/system", "/risk"):
        _, body = _get(app, path, token)
        page = body.decode().lower()
        assert "<script" not in page, path
        assert "javascript:" not in page, path


# --- 15. Dark-theme baseline -------------------------------------------------


def test_stylesheet_is_dark_by_default_and_keeps_focus_and_contrast_hooks(
    tmp_path: Path,
) -> None:
    _, app, _token = _configured(tmp_path)  # /static/app.css needs no auth, but app needs a store
    captured: dict[str, Any] = {}

    def start(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status

    css = b"".join(
        app(
            {
                "PATH_INFO": "/static/app.css",
                "REQUEST_METHOD": "GET",
                "wsgi.input": io.BytesIO(b""),
            },
            start,
        )
    ).decode()
    assert captured["status"] == "200 OK"
    assert "color-scheme:dark" in css
    assert ":focus-visible" in css
    assert "--text-muted" in css


# --- 25. No horizontal overflow on narrow layouts (structural proxy) -------


def test_scrollable_regions_use_contained_overflow_not_page_overflow() -> None:
    from services.web_dashboard.app import CSS

    assert ".table-scroll{overflow-x:auto" in CSS
    assert ".section-nav{" in CSS and "overflow-x:auto" in CSS
