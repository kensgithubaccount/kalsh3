from __future__ import annotations

import asyncio
import inspect
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from services.kalshi_account_gateway.auth import AuthenticationError, signature_message
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
from services.perps_shadow_research.live_smoke import LiveSmokeConfig
from services.perps_shadow_research.live_transport import (
    MAX_WEBSOCKET_MESSAGE_BYTES,
    AsyncMarginTransport,
)
from services.perps_shadow_research.margin_protocol import MarginChannel

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
