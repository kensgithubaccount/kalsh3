"""M23A — Control Center UX, visualization, and maintainability regression tests.

These cover the new readiness derivation, chart primitives, navigation
grouping, account-value history, and the redesigned Overview page. They are
deliberately separate from account reconciliation/trading logic: nothing
here touches the signer, risk authorization, or order paths.
"""

from __future__ import annotations

import dataclasses
import io
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from services.web_dashboard.app import DashboardApp
from services.web_dashboard.charts import chart_empty_state, composition_bar, limit_bars, sparkline
from services.web_dashboard.product import (
    SURFACES,
    assert_navigation_covers_all_surfaces,
    grouped_navigation,
    status_pill,
)
from services.web_dashboard.readiness import (
    build_readiness,
    primary_action,
    readiness_summary_text,
    unmet_count,
)
from services.web_dashboard.security import SecretBox, hash_password
from services.web_dashboard.store import ACCOUNT_VALUE_HISTORY_LIMIT, StateStore


def _healthy_kwargs() -> dict[str, Any]:
    return dict(
        account_status="healthy",
        stale=False,
        unresolved_gaps=0,
        compliance_hold=False,
        compliance_reason=None,
        globally_halted=False,
        global_halt_reason=None,
    )


def test_readiness_reflects_structural_facts_even_when_every_real_signal_is_clean() -> None:
    readiness = build_readiness(**_healthy_kwargs())
    unmet, total = unmet_count(readiness)
    # Deterministic risk reconciliation, production mutation capability, and
    # bounded autonomy are structurally unmet in this build regardless of how
    # healthy the live signals are: no production-write credential exists,
    # autonomy is off, and M13 read-side reconciliation is not yet complete.
    assert unmet == 3
    assert total == 8
    assert readiness_summary_text(readiness) == f"{unmet} of {total} readiness checks unmet."


def test_readiness_flags_disconnection_staleness_gaps_hold_and_halt() -> None:
    readiness = build_readiness(
        account_status="error",
        stale=True,
        unresolved_gaps=3,
        compliance_hold=True,
        compliance_reason="manual review",
        globally_halted=True,
        global_halt_reason="account state uncertain",
    )
    by_label = {check.label: check for category in readiness for check in category.checks}
    assert not by_label["Real account connected"].met
    assert not by_label["Read-only reconciliation is current"].met
    assert not by_label["No unresolved market-data gaps"].met
    assert "3" in by_label["No unresolved market-data gaps"].detail
    assert not by_label["No compliance hold"].met
    assert by_label["No compliance hold"].detail == "manual review"
    assert not by_label["Global halt is clear"].met
    assert by_label["Global halt is clear"].detail == "account state uncertain"


def test_primary_action_returns_first_unmet_check_in_category_priority_order() -> None:
    readiness = build_readiness(
        account_status="error",
        stale=True,
        unresolved_gaps=0,
        compliance_hold=False,
        compliance_reason=None,
        globally_halted=False,
        global_halt_reason=None,
    )
    action = primary_action(readiness)
    assert action is not None
    assert action.label == "Real account connected"


def test_primary_action_is_none_only_when_every_check_passes() -> None:
    all_met = tuple(
        dataclasses.replace(category, checks=())
        for category in build_readiness(**_healthy_kwargs())
    )
    assert primary_action(all_met) is None


def test_navigation_groups_cover_every_surface_exactly_once() -> None:
    assert_navigation_covers_all_surfaces()
    grouped = [surface for _, surfaces in grouped_navigation() for surface in surfaces]
    assert len(grouped) == len(SURFACES)
    assert {surface.path for surface in grouped} == {surface.path for surface in SURFACES}
    primary_label, primary_surfaces = grouped_navigation()[0]
    assert primary_label is None
    assert [surface.path for surface in primary_surfaces] == ["/"]


def test_status_pill_escapes_text_and_rejects_unknown_tone() -> None:
    rendered = status_pill("<script>alert(1)</script>", "warn")
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert 'class="pill pill-warn"' in rendered
    with pytest.raises(ValueError):
        status_pill("x", "danger")


def test_composition_bar_renders_real_segments_with_escaped_labels_and_table() -> None:
    html_out = composition_bar(
        "Capital composition", [("<b>Cash</b>", Decimal("100")), ("In positions", Decimal("300"))]
    )
    assert "&lt;b&gt;Cash&lt;/b&gt;" in html_out
    assert "<b>Cash</b>" not in html_out
    assert "$100.00" in html_out and "$300.00" in html_out
    assert "<details class=chart-table>" in html_out
    assert 'role="img"' in html_out


def test_composition_bar_is_an_honest_empty_state_when_nothing_is_positive() -> None:
    html_out = composition_bar("Capital composition", [("Cash", Decimal("0"))])
    assert html_out == chart_empty_state(
        "Capital composition: insufficient reconciled data to visualize."
    )
    assert "chart-bar" not in html_out


def test_limit_bars_renders_policy_ceilings_not_usage() -> None:
    html_out = limit_bars(
        "Policy limits",
        [("Protected reserve", Decimal("700")), ("Active allocation", Decimal("300"))],
    )
    assert "$700.00" in html_out and "$300.00" in html_out
    assert 'role="img"' in html_out


def test_limit_bars_empty_state_when_no_limits_given() -> None:
    assert limit_bars("Policy limits", []) == chart_empty_state(
        "Policy limits: no policy limits configured."
    )


def test_sparkline_shows_insufficient_history_below_two_points() -> None:
    assert sparkline("Account equity", []) == chart_empty_state(
        "Account equity: insufficient history to chart. This accumulates automatically after "
        "each successful read-only account reconciliation."
    )
    assert sparkline("Account equity", [("t0", Decimal("100"))]) == chart_empty_state(
        "Account equity: insufficient history to chart. This accumulates automatically after "
        "each successful read-only account reconciliation."
    )


def test_sparkline_renders_real_points_with_exact_value_table() -> None:
    points = [("2026-08-01", Decimal("900")), ("2026-08-02", Decimal("950"))]
    html_out = sparkline("Account equity", points)
    assert "chart-sparkline" in html_out
    assert "$900.00" in html_out and "$950.00" in html_out
    assert "<details class=chart-table>" in html_out


def _snapshot(observed_at: str, cash: str, portfolio_value: str) -> dict[str, Any]:
    return {
        "observed_at": observed_at,
        "cash": Decimal(cash),
        "portfolio_value": Decimal(portfolio_value),
        "positions": [],
        "orders": [],
        "fills": [],
        "settlements": [],
    }


def test_account_value_history_records_real_points_in_order(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    store.refresh_succeeded(_snapshot("2026-08-01T00:00:00+00:00", "900", "1000"))
    store.refresh_succeeded(_snapshot("2026-08-02T00:00:00+00:00", "950", "1050"))
    history = store.account_value_history()
    assert [row["observed_at"] for row in history] == [
        "2026-08-01T00:00:00+00:00",
        "2026-08-02T00:00:00+00:00",
    ]
    assert history[0]["cash"] == "900" and history[1]["portfolio_value"] == "1050"


def test_account_value_history_skips_snapshots_missing_money_fields(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    incomplete = {"observed_at": "2026-08-01T00:00:00+00:00", "cash": None, "portfolio_value": None}
    store.refresh_succeeded(incomplete)
    assert store.account_value_history() == []
    assert store.refresh_state().status == "healthy"


def test_account_value_history_prunes_to_the_configured_limit(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    for index in range(ACCOUNT_VALUE_HISTORY_LIMIT + 5):
        store.refresh_succeeded(_snapshot(f"2026-01-01T00:{index:04d}:00+00:00", "10", "10"))
    history = store.account_value_history()
    assert len(history) == ACCOUNT_VALUE_HISTORY_LIMIT
    assert history[0]["observed_at"] == "2026-01-01T00:0005:00+00:00"
    assert (
        history[-1]["observed_at"]
        == f"2026-01-01T00:{ACCOUNT_VALUE_HISTORY_LIMIT + 4:04d}:00+00:00"
    )


def _configured(tmp_path: Path) -> tuple[StateStore, DashboardApp, str]:
    store = StateStore(tmp_path / "state.db")
    box = SecretBox(b"k" * 32)
    store.set_config("owner", "owner")
    store.set_config("password_hash", hash_password("LongProduction9Password"))
    store.set_config("vault", box.seal(b"read-only"))
    store.set_config("totp_secret", box.seal(b"JBSWY3DPEHPK3PXP"))
    token, _ = store.create_session(int(time.time()))
    return store, DashboardApp(store, box), token


def _get(app: DashboardApp, path: str, token: str) -> bytes:
    captured: dict[str, Any] = {}

    def start(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status

    return b"".join(
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


def test_overview_separates_actual_account_from_policy_target(tmp_path: Path) -> None:
    store, app, token = _configured(tmp_path)
    store.refresh_succeeded(_snapshot("2026-08-01T00:00:00+00:00", "50", "50"))
    body = _get(app, "/", token).decode()
    assert "Actual account" in body
    assert "Policy / target" in body
    assert "Target bankroll" in body and "$1,000.00" in body
    assert "Protected reserve" in body and "$700.00" in body
    assert "$50.00" in body  # the real, reconciled equity/cash — not the policy figures


def test_overview_labels_policy_bankroll_as_not_currently_fundable_when_equity_is_low(
    tmp_path: Path,
) -> None:
    store, app, token = _configured(tmp_path)
    store.refresh_succeeded(_snapshot("2026-08-01T00:00:00+00:00", "50", "50"))
    body = _get(app, "/", token).decode()
    assert "Not currently fundable" in body


def test_overview_does_not_claim_unfundable_when_equity_is_unknown(tmp_path: Path) -> None:
    _, app, token = _configured(tmp_path)
    body = _get(app, "/", token).decode()
    assert "Not currently fundable" not in body
    assert "Funding status unknown" in body


def test_overview_shows_insufficient_history_before_two_real_snapshots(tmp_path: Path) -> None:
    store, app, token = _configured(tmp_path)
    store.refresh_succeeded(_snapshot("2026-08-01T00:00:00+00:00", "50", "50"))
    body = _get(app, "/", token).decode()
    assert "insufficient history to chart" in body
    assert "chart-sparkline" not in body


def test_overview_renders_sparkline_once_two_real_snapshots_exist(tmp_path: Path) -> None:
    store, app, token = _configured(tmp_path)
    store.refresh_succeeded(_snapshot("2026-08-01T00:00:00+00:00", "50", "50"))
    store.refresh_succeeded(_snapshot("2026-08-02T00:00:00+00:00", "75", "80"))
    body = _get(app, "/", token).decode()
    assert "chart-sparkline" in body
    assert "insufficient history to chart" not in body


def test_overview_readiness_checklist_lists_every_category(tmp_path: Path) -> None:
    _, app, token = _configured(tmp_path)
    body = _get(app, "/", token).decode()
    for category in (
        "Connection",
        "Research readiness",
        "Risk readiness",
        "Execution readiness",
        "Autonomy readiness",
    ):
        assert category in body
    assert "readiness-checklist" in body


def test_overview_never_attributes_preexisting_account_activity_to_the_bot(tmp_path: Path) -> None:
    store, app, token = _configured(tmp_path)
    snapshot = _snapshot("2026-08-01T00:00:00+00:00", "50", "50")
    snapshot["positions"] = [{"ticker": "HUMAN-TICKER", "market_ticker": "HUMAN-TICKER"}]
    snapshot["fills"] = [{"ticker": "HUMAN-TICKER"}]
    store.refresh_succeeded(snapshot)
    for path in ("/", "/portfolio", "/orders"):
        body = _get(app, path, token).decode()
        for forbidden in (
            "bot-generated",
            "bot generated",
            "opened by strategy",
            "algorithmic order",
            "automated trade",
            "placed by the system",
        ):
            assert forbidden not in body.lower(), (path, forbidden)


def test_overview_still_states_no_production_order_capability(tmp_path: Path) -> None:
    _, app, token = _configured(tmp_path)
    body = _get(app, "/", token).decode()
    assert "Can it trade?" in body
    assert "<strong>NO</strong>" in body
    assert "Production-write credential: NONE" in body
    assert "signer: DISARMED" in body
    assert "Autonomy: OFF" in body
