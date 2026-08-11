"""Deterministic paper/mock mutation and private-stream fault injection."""

from dataclasses import dataclass, field
from enum import StrEnum

from .adapter import MutationRequest, TransportResponse
from .domain import DemoWriteCredential


class Fault(StrEnum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    TIMEOUT_BEFORE_SEND = "TIMEOUT_BEFORE_SEND"
    TIMEOUT_AFTER_SEND = "TIMEOUT_AFTER_SEND"
    PARTIAL_FILL = "PARTIAL_FILL"
    FULL_FILL = "FULL_FILL"
    DELAYED_FILL = "DELAYED_FILL"
    CANCEL_SUCCESS = "CANCEL_SUCCESS"
    CANCEL_FILL_RACE = "CANCEL_FILL_RACE"
    AMEND_FILL = "AMEND_FILL"
    DECREASE = "DECREASE"
    DUPLICATE_MESSAGE = "DUPLICATE_MESSAGE"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    WS_GAP = "WS_GAP"
    DISCONNECT = "DISCONNECT"
    RATE_LIMIT = "RATE_LIMIT"
    SERVER_ERROR = "SERVER_ERROR"
    MALFORMED = "MALFORMED"


class AmbiguousMutation(RuntimeError):
    def __init__(self, message: str, *, may_have_been_sent: bool) -> None:
        super().__init__(message)
        self.may_have_been_sent = may_have_been_sent


@dataclass(slots=True)
class FaultExchange:
    script: list[Fault]
    sent: list[MutationRequest] = field(default_factory=list)

    def __call__(
        self, origin: str, request: MutationRequest, credential: DemoWriteCredential
    ) -> TransportResponse:
        del origin, credential
        fault = self.script.pop(0)
        if fault == Fault.TIMEOUT_BEFORE_SEND:
            raise AmbiguousMutation("request proven unsent", may_have_been_sent=False)
        self.sent.append(request)
        if fault == Fault.TIMEOUT_AFTER_SEND:
            raise AmbiguousMutation("response lost after possible send", may_have_been_sent=True)
        if fault == Fault.RATE_LIMIT:
            return TransportResponse(429, {})
        if fault == Fault.SERVER_ERROR:
            return TransportResponse(500, {})
        if fault == Fault.MALFORMED:
            return TransportResponse(200, {"unexpected": object()})
        if fault == Fault.REJECT:
            return TransportResponse(400, {"error": "fixture rejection"})
        return TransportResponse(201, {"order_id": "demo-order-1", "fault": fault})
