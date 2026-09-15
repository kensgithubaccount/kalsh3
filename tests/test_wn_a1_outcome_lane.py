from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from services.forecasting import wn_a1_outcome_lane as outcome_lane
from services.forecasting.wn_a1_outcome_lane import NEXT_MILESTONE, SettlementOutcome


def test_next_milestone_is_documented_not_implemented() -> None:
    assert NEXT_MILESTONE.startswith("WN-A2")


def test_settlement_outcome_is_shape_only_and_unused_by_any_other_wn_a1_module() -> None:
    outcome = SettlementOutcome(
        market_ticker="KXHIGHCHI-26SEP15-T87",
        target_local_date=date(2026, 9, 15),
        settled_value_f=Decimal("89"),
        source_url="https://weather.com/kalshi",
        acquired_at=datetime(2026, 9, 20, 12, 0, tzinfo=UTC),
        raw_sha256="d" * 64,
    )
    assert outcome.research_only is True
    assert outcome.production_influence == Decimal(0)
    # No other WN-A1 module imports this module -- it is a standalone, unused shape.
    from pathlib import Path

    wn_a1_dir = Path(outcome_lane.__file__).parent
    for path in wn_a1_dir.glob("wn_a1_*.py"):
        if path.stem == "wn_a1_outcome_lane":
            continue
        assert "wn_a1_outcome_lane" not in path.read_text()
