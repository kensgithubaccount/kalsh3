"""Clock-controlled, scoped, streaming point-in-time replay."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .domain import ReplayError, ReplayEvent, order_key


def stream_available(events: Iterable[ReplayEvent], as_of: datetime) -> Iterator[ReplayEvent]:
    """Stream an already ordered source without materializing it in memory."""
    previous: tuple[datetime, int, int, int, str] | None = None
    for event in events:
        if event.availability.replay_available_at is None:
            continue
        key = order_key(event)
        if previous is not None and key < previous:
            raise ReplayError("replay source is not deterministically ordered")
        previous = key
        if key[0] > as_of:
            break
        yield event


class ReplayAccessor:
    """The sole strategy-facing view: future rows have no query surface."""

    def __init__(self, events: tuple[ReplayEvent, ...]) -> None:
        self._events = tuple(
            sorted((e for e in events if e.availability.replay_available_at), key=order_key)
        )

    def at(self, replay_at: datetime) -> tuple[ReplayEvent, ...]:
        return tuple(stream_available(self._events, replay_at))


@dataclass(frozen=True, slots=True)
class ReplayCheckpoint:
    replay_at: datetime
    last_event_id: str | None
    applied_count: int
    state: dict[str, Any]


class ReplayClock:
    def __init__(self, start_at: datetime, events: Iterable[ReplayEvent]) -> None:
        self.current = start_at
        self.paused = True
        self._events = iter(events)
        self._next: ReplayEvent | None = next(self._events, None)
        self.applied_count = 0
        self.last_event_id: str | None = None

    def pause(self) -> None:
        self.paused = True

    def step(self) -> ReplayEvent | None:
        event = self._next
        if event is None:
            return None
        available = event.availability.replay_available_at
        if available is None:
            self._next = next(self._events, None)
            return self.step()
        if available < self.current:
            raise ReplayError("clock received an out-of-order event")
        self.current = available
        self.last_event_id = event.event_id
        self.applied_count += 1
        self._next = next(self._events, None)
        return event

    def run_until(self, until: datetime) -> tuple[ReplayEvent, ...]:
        self.paused = False
        applied: list[ReplayEvent] = []
        while self._next is not None:
            available = self._next.availability.replay_available_at
            if available is not None and available > until:
                break
            event = self.step()
            if event is not None:
                applied.append(event)
        self.current = until
        self.paused = True
        return tuple(applied)

    def checkpoint(self, state: dict[str, Any]) -> ReplayCheckpoint:
        return ReplayCheckpoint(self.current, self.last_event_id, self.applied_count, dict(state))
