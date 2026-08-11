from decimal import Decimal
from pathlib import Path

import pytest

from services.learning.domain import (
    AblationResult,
    LearningError,
    ResearchWeightProposal,
)


def test_ablation_is_descriptive_and_weight_proposal_is_bounded_zero_influence() -> None:
    result = AblationResult(
        "a", "WEATHER", "NWS", Decimal(".18"), Decimal(".19"), 100, "dataset", True
    )
    assert result.descriptive_incremental_brier == Decimal(".01")
    proposal = ResearchWeightProposal(
        "p", "NWS", Decimal(".5"), Decimal(".55"), Decimal(".1"), "evaluation"
    )
    assert proposal.production_influence == 0
    with pytest.raises(LearningError):
        ResearchWeightProposal("p", "NWS", Decimal(".5"), Decimal(".8"), Decimal(".1"), "e")


def test_learning_has_no_risk_signer_execution_or_financial_limits() -> None:
    code = "\n".join(path.read_text() for path in Path("services/learning").glob("*.py"))
    for forbidden in (
        "risk_engine",
        "RequestSigner",
        "submit_order",
        "risk_limit",
        "position_size",
    ):
        assert forbidden not in code
