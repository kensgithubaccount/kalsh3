"""Versioned fee-policy boundary; unverified schedules fail closed."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_CEILING, Decimal
from enum import StrEnum

from .domain import OpportunityError


class FeeType(StrEnum):
    QUADRATIC = "quadratic"
    QUADRATIC_WITH_MAKER_FEES = "quadratic_with_maker_fees"
    FLAT = "flat"


@dataclass(frozen=True, slots=True)
class FeePolicy:
    policy_id: str
    fee_type: FeeType
    fee_multiplier: Decimal
    effective_at: datetime
    retired_at: datetime | None
    formula_version: str
    source_reference: str
    verified: bool
    flat_rate: Decimal | None = None
    quadratic_coefficient: Decimal | None = None
    balance_rounding_increment: Decimal = Decimal("0.0001")

    def applies_at(self, at: datetime) -> bool:
        return self.effective_at <= at and (self.retired_at is None or at < self.retired_at)


@dataclass(frozen=True, slots=True)
class FeeCalculation:
    theoretical_trade_fee: Decimal
    rounding_component: Decimal
    expected_rebate: Decimal
    total_fee: Decimal
    policy_id: str


def select_policy(policies: tuple[FeePolicy, ...], at: datetime) -> FeePolicy:
    eligible = [policy for policy in policies if policy.applies_at(at)]
    if len(eligible) != 1:
        raise OpportunityError("fee policy unavailable or historical overlap")
    return eligible[0]


def calculate_fee(
    policy: FeePolicy, price: Decimal, quantity: Decimal, maker: bool = False
) -> FeeCalculation:
    if not policy.verified:
        raise OpportunityError("fee model unverified")
    if not Decimal(0) < price < Decimal(1) or quantity <= 0:
        raise OpportunityError("fee input invalid")
    if policy.fee_type == FeeType.FLAT and policy.flat_rate is not None:
        theoretical = policy.flat_rate * quantity * policy.fee_multiplier
    elif (
        policy.fee_type in {FeeType.QUADRATIC, FeeType.QUADRATIC_WITH_MAKER_FEES}
        and policy.quadratic_coefficient is not None
    ):
        # The coefficient and formula version must come from verified policy metadata.
        theoretical = (
            policy.quadratic_coefficient
            * price
            * (Decimal(1) - price)
            * quantity
            * policy.fee_multiplier
        )
    else:
        raise OpportunityError("unknown or incomplete fee type")
    rebate = Decimal(0)
    if maker and policy.fee_type != FeeType.QUADRATIC_WITH_MAKER_FEES:
        rebate = Decimal(0)
    rounded = (theoretical / policy.balance_rounding_increment).to_integral_value(
        rounding=ROUND_CEILING
    ) * policy.balance_rounding_increment
    return FeeCalculation(
        theoretical, rounded - theoretical, rebate, rounded - rebate, policy.policy_id
    )
