"""Fixed, non-redirecting Kalshi production transport.

This module is deliberately not a general HTTP client.  It is only a byte-preserving
transport for already validated :class:`ProductionRequestEnvelope` instances.  The
public M15 boundary remains DISARMED until a separately reviewed activation milestone.
"""

from __future__ import annotations

import http.client
import ssl
from dataclasses import dataclass

from .domain import PRODUCTION_ORIGIN, Operation, validate_operation

PRODUCTION_HOST = "external-api.kalshi.com"
MAX_RESPONSE_BYTES = 1_000_000
TIMEOUT_SECONDS = 3


class ProductionTransportError(RuntimeError):
    """A transport failure; callers must reconcile rather than retry a mutation."""


@dataclass(frozen=True, slots=True)
class TransportReply:
    status: int
    body: bytes


class FixedKalshiProductionTransport:
    """Exact-host HTTPS transport with no proxy, redirect, or destination override."""

    origin = PRODUCTION_ORIGIN
    host = PRODUCTION_HOST
    timeout_seconds = TIMEOUT_SECONDS

    def send_exact(
        self,
        *,
        origin: str,
        method: str,
        path: str,
        body: bytes,
        headers: dict[str, str],
        verify_tls: bool,
        follow_redirects: bool,
        timeout_seconds: int,
        maximum_response_bytes: int,
    ) -> tuple[int, bytes]:
        if origin != self.origin or not verify_tls or follow_redirects:
            raise ProductionTransportError("production transport safety flags rejected")
        if timeout_seconds != self.timeout_seconds or maximum_response_bytes != MAX_RESPONSE_BYTES:
            raise ProductionTransportError("production transport bounds rejected")
        if not isinstance(body, bytes) or not isinstance(headers, dict):
            raise ProductionTransportError("exact request bytes and headers required")
        operation = _operation_for(method, path)
        validate_operation(operation, method, path)
        context = ssl.create_default_context()
        connection = http.client.HTTPSConnection(
            self.host, timeout=self.timeout_seconds, context=context
        )
        try:
            # HTTPSConnection does not consult HTTP(S)_PROXY and no caller-controlled
            # authority is accepted.  A redirect is a response, never a second request.
            connection.request(method, path, body=body, headers=dict(headers))
            response = connection.getresponse()
            if response.getheader("Content-Length") is not None:
                declared = int(response.getheader("Content-Length", "0"))
                if declared > maximum_response_bytes:
                    raise ProductionTransportError("production response too large")
            data = response.read(maximum_response_bytes + 1)
            if len(data) > maximum_response_bytes:
                raise ProductionTransportError("production response too large")
            if 300 <= response.status < 400:
                raise ProductionTransportError("production redirect rejected")
            return response.status, data
        except (TimeoutError, OSError, ValueError, http.client.HTTPException) as exc:
            raise ProductionTransportError("production transport failed") from exc
        finally:
            connection.close()


def _operation_for(method: str, path: str) -> Operation:
    root = "/trade-api/v2/portfolio/events/orders"
    if method == "POST" and path == root:
        return Operation.CREATE
    if method == "DELETE" and path.startswith(root + "/") and path != root + "/batched":
        return Operation.CANCEL
    if method == "POST" and path.endswith("/amend"):
        return Operation.AMEND
    if method == "POST" and path.endswith("/decrease"):
        return Operation.DECREASE
    raise ProductionTransportError("method/path outside typed production authority")
