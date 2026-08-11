"""Separate production-write budget preserving safety operations."""

from dataclasses import dataclass

from .domain import AuthorityClass


@dataclass(slots=True)
class ProductionWriteBudget:
    available: int
    safety_reserve: int

    def acquire(self, authority: AuthorityClass) -> bool:
        safety = authority in {AuthorityClass.RISK_REDUCING, AuthorityClass.EMERGENCY_SAFETY}
        if self.available <= 0 or (not safety and self.available <= self.safety_reserve):
            return False
        self.available -= 1
        return True
