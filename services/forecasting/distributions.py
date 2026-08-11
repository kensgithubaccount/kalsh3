"""Small auditable empirical distributions and exact contract-threshold conversion."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .domain import ForecastError


@dataclass(frozen=True, slots=True)
class EmpiricalDistribution:
    values: tuple[Decimal, ...]
    pooled_weight: Decimal
    station_sample_count: int

    @classmethod
    def residuals(
        cls, station: tuple[Decimal, ...], pooled: tuple[Decimal, ...], minimum_station: int = 30
    ) -> EmpiricalDistribution:
        if not pooled:
            raise ForecastError("insufficient historical error model")
        weight = min(Decimal(1), Decimal(len(station)) / Decimal(minimum_station))
        station_take = int((Decimal(len(pooled)) * weight).to_integral_value())
        combined = tuple(sorted(station[:station_take] + pooled[station_take:]))
        return cls(combined or pooled, Decimal(1) - weight, len(station))

    def shifted(
        self, center: Decimal, observed_floor: Decimal | None = None
    ) -> EmpiricalDistribution:
        values = tuple(
            max(center + residual, observed_floor)
            if observed_floor is not None
            else center + residual
            for residual in self.values
        )
        return EmpiricalDistribution(
            tuple(sorted(values)), self.pooled_weight, self.station_sample_count
        )

    def probability(
        self,
        comparator: str,
        lower: Decimal,
        upper: Decimal | None = None,
        rounding: Decimal | None = None,
    ) -> Decimal:
        values = self.values
        if rounding is not None:
            if rounding <= 0:
                raise ForecastError("unsupported rounding")
            values = tuple(
                (value / rounding).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * rounding
                for value in values
            )
        if comparator == "GT":
            selected = sum(value > lower for value in values)
        elif comparator == "GTE":
            selected = sum(value >= lower for value in values)
        elif comparator == "LT":
            selected = sum(value < lower for value in values)
        elif comparator == "LTE":
            selected = sum(value <= lower for value in values)
        elif comparator == "EQ":
            selected = sum(value == lower for value in values)
        elif comparator == "RANGE" and upper is not None:
            selected = sum(lower <= value <= upper for value in values)
        else:
            raise ForecastError("unsupported comparator")
        return Decimal(selected) / Decimal(len(values))

    def interval(self, level: Decimal) -> tuple[Decimal, Decimal]:
        if not Decimal(0) < level < Decimal(1):
            raise ForecastError("invalid interval level")
        tail = (Decimal(1) - level) / Decimal(2)
        low = int((tail * len(self.values)).to_integral_value())
        high = min(
            len(self.values) - 1, int(((Decimal(1) - tail) * len(self.values)).to_integral_value())
        )
        return self.values[low], self.values[high]


def coherent_bins(
    distribution: EmpiricalDistribution, bins: tuple[tuple[Decimal, Decimal], ...]
) -> tuple[Decimal, ...]:
    probabilities = tuple(distribution.probability("RANGE", low, high) for low, high in bins)
    if sum(probabilities) != Decimal(1):
        raise ForecastError("bins are not exhaustive and mutually exclusive")
    return probabilities
