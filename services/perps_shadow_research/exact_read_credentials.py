"""Demo-only environment-proven exact-read credential lifecycle for M25B3."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import stat
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, cast

from services.kalshi_account_gateway.auth import AuthenticationError, RequestSigner
from services.neutral_security import SecretBox, SecretStorageError

from .domain import ShadowResearchError
from .live_boundary import (
    ExactReadCredential,
    MarginEnabledClient,
    MarginEnvironment,
    MarginHttpTransport,
)

STORE_SCHEMA = "kalsh3.perps-exact-read-credential"
STORE_VERSION = 1
VERIFICATION_METHOD = "margin-enabled-authenticated-get-v1"
MAX_PRIVATE_KEY_BYTES = 32_768
MAX_STORE_BYTES = 131_072


class CredentialState(StrEnum):
    UNENROLLED = "UNENROLLED"
    ENROLLED_UNVERIFIED = "ENROLLED_UNVERIFIED"
    VERIFIED_DEMO = "VERIFIED_DEMO"
    DISABLED = "DISABLED"
    QUARANTINED = "QUARANTINED"


class CredentialBoundaryError(ShadowResearchError):
    """Sanitized credential-boundary failure."""


@dataclass(frozen=True, slots=True, repr=False)
class StoredCredential:
    environment: MarginEnvironment
    key_id: str
    private_key_pem: bytes
    credential_fingerprint: str
    allowed_scope: str
    state: CredentialState
    verification_target: MarginEnvironment | None
    verification_method: str | None
    verified_at: datetime | None
    created_at: datetime
    updated_at: datetime

    def __repr__(self) -> str:
        return "StoredCredential(<redacted>)"


@dataclass(frozen=True, slots=True)
class DemoVerificationResult:
    environment_proven: bool
    perps_enabled: bool


class ReadSigner(Protocol):
    def headers(self, timestamp_ms: int, method: str, request_target: str) -> dict[str, str]: ...


def default_store_directory() -> Path:
    """Return a deterministic user-state path outside the repository by default."""
    state_home = os.environ.get("XDG_STATE_HOME")
    root = Path(state_home) if state_home else Path.home() / ".local" / "state"
    return root / "kalsh3" / "perps-exact-read-demo"


class ExactReadCredentialStore:
    """Single-record authenticated encrypted store with strict filesystem ownership modes."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.master_key_path = directory / "master.key"
        self.record_path = directory / "credential.enc"

    def enroll_demo(
        self,
        key_id: str,
        private_key_pem: bytes,
        *,
        now: datetime,
    ) -> None:
        if len(private_key_pem) > MAX_PRIVATE_KEY_BYTES:
            raise CredentialBoundaryError("credential input exceeds size limit")
        try:
            RequestSigner(key_id, private_key_pem)
        except AuthenticationError as exc:
            raise CredentialBoundaryError("credential format rejected") from exc
        self._prepare_directory(create=True)
        master_key = self._master_key(create=True)
        timestamp = _utc(now)
        record = StoredCredential(
            MarginEnvironment.DEMO,
            key_id,
            bytes(private_key_pem),
            hmac.new(master_key, private_key_pem, hashlib.sha256).hexdigest(),
            "read",
            CredentialState.ENROLLED_UNVERIFIED,
            None,
            None,
            None,
            timestamp,
            timestamp,
        )
        self._write(record, master_key)

    def load(self) -> StoredCredential:
        self._prepare_directory(create=False)
        master_key = self._master_key(create=False)
        encrypted = self._read_regular(self.record_path, MAX_STORE_BYTES)
        try:
            plaintext = SecretBox(master_key).open(encrypted.decode("ascii"))
            raw = json.loads(plaintext)
            record = _record_from_json(raw)
            expected = hmac.new(master_key, record.private_key_pem, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(record.credential_fingerprint, expected):
                raise CredentialBoundaryError("credential fingerprint rejected")
            return record
        except (UnicodeDecodeError, json.JSONDecodeError, SecretStorageError, ValueError) as exc:
            raise CredentialBoundaryError("credential store integrity validation failed") from exc

    def verify_demo(
        self,
        transport: MarginHttpTransport,
        *,
        timestamp_ms: int,
        now: datetime,
        signer_factory: Callable[[str, bytes], ReadSigner] = RequestSigner,
    ) -> DemoVerificationResult:
        record = self.load()
        if record.environment is not MarginEnvironment.DEMO:
            raise CredentialBoundaryError("credential environment mismatch")
        if record.state is not CredentialState.ENROLLED_UNVERIFIED:
            raise CredentialBoundaryError("credential is not awaiting verification")
        signer = signer_factory(record.key_id, record.private_key_pem)
        enabled = MarginEnabledClient(
            MarginEnvironment.DEMO,
            cast(RequestSigner, signer),
            transport,
            max_retries=0,
            sleep=lambda _: None,
        ).enabled(timestamp_ms=timestamp_ms)
        verified_at = _utc(now)
        updated = StoredCredential(
            record.environment,
            record.key_id,
            record.private_key_pem,
            record.credential_fingerprint,
            record.allowed_scope,
            CredentialState.VERIFIED_DEMO,
            MarginEnvironment.DEMO,
            VERIFICATION_METHOD,
            verified_at,
            record.created_at,
            verified_at,
        )
        current = self.load()
        if (
            current.credential_fingerprint != record.credential_fingerprint
            or current.updated_at != record.updated_at
            or current.state is not CredentialState.ENROLLED_UNVERIFIED
        ):
            raise CredentialBoundaryError("credential changed during verification")
        self._write(updated, self._master_key(create=False))
        return DemoVerificationResult(True, enabled)

    def set_state(self, state: CredentialState, *, now: datetime) -> None:
        if state not in {CredentialState.DISABLED, CredentialState.QUARANTINED}:
            raise CredentialBoundaryError("unsupported credential state transition")
        record = self.load()
        updated = StoredCredential(
            record.environment,
            record.key_id,
            record.private_key_pem,
            record.credential_fingerprint,
            record.allowed_scope,
            state,
            record.verification_target,
            record.verification_method,
            record.verified_at,
            record.created_at,
            _utc(now),
        )
        self._write(updated, self._master_key(create=False))

    def _prepare_directory(self, *, create: bool) -> None:
        try:
            if (
                self.directory.is_symlink()
                or self.directory.resolve(strict=False) != self.directory.absolute()
            ):
                raise CredentialBoundaryError("credential store path rejected")
            if create and not self.directory.exists():
                self.directory.mkdir(mode=0o700, parents=True)
            info = self.directory.stat(follow_symlinks=False)
        except FileNotFoundError as exc:
            raise CredentialBoundaryError("credential is not enrolled") from exc
        except OSError as exc:
            raise CredentialBoundaryError("credential store path rejected") from exc
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o700
            or (hasattr(os, "getuid") and info.st_uid != os.getuid())
        ):
            raise CredentialBoundaryError("credential store permissions rejected")

    def _master_key(self, *, create: bool) -> bytes:
        if create and not self.master_key_path.exists():
            self._create_master_key()
        key = self._read_regular(self.master_key_path, 32)
        if len(key) != 32:
            raise CredentialBoundaryError("credential store integrity validation failed")
        return key

    def _create_master_key(self) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.master_key_path, flags, 0o600)
        except FileExistsError:
            return
        except OSError as exc:
            raise CredentialBoundaryError("credential store key creation failed") from exc
        try:
            value = secrets.token_bytes(32)
            remaining = memoryview(value)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise CredentialBoundaryError("credential store write failed")
                remaining = remaining[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _write(self, record: StoredCredential, master_key: bytes) -> None:
        try:
            plaintext = json.dumps(
                _record_json(record), sort_keys=True, separators=(",", ":")
            ).encode()
            token = SecretBox(master_key).seal(plaintext).encode("ascii")
            if self.record_path.is_symlink():
                raise CredentialBoundaryError("credential store path rejected")
            self._atomic_write(self.record_path, token)
        except (SecretStorageError, OSError) as exc:
            raise CredentialBoundaryError("credential store write failed") from exc

    @staticmethod
    def _read_regular(path: Path, maximum: int) -> bytes:
        try:
            info = path.stat(follow_symlinks=False)
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600
                or (hasattr(os, "getuid") and info.st_uid != os.getuid())
            ):
                raise CredentialBoundaryError("credential file permissions rejected")
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = os.read(descriptor, min(4096, maximum + 1 - total))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > maximum:
                        break
                value = b"".join(chunks)
            finally:
                os.close(descriptor)
        except (FileNotFoundError, OSError) as exc:
            raise CredentialBoundaryError("credential is not enrolled") from exc
        if len(value) > maximum:
            raise CredentialBoundaryError("credential store exceeds size limit")
        return value

    @staticmethod
    def _atomic_write(path: Path, value: bytes) -> None:
        if path.exists() or path.is_symlink():
            info = path.stat(follow_symlinks=False)
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600
                or (hasattr(os, "getuid") and info.st_uid != os.getuid())
            ):
                raise CredentialBoundaryError("credential file permissions rejected")
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            remaining = memoryview(value)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise CredentialBoundaryError("credential store write failed")
                remaining = remaining[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.replace(temporary, path)
            os.chmod(path, 0o600, follow_symlinks=False)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True, repr=False)
class VerifiedDemoCredentialProvider:
    store: ExactReadCredentialStore

    def resolve(self, environment: MarginEnvironment) -> ExactReadCredential:
        if environment is not MarginEnvironment.DEMO:
            raise CredentialBoundaryError(
                "BLOCKER: M25B3 production credential composition unavailable"
            )
        record = self.store.load()
        if (
            record.environment is not MarginEnvironment.DEMO
            or record.state is not CredentialState.VERIFIED_DEMO
            or record.verification_target is not MarginEnvironment.DEMO
            or record.verification_method != VERIFICATION_METHOD
            or record.verified_at is None
            or record.allowed_scope != "read"
        ):
            raise CredentialBoundaryError("verified DEMO credential unavailable")
        return ExactReadCredential(
            MarginEnvironment.DEMO,
            record.key_id,
            record.private_key_pem,
            frozenset({"read"}),
        )


def read_private_key_fd(fd: int, *, maximum: int = MAX_PRIVATE_KEY_BYTES) -> bytes:
    if type(fd) is not int or fd < 0:
        raise CredentialBoundaryError("invalid credential input descriptor")
    chunks: list[bytes] = []
    total = 0
    try:
        while True:
            chunk = os.read(fd, min(4096, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise CredentialBoundaryError("credential input exceeds size limit")
    except OSError:
        raise CredentialBoundaryError("credential input descriptor unavailable") from None
    return b"".join(chunks)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CredentialBoundaryError("credential timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _record_json(record: StoredCredential) -> dict[str, Any]:
    return {
        "schema": STORE_SCHEMA,
        "version": STORE_VERSION,
        "environment": record.environment.value,
        "key_id": record.key_id,
        "private_key_pem": record.private_key_pem.decode("ascii"),
        "credential_fingerprint": record.credential_fingerprint,
        "allowed_scope": record.allowed_scope,
        "state": record.state.value,
        "verification_target": (
            record.verification_target.value if record.verification_target else None
        ),
        "verification_method": record.verification_method,
        "verified_at": record.verified_at.isoformat() if record.verified_at else None,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def _record_from_json(raw: Any) -> StoredCredential:
    required = {
        "schema",
        "version",
        "environment",
        "key_id",
        "private_key_pem",
        "credential_fingerprint",
        "allowed_scope",
        "state",
        "verification_target",
        "verification_method",
        "verified_at",
        "created_at",
        "updated_at",
    }
    if not isinstance(raw, dict) or set(raw) != required:
        raise CredentialBoundaryError("credential record shape rejected")
    if raw["schema"] != STORE_SCHEMA or raw["version"] != STORE_VERSION:
        raise CredentialBoundaryError("credential store schema rejected")
    if raw["environment"] != MarginEnvironment.DEMO.value:
        raise CredentialBoundaryError("credential environment mismatch")
    if raw["allowed_scope"] != "read":
        raise CredentialBoundaryError("credential scope rejected")
    try:
        state = CredentialState(raw["state"])
        target = (
            MarginEnvironment(raw["verification_target"])
            if raw["verification_target"] is not None
            else None
        )
        verified = datetime.fromisoformat(raw["verified_at"]) if raw["verified_at"] else None
        created = datetime.fromisoformat(raw["created_at"])
        updated = datetime.fromisoformat(raw["updated_at"])
        pem = raw["private_key_pem"].encode("ascii")
        RequestSigner(raw["key_id"], pem)
    except (ValueError, TypeError, AttributeError, AuthenticationError) as exc:
        raise CredentialBoundaryError("credential record validation failed") from exc
    fingerprint = raw["credential_fingerprint"]
    if (
        not isinstance(fingerprint, str)
        or len(fingerprint) != 64
        or any(character not in "0123456789abcdef" for character in fingerprint)
    ):
        raise CredentialBoundaryError("credential fingerprint rejected")
    unverified_metadata = target is None and raw["verification_method"] is None and verified is None
    verified_metadata = (
        target is MarginEnvironment.DEMO
        and raw["verification_method"] == VERIFICATION_METHOD
        and verified is not None
    )
    if (
        state is CredentialState.UNENROLLED
        or (state is CredentialState.ENROLLED_UNVERIFIED and not unverified_metadata)
        or (state is CredentialState.VERIFIED_DEMO and not verified_metadata)
        or (
            state in {CredentialState.DISABLED, CredentialState.QUARANTINED}
            and not (unverified_metadata or verified_metadata)
        )
    ):
        raise CredentialBoundaryError("credential verification metadata rejected")
    if created > updated or (verified is not None and not created <= verified <= updated):
        raise CredentialBoundaryError("credential timestamp ordering rejected")
    return StoredCredential(
        MarginEnvironment.DEMO,
        raw["key_id"],
        pem,
        fingerprint,
        "read",
        state,
        target,
        raw["verification_method"],
        _utc(verified) if verified else None,
        _utc(created),
        _utc(updated),
    )
