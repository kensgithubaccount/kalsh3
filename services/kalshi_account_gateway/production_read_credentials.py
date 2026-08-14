"""Production environment- and server-scope-proven read-only credentials."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import stat
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from services.neutral_security import SecretBox, SecretStorageError

from .auth import AuthenticationError, RequestSigner
from .read_credentials import ExactReadCredential, ReadEnvironment

PRODUCTION_ORIGIN = "https://external-api.kalshi.com"
API_KEYS_PATH = "/trade-api/v2/api_keys"
STORE_SCHEMA = "kalsh3.production-exact-read-credential"
STORE_VERSION = 1
VERIFICATION_METHOD = "production-api-keys-exact-read-get-v1"
MAX_PRIVATE_KEY_BYTES = 32_768
MAX_STORE_BYTES = 131_072
MAX_RESPONSE_BYTES = 1_000_000


class ProductionCredentialState(StrEnum):
    UNENROLLED = "UNENROLLED"
    ENROLLED_UNVERIFIED = "ENROLLED_UNVERIFIED"
    VERIFIED_PRODUCTION_READONLY = "VERIFIED_PRODUCTION_READONLY"
    DISABLED = "DISABLED"
    QUARANTINED = "QUARANTINED"


class ProductionCredentialError(ValueError):
    """Sanitized production read-credential boundary failure."""


@dataclass(frozen=True, slots=True, repr=False)
class ProductionStoredCredential:
    environment: ReadEnvironment
    key_id: str
    private_key_pem: bytes
    credential_fingerprint: str
    allowed_scope: str
    state: ProductionCredentialState
    verification_target: ReadEnvironment | None
    verification_method: str | None
    verified_key_id: str | None
    verified_fingerprint: str | None
    server_scopes: tuple[str, ...] | None
    verified_at: datetime | None
    created_at: datetime
    updated_at: datetime

    def __repr__(self) -> str:
        return "ProductionStoredCredential(<redacted>)"


@dataclass(frozen=True, slots=True)
class ProductionVerificationResult:
    environment_proven: bool
    server_scopes: frozenset[str]


@dataclass(frozen=True, slots=True)
class ProductionReadReply:
    status: int
    body: bytes
    content_type: str = "application/json"
    location: str | None = None


class ReadSigner(Protocol):
    def headers(self, timestamp_ms: int, method: str, request_target: str) -> dict[str, str]: ...


class ProductionReadTransport(Protocol):
    def get(
        self, origin: str, path: str, headers: Mapping[str, str], *, timeout_seconds: float
    ) -> ProductionReadReply: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        return None


class UrllibProductionReadTransport:
    """One-origin GET-only production transport with redirects disabled."""

    def get(
        self, origin: str, path: str, headers: Mapping[str, str], *, timeout_seconds: float
    ) -> ProductionReadReply:
        if origin != PRODUCTION_ORIGIN or path != API_KEYS_PATH:
            raise ProductionCredentialError("production verification target rejected")
        request = urllib.request.Request(  # noqa: S310 - exact fixed HTTPS origin and path
            origin + path, headers=dict(headers), method="GET"
        )
        try:
            with urllib.request.build_opener(_NoRedirect()).open(
                request, timeout=timeout_seconds
            ) as response:
                declared = response.headers.get("Content-Length")
                if declared is not None and int(declared) > MAX_RESPONSE_BYTES:
                    raise ProductionCredentialError("production verification response too large")
                body = response.read(MAX_RESPONSE_BYTES + 1)
                reply = ProductionReadReply(
                    response.status,
                    body,
                    response.headers.get_content_type(),
                    response.headers.get("Location"),
                )
        except urllib.error.HTTPError as exc:
            return ProductionReadReply(exc.code, b"", location=exc.headers.get("Location"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProductionCredentialError("production verification transport failed") from exc
        except ValueError as exc:
            raise ProductionCredentialError("production verification response rejected") from exc
        if len(reply.body) > MAX_RESPONSE_BYTES:
            raise ProductionCredentialError("production verification response too large")
        return reply


def default_production_store_directory() -> Path:
    state_home = os.environ.get("XDG_STATE_HOME")
    root = Path(state_home) if state_home else Path.home() / ".local" / "state"
    return root / "kalsh3" / "production-exact-read"


class ProductionReadCredentialStore:
    """Separate single-record authenticated store for production read credentials."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.master_key_path = directory / "master.key"
        self.record_path = directory / "credential.enc"

    def enroll(self, key_id: str, private_key_pem: bytes, *, now: datetime) -> None:
        if len(private_key_pem) > MAX_PRIVATE_KEY_BYTES:
            raise ProductionCredentialError("credential input exceeds size limit")
        try:
            RequestSigner(key_id, private_key_pem)
        except AuthenticationError as exc:
            raise ProductionCredentialError("credential format rejected") from exc
        self._prepare_directory(create=True)
        master_key = self._master_key(create=True)
        timestamp = _utc(now)
        record = ProductionStoredCredential(
            ReadEnvironment.PRODUCTION,
            key_id,
            bytes(private_key_pem),
            hmac.new(master_key, private_key_pem, hashlib.sha256).hexdigest(),
            "read",
            ProductionCredentialState.ENROLLED_UNVERIFIED,
            None,
            None,
            None,
            None,
            None,
            None,
            timestamp,
            timestamp,
        )
        self._write(record, master_key)

    def load(self) -> ProductionStoredCredential:
        self._prepare_directory(create=False)
        master_key = self._master_key(create=False)
        encrypted = self._read_regular(self.record_path, MAX_STORE_BYTES)
        try:
            plaintext = SecretBox(master_key).open(encrypted.decode("ascii"))
            record = _record_from_json(json.loads(plaintext))
            expected = hmac.new(master_key, record.private_key_pem, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(record.credential_fingerprint, expected):
                raise ProductionCredentialError("credential fingerprint rejected")
            return record
        except (UnicodeDecodeError, json.JSONDecodeError, SecretStorageError, ValueError) as exc:
            raise ProductionCredentialError("credential store integrity validation failed") from exc

    def verify(
        self,
        transport: ProductionReadTransport,
        *,
        timestamp_ms: int,
        now: datetime,
        signer_factory: Callable[[str, bytes], ReadSigner] = RequestSigner,
    ) -> ProductionVerificationResult:
        record = self.load()
        if record.environment is not ReadEnvironment.PRODUCTION:
            raise ProductionCredentialError("credential environment mismatch")
        if record.state is not ProductionCredentialState.ENROLLED_UNVERIFIED:
            raise ProductionCredentialError("credential is not awaiting verification")
        signer = signer_factory(record.key_id, record.private_key_pem)
        headers = signer.headers(timestamp_ms, "GET", API_KEYS_PATH)
        try:
            reply = transport.get(PRODUCTION_ORIGIN, API_KEYS_PATH, headers, timeout_seconds=10)
        except (TimeoutError, OSError) as exc:
            raise ProductionCredentialError("production verification transport failed") from exc
        scopes = _verified_target_scopes(reply, record.key_id)
        checked_at = _utc(now)
        if scopes != ("read",):
            if scopes is not None:
                self._finish_check(
                    record,
                    ProductionCredentialState.QUARANTINED,
                    scopes,
                    checked_at,
                    verified=False,
                )
            raise ProductionCredentialError("server-side scope is not exactly read-only")
        self._finish_check(
            record,
            ProductionCredentialState.VERIFIED_PRODUCTION_READONLY,
            scopes,
            checked_at,
            verified=True,
        )
        return ProductionVerificationResult(True, frozenset({"read"}))

    def set_state(self, state: ProductionCredentialState, *, now: datetime) -> None:
        if state not in {
            ProductionCredentialState.DISABLED,
            ProductionCredentialState.QUARANTINED,
        }:
            raise ProductionCredentialError("unsupported credential state transition")
        record = self.load()
        self._write(
            ProductionStoredCredential(
                record.environment,
                record.key_id,
                record.private_key_pem,
                record.credential_fingerprint,
                record.allowed_scope,
                state,
                record.verification_target,
                record.verification_method,
                record.verified_key_id,
                record.verified_fingerprint,
                record.server_scopes,
                record.verified_at,
                record.created_at,
                _utc(now),
            ),
            self._master_key(create=False),
        )

    def _finish_check(
        self,
        original: ProductionStoredCredential,
        state: ProductionCredentialState,
        scopes: tuple[str, ...],
        checked_at: datetime,
        *,
        verified: bool,
    ) -> None:
        current = self.load()
        if (
            current.key_id != original.key_id
            or current.credential_fingerprint != original.credential_fingerprint
            or current.updated_at != original.updated_at
            or current.state is not ProductionCredentialState.ENROLLED_UNVERIFIED
        ):
            raise ProductionCredentialError("credential changed during verification")
        updated = ProductionStoredCredential(
            original.environment,
            original.key_id,
            original.private_key_pem,
            original.credential_fingerprint,
            original.allowed_scope,
            state,
            ReadEnvironment.PRODUCTION,
            VERIFICATION_METHOD,
            original.key_id,
            original.credential_fingerprint,
            scopes,
            checked_at if verified else None,
            original.created_at,
            checked_at,
        )
        self._write(updated, self._master_key(create=False))

    def _prepare_directory(self, *, create: bool) -> None:
        try:
            if (
                self.directory.is_symlink()
                or self.directory.resolve(strict=False) != self.directory.absolute()
            ):
                raise ProductionCredentialError("credential store path rejected")
            if create and not self.directory.exists():
                self.directory.mkdir(mode=0o700, parents=True)
            info = self.directory.stat(follow_symlinks=False)
        except FileNotFoundError as exc:
            raise ProductionCredentialError("credential is not enrolled") from exc
        except OSError as exc:
            raise ProductionCredentialError("credential store path rejected") from exc
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o700
            or (hasattr(os, "getuid") and info.st_uid != os.getuid())
        ):
            raise ProductionCredentialError("credential store permissions rejected")

    def _master_key(self, *, create: bool) -> bytes:
        if create and not self.master_key_path.exists():
            self._create_master_key()
        key = self._read_regular(self.master_key_path, 32)
        if len(key) != 32:
            raise ProductionCredentialError("credential store integrity validation failed")
        return key

    def _create_master_key(self) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.master_key_path, flags, 0o600)
        except FileExistsError:
            return
        except OSError as exc:
            raise ProductionCredentialError("credential store key creation failed") from exc
        try:
            _write_descriptor(descriptor, secrets.token_bytes(32))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _write(self, record: ProductionStoredCredential, master_key: bytes) -> None:
        try:
            plaintext = json.dumps(
                _record_json(record), sort_keys=True, separators=(",", ":")
            ).encode()
            token = SecretBox(master_key).seal(plaintext).encode("ascii")
            if self.record_path.is_symlink():
                raise ProductionCredentialError("credential store path rejected")
            self._atomic_write(self.record_path, token)
        except (SecretStorageError, OSError) as exc:
            raise ProductionCredentialError("credential store write failed") from exc

    @staticmethod
    def _read_regular(path: Path, maximum: int) -> bytes:
        try:
            info = path.stat(follow_symlinks=False)
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600
                or (hasattr(os, "getuid") and info.st_uid != os.getuid())
            ):
                raise ProductionCredentialError("credential file permissions rejected")
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
            raise ProductionCredentialError("credential is not enrolled") from exc
        if len(value) > maximum:
            raise ProductionCredentialError("credential store exceeds size limit")
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
                raise ProductionCredentialError("credential file permissions rejected")
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            _write_descriptor(descriptor, value)
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
class VerifiedProductionReadCredentialProvider:
    store: ProductionReadCredentialStore

    def resolve(self, environment: ReadEnvironment) -> ExactReadCredential:
        if environment is not ReadEnvironment.PRODUCTION:
            raise ProductionCredentialError("production credential environment mismatch")
        record = self.store.load()
        if (
            record.environment is not ReadEnvironment.PRODUCTION
            or record.state is not ProductionCredentialState.VERIFIED_PRODUCTION_READONLY
            or record.verification_target is not ReadEnvironment.PRODUCTION
            or record.verification_method != VERIFICATION_METHOD
            or record.verified_key_id != record.key_id
            or record.verified_fingerprint != record.credential_fingerprint
            or record.server_scopes != ("read",)
            or record.verified_at is None
            or record.allowed_scope != "read"
        ):
            raise ProductionCredentialError("verified production read credential unavailable")
        return ExactReadCredential(
            ReadEnvironment.PRODUCTION,
            record.key_id,
            record.private_key_pem,
            frozenset({"read"}),
        )


def read_private_key_fd(fd: int, *, maximum: int = MAX_PRIVATE_KEY_BYTES) -> bytes:
    if type(fd) is not int or fd < 0:
        raise ProductionCredentialError("invalid credential input descriptor")
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
                raise ProductionCredentialError("credential input exceeds size limit")
    except OSError:
        raise ProductionCredentialError("credential input descriptor unavailable") from None
    return b"".join(chunks)


def _verified_target_scopes(
    reply: ProductionReadReply, expected_key_id: str
) -> tuple[str, ...] | None:
    if reply.location is not None or 300 <= reply.status < 400:
        raise ProductionCredentialError("production verification redirect rejected")
    if reply.status in {401, 403}:
        raise ProductionCredentialError("production credential authentication rejected")
    if reply.status != 200 or reply.content_type != "application/json":
        raise ProductionCredentialError("production verification response rejected")
    if len(reply.body) > MAX_RESPONSE_BYTES:
        raise ProductionCredentialError("production verification response too large")
    try:
        payload = json.loads(reply.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProductionCredentialError("production verification response malformed") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("api_keys"), list):
        raise ProductionCredentialError("production verification response malformed")
    records = payload["api_keys"]
    if any(not isinstance(item, dict) for item in records):
        raise ProductionCredentialError("production verification response malformed")
    matches = [item for item in records if item.get("api_key_id") == expected_key_id]
    if len(matches) != 1:
        raise ProductionCredentialError("exact production credential not uniquely identified")
    scopes = matches[0].get("scopes")
    if not isinstance(scopes, list) or not scopes or any(type(item) is not str for item in scopes):
        raise ProductionCredentialError("production credential scopes malformed")
    return tuple(scopes)


def _write_descriptor(descriptor: int, value: bytes) -> None:
    remaining = memoryview(value)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise ProductionCredentialError("credential store write failed")
        remaining = remaining[written:]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProductionCredentialError("credential timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _record_json(record: ProductionStoredCredential) -> dict[str, Any]:
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
        "verified_key_id": record.verified_key_id,
        "verified_fingerprint": record.verified_fingerprint,
        "server_scopes": list(record.server_scopes) if record.server_scopes is not None else None,
        "verified_at": record.verified_at.isoformat() if record.verified_at else None,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def _record_from_json(raw: Any) -> ProductionStoredCredential:
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
        "verified_key_id",
        "verified_fingerprint",
        "server_scopes",
        "verified_at",
        "created_at",
        "updated_at",
    }
    if not isinstance(raw, dict) or set(raw) != required:
        raise ProductionCredentialError("credential record shape rejected")
    if raw["schema"] != STORE_SCHEMA or raw["version"] != STORE_VERSION:
        raise ProductionCredentialError("credential store schema rejected")
    if raw["environment"] != ReadEnvironment.PRODUCTION.value or raw["allowed_scope"] != "read":
        raise ProductionCredentialError("credential environment or scope rejected")
    try:
        state = ProductionCredentialState(raw["state"])
        target = (
            ReadEnvironment(raw["verification_target"])
            if raw["verification_target"] is not None
            else None
        )
        verified = datetime.fromisoformat(raw["verified_at"]) if raw["verified_at"] else None
        created = datetime.fromisoformat(raw["created_at"])
        updated = datetime.fromisoformat(raw["updated_at"])
        pem = raw["private_key_pem"].encode("ascii")
        RequestSigner(raw["key_id"], pem)
    except (ValueError, TypeError, AttributeError, AuthenticationError) as exc:
        raise ProductionCredentialError("credential record validation failed") from exc
    fingerprint = raw["credential_fingerprint"]
    if not _fingerprint(fingerprint):
        raise ProductionCredentialError("credential fingerprint rejected")
    scopes_raw = raw["server_scopes"]
    if scopes_raw is not None and (
        not isinstance(scopes_raw, list)
        or not scopes_raw
        or any(type(item) is not str for item in scopes_raw)
    ):
        raise ProductionCredentialError("credential server scopes rejected")
    scopes = tuple(scopes_raw) if scopes_raw is not None else None
    no_proof = all(
        item is None
        for item in (
            target,
            raw["verification_method"],
            raw["verified_key_id"],
            raw["verified_fingerprint"],
            scopes,
            verified,
        )
    )
    exact_proof = (
        target is ReadEnvironment.PRODUCTION
        and raw["verification_method"] == VERIFICATION_METHOD
        and raw["verified_key_id"] == raw["key_id"]
        and raw["verified_fingerprint"] == fingerprint
        and scopes == ("read",)
        and verified is not None
    )
    unsafe_proof = (
        target is ReadEnvironment.PRODUCTION
        and raw["verification_method"] == VERIFICATION_METHOD
        and raw["verified_key_id"] == raw["key_id"]
        and raw["verified_fingerprint"] == fingerprint
        and scopes is not None
        and scopes != ("read",)
        and verified is None
    )
    if (
        state is ProductionCredentialState.UNENROLLED
        or (state is ProductionCredentialState.ENROLLED_UNVERIFIED and not no_proof)
        or (state is ProductionCredentialState.VERIFIED_PRODUCTION_READONLY and not exact_proof)
        or (state is ProductionCredentialState.QUARANTINED and not (no_proof or unsafe_proof))
        or (
            state is ProductionCredentialState.DISABLED
            and not (no_proof or exact_proof or unsafe_proof)
        )
    ):
        raise ProductionCredentialError("credential verification metadata rejected")
    if created > updated or (verified is not None and not created <= verified <= updated):
        raise ProductionCredentialError("credential timestamp ordering rejected")
    return ProductionStoredCredential(
        ReadEnvironment.PRODUCTION,
        raw["key_id"],
        pem,
        fingerprint,
        "read",
        state,
        target,
        raw["verification_method"],
        raw["verified_key_id"],
        raw["verified_fingerprint"],
        scopes,
        _utc(verified) if verified else None,
        _utc(created),
        _utc(updated),
    )


def _fingerprint(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
