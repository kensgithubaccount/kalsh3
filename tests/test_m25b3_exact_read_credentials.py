from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pytest

from services.neutral_security import SecretBox
from services.perps_shadow_research import credential_cli, live_smoke
from services.perps_shadow_research.domain import ShadowResearchError
from services.perps_shadow_research.exact_read_credentials import (
    STORE_SCHEMA,
    STORE_VERSION,
    VERIFICATION_METHOD,
    CredentialBoundaryError,
    CredentialState,
    ExactReadCredentialStore,
    VerifiedDemoCredentialProvider,
    read_private_key_fd,
)
from services.perps_shadow_research.live_boundary import (
    ENVIRONMENTS,
    MARGIN_ENABLED_PATH,
    HttpReply,
    MarginEnvironment,
)

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)
FAKE_PEM = (
    b"-----BEGIN " + b"PRIVATE KEY-----\nsynthetic-test-only\n-----END " + b"PRIVATE KEY-----\n"
)


class FakeSigner:
    calls: ClassVar[list[tuple[int, str, str]]] = []

    def __init__(self, key_id: str, private_key_pem: bytes) -> None:
        assert key_id == "demo-id" and private_key_pem == FAKE_PEM

    def headers(self, timestamp_ms: int, method: str, path: str) -> dict[str, str]:
        self.calls.append((timestamp_ms, method, path))
        return {"synthetic-auth": "redacted"}


class FakeHttp:
    def __init__(self, reply: HttpReply | Exception) -> None:
        self.reply = reply
        self.calls: list[tuple[str, str, dict[str, str], float]] = []

    def get(self, origin: str, path: str, headers: Any, *, timeout_seconds: float) -> HttpReply:
        self.calls.append((origin, path, dict(headers), timeout_seconds))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def enrolled(tmp_path: Path) -> ExactReadCredentialStore:
    store = ExactReadCredentialStore(tmp_path / "credential-store")
    store.enroll_demo("demo-id", FAKE_PEM, now=NOW)
    return store


def verify(store: ExactReadCredentialStore, reply: HttpReply) -> Any:
    FakeSigner.calls = []
    return store.verify_demo(
        FakeHttp(reply),
        timestamp_ms=123,
        now=NOW + timedelta(seconds=1),
        signer_factory=FakeSigner,
    )


def test_no_credential_and_unverified_fail_closed_without_network(tmp_path: Path) -> None:
    for store in (
        ExactReadCredentialStore(tmp_path / "missing"),
        enrolled(tmp_path / "present"),
    ):
        with pytest.raises(CredentialBoundaryError):
            VerifiedDemoCredentialProvider(store).resolve(MarginEnvironment.DEMO)
    assert FakeSigner.calls == []


@pytest.mark.parametrize("state", [CredentialState.DISABLED, CredentialState.QUARANTINED])
def test_disabled_and_quarantined_fail_closed(tmp_path: Path, state: CredentialState) -> None:
    store = enrolled(tmp_path)
    verify(store, HttpReply(200, b'{"enabled":true}'))
    store.set_state(state, now=NOW + timedelta(seconds=2))
    with pytest.raises(CredentialBoundaryError, match="unavailable"):
        VerifiedDemoCredentialProvider(store).resolve(MarginEnvironment.DEMO)


def test_verified_demo_resolves_exact_read_and_production_is_always_blocked(tmp_path: Path) -> None:
    store = enrolled(tmp_path)
    result = verify(store, HttpReply(200, b'{"enabled":true}'))
    assert result.environment_proven and result.perps_enabled
    credential = VerifiedDemoCredentialProvider(store).resolve(MarginEnvironment.DEMO)
    assert credential.environment is MarginEnvironment.DEMO
    assert credential.scopes == frozenset({"read"})
    with pytest.raises(CredentialBoundaryError, match="production"):
        VerifiedDemoCredentialProvider(store).resolve(MarginEnvironment.PRODUCTION)


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failure_leaves_record_unverified(tmp_path: Path, status: int) -> None:
    store = enrolled(tmp_path)
    with pytest.raises(ShadowResearchError):
        verify(store, HttpReply(status, b""))
    assert store.load().state is CredentialState.ENROLLED_UNVERIFIED


@pytest.mark.parametrize(
    "reply",
    [
        HttpReply(200, b"{}"),
        HttpReply(200, b'{"enabled":1}'),
        HttpReply(302, b"", location="https://example.invalid"),
    ],
)
def test_malformed_or_redirect_proof_leaves_unverified(tmp_path: Path, reply: HttpReply) -> None:
    store = enrolled(tmp_path)
    with pytest.raises(ShadowResearchError):
        verify(store, reply)
    assert store.load().state is CredentialState.ENROLLED_UNVERIFIED


def test_timeout_leaves_unverified_and_never_falls_back(tmp_path: Path) -> None:
    store = enrolled(tmp_path)
    transport = FakeHttp(TimeoutError())
    with pytest.raises(ShadowResearchError):
        store.verify_demo(
            transport,
            timestamp_ms=123,
            now=NOW,
            signer_factory=FakeSigner,
        )
    assert [call[0] for call in transport.calls] == [
        ENVIRONMENTS[MarginEnvironment.DEMO].rest_origin
    ]
    assert store.load().state is CredentialState.ENROLLED_UNVERIFIED


def test_enabled_false_proves_demo_but_preserves_entitlement_truth(tmp_path: Path) -> None:
    store = enrolled(tmp_path)
    FakeSigner.calls = []
    transport = FakeHttp(HttpReply(200, b'{"enabled":false}'))
    result = store.verify_demo(
        transport,
        timestamp_ms=123,
        now=NOW,
        signer_factory=FakeSigner,
    )
    assert result.environment_proven and not result.perps_enabled
    assert transport.calls == [
        (
            ENVIRONMENTS[MarginEnvironment.DEMO].rest_origin,
            MARGIN_ENABLED_PATH,
            {"synthetic-auth": "redacted"},
            10,
        )
    ]
    assert FakeSigner.calls == [(123, "GET", MARGIN_ENABLED_PATH)]
    assert store.load().state is CredentialState.VERIFIED_DEMO


def test_tampered_truncated_wrong_schema_and_environmentless_records_fail(tmp_path: Path) -> None:
    store = enrolled(tmp_path)
    original = store.record_path.read_bytes()
    store.record_path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    with pytest.raises(CredentialBoundaryError, match="integrity"):
        store.load()
    store.record_path.write_bytes(b"truncated")
    with pytest.raises(CredentialBoundaryError, match="integrity"):
        store.load()

    master = store.master_key_path.read_bytes()
    for payload in (
        {"schema": STORE_SCHEMA, "version": STORE_VERSION + 1},
        {"key_id": "legacy", "private_key_pem": FAKE_PEM.decode()},
    ):
        store.record_path.write_text(SecretBox(master).seal(json.dumps(payload).encode()))
        os.chmod(store.record_path, 0o600)
        with pytest.raises(CredentialBoundaryError):
            store.load()


def test_production_looking_record_cannot_fall_back_to_demo(tmp_path: Path) -> None:
    store = enrolled(tmp_path)
    master = store.master_key_path.read_bytes()
    payload = json.loads(SecretBox(master).open(store.record_path.read_text()))
    payload["environment"] = "production"
    store.record_path.write_text(SecretBox(master).seal(json.dumps(payload).encode()))
    os.chmod(store.record_path, 0o600)
    with pytest.raises(CredentialBoundaryError):
        VerifiedDemoCredentialProvider(store).resolve(MarginEnvironment.DEMO)


def test_wrong_permissions_and_symlinks_fail_closed(tmp_path: Path) -> None:
    store = enrolled(tmp_path / "permissions")
    os.chmod(store.record_path, 0o644)
    with pytest.raises(CredentialBoundaryError, match="permissions"):
        store.load()

    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    link = tmp_path / "linked-store"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(CredentialBoundaryError, match="path"):
        ExactReadCredentialStore(link).enroll_demo("demo-id", FAKE_PEM, now=NOW)

    source = tmp_path / "source.enc"
    source.write_bytes(b"not-a-credential")
    os.chmod(source, 0o600)
    file_store = enrolled(tmp_path / "file-link")
    file_store.record_path.unlink()
    file_store.record_path.symlink_to(source)
    with pytest.raises(CredentialBoundaryError):
        file_store.load()


def test_secret_redaction_and_enrollment_is_never_verified(tmp_path: Path) -> None:
    store = enrolled(tmp_path)
    record = store.load()
    assert record.state is CredentialState.ENROLLED_UNVERIFIED
    combined = (
        repr(record) + repr(VerifiedDemoCredentialProvider(store)) + repr(SecretBox(b"x" * 32))
    )
    assert "synthetic-test-only" not in combined
    with pytest.raises(CredentialBoundaryError) as caught:
        VerifiedDemoCredentialProvider(store).resolve(MarginEnvironment.DEMO)
    assert "synthetic-test-only" not in str(caught.value)
    disk = store.record_path.read_bytes()
    assert FAKE_PEM not in disk and b"demo-id" not in disk


def test_verification_failure_does_not_overwrite_record(tmp_path: Path) -> None:
    store = enrolled(tmp_path)
    before = store.record_path.read_bytes()
    with pytest.raises(ShadowResearchError):
        verify(store, HttpReply(403, b""))
    assert store.record_path.read_bytes() == before


def test_atomic_reenrollment_keeps_master_key_and_complete_record(tmp_path: Path) -> None:
    store = enrolled(tmp_path)
    master = store.master_key_path.read_bytes()
    store.enroll_demo("demo-id", FAKE_PEM, now=NOW + timedelta(seconds=1))
    assert store.master_key_path.read_bytes() == master
    assert store.load().state is CredentialState.ENROLLED_UNVERIFIED
    assert not list(store.directory.glob(".*.tmp"))


def test_cli_has_fd_ingestion_no_secret_argv_or_environment_input(monkeypatch: Any) -> None:
    help_text = credential_cli.parser().format_help()
    assert "--credential-fd" in help_text
    assert "--private-key" not in help_text
    source = Path(credential_cli.__file__).read_text()
    assert "os.environ" not in source and "private_key_pem" not in source
    monkeypatch.setattr(sys, "argv", ["credential-cli", "verify", "--environment", "production"])
    assert credential_cli.main() == 2


def test_closed_credential_fd_error_is_sanitized() -> None:
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    os.close(write_fd)
    secret = "synthetic-secret-that-must-not-appear"
    with pytest.raises(CredentialBoundaryError) as caught:
        read_private_key_fd(read_fd)
    message = str(caught.value)
    assert message == "credential input descriptor unavailable"
    assert str(read_fd) not in message
    assert secret not in message
    assert "Bad file descriptor" not in message
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("fd", [-1, True])
def test_invalid_credential_fd_is_rejected_without_sensitive_context(fd: Any) -> None:
    secret = "synthetic-secret-that-must-not-appear"
    with pytest.raises(CredentialBoundaryError) as caught:
        read_private_key_fd(fd)
    message = str(caught.value)
    assert message == "invalid credential input descriptor"
    assert secret not in message
    assert "Bad file descriptor" not in message


def test_static_boundary_has_no_production_execution_or_write_surface() -> None:
    module = Path(__file__).parents[1] / "services" / "perps_shadow_research"
    source = "\n".join(
        (module / name).read_text() for name in ("exact_read_credentials.py", "credential_cli.py")
    )
    assert "services.production_execution" not in source
    assert '"POST"' not in source and '"PUT"' not in source and '"PATCH"' not in source
    assert '"DELETE"' not in source
    assert "MarginEnvironment.PRODUCTION," not in source
    assert VERIFICATION_METHOD in source


def test_live_smoke_cli_missing_or_production_credential_is_zero_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def credential_gate(config: Any, provider: Any, **kwargs: Any) -> Any:
        del kwargs
        provider.resolve(config.environment)
        raise AssertionError("network-capable smoke must not start")

    monkeypatch.setattr(live_smoke, "run_live_smoke", credential_gate)
    base = [
        "live-smoke",
        "--ticker",
        "BTC-PERP",
        "--evidence-db",
        str(tmp_path / "e.db"),
        "--live-readonly",
        "--credential-store",
        str(tmp_path / "missing"),
        "--production-credential-store",
        str(tmp_path / "missing-production"),
    ]
    monkeypatch.setattr(sys, "argv", [*base, "--environment", "demo"])
    assert live_smoke.main() == 2
    unverified = enrolled(tmp_path / "unverified")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            *base,
            "--credential-store",
            str(unverified.directory),
            "--environment",
            "demo",
        ],
    )
    assert live_smoke.main() == 2
    monkeypatch.setattr(
        sys,
        "argv",
        [*base, "--environment", "production", "--confirm-production-readonly"],
    )
    assert live_smoke.main() == 2
