"""Demo-only observations that never rewrite M10 or M11 artifacts."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class QueueComparison:
    observed_at: datetime
    market: str
    modeled_queue: Decimal
    observed_demo_queue: Decimal
    market_state: str

    @property
    def discrepancy(self) -> Decimal:
        return self.observed_demo_queue - self.modeled_queue


@dataclass(frozen=True, slots=True)
class FeeComparison:
    predicted_fee: Decimal
    actual_demo_fee: Decimal
    fee_policy_version: str

    @property
    def difference(self) -> Decimal:
        return self.actual_demo_fee - self.predicted_fee


@dataclass(frozen=True, slots=True)
class SlippageComparison:
    m10_current_book: Decimal
    m11_simulated_arrival: Decimal
    actual_demo_execution: Decimal

    @property
    def m11_error(self) -> Decimal:
        return self.actual_demo_execution - self.m11_simulated_arrival
