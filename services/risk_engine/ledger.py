"""Deterministic experiment ledger and conservative exposure projections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from .domain import EconomicAction, Ownership, RiskDomainError, RiskIntent
from .policy import RiskPolicy


class LedgerEntryType(StrEnum):
    CASH_COMMITTED = "CASH_COMMITTED"
    REALIZED_PNL = "REALIZED_PNL"
    FEE = "FEE"
    SETTLEMENT = "SETTLEMENT"
    OPEN_POSITION = "OPEN_POSITION"
    PENDING_ORDER = "PENDING_ORDER"


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    entry_id: str
    happened_at: datetime
    entry_type: LedgerEntryType
    amount: Decimal
    ownership: Ownership
    source_reference: str
    correction_of: str | None = None
    simulation: bool = False

    def __post_init__(self) -> None:
        if not self.amount.is_finite():
            raise RiskDomainError("ledger amount must be finite Decimal")


@dataclass(frozen=True, slots=True)
class ExperimentCapitalLedger:
    starting_experiment_capital: Decimal
    experiment_cash_committed: Decimal
    experiment_realized_pnl: Decimal
    experiment_fees: Decimal
    experiment_settlements: Decimal
    open_experiment_positions: Decimal
    pending_experiment_orders: Decimal
    experiment_equity: Decimal
    high_water_mark: Decimal
    drawdown: Decimal
    entry_ids: tuple[str, ...]

    @classmethod
    def build(
        cls,
        entries: tuple[LedgerEntry, ...],
        starting_capital: Decimal = Decimal("300"),
        prior_high_water_mark: Decimal | None = None,
    ) -> ExperimentCapitalLedger:
        seen: set[str] = set()
        totals = {kind: Decimal(0) for kind in LedgerEntryType}
        for entry in entries:
            if entry.entry_id in seen:
                raise RiskDomainError("duplicate ledger entry")
            seen.add(entry.entry_id)
            if entry.simulation:
                continue
            if entry.ownership != Ownership.BOT_OWNED:
                continue
            totals[entry.entry_type] += entry.amount
        equity = (
            starting_capital
            + totals[LedgerEntryType.REALIZED_PNL]
            - totals[LedgerEntryType.FEE]
            + totals[LedgerEntryType.SETTLEMENT]
        )
        high = max(starting_capital, prior_high_water_mark or starting_capital, equity)
        return cls(
            starting_capital,
            totals[LedgerEntryType.CASH_COMMITTED],
            totals[LedgerEntryType.REALIZED_PNL],
            totals[LedgerEntryType.FEE],
            totals[LedgerEntryType.SETTLEMENT],
            totals[LedgerEntryType.OPEN_POSITION],
            totals[LedgerEntryType.PENDING_ORDER],
            equity,
            high,
            high - equity,
            tuple(sorted(seen)),
        )


def available_active_capital(
    *,
    account_equity: Decimal,
    committed: Decimal,
    pending_commitments: Decimal,
    policy: RiskPolicy,
) -> Decimal:
    unprotected = max(Decimal(0), account_equity - policy.protected_reserve)
    return max(
        Decimal(0),
        min(policy.active_capital, unprotected) - committed - pending_commitments,
    )


@dataclass(frozen=True, slots=True)
class ExposureProjection:
    market_risk: Decimal
    event_risk: Decimal
    aggregate_risk: Decimal
    cash_commitment: Decimal
    risk_reducing: bool


def project_full_fill(
    *,
    intent: RiskIntent,
    current_market_risk: Decimal,
    current_event_risk: Decimal,
    current_aggregate_risk: Decimal,
    existing_resting_market_risk: Decimal,
    existing_resting_event_risk: Decimal,
    existing_resting_aggregate_risk: Decimal,
    directional_liability_increases: bool = True,
) -> ExposureProjection:
    if intent.maximum_loss_if_filled is None or intent.maximum_expected_cash_commitment is None:
        raise RiskDomainError("intent risk and commitment are required")
    opening = intent.maximum_loss_if_filled
    base_market = current_market_risk + existing_resting_market_risk
    base_event = current_event_risk + existing_resting_event_risk
    base_aggregate = current_aggregate_risk + existing_resting_aggregate_risk
    if intent.economic_action == EconomicAction.REDUCE_EXISTING_EXPOSURE:
        market = max(Decimal(0), base_market - opening)
        event = max(Decimal(0), base_event - opening)
        aggregate = max(Decimal(0), base_aggregate - opening)
    else:
        market = base_market + opening
        event = base_event + opening
        aggregate = base_aggregate + opening
    reducing = (
        intent.economic_action == EconomicAction.REDUCE_EXISTING_EXPOSURE
        and intent.reduce_only
        and not directional_liability_increases
        and market <= base_market
        and event <= base_event
        and aggregate <= base_aggregate
    )
    return ExposureProjection(
        market,
        event,
        aggregate,
        intent.maximum_expected_cash_commitment,
        reducing,
    )
