"""Concrete asynchronous read-only margin WebSocket transport."""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from importlib import import_module
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from services.kalshi_account_gateway.auth import RequestSigner
from services.real_time_market_data.transport import ReceivedFrame

from .domain import ShadowResearchError
from .live_boundary import ENVIRONMENTS, MarginEnvironment
from .margin_protocol import MarginProtocolState

MAX_WEBSOCKET_MESSAGE_BYTES = 1_000_000


class WebSocketSession(Protocol):
    async def recv(self) -> str | bytes: ...
    async def send(self, message: str) -> None: ...
    async def close(self) -> None: ...


class WebSocketConnector(Protocol):
    async def __call__(
        self,
        url: str,
        *,
        additional_headers: Mapping[str, str],
        open_timeout: float,
        close_timeout: float,
        max_size: int,
        ping_interval: float,
        ping_timeout: float,
    ) -> WebSocketSession: ...


async def websockets_connector(
    url: str,
    *,
    additional_headers: Mapping[str, str],
    open_timeout: float,
    close_timeout: float,
    max_size: int,
    ping_interval: float,
    ping_timeout: float,
) -> WebSocketSession:
    connect = cast(
        Callable[..., Awaitable[WebSocketSession]],
        import_module("websockets.asyncio.client").connect,
    )

    return await connect(
        url,
        additional_headers=additional_headers,
        open_timeout=open_timeout,
        close_timeout=close_timeout,
        max_size=max_size,
        ping_interval=ping_interval,
        ping_timeout=ping_timeout,
    )


class AsyncMarginTransport:
    """One connection epoch with protocol-state-only outbound application messages."""

    def __init__(
        self,
        environment: MarginEnvironment,
        signer: RequestSigner,
        connector: WebSocketConnector = websockets_connector,
        *,
        clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
        utc_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        open_timeout: float = 10,
        close_timeout: float = 5,
    ) -> None:
        if open_timeout <= 0 or close_timeout <= 0:
            raise ValueError("unsafe WebSocket timeout")
        self.config = ENVIRONMENTS[environment]
        self.signer = signer
        self.connector = connector
        self.clock_ms = clock_ms
        self.utc_clock = utc_clock
        self.monotonic_ns = monotonic_ns
        self.open_timeout = open_timeout
        self.close_timeout = close_timeout
        self.epoch: UUID | None = None
        self.protocol: MarginProtocolState | None = None
        self._websocket: WebSocketSession | None = None

    async def connect(self) -> UUID:
        if self._websocket is not None:
            raise ShadowResearchError("margin WebSocket is already connected")
        headers = self.signer.headers(self.clock_ms(), "GET", self.config.signed_websocket_path)
        websocket = await self.connector(
            self.config.websocket_url,
            additional_headers=headers,
            open_timeout=self.open_timeout,
            close_timeout=self.close_timeout,
            max_size=MAX_WEBSOCKET_MESSAGE_BYTES,
            ping_interval=20,
            ping_timeout=20,
        )
        epoch = uuid4()
        if epoch.int == 0:
            raise ShadowResearchError("zero connection epoch rejected")
        self._websocket = websocket
        self.epoch = epoch
        self.protocol = MarginProtocolState(epoch)
        return epoch

    async def send_protocol_command(self, command: Mapping[str, Any]) -> None:
        if self._websocket is None or self.protocol is None:
            raise ShadowResearchError("margin WebSocket is disconnected")
        if not self.protocol.validates_outbound(command):
            raise ShadowResearchError("outbound command was not created by protocol state")
        await self._websocket.send(json.dumps(dict(command), separators=(",", ":")))

    async def receive(self) -> ReceivedFrame:
        if self._websocket is None:
            raise ShadowResearchError("margin WebSocket is disconnected")
        raw = await self._websocket.recv()
        received_monotonic_ns = self.monotonic_ns()
        received_at = self.utc_clock().astimezone(UTC)
        if not isinstance(raw, (str, bytes)):
            raise ShadowResearchError("unexpected margin WebSocket frame type")
        size = len(raw.encode("utf-8")) if isinstance(raw, str) else len(raw)
        if size > MAX_WEBSOCKET_MESSAGE_BYTES:
            raise ShadowResearchError("margin WebSocket frame exceeds size limit")
        return ReceivedFrame(raw, received_at, received_monotonic_ns)

    async def close(self) -> None:
        websocket = self._websocket
        self._websocket = None
        self.epoch = None
        self.protocol = None
        if websocket is not None:
            await websocket.close()
