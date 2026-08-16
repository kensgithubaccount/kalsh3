"""Purpose-built production exact-read client for batch market orderbooks."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlencode

from .auth import RequestSigner
from .production_read_credentials import PRODUCTION_ORIGIN
from .read_credentials import ExactReadCredential, ReadEnvironment

ORDERBOOKS_PATH = "/trade-api/v2/markets/orderbooks"
MAX_RESPONSE_BYTES = 4_000_000


class OrderbookReadError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OrderbookReply:
    status: int
    body: bytes
    content_type: str = "application/json"
    location: str | None = None


class OrderbookTransport(Protocol):
    def get(
        self, origin: str, path: str, headers: Mapping[str, str], *, timeout_seconds: float
    ) -> OrderbookReply: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        return None


class UrllibOrderbookTransport:
    def get(
        self, origin: str, path: str, headers: Mapping[str, str], *, timeout_seconds: float
    ) -> OrderbookReply:
        if origin != PRODUCTION_ORIGIN or not path.startswith(ORDERBOOKS_PATH + "?tickers="):
            raise OrderbookReadError("orderbook target rejected")
        request = urllib.request.Request(  # noqa: S310 - fixed production HTTPS origin
            origin + path, headers=dict(headers), method="GET"
        )
        try:
            with urllib.request.build_opener(_NoRedirect()).open(
                request, timeout=timeout_seconds
            ) as response:
                declared = response.headers.get("Content-Length")
                if declared is not None and int(declared) > MAX_RESPONSE_BYTES:
                    raise OrderbookReadError("orderbook response too large")
                body = response.read(MAX_RESPONSE_BYTES + 1)
                reply = OrderbookReply(
                    response.status,
                    body,
                    response.headers.get_content_type(),
                    response.headers.get("Location"),
                )
        except urllib.error.HTTPError as exc:
            return OrderbookReply(exc.code, b"", location=exc.headers.get("Location"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OrderbookReadError("orderbook transport failed") from exc
        if len(reply.body) > MAX_RESPONSE_BYTES:
            raise OrderbookReadError("orderbook response too large")
        return reply


class ExactOrderbookClient:
    def __init__(
        self,
        credential: ExactReadCredential,
        transport: OrderbookTransport,
        *,
        clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
        sleep: Callable[[float], None] = time.sleep,
        timeout_seconds: float = 10,
        max_retries: int = 2,
        signer_factory: Callable[[str, bytes], RequestSigner] = RequestSigner,
    ) -> None:
        if (
            credential.environment is not ReadEnvironment.PRODUCTION
            or credential.scopes != frozenset({"read"})
        ):
            raise OrderbookReadError("verified production exact-read credential required")
        if timeout_seconds <= 0 or not 0 <= max_retries <= 5:
            raise OrderbookReadError("unsafe transport bounds")
        self._signer = signer_factory(credential.key_id, credential.private_key_pem)
        self._transport, self._clock_ms, self._sleep = transport, clock_ms, sleep
        self._timeout, self._max_retries = timeout_seconds, max_retries

    def fetch(self, tickers: list[str]) -> dict[str, dict[str, Any]]:
        canonical = _canonical_tickers(tickers)
        path = ORDERBOOKS_PATH + "?" + urlencode([("tickers", ticker) for ticker in canonical])
        for attempt in range(self._max_retries + 1):
            headers = self._signer.headers(self._clock_ms(), "GET", path)
            try:
                reply = self._transport.get(
                    PRODUCTION_ORIGIN, path, headers, timeout_seconds=self._timeout
                )
            except (OrderbookReadError, TimeoutError, OSError) as exc:
                if attempt == self._max_retries:
                    raise OrderbookReadError("orderbook transport retries exhausted") from exc
                self._sleep((attempt + 1) * 0.25)
                continue
            if reply.location is not None or 300 <= reply.status < 400:
                raise OrderbookReadError("orderbook redirect rejected")
            if reply.status == 200:
                return _parse_response(reply, set(canonical))
            if reply.status not in {429, 500, 502, 503, 504} or attempt == self._max_retries:
                raise OrderbookReadError("orderbook request failed")
            self._sleep((attempt + 1) * 0.25)
        raise AssertionError("unreachable")


def _canonical_tickers(tickers: list[str]) -> tuple[str, ...]:
    if not 1 <= len(tickers) <= 100:
        raise OrderbookReadError("one to 100 unique tickers required")
    for ticker in tickers:
        if (
            not isinstance(ticker, str)
            or not ticker
            or any(ord(char) < 33 or char in "/?#&=,%" for char in ticker)
        ):
            raise OrderbookReadError("invalid market ticker")
    if len(set(tickers)) != len(tickers):
        raise OrderbookReadError("one to 100 unique tickers required")
    return tuple(sorted(tickers))


def _parse_response(reply: OrderbookReply, requested: set[str]) -> dict[str, dict[str, Any]]:
    if reply.content_type != "application/json" or len(reply.body) > MAX_RESPONSE_BYTES:
        raise OrderbookReadError("orderbook response rejected")
    try:
        payload = json.loads(reply.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OrderbookReadError("malformed orderbook JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("orderbooks"), list):
        raise OrderbookReadError("malformed orderbook response")
    rows = payload["orderbooks"]
    if any(
        not isinstance(row, dict)
        or not isinstance(row.get("ticker"), str)
        or not isinstance(row.get("orderbook_fp"), dict)
        or not isinstance(row["orderbook_fp"].get("yes_dollars"), list)
        or not isinstance(row["orderbook_fp"].get("no_dollars"), list)
        for row in rows
    ):
        raise OrderbookReadError("malformed orderbook")
    tickers = [row["ticker"] for row in rows]
    if len(set(tickers)) != len(tickers) or set(tickers) != requested:
        raise OrderbookReadError("returned ticker set mismatch")
    return {row["ticker"]: row for row in rows}
