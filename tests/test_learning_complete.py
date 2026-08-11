from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from services.learning.configuration import (
    ConfigurationMode,
    LearningConfiguration,
    configuration_at,
    rollback,
)
from services.learning.domain import LearningError, ResearchWeightProposal
from services.learning.evaluation import (
    EventContribution,
    Redundancy,
    ablation,
    concentration,
    paired_event_interval,
    timeliness,
)
from services.learning.governance import (
    DriftMetric,
    EvaluationWindow,
    GovernanceProposal,
    ProposalType,
    compare_challenger,
)
from services.learning.statistics import SourceQuality, benjamini_hochberg
from services.learning.tournament import FamilyScore, allocate_budget

NOW = datetime(2026, 8, 10, tzinfo=UTC)


def events(count: int, improvement: str = ".01") -> tuple[EventContribution, ...]:
    delta = Decimal(improvement)
    return tuple(
        EventContribution(
            f"e{i}",
            Decimal(".18"),
            Decimal(".18") + delta,
            i * 1000,
            i * 1000 + 500,
            i * 1000 + 200,
        )
        for i in range(count)
    )


def config(
    at: datetime, mode: ConfigurationMode, predecessor: str | None = None
) -> LearningConfiguration:
    return LearningConfiguration.build(
        active_models=("weather",),
        model_weights=(("weather", Decimal("1")),),
        active_sources=("NWS",),
        source_weights=(("NWS", Decimal("1")),),
        family_routing=(("weather", "weather"),),
        abstention_thresholds=(("minimum_events", Decimal("50")),),
        version="v1",
        effective_at=at,
        predecessor_id=predecessor,
        mode=mode,
        production_influence=Decimal(0),
    )


def test_event_level_ablation_interval_concentration_and_timeliness() -> None:
    sample = events(60)
    result = ablation("NWS", "weather", sample, "dataset", True, (("horizon", "0-24h"),))
    assert result.unique_event_count == result.effective_sample_size == 60
    assert result.descriptive_incremental_brier == Decimal(".01")
    interval = paired_event_interval(sample)
    assert interval.evidence == "STRONGER_EVIDENCE" and interval.lower > 0
    concentrated = concentration(
        (EventContribution("one", Decimal(".1"), Decimal(".2"), 0, 1, 1), *events(9, "0"))
    )
    assert concentrated.best_event_fraction == 1 and concentrated.excluding_best == 0
    timing = timeliness(sample)
    assert timing.before_kalshi_fraction == 1 and timing.forecast_impact_fraction == 1


def test_redundant_late_source_gets_little_incremental_credit() -> None:
    redundant = Redundancy(
        "reposter",
        "primary",
        Decimal(".98"),
        Decimal("1"),
        Decimal(".9"),
        Decimal(".9"),
        Decimal("1"),
    )
    assert redundant.duplicated_credit_factor == 0
    late = tuple(
        EventContribution(f"e{i}", Decimal(".1"), Decimal(".1"), 1000, 500, None) for i in range(10)
    )
    assert timeliness(late).before_kalshi_fraction == 0


def test_small_sample_no_promotion_and_10pp_weekly_cap() -> None:
    interval = paired_event_interval(events(7))
    assert interval.evidence == "INCONCLUSIVE"
    with pytest.raises(LearningError, match="promotion evidence"):
        GovernanceProposal(
            "p",
            ProposalType.PROMOTION_PROPOSAL,
            "model",
            "CHALLENGER",
            "RESEARCH_CHAMPION",
            interval,
            7,
            "same-events",
            "6 of 7 is insufficient",
        )
    ResearchWeightProposal("w", "NWS", Decimal(".2"), Decimal(".3"), Decimal(".10"), "e")
    with pytest.raises(LearningError):
        ResearchWeightProposal("w", "NWS", Decimal(".2"), Decimal(".31"), Decimal(".11"), "e")


def test_challenger_same_event_and_nonoverlapping_promotion_windows() -> None:
    EvaluationWindow(
        (NOW - timedelta(days=90), NOW - timedelta(days=60)),
        (NOW - timedelta(days=60), NOW - timedelta(days=30)),
        (NOW - timedelta(days=30), NOW),
    )
    strong = paired_event_interval(events(60))
    assert compare_challenger(
        tuple(f"e{i}" for i in range(60)),
        tuple(f"e{i}" for i in range(60)),
        Decimal(".18"),
        Decimal(".17"),
        strong,
    )
    with pytest.raises(LearningError, match="different events"):
        compare_challenger(("a",), ("b",), Decimal(".2"), Decimal(".1"), strong)


def test_multiple_testing_drift_quarantine_and_source_quality_cost() -> None:
    accepted = benjamini_hochberg(
        (("a", Decimal(".001")), ("b", Decimal(".4")), ("c", Decimal(".6"))), Decimal(".05")
    )
    assert accepted == {"a"}
    assert (
        DriftMetric("source", "latency", Decimal(1), Decimal(10), Decimal(2), Decimal(5)).action
        == "QUARANTINE_PROPOSAL"
    )
    quality = SourceQuality(
        "stable-did",
        Decimal(".99"),
        Decimal(".01"),
        Decimal(0),
        Decimal(".1"),
        Decimal(".1"),
        Decimal(0),
        Decimal(500),
        Decimal(".9"),
        Decimal(".1"),
        Decimal(".1"),
        Decimal(500),
        False,
        "OUTCOME_PREDICTION",
        100,
        80,
        70,
        10,
        "predeclared mapped markets",
    )
    assert quality.incremental_brier_per_dollar(Decimal(".001")) == Decimal(".000002")


def test_configuration_replay_future_score_tripwire_and_exact_rollback() -> None:
    old = config(NOW - timedelta(days=10), ConfigurationMode.HISTORICAL_OBSERVED_CONFIGURATION)
    future = config(
        NOW + timedelta(days=10),
        ConfigurationMode.HISTORICAL_OBSERVED_CONFIGURATION,
        old.configuration_id,
    )
    retro = config(NOW - timedelta(days=20), ConfigurationMode.RETROSPECTIVE_RESEARCH_CONFIGURATION)
    assert configuration_at((old, future, retro), NOW) == old
    assert configuration_at((future,), NOW) is None
    assert configuration_at((retro,), NOW, allow_retrospective=True) == retro
    restored = rollback(future, old, NOW + timedelta(days=11))
    assert (
        restored.active_models == old.active_models
        and restored.predecessor_id == future.configuration_id
    )


def family(name: str, events_count: int, skill: str) -> FamilyScore:
    return FamilyScore.calculate(
        family=name,
        market_count=100,
        usable_markets=80,
        unique_settled_events=events_count,
        forecast_coverage=Decimal(".8"),
        market_relative_skill=Decimal(skill),
        calibration_quality=Decimal(".8"),
        abstention_rate=Decimal(".2"),
        data_completeness=Decimal(".9"),
        source_reliability=Decimal(".9"),
        median_latency_ms=Decimal(500),
        liquidity_quality=Decimal(".7"),
        monthly_research_cost=Decimal(10),
        operational_complexity=Decimal(".2"),
    )


def test_family_tournament_budget_exploration_floor_and_no_capital() -> None:
    scores = tuple(
        family(name, 50 if name == "weather" else 5, ".02")
        for name in ("weather", "macro", "energy", "sports")
    )
    budget = allocate_budget(scores, 100)
    assert sum(item.forecast_jobs for item in budget.family_budgets) == 100
    assert all(item.forecast_jobs >= 5 for item in budget.family_budgets)
    assert all(score.capital_allocation == "NOT DETERMINED" for score in scores)


def test_20k_fixture_uses_2000_events_not_correlated_contract_rows() -> None:
    rows = tuple(
        EventContribution(f"event{i // 10}", Decimal(".18"), Decimal(".181"), i, i + 1, i + 1)
        for i in range(20_000)
    )
    unique = {row.event_id for row in rows}
    assert len(rows) == 20_000 and len(unique) == 2_000
    grouped = tuple(rows[index * 10] for index in range(2_000))
    assert ablation("model", "multi", grouped, "large", True).effective_sample_size == 2_000


def test_m9_cannot_touch_credentials_execution_or_financial_limits() -> None:
    code = "\n".join(path.read_text() for path in Path("services/learning").glob("*.py"))
    for forbidden in (
        "RequestSigner",
        "submit_order",
        "kalshi_account_gateway",
        "risk_engine",
        "reserve",
        "daily_stop",
        "monthly_stop",
        "position_size",
    ):
        assert forbidden not in code
