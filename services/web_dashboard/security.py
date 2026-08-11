"""Dependency-free dashboard security primitives with explicit secret boundaries."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import subprocess
import time
from dataclasses import dataclass, field


class SecurityError(ValueError):
    pass


def hash_password(password: str) -> str:
    if len(password) < 14 or password.lower() == password or not any(c.isdigit() for c in password):
        raise SecurityError("password must be 14+ characters with mixed case and a number")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=2**15, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024
    )
    return f"scrypt$32768$8$1${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$")
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=32,
            maxmem=64 * 1024 * 1024,
        )
        return hmac.compare_digest(actual, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp(secret: str, when: int | None = None) -> str:
    when = int(time.time()) if when is None else when
    padded = secret + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", when // 30), hashlib.sha1).digest()
    offset = digest[-1] & 15
    value = (int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF) % 1_000_000
    return f"{value:06d}"


def verify_totp(secret: str, code: str, when: int | None = None) -> bool:
    now = int(time.time()) if when is None else when
    return any(hmac.compare_digest(totp(secret, now + offset), code) for offset in (-30, 0, 30))


@dataclass(frozen=True, slots=True)
class SecretBox:
    """Encrypt-then-MAC vault using scrypt-derived AES-256-CTR and HMAC-SHA256 keys."""

    master_key: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if len(self.master_key) < 32:
            raise SecurityError("vault master key must contain at least 256 bits")

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
        result = subprocess.run(  # noqa: S603
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
            raise SecurityError("credential encryption failed")
        body = b"KPV3\x01" + salt + iv + result.stdout
        return base64.urlsafe_b64encode(body + hmac.digest(mac_key, body, "sha256")).decode()

    def open(self, token: str) -> bytes:
        try:
            raw = base64.urlsafe_b64decode(token)
            if raw[:5] != b"KPV3\x01" or len(raw) < 69:
                raise SecurityError("invalid credential envelope")
            salt, iv, ciphertext, signature = raw[5:21], raw[21:37], raw[37:-32], raw[-32:]
            encryption_key, mac_key = self._keys(salt)
            if not hmac.compare_digest(signature, hmac.digest(mac_key, raw[:-32], "sha256")):
                raise SecurityError("credential envelope authentication failed")
            result = subprocess.run(  # noqa: S603
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
            raise SecurityError("invalid credential envelope") from exc
        if result.returncode:
            raise SecurityError("credential decryption failed")
        return result.stdout


def recovery_codes() -> tuple[tuple[str, ...], tuple[str, ...]]:
    clear = tuple(f"{secrets.token_hex(4)}-{secrets.token_hex(4)}" for _ in range(8))
    hashes = tuple(hashlib.sha256(code.encode()).hexdigest() for code in clear)
    return clear, hashes


def consume_recovery_code(code: str, hashes: tuple[str, ...]) -> tuple[bool, tuple[str, ...]]:
    candidate = hashlib.sha256(code.encode()).hexdigest()
    matched = any(hmac.compare_digest(candidate, item) for item in hashes)
    return matched, tuple(item for item in hashes if not hmac.compare_digest(candidate, item))
