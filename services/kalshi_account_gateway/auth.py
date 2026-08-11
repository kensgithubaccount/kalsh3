"""Kalshi RSA-PSS request authentication.

The private key is consumed in memory and passed to OpenSSL over an inherited pipe. It is
never written to disk, logged, or returned from this module.
"""

from __future__ import annotations

import base64
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import urlsplit


class AuthenticationError(ValueError):
    """Raised when a request cannot safely be authenticated."""


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
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, self.private_key_pem)
            os.close(write_fd)
            write_fd = -1
            command = [
                "/usr/bin/openssl",
                "dgst",
                "-sha256",
                "-sigopt",
                "rsa_padding_mode:pss",
                "-sigopt",
                "rsa_pss_saltlen:digest",
                "-sign",
                f"/proc/self/fd/{read_fd}",
            ]
            result = subprocess.run(  # noqa: S603 - fixed command, no shell
                command,
                input=message,
                capture_output=True,
                check=False,
                pass_fds=(read_fd,),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise AuthenticationError("RSA signing failed") from exc
        finally:
            os.close(read_fd)
            if write_fd >= 0:
                os.close(write_fd)
        if result.returncode != 0:
            raise AuthenticationError("RSA signing failed")
        return {
            "KALSHI-ACCESS-KEY": self.access_key,
            "KALSHI-ACCESS-TIMESTAMP": str(timestamp_ms),
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(result.stdout).decode("ascii"),
        }
