from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from services.document_intelligence.models import EvidenceStatus
from services.forecasting.calibration import CalibrationMethod, SettledSample, fit_walk_forward
from services.forecasting.connectors import ConnectorState, ReadConnectorPolicy
from services.forecasting.distributions import EmpiricalDistribution, coherent_bins
from services.forecasting.domain import ForecastError, ModelFamily
from services.forecasting.engine import (
    independent_probability,
    market_anchored_blend,
    validate_evidence_statuses,
)
from services.forecasting.evaluation import grouped_counts
from services.forecasting.macro import (
    ReleaseTarget,
    ReleaseVintage,
    latest_visible_vintage,
    transparent_release_distribution,
)
from services.forecasting.models import (
    AbstentionReason,
    FeatureProvenance,
    FeatureSnapshot,
    FeatureValue,
    Forecast,
    ForecastKind,
    MarketReference,
)
from services.forecasting.registry import (
    ModelCard,
    ModelRegistry,
    ModelStatus,
    RegisteredModel,
)
from services.forecasting.scoring import relative_score, score
from services.forecasting.weather import (
    WeatherContract,
    WeatherSourceRecord,
    WeatherSourceRole,
    convert_temperature,
    forecast_weather,
)

NOW = datetime(2026, 8, 10, 12, tzinfo=UTC)


def feature(
    name: str, value: Decimal, provenance: FeatureProvenance, available: datetime = NOW
) -> FeatureValue:
    return FeatureValue(name, value, ("fixture",), available, provenance, False, None)


def market_reference(at: datetime = NOW) -> MarketReference:
    return MarketReference.midpoint(
        forecast_at=at,
        snapshot_time=at,
        yes_bid=Decimal(".44"),
        yes_ask=Decimal(".46"),
        no_bid=Decimal(".54"),
        no_ask=Decimal(".56"),
        max_age_ms=1000,
        max_spread=Decimal(".05"),
    )


def test_market_reference_is_fresh_consensus_not_executable_price() -> None:
    reference = market_reference()
    assert reference.reference_probability == Decimal(".45")
    assert reference.yes_ask == Decimal(".46") and reference.construction_method.endswith(
        "MIDPOINT"
    )
    with pytest.raises(ForecastError, match="stale"):
        MarketReference.midpoint(
            forecast_at=NOW,
            snapshot_time=NOW - timedelta(seconds=2),
            yes_bid=Decimal(".4"),
            yes_ask=Decimal(".5"),
            no_bid=Decimal(".5"),
            no_ask=Decimal(".6"),
            max_age_ms=1000,
            max_spread=Decimal(".2"),
        )
    with pytest.raises(ForecastError):
        MarketReference.midpoint(
            forecast_at=NOW,
            snapshot_time=NOW,
            yes_bid=Decimal(".6"),
            yes_ask=Decimal(".5"),
            no_bid=Decimal(".5"),
            no_ask=Decimal(".6"),
            max_age_ms=1000,
            max_spread=Decimal(".2"),
        )


def test_feature_snapshot_blocks_future_and_market_features_from_independent_model() -> None:
    with pytest.raises(ForecastError, match="future"):
        FeatureSnapshot.build(
            NOW,
            "v1",
            (
                feature(
                    "tomorrow",
                    Decimal(1),
                    FeatureProvenance.FUNDAMENTAL_STRUCTURED,
                    NOW + timedelta(days=1),
                ),
            ),
        )
    fundamental = FeatureSnapshot.build(
        NOW, "v1", (feature("forecast", Decimal("90"), FeatureProvenance.FUNDAMENTAL_STRUCTURED),)
    )
    assert independent_probability(fundamental, Decimal(".52")) == Decimal(".52")
    for provenance in (
        FeatureProvenance.KALSHI_MARKET_DERIVED,
        FeatureProvenance.EXTERNAL_MARKET_DERIVED,
    ):
        snapshot = FeatureSnapshot.build(NOW, "v1", (feature("market", Decimal(".5"), provenance),))
        with pytest.raises(ForecastError, match="market-derived"):
            independent_probability(snapshot, Decimal(".5"))
    assert market_anchored_blend(market_reference(), Decimal(".55")) == Decimal(".48")


def test_only_validated_m7_evidence_is_usable() -> None:
    validate_evidence_statuses((EvidenceStatus.VALIDATED,))
    for status in (
        EvidenceStatus.PROPOSED,
        EvidenceStatus.CITATION_VALIDATED,
        EvidenceStatus.AMBIGUOUS,
        EvidenceStatus.RETRACTED,
    ):
        with pytest.raises(ForecastError):
            validate_evidence_statuses((status,))


def test_weather_semantics_units_dst_observed_floor_and_thresholds() -> None:
    contract = WeatherContract(
        "KNYC",
        "Central Park",
        "DAILY_MAX",
        date(2026, 11, 1),
        "America/New_York",
        Decimal("90"),
        None,
        "GT",
        "degF",
        Decimal("1"),
        "NWS",
        "CLI",
        "final correction controls",
    )
    result = forecast_weather(
        contract,
        Decimal("89"),
        (),
        (Decimal("-2"), Decimal("0"), Decimal("2"), Decimal("4")),
        Decimal("91"),
    )
    assert result.probability == Decimal(1)
    assert result.outcome_interval[0] >= Decimal("91")
    assert convert_temperature(Decimal(0), "degC", "degF") == Decimal(32)
    assert ZoneInfo(contract.timezone).utcoffset(datetime(2026, 11, 1, 1)) is not None
    distribution = EmpiricalDistribution(
        (Decimal(1), Decimal(2), Decimal(3), Decimal(4)), Decimal(0), 4
    )
    assert distribution.probability("GT", Decimal(2)) == Decimal(".5")
    assert distribution.probability("GTE", Decimal(2)) == Decimal(".75")
    assert distribution.probability("LT", Decimal(2)) == Decimal(".25")
    assert distribution.probability("LTE", Decimal(2)) == Decimal(".5")
    assert distribution.probability("RANGE", Decimal(1), Decimal(2)) == Decimal(".5")
    assert coherent_bins(distribution, ((Decimal(1), Decimal(2)), (Decimal(3), Decimal(4)))) == (
        Decimal(".5"),
        Decimal(".5"),
    )


def test_weather_missing_semantics_correction_and_source_time_fail_closed() -> None:
    ambiguous = WeatherContract(
        "",
        "City",
        "DAILY_MAX",
        date(2026, 1, 1),
        "UTC",
        Decimal(1),
        None,
        "GT",
        "degF",
        None,
        "NWS",
        "CLI",
        "final",
    )
    with pytest.raises(ForecastError, match="ambiguous"):
        ambiguous.validate()
    later = WeatherSourceRecord(
        WeatherSourceRole.LIVE_OBSERVATION_SOURCE,
        "KNYC",
        NOW,
        NOW,
        NOW,
        NOW + timedelta(minutes=1),
        Decimal("90"),
        "degF",
        "h",
        "OK",
        "v1",
    )
    assert not later.visible(NOW) and later.visible(NOW + timedelta(minutes=1))
    corrected = WeatherSourceRecord(
        WeatherSourceRole.FINAL_OFFICIAL_SETTLEMENT_SOURCE,
        "KNYC",
        NOW,
        NOW,
        NOW,
        NOW + timedelta(minutes=2),
        Decimal("91"),
        "degF",
        "h2",
        "OK",
        "v1",
        "h",
    )
    assert not corrected.visible(NOW)


def vintage(
    identifier: str, value: str, available: datetime, revision: int = 0, revises: str | None = None
) -> ReleaseVintage:
    return ReleaseVintage(
        identifier,
        ReleaseTarget.CPI,
        "CUUR0000SA0",
        "2026-07",
        NOW,
        available,
        available,
        Decimal(value),
        "PERCENT",
        revision,
        revises,
        "BLS",
    )


def test_macro_initial_release_revision_and_vintage_leakage() -> None:
    initial = vintage("initial", "3.2", NOW - timedelta(days=1))
    revision = vintage("revision", "3.1", NOW + timedelta(days=30), 1, "initial")
    assert latest_visible_vintage((initial, revision), NOW) == initial
    history = tuple(
        vintage(str(i), str(2 + i / 10), NOW - timedelta(days=20 - i)) for i in range(12)
    )
    center, distribution = transparent_release_distribution(
        history, NOW, (Decimal("-.2"), Decimal("0"), Decimal(".2"))
    )
    assert center > 0 and distribution.probability("GT", Decimal("3")) >= 0
    with pytest.raises(ForecastError, match="insufficient"):
        transparent_release_distribution(history[:3], NOW, (Decimal(0),))


def test_walk_forward_calibration_excludes_future_and_groups_events() -> None:
    past = tuple(
        SettledSample(
            f"e{i % 4}", NOW - timedelta(days=2), NOW - timedelta(days=1), Decimal(".4"), i % 2
        )
        for i in range(8)
    )
    future = SettledSample("future", NOW, NOW + timedelta(days=1), Decimal(".9"), 1)
    calibrator = fit_walk_forward((*past, future), NOW, ModelFamily.WEATHER, "24h", 30)
    assert calibrator.training_event_count == 4
    assert calibrator.method == CalibrationMethod.HIERARCHICAL_SHRINKAGE
    assert Decimal(0) <= calibrator.apply(Decimal(".7")) <= Decimal(1)
    empty = fit_walk_forward((), NOW, ModelFamily.WEATHER, "24h")
    assert empty.method == CalibrationMethod.IDENTITY


def test_proper_scoring_and_same_checkpoint_market_comparison() -> None:
    perfect = score(Decimal(1), 1)
    assert perfect.brier == 0 and perfect.log_clipped_for_computation
    compared = relative_score(Decimal(".7"), Decimal(".6"), 1, NOW, NOW)
    assert compared.model.brier < compared.market.brier
    with pytest.raises(ForecastError, match="same forecast checkpoint"):
        relative_score(Decimal(".7"), Decimal(".6"), 1, NOW, NOW + timedelta(hours=1))


def test_forecast_is_content_addressed_immutable_and_abstention_is_not_half() -> None:
    values = dict(
        market_ticker="M",
        event_id="E",
        series_id="S",
        market_family=ModelFamily.WEATHER,
        rules_version="r",
        rules_hash="rh",
        forecast_kind=ForecastKind.INDEPENDENT_FUNDAMENTAL,
        issued_at=NOW,
        replay_time=NOW,
        target_resolution_time=NOW + timedelta(days=1),
        horizon_seconds=86400,
        model_id="weather",
        model_version="v1",
        feature_schema_version="f1",
        model_artifact_hash="a",
        calibration_id="identity",
        calibration_version="v1",
        feature_snapshot_id="fs",
        evidence_bundle_id="eb",
        source_snapshot_id="ss",
        raw_probability=Decimal(".5"),
        calibrated_probability=Decimal(".5"),
        lower_probability=Decimal(".4"),
        upper_probability=Decimal(".6"),
        interval_level=Decimal(".9"),
        uncertainty_method="EMPIRICAL_RESIDUAL",
        uncertainty_quality="POOLED_SMALL_SAMPLE",
        market_reference=market_reference(),
        abstention_reason=None,
        research_status="SHADOW",
        production_influence=Decimal(0),
        code_git_sha="git",
        created_at=NOW,
    )
    first, second = Forecast.freeze(**values), Forecast.freeze(**values)
    assert first.forecast_id == second.forecast_id == first.content_hash
    with pytest.raises(ForecastError, match="abstention"):
        Forecast.freeze(**(values | {"abstention_reason": AbstentionReason.STALE_SOURCE}))


def test_registry_cards_connectors_and_unique_event_accounting() -> None:
    model = RegisteredModel(
        "weather",
        "v1",
        ModelFamily.WEATHER,
        "daily_max",
        "EMPIRICAL",
        ForecastKind.INDEPENDENT_FUNDAMENTAL,
        "f1",
        frozenset({FeatureProvenance.FUNDAMENTAL_STRUCTURED}),
        (NOW - timedelta(days=365), NOW),
        "dataset",
        "identity",
        "git",
        (),
        "hash",
        ModelStatus.SHADOW,
        NOW,
    )
    registry = ModelRegistry()
    registry.register(model)
    with pytest.raises(ForecastError):
        registry.register(model)
    card = ModelCard(
        "weather",
        "research",
        ModelFamily.WEATHER,
        "daily max",
        ("NWS",),
        ("market prices",),
        "walk-forward residuals",
        "identity/shrinkage",
        "empirical interval",
        ("station ambiguity",),
        ("validated rules",),
        "ONE_MINUTE_CANDLES",
        ("Brier", "log loss"),
        0,
        ("source outage",),
        ModelStatus.SHADOW,
    )
    assert card.production_influence == 0
    policy = ReadConnectorPolicy(
        "NWS",
        "api.weather.gov",
        ("/points",),
        "GET",
        10,
        1_000_000,
        ("application/geo+json",),
        "KalshiProductionV3 contact@example.invalid",
        ConnectorState.MOCK,
    )
    policy.validate()
    with pytest.raises(ValueError):
        ReadConnectorPolicy(
            "NWS",
            "evil.test",
            ("/",),
            "GET",
            1,
            1,
            ("application/json",),
            "id",
            ConnectorState.MOCK,
        ).validate()
    rows = tuple((f"m{i}", f"event{i // 10}", "24h", True) for i in range(5000))
    counts = grouped_counts(rows)
    assert counts.forecast_runs == 5000 and counts.settled_unique_events == 500
    assert counts.effective_sample_size == 500


def test_m8_has_no_signer_risk_mutation_execution_or_capital_path() -> None:
    code = "\n".join(path.read_text() for path in Path("services/forecasting").glob("*.py"))
    for forbidden in (
        "RequestSigner",
        "kalshi_account_gateway",
        "submit_order",
        "risk_engine",
        "position_size",
        "expected_profit",
    ):
        assert forbidden not in code
