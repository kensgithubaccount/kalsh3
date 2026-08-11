"""Current externally-verified Kalshi WebSocket command and response protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

from services.kalshi_account_gateway.auth import RequestSigner

WS_PATH = "/trade-api/ws/v2"
PRODUCTION_WS = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"


class ProtocolError(ValueError):
    pass


class Channel(StrEnum):
    ORDERBOOK = "orderbook_delta"
    TICKER = "ticker"
    TRADE = "trade"
    LIFECYCLE = "market_lifecycle_v2"
    MVE_LIFECYCLE = "multivariate_market_lifecycle"


class CommandKind(StrEnum):
    SUBSCRIBE = "subscribe"
    UNSUBSCRIBE = "unsubscribe"
    LIST = "list_subscriptions"
    UPDATE = "update_subscription"


@dataclass(frozen=True, slots=True)
class PendingCommand:
    command_id: int
    kind: CommandKind
    channel: Channel | None
    market_tickers: tuple[str, ...]
    action: str | None


@dataclass(frozen=True, slots=True)
class Subscription:
    epoch: UUID
    sid: int
    channel: Channel
    command_id: int
    market_tickers: tuple[str, ...]
    use_yes_price: bool | None


@dataclass(slots=True)
class ProtocolState:
    epoch: UUID
    next_id: int = 1
    pending: dict[int, PendingCommand] = field(default_factory=dict)
    subscriptions: dict[int, Subscription] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def _command(
        self,
        kind: CommandKind,
        params: dict[str, Any] | None,
        channel: Channel | None,
        tickers: tuple[str, ...],
        action: str | None,
    ) -> dict[str, Any]:
        command_id = self.next_id
        self.next_id += 1
        self.pending[command_id] = PendingCommand(command_id, kind, channel, tickers, action)
        body: dict[str, Any] = {"id": command_id, "cmd": kind.value}
        if params is not None:
            body["params"] = params
        return body

    def subscribe(self, channel: Channel, tickers: tuple[str, ...] = ()) -> dict[str, Any]:
        params: dict[str, Any] = {"channels": [channel.value]}
        if channel == Channel.ORDERBOOK:
            if not tickers:
                raise ProtocolError("orderbook subscription requires market tickers")
            params.update({"market_tickers": list(tickers), "use_yes_price": True})
        elif tickers:
            params["market_tickers"] = list(tickers)
        return self._command(CommandKind.SUBSCRIBE, params, channel, tickers, None)

    def unsubscribe(self, sid: int) -> dict[str, Any]:
        return self._command(CommandKind.UNSUBSCRIBE, {"sids": [sid]}, None, (), None)

    def list_subscriptions(self) -> dict[str, Any]:
        return self._command(CommandKind.LIST, None, None, (), None)

    def update(self, sid: int, action: str, tickers: tuple[str, ...]) -> dict[str, Any]:
        if action not in {"add_markets", "delete_markets", "get_snapshot"}:
            raise ProtocolError("unsupported subscription update")
        if not tickers:
            raise ProtocolError("update requires market tickers")
        return self._command(
            CommandKind.UPDATE,
            {"sid": sid, "action": action, "market_tickers": list(tickers)},
            None,
            tickers,
            action,
        )

    def response(self, raw: Any) -> Subscription | None:
        if not isinstance(raw, dict) or not isinstance(raw.get("type"), str):
            raise ProtocolError("malformed command response")
        kind = raw["type"]
        command_id = raw.get("id")
        if not isinstance(command_id, int) or command_id not in self.pending:
            raise ProtocolError("unknown command id")
        pending = self.pending.pop(command_id)
        if kind == "error":
            message = raw.get("msg", {}).get("msg") if isinstance(raw.get("msg"), dict) else None
            self.errors.append(str(message or "protocol error"))
            return None
        if kind == "subscribed":
            sid = raw.get("sid")
            if not isinstance(sid, int) or pending.channel is None:
                raise ProtocolError("malformed subscribed response")
            subscription = Subscription(
                self.epoch,
                sid,
                pending.channel,
                command_id,
                pending.market_tickers,
                True if pending.channel == Channel.ORDERBOOK else None,
            )
            self.subscriptions[sid] = subscription
            return subscription
        if kind == "ok":
            return None
        raise ProtocolError("unexpected command response type")


def websocket_headers(signer: RequestSigner, timestamp_ms: int) -> dict[str, str]:
    return signer.headers(timestamp_ms, "GET", WS_PATH)
