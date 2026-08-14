"""Dependency-free dashboard security primitives with explicit secret boundaries."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time

from services.neutral_security import SecretBox, SecretStorageError

SecurityError = SecretStorageError
__all__ = ["SecretBox", "SecurityError"]


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
        if algorithm != "scrypt" or (n, r, p) != ("32768", "8", "1"):
            return False
        decoded_salt = base64.b64decode(salt, validate=True)
        decoded_expected = base64.b64decode(expected, validate=True)
        if len(decoded_salt) != 16 or len(decoded_expected) != 32:
            return False
        actual = hashlib.scrypt(
            password.encode(),
            salt=decoded_salt,
            n=32768,
            r=8,
            p=1,
            dklen=32,
            maxmem=64 * 1024 * 1024,
        )
        return hmac.compare_digest(actual, decoded_expected)
    except (ValueError, TypeError, MemoryError):
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
    if len(code) != 6 or not code.isascii() or not code.isdigit():
        return False
    now = int(time.time()) if when is None else when
    try:
        return any(hmac.compare_digest(totp(secret, now + offset), code) for offset in (-30, 0, 30))
    except (ValueError, TypeError):
        return False


def recovery_codes() -> tuple[tuple[str, ...], tuple[str, ...]]:
    clear = tuple(f"{secrets.token_hex(4)}-{secrets.token_hex(4)}" for _ in range(8))
    hashes = tuple(hashlib.sha256(code.encode()).hexdigest() for code in clear)
    return clear, hashes


def consume_recovery_code(code: str, hashes: tuple[str, ...]) -> tuple[bool, tuple[str, ...]]:
    candidate = hashlib.sha256(code.encode()).hexdigest()
    matched = any(hmac.compare_digest(candidate, item) for item in hashes)
    return matched, tuple(item for item in hashes if not hmac.compare_digest(candidate, item))
