"""Transport-independent authenticated subscription/reconnect/resnapshot state machine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from .orderbook import BookState, SequencedBook


class ConnectionState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    AUTHENTICATED = "AUTHENTICATED"
    RESYNCHRONIZING = "RESYNCHRONIZING"
    HEALTHY = "HEALTHY"


class SnapshotProvider(Protocol):
    def resnapshot(self, ticker: str) -> tuple[int, list[list[str]], list[list[str]], str]: ...


@dataclass(slots=True)
class RealtimeSupervisor:
    snapshot_provider: SnapshotProvider
    books: dict[str, SequencedBook] = field(default_factory=dict)
    subscriptions: set[str] = field(default_factory=set)
    state: ConnectionState = ConnectionState.DISCONNECTED
    reconnect_attempt: int = 0

    def subscribe(self, tickers: list[str]) -> None:
        self.subscriptions.update(tickers)
        self.books.update((x, self.books.get(x, SequencedBook(x))) for x in tickers)

    def connected(self, authenticated: bool) -> None:
        self.state = (
            ConnectionState.AUTHENTICATED if authenticated else ConnectionState.DISCONNECTED
        )
        if authenticated:
            self.resnapshot_all()

    def disconnected(self) -> float:
        self.state = ConnectionState.DISCONNECTED
        self.reconnect_attempt += 1
        for book in self.books.values():
            book.state = BookState.GAP
        return float(min(30.0, 0.5 * (2 ** min(self.reconnect_attempt, 6))))

    def resnapshot_all(self) -> None:
        from datetime import UTC, datetime

        self.state = ConnectionState.RESYNCHRONIZING
        for ticker in sorted(self.subscriptions):
            sequence, yes, no, structure = self.snapshot_provider.resnapshot(ticker)
            self.books[ticker].snapshot(sequence, yes, no, datetime.now(UTC), structure)
        self.state = ConnectionState.HEALTHY
        self.reconnect_attempt = 0

    def sequence_gap(self, ticker: str) -> None:
        from datetime import UTC, datetime

        self.state = ConnectionState.RESYNCHRONIZING
        sequence, yes, no, structure = self.snapshot_provider.resnapshot(ticker)
        self.books[ticker].snapshot(sequence, yes, no, datetime.now(UTC), structure)
        self.state = ConnectionState.HEALTHY
