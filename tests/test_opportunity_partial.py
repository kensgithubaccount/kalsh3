from decimal import Decimal
from pathlib import Path

import pytest

from services.opportunity_engine.domain import OpportunityError, TradeCandidate


def test_candidate_is_research_only_and_accounts_for_all_declared_costs() -> None:
    candidate = TradeCandidate(
        "c",
        "M",
        Decimal(".60"),
        Decimal(".50"),
        Decimal(".01"),
        Decimal(".01"),
        Decimal(".01"),
        Decimal(".5"),
        Decimal(".02"),
        Decimal(".8"),
        Decimal(".01"),
        Decimal(".01"),
        Decimal(".9"),
    )
    assert candidate.descriptive_after_cost_difference == Decimal(".0135")
    assert candidate.production_influence == 0
    with pytest.raises(OpportunityError):
        TradeCandidate(
            "c",
            "M",
            Decimal("1.1"),
            Decimal(".5"),
            *(Decimal("0") for _ in range(6)),
            Decimal(".5"),
            Decimal("0"),
            Decimal(".5"),
        )


def test_opportunity_engine_has_no_execution_signer_or_risk_path() -> None:
    code = "\n".join(path.read_text() for path in Path("services/opportunity_engine").glob("*.py"))
    for forbidden in ("RequestSigner", "submit_order", "kalshi_account_gateway", "risk_engine"):
        assert forbidden not in code
