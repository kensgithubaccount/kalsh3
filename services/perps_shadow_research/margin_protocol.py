"""Minimal offline state machine for safe Perps WebSocket subscriptions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

from .domain import ShadowResearchError


class MarginChannel(StrEnum):
    ORDERBOOK = "orderbook_delta"
    TICKER = "ticker"


class MarginCommand(StrEnum):
    SUBSCRIBE = "subscribe"
    UNSUBSCRIBE = "unsubscribe"
    LIST = "list_subscriptions"


@dataclass(frozen=True, slots=True)
class MarginSubscription:
    epoch: UUID
    sid: int
    channel: MarginChannel
    tickers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Pending:
    command_id: int
    command: MarginCommand
    channel: MarginChannel | None
    tickers: tuple[str, ...]


@dataclass(slots=True)
class MarginProtocolState:
    epoch: UUID
    next_id: int = 0
    pending: dict[int, _Pending] = field(default_factory=dict)
    subscriptions: dict[int, MarginSubscription] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.epoch, UUID) or self.epoch.int == 0:
            raise ShadowResearchError("non-zero protocol epoch required")

    def subscribe(self, channel: MarginChannel, tickers: tuple[str, ...]) -> dict[str, Any]:
        if not isinstance(channel, MarginChannel):
            raise ShadowResearchError("unsupported margin channel")
        if (
            not tickers
            or any(not ticker for ticker in tickers)
            or len(set(tickers)) != len(tickers)
        ):
            raise ShadowResearchError("subscription requires unique market tickers")
        return self._command(
            MarginCommand.SUBSCRIBE,
            channel,
            tuple(tickers),
            {"channels": [channel.value], "market_tickers": list(tickers)},
        )

    def unsubscribe(self, sid: int) -> dict[str, Any]:
        self._sid(sid)
        return self._command(MarginCommand.UNSUBSCRIBE, None, (), {"sids": [sid]})

    def list_subscriptions(self) -> dict[str, Any]:
        return self._command(MarginCommand.LIST, None, (), None)

    def subscribed(self, raw: Any) -> MarginSubscription:
        if (
            not isinstance(raw, dict)
            or raw.get("type") != "subscribed"
            or not isinstance(raw.get("msg"), dict)
        ):
            raise ShadowResearchError("malformed subscribed acknowledgement")
        msg = raw["msg"]
        try:
            channel = MarginChannel(msg.get("channel"))
        except ValueError as exc:
            raise ShadowResearchError("unapproved subscribed channel") from exc
        sid = msg.get("sid")
        self._sid(sid)
        command_id = raw.get("id")
        if command_id is not None:
            if type(command_id) is not int or command_id < 0:
                raise ShadowResearchError("command id must be exact non-negative integer")
            pending = self.pending.get(command_id)
            if (
                pending is None
                or pending.command is not MarginCommand.SUBSCRIBE
                or pending.channel is not channel
            ):
                raise ShadowResearchError(
                    "subscribed acknowledgement does not match pending command"
                )
        else:
            matches = [
                item
                for item in self.pending.values()
                if item.command is MarginCommand.SUBSCRIBE and item.channel is channel
            ]
            if len(matches) != 1:
                raise ShadowResearchError("subscribed acknowledgement is ambiguous")
            pending = matches[0]
            command_id = pending.command_id
        if sid in self.subscriptions:
            raise ShadowResearchError("subscription SID collision")
        del self.pending[command_id]
        subscription = MarginSubscription(self.epoch, sid, channel, pending.tickers)
        self.subscriptions[sid] = subscription
        return subscription

    def _command(
        self,
        command: MarginCommand,
        channel: MarginChannel | None,
        tickers: tuple[str, ...],
        params: dict[str, Any] | None,
    ) -> dict[str, Any]:
        command_id = self.next_id
        self.next_id += 1
        self.pending[command_id] = _Pending(command_id, command, channel, tickers)
        result: dict[str, Any] = {"id": command_id, "cmd": command.value}
        if params is not None:
            result["params"] = params
        return result

    @staticmethod
    def _sid(value: Any) -> None:
        if type(value) is not int or value < 1:
            raise ShadowResearchError("SID must be an exact integer >= 1")
