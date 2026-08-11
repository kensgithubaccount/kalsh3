"""Safe M2 invalidation/actions derived from realtime lifecycle events."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from .events import LifecycleEvent, LifecycleKind
from .orderbook import BookState, SequencedBook


class CanonicalAction(StrEnum):
    DISCOVER = "DISCOVER"
    REFRESH_METADATA = "REFRESH_METADATA"
    INVALIDATE_BOOK = "INVALIDATE_BOOK"
    MARK_INACTIVE = "MARK_INACTIVE"
    RECORD_DETERMINATION = "RECORD_DETERMINATION"
    RECONCILE_FINALIZED = "RECONCILE_FINALIZED"


@dataclass(slots=True)
class RefreshJob:
    ticker: str
    action: CanonicalAction
    attempts: int
    not_before: datetime
    reason: str


@dataclass(slots=True)
class LifecycleIntegrator:
    jobs: list[RefreshJob] = field(default_factory=list)
    events: list[LifecycleEvent] = field(default_factory=list)
    max_created_attempts: int = 5

    def accept(
        self, event: LifecycleEvent, received_at: datetime, books: dict[str, SequencedBook]
    ) -> tuple[CanonicalAction, ...]:
        self.events.append(event)
        actions = []
        if event.kind == LifecycleKind.CREATED:
            actions.append(CanonicalAction.DISCOVER)
            self.jobs.append(
                RefreshJob(
                    event.ticker, CanonicalAction.DISCOVER, 0, received_at, "created_404_race"
                )
            )
        if event.kind in {
            LifecycleKind.ACTIVATED,
            LifecycleKind.CLOSE_DATE_UPDATED,
            LifecycleKind.METADATA_UPDATED,
        }:
            actions.append(CanonicalAction.REFRESH_METADATA)
            self.jobs.append(
                RefreshJob(
                    event.ticker, CanonicalAction.REFRESH_METADATA, 0, received_at, event.kind.value
                )
            )
        if event.kind == LifecycleKind.DEACTIVATED:
            actions.append(CanonicalAction.MARK_INACTIVE)
        if event.kind == LifecycleKind.PRICE_STRUCTURE_UPDATED:
            actions.extend((CanonicalAction.REFRESH_METADATA, CanonicalAction.INVALIDATE_BOOK))
            if event.ticker in books:
                books[event.ticker].state = BookState.GAP
            self.jobs.append(
                RefreshJob(
                    event.ticker, CanonicalAction.REFRESH_METADATA, 0, received_at, event.kind.value
                )
            )
        if event.kind == LifecycleKind.DETERMINED:
            actions.append(CanonicalAction.RECORD_DETERMINATION)
        if event.kind == LifecycleKind.SETTLED:
            actions.append(CanonicalAction.RECONCILE_FINALIZED)
        return tuple(actions)

    def retry_created_404(self, job: RefreshJob, now: datetime) -> RefreshJob | None:
        if job.reason != "created_404_race" or job.attempts + 1 >= self.max_created_attempts:
            return None
        attempt = job.attempts + 1
        return RefreshJob(
            job.ticker,
            job.action,
            attempt,
            now + timedelta(seconds=min(30, 2**attempt)),
            job.reason,
        )
