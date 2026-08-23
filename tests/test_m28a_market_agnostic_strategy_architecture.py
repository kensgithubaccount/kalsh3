"""Offline tests for M28 market-agnostic strategy registration."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from services.production_weather_strategy.architecture import (
    HeritageBaseline,
    MarketFamilySpec,
    ModelRecipe,
    SourceCapability,
    SourceRole,
    StrategyRegistry,
)
from services.production_weather_strategy.contracts import ProductionStrategyError


def weather_source() -> SourceCapability:
    return SourceCapability.build(
        source_id="noaa-ndfd",
        roles=(SourceRole.FORECAST, SourceRole.PRIMARY_OBSERVATION),
        domains=("weather",),
        authority="reviewed NOAA public acquisition",
        maximum_age_seconds=1800,
        production_allowed=True,
    )


def market_source() -> SourceCapability:
    return SourceCapability.build(
        source_id="kalshi-public",
        roles=(SourceRole.MARKET_DATA, SourceRole.SETTLEMENT),
        domains=("weather", "economics", "politics", "sports"),
        authority="reviewed fixed-origin Kalshi public API",
        maximum_age_seconds=30,
        production_allowed=True,
    )


def weather_recipe() -> ModelRecipe:
    return ModelRecipe.build(
        recipe_id="weather-calibrated-ensemble-v1",
        algorithm="champion-challenger calibrated ensemble",
        supported_domains=("weather",),
        required_feature_groups=(
            "forecast-level",
            "forecast-revisions",
            "seasonality",
            "market-economics",
        ),
        calibration_method="held-out temporal calibration",
        supports_retraining=True,
        supports_ensemble_weighting=True,
    )


def chicago() -> MarketFamilySpec:
    return MarketFamilySpec.build(
        family_id="weather.daily-high.chicago",
        domain="weather",
        selector="KXHIGHCHI",
        settlement_mapping_id="kalshi-weather-company-chicago-v1",
        source_ids=("noaa-ndfd", "kalshi-public"),
        feature_groups=(
            "forecast-level",
            "forecast-revisions",
            "market-economics",
            "seasonality",
        ),
        model_recipe_ids=("weather-calibrated-ensemble-v1",),
    )


def nyc() -> MarketFamilySpec:
    return MarketFamilySpec.build(
        family_id="weather.daily-high.new-york",
        domain="weather",
        selector="KXHIGHNY",
        settlement_mapping_id="kalshi-weather-company-new-york-v1",
        source_ids=("noaa-ndfd", "kalshi-public"),
        feature_groups=(
            "forecast-level",
            "forecast-revisions",
            "market-economics",
            "seasonality",
        ),
        model_recipe_ids=("weather-calibrated-ensemble-v1",),
    )


def test_chicago_is_configuration_not_a_hard_coded_universe() -> None:
    registry = StrategyRegistry.build(
        sources=(weather_source(), market_source()),
        model_recipes=(weather_recipe(),),
        market_families=(chicago(), nyc()),
    )
    assert registry.enabled_family_ids == (
        "weather.daily-high.chicago",
        "weather.daily-high.new-york",
    )
    assert {item.selector for item in registry.market_families} == {"KXHIGHCHI", "KXHIGHNY"}


def test_registry_can_extend_to_non_weather_without_changing_core_contracts() -> None:
    economic_source = SourceCapability.build(
        source_id="official-economic-release",
        roles=(SourceRole.STRUCTURED_DATA, SourceRole.SETTLEMENT),
        domains=("economics",),
        authority="reviewed official statistical release",
        maximum_age_seconds=3600,
        production_allowed=False,
    )
    economic_recipe = ModelRecipe.build(
        recipe_id="economics-ensemble-v1",
        algorithm="calibrated economics ensemble",
        supported_domains=("economics",),
        required_feature_groups=("official-releases", "market-economics"),
        calibration_method="walk-forward temporal calibration",
        supports_retraining=True,
        supports_ensemble_weighting=True,
    )
    economic_family = MarketFamilySpec.build(
        family_id="economics.generic-example",
        domain="economics",
        selector="CONFIGURED_AT_ADAPTER_BOUNDARY",
        settlement_mapping_id="official-economic-settlement-v1",
        source_ids=("kalshi-public", "official-economic-release"),
        feature_groups=("official-releases", "market-economics"),
        model_recipe_ids=("economics-ensemble-v1",),
        enabled=False,
    )
    registry = StrategyRegistry.build(
        sources=(market_source(), economic_source),
        model_recipes=(economic_recipe,),
        market_families=(economic_family,),
    )
    assert registry.market_families[0].domain == "economics"
    assert registry.enabled_family_ids == ()


def test_frozen_m27_model_can_be_retained_as_zero_influence_baseline() -> None:
    baseline = HeritageBaseline.build(
        source_system="M27",
        source_model_id="bb2758d3dbeac46b6fd92f7bde09549178ca2ac585f447d177cebc44fb758981",
        family_id="weather.daily-high.chicago",
        evidence_manifest_ids=("m27-prospective-predictions",),
    )
    registry = StrategyRegistry.build(
        sources=(weather_source(), market_source()),
        model_recipes=(weather_recipe(),),
        market_families=(chicago(),),
        heritage_baselines=(baseline,),
    )
    assert registry.heritage_baselines[0].source_system == "M27"
    assert registry.heritage_baselines[0].production_influence == Decimal("0")
    assert registry.heritage_baselines[0].frozen is True


def test_heritage_baseline_cannot_be_given_direct_production_influence() -> None:
    with pytest.raises(ProductionStrategyError, match="zero direct production influence"):
        HeritageBaseline.build(
            source_system="M27",
            source_model_id="model",
            family_id="weather.daily-high.chicago",
            evidence_manifest_ids=("evidence",),
            production_influence=Decimal("0.01"),
        )


def test_family_cannot_reference_unknown_source_or_model() -> None:
    with pytest.raises(ProductionStrategyError, match="unknown source"):
        StrategyRegistry.build(
            sources=(market_source(),),
            model_recipes=(weather_recipe(),),
            market_families=(chicago(),),
        )


def test_family_cannot_reference_source_from_wrong_domain() -> None:
    economics_only = SourceCapability.build(
        source_id="economics-only",
        roles=(SourceRole.STRUCTURED_DATA,),
        domains=("economics",),
        authority="official source",
        maximum_age_seconds=60,
        production_allowed=False,
    )
    family = MarketFamilySpec.build(
        family_id="weather.invalid-source",
        domain="weather",
        selector="INVALID",
        settlement_mapping_id="mapping",
        source_ids=("economics-only",),
        feature_groups=(
            "forecast-level",
            "forecast-revisions",
            "market-economics",
            "seasonality",
        ),
        model_recipe_ids=("weather-calibrated-ensemble-v1",),
    )
    with pytest.raises(ProductionStrategyError, match="source does not support"):
        StrategyRegistry.build(
            sources=(economics_only,),
            model_recipes=(weather_recipe(),),
            market_families=(family,),
        )


def test_family_must_satisfy_model_recipe_features() -> None:
    incomplete = MarketFamilySpec.build(
        family_id="weather.incomplete-features",
        domain="weather",
        selector="INCOMPLETE",
        settlement_mapping_id="mapping",
        source_ids=("noaa-ndfd", "kalshi-public"),
        feature_groups=("forecast-level", "market-economics"),
        model_recipe_ids=("weather-calibrated-ensemble-v1",),
    )
    with pytest.raises(ProductionStrategyError, match="feature requirements"):
        StrategyRegistry.build(
            sources=(weather_source(), market_source()),
            model_recipes=(weather_recipe(),),
            market_families=(incomplete,),
        )


def test_core_architecture_has_no_execution_or_credential_imports() -> None:
    source = Path("services/production_weather_strategy/architecture.py").read_text()
    assert "services.production_execution" not in source
    assert "services.kalshi_account_gateway" not in source
    assert "services.risk_engine.authorization" not in source
    assert "services.supervised_canary" not in source
