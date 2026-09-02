"""Research-only forward-reality evidence primitives."""

from .outcome_scoring import (
    OutcomeEvidenceAuthority,
    OutcomeEvidenceReceipt,
    OutcomeScoringStore,
    OutcomeStatus,
    ScoringError,
    ScoringRecord,
    calibration_buckets,
    confidence_interval,
    event_equal_aggregation,
    score_trial,
)
from .prospective_receipts import (
    ProspectiveOutcomeBoundary,
    ProspectivePredictionReceipt,
    ProspectiveReceiptError,
    ProspectiveReceiptPublication,
    ProspectiveReceiptStore,
)

__all__ = (
    "OutcomeEvidenceAuthority",
    "OutcomeEvidenceReceipt",
    "OutcomeScoringStore",
    "OutcomeStatus",
    "ProspectiveOutcomeBoundary",
    "ProspectivePredictionReceipt",
    "ProspectiveReceiptError",
    "ProspectiveReceiptPublication",
    "ProspectiveReceiptStore",
    "ScoringError",
    "ScoringRecord",
    "calibration_buckets",
    "confidence_interval",
    "event_equal_aggregation",
    "score_trial",
)
