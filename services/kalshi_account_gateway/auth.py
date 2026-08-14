"""Kalshi RSA-PSS request authentication.

The private key is consumed in memory and passed to OpenSSL over an inherited pipe. It is
never written to disk, logged, or returned from this module.
"""

from __future__ import annotations

import base64
import os
import subprocess
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import urlsplit


class AuthenticationError(ValueError):
    """Raised when a request cannot safely be authenticated."""


_OPENSSL = "/usr/bin/openssl"
_PORTABLE_FD_ROOT = "/dev/fd"


def _write_all(descriptor: int, value: bytes) -> None:
    view = memoryview(value)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("private-key pipe write failed")
        view = view[written:]


def _rsa_pss_sha256(private_key_pem: bytes, message: bytes) -> bytes:
    """Sign with OpenSSL while transferring the key through an inherited pipe."""
    read_fd, write_fd = os.pipe()
    process: subprocess.Popen[bytes] | None = None
    try:
        command = [
            _OPENSSL,
            "dgst",
            "-sha256",
            "-sigopt",
            "rsa_padding_mode:pss",
            "-sigopt",
            "rsa_pss_saltlen:digest",
            "-sign",
            f"{_PORTABLE_FD_ROOT}/{read_fd}",
        ]
        process = subprocess.Popen(  # noqa: S603 - fixed command, no shell
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            pass_fds=(read_fd,),
        )
        os.close(read_fd)
        read_fd = -1
        _write_all(write_fd, private_key_pem)
        os.close(write_fd)
        write_fd = -1
        stdout, _stderr = process.communicate(input=message)
        if process.returncode != 0:
            raise AuthenticationError("RSA signing failed")
        return stdout
    except (OSError, subprocess.SubprocessError) as exc:
        if process is not None:
            with suppress(OSError, subprocess.SubprocessError):
                process.kill()
                process.communicate()
        raise AuthenticationError("RSA signing failed") from exc
    finally:
        if read_fd >= 0:
            os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)


def signature_message(timestamp_ms: int, method: str, request_target: str) -> bytes:
    """Build the exact Kalshi signature payload, excluding host and query string."""
    if timestamp_ms <= 0:
        raise AuthenticationError("timestamp must be a positive Unix millisecond value")
    normalized_method = method.strip().upper()
    if normalized_method not in {"GET", "HEAD"}:
        raise AuthenticationError("read-only gateway permits only GET and HEAD")
    parsed = urlsplit(request_target)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
        raise AuthenticationError("request target must be an absolute path, not a URL")
    path = str(PurePosixPath(parsed.path))
    if path != parsed.path or ".." in parsed.path.split("/"):
        raise AuthenticationError("request path is not canonical")
    return f"{timestamp_ms}{normalized_method}{path}".encode("ascii")


@dataclass(frozen=True, slots=True)
class RequestSigner:
    """Create Kalshi headers with RSA-PSS/SHA-256 using the system OpenSSL."""

    access_key: str
    private_key_pem: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not self.access_key.strip() or "\n" in self.access_key:
            raise AuthenticationError("invalid access key identifier")
        if not self.private_key_pem.startswith(b"-----BEGIN PRIVATE KEY-----"):
            raise AuthenticationError("an unencrypted PKCS#8 PEM private key is required")

    def headers(self, timestamp_ms: int, method: str, request_target: str) -> dict[str, str]:
        message = signature_message(timestamp_ms, method, request_target)
        signature = _rsa_pss_sha256(self.private_key_pem, message)
        return {
            "KALSHI-ACCESS-KEY": self.access_key,
            "KALSHI-ACCESS-TIMESTAMP": str(timestamp_ms),
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("ascii"),
        }
