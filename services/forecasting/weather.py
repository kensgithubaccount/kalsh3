"""Fixture-ready weather semantics, source observations, and specialist model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

from .distributions import EmpiricalDistribution
from .domain import ForecastError


class WeatherSourceRole(StrEnum):
    FORECAST_SOURCE = "FORECAST_SOURCE"
    LIVE_OBSERVATION_SOURCE = "LIVE_OBSERVATION_SOURCE"
    PRELIMINARY_CLIMATE_PRODUCT = "PRELIMINARY_CLIMATE_PRODUCT"
    FINAL_OFFICIAL_SETTLEMENT_SOURCE = "FINAL_OFFICIAL_SETTLEMENT_SOURCE"


@dataclass(frozen=True, slots=True)
class WeatherContract:
    station_id: str
    location: str
    measurement: str
    local_date: date
    timezone: str
    lower: Decimal
    upper: Decimal | None
    comparator: str
    unit: str
    rounding: Decimal | None
    settlement_authority: str
    settlement_source: str
    revision_policy: str

    def validate(self) -> None:
        required = (
            self.station_id,
            self.location,
            self.measurement,
            self.timezone,
            self.unit,
            self.settlement_authority,
            self.settlement_source,
            self.revision_policy,
        )
        if any(not value for value in required):
            raise ForecastError("ambiguous weather contract")
        ZoneInfo(self.timezone)
        if self.unit not in {"degF", "degC"} or self.measurement not in {"DAILY_MAX", "DAILY_MIN"}:
            raise ForecastError("unsupported weather unit or measurement")


@dataclass(frozen=True, slots=True)
class WeatherSourceRecord:
    role: WeatherSourceRole
    station_id: str
    request_at: datetime
    issued_at: datetime
    valid_at: datetime
    ingest_at: datetime
    value: Decimal
    unit: str
    source_hash: str
    provider_status: str
    parser_version: str
    correction_of: str | None = None

    def visible(self, at: datetime) -> bool:
        return self.ingest_at <= at


def convert_temperature(value: Decimal, source_unit: str, target_unit: str) -> Decimal:
    if source_unit == target_unit:
        return value
    if source_unit == "degC" and target_unit == "degF":
        return value * Decimal(9) / Decimal(5) + 32
    if source_unit == "degF" and target_unit == "degC":
        return (value - 32) * Decimal(5) / Decimal(9)
    raise ForecastError("unit ambiguity")


@dataclass(frozen=True, slots=True)
class WeatherForecastResult:
    probability: Decimal
    outcome_interval: tuple[Decimal, Decimal]
    pooled_weight: Decimal


def forecast_weather(
    contract: WeatherContract,
    central: Decimal,
    station_residuals: tuple[Decimal, ...],
    pooled_residuals: tuple[Decimal, ...],
    observed_max: Decimal | None = None,
) -> WeatherForecastResult:
    contract.validate()
    distribution = EmpiricalDistribution.residuals(station_residuals, pooled_residuals).shifted(
        central, observed_max if contract.measurement == "DAILY_MAX" else None
    )
    probability = distribution.probability(
        contract.comparator, contract.lower, contract.upper, contract.rounding
    )
    return WeatherForecastResult(
        probability, distribution.interval(Decimal("0.9")), distribution.pooled_weight
    )
