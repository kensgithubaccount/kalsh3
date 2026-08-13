"""M12 product-surface inventory and cross-product truth labels."""

from __future__ import annotations

import html
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any


class EvidenceMode(StrEnum):
    LIVE_RESEARCH_DATA = "LIVE RESEARCH DATA"
    HISTORICAL_REPLAY = "HISTORICAL REPLAY"
    SYNTHETIC_TEST = "SYNTHETIC TEST"
    NOT_AVAILABLE = "NOT AVAILABLE"


class GlobalProductState(StrEnum):
    LEARNING = "LEARNING"
    READY_FOR_APPROVAL = "READY FOR APPROVAL"
    AWAITING_APPROVAL = "AWAITING APPROVAL"
    TRADING = "TRADING"
    PAUSED = "PAUSED"
    NEEDS_ATTENTION = "NEEDS ATTENTION"
    HALTED = "HALTED"


@dataclass(frozen=True, slots=True)
class ProductSurface:
    path: str
    label: str
    purpose: str
    evidence_mode: EvidenceMode
    production_write: bool = False


SURFACES = (
    ProductSurface(
        "/",
        "Dashboard",
        "Account value, performance, and what needs attention",
        EvidenceMode.NOT_AVAILABLE,
    ),
    ProductSurface(
        "/opportunities",
        "Opportunities",
        "After-cost research candidates",
        EvidenceMode.HISTORICAL_REPLAY,
    ),
    ProductSurface(
        "/breaking", "Breaking Now", "Validated signal intake", EvidenceMode.LIVE_RESEARCH_DATA
    ),
    ProductSurface(
        "/markets",
        "Markets",
        "Universe, market data, and semantics",
        EvidenceMode.LIVE_RESEARCH_DATA,
    ),
    ProductSurface(
        "/activity",
        "Activity",
        "Account positions, orders, fills, and reports",
        EvidenceMode.NOT_AVAILABLE,
    ),
    ProductSurface(
        "/portfolio", "Portfolio", "Read-only account state", EvidenceMode.NOT_AVAILABLE
    ),
    ProductSurface(
        "/orders",
        "Orders & Trades",
        "Read-only lifecycle and reconciliation",
        EvidenceMode.NOT_AVAILABLE,
    ),
    ProductSurface(
        "/reports", "Reports", "Operating and governance reports", EvidenceMode.NOT_AVAILABLE
    ),
    ProductSurface(
        "/strategy",
        "Strategy",
        "Forecasting, learning, sources, and research diagnostics",
        EvidenceMode.HISTORICAL_REPLAY,
    ),
    ProductSurface("/learning", "Learning", "Research governance", EvidenceMode.HISTORICAL_REPLAY),
    ProductSurface(
        "/sources", "Sources", "Source health and provenance", EvidenceMode.LIVE_RESEARCH_DATA
    ),
    ProductSurface(
        "/advanced",
        "Advanced",
        "Forecast, replay, and execution research diagnostics",
        EvidenceMode.HISTORICAL_REPLAY,
    ),
    ProductSurface(
        "/system",
        "System",
        "Health, readiness, replay, and data quality",
        EvidenceMode.NOT_AVAILABLE,
    ),
    ProductSurface(
        "/risk", "Risk & Safety", "Deterministic limits and blockers", EvidenceMode.NOT_AVAILABLE
    ),
)


@dataclass(frozen=True, slots=True)
class NavSection:
    key: str
    label: str
    path: str
    members: tuple[str, ...]


NAV_SECTIONS: tuple[NavSection, ...] = (
    NavSection("dashboard", "Dashboard", "/", ("/",)),
    NavSection("markets", "Markets", "/markets", ("/markets", "/opportunities", "/breaking")),
    NavSection(
        "activity", "Activity", "/activity", ("/activity", "/portfolio", "/orders", "/reports")
    ),
    NavSection(
        "strategy",
        "Strategy",
        "/strategy",
        ("/strategy", "/learning", "/sources", "/forecasting", "/backtests", "/advanced"),
    ),
    NavSection("system", "System", "/system", ("/system", "/risk")),
)


def section_for_path(path: str) -> NavSection:
    """Return the top-level nav section a page belongs to, defaulting to Dashboard.

    Detail pages (e.g. `/markets/TICKER`, `/breaking/SIGNAL`) belong to their
    parent section via prefix match.
    """
    for section in NAV_SECTIONS:
        for member in section.members:
            if path == member or (member != "/" and path.startswith(member + "/")):
                return section
    return NAV_SECTIONS[0]


def assert_navigation_covers_all_surfaces() -> None:
    """Every reachable SURFACES path must belong to exactly one top-level section."""
    section_paths = {surface.path for surface in SURFACES}
    covered = {member for section in NAV_SECTIONS for member in section.members}
    missing = section_paths - covered
    if missing:
        raise RuntimeError(f"navigation sections do not cover surfaces: {sorted(missing)}")


ADVANCED_SURFACES = (
    ProductSurface(
        "/forecasting", "Forecasting", "Frozen research forecasts", EvidenceMode.HISTORICAL_REPLAY
    ),
    ProductSurface(
        "/backtests", "Backtests", "Historical execution simulation", EvidenceMode.HISTORICAL_REPLAY
    ),
)


def assert_non_mutating_surfaces() -> None:
    if any(surface.production_write for surface in (*SURFACES, *ADVANCED_SURFACES)):
        raise RuntimeError("M12 surface inventory cannot enable production writes")


def derive_global_state(
    *,
    account_status: str,
    stale: bool,
    unresolved_gaps: int,
    compliance_hold: bool = False,
    globally_halted: bool = False,
) -> GlobalProductState:
    """Return exactly one fail-closed owner-facing state.

    M12 has no write capability, so it can never derive approval or trading states.
    """
    if globally_halted or compliance_hold:
        return GlobalProductState.HALTED
    if stale or unresolved_gaps or account_status not in {"healthy", "connected"}:
        return GlobalProductState.NEEDS_ATTENTION
    return GlobalProductState.LEARNING


def dollars(value: Any) -> str:
    if value is None or value == "—":
        return "Unavailable"
    try:
        return f"${Decimal(str(value)):,.2f}"
    except (InvalidOperation, TypeError, ValueError):
        return "Unavailable"


def decimal_or_none(value: Any) -> Decimal | None:
    """Parse a real reconciled value into Decimal, or None if it is absent/invalid.

    Never fabricates a number: unparsable input stays None so callers show an
    honest empty state instead of a guessed figure.
    """
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


_PILL_TONES = frozenset({"good", "warn", "bad", "neutral"})


def status_pill(text: str, tone: str) -> str:
    """One centralized, escaped status-label renderer so every page shares markup.

    `tone` selects styling only; the visible text always carries the meaning
    so nothing is conveyed by color alone.
    """
    if tone not in _PILL_TONES:
        raise ValueError(f"unknown status pill tone: {tone!r}")
    return f'<span class="pill pill-{tone}">{html.escape(text)}</span>'
