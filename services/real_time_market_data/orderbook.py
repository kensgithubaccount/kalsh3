"""Exact YES/NO bid books with sequence gaps and fail-closed freshness."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from services.market_universe.domain import UniverseValidationError, exact


class BookState(StrEnum):
    EMPTY = "EMPTY"
    CURRENT = "CURRENT"
    GAP = "GAP"
    STALE = "STALE"


class PriceMode(StrEnum):
    UNIFIED_YES = "use_yes_price_true"
    LEGACY_SIDE = "legacy_side_price"


@dataclass(frozen=True, slots=True)
class BookView:
    ticker: str
    sequence: int
    yes_bids: tuple[tuple[Decimal, Decimal], ...]
    no_bids: tuple[tuple[Decimal, Decimal], ...]
    best_yes_bid: Decimal | None
    best_yes_ask: Decimal | None
    state: BookState
    observed_at: datetime
    ingested_at: datetime
    best_bid_size: Decimal | None
    best_ask_size: Decimal | None


@dataclass(slots=True)
class SequencedBook:
    ticker: str
    sequence: int = 0
    yes: dict[Decimal, Decimal] = field(default_factory=dict)
    no: dict[Decimal, Decimal] = field(default_factory=dict)
    state: BookState = BookState.EMPTY
    observed_at: datetime | None = None
    price_structure_hash: str | None = None
    price_mode: PriceMode = PriceMode.UNIFIED_YES
    ingested_at: datetime | None = None

    def snapshot(
        self,
        sequence: int,
        yes: Any,
        no: Any,
        observed_at: datetime,
        structure_hash: str,
        *,
        ingested_at: datetime | None = None,
        price_mode: PriceMode = PriceMode.UNIFIED_YES,
    ) -> None:
        if sequence < 0 or observed_at.tzinfo is None:
            raise UniverseValidationError("invalid snapshot envelope")
        self.yes = self._levels(yes)
        self.no = self._levels(no)
        self.sequence = sequence
        self.observed_at = observed_at.astimezone(UTC)
        self.price_structure_hash = structure_hash
        self.price_mode = price_mode
        self.ingested_at = (ingested_at or observed_at).astimezone(UTC)
        self.state = BookState.CURRENT

    def delta(
        self,
        sequence: int,
        side: str,
        price_value: Any,
        size_delta_value: Any,
        observed_at: datetime,
    ) -> bool:
        if self.state != BookState.CURRENT or sequence != self.sequence + 1:
            self.state = BookState.GAP
            return False
        if side not in {"yes", "no"}:
            self.state = BookState.GAP
            return False
        price, size_delta = exact(price_value, "price"), exact(size_delta_value, "size delta")
        levels = self.yes if side == "yes" else self.no
        new = levels.get(price, Decimal(0)) + size_delta
        if price <= 0 or price >= 1 or new < 0:
            self.state = BookState.GAP
            return False
        if new == 0:
            levels.pop(price, None)
        else:
            levels[price] = new
        self.sequence = sequence
        self.observed_at = observed_at.astimezone(UTC)
        self.ingested_at = observed_at.astimezone(UTC)
        return True

    def invalidate_for_metadata(self, new_structure_hash: str) -> None:
        if self.price_structure_hash != new_structure_hash:
            self.state = BookState.GAP

    def view(self, now: datetime, stale_after: timedelta = timedelta(seconds=30)) -> BookView:
        state = self.state
        if self.observed_at is None:
            observed = datetime.fromtimestamp(0, UTC)
        else:
            observed = self.observed_at
            if state == BookState.CURRENT and now - observed > stale_after:
                state = BookState.STALE
        bid = max(self.yes, default=None)
        if self.price_mode == PriceMode.UNIFIED_YES:
            ask = min(self.no, default=None)
        else:
            no_bid = max(self.no, default=None)
            ask = None if no_bid is None else Decimal(1) - no_bid
        bid_size = None if bid is None else self.yes[bid]
        ask_size = (
            None
            if ask is None
            else (
                self.no[ask]
                if self.price_mode == PriceMode.UNIFIED_YES
                else self.no[Decimal(1) - ask]
            )
        )
        return BookView(
            self.ticker,
            self.sequence,
            tuple(sorted(self.yes.items(), reverse=True)),
            tuple(sorted(self.no.items(), reverse=True)),
            bid,
            ask,
            state,
            observed,
            self.ingested_at or observed,
            bid_size,
            ask_size,
        )

    @staticmethod
    def _levels(raw: Any) -> dict[Decimal, Decimal]:
        if not isinstance(raw, list):
            raise UniverseValidationError("snapshot levels malformed")
        result = {}
        for item in raw:
            if not isinstance(item, list) or len(item) != 2:
                raise UniverseValidationError("snapshot level malformed")
            price, size = exact(item[0], "price"), exact(item[1], "size")
            if price <= 0 or price >= 1 or size <= 0 or price in result:
                raise UniverseValidationError("snapshot level invalid")
            result[price] = size
        return result
