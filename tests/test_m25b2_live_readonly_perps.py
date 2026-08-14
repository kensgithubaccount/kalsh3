from __future__ import annotations

import asyncio
import inspect
import json
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from services.kalshi_account_gateway.auth import AuthenticationError, signature_message
from services.perps_shadow_research import live_transport
from services.perps_shadow_research.domain import ShadowResearchError
from services.perps_shadow_research.live_boundary import (
    ENVIRONMENTS,
    MARGIN_ENABLED_PATH,
    MAX_HTTP_RESPONSE_BYTES,
    ExactReadCredential,
    HttpReply,
    MarginAuthenticationFailure,
    MarginEnabledClient,
    MarginEnvironment,
    MarginUpstreamFailure,
    PerpsMarketClient,
    resolve_signer,
)
from services.perps_shadow_research.live_smoke import (
    LiveSmokeConfig,
    SmokeOutcome,
    collect_live_evidence,
    run_live_smoke,
)
from services.perps_shadow_research.live_transport import (
    MAX_WEBSOCKET_MESSAGE_BYTES,
    AsyncMarginTransport,
)
from services.perps_shadow_research.margin_protocol import MarginChannel
from services.perps_shadow_research.perps_metadata import parse_perps_market
from services.perps_shadow_research.perps_runtime import (
    OfflinePerpsEvidenceRuntime,
    ScriptedPerpsTransport,
)
from services.perps_shadow_research.perps_store import PerpsEvidenceStore

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


class FakeSigner:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str, str]] = []

    def headers(self, timestamp_ms: int, method: str, path: str) -> dict[str, str]:
        self.calls.append((timestamp_ms, method, path))
        return {"auth": "redacted"}


class FakeHttp:
    def __init__(self, replies: list[HttpReply]) -> None:
        self.replies = iter(replies)
        self.calls: list[tuple[str, str, dict[str, str], float]] = []

    def get(self, origin: str, path: str, headers: Any, *, timeout_seconds: float) -> HttpReply:
        self.calls.append((origin, path, dict(headers), timeout_seconds))
        return next(self.replies)


def market_reply(**changes: object) -> HttpReply:
    market: dict[str, object] = {
        "ticker": "BTC-PERP",
        "status": "active",
        "title": "Bitcoin",
        "exchange_index": 4,
        "contract_size": "1.000000",
        "tick_size": "0.50",
        "fractional_trading_enabled": True,
        "schedule": None,
    }
    market.update(changes)
    return HttpReply(200, json.dumps({"market": market}).encode())


@pytest.mark.parametrize("environment", list(MarginEnvironment))
def test_fixed_hosts_and_public_market_contract(environment: MarginEnvironment) -> None:
    transport = FakeHttp([market_reply()])
    market = PerpsMarketClient(environment, transport, sleep=lambda _: None).get_market(
        "BTC-PERP", observed_at=NOW
    )
    assert transport.calls == [
        (
            ENVIRONMENTS[environment].rest_origin,
            "/trade-api/v2/margin/markets/BTC-PERP",
            {},
            10,
        )
    ]
    assert market.exchange_index == 4
    assert market.contract_size == Decimal("1.000000")
    assert market.tick_size == Decimal("0.50")


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        (HttpReply(200, b"{}"), "market object"),
        (HttpReply(200, b'{"market":{"ticker":"wrong"}}'), "required Perps"),
        (HttpReply(302, b"", location="https://evil.invalid"), "redirect"),
        (HttpReply(200, b"x" * (MAX_HTTP_RESPONSE_BYTES + 1)), "size"),
        (HttpReply(404, b""), "not found"),
    ],
)
def test_market_malformed_redirect_oversize_and_404(reply: HttpReply, message: str) -> None:
    with pytest.raises(ShadowResearchError, match=message):
        PerpsMarketClient(
            MarginEnvironment.DEMO, FakeHttp([reply]), max_retries=0, sleep=lambda _: None
        ).get_market("BTC-PERP", observed_at=NOW)


def test_ticker_mismatch_closed_market_nan_and_timeout() -> None:
    with pytest.raises(ShadowResearchError, match="ticker mismatch"):
        client = PerpsMarketClient(
            MarginEnvironment.DEMO, FakeHttp([market_reply(ticker="ETH-PERP")])
        )
        client.get_market("BTC-PERP", observed_at=NOW)
    with pytest.raises(ShadowResearchError, match="not currently open"):
        client = PerpsMarketClient(
            MarginEnvironment.DEMO, FakeHttp([market_reply(status="closed")])
        )
        client.get_market("BTC-PERP", observed_at=NOW)
    body = market_reply().body.replace(b'"exchange_index": 4', b'"exchange_index": NaN')
    bad = HttpReply(200, body)
    with pytest.raises(MarginUpstreamFailure, match="malformed"):
        PerpsMarketClient(MarginEnvironment.DEMO, FakeHttp([bad])).get_market(
            "BTC-PERP", observed_at=NOW
        )

    class TimeoutHttp(FakeHttp):
        def get(self, *args: Any, **kwargs: Any) -> HttpReply:
            raise TimeoutError

    with pytest.raises(MarginUpstreamFailure, match="bounded retries"):
        PerpsMarketClient(
            MarginEnvironment.DEMO, TimeoutHttp([]), max_retries=1, sleep=lambda _: None
        ).get_market("BTC-PERP", observed_at=NOW)


def test_enabled_exact_path_true_false_malformed_auth_and_bounded_retry() -> None:
    signer = FakeSigner()
    transport = FakeHttp([HttpReply(429, b""), HttpReply(200, b'{"enabled":true}')])
    client = MarginEnabledClient(
        MarginEnvironment.PRODUCTION,
        signer,  # type: ignore[arg-type]
        transport,
        max_retries=1,
        sleep=lambda _: None,
    )
    assert client.enabled(timestamp_ms=123)
    assert signer.calls == [(123, "GET", MARGIN_ENABLED_PATH)]
    assert all(call[1] == MARGIN_ENABLED_PATH for call in transport.calls)
    assert not MarginEnabledClient(
        MarginEnvironment.DEMO,
        signer,  # type: ignore[arg-type]
        FakeHttp([HttpReply(200, b'{"enabled":false}')]),
    ).enabled(timestamp_ms=124)
    with pytest.raises(MarginUpstreamFailure, match="malformed"):
        MarginEnabledClient(
            MarginEnvironment.DEMO,
            signer,  # type: ignore[arg-type]
            FakeHttp([HttpReply(200, b'{"enabled":1}')]),
        ).enabled(timestamp_ms=125)
    for status in (401, 403):
        with pytest.raises(MarginAuthenticationFailure):
            MarginEnabledClient(
                MarginEnvironment.DEMO,
                signer,  # type: ignore[arg-type]
                FakeHttp([HttpReply(status, b"")]),
            ).enabled(timestamp_ms=126)


def test_credential_environment_provenance_and_get_head_only() -> None:
    class Provider:
        def resolve(self, environment: MarginEnvironment) -> ExactReadCredential:
            del environment
            return ExactReadCredential(MarginEnvironment.DEMO, "id", b"fake")

    with pytest.raises(MarginAuthenticationFailure, match="environment mismatch"):
        resolve_signer(Provider(), MarginEnvironment.PRODUCTION)
    assert signature_message(1, "GET", "/trade-api/ws/v2/margin").endswith(
        b"GET/trade-api/ws/v2/margin"
    )
    with pytest.raises(AuthenticationError, match="GET and HEAD"):
        signature_message(1, "POST", MARGIN_ENABLED_PATH)


class FakeWebSocket:
    def __init__(self, frames: list[object], events: list[str]) -> None:
        self.frames = iter(frames)
        self.events = events
        self.sent: list[str] = []
        self.closed = False

    async def recv(self) -> Any:
        self.events.append("recv")
        return next(self.frames)

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def close(self) -> None:
        self.closed = True


def test_websocket_exact_url_signing_raw_receipt_order_and_protocol_only() -> None:
    async def run() -> None:
        events: list[str] = []
        websocket = FakeWebSocket([b'{"type":"ticker"}'], events)
        connector_calls: list[tuple[str, dict[str, Any]]] = []

        async def connector(url: str, **kwargs: Any) -> FakeWebSocket:
            connector_calls.append((url, kwargs))
            return websocket

        signer = FakeSigner()

        def mono() -> int:
            events.append("monotonic")
            return 7

        def clock() -> datetime:
            events.append("wall")
            return NOW

        transport = AsyncMarginTransport(
            MarginEnvironment.DEMO,
            signer,  # type: ignore[arg-type]
            connector,
            clock_ms=lambda: 123,
            monotonic_ns=mono,
            utc_clock=clock,
        )
        epoch = await transport.connect()
        assert epoch.int != 0
        assert signer.calls == [(123, "GET", "/trade-api/ws/v2/margin")]
        assert connector_calls[0][0] == ENVIRONMENTS[MarginEnvironment.DEMO].websocket_url
        assert connector_calls[0][1]["additional_headers"] == {"auth": "redacted"}
        assert connector_calls[0][1]["max_size"] == MAX_WEBSOCKET_MESSAGE_BYTES
        frame = await transport.receive()
        assert frame.raw == b'{"type":"ticker"}'
        assert events == ["recv", "monotonic", "wall"]
        assert transport.protocol is not None
        command = transport.protocol.subscribe(MarginChannel.TICKER, ("BTC-PERP",))
        await transport.send_protocol_command(command)
        with pytest.raises(ShadowResearchError, match="protocol state"):
            await transport.send_protocol_command({"id": 999, "cmd": "subscribe"})
        await transport.close()
        assert websocket.closed and transport.epoch is None

    asyncio.run(run())


def test_websocket_requires_exact_protocol_created_command_payload() -> None:
    async def run() -> None:
        websocket = FakeWebSocket([], [])

        async def connector(url: str, **kwargs: Any) -> FakeWebSocket:
            del url, kwargs
            return websocket

        transport = AsyncMarginTransport(
            MarginEnvironment.DEMO,
            FakeSigner(),
            connector,  # type: ignore[arg-type]
        )
        await transport.connect()
        assert transport.protocol is not None
        protocol = transport.protocol

        untouched = protocol.subscribe(MarginChannel.TICKER, ("BTC-PERP",))
        await transport.send_protocol_command(untouched)

        mutations = [
            lambda command: command["params"]["channels"].__setitem__(0, "user_orders"),
            lambda command: command["params"]["market_tickers"].__setitem__(0, "ETH-PERP"),
            lambda command: command["params"]["sids"].__setitem__(0, 99),
            lambda command: command.__setitem__("params", {}),
            lambda command: command.__setitem__("extra", True),
        ]
        commands = [
            protocol.subscribe(MarginChannel.ORDERBOOK, ("BTC-PERP",)),
            protocol.subscribe(MarginChannel.ORDERBOOK, ("BTC-PERP",)),
            protocol.unsubscribe(7),
            protocol.list_subscriptions(),
            protocol.list_subscriptions(),
        ]
        for command, mutate in zip(commands, mutations, strict=True):
            mutate(command)
            with pytest.raises(ShadowResearchError, match="protocol state"):
                await transport.send_protocol_command(command)

        canonical = protocol.list_subscriptions()
        handcrafted = dict(canonical)
        with pytest.raises(ShadowResearchError, match="protocol state"):
            await transport.send_protocol_command(handcrafted)
        assert len(websocket.sent) == 1

    asyncio.run(run())


def test_cross_protocol_command_and_boolean_id_are_rejected() -> None:
    async def run() -> None:
        websockets = [FakeWebSocket([], []), FakeWebSocket([], [])]

        async def connector(url: str, **kwargs: Any) -> FakeWebSocket:
            del url, kwargs
            return websockets.pop(0)

        first = AsyncMarginTransport(MarginEnvironment.DEMO, FakeSigner(), connector)
        second = AsyncMarginTransport(MarginEnvironment.DEMO, FakeSigner(), connector)
        await first.connect()
        await second.connect()
        assert first.protocol is not None
        command = first.protocol.list_subscriptions()
        with pytest.raises(ShadowResearchError, match="protocol state"):
            await second.send_protocol_command(command)
        command["id"] = True
        with pytest.raises(ShadowResearchError, match="protocol state"):
            await first.send_protocol_command(command)

    asyncio.run(run())


def test_unsubscribe_and_list_acknowledgements_clear_only_matching_pending() -> None:
    from services.perps_shadow_research.margin_protocol import MarginProtocolState

    protocol = MarginProtocolState(UUID(int=1))
    unsubscribe = protocol.unsubscribe(7)
    listed = protocol.list_subscriptions()
    protocol.command_acknowledged(
        {"id": unsubscribe["id"], "type": "unsubscribed", "sid": 7, "seq": 1}
    )
    assert unsubscribe["id"] not in protocol.pending and listed["id"] in protocol.pending
    protocol.command_acknowledged({"id": listed["id"], "type": "ok", "msg": []})
    assert not protocol.pending


def test_command_acknowledgement_mismatch_preserves_pending() -> None:
    from services.perps_shadow_research.margin_protocol import MarginProtocolState

    protocol = MarginProtocolState(UUID(int=1))
    command = protocol.unsubscribe(7)
    with pytest.raises(ShadowResearchError, match="does not match"):
        protocol.command_acknowledged(
            {"id": command["id"], "type": "unsubscribed", "sid": 8, "seq": 1}
        )
    assert command["id"] in protocol.pending


def _subscribed(channel: str, sid: int, command_id: int) -> dict[str, object]:
    return {"type": "subscribed", "id": command_id, "msg": {"channel": channel, "sid": sid}}


def _snapshot() -> dict[str, object]:
    return {
        "type": "orderbook_snapshot",
        "sid": 7,
        "seq": 1,
        "msg": {
            "market_ticker": "BTC-PERP",
            "bid": [["100.00", "2.00"]],
            "ask": [["101.00", "3.00"]],
        },
    }


def _delta() -> dict[str, object]:
    return {
        "type": "orderbook_delta",
        "sid": 7,
        "seq": 2,
        "msg": {
            "market_ticker": "BTC-PERP",
            "price": "100.00",
            "delta": "1.00",
            "side": "bid",
        },
    }


def _ticker() -> dict[str, object]:
    return {
        "type": "ticker",
        "sid": 8,
        "msg": {
            "market_ticker": "BTC-PERP",
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


class BoundedFakeWebSocket(FakeWebSocket):
    async def recv(self) -> Any:
        self.events.append("recv")
        try:
            return next(self.frames)
        except StopIteration:
            await asyncio.sleep(3600)
            raise AssertionError("unreachable") from None


@pytest.mark.parametrize(
    "second_session_frames",
    [
        [_subscribed("orderbook_delta", 7, 0), _subscribed("ticker", 8, 1)],
        [
            _subscribed("orderbook_delta", 7, 0),
            _subscribed("ticker", 8, 1),
            _ticker(),
        ],
        [
            _subscribed("orderbook_delta", 7, 0),
            _subscribed("ticker", 8, 1),
            _delta(),
        ],
    ],
    ids=["ack", "ticker", "delta"],
)
def test_reconnect_requires_session_local_snapshot(
    tmp_path: Path, second_session_frames: list[dict[str, object]]
) -> None:
    async def run() -> None:
        sessions = iter(
            [
                BoundedFakeWebSocket(
                    [
                        json.dumps(_subscribed("orderbook_delta", 7, 0)),
                        json.dumps(_subscribed("ticker", 8, 1)),
                        json.dumps(_snapshot()),
                        json.dumps(_delta()),
                        json.dumps(_ticker()),
                    ],
                    [],
                ),
                BoundedFakeWebSocket([json.dumps(item) for item in second_session_frames], []),
            ]
        )

        async def connector(url: str, **kwargs: Any) -> BoundedFakeWebSocket:
            del url, kwargs
            return next(sessions)

        metadata = parse_perps_market(json.loads(market_reply().body)["market"], observed_at=NOW)
        runtime = OfflinePerpsEvidenceRuntime(
            metadata,
            PerpsEvidenceStore(tmp_path / "evidence.sqlite3"),
            ScriptedPerpsTransport(),
            lambda: datetime.now(UTC),
            time.monotonic_ns,
            enabled=True,
        )
        config = LiveSmokeConfig(
            MarginEnvironment.DEMO,
            "BTC-PERP",
            tmp_path / "evidence.sqlite3",
            live_readonly=True,
            window_seconds=0.02,
        )
        epochs, snapshots, deltas, tickers = await collect_live_evidence(
            config,
            runtime,
            FakeSigner(),  # type: ignore[arg-type]
            connector=connector,  # type: ignore[arg-type]
        )
        assert len(set(epochs)) == 2
        assert snapshots == 1
        assert deltas == 1
        assert tickers >= 1
        assert runtime.health().accepted_snapshot_count == 1

    asyncio.run(run())


def test_fresh_reconnect_snapshot_is_counted_for_success(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        initial = [
            _subscribed("orderbook_delta", 7, 0),
            _subscribed("ticker", 8, 1),
            _snapshot(),
            _delta(),
            _ticker(),
        ]
        reconnect = [
            _subscribed("orderbook_delta", 7, 0),
            _subscribed("ticker", 8, 1),
            _snapshot(),
        ]
        sessions = iter(
            BoundedFakeWebSocket([json.dumps(item) for item in frames], [])
            for frames in (initial, reconnect)
        )

        async def connector(url: str, **kwargs: Any) -> BoundedFakeWebSocket:
            del url, kwargs
            return next(sessions)

        metadata = parse_perps_market(json.loads(market_reply().body)["market"], observed_at=NOW)
        runtime = OfflinePerpsEvidenceRuntime(
            metadata,
            PerpsEvidenceStore(tmp_path / "evidence.sqlite3"),
            ScriptedPerpsTransport(),
            lambda: datetime.now(UTC),
            time.monotonic_ns,
            enabled=True,
        )
        config = LiveSmokeConfig(
            MarginEnvironment.DEMO,
            "BTC-PERP",
            tmp_path / "evidence.sqlite3",
            live_readonly=True,
            window_seconds=0.02,
        )
        _, snapshots, deltas, tickers = await collect_live_evidence(
            config,
            runtime,
            FakeSigner(),  # type: ignore[arg-type]
            connector=connector,  # type: ignore[arg-type]
        )
        assert (snapshots, deltas, tickers) == (2, 1, 1)
        assert runtime.health().accepted_snapshot_count == 2

    asyncio.run(run())


@pytest.mark.parametrize("raw", [object(), "x" * (MAX_WEBSOCKET_MESSAGE_BYTES + 1)])
def test_websocket_rejects_bad_frame_type_and_oversize(raw: object) -> None:
    async def run() -> None:
        async def connector(url: str, **kwargs: Any) -> FakeWebSocket:
            del url, kwargs
            return FakeWebSocket([raw], [])

        transport = AsyncMarginTransport(
            MarginEnvironment.DEMO,
            FakeSigner(),
            connector,  # type: ignore[arg-type]
        )
        await transport.connect()
        with pytest.raises(ShadowResearchError):
            await transport.receive()

    asyncio.run(run())


def test_smoke_opt_in_production_confirmation_and_path_guard(tmp_path: Path) -> None:
    with pytest.raises(ShadowResearchError, match="live-readonly"):
        LiveSmokeConfig(MarginEnvironment.DEMO, "BTC-PERP", tmp_path / "e.db")
    with pytest.raises(ShadowResearchError, match="confirm-production"):
        LiveSmokeConfig(
            MarginEnvironment.PRODUCTION, "BTC-PERP", tmp_path / "e.db", live_readonly=True
        )
    with pytest.raises(ShadowResearchError, match="fixture/data"):
        LiveSmokeConfig(
            MarginEnvironment.DEMO,
            "BTC-PERP",
            tmp_path / "fixtures" / "e.db",
            live_readonly=True,
        )


def test_smoke_path_guard_resolves_symlink_parent(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    linked = tmp_path / "safe-name"
    linked.symlink_to(fixtures, target_is_directory=True)
    with pytest.raises(ShadowResearchError, match="fixture/data"):
        LiveSmokeConfig(MarginEnvironment.DEMO, "BTC-PERP", linked / "e.db", live_readonly=True)
    config = LiveSmokeConfig(
        MarginEnvironment.DEMO, "BTC-PERP", tmp_path / "normal" / "e.db", live_readonly=True
    )
    assert config.evidence_db == tmp_path / "normal" / "e.db"


class FakeProvider:
    def resolve(self, environment: MarginEnvironment) -> ExactReadCredential:
        return ExactReadCredential(environment, "fake-id", b"fake-key")


def _session_frames(
    *, snapshot: bool = True, delta: bool = True, ticker_event: bool = True
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    initial = [
        _subscribed("orderbook_delta", 7, 0),
        _subscribed("ticker", 8, 1),
    ]
    if snapshot:
        initial.append(_snapshot())
    if ticker_event:
        initial.append(_ticker())
    if delta:
        initial.append(_delta())
    reconnect = [
        _subscribed("orderbook_delta", 7, 0),
        _subscribed("ticker", 8, 1),
    ]
    if snapshot:
        reconnect.append(_snapshot())
    return initial, reconnect


async def _direct_smoke(
    monkeypatch: pytest.MonkeyPatch,
    db: Path,
    sessions: tuple[list[dict[str, object]], list[dict[str, object]]],
    *,
    enabled: bool = True,
    epochs: tuple[UUID, UUID] | None = None,
) -> tuple[Any, int]:
    monkeypatch.setattr(
        "services.perps_shadow_research.live_smoke.resolve_signer",
        lambda provider, environment: FakeSigner(),
    )
    if epochs is not None:
        epoch_values = iter(epochs)
        monkeypatch.setattr(live_transport, "uuid4", lambda: next(epoch_values))
    sockets = iter(
        BoundedFakeWebSocket([json.dumps(item) for item in frames], []) for frames in sessions
    )
    connection_count = 0

    async def connector(url: str, **kwargs: Any) -> BoundedFakeWebSocket:
        nonlocal connection_count
        del url, kwargs
        connection_count += 1
        return next(sockets)

    summary = await run_live_smoke(
        LiveSmokeConfig(
            MarginEnvironment.DEMO,
            "BTC-PERP",
            db,
            live_readonly=True,
            window_seconds=0.02,
        ),
        FakeProvider(),
        http_transport=FakeHttp(
            [market_reply(), HttpReply(200, json.dumps({"enabled": enabled}).encode())]
        ),
        connector=connector,
    )
    return summary, connection_count


def test_run_live_smoke_preseeded_rows_do_not_help_and_current_rows_succeed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        db = tmp_path / "evidence.sqlite3"
        success, _ = await _direct_smoke(monkeypatch, db, _session_frames())
        assert success.outcome is SmokeOutcome.SUCCESS
        assert (success.book_rows, success.market_state_rows) == (3, 1)

        stale_only, _ = await _direct_smoke(
            monkeypatch,
            db,
            (
                [_subscribed("orderbook_delta", 7, 0), _subscribed("ticker", 8, 1)],
                [_subscribed("orderbook_delta", 7, 0), _subscribed("ticker", 8, 1)],
            ),
        )
        assert stale_only.outcome is SmokeOutcome.INCONCLUSIVE
        assert (stale_only.book_rows, stale_only.market_state_rows) == (0, 0)

        current, _ = await _direct_smoke(monkeypatch, db, _session_frames())
        assert current.outcome is SmokeOutcome.SUCCESS
        assert (current.book_rows, current.market_state_rows) == (3, 1)

    asyncio.run(run())


def test_run_live_smoke_replay_in_same_epochs_adds_no_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        db = tmp_path / "evidence.sqlite3"
        epochs = (UUID(int=11), UUID(int=12))
        first, _ = await _direct_smoke(monkeypatch, db, _session_frames(), epochs=epochs)
        assert first.outcome is SmokeOutcome.SUCCESS
        replay, _ = await _direct_smoke(monkeypatch, db, _session_frames(), epochs=epochs)
        assert replay.outcome is SmokeOutcome.INCONCLUSIVE
        assert (replay.book_rows, replay.market_state_rows) == (0, 0)

    asyncio.run(run())


@pytest.mark.parametrize(
    ("sessions", "expected_book", "expected_state"),
    [
        (_session_frames(ticker_event=False), 3, 0),
        (_session_frames(snapshot=False), 0, 1),
        (_session_frames(delta=False), 2, 1),
    ],
    ids=["no-new-market-state", "no-new-book-evidence", "no-delta"],
)
def test_run_live_smoke_missing_current_evidence_is_inconclusive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sessions: tuple[list[dict[str, object]], list[dict[str, object]]],
    expected_book: int,
    expected_state: int,
) -> None:
    async def run() -> None:
        db = tmp_path / "e.db"
        preseeded, _ = await _direct_smoke(monkeypatch, db, _session_frames())
        assert preseeded.outcome is SmokeOutcome.SUCCESS
        summary, _ = await _direct_smoke(monkeypatch, db, sessions)
        assert summary.outcome is SmokeOutcome.INCONCLUSIVE
        assert (summary.book_rows, summary.market_state_rows) == (
            expected_book,
            expected_state,
        )

    asyncio.run(run())


def test_run_live_smoke_false_entitlement_is_no_go_without_websocket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary, connections = asyncio.run(
        _direct_smoke(monkeypatch, tmp_path / "e.db", _session_frames(), enabled=False)
    )
    assert summary.outcome is SmokeOutcome.NO_GO
    assert connections == 0
    assert (summary.book_rows, summary.market_state_rows) == (0, 0)


def test_static_boundary_has_no_write_or_private_capability() -> None:
    modules = [
        "live_boundary.py",
        "live_transport.py",
        "live_smoke.py",
    ]
    root = Path(inspect.getfile(LiveSmokeConfig)).parent
    source = "\n".join((root / name).read_text() for name in modules)
    forbidden = (
        "production_execution",
        "demo_execution",
        "risk_engine",
        "supervised_canary",
        "bounded_autonomy",
        "services.learning",
        "create_order",
        "cancel_order",
        "amend_order",
        "transfer",
        "signer_service",
        "ProductionWriteCredential",
        "user_orders",
        "order_group_updates",
        "get_snapshot",
        "use_yes_price",
    )
    assert not [item for item in forbidden if item in source]
    assert set(MarginChannel) == {MarginChannel.ORDERBOOK, MarginChannel.TICKER}
