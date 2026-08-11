"""Content-addressed research configurations, replay selection, events, and rollback."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from services.historical_replay.archive import stable_hash

from .domain import LearningError


class ConfigurationMode(StrEnum):
    HISTORICAL_OBSERVED_CONFIGURATION = "HISTORICAL_OBSERVED_CONFIGURATION"
    RETROSPECTIVE_RESEARCH_CONFIGURATION = "RETROSPECTIVE_RESEARCH_CONFIGURATION"


@dataclass(frozen=True, slots=True)
class LearningConfiguration:
    configuration_id: str
    active_models: tuple[str, ...]
    model_weights: tuple[tuple[str, Decimal], ...]
    active_sources: tuple[str, ...]
    source_weights: tuple[tuple[str, Decimal], ...]
    family_routing: tuple[tuple[str, str], ...]
    abstention_thresholds: tuple[tuple[str, Decimal], ...]
    version: str
    effective_at: datetime
    predecessor_id: str | None
    mode: ConfigurationMode
    content_hash: str
    production_influence: Decimal = Decimal("0")

    @classmethod
    def build(cls, **values: object) -> LearningConfiguration:
        if values.get("production_influence", Decimal(0)) != 0:
            raise LearningError("learning configuration has zero production influence")
        for field in ("model_weights", "source_weights"):
            weights = values.get(field)
            if not isinstance(weights, tuple) or any(
                not Decimal(0) <= weight <= Decimal(1) for _, weight in weights
            ):
                raise LearningError("configuration weight invalid")
        digest = stable_hash(tuple(sorted(values.items())))
        return cls(configuration_id=digest, content_hash=digest, **values)  # type: ignore[arg-type]


def configuration_at(
    configurations: tuple[LearningConfiguration, ...],
    at: datetime,
    allow_retrospective: bool = False,
) -> LearningConfiguration | None:
    eligible = [
        config
        for config in configurations
        if config.effective_at <= at
        and (
            allow_retrospective
            or config.mode == ConfigurationMode.HISTORICAL_OBSERVED_CONFIGURATION
        )
    ]
    return max(eligible, key=lambda config: config.effective_at, default=None)


@dataclass(frozen=True, slots=True)
class LearningEvent:
    event_id: str
    happened_at: datetime
    actor: str
    event_type: str
    previous_configuration: str | None
    new_configuration: str | None
    evidence: tuple[str, ...]
    reason: str
    status: str


def rollback(
    current: LearningConfiguration, previous: LearningConfiguration, effective_at: datetime
) -> LearningConfiguration:
    values = {
        name: getattr(previous, name)
        for name in previous.__dataclass_fields__
        if name not in {"configuration_id", "content_hash", "effective_at", "predecessor_id"}
    }
    return LearningConfiguration.build(
        **values, effective_at=effective_at, predecessor_id=current.configuration_id
    )
