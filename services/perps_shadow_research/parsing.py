"""Conservative parsers for margin/perps research payloads.

Unknown fields are retained in `raw`. Missing risk fields remain None. No derived
position-level margin values are manufactured.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .domain import (
    LeverageEstimate,
    MarginMarketObservation,
    PortfolioMarginObservation,
    ShadowResearchError,
)


def _decimal(value: Any, field: str) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ShadowResearchError(f"{field} must be numeric when present") from exc


def _estimate_tuple(value: Any, field: str) -> tuple[LeverageEstimate, ...]:
    if value is None:
        return ()
    if not isinstance(value, dict):
        raise ShadowResearchError(f"{field} must be a notional-keyed mapping")
    rows: list[LeverageEstimate] = []
    for notional, leverage in value.items():
        parsed_notional = _decimal(notional, f"{field}.notional")
        parsed_leverage = _decimal(leverage, f"{field}.leverage")
        if parsed_notional is None or parsed_leverage is None:
            raise ShadowResearchError(f"{field} entries must be numeric")
        rows.append(
            LeverageEstimate(
                notional=parsed_notional,
                leverage=parsed_leverage,
            )
        )
    return tuple(sorted(rows, key=lambda row: row.notional))


def parse_margin_market(raw: dict[str, Any], *, observed_at: datetime) -> MarginMarketObservation:
    if not isinstance(raw, dict):
        raise ShadowResearchError("margin market payload must be an object")
    ticker = raw.get("ticker")
    exchange_index = raw.get("exchange_index")
    if not isinstance(ticker, str) or not ticker:
        raise ShadowResearchError("ticker missing")
    if not isinstance(exchange_index, int):
        raise ShadowResearchError("exchange_index missing or invalid")

    return MarginMarketObservation(
        ticker=ticker,
        exchange_index=exchange_index,
        observed_at=observed_at,
        long_leverage_estimates=_estimate_tuple(
            raw.get("long_leverage_estimates"), "long_leverage_estimates"
        ),
        short_leverage_estimates=_estimate_tuple(
            raw.get("short_leverage_estimates"), "short_leverage_estimates"
        ),
        symmetric_leverage_estimates=_estimate_tuple(
            raw.get("leverage_estimates"), "leverage_estimates"
        ),
        funding_rate=_decimal(raw.get("funding_rate"), "funding_rate"),
        mark_price=_decimal(raw.get("mark_price"), "mark_price"),
        reference_price=_decimal(raw.get("reference_price"), "reference_price"),
        raw=dict(raw),
    )


def parse_portfolio_margin(
    raw: dict[str, Any],
    *,
    observed_at: datetime,
    subaccount: int,
    exchange_index: int,
) -> PortfolioMarginObservation:
    if not isinstance(raw, dict):
        raise ShadowResearchError("portfolio margin payload must be an object")
    return PortfolioMarginObservation(
        subaccount=subaccount,
        exchange_index=exchange_index,
        observed_at=observed_at,
        available_balance=_decimal(raw.get("available_balance"), "available_balance"),
        portfolio_value=_decimal(raw.get("portfolio_value"), "portfolio_value"),
        margin_used=_decimal(raw.get("margin_used"), "margin_used"),
        maintenance_margin=_decimal(raw.get("maintenance_margin"), "maintenance_margin"),
        raw=dict(raw),
    )
