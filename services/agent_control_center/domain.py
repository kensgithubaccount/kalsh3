"""Immutable M26A agent registry and research decision receipts.

These objects describe research. They have no execution dependency and cannot
authorize, size, sign, send, or cancel an order.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum


class ImplementationAvailability(StrEnum):
    PLANNED = "PLANNED"
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


class AutonomyMode(StrEnum):
    """M26A deliberately has no live-production member."""

    DISABLED = "DISABLED"
    SHADOW = "SHADOW"
    PAPER = "PAPER"
    CANDIDATE = "CANDIDATE"


class ResearchDecision(StrEnum):
    WOULD_TRADE = "WOULD_TRADE"
    NO_TRADE = "NO_TRADE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    BLOCKED_BY_RISK = "BLOCKED_BY_RISK"


ZERO_INFLUENCE = Decimal("0")


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    agent_id: str
    display_name: str
    version: str
    mandate: str
    market_universe: str
    inputs_sources: str
    watches: str
    belief: str
    act_when: str
    principal_risks: tuple[str, ...]
    availability: ImplementationAvailability
    autonomy_mode: AutonomyMode
    production_influence: Decimal
    risk_limit_summary: str
    performance_availability: str

    def __post_init__(self) -> None:
        if not self.agent_id or self.agent_id.lower() != self.agent_id:
            raise ValueError("agent_id must be a stable lowercase identifier")
        if self.production_influence != ZERO_INFLUENCE:
            raise ValueError("M26A agents must have exactly zero production influence")
        if self.availability is not ImplementationAvailability.AVAILABLE and (
            self.autonomy_mode is not AutonomyMode.DISABLED
        ):
            raise ValueError("unavailable or planned agents must be disabled")


_NO_PERFORMANCE = "Not enough evidence"
_NO_AUTHORITY = (
    "Research only. Deterministic risk checks fail closed; this agent cannot authorize an order."
)


AGENT_REGISTRY: tuple[AgentDefinition, ...] = (
    AgentDefinition(
        "event-edge",
        "Event Edge",
        "1.0.0",
        "Find prediction-market prices that differ from supported, cost-adjusted forecasts.",
        "Strategy-supported event contracts with validated semantics.",
        "Forecasting, contract intelligence, market books, fees, and slippage research.",
        "Validated contracts, forecasts, executable prices, fees, liquidity, and evidence quality.",
        "A conservative fair probability may differ from an executable market price after costs.",
        "Evidence, semantics, freshness, liquidity, cost, and deterministic risk gates all pass.",
        ("Model error", "Stale books", "Fee or slippage error", "Contract ambiguity"),
        ImplementationAvailability.AVAILABLE,
        AutonomyMode.SHADOW,
        ZERO_INFLUENCE,
        _NO_AUTHORITY,
        _NO_PERFORMANCE,
    ),
    AgentDefinition(
        "breaking-signals",
        "Breaking Signals",
        "1.0.0",
        "Surface validated, time-sensitive evidence relevant to known event contracts.",
        "Configured external signals matched to semantically validated event contracts.",
        "Breaking-signal adapters, source provenance, deduplication, and contract matching.",
        "New claims, corrections, source independence, manipulation flags, and market movement.",
        "Independent evidence may change the supported view before the market fully reacts.",
        "The signal clears validation before it becomes stale; research gates still pass.",
        ("False reports", "Recirculated claims", "Late signals", "Incorrect contract matching"),
        ImplementationAvailability.AVAILABLE,
        AutonomyMode.SHADOW,
        ZERO_INFLUENCE,
        _NO_AUTHORITY,
        _NO_PERFORMANCE,
    ),
    AgentDefinition(
        "perps",
        "Perps",
        "1.0.0",
        "Study perpetual-futures market evidence without influencing production.",
        "Kalshi Perps instruments with ticker-only identity; observation is separately operated.",
        "M24 immutable shadow observations and book evidence. M26A accesses no production data.",
        "Offline/replayed Perps books, sequence integrity, liquidity, and market state.",
        "Audited Perps evidence may eventually support a cost-aware research conclusion.",
        "A future strategy exists and all evidence, cost, and deterministic risk gates pass.",
        ("Unimplemented strategy", "Sequence gaps", "Thin liquidity", "Margin-specific risk"),
        ImplementationAvailability.UNAVAILABLE,
        AutonomyMode.DISABLED,
        ZERO_INFLUENCE,
        _NO_AUTHORITY,
        _NO_PERFORMANCE,
    ),
    AgentDefinition(
        "cross-market",
        "Cross-Market",
        "1.0.0",
        "Compare related prices across venues for research without allocating capital.",
        "Semantically matched markets across supported venues.",
        "Opportunity-engine cross-venue research contracts and point-in-time evidence.",
        "Price disagreement, semantic equivalence, executable depth, costs, and leg risk.",
        "A conservative discrepancy survives costs and one-leg failure assumptions.",
        "Both legs and settlement semantics are verified; M26A cannot allocate or execute.",
        ("One-leg exposure", "Semantic mismatch", "Venue latency", "Unverified fees"),
        ImplementationAvailability.AVAILABLE,
        AutonomyMode.SHADOW,
        ZERO_INFLUENCE,
        _NO_AUTHORITY,
        _NO_PERFORMANCE,
    ),
    AgentDefinition(
        "resolution",
        "Resolution",
        "1.0.0",
        "Identify contract-resolution ambiguity and settlement evidence risk.",
        "Event contracts supported by deterministic contract intelligence.",
        "Rules, authorities, deadlines, revisions, corrections, and cited evidence.",
        "Settlement wording, authoritative sources, contradictions, and unresolved semantics.",
        "A contract interpretation is sufficiently supported to permit downstream research.",
        "It does not trade; it may only support or block research eligibility.",
        ("Ambiguous rules", "Authority revisions", "Timezone errors", "Citation gaps"),
        ImplementationAvailability.AVAILABLE,
        AutonomyMode.SHADOW,
        ZERO_INFLUENCE,
        _NO_AUTHORITY,
        _NO_PERFORMANCE,
    ),
    AgentDefinition(
        "portfolio",
        "Portfolio",
        "1.0.0",
        "Provide a reconciled, portfolio-aware research view.",
        "Account positions and aggregate exposure when complete reconciliation exists.",
        "Read-only account state and deterministic risk ledgers.",
        "Positions, concentration, correlation, reserves, and unresolved activity.",
        "Existing exposure permits a candidate under deterministic limits.",
        "Complete fresh reconciliation and a future reviewed integration are available.",
        ("Incomplete reconciliation", "Unknown external activity", "Correlation error"),
        ImplementationAvailability.UNAVAILABLE,
        AutonomyMode.DISABLED,
        ZERO_INFLUENCE,
        _NO_AUTHORITY,
        _NO_PERFORMANCE,
    ),
    AgentDefinition(
        "learning",
        "Learning",
        "1.0.0",
        "Evaluate research components and propose reviewable changes from settled evidence.",
        "Forecast, source, model, and execution-research evaluation datasets.",
        "Point-in-time outcomes, calibration, ablation, uncertainty, and governance records.",
        "Calibration, contribution, redundancy, drift, sample sufficiency, and regressions.",
        "A bounded proposal has sufficient evidence for explicit human review.",
        "It never rewrites risk, credentials, execution controls, or production code.",
        ("Small samples", "Multiple testing", "Regime change", "Overfitting"),
        ImplementationAvailability.AVAILABLE,
        AutonomyMode.CANDIDATE,
        ZERO_INFLUENCE,
        _NO_AUTHORITY,
        _NO_PERFORMANCE,
    ),
)


def _validate_registry() -> None:
    ids = tuple(agent.agent_id for agent in AGENT_REGISTRY)
    if len(ids) != len(set(ids)):
        raise RuntimeError("agent registry IDs must be unique")


_validate_registry()


def agent_by_id(agent_id: str) -> AgentDefinition | None:
    return next((agent for agent in AGENT_REGISTRY if agent.agent_id == agent_id), None)


@dataclass(frozen=True, slots=True)
class DecisionReceipt:
    receipt_id: str
    created_at: datetime
    agent_id: str
    agent_version: str
    instrument_id: str
    observed_market_price: Decimal | None
    model_probability: Decimal | None
    fair_value: Decimal | None
    raw_edge: Decimal | None
    estimated_fees: Decimal | None
    estimated_slippage: Decimal | None
    after_cost_edge: Decimal | None
    confidence: Decimal | None
    evidence_references: tuple[str, ...]
    current_exposure: Decimal | None
    applicable_limits: tuple[tuple[str, Decimal], ...]
    risk_check_results: tuple[str, ...]
    decision: ResearchDecision
    rejected_alternatives: tuple[str, ...]
    rejection_reasons: tuple[str, ...]
    production_influence: Decimal = ZERO_INFLUENCE

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        if self.production_influence != ZERO_INFLUENCE:
            raise ValueError("decision receipts must have exactly zero production influence")
        definition = agent_by_id(self.agent_id)
        if definition is None:
            raise ValueError("decision receipt agent_id is not registered")
        if self.agent_version != definition.version:
            raise ValueError("decision receipt agent version does not match the registry")
        if definition.availability is not ImplementationAvailability.AVAILABLE:
            raise ValueError("decision receipt agent is not available")
        if definition.autonomy_mode is AutonomyMode.DISABLED:
            raise ValueError("decision receipt agent autonomy is disabled")
        if self.decision is ResearchDecision.WOULD_TRADE and self.rejection_reasons:
            raise ValueError("WOULD_TRADE cannot include rejection reasons")

    def to_json(self) -> str:
        def decimal_text(value: Decimal | None) -> str | None:
            return None if value is None else str(value)

        payload = {
            "after_cost_edge": decimal_text(self.after_cost_edge),
            "agent_id": self.agent_id,
            "agent_version": self.agent_version,
            "applicable_limits": [[name, str(value)] for name, value in self.applicable_limits],
            "confidence": decimal_text(self.confidence),
            "created_at": self.created_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "current_exposure": decimal_text(self.current_exposure),
            "decision": self.decision,
            "estimated_fees": decimal_text(self.estimated_fees),
            "estimated_slippage": decimal_text(self.estimated_slippage),
            "evidence_references": list(self.evidence_references),
            "fair_value": decimal_text(self.fair_value),
            "instrument_id": self.instrument_id,
            "model_probability": decimal_text(self.model_probability),
            "observed_market_price": decimal_text(self.observed_market_price),
            "production_influence": str(self.production_influence),
            "raw_edge": decimal_text(self.raw_edge),
            "receipt_id": self.receipt_id,
            "rejected_alternatives": list(self.rejected_alternatives),
            "rejection_reasons": list(self.rejection_reasons),
            "risk_check_results": list(self.risk_check_results),
        }
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def explain_decision(receipt: DecisionReceipt, agent_name: str | None = None) -> str:
    """Generate owner-facing text using receipt fields only."""
    definition = agent_by_id(receipt.agent_id)
    if definition is None:  # Defensive if a caller bypassed normal dataclass construction.
        raise ValueError("decision receipt agent_id is not registered")
    name = agent_name or definition.display_name
    edge = (
        f" sees a {(receipt.after_cost_edge * Decimal('100')):.1f}% after-cost edge"
        if receipt.after_cost_edge is not None
        else " reviewed the available evidence"
    )
    reason = receipt.rejection_reasons[0] if receipt.rejection_reasons else None
    if receipt.decision is ResearchDecision.WOULD_TRADE:
        return (
            f"{name}{edge}, so its research conclusion is that it would trade. "
            "No order is authorized."
        )
    if receipt.decision is ResearchDecision.BLOCKED_BY_RISK:
        why = reason or "a deterministic risk check did not pass"
        return f"{name}{edge}, but {why}, so it is blocked by risk and would not trade."
    if receipt.decision is ResearchDecision.INSUFFICIENT_EVIDENCE:
        why = reason or "the required evidence is insufficient"
        return f"{name}{edge}, but {why}, so it would not trade."
    why = reason or "the research threshold was not met"
    return f"{name}{edge}, but {why}, so it would not trade."
