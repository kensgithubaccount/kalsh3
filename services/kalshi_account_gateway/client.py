"""Bounded, mutation-free Kalshi production account client."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from .auth import RequestSigner
from .models import AccountSnapshot

BASE_URL = "https://external-api.kalshi.com"


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    payload: dict[str, Any]


class ReadTransport(Protocol):
    def get(
        self, path: str, headers: Mapping[str, str], *, timeout_seconds: float
    ) -> HttpResponse: ...


class UrllibReadTransport:
    """Production HTTPS transport with a fixed origin and no mutation interface."""

    def get(self, path: str, headers: Mapping[str, str], *, timeout_seconds: float) -> HttpResponse:
        request = urllib.request.Request(  # noqa: S310 - fixed HTTPS production origin
            BASE_URL + path, headers=dict(headers), method="GET"
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
                raw, status = response.read(2_000_000), response.status
        except urllib.error.HTTPError as exc:
            return HttpResponse(exc.code, {})
        except (urllib.error.URLError, TimeoutError) as exc:
            raise TimeoutError("account API request timed out or failed") from exc
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AccountGatewayError("upstream returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise AccountGatewayError("upstream response body is not an object")
        return HttpResponse(status, payload)


class AccountGatewayError(RuntimeError):
    """Base error: callers must retain the prior snapshot and mark it stale."""


class AuthenticationRejected(AccountGatewayError):
    pass


class RateLimited(AccountGatewayError):
    pass


class UpstreamUnavailable(AccountGatewayError):
    pass


class PaginationError(AccountGatewayError):
    pass


@dataclass(frozen=True, slots=True)
class ReadBudget:
    requests: int
    retries: int


COLLECTIONS = {
    "positions": ("/trade-api/v2/portfolio/positions", "market_positions"),
    "orders": ("/trade-api/v2/portfolio/orders", "orders"),
    "fills": ("/trade-api/v2/portfolio/fills", "fills"),
    "settlements": ("/trade-api/v2/portfolio/settlements", "settlements"),
}


class KalshiAccountClient:
    """Read account 0 only; there is intentionally no generic request/mutation method."""

    def __init__(
        self,
        signer: RequestSigner,
        transport: ReadTransport,
        *,
        clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
        sleep: Callable[[float], None] = time.sleep,
        timeout_seconds: float = 10,
        max_retries: int = 2,
    ) -> None:
        if timeout_seconds <= 0 or max_retries < 0 or max_retries > 5:
            raise ValueError("unsafe timeout or retry setting")
        self._signer = signer
        self._transport = transport
        self._clock_ms = clock_ms
        self._sleep = sleep
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._requests = 0
        self._retries = 0

    @property
    def read_budget(self) -> ReadBudget:
        return ReadBudget(self._requests, self._retries)

    def _get(self, path: str) -> dict[str, Any]:
        for attempt in range(self._max_retries + 1):
            headers = self._signer.headers(self._clock_ms(), "GET", path)
            try:
                response = self._transport.get(path, headers, timeout_seconds=self._timeout)
            except TimeoutError as exc:
                response = HttpResponse(599, {})
                last_error: Exception = exc
            else:
                last_error = UpstreamUnavailable("upstream request failed")
            self._requests += 1
            if response.status == 200:
                if not isinstance(response.payload, dict):
                    raise AccountGatewayError("response body is not an object")
                return response.payload
            if response.status == 401:
                raise AuthenticationRejected("read credential was rejected")
            retryable = response.status == 429 or response.status >= 500
            if not retryable:
                raise AccountGatewayError(f"unexpected upstream status {response.status}")
            if attempt < self._max_retries:
                self._retries += 1
                self._sleep(DecimalBackoff.seconds(attempt))
                continue
            if response.status == 429:
                raise RateLimited("read API rate limit exhausted")
            raise UpstreamUnavailable("read API unavailable after bounded retries") from last_error
        raise AssertionError("unreachable")

    def _all_pages(self, path: str, field: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            separator = "&" if "?" in path else "?"
            target = f"{path}{separator}subaccount=0"
            if cursor is not None:
                target += f"&cursor={cursor}"
            payload = self._get(target)
            page = payload.get(field)
            if not isinstance(page, list) or any(not isinstance(item, dict) for item in page):
                raise PaginationError(f"malformed {field} page")
            items.extend(page)
            next_cursor = payload.get("cursor")
            if next_cursor in (None, ""):
                return items
            if not isinstance(next_cursor, str) or next_cursor in seen:
                raise PaginationError(f"invalid or repeated {field} cursor")
            seen.add(next_cursor)
            cursor = next_cursor

    def verify_exact_read_scope(self, expected_key_id: str) -> None:
        payload = self._get("/trade-api/v2/api_keys")
        keys = payload.get("api_keys")
        if not isinstance(keys, list):
            raise AuthenticationRejected("API key scope response is malformed")
        matches = [
            item for item in keys if isinstance(item, dict) and item.get("id") == expected_key_id
        ]
        if len(matches) != 1 or matches[0].get("scopes") != ["read"]:
            raise AuthenticationRejected(
                "credential is not positively verified as exactly read-only"
            )

    def refresh(self, expected_key_id: str) -> AccountSnapshot:
        """Fetch a complete snapshot; page failures never return partial data."""
        self.verify_exact_read_scope(expected_key_id)
        balance = self._get("/trade-api/v2/portfolio/balance?subaccount=0")
        limits = self._get("/trade-api/v2/account/limits")
        payloads: dict[str, Any] = {"balance": balance, "limits": limits}
        for name, (path, field) in COLLECTIONS.items():
            payloads[name] = self._all_pages(path, field)
        return AccountSnapshot.from_payloads(payloads, datetime.now(UTC), self.read_budget)


class DecimalBackoff:
    """Small deterministic retry schedule; isolated for tests."""

    @staticmethod
    def seconds(attempt: int) -> float:
        return (attempt + 1) * 0.25
