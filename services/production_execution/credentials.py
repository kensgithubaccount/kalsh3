"""Future production-write credential domain; never installed by M15."""

from dataclasses import dataclass, field

# Per current official Kalshi API-key scope documentation (docs.kalshi.com/api-reference
# /api-keys), "write" is a broad parent scope granting every write endpoint, including
# fund transfers and block-trade acceptance. "write::trade" is an independently grantable
# child scope that permits only order create/cancel/amend/decrease -- exactly the M16
# supervised-canary trade-lifecycle surface -- without transfer or block-trade authority.
# "read" (the broad read parent) covers balance/orders/positions/fills/settlements/limits.
REQUIRED_LIVE_WRITE_SCOPES: frozenset[str] = frozenset({"read", "write::trade"})
REQUIRED_LIVE_WRITE_SUBACCOUNT = 0


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
        if self.scopes != REQUIRED_LIVE_WRITE_SCOPES:
            raise ValueError("exact read and write::trade scopes are required")
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
