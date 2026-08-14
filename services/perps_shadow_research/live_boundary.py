"""Closed M25B2 network/auth boundary for manual read-only Perps evidence."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import quote

from services.kalshi_account_gateway.auth import RequestSigner

from .domain import ShadowResearchError
from .perps_metadata import PerpsMarketMetadata, parse_perps_market

MAX_HTTP_RESPONSE_BYTES = 1_000_000
MARGIN_ENABLED_PATH = "/trade-api/v2/margin/enabled"


class MarginEnvironment(StrEnum):
    DEMO = "demo"
    PRODUCTION = "production"


@dataclass(frozen=True, slots=True)
class MarginEnvironmentConfig:
    environment: MarginEnvironment
    rest_origin: str
    websocket_url: str
    signed_websocket_path: str = "/trade-api/ws/v2/margin"


ENVIRONMENTS: Mapping[MarginEnvironment, MarginEnvironmentConfig] = {
    MarginEnvironment.DEMO: MarginEnvironmentConfig(
        MarginEnvironment.DEMO,
        "https://external-api.demo.kalshi.co",
        "wss://external-api-margin-ws.demo.kalshi.co/trade-api/ws/v2/margin",
    ),
    MarginEnvironment.PRODUCTION: MarginEnvironmentConfig(
        MarginEnvironment.PRODUCTION,
        "https://external-api.kalshi.com",
        "wss://external-api-margin-ws.kalshi.com/trade-api/ws/v2/margin",
    ),
}


@dataclass(frozen=True, slots=True, repr=False)
class ExactReadCredential:
    environment: MarginEnvironment
    key_id: str = field(repr=False)
    private_key_pem: bytes = field(repr=False)
    scopes: frozenset[str] = field(default=frozenset({"read"}), repr=False)

    def __post_init__(self) -> None:
        if self.scopes != frozenset({"read"}) or not self.key_id or not self.private_key_pem:
            raise ShadowResearchError("an exact-read credential is required")


class ExactReadCredentialProvider(Protocol):
    def resolve(self, environment: MarginEnvironment) -> ExactReadCredential: ...


@dataclass(frozen=True, slots=True)
class HttpReply:
    status: int
    body: bytes
    content_type: str = "application/json"
    location: str | None = None


class MarginHttpTransport(Protocol):
    def get(
        self, origin: str, path: str, headers: Mapping[str, str], *, timeout_seconds: float
    ) -> HttpReply: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        return None


class UrllibMarginHttpTransport:
    """HTTPS-only transport; callers can represent only GETs on a closed origin."""

    def get(
        self, origin: str, path: str, headers: Mapping[str, str], *, timeout_seconds: float
    ) -> HttpReply:
        if origin not in {item.rest_origin for item in ENVIRONMENTS.values()}:
            raise ShadowResearchError("unapproved margin REST origin")
        request = urllib.request.Request(  # noqa: S310 - origin is from closed enum
            origin + path, headers=dict(headers), method="GET"
        )
        try:
            with urllib.request.build_opener(_NoRedirect()).open(
                request, timeout=timeout_seconds
            ) as response:
                declared = response.headers.get("Content-Length")
                if declared is not None and int(declared) > MAX_HTTP_RESPONSE_BYTES:
                    raise ShadowResearchError("margin response exceeds size limit")
                body = response.read(MAX_HTTP_RESPONSE_BYTES + 1)
                reply = HttpReply(
                    response.status,
                    body,
                    response.headers.get_content_type(),
                    response.headers.get("Location"),
                )
        except urllib.error.HTTPError as exc:
            return HttpReply(exc.code, b"", location=exc.headers.get("Location"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TimeoutError("margin REST network failure") from exc
        if len(reply.body) > MAX_HTTP_RESPONSE_BYTES:
            raise ShadowResearchError("margin response exceeds size limit")
        return reply


class MarginAuthenticationFailure(ShadowResearchError):
    pass


class MarginNotEntitled(ShadowResearchError):
    pass


class MarginUpstreamFailure(ShadowResearchError):
    pass


def _json_object(reply: HttpReply) -> dict[str, Any]:
    if reply.location is not None or 300 <= reply.status < 400:
        raise MarginUpstreamFailure("margin redirect rejected")
    if reply.content_type != "application/json":
        raise MarginUpstreamFailure("margin response is not JSON")
    if len(reply.body) > MAX_HTTP_RESPONSE_BYTES:
        raise MarginUpstreamFailure("margin response exceeds size limit")
    try:
        value = json.loads(
            reply.body,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise MarginUpstreamFailure("malformed margin JSON response") from exc
    if not isinstance(value, dict):
        raise MarginUpstreamFailure("margin response must be an object")
    return value


class PerpsMarketClient:
    def __init__(
        self,
        environment: MarginEnvironment,
        transport: MarginHttpTransport,
        *,
        timeout_seconds: float = 10,
        max_retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if timeout_seconds <= 0 or not 0 <= max_retries <= 5:
            raise ValueError("unsafe margin REST bounds")
        self.config = ENVIRONMENTS[environment]
        self.transport = transport
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.sleep = sleep

    def get_market(self, ticker: str, *, observed_at: datetime) -> PerpsMarketMetadata:
        if not ticker or ticker.strip() != ticker or "/" in ticker:
            raise ShadowResearchError("an explicit canonical ticker is required")
        path = f"/trade-api/v2/margin/markets/{quote(ticker, safe='-_.')}"
        reply = self._bounded_get(path, {})
        payload = _json_object(reply)
        if set(payload) != {"market"} or not isinstance(payload["market"], dict):
            raise MarginUpstreamFailure("market response must contain exactly one market object")
        market = parse_perps_market(payload["market"], observed_at=observed_at)
        if market.ticker != ticker:
            raise ShadowResearchError("margin market ticker mismatch")
        if not market.is_open():
            raise ShadowResearchError("margin market is not currently open")
        return market

    def _bounded_get(self, path: str, headers: Mapping[str, str]) -> HttpReply:
        for attempt in range(self.max_retries + 1):
            try:
                reply = self.transport.get(
                    self.config.rest_origin, path, headers, timeout_seconds=self.timeout_seconds
                )
            except TimeoutError as exc:
                if attempt == self.max_retries:
                    raise MarginUpstreamFailure(
                        "margin REST unavailable after bounded retries"
                    ) from exc
            else:
                if reply.location is not None or 300 <= reply.status < 400:
                    raise MarginUpstreamFailure("margin redirect rejected")
                if reply.status == 200:
                    return reply
                if reply.status in {401, 403}:
                    raise MarginAuthenticationFailure("margin authentication failed")
                if reply.status == 404:
                    raise MarginUpstreamFailure("margin resource not found")
                if reply.status != 429 and reply.status < 500:
                    raise MarginUpstreamFailure("unexpected margin REST response")
                if attempt == self.max_retries:
                    raise MarginUpstreamFailure("margin REST unavailable after bounded retries")
            self.sleep(float(Decimal(attempt + 1) / Decimal(4)))
        raise AssertionError("unreachable")


class MarginEnabledClient(PerpsMarketClient):
    def __init__(
        self,
        environment: MarginEnvironment,
        signer: RequestSigner,
        transport: MarginHttpTransport,
        **kwargs: Any,
    ) -> None:
        super().__init__(environment, transport, **kwargs)
        self.signer = signer

    def enabled(self, *, timestamp_ms: int) -> bool:
        headers = self.signer.headers(timestamp_ms, "GET", MARGIN_ENABLED_PATH)
        payload = _json_object(self._bounded_get(MARGIN_ENABLED_PATH, headers))
        if set(payload) != {"enabled"} or type(payload["enabled"]) is not bool:
            raise MarginUpstreamFailure("malformed margin enabled response")
        return bool(payload["enabled"])


def resolve_signer(
    provider: ExactReadCredentialProvider, environment: MarginEnvironment
) -> RequestSigner:
    credential = provider.resolve(environment)
    if credential.environment is not environment:
        raise MarginAuthenticationFailure("credential environment mismatch")
    return RequestSigner(credential.key_id, credential.private_key_pem)
