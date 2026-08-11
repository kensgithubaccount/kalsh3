"""Immutable source observations, exchange determinations, final settlements, and fair queueing."""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class DeterminationState(StrEnum):
    DETERMINED = "DETERMINED"
    DISPUTED = "DISPUTED"
    AMENDED = "AMENDED"
    FINALIZED = "FINALIZED"


class ReconciliationStatus(StrEnum):
    UNRECONCILED = "UNRECONCILED"
    MATCHED = "MATCHED"
    MISMATCH = "MISMATCH"


@dataclass(frozen=True, slots=True)
class SourceObservation:
    observation_id: str
    market_ticker: str
    source_id: str
    value: Decimal | str
    observed_at: datetime
    published_at: datetime
    content_hash: str
    is_correction: bool
    supersedes_observation_id: str | None


@dataclass(frozen=True, slots=True)
class ExchangeDetermination:
    determination_id: str
    market_ticker: str
    state: DeterminationState
    result: str | None
    settlement_value_dollars: Decimal | None
    exchange_at: datetime
    received_at: datetime
    raw_hash: str
    supersedes_determination_id: str | None


@dataclass(frozen=True, slots=True)
class SettlementRecord:
    market_ticker: str
    rules_version: str
    semantic_spec_id: str
    result: str
    settlement_value_dollars: Decimal
    determined_at: datetime
    finalized_at: datetime | None
    exchange_record_hash: str
    source_observation_id: str | None
    reconciliation_status: ReconciliationStatus

    @property
    def eligible_training_label(self) -> bool:
        return (
            self.finalized_at is not None
            and self.reconciliation_status == ReconciliationStatus.MATCHED
        )


@dataclass(order=True, slots=True)
class QueueItem:
    priority: tuple[int, float, int]
    market_ticker: str = field(compare=False)
    reason: str = field(compare=False)


class SettlementQueue:
    def __init__(self) -> None:
        self._heap: list[QueueItem] = []
        self._counter = 0

    def add(
        self,
        market_ticker: str,
        *,
        state: str,
        has_open_position: bool,
        expected_at: datetime,
        now: datetime,
        lifecycle_determined: bool,
        stale: bool,
    ) -> None:
        self._counter += 1
        state_rank = (
            0 if lifecycle_determined or state == "determined" else 1 if state == "closed" else 2
        )
        position_rank = 0 if has_open_position else 1
        age = (expected_at - now).total_seconds()
        heapq.heappush(
            self._heap,
            QueueItem(
                (state_rank * 10 + position_rank, age, self._counter),
                market_ticker,
                "determination" if lifecycle_determined else state,
            ),
        )

    def pop(self, limit: int) -> list[QueueItem]:
        return [heapq.heappop(self._heap) for _ in range(min(max(limit, 0), len(self._heap)))]
