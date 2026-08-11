"""Public M15 boundary: permanently DISARMED and without a signer or credential."""

from dataclasses import dataclass, field
from typing import Protocol

from .credentials import CredentialHealth
from .domain import ProductionArmState


class IsolatedSigner(Protocol):
    def sign_bound_request(self, request_hash: str, one_time_capability: bytes) -> bytes: ...


@dataclass(frozen=True, slots=True)
class ProductionExecutionBoundary:
    arm_state: ProductionArmState = ProductionArmState.DISARMED
    credential: CredentialHealth = field(default_factory=CredentialHealth)
    production_write_credential_present: bool = False
    signer: IsolatedSigner | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.arm_state != ProductionArmState.DISARMED:
            raise ValueError("M15 cannot leave DISARMED")

    def preflight(self) -> None:
        if (
            self.credential.installed
            or self.production_write_credential_present
            or self.signer is not None
        ):
            raise ValueError("initial M15 boundary accepts no production credential or signer")
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
