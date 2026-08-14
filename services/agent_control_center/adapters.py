"""Narrow deterministic research adapters; no execution or network dependencies."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from services.opportunity_engine.cross_venue import CrossVenueOpportunityObservation
from services.opportunity_engine.models import DecisionState, RejectionReason, TradeCandidate

from .attribution import OpportunityAttribution, receipt_identity
from .domain import DecisionReceipt, ResearchDecision

_INSUFFICIENT = frozenset(
    {
        RejectionReason.RULES_INVALID,
        RejectionReason.RULES_CHANGED,
        RejectionReason.CONTRACT_AMBIGUOUS,
        RejectionReason.SETTLEMENT_SOURCE_UNKNOWN,
        RejectionReason.FORECAST_MISSING,
        RejectionReason.FORECAST_STALE,
        RejectionReason.FORECAST_RULE_VERSION_MISMATCH,
        RejectionReason.FORECAST_ABSTAINED,
        RejectionReason.BOOK_MISSING,
        RejectionReason.BOOK_STALE,
        RejectionReason.BOOK_CROSSED,
        RejectionReason.BOOK_SEQUENCE_INVALID,
        RejectionReason.DEPTH_INSUFFICIENT,
        RejectionReason.FEE_MODEL_UNAVAILABLE,
        RejectionReason.FEE_MODEL_UNVERIFIED,
        RejectionReason.SOURCE_HEALTH_FAILURE,
        RejectionReason.DATA_TEMPORAL_SKEW,
        RejectionReason.FILL_MODEL_UNAVAILABLE,
        RejectionReason.SLIPPAGE_UNCERTAIN,
        RejectionReason.INFORMATION_TOO_STALE,
        RejectionReason.RESEARCH_EVIDENCE_INSUFFICIENT,
        RejectionReason.DATA_QUALITY_FAILURE,
        RejectionReason.AFTER_COST_REPLAY_UNSUPPORTED,
    }
)
_RISK_BLOCKS = frozenset(
    {RejectionReason.TOO_CLOSE_TO_CLOSE, RejectionReason.CORRELATION_DUPLICATE}
)


def _event_decision(candidate: TradeCandidate) -> ResearchDecision:
    if candidate.decision_state in {
        DecisionState.RESEARCH_CANDIDATE,
        DecisionState.HIGH_PRIORITY_RESEARCH_CANDIDATE,
    }:
        return ResearchDecision.WOULD_TRADE
    if candidate.decision_state is DecisionState.INCOMPLETE:
        return ResearchDecision.INSUFFICIENT_EVIDENCE
    reasons = set(candidate.rejection_reasons)
    if reasons & _RISK_BLOCKS:
        return ResearchDecision.BLOCKED_BY_RISK
    if candidate.decision_state is DecisionState.REJECTED and reasons & _INSUFFICIENT:
        return ResearchDecision.INSUFFICIENT_EVIDENCE
    return ResearchDecision.NO_TRADE


def event_edge_attribution(candidate: TradeCandidate) -> OpportunityAttribution:
    if not candidate.research_only or candidate.production_influence != 0:
        raise ValueError("only zero-influence research candidates may be attributed")
    if candidate.expected_slippage != candidate.economics.expected_slippage:
        raise ValueError("candidate slippage disagrees with its frozen economics")
    agent_id, version, source_kind = "event-edge", "1.0.0", "trade-candidate"
    evidence = (
        f"forecast:{candidate.forecast_id}",
        f"market-snapshot:{candidate.market_snapshot_id}",
        f"rules:{candidate.rules_hash}",
        f"fee-schedule:{candidate.fee_schedule_id}",
    )
    decision = _event_decision(candidate)
    reasons = tuple(reason.value for reason in candidate.rejection_reasons)
    receipt = DecisionReceipt(
        receipt_identity(agent_id, version, source_kind, candidate.candidate_id),
        candidate.created_at,
        agent_id,
        version,
        f"{candidate.market_ticker}:{candidate.outcome_side.value}",
        candidate.economics.executable_price,
        candidate.economics.fair_probability,
        None,
        candidate.economics.gross_probability_difference,
        candidate.economics.expected_fee,
        candidate.economics.expected_slippage,
        candidate.economics.conservative_expected_value,
        None,
        evidence,
        None,
        (),
        (),
        decision,
        (),
        () if decision is ResearchDecision.WOULD_TRADE else reasons,
    )
    return OpportunityAttribution(source_kind, candidate.candidate_id, agent_id, evidence, receipt)


def cross_market_attribution(
    observation: CrossVenueOpportunityObservation,
    *,
    instrument_id: str,
    created_at: datetime,
    evidence_references: tuple[str, ...],
) -> OpportunityAttribution:
    if observation.production_influence != 0 or not evidence_references:
        raise ValueError("cross-market attribution requires zero influence and explicit evidence")
    state = observation.research_state
    decision = (
        ResearchDecision.WOULD_TRADE
        if state == "CROSS_VENUE_RESEARCH_CANDIDATE"
        else ResearchDecision.INSUFFICIENT_EVIDENCE
        if state in {"INCOMPLETE", "SEMANTIC_MATCH_INSUFFICIENT"}
        else ResearchDecision.NO_TRADE
    )
    fees = (
        None
        if observation.kalshi_fee is None or observation.polymarket_fee is None
        else observation.kalshi_fee + observation.polymarket_fee
    )
    raw = abs(observation.kalshi_price - observation.polymarket_price)
    after_cost = (
        None
        if fees is None or observation.expected_slippage is None
        else raw
        - fees
        - observation.expected_slippage
        - observation.semantic_reserve
        - observation.leg_risk_reserve
    )
    reasons = () if decision is ResearchDecision.WOULD_TRADE else (state,)
    agent_id, version, source_kind = "cross-market", "1.0.0", "cross-market-source-v1"
    source_id = cross_market_source_identity(
        observation,
        instrument_id=instrument_id,
        created_at=created_at,
        evidence_references=evidence_references,
    )
    receipt = DecisionReceipt(
        receipt_identity(agent_id, version, source_kind, source_id),
        created_at,
        agent_id,
        version,
        instrument_id,
        observation.kalshi_price,
        None,
        None,
        raw,
        fees,
        observation.expected_slippage,
        after_cost,
        None,
        evidence_references,
        None,
        (),
        (
            f"semantic_match={observation.semantic_match.value}",
            f"semantic_reserve={observation.semantic_reserve}",
            f"leg_risk_reserve={observation.leg_risk_reserve}",
            f"venue_state_risk={observation.venue_state_risk}",
            f"reference_overlap={observation.reference_overlap}",
        ),
        decision,
        (),
        reasons,
    )
    return OpportunityAttribution(source_kind, source_id, agent_id, evidence_references, receipt)


def cross_market_source_identity(
    observation: CrossVenueOpportunityObservation,
    *,
    instrument_id: str,
    created_at: datetime,
    evidence_references: tuple[str, ...],
) -> str:
    """Bind the complete caller-attested cross-market source package to one audit ID."""
    if created_at.tzinfo is None or created_at.utcoffset() != UTC.utcoffset(created_at):
        raise ValueError("cross-market source timestamp must be UTC")

    def decimal(value: object) -> str | None:
        return None if value is None else str(value)

    payload = {
        "domain": "m26b-cross-market-source-v1",
        "attribution": {
            "created_at": created_at.isoformat(timespec="microseconds").replace("+00:00", "Z"),
            "evidence_references": list(evidence_references),
            "instrument_id": instrument_id,
        },
        "observation": {
            "caller_observation_id": observation.observation_id,
            "expected_slippage": decimal(observation.expected_slippage),
            "kalshi_depth": decimal(observation.kalshi_depth),
            "kalshi_fee": decimal(observation.kalshi_fee),
            "kalshi_price": decimal(observation.kalshi_price),
            "leg_risk_reserve": decimal(observation.leg_risk_reserve),
            "polymarket_depth": decimal(observation.polymarket_depth),
            "polymarket_fee": decimal(observation.polymarket_fee),
            "polymarket_price": decimal(observation.polymarket_price),
            "production_influence": decimal(observation.production_influence),
            "reference_overlap": observation.reference_overlap,
            "research_state": observation.research_state,
            "semantic_match": observation.semantic_match.value,
            "semantic_reserve": decimal(observation.semantic_reserve),
            "timestamp_skew_ms": observation.timestamp_skew_ms,
            "venue_state_risk": observation.venue_state_risk,
        },
    }
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()
