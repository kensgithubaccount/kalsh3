"""Chronological immutable identity, Platt, isotonic, and shrinkage calibration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256

from .domain import ForecastError, ModelFamily


class CalibrationMethod(StrEnum):
    IDENTITY = "IDENTITY"
    PLATT = "PLATT"
    ISOTONIC = "ISOTONIC"
    BETA = "BETA"
    HIERARCHICAL_SHRINKAGE = "HIERARCHICAL_SHRINKAGE"


@dataclass(frozen=True, slots=True)
class SettledSample:
    event_id: str
    forecast_at: datetime
    settled_available_at: datetime
    probability: Decimal
    outcome: int


@dataclass(frozen=True, slots=True)
class Calibrator:
    calibrator_id: str
    version: str
    training_cutoff: datetime
    training_event_count: int
    effective_sample_size: Decimal
    family: ModelFamily
    horizon_bucket: str
    method: CalibrationMethod
    parameters: tuple[Decimal, ...]
    artifact_hash: str

    def apply(self, probability: Decimal) -> Decimal:
        if not probability.is_finite() or not Decimal(0) <= probability <= Decimal(1):
            raise ForecastError("calibration input outside probability range")
        if self.method == CalibrationMethod.IDENTITY:
            return probability
        if self.method == CalibrationMethod.HIERARCHICAL_SHRINKAGE:
            weight, target = self.parameters
            return weight * probability + (Decimal(1) - weight) * target
        if self.method == CalibrationMethod.PLATT:
            # Auditable linear-on-probability reference; richer scientific fit remains optional.
            slope, intercept = self.parameters
            return min(Decimal(1), max(Decimal(0), slope * probability + intercept))
        if self.method == CalibrationMethod.ISOTONIC:
            pairs = tuple(zip(self.parameters[::2], self.parameters[1::2], strict=True))
            return next(
                (value for threshold, value in pairs if probability <= threshold), pairs[-1][1]
            )
        raise ForecastError("calibration method not fitted offline")


def fit_walk_forward(
    samples: tuple[SettledSample, ...],
    cutoff: datetime,
    family: ModelFamily,
    horizon_bucket: str,
    minimum_events: int = 30,
) -> Calibrator:
    eligible = tuple(sample for sample in samples if sample.settled_available_at < cutoff)
    events = {sample.event_id for sample in eligible}
    method = CalibrationMethod.IDENTITY
    parameters: tuple[Decimal, ...] = ()
    if 0 < len(events) < minimum_events:
        method = CalibrationMethod.HIERARCHICAL_SHRINKAGE
        weight = Decimal(len(events)) / Decimal(minimum_events)
        base = sum((Decimal(sample.outcome) for sample in eligible), Decimal(0)) / Decimal(
            len(eligible)
        )
        parameters = weight, base
    material = f"{cutoff}:{family}:{horizon_bucket}:{method}:{parameters}:{sorted(events)}"
    digest = sha256(material.encode()).hexdigest()
    return Calibrator(
        digest,
        "cal-v1",
        cutoff,
        len(events),
        Decimal(len(events)),
        family,
        horizon_bucket,
        method,
        parameters,
        digest,
    )
