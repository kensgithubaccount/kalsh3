"""M28 market-agnostic strategy architecture.

Chicago daily highs are the first proving ground, not a permanent hard-coded universe.
This module defines configuration-only contracts that let M28 register additional weather
families, sources, feature sets, and model recipes and later extend the same production
learning machinery to non-weather Kalshi families.

Nothing here performs network I/O, accesses credentials, mutates production state, issues
risk authority, or sends orders.  The registry describes *what may be evaluated*; separate
reviewed acquisition, settlement, risk, and execution boundaries remain authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from services.historical_replay.archive import stable_hash

from .contracts import ProductionStrategyError


class SourceRole(StrEnum):
    PRIMARY_OBSERVATION = "PRIMARY_OBSERVATION"
    FORECAST = "FORECAST"
    SETTLEMENT = "SETTLEMENT"
    MARKET_DATA = "MARKET_DATA"
    CROSS_VENUE = "CROSS_VENUE"
    NEWS = "NEWS"
    SOCIAL = "SOCIAL"
    STRUCTURED_DATA = "STRUCTURED_DATA"


@dataclass(frozen=True, slots=True)
class SourceCapability:
    """One governed input source and the roles it may play."""

    source_id: str
    roles: tuple[SourceRole, ...]
    domains: tuple[str, ...]
    authority: str
    maximum_age_seconds: int
    production_allowed: bool
    content_hash: str

    @classmethod
    def build(
        cls,
        *,
        source_id: str,
        roles: tuple[SourceRole, ...],
        domains: tuple[str, ...],
        authority: str,
        maximum_age_seconds: int,
        production_allowed: bool,
    ) -> SourceCapability:
        if not source_id.strip() or not roles or not domains or not authority.strip():
            raise ProductionStrategyError("source capability is incomplete")
        if maximum_age_seconds <= 0:
            raise ProductionStrategyError("source freshness bound must be positive")
        normalized_roles = tuple(sorted(set(roles), key=lambda role: role.value))
        normalized_domains = tuple(sorted(set(domain.strip() for domain in domains if domain.strip())))
        if not normalized_domains:
            raise ProductionStrategyError("source domains cannot be empty")
        values = (
            source_id,
            tuple(role.value for role in normalized_roles),
            normalized_domains,
            authority,
            maximum_age_seconds,
            production_allowed,
        )
        digest = stable_hash(values)
        return cls(
            source_id=source_id,
            roles=normalized_roles,
            domains=normalized_domains,
            authority=authority,
            maximum_age_seconds=maximum_age_seconds,
            production_allowed=production_allowed,
            content_hash=digest,
        )


@dataclass(frozen=True, slots=True)
class ModelRecipe:
    """Trainable model family independent of any one Kalshi series."""

    recipe_id: str
    algorithm: str
    supported_domains: tuple[str, ...]
    required_feature_groups: tuple[str, ...]
    calibration_method: str
    supports_retraining: bool
    supports_ensemble_weighting: bool
    content_hash: str

    @classmethod
    def build(
        cls,
        *,
        recipe_id: str,
        algorithm: str,
        supported_domains: tuple[str, ...],
        required_feature_groups: tuple[str, ...],
        calibration_method: str,
        supports_retraining: bool,
        supports_ensemble_weighting: bool,
    ) -> ModelRecipe:
        if not recipe_id.strip() or not algorithm.strip() or not calibration_method.strip():
            raise ProductionStrategyError("model recipe identity is incomplete")
        domains = tuple(sorted(set(item.strip() for item in supported_domains if item.strip())))
        feature_groups = tuple(
            sorted(set(item.strip() for item in required_feature_groups if item.strip()))
        )
        if not domains or not feature_groups:
            raise ProductionStrategyError("model recipe requires domains and feature groups")
        values = (
            recipe_id,
            algorithm,
            domains,
            feature_groups,
            calibration_method,
            supports_retraining,
            supports_ensemble_weighting,
        )
        digest = stable_hash(values)
        return cls(
            recipe_id=recipe_id,
            algorithm=algorithm,
            supported_domains=domains,
            required_feature_groups=feature_groups,
            calibration_method=calibration_method,
            supports_retraining=supports_retraining,
            supports_ensemble_weighting=supports_ensemble_weighting,
            content_hash=digest,
        )


@dataclass(frozen=True, slots=True)
class MarketFamilySpec:
    """Configuration for one discoverable Kalshi market family.

    ``selector`` is deliberately opaque here. A weather adapter may use a series prefix such
    as KXHIGHCHI; a future economics or politics adapter can use a different reviewed selector
    without changing the learning, scoring, promotion, or risk machinery.
    """

    family_id: str
    domain: str
    selector: str
    settlement_mapping_id: str
    source_ids: tuple[str, ...]
    feature_groups: tuple[str, ...]
    model_recipe_ids: tuple[str, ...]
    enabled: bool
    content_hash: str

    @classmethod
    def build(
        cls,
        *,
        family_id: str,
        domain: str,
        selector: str,
        settlement_mapping_id: str,
        source_ids: tuple[str, ...],
        feature_groups: tuple[str, ...],
        model_recipe_ids: tuple[str, ...],
        enabled: bool = True,
    ) -> MarketFamilySpec:
        required = (family_id, domain, selector, settlement_mapping_id)
        if any(not value.strip() for value in required):
            raise ProductionStrategyError("market family identity is incomplete")
        sources = tuple(sorted(set(item.strip() for item in source_ids if item.strip())))
        features = tuple(sorted(set(item.strip() for item in feature_groups if item.strip())))
        recipes = tuple(sorted(set(item.strip() for item in model_recipe_ids if item.strip())))
        if not sources or not features or not recipes:
            raise ProductionStrategyError("market family requires sources, features, and models")
        values = (
            family_id,
            domain,
            selector,
            settlement_mapping_id,
            sources,
            features,
            recipes,
            enabled,
        )
        digest = stable_hash(values)
        return cls(
            family_id=family_id,
            domain=domain,
            selector=selector,
            settlement_mapping_id=settlement_mapping_id,
            source_ids=sources,
            feature_groups=features,
            model_recipe_ids=recipes,
            enabled=enabled,
            content_hash=digest,
        )


@dataclass(frozen=True, slots=True)
class HeritageBaseline:
    """Frozen predecessor model retained as an evaluation benchmark.

    M27 can therefore continue contributing useful predictive information to M28 without
    becoming an adaptive production authority.  A baseline may be scored prospectively and
    compared with M28 challengers, but it has zero direct production influence.
    """

    baseline_id: str
    source_system: str
    source_model_id: str
    family_id: str
    evidence_manifest_ids: tuple[str, ...]
    frozen: bool
    production_influence: Decimal
    content_hash: str

    @classmethod
    def build(
        cls,
        *,
        source_system: str,
        source_model_id: str,
        family_id: str,
        evidence_manifest_ids: tuple[str, ...],
        frozen: bool = True,
        production_influence: Decimal = Decimal("0"),
    ) -> HeritageBaseline:
        if not source_system.strip() or not source_model_id.strip() or not family_id.strip():
            raise ProductionStrategyError("heritage baseline identity is incomplete")
        evidence = tuple(sorted(set(item.strip() for item in evidence_manifest_ids if item.strip())))
        if not evidence:
            raise ProductionStrategyError("heritage baseline requires evidence")
        if not frozen:
            raise ProductionStrategyError("heritage baseline must remain frozen")
        if production_influence != 0:
            raise ProductionStrategyError("heritage baseline has zero direct production influence")
        values = (source_system, source_model_id, family_id, evidence, frozen)
        digest = stable_hash(values)
        return cls(
            baseline_id=digest,
            source_system=source_system,
            source_model_id=source_model_id,
            family_id=family_id,
            evidence_manifest_ids=evidence,
            frozen=frozen,
            production_influence=production_influence,
            content_hash=digest,
        )


@dataclass(frozen=True, slots=True)
class StrategyRegistry:
    """Content-addressed universe configuration used by future scanners/tournaments."""

    registry_id: str
    sources: tuple[SourceCapability, ...]
    model_recipes: tuple[ModelRecipe, ...]
    market_families: tuple[MarketFamilySpec, ...]
    heritage_baselines: tuple[HeritageBaseline, ...]
    content_hash: str

    @classmethod
    def build(
        cls,
        *,
        sources: tuple[SourceCapability, ...],
        model_recipes: tuple[ModelRecipe, ...],
        market_families: tuple[MarketFamilySpec, ...],
        heritage_baselines: tuple[HeritageBaseline, ...] = (),
    ) -> StrategyRegistry:
        if not sources or not model_recipes or not market_families:
            raise ProductionStrategyError("strategy registry cannot be empty")
        source_map = {item.source_id: item for item in sources}
        recipe_map = {item.recipe_id: item for item in model_recipes}
        family_map = {item.family_id: item for item in market_families}
        if len(source_map) != len(sources) or len(recipe_map) != len(model_recipes):
            raise ProductionStrategyError("strategy registry ids must be unique")
        if len(family_map) != len(market_families):
            raise ProductionStrategyError("market family ids must be unique")

        for family in market_families:
            missing_sources = set(family.source_ids) - source_map.keys()
            missing_recipes = set(family.model_recipe_ids) - recipe_map.keys()
            if missing_sources:
                raise ProductionStrategyError("market family references unknown source")
            if missing_recipes:
                raise ProductionStrategyError("market family references unknown model recipe")
            for recipe_id in family.model_recipe_ids:
                if family.domain not in recipe_map[recipe_id].supported_domains:
                    raise ProductionStrategyError("model recipe does not support market domain")

        for baseline in heritage_baselines:
            if baseline.family_id not in family_map:
                raise ProductionStrategyError("heritage baseline references unknown family")

        values = (
            tuple(sorted((item.source_id, item.content_hash) for item in sources)),
            tuple(sorted((item.recipe_id, item.content_hash) for item in model_recipes)),
            tuple(sorted((item.family_id, item.content_hash) for item in market_families)),
            tuple(sorted((item.baseline_id, item.content_hash) for item in heritage_baselines)),
        )
        digest = stable_hash(values)
        return cls(
            registry_id=digest,
            sources=tuple(sorted(sources, key=lambda item: item.source_id)),
            model_recipes=tuple(sorted(model_recipes, key=lambda item: item.recipe_id)),
            market_families=tuple(sorted(market_families, key=lambda item: item.family_id)),
            heritage_baselines=tuple(sorted(heritage_baselines, key=lambda item: item.baseline_id)),
            content_hash=digest,
        )

    @property
    def enabled_family_ids(self) -> tuple[str, ...]:
        return tuple(item.family_id for item in self.market_families if item.enabled)
