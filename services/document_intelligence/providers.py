"""SDK-free provider request/response normalization and host-constrained transports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from .models import InferenceRun, RunMode, Usage


class ProviderFailure(StrEnum):
    TIMEOUT = "TIMEOUT"
    SCHEMA_REQUEST = "SCHEMA_REQUEST"
    AUTHENTICATION = "AUTHENTICATION"
    RATE_LIMITED = "RATE_LIMITED"
    SERVER_ERROR = "SERVER_ERROR"
    REFUSAL = "REFUSAL"
    INCOMPLETE = "INCOMPLETE"
    INVALID_SCHEMA = "INVALID_SCHEMA"
    UNKNOWN = "UNKNOWN"


class ProviderTransport(Protocol):
    def post(
        self, host: str, path: str, body: dict[str, Any], timeout: float
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    model: str
    system: str
    user: str
    schema: dict[str, Any]
    max_output: int
    timeout: float


@dataclass(frozen=True, slots=True)
class ProviderAttempt:
    attempt_number: int
    succeeded: bool
    error: str | None


def validate_model_id(model: str, mode: RunMode) -> None:
    alias = model.casefold().endswith(("latest", "-latest"))
    if alias and mode != RunMode.EXPLORATORY_NONREPRODUCIBLE:
        raise ValueError("moving model aliases are exploratory-only")


def bounded_retry(
    call: Callable[[], dict[str, Any]], max_retries: int
) -> tuple[dict[str, Any], tuple[ProviderAttempt, ...]]:
    if max_retries < 0 or max_retries > 3:
        raise ValueError("retry policy must be between zero and three")
    attempts: list[ProviderAttempt] = []
    for number in range(1, max_retries + 2):
        try:
            response = call()
            attempts.append(ProviderAttempt(number, True, None))
            return response, tuple(attempts)
        except (TimeoutError, ConnectionError) as exc:
            attempts.append(ProviderAttempt(number, False, type(exc).__name__))
            if number > max_retries:
                raise
    raise RuntimeError("unreachable")


def openai_body(request: ProviderRequest) -> dict[str, Any]:
    return {
        "model": request.model,
        "input": [
            {"role": "system", "content": request.system},
            {"role": "user", "content": request.user},
        ],
        "tools": [],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "evidence_extraction",
                "strict": True,
                "schema": request.schema,
            }
        },
        "max_output_tokens": request.max_output,
    }


def anthropic_body(request: ProviderRequest) -> dict[str, Any]:
    return {
        "model": request.model,
        "system": request.system,
        "messages": [{"role": "user", "content": request.user}],
        "tools": [
            {
                "name": "submit_evidence_extraction",
                "description": "Return typed evidence only; performs no action",
                "input_schema": request.schema,
            }
        ],
        "tool_choice": {"type": "tool", "name": "submit_evidence_extraction"},
        "max_tokens": request.max_output,
    }


class ProviderAdapter:
    def __init__(
        self, provider: str, host: str, transport: ProviderTransport, clock: Callable[[], datetime]
    ) -> None:
        expected = {"openai": "api.openai.com", "anthropic": "api.anthropic.com"}
        if expected.get(provider) != host:
            raise ValueError("provider host is not allowlisted")
        self.provider, self.host, self.transport, self.clock = provider, host, transport, clock

    def run(
        self,
        request: ProviderRequest,
        *,
        prompt_version: str,
        prompt_hash: str,
        schema_version: str,
        schema_hash: str,
        bundle_hash: str,
        code_sha: str,
        mode: RunMode = RunMode.MODEL_OUTPUT_OBSERVED_LIVE,
    ) -> tuple[InferenceRun, Any]:
        validate_model_id(request.model, mode)
        started = self.clock()
        body = openai_body(request) if self.provider == "openai" else anthropic_body(request)
        response = self.transport.post(
            self.host,
            "/v1/responses" if self.provider == "openai" else "/v1/messages",
            body,
            request.timeout,
        )
        completed = self.clock()
        if self.provider == "openai":
            output = response.get("output_parsed")
            returned_model, stop = str(response.get("model", "")), response.get("status")
        else:
            blocks = response.get("content", [])
            tools = [
                item.get("input")
                for item in blocks
                if isinstance(item, dict)
                and item.get("type") == "tool_use"
                and item.get("name") == "submit_evidence_extraction"
            ]
            output = tools[0] if len(tools) == 1 else None
            returned_model, stop = str(response.get("model", "")), response.get("stop_reason")
        usage_raw = response.get("usage", {})
        usage = Usage(
            usage_raw.get("input_tokens"),
            usage_raw.get("output_tokens"),
            usage_raw.get("cached_tokens"),
            None,
            None,
        )
        incomplete = output is None
        run = InferenceRun(
            str(response.get("id", "missing")),
            self.provider,
            request.model,
            returned_model,
            response.get("request_id"),
            response.get("id"),
            prompt_version,
            prompt_hash,
            schema_version,
            schema_hash,
            bundle_hash,
            code_sha,
            started,
            completed,
            started,
            "INCOMPLETE" if incomplete else "OK",
            str(stop) if stop else None,
            response.get("refusal"),
            incomplete,
            "missing structured output" if incomplete else None,
            usage,
            mode,
        )
        return run, output
