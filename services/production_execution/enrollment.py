"""Protected operator-only write-credential enrollment boundary.

The command that invokes this boundary is intentionally not run by M27E.  This module
accepts synthetic credentials in tests, stores only sealed material, and emits a receipt
containing hashes and metadata rather than secrets.
"""

import hashlib
import json
import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from services.kalshi_account_gateway.auth import RequestSigner
from services.kalshi_account_gateway.production_read_credentials import (
    API_KEYS_PATH,
    PRODUCTION_ORIGIN,
    ProductionReadReply,
    ProductionReadTransport,
    ReadSigner,
)
from services.neutral_security import SecretBox

from .credentials import (
    REQUIRED_LIVE_WRITE_SCOPES,
    REQUIRED_LIVE_WRITE_SUBACCOUNT,
    ProductionWriteCredential,
)

# Full current official Kalshi API-key scope enum (docs.kalshi.com/api-reference/api-keys):
# broad read/write parents plus the independently grantable child scopes. Any scope string
# outside this set on a server-reported key is treated as unknown/untrusted, not silently
# accepted.
KNOWN_KALSHI_API_KEY_SCOPES: frozenset[str] = frozenset(
    {
        "read",
        "write",
        "read::block_trade_accept",
        "read::portfolio_balance",
        "write::trade",
        "write::transfer",
        "write::block_trade_accept",
    }
)


class SecretSealer(Protocol):
    def seal(self, value: bytes) -> str: ...


class WriteCredentialAuthorityError(PermissionError):
    """The candidate key's server-reported metadata did not prove live write authority."""


@dataclass(frozen=True, slots=True)
class WriteCredentialServerProof:
    """Parsed, format-validated ``GET /api_keys`` metadata for one candidate key.

    Producing this does not by itself authorize installation: :func:`require_live_write_authority`
    still checks it against the exact scope/subaccount/identity policy.
    """

    key_id: str
    scopes: frozenset[str]
    subaccount: int | None


def verify_live_write_credential_authority(
    transport: ProductionReadTransport,
    credential: ProductionWriteCredential,
    *,
    timestamp_ms: int,
    signer_factory: Callable[[str, bytes], ReadSigner] = RequestSigner,
) -> WriteCredentialServerProof:
    """Authenticate as the candidate key and fetch its own server-reported metadata.

    This calls ``GET /trade-api/v2/api_keys`` -- the same endpoint the M25B5 read-credential
    boundary uses -- signed by the candidate key itself, so the response describes exactly
    the key that would be installed rather than a caller-declared claim about it. M27E never
    invokes this: no real write credential exists in this milestone. It exists so a future
    enrollment command need not trust caller-declared scope/subaccount metadata.
    """
    signer = signer_factory(credential.key_id, credential.private_key_pem)
    headers = signer.headers(timestamp_ms, "GET", API_KEYS_PATH)
    try:
        reply = transport.get(PRODUCTION_ORIGIN, API_KEYS_PATH, headers, timeout_seconds=10)
    except (TimeoutError, OSError) as exc:
        raise WriteCredentialAuthorityError("production key metadata transport failed") from exc
    return _parse_server_proof(reply, expected_key_id=credential.key_id)


def require_live_write_authority(
    proof: WriteCredentialServerProof, *, expected_key_id: str
) -> None:
    """Enforce the exact live scope/subaccount/identity policy against a fetched proof.

    Rejects: identity mismatch, any scope set other than exactly ``{read, write::trade}``
    (broad ``write``, extra/unknown scopes, missing ``write::trade``, missing ``read``), a
    null/unrestricted subaccount, and any subaccount other than 0.
    """
    if proof.key_id != expected_key_id:
        raise WriteCredentialAuthorityError("production key metadata identity mismatch")
    if proof.scopes != REQUIRED_LIVE_WRITE_SCOPES:
        raise WriteCredentialAuthorityError(
            "production key does not hold exactly read and write::trade"
        )
    if proof.subaccount != REQUIRED_LIVE_WRITE_SUBACCOUNT:
        raise WriteCredentialAuthorityError("production key is not restricted to subaccount 0")


def _parse_server_proof(
    reply: ProductionReadReply, *, expected_key_id: str
) -> WriteCredentialServerProof:
    if reply.location is not None or 300 <= reply.status < 400:
        raise WriteCredentialAuthorityError("production key metadata redirect rejected")
    if reply.status in {401, 403}:
        raise WriteCredentialAuthorityError("production key metadata authentication rejected")
    if reply.status != 200 or reply.content_type != "application/json":
        raise WriteCredentialAuthorityError("production key metadata response rejected")
    try:
        payload = json.loads(reply.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WriteCredentialAuthorityError("production key metadata response malformed") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("api_keys"), list):
        raise WriteCredentialAuthorityError("production key metadata response malformed")
    records = payload["api_keys"]
    if any(not isinstance(item, dict) for item in records):
        raise WriteCredentialAuthorityError("production key metadata response malformed")
    matches = [item for item in records if item.get("api_key_id") == expected_key_id]
    if len(matches) != 1:
        raise WriteCredentialAuthorityError("exact production key not uniquely identified")
    match = matches[0]
    raw_scopes = match.get("scopes")
    if (
        not isinstance(raw_scopes, list)
        or not raw_scopes
        or any(type(item) is not str for item in raw_scopes)
        or len(raw_scopes) != len(set(raw_scopes))
        or not set(raw_scopes) <= KNOWN_KALSHI_API_KEY_SCOPES
    ):
        raise WriteCredentialAuthorityError("production key metadata scopes malformed")
    subaccount: int | None = match.get("subaccount")
    if subaccount is not None and (type(subaccount) is bool or type(subaccount) is not int):
        raise WriteCredentialAuthorityError("production key metadata subaccount malformed")
    if subaccount is not None and not 0 <= subaccount <= 63:
        raise WriteCredentialAuthorityError("production key metadata subaccount out of range")
    return WriteCredentialServerProof(expected_key_id, frozenset(raw_scopes), subaccount)


@dataclass(frozen=True, slots=True)
class SealedCredentialPackage:
    sealed_key_id: str
    sealed_private_key: str
    scope_state: str = "READ_WRITE_TRADE_VALIDATED"
    account: int = 0
    environment: str = "PRODUCTION"


CONFIRMATION = "INSTALL PRODUCTION WRITE CREDENTIAL — REAL MONEY — ONE CONTRACT ONLY"


@dataclass(frozen=True, slots=True, repr=False)
class InstallationReceipt:
    key_id_hash: str
    private_key_hash: str
    credential_fingerprint: str
    installed_at: datetime
    environment: str = "PRODUCTION"
    account: int = 0
    scopes: tuple[str, ...] = ("read", "write::trade")

    def __repr__(self) -> str:
        return "InstallationReceipt(<secret-free>)"


class ProtectedWriteCredentialStore:
    """Atomic sealed store for the future isolated signer credential."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.master_key_path = directory / "master.key"
        self.record_path = directory / "credential.enc"

    def install(
        self, credential: ProductionWriteCredential, *, now: datetime
    ) -> InstallationReceipt:
        if not credential.fixture_only:
            raise PermissionError("real enrollment requires an explicit operator action")
        self.directory.mkdir(parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        if self.master_key_path.exists() or self.record_path.exists():
            raise PermissionError("write credential is already installed")
        master = secrets.token_bytes(32)
        _atomic_bytes(self.master_key_path, master)
        payload = json.dumps(
            {
                "key_id": credential.key_id,
                "private_key_pem": credential.private_key_pem.decode("ascii"),
            },
            sort_keys=True,
        ).encode("ascii")
        try:
            _atomic_text(self.record_path, SecretBox(master).seal(payload))
        except Exception:
            self.master_key_path.unlink(missing_ok=True)
            self.record_path.unlink(missing_ok=True)
            raise
        return InstallationReceipt(
            hashlib.sha256(credential.key_id.encode()).hexdigest(),
            hashlib.sha256(credential.private_key_pem).hexdigest(),
            hashlib.sha256(credential.private_key_pem).hexdigest(),
            now.astimezone(UTC),
        )


def enroll_live_write_credential(
    *,
    credential: ProductionWriteCredential,
    store: ProtectedWriteCredentialStore,
    owner_authenticated: bool,
    password_reauthenticated: bool,
    totp_valid: bool,
    csrf_valid: bool,
    explicit_confirmation: str,
    validated_environment: str,
    validated_account: int,
    validated_scopes: frozenset[str],
    server_proof: WriteCredentialServerProof,
) -> InstallationReceipt:
    """Validate every operator gate before an atomic sealed installation.

    ``fixture_only`` is required in this repository milestone so an accidental call
    cannot install a real production key.  A later operator-controlled release may
    replace that final boundary with a hardware/vault-backed verifier.

    ``validated_scopes``/``validated_account`` are the caller-declared claim; ``server_proof``
    is independent evidence fetched with :func:`verify_live_write_credential_authority` from
    the key's own authenticated ``GET /api_keys`` metadata. Both must agree with the exact
    live policy -- a caller-declared claim alone is never sufficient.
    """
    if not all((owner_authenticated, password_reauthenticated, totp_valid, csrf_valid)):
        raise PermissionError("owner reauthentication, TOTP, and CSRF are required")
    if explicit_confirmation != CONFIRMATION:
        raise PermissionError("exact real-money warning confirmation required")
    if validated_environment != "PRODUCTION" or validated_account != REQUIRED_LIVE_WRITE_SUBACCOUNT:
        raise PermissionError("production account 0 is required")
    if validated_scopes != REQUIRED_LIVE_WRITE_SCOPES:
        raise PermissionError("exact read and write::trade scopes are required")
    if credential.scopes != REQUIRED_LIVE_WRITE_SCOPES or credential.environment != "PRODUCTION":
        raise PermissionError("credential scope or environment mismatch")
    require_live_write_authority(server_proof, expected_key_id=credential.key_id)
    return store.install(credential, now=datetime.now(UTC))


def _atomic_bytes(path: Path, value: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, value)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_text(path: Path, value: str) -> None:
    _atomic_bytes(path, value.encode("ascii"))


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
    if (
        validated_account != REQUIRED_LIVE_WRITE_SUBACCOUNT
        or credential.scopes != REQUIRED_LIVE_WRITE_SCOPES
    ):
        raise PermissionError("scope or account validation failed")
    return SealedCredentialPackage(
        sealer.seal(credential.key_id.encode()), sealer.seal(credential.private_key_pem)
    )
