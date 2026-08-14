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
from .evaluation import (
    AgentPerformance,
    EvaluationDatasetManifest,
    EvaluationEligibility,
    EvaluationTarget,
    ReceiptEvaluation,
    calibration,
    effective_evaluations,
    evaluate_receipt,
    performance,
)
from .evaluation_store import EvaluationStore, EvaluationStoreError
from .store import DecisionReceiptStore, DecisionReceiptStoreError

__all__ = [
    "AGENT_REGISTRY",
    "AgentDefinition",
    "AgentPerformance",
    "AutonomyMode",
    "CurrentAgentBelief",
    "DecisionReceipt",
    "DecisionReceiptStore",
    "DecisionReceiptStoreError",
    "EvaluationDatasetManifest",
    "EvaluationEligibility",
    "EvaluationStore",
    "EvaluationStoreError",
    "EvaluationTarget",
    "FreshnessPolicy",
    "ImplementationAvailability",
    "OpportunityAttribution",
    "ReceiptEvaluation",
    "ResearchDecision",
    "agent_by_id",
    "calibration",
    "current_belief",
    "effective_evaluations",
    "evaluate_receipt",
    "explain_decision",
    "performance",
    "receipt_identity",
]
