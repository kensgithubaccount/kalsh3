"""Read-only composition from accepted realtime books to research evidence."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from services.real_time_market_data.events import BookDeltaEvent, BookSnapshotEvent
from services.real_time_market_data.manager import SubscriptionManager
from services.real_time_market_data.orderbook import BookState

from .book_evidence import (
    BookEvidenceObservation,
    BookUpdateKind,
    source_event_fingerprint,
)
from .domain import ShadowResearchError
from .store import BookEvidenceStore


class ReadOnlyBookEvidencePipeline:
    def __init__(
        self,
        manager: SubscriptionManager,
        store: BookEvidenceStore,
        exchange_indexes: Mapping[str, int],
        utc_clock: Callable[[], datetime],
    ) -> None:
        self._manager = manager
        self._store = store
        self._exchange_indexes = exchange_indexes
        self._clock = utc_clock

    def _epoch(self) -> UUID:
        epoch = self._manager.epoch
        if not isinstance(epoch, UUID) or epoch.int == 0:
            raise ShadowResearchError("a non-zero connection epoch is required")
        return epoch

    def _exchange_index(self, ticker: str) -> int:
        try:
            value = self._exchange_indexes[ticker]
        except KeyError as exc:
            raise ShadowResearchError("unknown exchange index") from exc
        if type(value) is not int or value < 0:
            raise ShadowResearchError("exchange_index must be a non-negative integer")
        return value

    @staticmethod
    def _utc(value: datetime, field: str) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ShadowResearchError(f"{field} must be timezone-aware")
        return value.astimezone(UTC)

    def snapshot(
        self,
        event: BookSnapshotEvent,
        *,
        received_at: datetime,
        structure_hash: str,
    ) -> BookEvidenceObservation | None:
        epoch = self._epoch()
        exchange_index = self._exchange_index(event.ticker)
        received_at = self._utc(received_at, "received_at")
        fingerprint = source_event_fingerprint(event)
        replay = self._preflight(event, epoch, fingerprint)
        if replay is not None:
            return replay
        self._manager.install_snapshot(event, received_at, structure_hash)
        available_at = self._utc(self._clock(), "available_at")
        return self._persist(
            event,
            BookUpdateKind.SNAPSHOT,
            exchange_index,
            epoch,
            received_at,
            available_at,
            None,
            fingerprint,
        )

    def delta(
        self, event: BookDeltaEvent, *, received_at: datetime
    ) -> BookEvidenceObservation | None:
        epoch = self._epoch()
        exchange_index = self._exchange_index(event.ticker)
        received_at = self._utc(received_at, "received_at")
        fingerprint = source_event_fingerprint(event)
        replay = self._preflight(event, epoch, fingerprint)
        if replay is not None:
            return replay
        if not self._manager.apply_delta(event, received_at):
            return None
        available_at = self._utc(self._clock(), "available_at")
        return self._persist(
            event,
            BookUpdateKind.DELTA,
            exchange_index,
            epoch,
            received_at,
            available_at,
            event.exchange_at,
            fingerprint,
        )

    def _preflight(
        self,
        event: BookSnapshotEvent | BookDeltaEvent,
        epoch: UUID,
        fingerprint: str,
    ) -> BookEvidenceObservation | None:
        existing = self._store.get_by_source_identity(epoch, event.sid, event.seq, event.ticker)
        if existing is None:
            return None
        if existing.source_event_fingerprint != fingerprint:
            raise ShadowResearchError("book source-event logical identity collision")
        return existing

    def _persist(
        self,
        event: BookSnapshotEvent | BookDeltaEvent,
        kind: BookUpdateKind,
        exchange_index: int,
        epoch: UUID,
        received_at: datetime,
        available_at: datetime,
        exchange_at: datetime | None,
        fingerprint: str,
    ) -> BookEvidenceObservation | None:
        book = self._manager.books.get(event.ticker)
        if book is None:
            return None
        view = book.view(available_at)
        if view.state is not BookState.CURRENT:
            return None
        if not book.price_structure_hash:
            raise ShadowResearchError("book structure hash is required")
        observation = BookEvidenceObservation.create(
            update_kind=kind,
            ticker=event.ticker,
            market_id=event.market_id,
            exchange_index=exchange_index,
            connection_epoch=epoch,
            sid=event.sid,
            sequence=event.seq,
            source_event_fingerprint=fingerprint,
            book_state=view.state,
            best_yes_bid=view.best_yes_bid,
            best_yes_ask=view.best_yes_ask,
            best_bid_size=view.best_bid_size,
            best_ask_size=view.best_ask_size,
            exchange_at=exchange_at,
            received_at=received_at,
            available_at=available_at,
            price_mode=book.price_mode,
            structure_hash=book.price_structure_hash,
            production_influence=Decimal("0"),
        )
        self._store.append(observation)
        return observation
