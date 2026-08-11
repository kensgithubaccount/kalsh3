"""Chronological evaluation, calibration targets, and collision diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .domain import SimulationError


@dataclass(frozen=True, slots=True)
class EvaluationPeriod:
    name: str
    start_at: datetime
    end_at: datetime


@dataclass(frozen=True, slots=True)
class WalkForwardManifest:
    training: EvaluationPeriod
    validation: EvaluationPeriod
    promotion: EvaluationPeriod
    tested_variants: tuple[str, ...]
    multiple_comparison_method: str
    event_groups: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not (
            self.training.end_at <= self.validation.start_at
            and self.validation.end_at <= self.promotion.start_at
        ):
            raise SimulationError("walk-forward periods overlap or run backward")
        grouped: dict[str, str] = {}
        for event_id, period in self.event_groups:
            prior = grouped.setdefault(event_id, period)
            if prior != period:
                raise SimulationError("related event leaked across evaluation periods")
        if not self.tested_variants:
            raise SimulationError("parameter sweep manifest cannot hide tested variants")


@dataclass(frozen=True, slots=True)
class ExecutionCalibrationTarget:
    attempted_at: datetime
    predicted_fill_probability: Decimal | None
    actual_simulated_fill: bool
    fill_fraction: Decimal
    time_to_first_fill_ms: int | None
    time_to_full_fill_ms: int | None
    predicted_slippage: Decimal | None
    simulated_slippage: Decimal | None
    predicted_fee: Decimal | None
    simulated_fee: Decimal | None
    model_status: str = "SYNTHETIC_VALIDATED"

    @property
    def fill_brier(self) -> Decimal | None:
        if self.predicted_fill_probability is None:
            return None
        outcome = Decimal(1) if self.actual_simulated_fill else Decimal(0)
        return (self.predicted_fill_probability - outcome) ** 2


@dataclass(frozen=True, slots=True)
class RealQueueObservation:
    """Future read-only calibration schema; M11 does not create the observed order."""

    observed_order_reference: str
    observed_at: datetime
    queue_position_fp: Decimal
    remaining_quantity_fp: Decimal
    price: Decimal
    subsequent_fill_quantity_fp: Decimal
    canceled: bool
    market_state: str
    validation_status: str = "NOT_VERIFIED"


@dataclass(frozen=True, slots=True)
class CollisionDiagnostic:
    market_ticker: str
    price: Decimal
    outcome_side: str
    simulated_order_ids: tuple[str, ...]
    shared_liquidity: Decimal
    collision: bool


def collision_diagnostic(
    market_ticker: str,
    price: Decimal,
    outcome_side: str,
    simulated_order_ids: tuple[str, ...],
    shared_liquidity: Decimal,
) -> CollisionDiagnostic:
    return CollisionDiagnostic(
        market_ticker,
        price,
        outcome_side,
        simulated_order_ids,
        shared_liquidity,
        len(simulated_order_ids) > 1,
    )
