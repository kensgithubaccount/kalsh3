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
        "/", "Overview", "System-wide state and safety posture", EvidenceMode.NOT_AVAILABLE
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
        "/sources", "Sources", "Source health and provenance", EvidenceMode.LIVE_RESEARCH_DATA
    ),
    ProductSurface("/learning", "Learning", "Research governance", EvidenceMode.HISTORICAL_REPLAY),
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
        "/risk", "Risk & Safety", "Deterministic limits and blockers", EvidenceMode.NOT_AVAILABLE
    ),
    ProductSurface(
        "/system", "System", "Health, replay, and data quality", EvidenceMode.NOT_AVAILABLE
    ),
    ProductSurface(
        "/advanced",
        "Advanced",
        "Forecast, replay, and execution research diagnostics",
        EvidenceMode.HISTORICAL_REPLAY,
    ),
)


NAV_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Research", ("/opportunities", "/breaking", "/markets", "/sources", "/learning")),
    ("Account", ("/portfolio", "/orders", "/reports")),
    ("Safety", ("/risk",)),
    ("System", ("/system", "/advanced")),
)


def grouped_navigation() -> tuple[tuple[str | None, tuple[ProductSurface, ...]], ...]:
    """Group SURFACES for progressive-disclosure navigation without hiding any page.

    Every surface still appears exactly once, in its original SURFACES order;
    this only changes how the navigation is visually sectioned.
    """
    by_path = {surface.path: surface for surface in SURFACES}
    grouped_paths = {path for _, paths in NAV_GROUPS for path in paths}
    primary = tuple(surface for surface in SURFACES if surface.path not in grouped_paths)
    groups = tuple((label, tuple(by_path[path] for path in paths)) for label, paths in NAV_GROUPS)
    return ((None, primary), *groups)


def assert_navigation_covers_all_surfaces() -> None:
    grouped = tuple(surface for _, surfaces in grouped_navigation() for surface in surfaces)
    if len(grouped) != len(SURFACES) or set(grouped) != set(SURFACES):
        raise RuntimeError("navigation grouping dropped or duplicated a product surface")


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


_PILL_TONES = frozenset({"good", "warn", "bad", "neutral"})


def status_pill(text: str, tone: str) -> str:
    """One centralized, escaped status-label renderer so every page shares markup.

    `tone` selects styling only; the visible text always carries the meaning
    so nothing is conveyed by color alone.
    """
    if tone not in _PILL_TONES:
        raise ValueError(f"unknown status pill tone: {tone!r}")
    return f'<span class="pill pill-{tone}">{html.escape(text)}</span>'
