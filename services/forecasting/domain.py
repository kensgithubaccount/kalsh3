"""Frozen family-specific forecasts benchmarked against executable market prices."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class ForecastError(ValueError):
    pass


class ModelFamily(StrEnum):
    WEATHER = "WEATHER"
    SCHEDULED_ECONOMIC_RELEASE = "SCHEDULED_ECONOMIC_RELEASE"


@dataclass(frozen=True, slots=True)
class MarketBaseline:
    observed_at: datetime
    yes_bid: Decimal
    yes_ask: Decimal
    no_bid: Decimal
    no_ask: Decimal

    def __post_init__(self) -> None:
        if any(
            value < 0 or value > 1
            for value in (self.yes_bid, self.yes_ask, self.no_bid, self.no_ask)
        ):
            raise ForecastError("executable price outside probability scale")
        if self.yes_bid > self.yes_ask or self.no_bid > self.no_ask:
            raise ForecastError("crossed executable market baseline")


@dataclass(frozen=True, slots=True)
class FrozenForecast:
    forecast_id: str
    market_ticker: str
    family: ModelFamily
    created_at: datetime
    target_at: datetime
    probability_yes: Decimal
    lower_bound: Decimal
    upper_bound: Decimal
    model_version: str
    feature_vintage_hash: str
    evidence_bundle_ids: tuple[str, ...]
    baseline: MarketBaseline
    production_influence: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if not (
            Decimal("0")
            <= self.lower_bound
            <= self.probability_yes
            <= self.upper_bound
            <= Decimal("1")
        ):
            raise ForecastError("invalid calibrated uncertainty interval")
        if self.production_influence != 0:
            raise ForecastError("M8 forecasts have zero production influence")

    @property
    def comparison_to_executable_ask(self) -> Decimal:
        """Descriptive research difference, never an order or expected profit."""
        return self.probability_yes - self.baseline.yes_ask


@dataclass(frozen=True, slots=True)
class CalibrationBin:
    lower: Decimal
    upper: Decimal
    forecast_count: int
    mean_forecast: Decimal
    observed_frequency: Decimal | None

    def __post_init__(self) -> None:
        if self.forecast_count == 0 and self.observed_frequency is not None:
            raise ForecastError("empty calibration bin has no observed frequency")
