"""Dependency-free authenticated secret encryption shared by neutral read boundaries."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import subprocess
from dataclasses import dataclass, field


class SecretStorageError(ValueError):
    """Sanitized authenticated-encryption failure."""


@dataclass(frozen=True, slots=True, repr=False)
class SecretBox:
    """Encrypt-then-MAC using scrypt-derived AES-256-CTR and HMAC-SHA256 keys."""

    master_key: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if len(self.master_key) < 32:
            raise SecretStorageError("vault master key must contain at least 256 bits")

    def __repr__(self) -> str:
        return "SecretBox(<redacted>)"

    def _keys(self, salt: bytes) -> tuple[bytes, bytes]:
        material = hashlib.scrypt(
            self.master_key,
            salt=salt,
            n=2**15,
            r=8,
            p=1,
            dklen=64,
            maxmem=64 * 1024 * 1024,
        )
        return material[:32], material[32:]

    def seal(self, plaintext: bytes) -> str:
        salt, iv = secrets.token_bytes(16), secrets.token_bytes(16)
        encryption_key, mac_key = self._keys(salt)
        result = subprocess.run(  # noqa: S603 - fixed executable and arguments
            [
                "/usr/bin/openssl",
                "enc",
                "-aes-256-ctr",
                "-K",
                encryption_key.hex(),
                "-iv",
                iv.hex(),
            ],
            input=plaintext,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise SecretStorageError("credential encryption failed")
        body = b"KPV3\x01" + salt + iv + result.stdout
        return base64.urlsafe_b64encode(body + hmac.digest(mac_key, body, "sha256")).decode()

    def open(self, token: str) -> bytes:
        try:
            raw = base64.urlsafe_b64decode(token)
            if raw[:5] != b"KPV3\x01" or len(raw) < 69:
                raise SecretStorageError("invalid credential envelope")
            salt, iv, ciphertext, signature = raw[5:21], raw[21:37], raw[37:-32], raw[-32:]
            encryption_key, mac_key = self._keys(salt)
            if not hmac.compare_digest(signature, hmac.digest(mac_key, raw[:-32], "sha256")):
                raise SecretStorageError("credential envelope authentication failed")
            result = subprocess.run(  # noqa: S603 - fixed executable and arguments
                [
                    "/usr/bin/openssl",
                    "enc",
                    "-d",
                    "-aes-256-ctr",
                    "-K",
                    encryption_key.hex(),
                    "-iv",
                    iv.hex(),
                ],
                input=ciphertext,
                capture_output=True,
                check=False,
            )
        except (ValueError, TypeError) as exc:
            raise SecretStorageError("invalid credential envelope") from exc
        if result.returncode:
            raise SecretStorageError("credential decryption failed")
        return result.stdout
