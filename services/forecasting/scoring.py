"""Proper scores and same-checkpoint market-relative research evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext

from .domain import ForecastError


@dataclass(frozen=True, slots=True)
class ProperScore:
    brier: Decimal
    log_loss: Decimal
    log_clipped_for_computation: bool


def score(
    probability: Decimal, outcome: int, epsilon: Decimal = Decimal("0.000001")
) -> ProperScore:
    if (
        outcome not in {0, 1}
        or not probability.is_finite()
        or not Decimal(0) <= probability <= Decimal(1)
    ):
        raise ForecastError("invalid score input")
    clipped = min(Decimal(1) - epsilon, max(epsilon, probability))
    with localcontext() as context:
        context.prec = 28
        loss = -(
            Decimal(outcome) * clipped.ln() + Decimal(1 - outcome) * (Decimal(1) - clipped).ln()
        )
    return ProperScore((probability - Decimal(outcome)) ** 2, loss, clipped != probability)


@dataclass(frozen=True, slots=True)
class RelativeScore:
    model: ProperScore
    market: ProperScore
    brier_skill: Decimal | None
    log_loss_improvement: Decimal


def relative_score(
    model_probability: Decimal,
    market_probability: Decimal,
    outcome: int,
    model_snapshot_time: object,
    market_snapshot_time: object,
) -> RelativeScore:
    if model_snapshot_time != market_snapshot_time:
        raise ForecastError("market baseline must use the same forecast checkpoint")
    model, market = score(model_probability, outcome), score(market_probability, outcome)
    skill = None if market.brier == 0 else Decimal(1) - model.brier / market.brier
    return RelativeScore(model, market, skill, market.log_loss - model.log_loss)
