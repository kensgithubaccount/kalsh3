"""Scheduled CPI-release fixtures with immutable vintage-safe transparent models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from .distributions import EmpiricalDistribution
from .domain import ForecastError


class ReleaseTarget(StrEnum):
    CPI = "CPI"
    PCE = "PCE"
    PAYROLLS = "PAYROLLS"
    UNEMPLOYMENT = "UNEMPLOYMENT"
    GDP = "GDP"
    CLAIMS = "CLAIMS"
    EIA = "EIA"


@dataclass(frozen=True, slots=True)
class ReleaseVintage:
    vintage_id: str
    target: ReleaseTarget
    series_id: str
    reference_period: str
    scheduled_at: datetime
    published_at: datetime
    replay_available_at: datetime
    value: Decimal
    unit: str
    revision_number: int
    revises_vintage_id: str | None
    source: str

    def visible(self, forecast_at: datetime) -> bool:
        return self.replay_available_at <= forecast_at


@dataclass(frozen=True, slots=True)
class ReleaseDefinition:
    target: ReleaseTarget
    timezone: str
    unit: str
    source: str
    series_id: str


def latest_visible_vintage(
    vintages: tuple[ReleaseVintage, ...], at: datetime
) -> ReleaseVintage | None:
    visible = [vintage for vintage in vintages if vintage.visible(at)]
    return max(
        visible, key=lambda item: (item.replay_available_at, item.revision_number), default=None
    )


def transparent_release_distribution(
    history: tuple[ReleaseVintage, ...],
    forecast_at: datetime,
    residuals: tuple[Decimal, ...],
    minimum: int = 12,
) -> tuple[Decimal, EmpiricalDistribution]:
    visible = tuple(
        vintage
        for vintage in history
        if vintage.visible(forecast_at) and vintage.revision_number == 0
    )
    if len(visible) < minimum or not residuals:
        raise ForecastError("insufficient scheduled-release training data")
    recent = visible[-3:]
    center = sum((item.value for item in recent), Decimal(0)) / Decimal(len(recent))
    distribution = EmpiricalDistribution.residuals((), residuals, minimum).shifted(center)
    return center, distribution
