"""M23B simplified Dashboard: view model + server-rendered composition.

Kept separate from app.py's routing/session/auth plumbing so the page most
users actually look at stays small, typed, and testable on its own. Nothing
here talks to the network or a database — it composes HTML from data the
caller already fetched and validated.

Two rules this module exists to enforce:

1. Account activity is never presented as bot activity, and never presented
   with *more* confidence than the data actually supports either. Positions,
   orders, and fills carry no ownership/provenance field anywhere in the read
   path today, so every one of them renders as "Unattributed" — never "bot",
   "strategy", "automated", and never "Pre-existing" either, since that is
   also a specific ownership claim ("this predates the bot") that nothing
   persisted currently proves. A manual trade placed after deployment would
   be indistinguishable from an old one without a real provenance field, so
   guessing either way is wrong. Bot P&L/positions/orders show as unavailable
   until real provenance exists.
2. Nothing is fabricated to fill space, and nothing is inferred from a field
   whose exact semantics are not positively validated. An empty or deferred
   concept gets a short, honest empty state, never invented rows, invented
   history, or a conclusion (e.g. "below target bankroll") built by combining
   an unvalidated field with an unrelated policy number.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from services.opportunity_engine.models import DecisionState

from .charts import sparkline
from .product import EvidenceMode, decimal_or_none, dollars
from .readiness import ReadinessCategory, primary_action

# Only these two decision states are the research engine's own affirmative
# candidates; REJECTED/INCOMPLETE/WATCH must never render as an "opportunity".
_LIVE_OPPORTUNITY_DECISION_STATES = frozenset(
    {DecisionState.RESEARCH_CANDIDATE, DecisionState.HIGH_PRIORITY_RESEARCH_CANDIDATE}
)
_DECISION_STATE_LABELS: dict[str, str] = {
    DecisionState.RESEARCH_CANDIDATE: "Research",
    DecisionState.HIGH_PRIORITY_RESEARCH_CANDIDATE: "High-priority research",
}


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


def _provenance_label(row: dict[str, Any]) -> str:
    """Truthful per-item ownership label — never a guess in either direction.

    The persisted account snapshot carries no ownership/baseline field today,
    so every position/order/fill defaults to "Unattributed": it is neither
    claimed as the bot's (no attribution field says so) nor asserted to
    predate the bot (no baseline/provenance field proves that either) — a
    manual trade placed after deployment would look identical to an old one
    without a real field to tell them apart. This stays ready for a future
    explicit `provenance` field ("BOT" / "PRE_EXISTING") without inventing
    one now.
    """
    provenance = row.get("provenance")
    if provenance == "BOT":
        return "Bot owned"
    if provenance == "PRE_EXISTING":
        return "Pre-existing"
    return "Unattributed"


def _eligible_dashboard_candidates(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Only candidates whose persisted mode/decision prove them live-research-eligible.

    A SYNTHETIC/HISTORICAL-REPLAY/fixture-mode candidate, or one the research
    engine itself rejected, marked incomplete, or is only watching, must never
    render on the Dashboard next to "Opportunities" as if it were a current
    live signal — that reads as unsafe/executable when it is neither.
    """
    return [
        row
        for row in candidates
        if row.get("data_mode") == EvidenceMode.LIVE_RESEARCH_DATA
        and row.get("decision_state") in _LIVE_OPPORTUNITY_DECISION_STATES
    ]


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
    positions = list(data.get("positions", []))
    cash_dec = decimal_or_none(data.get("cash"))
    portfolio_value_dec = decimal_or_none(data.get("portfolio_value"))

    # `portfolio_value` semantics have not been positively validated (Kalshi's own
    # materials describe it inconsistently), so nothing is inferred from it beyond
    # showing it as-is — no bankroll comparison, no funded/not-funded conclusion.
    # Target bankroll stays a click away on Risk & Safety.
    hero = (
        "<section class=hero-metric>"
        f"<p class=eyebrow>{html.escape(connection_headline)}</p>"
        f"<p class=hero-value>{html.escape(dollars(portfolio_value_dec))}</p>"
        "<p class=hero-label>Reported portfolio value · "
        "<a class=text-link href=/risk>compare to target bankroll</a></p>"
        + _metric_row(
            (
                MetricItem("Available cash", dollars(cash_dec)),
                MetricItem("Bot P&L", "—", "No attributable live trades yet"),
                MetricItem("Open risk", "Unavailable", "Portfolio risk reconciliation pending"),
                MetricItem("Account positions", str(len(positions)), "Unattributed"),
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

    candidates = _eligible_dashboard_candidates(list(opportunities.get("candidates", [])))[:5]
    if candidates:
        rows = [
            (
                str(row.get("market_ticker", "—")),
                str(row.get("outcome_side", "—")),
                str(row.get("executable_price", "—")),
                str(row.get("fair_probability", "—")),
                str(row.get("raw_difference", "—")),
                _DECISION_STATE_LABELS.get(str(row.get("decision_state", "")), "Research"),
            )
            for row in candidates
        ]
        opportunities_body = (
            _table(
                "Opportunities",
                ("Market", "Side", "Market price", "Model probability", "Edge", "Mode"),
                rows,
            )
            + "<p class=empty-line>Research signals only — no order is authorized.</p>"
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
            (
                str(row.get("ticker") or row.get("market_ticker") or "Account item"),
                _provenance_label(row),
            )
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
        f"{len(positions)} account position(s) on file (ownership unattributed)",
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
