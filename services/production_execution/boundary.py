"""Public M15 boundary: permanently DISARMED, with no signer API or credential slot."""

from dataclasses import dataclass, field

from .credentials import CredentialHealth
from .domain import ProductionArmState


@dataclass(frozen=True, slots=True)
class ProductionExecutionBoundary:
    arm_state: ProductionArmState = ProductionArmState.DISARMED
    credential: CredentialHealth = field(default_factory=CredentialHealth)

    def __post_init__(self) -> None:
        if self.arm_state != ProductionArmState.DISARMED:
            raise ValueError("M15 cannot leave DISARMED")

    def preflight(self) -> None:
        if self.credential.installed:
            raise ValueError("initial M15 boundary accepts no production credential")
        raise PermissionError("production execution is DISARMED and unavailable")

    @staticmethod
    def health() -> dict[str, object]:
        return {
            "process_healthy": True,
            "credential_installed": False,
            "scope_validated": False,
            "production_state": "DISARMED",
            "signer_version": "m15-sign-and-send-v1",
        }
