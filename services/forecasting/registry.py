"""Research-only model registry, cards, and checkpoint policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from .domain import ForecastError, ModelFamily
from .models import FeatureProvenance, ForecastKind


class ModelStatus(StrEnum):
    DEVELOPMENT = "DEVELOPMENT"
    RESEARCH = "RESEARCH"
    SHADOW = "SHADOW"
    CHALLENGER = "CHALLENGER"
    RETIRED = "RETIRED"
    QUARANTINED = "QUARANTINED"


class ResearchStanding(StrEnum):
    BASELINE = "BASELINE"
    CHAMPION_RESEARCH = "CHAMPION_RESEARCH"
    CHALLENGER_RESEARCH = "CHALLENGER_RESEARCH"
    RETIRED_RESEARCH = "RETIRED_RESEARCH"


@dataclass(frozen=True, slots=True)
class RegisteredModel:
    model_id: str
    version: str
    family: ModelFamily
    target: str
    model_type: str
    forecast_kind: ForecastKind
    feature_schema: str
    allowed_provenance: frozenset[FeatureProvenance]
    training_window: tuple[datetime, datetime]
    training_dataset_manifest: str
    calibration_relationship: str
    code_version: str
    parameters: tuple[tuple[str, str], ...]
    artifact_hash: str
    status: ModelStatus
    created_at: datetime

    def __post_init__(self) -> None:
        market = {
            FeatureProvenance.KALSHI_MARKET_DERIVED,
            FeatureProvenance.EXTERNAL_MARKET_DERIVED,
        }
        if (
            self.forecast_kind == ForecastKind.INDEPENDENT_FUNDAMENTAL
            and self.allowed_provenance & market
        ):
            raise ForecastError("independent model registry permits market-derived inputs")


@dataclass(frozen=True, slots=True)
class ModelCard:
    model_id: str
    intended_use: str
    family: ModelFamily
    target: str
    inputs: tuple[str, ...]
    prohibited_inputs: tuple[str, ...]
    training_methodology: str
    calibration: str
    uncertainty: str
    limitations: tuple[str, ...]
    required_data: tuple[str, ...]
    replay_fidelity: str
    evaluation_metrics: tuple[str, ...]
    current_sample_size: int
    known_failure_modes: tuple[str, ...]
    status: ModelStatus
    production_influence: int = 0

    def __post_init__(self) -> None:
        if self.production_influence != 0:
            raise ForecastError("model cards must state production influence NONE")


@dataclass(frozen=True, slots=True)
class CheckpointPolicy:
    family: ModelFamily
    horizons: tuple[timedelta, ...]
    require_market_exists: bool = True
    require_inputs: bool = True
    require_valid_rules: bool = True
    require_replay_fidelity: bool = True


@dataclass(slots=True)
class ModelRegistry:
    models: dict[tuple[str, str], RegisteredModel] = field(default_factory=dict)

    def register(self, model: RegisteredModel) -> None:
        key = model.model_id, model.version
        if key in self.models:
            raise ForecastError("immutable model version already registered")
        self.models[key] = model
