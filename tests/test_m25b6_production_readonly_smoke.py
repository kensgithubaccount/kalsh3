from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from services.kalshi_account_gateway.production_read_credentials import (
    API_KEYS_PATH,
    PRODUCTION_ORIGIN,
    VERIFICATION_METHOD,
    ProductionCredentialError,
    ProductionCredentialState,
    ProductionReadCredentialStore,
    ProductionReadReply,
    ProductionStoredCredential,
    VerifiedProductionReadCredentialProvider,
)
from services.kalshi_account_gateway.read_credentials import ExactReadCredential, ReadEnvironment
from services.neutral_security import SecretBox
from services.perps_shadow_research import live_smoke
from services.perps_shadow_research.domain import ShadowResearchError
from services.perps_shadow_research.live_boundary import ENVIRONMENTS, HttpReply
from services.perps_shadow_research.live_smoke import LiveSmokeConfig, SmokeOutcome, SmokeSummary

SCRIPTED_SMOKE_WINDOW_SECONDS = 1.0

NOW = datetime(2026, 8, 14, 12, tzinfo=UTC)
TICKER = "BTC-PERP"


@pytest.fixture(scope="module")
def private_key() -> bytes:
    return subprocess.run(
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
    ).stdout


def verified_record(private_key: bytes, **changes: Any) -> ProductionStoredCredential:
    values: dict[str, Any] = {
        "environment": ReadEnvironment.PRODUCTION,
        "key_id": "production-read-id",
        "private_key_pem": private_key,
        "credential_fingerprint": "a" * 64,
        "allowed_scope": "read",
        "state": ProductionCredentialState.VERIFIED_PRODUCTION_READONLY,
        "verification_target": ReadEnvironment.PRODUCTION,
        "verification_method": VERIFICATION_METHOD,
        "verified_key_id": "production-read-id",
        "verified_fingerprint": "a" * 64,
        "server_scopes": ("read",),
        "verified_at": NOW,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    return ProductionStoredCredential(**values)


class FakeProductionStore:
    def __init__(self, record: ProductionStoredCredential | Exception, events: list[str]) -> None:
        self.record = record
        self.events = events

    def load(self) -> ProductionStoredCredential:
        self.events.append("credential.resolve")
        if isinstance(self.record, Exception):
            raise self.record
        return self.record


class FakeVerificationTransport:
    def __init__(self, scopes: list[str] | None = None) -> None:
        self.scopes = scopes or ["read"]
        self.calls: list[tuple[str, str, dict[str, str], float]] = []

    def get(
        self, origin: str, path: str, headers: Any, *, timeout_seconds: float
    ) -> ProductionReadReply:
        self.calls.append((origin, path, dict(headers), timeout_seconds))
        return ProductionReadReply(
            200,
            json.dumps(
                {"api_keys": [{"api_key_id": "production-read-id", "scopes": self.scopes}]}
            ).encode(),
        )


def real_store(
    tmp_path: Path, private_key: bytes, *, verify: bool = True
) -> tuple[ProductionReadCredentialStore, FakeVerificationTransport]:
    store = ProductionReadCredentialStore(tmp_path / "production-store")
    store.enroll("production-read-id", private_key, now=NOW)
    transport = FakeVerificationTransport()
    if verify:
        store.verify(transport, timestamp_ms=123, now=NOW + timedelta(seconds=1))
    return store, transport


class ForbiddenHttp:
    def get(self, *args: Any, **kwargs: Any) -> HttpReply:
        del args, kwargs
        pytest.fail("REST must not be reached")


async def forbidden_connector(*args: Any, **kwargs: Any) -> Any:
    del args, kwargs
    pytest.fail("WebSocket must not be reached")


def production_config(tmp_path: Path, **changes: Any) -> LiveSmokeConfig:
    values: dict[str, Any] = {
        "environment": ReadEnvironment.PRODUCTION,
        "ticker": TICKER,
        "evidence_db": tmp_path / "evidence.sqlite3",
        "live_readonly": True,
        "confirm_production_readonly": True,
        "window_seconds": SCRIPTED_SMOKE_WINDOW_SECONDS,
        "max_reconnects": 1,
    }
    values.update(changes)
    return LiveSmokeConfig(**values)


def test_generic_exact_read_provider_cannot_cross_gate_or_cause_side_effects(
    tmp_path: Path, private_key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    class GenericProvider:
        def resolve(self, environment: ReadEnvironment) -> ExactReadCredential:
            calls.append("generic.resolve")
            return ExactReadCredential(environment, "synthetic", private_key)

    forbid_post_gate_side_effects(monkeypatch)
    db = tmp_path / "evidence.sqlite3"
    with pytest.raises(ShadowResearchError, match="approved production"):
        asyncio.run(
            live_smoke.run_live_smoke(
                production_config(tmp_path),
                GenericProvider(),
                http_transport=ForbiddenHttp(),
                connector=forbidden_connector,
            )
        )
    assert calls == []
    assert not db.exists()


def forbid_post_gate_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    class ForbiddenDateTime:
        @staticmethod
        def now(*args: Any, **kwargs: Any) -> datetime:
            del args, kwargs
            pytest.fail("clock must not be read")

    monkeypatch.setattr(live_smoke, "datetime", ForbiddenDateTime)
    monkeypatch.setattr(
        live_smoke,
        "PerpsEvidenceStore",
        lambda *args, **kwargs: pytest.fail("evidence store must not be constructed"),
    )


def assert_wrong_boundary_rejected(
    tmp_path: Path, provider: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    forbid_post_gate_side_effects(monkeypatch)
    with pytest.raises(ShadowResearchError, match="approved production read credential boundary"):
        asyncio.run(
            live_smoke.run_live_smoke(
                production_config(tmp_path),
                provider,  # type: ignore[arg-type]
                http_transport=ForbiddenHttp(),
                connector=forbidden_connector,
            )
        )
    assert not (tmp_path / "evidence.sqlite3").exists()


def test_subclassed_provider_is_rejected_before_store_load(
    tmp_path: Path, private_key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []

    class ProviderSubclass(VerifiedProductionReadCredentialProvider):
        pass

    provider = ProviderSubclass(FakeProductionStore(verified_record(private_key), events))  # type: ignore[arg-type]
    assert_wrong_boundary_rejected(tmp_path, provider, monkeypatch)
    assert events == []


@pytest.mark.parametrize("kind", ["duck", "wrapped"], ids=["duck-store", "wrapped-store"])
def test_duck_or_wrapped_store_is_rejected_without_load(
    tmp_path: Path, private_key: bytes, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    events: list[str] = []
    fake = FakeProductionStore(verified_record(private_key), events)

    class WrappedStore:
        def __init__(self, inner: FakeProductionStore) -> None:
            self.inner = inner

        def load(self) -> ProductionStoredCredential:
            events.append("wrapper.load")
            return self.inner.load()

    store: object = fake if kind == "duck" else WrappedStore(fake)
    provider = VerifiedProductionReadCredentialProvider(store)  # type: ignore[arg-type]
    assert_wrong_boundary_rejected(tmp_path, provider, monkeypatch)
    assert events == []


def test_fake_store_whose_load_asserts_is_never_called(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ExplodingStore:
        def load(self) -> ProductionStoredCredential:
            pytest.fail("fake store load must not be called")

    provider = VerifiedProductionReadCredentialProvider(ExplodingStore())  # type: ignore[arg-type]
    assert_wrong_boundary_rejected(tmp_path, provider, monkeypatch)


def test_subclassed_real_store_overriding_load_is_rejected_before_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class StoreSubclass(ProductionReadCredentialStore):
        def load(self) -> ProductionStoredCredential:
            pytest.fail("subclass load must not be called")

    provider = VerifiedProductionReadCredentialProvider(StoreSubclass(tmp_path / "subclass"))
    assert_wrong_boundary_rejected(tmp_path, provider, monkeypatch)


@pytest.mark.parametrize(
    "state",
    [
        ProductionCredentialState.UNENROLLED,
        ProductionCredentialState.ENROLLED_UNVERIFIED,
        ProductionCredentialState.DISABLED,
        ProductionCredentialState.QUARANTINED,
    ],
)
def test_real_store_nonverified_states_fail_before_smoke_side_effects(
    tmp_path: Path,
    private_key: bytes,
    monkeypatch: pytest.MonkeyPatch,
    state: ProductionCredentialState,
) -> None:
    store = ProductionReadCredentialStore(tmp_path / state.value.lower())
    if state is not ProductionCredentialState.UNENROLLED:
        store.enroll("production-read-id", private_key, now=NOW)
    if state is ProductionCredentialState.DISABLED:
        store.set_state(state, now=NOW + timedelta(seconds=1))
    elif state is ProductionCredentialState.QUARANTINED:
        with pytest.raises(ProductionCredentialError, match="not exactly read-only"):
            store.verify(
                FakeVerificationTransport(["write"]),
                timestamp_ms=123,
                now=NOW + timedelta(seconds=1),
            )
    forbid_post_gate_side_effects(monkeypatch)
    with pytest.raises(ProductionCredentialError):
        asyncio.run(
            live_smoke.run_live_smoke(
                production_config(tmp_path),
                VerifiedProductionReadCredentialProvider(store),
                http_transport=ForbiddenHttp(),
                connector=forbidden_connector,
            )
        )
    assert not (tmp_path / "evidence.sqlite3").exists()


@pytest.mark.parametrize("corruption", ["ciphertext", "metadata"])
def test_real_store_corrupt_or_mismatched_metadata_fails_before_smoke_side_effects(
    tmp_path: Path,
    private_key: bytes,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    store, _ = real_store(tmp_path, private_key)
    if corruption == "ciphertext":
        store.record_path.write_bytes(b"corrupt")
        os.chmod(store.record_path, 0o600)
    else:
        master = store.master_key_path.read_bytes()
        payload = json.loads(SecretBox(master).open(store.record_path.read_text()))
        payload["verified_key_id"] = "mismatched"
        store.record_path.write_text(SecretBox(master).seal(json.dumps(payload).encode()))
        os.chmod(store.record_path, 0o600)
    forbid_post_gate_side_effects(monkeypatch)
    with pytest.raises(ProductionCredentialError):
        asyncio.run(
            live_smoke.run_live_smoke(
                production_config(tmp_path),
                VerifiedProductionReadCredentialProvider(store),
                http_transport=ForbiddenHttp(),
                connector=forbidden_connector,
            )
        )
    assert not (tmp_path / "evidence.sqlite3").exists()


def test_production_credential_material_is_redacted_from_repr_and_errors(
    private_key: bytes,
) -> None:
    events: list[str] = []
    record = verified_record(private_key)
    provider = VerifiedProductionReadCredentialProvider(FakeProductionStore(record, events))  # type: ignore[arg-type]
    credential = provider.resolve(ReadEnvironment.PRODUCTION)
    rendered = repr(record) + repr(provider) + repr(credential)
    assert private_key.decode() not in rendered
    bad_provider = VerifiedProductionReadCredentialProvider(
        FakeProductionStore(verified_record(private_key, server_scopes=("write",)), events)  # type: ignore[arg-type]
    )
    with pytest.raises(ProductionCredentialError) as raised:
        bad_provider.resolve(ReadEnvironment.PRODUCTION)
    assert private_key.decode() not in str(raised.value)


def test_production_confirmation_ticker_time_and_reconnect_bounds(tmp_path: Path) -> None:
    for changes, message in [
        ({"confirm_production_readonly": False}, "confirm-production"),
        ({"ticker": ""}, "ticker"),
        ({"window_seconds": 60.01}, "unsafe"),
        ({"max_reconnects": 2}, "unsafe"),
    ]:
        with pytest.raises(ShadowResearchError, match=message):
            production_config(tmp_path, **changes)


def market_reply() -> HttpReply:
    body = {
        "market": {
            "ticker": TICKER,
            "status": "active",
            "title": "Bitcoin",
            "exchange_index": 4,
            "contract_size": "1.000000",
            "tick_size": "0.50",
            "fractional_trading_enabled": True,
            "schedule": None,
        }
    }
    return HttpReply(200, json.dumps(body).encode())


class FakeHttp:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[tuple[str, str, dict[str, str], float]] = []
        self.replies = iter([market_reply(), HttpReply(200, b'{"enabled":true}')])

    def get(self, origin: str, path: str, headers: Any, *, timeout_seconds: float) -> HttpReply:
        self.events.append("rest.get")
        self.calls.append((origin, path, dict(headers), timeout_seconds))
        return next(self.replies)


class FakeWebSocket:
    def __init__(self, frames: list[dict[str, object]]) -> None:
        self.frames = iter(json.dumps(frame) for frame in frames)
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    async def recv(self) -> str:
        try:
            return next(self.frames)
        except StopIteration:
            raise TimeoutError("scripted fake session exhausted") from None

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def close(self) -> None:
        self.closed = True


def subscribed(channel: str, sid: int, command_id: int) -> dict[str, object]:
    return {"type": "subscribed", "id": command_id, "msg": {"channel": channel, "sid": sid}}


def snapshot() -> dict[str, object]:
    return {
        "type": "orderbook_snapshot",
        "sid": 7,
        "seq": 1,
        "msg": {"market_ticker": TICKER, "bid": [["100", "2"]], "ask": [["101", "3"]]},
    }


def delta() -> dict[str, object]:
    return {
        "type": "orderbook_delta",
        "sid": 7,
        "seq": 2,
        "msg": {"market_ticker": TICKER, "price": "100", "delta": "1", "side": "bid"},
    }


def ticker() -> dict[str, object]:
    return {
        "type": "ticker",
        "sid": 8,
        "msg": {
            "market_ticker": TICKER,
            "price": "100.5",
            "bid": "100",
            "ask": "101",
            "bid_size_fp": "3",
            "ask_size_fp": "4",
            "last_trade_size_fp": "1",
            "volume": "10",
            "volume_notional_value_dollars": "1000",
            "volume_24h": "5",
            "volume_24h_notional_value_dollars": "500",
            "open_interest": "7",
            "open_interest_notional_value_dollars": "700",
            "ts_ms": 1_786_622_400_000,
        },
    }


def test_verified_production_composes_complete_offline_bounded_smoke(
    tmp_path: Path, private_key: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    store, verification_transport = real_store(tmp_path, private_key)
    assert store.load().state is ProductionCredentialState.VERIFIED_PRODUCTION_READONLY
    assert [(origin, path) for origin, path, _, _ in verification_transport.calls] == [
        (PRODUCTION_ORIGIN, API_KEYS_PATH)
    ]
    provider = VerifiedProductionReadCredentialProvider(store)
    original_load = ProductionReadCredentialStore.load

    def tracked_load(target: ProductionReadCredentialStore) -> ProductionStoredCredential:
        events.append("credential.resolve")
        return original_load(target)

    monkeypatch.setattr(ProductionReadCredentialStore, "load", tracked_load)
    http = FakeHttp(events)
    sockets = [
        FakeWebSocket(
            [
                subscribed("orderbook_delta", 7, 0),
                subscribed("ticker", 8, 1),
                snapshot(),
                ticker(),
                delta(),
            ]
        ),
        FakeWebSocket(
            [subscribed("orderbook_delta", 7, 0), subscribed("ticker", 8, 1), snapshot()]
        ),
    ]
    connector_calls: list[tuple[str, dict[str, Any]]] = []

    async def connector(url: str, **kwargs: Any) -> FakeWebSocket:
        events.append("websocket.connect")
        connector_calls.append((url, kwargs))
        return sockets[len(connector_calls) - 1]

    summary = asyncio.run(
        live_smoke.run_live_smoke(
            production_config(tmp_path), provider, http_transport=http, connector=connector
        )
    )
    assert summary.outcome is SmokeOutcome.SUCCESS
    assert events[0] == "credential.resolve"
    assert events.count("credential.resolve") == 1
    config = ENVIRONMENTS[ReadEnvironment.PRODUCTION]
    assert [(origin, path) for origin, path, _, _ in http.calls] == [
        (config.rest_origin, f"/trade-api/v2/margin/markets/{TICKER}"),
        (config.rest_origin, "/trade-api/v2/margin/enabled"),
    ]
    assert len(connector_calls) == 2
    assert {call[0] for call in connector_calls} == {config.websocket_url}
    assert all(call[1]["open_timeout"] == 10 for call in connector_calls)
    commands = [command for socket in sockets for command in socket.sent]
    assert {command["cmd"] for command in commands} == {"subscribe", "unsubscribe"}
    subscriptions = [command for command in commands if command["cmd"] == "subscribe"]
    assert {tuple(command["params"]["channels"]) for command in subscriptions} == {
        ("orderbook_delta",),
        ("ticker",),
    }
    assert all(socket.closed for socket in sockets)
    assert (summary.snapshots, summary.deltas, summary.tickers) == (2, 1, 1)


def test_demo_remains_compatible_without_production_provider(tmp_path: Path) -> None:
    config = LiveSmokeConfig(
        ReadEnvironment.DEMO,
        TICKER,
        tmp_path / "demo.sqlite3",
        live_readonly=True,
        window_seconds=300,
        max_reconnects=3,
    )
    assert config.environment is ReadEnvironment.DEMO


def test_cli_selects_separate_environment_store_without_secret_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    selected: list[object] = []

    async def fake_run(config: LiveSmokeConfig, provider: object, **kwargs: Any) -> SmokeSummary:
        del kwargs
        selected.append(provider)
        return SmokeSummary(
            SmokeOutcome.SUCCESS, config.environment, config.ticker, True, 4, (), 0, 0, 0, 0, 0
        )

    monkeypatch.setattr(live_smoke, "run_live_smoke", fake_run)
    monkeypatch.setattr(
        live_smoke.VerifiedProductionReadCredentialProvider,
        "resolve",
        lambda self, environment: pytest.fail("CLI must not pre-resolve production credential"),
    )
    monkeypatch.setattr(
        live_smoke.VerifiedDemoCredentialProvider,
        "resolve",
        lambda self, environment: pytest.fail("CLI must not pre-resolve DEMO credential"),
    )
    demo_store = tmp_path / "demo-store"
    production_store = tmp_path / "production-store"
    common = [
        "smoke",
        "--ticker",
        TICKER,
        "--evidence-db",
        str(tmp_path / "e.db"),
        "--live-readonly",
    ]
    monkeypatch.setattr(
        sys,
        "argv",
        [
            *common,
            "--environment",
            "production",
            "--confirm-production-readonly",
            "--credential-store",
            str(demo_store),
            "--production-credential-store",
            str(production_store),
        ],
    )
    assert live_smoke.main() == 0
    production_provider = selected.pop()
    assert type(production_provider) is VerifiedProductionReadCredentialProvider
    assert production_provider.store.directory == production_store  # type: ignore[attr-defined]
    monkeypatch.setattr(
        sys,
        "argv",
        [
            *common,
            "--environment",
            "demo",
            "--credential-store",
            str(demo_store),
            "--production-credential-store",
            str(production_store),
        ],
    )
    assert live_smoke.main() == 0
    assert selected.pop().store.directory == demo_store  # type: ignore[attr-defined]
    assert "PRIVATE KEY" not in capsys.readouterr().out


def test_cli_missing_production_credential_is_sanitized_and_zero_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "smoke",
            "--environment",
            "production",
            "--ticker",
            TICKER,
            "--evidence-db",
            str(tmp_path / "e.db"),
            "--live-readonly",
            "--confirm-production-readonly",
            "--production-credential-store",
            str(tmp_path / "missing"),
        ],
    )
    assert live_smoke.main() == 2
    assert capsys.readouterr().out == "BLOCKER: verified PRODUCTION read-only smoke unavailable\n"
    assert not (tmp_path / "e.db").exists()
