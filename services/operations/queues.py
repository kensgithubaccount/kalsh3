"""Deterministic queue/backpressure and crash-recovery policy."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class QueuePolicy:
    soft_limit: int = 5_000
    hard_limit: int = 10_000
    maximum_delivery_attempts: int = 5

    def __post_init__(self) -> None:
        if not 0 < self.soft_limit < self.hard_limit:
            raise ValueError("queue limits must be ordered and positive")
        if self.maximum_delivery_attempts < 1:
            raise ValueError("delivery attempt limit must be positive")

    def action(self, depth: int, attempts: int = 0) -> str:
        if depth < 0 or attempts < 0:
            raise ValueError("queue counters cannot be negative")
        if attempts >= self.maximum_delivery_attempts:
            return "DEAD_LETTER_AND_ALERT"
        if depth >= self.hard_limit:
            return "HALT_NEW_RISK"
        if depth >= self.soft_limit:
            return "BACKPRESSURE_RESEARCH"
        return "ACCEPT"
