"""M23B simplified Dashboard: view model + server-rendered composition.

Kept separate from app.py's routing/session/auth plumbing so the page most
users actually look at stays small, typed, and testable on its own. Nothing
here talks to the network or a database — it composes HTML from data the
caller already fetched and validated.

Two rules this module exists to enforce:

1. Account activity is never presented as bot activity. Every position, order,
   and fill already on the account predates this product and carries no
   attribution field anywhere in the read path, so every one of them is
   labeled "Pre-existing" here — never "bot", "strategy", or "automated".
   Bot P&L/positions/orders show as unavailable until real provenance exists.
2. Nothing is fabricated to fill space. An empty or deferred concept gets a
   short, honest empty state, never invented rows, invented history, or an
   inferred number built from ambiguous fields.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from services.risk_engine.policy import RiskPolicy

from .charts import sparkline
from .product import decimal_or_none, dollars, status_pill
from .readiness import ReadinessCategory, primary_action


@dataclass(frozen=True, slots=True)
class MetricItem:
    label: str
    value: str
    note: str = ""


@dataclass(frozen=True, slots=True)
class StatusStripItem:
    label: str
    state: str
    tone: str
    href: str


def _metric_row(items: tuple[MetricItem, ...]) -> str:
    cells = []
    for item in items:
        note = f"<span class=metric-note>{html.escape(item.note)}</span>" if item.note else ""
        cells.append(
            f"<div class=metric><span class=metric-label>{html.escape(item.label)}</span>"
            f"<span class=metric-value>{html.escape(item.value)}</span>{note}</div>"
        )
    return f"<div class=metric-row>{''.join(cells)}</div>"


def _status_strip(items: tuple[StatusStripItem, ...]) -> str:
    cells = "".join(
        f'<a class="strip-item strip-{item.tone}" href="{item.href}">'
        f"<span class=strip-label>{html.escape(item.label)}</span>"
        f"<span class=strip-state>{html.escape(item.state)}</span></a>"
        for item in items
    )
    return f'<div class=status-strip role=list aria-label="System status summary">{cells}</div>'


def _table(caption: str, headers: tuple[str, ...], rows: Sequence[tuple[str, ...]]) -> str:
    head = "".join(f"<th scope=col>{html.escape(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in row) + "</tr>" for row in rows
    )
    return (
        "<div class=table-scroll><table class=data-table>"
        f"<caption class=sr-only>{html.escape(caption)}</caption>"
        f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"
    )


def build_status_strip(readiness: tuple[ReadinessCategory, ...], realtime_state: str) -> str:
    by_name = {category.name: category for category in readiness}
    connection_ok = by_name["Connection"].met
    research = by_name["Research readiness"]
    market_data_check = next(
        (c for c in research.checks if c.label == "Live market data connected"), None
    )
    evidence_check = next(
        (c for c in research.checks if c.label == "Required real evidence sufficient"), None
    )
    items = (
        StatusStripItem(
            "Account",
            "✓ OK" if connection_ok else "✕ NEEDS ATTENTION",
            "good" if connection_ok else "bad",
            "/system",
        ),
        StatusStripItem(
            "Market data",
            ("✓ OK" if market_data_check.met else f"✕ {html.escape(realtime_state)}")
            if market_data_check
            else "◌ UNKNOWN",
            "good" if (market_data_check and market_data_check.met) else "bad",
            "/system",
        ),
        StatusStripItem("Risk", "◌ PENDING", "warn", "/risk"),
        StatusStripItem(
            "Evidence",
            evidence_check.detail.split(" relevant")[0] if evidence_check else "—",
            "good" if (evidence_check and evidence_check.met) else "neutral",
            "/learning",
        ),
        StatusStripItem("Trading", "OFF", "neutral", "/risk"),
    )
    return _status_strip(items)


def build_dashboard(
    *,
    connection_headline: str,
    data: dict[str, Any],
    universe: dict[str, Any],
    realtime: dict[str, Any],
    opportunities: dict[str, Any],
    readiness: tuple[ReadinessCategory, ...],
    account_history: list[dict[str, Any]],
) -> str:
    policy = RiskPolicy()
    positions = list(data.get("positions", []))
    cash_dec = decimal_or_none(data.get("cash"))
    portfolio_value_dec = decimal_or_none(data.get("portfolio_value"))

    below_target = None if portfolio_value_dec is None else portfolio_value_dec < policy.bankroll
    hero_note = ""
    if below_target is True:
        hero_note = status_pill("Below target bankroll", "warn")
    elif below_target is None:
        hero_note = status_pill("Funding status unknown", "neutral")
    hero = (
        "<section class=hero-metric>"
        f"<p class=eyebrow>{html.escape(connection_headline)}</p>"
        f"<p class=hero-value>{html.escape(dollars(portfolio_value_dec))}</p>"
        f"<p class=hero-label>Reported portfolio value{' · ' + hero_note if hero_note else ''}</p>"
        + _metric_row(
            (
                MetricItem("Available cash", dollars(cash_dec)),
                MetricItem("Bot P&L", "—", "No attributable live trades yet"),
                MetricItem("Open risk", "Unavailable", "Portfolio risk reconciliation pending"),
                MetricItem("Account positions", str(len(positions)), "Pre-existing"),
            )
        )
        + "</section>"
    )

    history_points: list[tuple[str, Decimal]] = []
    for row in account_history:
        observed, value = row.get("observed_at"), decimal_or_none(row.get("portfolio_value"))
        if isinstance(observed, str) and value is not None:
            history_points.append((observed, value))
    chart_section = (
        "<section aria-labelledby=chart-heading><h2 id=chart-heading>Portfolio value</h2>"
        f"{sparkline('Reported portfolio value', history_points)}</section>"
    )

    candidates = list(opportunities.get("candidates", []))[:5]
    if candidates:
        rows = [
            (
                str(row.get("market_ticker", "—")),
                str(row.get("outcome_side", "—")),
                str(row.get("executable_price", "—")),
                str(row.get("fair_probability", "—")),
                str(row.get("raw_difference", "—")),
                str(row.get("decision_state", "—")),
            )
            for row in candidates
        ]
        opportunities_body = _table(
            "Opportunities",
            ("Market", "Side", "Market price", "Model probability", "Edge", "Status"),
            rows,
        )
    else:
        why = "" if universe.get("status") != "NOT_STARTED" else " Market universe has not started."
        opportunities_body = (
            f"<p class=empty-line>No qualified opportunities yet.{html.escape(why)}</p>"
        )
    opportunities_section = (
        "<section aria-labelledby=opportunities-heading>"
        "<h2 id=opportunities-heading>Opportunities</h2>"
        f"{opportunities_body}"
        "<a class=text-link href=/opportunities>All opportunities</a></section>"
    )

    if positions:
        position_rows = [
            (str(row.get("ticker") or row.get("market_ticker") or "Account item"), "Pre-existing")
            for row in positions[:10]
        ]
        positions_body = _table("Positions", ("Market", "Provenance"), position_rows)
    else:
        positions_body = "<p class=empty-line>No positions on file.</p>"
    positions_section = (
        "<section aria-labelledby=positions-heading><h2 id=positions-heading>Positions</h2>"
        f"{positions_body}"
        "<a class=text-link href=/activity>All activity</a></section>"
    )

    orders = data.get("orders", [])
    fills = data.get("fills", [])
    settlements = data.get("settlements", [])
    facts = [
        f"{len(positions)} pre-existing account position(s) on file",
        f"{len(orders)} account order(s) on file",
        f"{len(fills)} account fill(s) on file",
        f"{len(settlements)} settlement(s) on file",
        "0 bot-attributed fill(s)",
    ]
    if realtime.get("unresolved_gaps"):
        facts.append(f"{realtime['unresolved_gaps']} unresolved market-data gap(s)")
    activity_section = (
        "<section aria-labelledby=activity-heading><h2 id=activity-heading>Activity</h2>"
        "<ul class=fact-list>"
        + "".join(f"<li>{html.escape(item)}</li>" for item in facts)
        + "</ul></section>"
    )

    action = primary_action(readiness)
    attention_note = (
        "<p class=dashboard-note>Needs attention: "
        f'<a href="/system">{html.escape(action.label)}</a></p>'
        if action is not None
        else ""
    )

    return (
        hero
        + attention_note
        + chart_section
        + opportunities_section
        + positions_section
        + activity_section
    )
