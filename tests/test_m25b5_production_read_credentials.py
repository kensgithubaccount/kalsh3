from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pytest

from services.kalshi_account_gateway import production_read_credential_cli
from services.kalshi_account_gateway.auth import RequestSigner
from services.kalshi_account_gateway.production_read_credentials import (
    API_KEYS_PATH,
    PRODUCTION_ORIGIN,
    STORE_SCHEMA,
    STORE_VERSION,
    VERIFICATION_METHOD,
    ProductionCredentialError,
    ProductionCredentialState,
    ProductionReadCredentialStore,
    ProductionReadReply,
    VerifiedProductionReadCredentialProvider,
    read_private_key_fd,
)
from services.kalshi_account_gateway.read_credentials import (
    ExactReadCredential,
    ReadCredentialError,
    ReadEnvironment,
)
from services.neutral_security import SecretBox
from services.perps_shadow_research import live_smoke
from services.perps_shadow_research.domain import ShadowResearchError
from services.perps_shadow_research.exact_read_credentials import (
    CredentialBoundaryError,
    ExactReadCredentialStore,
    VerifiedDemoCredentialProvider,
)

NOW = datetime(2026, 8, 14, 12, tzinfo=UTC)
FAKE_PEM = (
    b"-----BEGIN " + b"PRIVATE KEY-----\nproduction-test-only\n-----END " + b"PRIVATE KEY-----\n"
)
REPLACEMENT_PEM = (
    b"-----BEGIN " + b"PRIVATE KEY-----\nreplacement-test-only\n-----END " + b"PRIVATE KEY-----\n"
)


class FakeSigner:
    calls: ClassVar[list[tuple[str, bytes, int, str, str]]] = []

    def __init__(self, key_id: str, private_key_pem: bytes) -> None:
        self.key_id = key_id
        self.private_key_pem = private_key_pem

    def headers(self, timestamp_ms: int, method: str, path: str) -> dict[str, str]:
        self.calls.append((self.key_id, self.private_key_pem, timestamp_ms, method, path))
        return {"synthetic-auth": "redacted"}


class FakeTransport:
    def __init__(self, reply: ProductionReadReply | Exception) -> None:
        self.reply = reply
        self.calls: list[tuple[str, str, dict[str, str], float]] = []

    def get(
        self, origin: str, path: str, headers: Any, *, timeout_seconds: float
    ) -> ProductionReadReply:
        self.calls.append((origin, path, dict(headers), timeout_seconds))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def api_keys(records: Any, **extra: Any) -> ProductionReadReply:
    return ProductionReadReply(
        200, json.dumps({"api_keys": records, **extra}, separators=(",", ":")).encode()
    )


def enrolled(tmp_path: Path) -> ProductionReadCredentialStore:
    store = ProductionReadCredentialStore(tmp_path / "production-read-store")
    store.enroll("production-id", FAKE_PEM, now=NOW)
    return store


def verify(
    store: ProductionReadCredentialStore,
    reply: ProductionReadReply | Exception,
    *,
    now: datetime = NOW + timedelta(seconds=1),
) -> tuple[Any, FakeTransport]:
    FakeSigner.calls = []
    transport = FakeTransport(reply)
    result = store.verify(
        transport,
        timestamp_ms=123,
        now=now,
        signer_factory=FakeSigner,
    )
    return result, transport


def rewrite_record(store: ProductionReadCredentialStore, changes: dict[str, Any]) -> None:
    master = store.master_key_path.read_bytes()
    payload = json.loads(SecretBox(master).open(store.record_path.read_text()))
    payload.update(changes)
    store.record_path.write_text(SecretBox(master).seal(json.dumps(payload).encode()))
    os.chmod(store.record_path, 0o600)


def test_enrollment_is_explicitly_unverified_encrypted_and_separate(tmp_path: Path) -> None:
    store = enrolled(tmp_path)
    record = store.load()
    assert record.environment is ReadEnvironment.PRODUCTION
    assert record.state is ProductionCredentialState.ENROLLED_UNVERIFIED
    assert record.verification_target is None and record.server_scopes is None
    assert FAKE_PEM not in store.record_path.read_bytes()
    assert store.directory.name == "production-read-store"
    with pytest.raises(ProductionCredentialError):
        VerifiedProductionReadCredentialProvider(store).resolve(ReadEnvironment.PRODUCTION)


def test_exact_single_production_get_proves_exact_read_scope(tmp_path: Path) -> None:
    store = enrolled(tmp_path)
    result, transport = verify(
        store,
        api_keys(
            [
                {"api_key_id": "other", "scopes": ["read", "write"]},
                {
                    "api_key_id": "production-id",
                    "name": "dedicated reader",
                    "scopes": ["read"],
                    "harmless_future_metadata": {"ignored": True},
                },
            ],
            cursor="harmless",
        ),
    )
    assert result.environment_proven and result.server_scopes == frozenset({"read"})
    assert transport.calls == [
        (PRODUCTION_ORIGIN, API_KEYS_PATH, {"synthetic-auth": "redacted"}, 10)
    ]
    assert FakeSigner.calls == [("production-id", FAKE_PEM, 123, "GET", API_KEYS_PATH)]
    record = store.load()
    assert record.state is ProductionCredentialState.VERIFIED_PRODUCTION_READONLY
    assert record.verification_target is ReadEnvironment.PRODUCTION
    assert record.verification_method == VERIFICATION_METHOD
    assert record.verified_key_id == record.key_id
    assert record.verified_fingerprint == record.credential_fingerprint
    assert record.server_scopes == ("read",)
    credential = VerifiedProductionReadCredentialProvider(store).resolve(ReadEnvironment.PRODUCTION)
    assert credential.environment is ReadEnvironment.PRODUCTION
    assert credential.scopes == frozenset({"read"})


@pytest.mark.parametrize(
    "scopes",
    [["read", "write"], ["write", "read"], ["write"], ["read", "admin"], ["read", "read"]],
)
def test_positive_unsafe_server_scope_is_quarantined_and_never_resolves(
    tmp_path: Path, scopes: list[str]
) -> None:
    store = enrolled(tmp_path)
    with pytest.raises(ProductionCredentialError, match="not exactly read-only"):
        verify(store, api_keys([{"api_key_id": "production-id", "scopes": scopes}]))
    assert store.load().state is ProductionCredentialState.QUARANTINED
    with pytest.raises(ProductionCredentialError):
        VerifiedProductionReadCredentialProvider(store).resolve(ReadEnvironment.PRODUCTION)


@pytest.mark.parametrize(
    "record",
    [
        {"api_key_id": "production-id"},
        {"api_key_id": "production-id", "scopes": []},
        {"api_key_id": "production-id", "scopes": "read"},
        {"api_key_id": "production-id", "scopes": [1]},
        {"api_key_id": "production-id", "scopes": [True]},
    ],
)
def test_missing_empty_or_malformed_scope_stays_unverified(
    tmp_path: Path, record: dict[str, Any]
) -> None:
    store = enrolled(tmp_path)
    before = store.record_path.read_bytes()
    with pytest.raises(ProductionCredentialError, match="scopes malformed"):
        verify(store, api_keys([record]))
    assert store.load().state is ProductionCredentialState.ENROLLED_UNVERIFIED
    assert store.record_path.read_bytes() == before


@pytest.mark.parametrize(
    "records",
    [
        [],
        [{"api_key_id": "other", "scopes": ["read"]}],
        [
            {"api_key_id": "production-id", "scopes": ["read"]},
            {"api_key_id": "production-id", "scopes": ["read"]},
        ],
    ],
)
def test_absent_or_ambiguous_exact_key_stays_unverified(
    tmp_path: Path, records: list[dict[str, Any]]
) -> None:
    store = enrolled(tmp_path)
    with pytest.raises(ProductionCredentialError, match="uniquely identified"):
        verify(store, api_keys(records))
    assert store.load().state is ProductionCredentialState.ENROLLED_UNVERIFIED


@pytest.mark.parametrize(
    "reply",
    [
        ProductionReadReply(200, b"{}"),
        ProductionReadReply(200, b'{"api_keys":{}}'),
        ProductionReadReply(200, b'{"api_keys":[1]}'),
        ProductionReadReply(200, b"not-json"),
        ProductionReadReply(200, b'{"api_keys":[]}', content_type="text/plain"),
        ProductionReadReply(500, b"{}"),
    ],
)
def test_malformed_or_non_success_response_stays_unverified(
    tmp_path: Path, reply: ProductionReadReply
) -> None:
    store = enrolled(tmp_path)
    with pytest.raises(ProductionCredentialError):
        verify(store, reply)
    assert store.load().state is ProductionCredentialState.ENROLLED_UNVERIFIED


@pytest.mark.parametrize("status", [401, 403])
def test_authentication_failure_is_not_environment_inference(tmp_path: Path, status: int) -> None:
    store = enrolled(tmp_path)
    with pytest.raises(ProductionCredentialError, match="authentication rejected"):
        verify(store, ProductionReadReply(status, b""))
    assert store.load().state is ProductionCredentialState.ENROLLED_UNVERIFIED


def test_redirect_timeout_and_transport_failure_never_fallback(tmp_path: Path) -> None:
    for name, reply in (
        (
            "redirect",
            ProductionReadReply(
                302, b"", location="https://external-api.demo.kalshi.co/trade-api/v2/api_keys"
            ),
        ),
        ("timeout", TimeoutError()),
        ("transport", OSError()),
    ):
        store = enrolled(tmp_path / name)
        transport = FakeTransport(reply)
        with pytest.raises(ProductionCredentialError):
            store.verify(transport, timestamp_ms=123, now=NOW, signer_factory=FakeSigner)
        assert [call[:2] for call in transport.calls] == [(PRODUCTION_ORIGIN, API_KEYS_PATH)]
        assert store.load().state is ProductionCredentialState.ENROLLED_UNVERIFIED


@pytest.mark.parametrize(
    "state", [ProductionCredentialState.DISABLED, ProductionCredentialState.QUARANTINED]
)
def test_disabled_and_quarantined_local_state_fails_zero_network(
    tmp_path: Path, state: ProductionCredentialState
) -> None:
    store = enrolled(tmp_path)
    store.set_state(state, now=NOW + timedelta(seconds=1))
    FakeSigner.calls = []
    with pytest.raises(ProductionCredentialError):
        VerifiedProductionReadCredentialProvider(store).resolve(ReadEnvironment.PRODUCTION)
    assert FakeSigner.calls == []


def test_provider_rejects_demo_and_internal_proof_mismatch(tmp_path: Path) -> None:
    store = enrolled(tmp_path)
    verify(store, api_keys([{"api_key_id": "production-id", "scopes": ["read"]}]))
    provider = VerifiedProductionReadCredentialProvider(store)
    with pytest.raises(ProductionCredentialError, match="environment mismatch"):
        provider.resolve(ReadEnvironment.DEMO)
    rewrite_record(store, {"verified_key_id": "different"})
    with pytest.raises(ProductionCredentialError):
        provider.resolve(ReadEnvironment.PRODUCTION)


def test_demo_and_production_records_cannot_cross_provider_boundaries(tmp_path: Path) -> None:
    demo_store = ExactReadCredentialStore(tmp_path / "demo")
    demo_store.enroll_demo("demo-id", FAKE_PEM, now=NOW)
    with pytest.raises(ProductionCredentialError):
        VerifiedProductionReadCredentialProvider(
            ProductionReadCredentialStore(demo_store.directory)
        ).resolve(ReadEnvironment.PRODUCTION)

    production_store = enrolled(tmp_path / "production")
    with pytest.raises(CredentialBoundaryError):
        VerifiedDemoCredentialProvider(
            ExactReadCredentialStore(production_store.directory)
        ).resolve(ReadEnvironment.DEMO)


def test_corruption_schema_environment_and_permission_fail_zero_network(tmp_path: Path) -> None:
    cases = ("tamper", "schema", "environment", "permission")
    for case in cases:
        store = enrolled(tmp_path / case)
        if case == "tamper":
            value = bytearray(store.record_path.read_bytes())
            value[-1] ^= 1
            store.record_path.write_bytes(value)
        elif case == "schema":
            rewrite_record(store, {"schema": STORE_SCHEMA, "version": STORE_VERSION + 1})
        elif case == "environment":
            rewrite_record(store, {"environment": None})
        else:
            os.chmod(store.record_path, 0o644)
        with pytest.raises(ProductionCredentialError):
            VerifiedProductionReadCredentialProvider(store).resolve(ReadEnvironment.PRODUCTION)


def test_symlink_store_and_record_attacks_fail_closed(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    link = tmp_path / "linked"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ProductionCredentialError, match="path rejected"):
        ProductionReadCredentialStore(link).enroll("production-id", FAKE_PEM, now=NOW)

    store = enrolled(tmp_path / "file")
    source = tmp_path / "source"
    source.write_bytes(b"ciphertext")
    os.chmod(source, 0o600)
    store.record_path.unlink()
    store.record_path.symlink_to(source)
    with pytest.raises(ProductionCredentialError):
        store.load()


def test_reenrollment_race_cannot_stale_verify_replacement(tmp_path: Path) -> None:
    store = enrolled(tmp_path)

    class ReenrollingTransport(FakeTransport):
        def get(
            self, origin: str, path: str, headers: Any, *, timeout_seconds: float
        ) -> ProductionReadReply:
            result = super().get(origin, path, headers, timeout_seconds=timeout_seconds)
            store.enroll("replacement-id", REPLACEMENT_PEM, now=NOW + timedelta(seconds=1))
            return result

    transport = ReenrollingTransport(
        api_keys([{"api_key_id": "production-id", "scopes": ["read"]}])
    )
    with pytest.raises(ProductionCredentialError, match="changed during verification"):
        store.verify(
            transport,
            timestamp_ms=123,
            now=NOW + timedelta(seconds=2),
            signer_factory=FakeSigner,
        )
    record = store.load()
    assert record.key_id == "replacement-id"
    assert record.state is ProductionCredentialState.ENROLLED_UNVERIFIED


def test_quarantine_requires_explicit_human_reenrollment_and_verify_cannot_restore(
    tmp_path: Path,
) -> None:
    store = enrolled(tmp_path)
    with pytest.raises(ProductionCredentialError, match="not exactly read-only"):
        verify(store, api_keys([{"api_key_id": "production-id", "scopes": ["unknown"]}]))
    assert store.load().state is ProductionCredentialState.QUARANTINED

    transport = FakeTransport(api_keys([{"api_key_id": "production-id", "scopes": ["read"]}]))
    with pytest.raises(ProductionCredentialError, match="not awaiting verification"):
        store.verify(transport, timestamp_ms=123, now=NOW + timedelta(seconds=2))
    assert transport.calls == []
    assert store.load().state is ProductionCredentialState.QUARANTINED

    store.enroll("replacement-id", REPLACEMENT_PEM, now=NOW + timedelta(seconds=3))
    replacement = store.load()
    assert replacement.key_id == "replacement-id"
    assert replacement.state is ProductionCredentialState.ENROLLED_UNVERIFIED


def test_secret_redaction_fd_and_cli_inputs(tmp_path: Path) -> None:
    store = enrolled(tmp_path)
    record = store.load()
    provider = VerifiedProductionReadCredentialProvider(store)
    assert "production-test-only" not in repr(record) + repr(provider)
    with pytest.raises(ProductionCredentialError) as raised:
        provider.resolve(ReadEnvironment.PRODUCTION)
    assert "production-test-only" not in str(raised.value)

    help_text = production_read_credential_cli.parser().format_help()
    assert "--credential-fd" in help_text
    assert "--private-key" not in help_text
    source = Path(production_read_credential_cli.__file__).read_text()
    assert "private_key_pem" not in source
    assert "os.environ" not in source


def test_closed_fd_error_is_sanitized() -> None:
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    os.close(write_fd)
    with pytest.raises(ProductionCredentialError) as raised:
        read_private_key_fd(read_fd)
    assert str(raised.value) == "credential input descriptor unavailable"
    assert raised.value.__cause__ is None


def test_cli_invalid_local_state_fails_before_transport_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_transport() -> None:
        pytest.fail("transport must not be created for missing local credential state")

    monkeypatch.setattr(
        production_read_credential_cli,
        "UrllibProductionReadTransport",
        forbidden_transport,
    )
    assert (
        production_read_credential_cli.main(
            [
                "verify",
                "--environment",
                "production",
                "--store-dir",
                str(tmp_path / "missing"),
            ]
        )
        == 2
    )


def test_static_boundaries_remain_read_only_neutral_and_disconnected() -> None:
    root = Path(__file__).parents[1]
    runtime = (root / "services/kalshi_account_gateway/production_read_credentials.py").read_text()
    cli = (root / "services/kalshi_account_gateway/production_read_credential_cli.py").read_text()
    signer = (root / "services/kalshi_account_gateway/auth.py").read_text().lower()
    smoke = (root / "services/perps_shadow_research/live_smoke.py").read_text()
    combined = runtime + cli
    assert "services.production_execution" not in combined
    assert 'method="POST"' not in combined
    assert 'method="PUT"' not in combined
    assert 'method="PATCH"' not in combined
    assert 'method="DELETE"' not in combined
    assert "/api_keys/create" not in combined and "/api_keys/delete" not in combined
    assert "demo.kalshi" not in combined
    assert "demo" not in signer and "production" not in signer
    assert "approved production read credential boundary required" in smoke
    assert live_smoke.main is not None
    assert RequestSigner.headers is not None


def test_run_live_smoke_structurally_rejects_verified_production_before_side_effects(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    class VerifiedFakeProductionProvider:
        def resolve(self, environment: ReadEnvironment) -> ExactReadCredential:
            calls.append("provider.resolve")
            return ExactReadCredential(environment, "verified-production-id", FAKE_PEM)

    class ForbiddenHttpTransport:
        def get(self, *args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            calls.append("http.get")
            pytest.fail("REST transport must not be called")

    async def forbidden_connector(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        calls.append("websocket.connect")
        pytest.fail("WebSocket connector must not be called")

    evidence_db = tmp_path / "production-evidence.sqlite3"
    config = live_smoke.LiveSmokeConfig(
        ReadEnvironment.PRODUCTION,
        "BTC-PERP",
        evidence_db,
        live_readonly=True,
        confirm_production_readonly=True,
    )
    with pytest.raises(ShadowResearchError, match="approved production read credential boundary"):
        asyncio.run(
            live_smoke.run_live_smoke(
                config,
                VerifiedFakeProductionProvider(),
                http_transport=ForbiddenHttpTransport(),
                connector=forbidden_connector,
            )
        )
    assert calls == []
    assert not evidence_db.exists()


def test_shared_exact_read_credential_validation_error_is_documented_contract() -> None:
    with pytest.raises(ReadCredentialError, match="exact-read") as raised:
        ExactReadCredential(ReadEnvironment.DEMO, "", b"")
    assert not isinstance(raised.value, ShadowResearchError)
