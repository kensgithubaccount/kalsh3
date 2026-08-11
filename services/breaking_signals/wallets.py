"""Public wallet/trader observations remain descriptive attention signals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class PublicTraderProfile:
    stable_wallet_id: str
    provider_display_name: str | None
    provider_rank: int | None
    provider_realized_pnl: Decimal | None
    observed_volume: Decimal
    activity_count: int
    market_families: tuple[str, ...]
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class WalletObservation:
    wallet_id: str
    market_id: str
    absolute_size: Decimal
    relative_to_recent_volume: Decimal | None
    relative_to_visible_depth: Decimal | None
    relative_to_trader_typical: Decimal | None
    concentration: Decimal | None
    directional_accumulation: Decimal | None
    vendor_label: str | None
    vendor: str | None
    observed_at: datetime
    action: str = "RESEARCH_ATTENTION_ONLY"
