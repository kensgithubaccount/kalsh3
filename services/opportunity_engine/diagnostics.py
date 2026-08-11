"""Liquidity, information decay, correlation, and fee reconciliation diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from .models import InformationDecay, LiquidityDiagnostic


def liquidity(
    spread: Decimal,
    price: Decimal,
    top_quantity: Decimal,
    depth_within_ticks: Decimal,
    recent_trade_count: int,
    recent_quantity: Decimal,
    open_interest: Decimal,
    quote_age_ms: int,
    stability: str,
) -> LiquidityDiagnostic:
    relative = None if price == 0 else spread / price
    return LiquidityDiagnostic(
        spread,
        relative,
        top_quantity,
        depth_within_ticks,
        recent_trade_count,
        recent_quantity,
        open_interest,
        quote_age_ms,
        stability,
    )


def information_decay(
    age: timedelta, recent_velocity: Decimal, time_to_close: timedelta
) -> tuple[InformationDecay, Decimal]:
    if age < timedelta(minutes=2) and recent_velocity > Decimal(".02"):
        return InformationDecay.FAST, Decimal(".4")
    if age < timedelta(hours=1) and time_to_close > timedelta(hours=1):
        return InformationDecay.MODERATE, Decimal(".75")
    if age < timedelta(days=1):
        return InformationDecay.SLOW, Decimal(".9")
    return InformationDecay.UNKNOWN, Decimal(".5")


@dataclass(frozen=True, slots=True)
class CorrelationCluster:
    cluster_id: str
    underlying_event_id: str
    markets: tuple[str, ...]
    mutually_exclusive: bool
    exhaustive: bool
    duplicate_thesis: bool


@dataclass(frozen=True, slots=True)
class FeeReconciliation:
    predicted_fee: Decimal
    actual_fee: Decimal
    predicted_rounding: Decimal
    actual_rounding: Decimal
    tolerance: Decimal

    @property
    def status(self) -> str:
        return (
            "FEE_MODEL_MISMATCH"
            if abs(self.predicted_fee - self.actual_fee) > self.tolerance
            or abs(self.predicted_rounding - self.actual_rounding) > self.tolerance
            else "MATCH"
        )
