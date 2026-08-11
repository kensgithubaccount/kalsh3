"""Future protected enrollment validation; deliberately not wired to any route or service."""

from dataclasses import dataclass
from typing import Protocol

from .credentials import ProductionWriteCredential


class SecretSealer(Protocol):
    def seal(self, value: bytes) -> str: ...


@dataclass(frozen=True, slots=True)
class SealedCredentialPackage:
    sealed_key_id: str
    sealed_private_key: str
    scope_state: str = "READ_WRITE_VALIDATED"
    account: int = 0
    environment: str = "PRODUCTION"


def prepare_enrollment_fixture(
    *,
    credential: ProductionWriteCredential,
    sealer: SecretSealer,
    owner_authenticated: bool,
    password_reauthenticated: bool,
    totp_valid: bool,
    explicit_confirmation: str,
    validated_environment: str,
    validated_account: int,
    fixture_validator: bool,
) -> SealedCredentialPackage:
    """Models future backend workflow using fixtures; it never installs the package."""
    if not fixture_validator or not credential.fixture_only:
        raise PermissionError("live production enrollment is unavailable in M15")
    if not all((owner_authenticated, password_reauthenticated, totp_valid)):
        raise PermissionError("strong owner reauthentication required")
    if (
        explicit_confirmation != "PREPARE PRODUCTION WRITE CREDENTIAL"
        or validated_environment != "PRODUCTION"
    ):
        raise PermissionError("explicit production warning confirmation required")
    if validated_account != 0 or credential.scopes != frozenset({"read", "write"}):
        raise PermissionError("scope or account validation failed")
    return SealedCredentialPackage(
        sealer.seal(credential.key_id.encode()), sealer.seal(credential.private_key_pem)
    )
