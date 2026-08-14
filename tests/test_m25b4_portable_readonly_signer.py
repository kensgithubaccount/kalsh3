from __future__ import annotations

import inspect
import os
import subprocess
from typing import Any

import pytest

import services.kalshi_account_gateway.auth as auth
from services.kalshi_account_gateway.auth import AuthenticationError, RequestSigner

PKCS8_PREFIX = b"-----BEGIN " + b"PRIVATE KEY-----"


@pytest.fixture(scope="module")
def private_key() -> bytes:
    result = subprocess.run(
        [
            "/usr/bin/openssl",
            "genpkey",
            "-algorithm",
            "RSA",
            "-pkeyopt",
            "rsa_keygen_bits:2048",
        ],
        check=True,
        capture_output=True,
    )
    return result.stdout


@pytest.mark.parametrize("method", ["GET", "HEAD", "get", " HeAd "])
def test_portable_pipe_signs_every_currently_supported_method(
    private_key: bytes, method: str
) -> None:
    headers = RequestSigner("read-key", private_key).headers(
        1_700_000_000_123, method, "/trade-api/v2/margin/enabled?ignored=yes"
    )
    assert headers["KALSHI-ACCESS-KEY"] == "read-key"
    assert headers["KALSHI-ACCESS-TIMESTAMP"] == "1700000000123"
    assert headers["KALSHI-ACCESS-SIGNATURE"]


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_write_methods_fail_before_openssl(
    private_key: bytes, method: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_popen(*args: object, **kwargs: object) -> None:
        pytest.fail(f"OpenSSL was invoked for {method}: {args!r}, {kwargs!r}")

    monkeypatch.setattr(auth.subprocess, "Popen", forbidden_popen)
    with pytest.raises(AuthenticationError, match="only GET and HEAD"):
        RequestSigner("read-key", private_key).headers(123, method, "/trade-api/v2/orders")


@pytest.mark.parametrize(
    "value",
    [b"", b"not a PEM", b"-----BEGIN RSA " + b"PRIVATE KEY-----\ninvalid\n"],
)
def test_obviously_malformed_or_empty_pem_fails_sanitized(value: bytes) -> None:
    with pytest.raises(AuthenticationError) as raised:
        RequestSigner("read-key", value)
    if value:
        assert value.decode("ascii", errors="ignore") not in str(raised.value)


def test_invalid_pkcs8_key_fails_sanitized() -> None:
    secret = PKCS8_PREFIX + b"\nnot-a-private-key\n-----END " + b"PRIVATE KEY-----\n"
    signer = RequestSigner("read-key", secret)
    with pytest.raises(AuthenticationError, match="RSA signing failed") as raised:
        signer.headers(123, "GET", "/trade-api/v2/margin/enabled")
    assert secret.decode() not in str(raised.value)
    assert secret.decode() not in repr(raised.value)
    assert "not-a-private-key" not in repr(signer)


def test_key_is_only_transferred_over_the_inherited_pipe(
    private_key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    real_popen = subprocess.Popen

    def recording_popen(command: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        captured["command"] = list(command)
        captured["kwargs"] = kwargs
        return real_popen(command, **kwargs)

    monkeypatch.setattr(auth.subprocess, "Popen", recording_popen)
    signer = RequestSigner("read-key", private_key)
    signer.headers(123, "gEt", "/trade-api/v2/margin/enabled?query=omitted")

    command = captured["command"]
    kwargs = captured["kwargs"]
    assert command[-1].startswith("/dev/fd/")
    assert private_key not in [item.encode() for item in command]
    assert "env" not in kwargs
    assert kwargs["close_fds"] is True
    assert len(kwargs["pass_fds"]) == 1


def test_signer_has_no_linux_only_or_plaintext_file_key_transfer() -> None:
    source = inspect.getsource(auth)
    assert "/proc/self/fd" not in source
    assert "memfd_create" not in source
    assert "NamedTemporaryFile" not in source
    assert "mkstemp" not in source
    assert os.path.isdir("/dev/fd")


def test_signer_remains_environment_neutral_and_redacted(private_key: bytes) -> None:
    source = inspect.getsource(auth).lower()
    assert "demo" not in source
    assert "production" not in source
    assert private_key.decode() not in repr(RequestSigner("read-key", private_key))
