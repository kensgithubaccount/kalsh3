"""Structured, derived readiness state for the Control Center.

Every check here is computed from the same real signals `derive_global_state`
already uses (account connection, staleness, data gaps, compliance hold,
global halt), plus this build's fixed structural facts: no production-write
credential exists and bounded autonomy is off. Nothing is fabricated per
request; this is a readable breakdown of the same fail-closed truth already
rendered elsewhere in the product, grouped so an operator can see exactly
which category is blocking trading and why.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReadinessCheck:
    label: str
    met: bool
    detail: str


@dataclass(frozen=True, slots=True)
class ReadinessCategory:
    name: str
    checks: tuple[ReadinessCheck, ...]

    @property
    def met(self) -> bool:
        return all(check.met for check in self.checks)


def build_readiness(
    *,
    account_status: str,
    stale: bool,
    unresolved_gaps: int,
    universe_status: str,
    realtime_state: str,
    compliance_state: str,
    compliance_reason: str | None,
    globally_halted: bool,
    global_halt_reason: str | None,
    real_settled_events: int,
    promotion_minimum: int,
) -> tuple[ReadinessCategory, ...]:
    connected = account_status in {"healthy", "connected"}
    connection_detail = (
        "Read-only account gateway reachable and reconciled"
        if connected
        else f"Account status: {account_status}"
    )
    freshness_detail = (
        "Last successful read is within the freshness window"
        if not stale
        else "Account data is stale or has never successfully reconciled"
    )
    # Zero unresolved gaps must never look like a healthy research state on its own —
    # a market-data system that never started or is disconnected has no gaps to
    # report yet, which is a different fact than "gaps were found and resolved".
    universe_initialized = universe_status != "NOT_STARTED"
    universe_detail = (
        "Market universe is initialized"
        if universe_initialized
        else f"Market universe status: {universe_status}"
    )
    market_data_connected = realtime_state == "HEALTHY"
    market_data_detail = (
        "Live market data is connected"
        if market_data_connected
        else f"Market data status: {realtime_state}"
    )
    gap_detail = (
        "No unresolved market-data gaps"
        if unresolved_gaps == 0
        else f"{unresolved_gaps} unresolved market-data gap(s)"
    )
    # Reuses the same governed promotion_minimum threshold already shown on /learning;
    # this check is informational only and never changes promotion, strategy, risk,
    # execution, or autonomy behavior.
    evidence_sufficient = promotion_minimum > 0 and real_settled_events >= promotion_minimum
    evidence_detail = (
        f"{real_settled_events} / {promotion_minimum} relevant real settled events"
        if promotion_minimum > 0
        else "No governed evidence threshold is configured"
    )
    # Distinguish "no hold has ever been recorded" (compliance_state == UNKNOWN, the
    # store's own default) from an actual active hold with a real reason — both are
    # "not clear", but they are not the same fact and must not read the same way.
    compliance_clear = compliance_state == "CLEAR"
    if compliance_clear:
        compliance_detail = "Compliance state is established and clear"
    elif compliance_state == "UNKNOWN":
        compliance_detail = "Compliance state has not yet been established"
    else:
        compliance_detail = compliance_reason or f"Compliance hold is active: {compliance_state}"
    halt_detail = "Global halt is not active"
    if globally_halted:
        halt_detail = global_halt_reason or "Global halt is active"
    return (
        ReadinessCategory(
            "Connection",
            (
                ReadinessCheck("Real account connected", connected, connection_detail),
                ReadinessCheck("Read-only reconciliation is current", not stale, freshness_detail),
            ),
        ),
        ReadinessCategory(
            "Research readiness",
            (
                ReadinessCheck(
                    "Market universe initialized", universe_initialized, universe_detail
                ),
                ReadinessCheck(
                    "Live market data connected", market_data_connected, market_data_detail
                ),
                ReadinessCheck("No unresolved market-data gaps", unresolved_gaps == 0, gap_detail),
                ReadinessCheck(
                    "Required real evidence sufficient", evidence_sufficient, evidence_detail
                ),
            ),
        ),
        ReadinessCategory(
            "Risk readiness",
            (
                ReadinessCheck(
                    "Compliance state established and clear", compliance_clear, compliance_detail
                ),
                ReadinessCheck("Global halt is clear", not globally_halted, halt_detail),
                ReadinessCheck(
                    "Deterministic portfolio risk reconciliation complete",
                    False,
                    "Real-time exposure and loss-window reconciliation is not yet available",
                ),
            ),
        ),
        ReadinessCategory(
            "Execution readiness",
            (
                ReadinessCheck(
                    "Production mutation capability installed",
                    False,
                    "Production-write credential: NONE; signer: DISARMED",
                ),
            ),
        ),
        ReadinessCategory(
            "Autonomy readiness",
            (
                ReadinessCheck(
                    "Bounded autonomy armed",
                    False,
                    "Autonomy: OFF; a separate future human governance decision is required",
                ),
            ),
        ),
    )


def primary_action(categories: tuple[ReadinessCategory, ...]) -> ReadinessCheck | None:
    """Return the first unmet check in priority order, or None if all pass."""
    for category in categories:
        for check in category.checks:
            if not check.met:
                return check
    return None


def unmet_count(categories: tuple[ReadinessCategory, ...]) -> tuple[int, int]:
    checks = [check for category in categories for check in category.checks]
    return sum(1 for check in checks if not check.met), len(checks)


def readiness_summary_text(categories: tuple[ReadinessCategory, ...]) -> str:
    unmet, total = unmet_count(categories)
    if unmet == 0:
        return "All readiness checks pass for this read-only build."
    return f"{unmet} of {total} readiness checks unmet."
