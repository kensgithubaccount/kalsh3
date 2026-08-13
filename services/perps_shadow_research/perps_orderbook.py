"""Canonical ordinary bid/ask book for Perps margin markets."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from .domain import ShadowResearchError
from .perps_events import PerpsBookDeltaEvent, PerpsBookSide, PerpsBookSnapshotEvent
from .perps_metadata import PerpsMarketMetadata, canonical_hash


class PerpsBookState(StrEnum):
    EMPTY = "EMPTY"
    CURRENT = "CURRENT"
    CROSSED = "CROSSED"
    GAP = "GAP"
    STALE = "STALE"


@dataclass(frozen=True, slots=True)
class PerpsBookView:
    ticker: str
    sequence: int
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]
    best_bid: Decimal | None
    best_ask: Decimal | None
    best_bid_size: Decimal | None
    best_ask_size: Decimal | None
    state: PerpsBookState
    observed_at: datetime | None
    ingested_at: datetime | None
    full_book_hash: str

    @property
    def usable(self) -> bool:
        return self.state is PerpsBookState.CURRENT and (
            self.best_bid is not None or self.best_ask is not None
        )


@dataclass(slots=True)
class PerpsSequencedBook:
    market: PerpsMarketMetadata
    sequence: int = 0
    bids: dict[Decimal, Decimal] = field(default_factory=dict)
    asks: dict[Decimal, Decimal] = field(default_factory=dict)
    state: PerpsBookState = PerpsBookState.EMPTY
    observed_at: datetime | None = None
    ingested_at: datetime | None = None
    contract_hash: str = ""

    def __post_init__(self) -> None:
        self.contract_hash = self.market.perps_contract_hash

    def snapshot(self, event: PerpsBookSnapshotEvent, received_at: datetime) -> None:
        self._time(received_at)
        if event.ticker != self.market.ticker:
            raise ShadowResearchError("snapshot ticker mismatch")
        self.bids = dict(event.bids)
        self.asks = dict(event.asks)
        self.sequence = event.sequence
        self.observed_at = received_at.astimezone(UTC)
        self.ingested_at = received_at.astimezone(UTC)
        self.contract_hash = self.market.perps_contract_hash
        self.state = self._source_state()

    def delta(self, event: PerpsBookDeltaEvent, received_at: datetime) -> bool:
        self._time(received_at)
        if (
            self.state not in {PerpsBookState.CURRENT, PerpsBookState.CROSSED}
            or event.sequence != self.sequence + 1
        ):
            self.state = PerpsBookState.GAP
            return False
        levels = self.bids if event.side is PerpsBookSide.BID else self.asks
        new_size = levels.get(event.price, Decimal(0)) + event.delta
        if new_size < 0 or not self.market.quantity_valid(new_size):
            self.state = PerpsBookState.GAP
            return False
        if new_size == 0:
            levels.pop(event.price, None)
        else:
            levels[event.price] = new_size
        self.sequence = event.sequence
        self.observed_at = (event.exchange_at or received_at).astimezone(UTC)
        self.ingested_at = received_at.astimezone(UTC)
        self.state = self._source_state()
        return True

    def invalidate_for_metadata(self, market: PerpsMarketMetadata) -> bool:
        if market.ticker != self.market.ticker:
            raise ShadowResearchError("metadata ticker mismatch")
        self.market = market
        if self.contract_hash != market.perps_contract_hash:
            self.state = PerpsBookState.STALE
            return True
        return False

    def mark_stale(self) -> None:
        self.state = PerpsBookState.STALE

    def view(self, now: datetime, stale_after: timedelta = timedelta(seconds=30)) -> PerpsBookView:
        self._time(now)
        state = self.state
        if (
            self.observed_at is not None
            and state is PerpsBookState.CURRENT
            and now.astimezone(UTC) - self.observed_at > stale_after
        ):
            state = PerpsBookState.STALE
        bids = tuple(sorted(self.bids.items(), reverse=True))
        asks = tuple(sorted(self.asks.items()))
        best_bid = bids[0][0] if bids else None
        best_ask = asks[0][0] if asks else None
        return PerpsBookView(
            self.market.ticker,
            self.sequence,
            bids,
            asks,
            best_bid,
            best_ask,
            bids[0][1] if bids else None,
            asks[0][1] if asks else None,
            state,
            self.observed_at,
            self.ingested_at,
            canonical_hash(
                {
                    "ticker": self.market.ticker,
                    "sequence": self.sequence,
                    "bids": bids,
                    "asks": asks,
                }
            ),
        )

    def _source_state(self) -> PerpsBookState:
        if self.bids and self.asks and max(self.bids) >= min(self.asks):
            return PerpsBookState.CROSSED
        return PerpsBookState.CURRENT

    @staticmethod
    def _time(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ShadowResearchError("book timestamp must be timezone-aware")
