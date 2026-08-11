"""Non-atomic hypothetical cross-venue leg-risk research."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class LegState(StrEnum):
    BOTH_FILL = "BOTH_FILL"
    KALSHI_ONLY = "KALSHI_ONLY"
    POLYMARKET_ONLY = "POLYMARKET_ONLY"
    NEITHER = "NEITHER"
    SECOND_LEG_REPRICED = "SECOND_LEG_REPRICED"
    VENUE_STALE = "VENUE_STALE"
    VENUE_PAUSED = "VENUE_PAUSED"


@dataclass(frozen=True, slots=True)
class CrossVenueLegSimulation:
    simulation_id: str
    kalshi_arrival: datetime
    polymarket_arrival: datetime
    kalshi_filled: bool
    polymarket_hypothetical_filled: bool
    state: LegState
    kalshi_cost: Decimal | None
    polymarket_cost: Decimal | None
    leg_risk_loss: Decimal
    basis_max_loss: Decimal
    label: str = "HYPOTHETICAL CROSS-VENUE SIMULATION"
    production_influence: Decimal = Decimal(0)

    @classmethod
    def evaluate(
        cls,
        *,
        simulation_id: str,
        kalshi_arrival: datetime,
        polymarket_arrival: datetime,
        kalshi_filled: bool,
        polymarket_filled: bool,
        kalshi_cost: Decimal | None,
        polymarket_cost: Decimal | None,
        second_leg_repriced: bool = False,
        venue_stale: bool = False,
        venue_paused: bool = False,
        leg_risk_loss: Decimal = Decimal(0),
        basis_max_loss: Decimal = Decimal(0),
    ) -> CrossVenueLegSimulation:
        if venue_paused:
            state = LegState.VENUE_PAUSED
        elif venue_stale:
            state = LegState.VENUE_STALE
        elif second_leg_repriced:
            state = LegState.SECOND_LEG_REPRICED
        elif kalshi_filled and polymarket_filled:
            state = LegState.BOTH_FILL
        elif kalshi_filled:
            state = LegState.KALSHI_ONLY
        elif polymarket_filled:
            state = LegState.POLYMARKET_ONLY
        else:
            state = LegState.NEITHER
        return cls(
            simulation_id,
            kalshi_arrival,
            polymarket_arrival,
            kalshi_filled,
            polymarket_filled,
            state,
            kalshi_cost,
            polymarket_cost,
            leg_risk_loss,
            basis_max_loss,
        )
