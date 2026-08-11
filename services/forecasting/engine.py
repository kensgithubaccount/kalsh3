"""Independent and market-anchored research computations over frozen snapshots."""

from __future__ import annotations

from decimal import Decimal

from services.document_intelligence.models import EvidenceStatus

from .domain import ForecastError
from .models import FeatureSnapshot, ForecastKind, MarketReference


def validate_evidence_statuses(statuses: tuple[EvidenceStatus, ...]) -> None:
    if any(status != EvidenceStatus.VALIDATED for status in statuses):
        raise ForecastError("only validated M7 evidence may enter forecasting")


def independent_probability(snapshot: FeatureSnapshot, probability: Decimal) -> Decimal:
    snapshot.validate_for(ForecastKind.INDEPENDENT_FUNDAMENTAL)
    if not probability.is_finite() or not Decimal(0) <= probability <= Decimal(1):
        raise ForecastError("independent distribution probability invalid")
    return probability


def market_anchored_blend(
    reference: MarketReference, independent: Decimal, market_weight: Decimal = Decimal("0.7")
) -> Decimal:
    if not Decimal(0) <= market_weight <= Decimal(1):
        raise ForecastError("invalid fixed research blend")
    if not Decimal(0) <= independent <= Decimal(1):
        raise ForecastError("invalid independent probability")
    return (
        market_weight * reference.reference_probability + (Decimal(1) - market_weight) * independent
    )
