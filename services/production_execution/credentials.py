"""Future production-write credential domain; never installed by M15."""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True, repr=False)
class ProductionWriteCredential:
    key_id: str = field(repr=False)
    private_key_pem: bytes = field(repr=False)
    scopes: frozenset[str] = field(repr=False)
    environment: str = "PRODUCTION"
    credential_class: str = "PRODUCTION_WRITE"
    fixture_only: bool = False

    def __post_init__(self) -> None:
        if self.credential_class != "PRODUCTION_WRITE" or self.environment != "PRODUCTION":
            raise ValueError("credential-domain confusion")
        if self.scopes != frozenset({"read", "write"}):
            raise ValueError("explicit read and write scopes required")
        if not self.key_id or not self.private_key_pem:
            raise ValueError("complete credential required")

    def __repr__(self) -> str:
        return "ProductionWriteCredential(<redacted>)"


@dataclass(frozen=True, slots=True)
class CredentialHealth:
    installed: bool = False
    scope_validated: bool = False
    state: str = "NOT INSTALLED"


def enrollment_available() -> bool:
    """M15 intentionally has no reachable enrollment path."""
    return False
