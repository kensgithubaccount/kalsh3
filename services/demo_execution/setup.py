"""Owner-controlled encrypted DEMO-only credential enrollment."""

from dataclasses import dataclass
from typing import Protocol

from services.web_dashboard.security import SecretBox
from services.web_dashboard.store import StateStore

from .domain import DEMO_REST_ORIGIN, DemoWriteCredential


class DemoCredentialValidator(Protocol):
    def __call__(self, credential: DemoWriteCredential, origin: str, subaccount: int) -> bool: ...


@dataclass(frozen=True, slots=True)
class DemoCredentialSetup:
    store: StateStore
    box: SecretBox
    validator: DemoCredentialValidator

    def enroll(
        self,
        *,
        key_id: str,
        pem: bytes,
        requested_origin: str,
        strongly_reauthenticated: bool,
        explicit_confirmation: str,
    ) -> None:
        if not strongly_reauthenticated or explicit_confirmation != "INSTALL DEMO CREDENTIAL":
            raise ValueError("strong owner confirmation required")
        if requested_origin != DEMO_REST_ORIGIN:
            raise ValueError("only the dedicated demo origin is accepted")
        credential = DemoWriteCredential(key_id, pem)
        if not self.validator(credential, DEMO_REST_ORIGIN, 0):
            raise ValueError("credential did not validate against demo account 0")
        # Names are deliberately distinct from the production-read account vault.
        self.store.set_config("m14_demo_write_key_id", self.box.seal(key_id.encode()))
        self.store.set_config("m14_demo_write_private_key", self.box.seal(pem))
        self.store.audit("demo_credential_installed", "owner", "DEMO host; subaccount 0")

    def load(self) -> DemoWriteCredential | None:
        key_id = self.store.config("m14_demo_write_key_id")
        pem = self.store.config("m14_demo_write_private_key")
        if key_id is None or pem is None:
            return None
        return DemoWriteCredential(self.box.open(key_id).decode(), self.box.open(pem))
