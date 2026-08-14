"""M26A owner-facing research-agent domain contracts."""

from .attribution import OpportunityAttribution, receipt_identity
from .beliefs import CurrentAgentBelief, FreshnessPolicy, current_belief
from .domain import (
    AGENT_REGISTRY,
    AgentDefinition,
    AutonomyMode,
    DecisionReceipt,
    ImplementationAvailability,
    ResearchDecision,
    agent_by_id,
    explain_decision,
)
from .store import DecisionReceiptStore, DecisionReceiptStoreError

__all__ = [
    "AGENT_REGISTRY",
    "AgentDefinition",
    "AutonomyMode",
    "CurrentAgentBelief",
    "DecisionReceipt",
    "DecisionReceiptStore",
    "DecisionReceiptStoreError",
    "FreshnessPolicy",
    "ImplementationAvailability",
    "OpportunityAttribution",
    "ResearchDecision",
    "agent_by_id",
    "current_belief",
    "explain_decision",
    "receipt_identity",
]
