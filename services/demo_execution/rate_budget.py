"""Deterministic write budget reserving capacity for safety operations."""

from dataclasses import dataclass

from .adapter import MutationKind


@dataclass(slots=True)
class WriteBudget:
    available: int
    safety_reserve: int

    def acquire(self, kind: MutationKind) -> bool:
        if self.available <= 0:
            return False
        safety = kind in {MutationKind.CANCEL, MutationKind.DECREASE}
        if not safety and self.available <= self.safety_reserve:
            return False
        self.available -= 1
        return True
