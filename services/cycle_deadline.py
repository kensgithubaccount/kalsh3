"""Absolute monotonic deadlines shared by bounded cycle stages."""

from __future__ import annotations

import time
from collections.abc import Callable


class CycleDeadlineExceeded(TimeoutError):
    """Raised when a cycle-wide deadline has expired at a named stage."""

    def __init__(self, stage: str) -> None:
        super().__init__(stage)
        self.stage = stage


class CycleDeadline:
    """One absolute deadline; all stage budgets are derived from it."""

    def __init__(
        self,
        budget_seconds: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        started_at: float | None = None,
    ) -> None:
        if budget_seconds <= 0:
            raise ValueError("budget_seconds must be positive")
        self.budget_seconds = budget_seconds
        self._monotonic = monotonic
        self._started_at = monotonic() if started_at is None else started_at
        self._deadline = self._started_at + budget_seconds

    @property
    def started_at(self) -> float:
        return self._started_at

    @property
    def deadline(self) -> float:
        return self._deadline

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, self._monotonic() - self._started_at)

    def remaining_seconds(self, stage: str) -> float:
        remaining = self._deadline - self._monotonic()
        if remaining <= 0:
            raise CycleDeadlineExceeded(stage)
        return remaining

    def check(self, stage: str) -> None:
        self.remaining_seconds(stage)

    def timeout_seconds(self, configured: float, stage: str) -> float:
        if configured <= 0:
            raise ValueError("configured timeout must be positive")
        return min(configured, self.remaining_seconds(stage))
