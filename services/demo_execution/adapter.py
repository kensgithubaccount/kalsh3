"""Current V2 fixed-point request construction and demo-only transport isolation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from urllib.parse import urlsplit

from .domain import DEMO_REST_ORIGIN, DemoWriteCredential, ExecutionEnvironment, ExecutionIntent


class MutationKind(StrEnum):
    CREATE = "CREATE"
    CANCEL = "CANCEL"
    AMEND = "AMEND"
    DECREASE = "DECREASE"


@dataclass(frozen=True, slots=True)
class MutationRequest:
    method: str
    path: str
    body: dict[str, object]
    subaccount: int = 0


@dataclass(frozen=True, slots=True)
class TransportResponse:
    status: int
    body: dict[str, object]


class Sender(Protocol):
    def __call__(
        self, origin: str, request: MutationRequest, credential: DemoWriteCredential
    ) -> TransportResponse: ...


def _fixed(value: object) -> str:
    rendered = format(value, "f")
    if "e" in rendered.lower():
        raise ValueError("fixed-point value required")
    return rendered


def create_request(intent: ExecutionIntent) -> MutationRequest:
    side = intent.book_side.lower()
    if side not in {"bid", "ask"}:
        raise ValueError("canonical book side must translate to bid or ask")
    body: dict[str, object] = {
        "ticker": intent.ticker,
        "client_order_id": intent.client_order_id,
        "side": side,
        "count": _fixed(intent.quantity),
        "price": _fixed(intent.price),
        "time_in_force": intent.time_in_force,
        "self_trade_prevention_type": intent.self_trade_prevention_type,
        "post_only": intent.post_only,
        "cancel_order_on_pause": intent.cancel_order_on_pause,
        "reduce_only": intent.reduce_only,
        "subaccount": 0,
    }
    if intent.expiration_time is not None:
        body["expiration_time"] = int(intent.expiration_time.timestamp())
    if intent.order_group_id is not None:
        body["order_group_id"] = intent.order_group_id
    if intent.exchange_index is not None:
        body["exchange_index"] = intent.exchange_index
    return MutationRequest("POST", "/portfolio/events/orders", body)


def cancel_request(order_id: str) -> MutationRequest:
    return MutationRequest("DELETE", f"/portfolio/events/orders/{order_id}", {}, 0)


def decrease_request(
    order_id: str, *, reduce_by: object | None = None, reduce_to: object | None = None
) -> MutationRequest:
    if (reduce_by is None) == (reduce_to is None):
        raise ValueError("exactly one of reduce_by or reduce_to is required")
    body: dict[str, object] = {
        "reduce_by" if reduce_by is not None else "reduce_to": _fixed(reduce_by or reduce_to)
    }
    body["subaccount"] = 0
    return MutationRequest("POST", f"/portfolio/events/orders/{order_id}/decrease", body)


def amend_request(
    order_id: str,
    *,
    authorized_intent_hash: str,
    replacement_intent: ExecutionIntent,
    new_client_order_id: str,
) -> MutationRequest:
    if replacement_intent.intent_hash != authorized_intent_hash:
        raise ValueError("INTENT_CHANGED")
    return MutationRequest(
        "POST",
        f"/portfolio/events/orders/{order_id}/amend",
        {
            "price": _fixed(replacement_intent.price),
            "count": _fixed(replacement_intent.quantity),
            "client_order_id": new_client_order_id,
            "subaccount": 0,
        },
    )


class DemoMutationTransport:
    """The only network-capable M14 boundary; destination is not caller-configurable."""

    origin = DEMO_REST_ORIGIN

    def __init__(self, credential: DemoWriteCredential, sender: Sender) -> None:
        self._credential = credential
        self._sender = sender

    @staticmethod
    def validate_origin(origin: str) -> None:
        parsed = urlsplit(origin)
        if origin != DEMO_REST_ORIGIN or parsed.hostname != "external-api.demo.kalshi.co":
            raise ValueError("M14 mutation destination is not the exact demo origin")

    def send(self, mode: ExecutionEnvironment, request: MutationRequest) -> TransportResponse:
        if mode is not ExecutionEnvironment.DEMO or request.subaccount != 0:
            raise ValueError("demo mutation requires DEMO mode and subaccount 0")
        self.validate_origin(self.origin)
        # Serialization proves the journaled request is JSON-compatible before the credential is used.
        json.dumps(request.body, sort_keys=True)
        return self._sender(self.origin, request, self._credential)
