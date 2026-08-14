"""Deterministic owner-facing current-belief projection over persisted receipts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from .domain import ZERO_INFLUENCE, AgentDefinition, DecisionReceipt, explain_decision


@dataclass(frozen=True, slots=True)
class FreshnessPolicy:
    maximum_age_by_agent: tuple[tuple[str, timedelta], ...]

    def maximum_age(self, agent_id: str) -> timedelta | None:
        return dict(self.maximum_age_by_agent).get(agent_id)


@dataclass(frozen=True, slots=True)
class CurrentAgentBelief:
    agent: AgentDefinition
    receipt: DecisionReceipt | None
    as_of: datetime
    stale: bool | None
    explanation: str
    authority: str
    production_influence: Decimal = ZERO_INFLUENCE

    def __post_init__(self) -> None:
        if self.production_influence != ZERO_INFLUENCE:
            raise ValueError("current beliefs must have exactly zero production influence")
        if self.receipt is not None and self.receipt.agent_id != self.agent.agent_id:
            raise ValueError("belief receipt must belong to its agent")


def current_belief(
    agent: AgentDefinition,
    receipt: DecisionReceipt | None,
    *,
    as_of: datetime,
    freshness: FreshnessPolicy,
) -> CurrentAgentBelief:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("belief as_of must be timezone-aware")
    if receipt is None:
        return CurrentAgentBelief(
            agent, None, as_of, None, "No decisions yet.", agent.risk_limit_summary
        )
    maximum_age = freshness.maximum_age(agent.agent_id)
    stale = (
        receipt.created_at > as_of
        or maximum_age is None
        or as_of - receipt.created_at > maximum_age
    )
    explanation = explain_decision(receipt, agent.display_name)
    return CurrentAgentBelief(agent, receipt, as_of, stale, explanation, agent.risk_limit_summary)
