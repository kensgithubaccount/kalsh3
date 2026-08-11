from decimal import Decimal
from pathlib import Path

from services.execution_simulation.domain import (
    CandidateSimulation,
    SimulatedOutcome,
    SimulationCase,
)


def test_optimistic_only_candidate_does_not_advance() -> None:
    outcomes = (
        SimulatedOutcome(
            SimulationCase.OPTIMISTIC,
            Decimal(1),
            Decimal(".4"),
            Decimal(".01"),
            Decimal(0),
            Decimal(0),
            Decimal(".05"),
        ),
        SimulatedOutcome(
            SimulationCase.BASE,
            Decimal(".5"),
            Decimal(".45"),
            Decimal(".02"),
            Decimal(".02"),
            Decimal(".01"),
            Decimal("-.01"),
        ),
        SimulatedOutcome(
            SimulationCase.ADVERSE, Decimal(0), None, Decimal(0), Decimal(0), Decimal(".03"), None
        ),
    )
    simulation = CandidateSimulation.assess("c", outcomes)
    assert simulation.research_advancement == "FAIL"
    assert simulation.production_influence == 0


def test_simulation_has_no_live_execution_path() -> None:
    code = "\n".join(
        path.read_text() for path in Path("services/execution_simulation").glob("*.py")
    )
    for forbidden in ("RequestSigner", "submit_order", "kalshi_account_gateway", "risk_engine"):
        assert forbidden not in code
