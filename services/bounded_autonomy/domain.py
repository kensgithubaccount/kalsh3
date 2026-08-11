"""Non-active M17 progression model with no execution or activation capability."""

from dataclasses import dataclass
from enum import StrEnum


class AutonomyState(StrEnum):
    OFF = "OFF"


@dataclass(frozen=True, slots=True)
class AutonomyEvidence:
    supervised_canary_live_verified: bool
    real_reconciliation_verified: bool
    strategy_evidence_sufficient: bool
    signer_runtime_verified: bool
    postgres_concurrency_verified: bool
    human_governance_approved: bool

    def state(self) -> AutonomyState:
        return AutonomyState.OFF

    def missing(self) -> tuple[str, ...]:
        return tuple(name for name in self.__dataclass_fields__ if getattr(self, name) is not True)
