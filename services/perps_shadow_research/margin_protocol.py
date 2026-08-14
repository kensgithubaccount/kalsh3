"""Minimal offline state machine for safe Perps WebSocket subscriptions."""

from __future__ import annotations

import json
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
    canonical_command_json: str
    issued_command: dict[str, Any]


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

    def canonical_command(self, command_id: int) -> dict[str, Any]:
        """Return a detached copy of the exact command issued for ``command_id``."""
        pending = self.pending.get(command_id)
        if pending is None:
            raise ShadowResearchError("unknown pending margin command")
        command = json.loads(pending.canonical_command_json)
        if not isinstance(command, dict):  # pragma: no cover - construction invariant
            raise ShadowResearchError("invalid canonical margin command")
        return command

    def validates_outbound(self, command: Any) -> bool:
        """Require both protocol provenance and exact structural equality."""
        if type(command) is not dict:
            return False
        command_id = command.get("id")
        if type(command_id) is not int:
            return False
        pending = self.pending.get(command_id)
        return (
            pending is not None
            and command is pending.issued_command
            and _exact_value(command, self.canonical_command(command_id))
        )

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

    def command_acknowledged(self, raw: Any) -> None:
        """Consume an exact unsubscribe or list response identified by command id."""
        if not isinstance(raw, dict):
            raise ShadowResearchError("malformed margin command acknowledgement")
        command_id = raw.get("id")
        if type(command_id) is not int or command_id < 0:
            raise ShadowResearchError("command id must be exact non-negative integer")
        pending = self.pending.get(command_id)
        if pending is None:
            raise ShadowResearchError("unknown pending margin command")
        if pending.command is MarginCommand.UNSUBSCRIBE:
            sid = raw.get("sid")
            sequence = raw.get("seq")
            if (
                raw.get("type") != "unsubscribed"
                or type(sid) is not int
                or type(sequence) is not int
                or sid < 1
                or sequence < 1
                or self.canonical_command(command_id).get("params") != {"sids": [sid]}
            ):
                raise ShadowResearchError("unsubscribe acknowledgement does not match command")
            self.subscriptions.pop(sid, None)
        elif pending.command is MarginCommand.LIST:
            message = raw.get("msg")
            if raw.get("type") != "ok" or not isinstance(message, list):
                raise ShadowResearchError("list acknowledgement does not match command")
            for item in message:
                if not isinstance(item, dict) or set(item) != {"channel", "sid"}:
                    raise ShadowResearchError("malformed listed margin subscription")
                try:
                    MarginChannel(item["channel"])
                except (ValueError, TypeError) as exc:
                    raise ShadowResearchError("unapproved listed margin channel") from exc
                self._sid(item["sid"])
        else:
            raise ShadowResearchError("acknowledgement does not match pending command")
        del self.pending[command_id]

    def _command(
        self,
        command: MarginCommand,
        channel: MarginChannel | None,
        tickers: tuple[str, ...],
        params: dict[str, Any] | None,
    ) -> dict[str, Any]:
        command_id = self.next_id
        self.next_id += 1
        result: dict[str, Any] = {"id": command_id, "cmd": command.value}
        if params is not None:
            result["params"] = _copy_command(params)
        self.pending[command_id] = _Pending(
            command_id,
            command,
            channel,
            tickers,
            json.dumps(result, sort_keys=True, separators=(",", ":")),
            result,
        )
        return result

    @staticmethod
    def _sid(value: Any) -> None:
        if type(value) is not int or value < 1:
            raise ShadowResearchError("SID must be an exact integer >= 1")


def _copy_command(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: [_copy_value(item) for item in item_value]
        if isinstance(item_value, list)
        else _copy_value(item_value)
        for key, item_value in value.items()
    }


def _copy_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _copy_command(value)
    if isinstance(value, list):
        return [_copy_value(item) for item in value]
    return value


def _exact_value(actual: Any, expected: Any) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            _exact_value(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _exact_value(actual_item, expected_item)
            for actual_item, expected_item in zip(actual, expected, strict=True)
        )
    return bool(actual == expected)
