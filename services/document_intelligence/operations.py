"""Configuration, budgets, cache, document preprocessing, and background-only work."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256

from .models import EvidenceBundle


class LLMState(StrEnum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    RATE_LIMITED = "RATE_LIMITED"
    COST_LIMIT_REACHED = "COST_LIMIT_REACHED"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    SCHEMA_FAILURE = "SCHEMA_FAILURE"
    CITATION_FAILURE = "CITATION_FAILURE"
    EVAL_REGRESSION = "EVAL_REGRESSION"


@dataclass(frozen=True, slots=True)
class LLMConfig:
    provider: str
    model: str
    timeout: float
    max_output: int
    retry_policy: int
    cost_limit: Decimal
    request_limit: int
    token_limit: int
    concurrency_limit: int

    @classmethod
    def from_mapping(cls, values: dict[str, str]) -> LLMConfig:
        return cls(
            values.get("LLM_PROVIDER", "fixture"),
            values.get("LLM_MODEL", "fixture-v1"),
            float(values.get("LLM_TIMEOUT", "30")),
            int(values.get("LLM_MAX_OUTPUT", "4096")),
            int(values.get("LLM_RETRY_POLICY", "1")),
            Decimal(values.get("LLM_COST_LIMIT", "0")),
            int(values.get("LLM_REQUEST_LIMIT", "100")),
            int(values.get("LLM_TOKEN_LIMIT", "100000")),
            int(values.get("LLM_CONCURRENCY_LIMIT", "2")),
        )


def cache_key(
    provider: str,
    exact_model: str,
    prompt_hash: str,
    schema_hash: str,
    bundle_hash: str,
    inference_config: dict[str, object],
) -> str:
    material = json.dumps(
        (provider, exact_model, prompt_hash, schema_hash, bundle_hash, inference_config),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(material.encode()).hexdigest()


@dataclass(slots=True)
class InferenceCache:
    values: dict[str, str] = field(default_factory=dict)

    def put_once(self, key: str, run_id: str) -> None:
        self.values.setdefault(key, run_id)

    def get(self, key: str) -> str | None:
        return self.values.get(key)


@dataclass(slots=True)
class Budget:
    request_limit: int
    token_limit: int
    cost_limit: Decimal
    requests: int = 0
    tokens: int = 0
    cost: Decimal = Decimal("0")

    def consume(self, tokens: int, cost: Decimal | None) -> bool:
        proposed_cost = self.cost + (cost or Decimal("0"))
        if self.requests + 1 > self.request_limit or self.tokens + tokens > self.token_limit:
            return False
        if cost is not None and proposed_cost > self.cost_limit:
            return False
        self.requests += 1
        self.tokens += tokens
        self.cost = proposed_cost
        return True


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    chunk_id: str
    source_id: str
    start: int
    end: int
    text: str
    complete_document: bool
    extraction_quality: str


def chunk_document(
    source_id: str, text: str, max_chars: int = 4000, overlap: int = 200
) -> tuple[DocumentChunk, ...]:
    if max_chars <= overlap or max_chars < 100:
        raise ValueError("invalid chunk bounds")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    chunks: list[DocumentChunk] = []
    start = 0
    while start < len(normalized):
        candidate = min(start + max_chars, len(normalized))
        end = candidate
        if candidate < len(normalized):
            boundary = normalized.rfind("\n\n", start, candidate)
            if boundary > start:
                end = boundary + 2
        content = normalized[start:end]
        digest = sha256(f"{source_id}:{start}:{end}:{content}".encode()).hexdigest()
        chunks.append(
            DocumentChunk(
                digest,
                source_id,
                start,
                end,
                content,
                len(chunks) == 0 and end == len(normalized),
                "SOURCE_NATIVE",
            )
        )
        if end == len(normalized):
            break
        start = max(end - overlap, start + 1)
    return tuple(chunks)


@dataclass(frozen=True, slots=True)
class TableCell:
    table_id: str
    row_label: str
    column_label: str
    value: Decimal
    unit: str
    footnote: str | None
    source_locator: str


@dataclass(frozen=True, slots=True)
class InferenceJob:
    job_id: str
    bundle: EvidenceBundle
    cache_key: str


class BackgroundQueue:
    """Inference can only be enqueued; web rendering has no provider object or run method."""

    def __init__(self, limit: int = 1000) -> None:
        self.limit = limit
        self._jobs: list[InferenceJob] = []

    def enqueue(self, job: InferenceJob) -> None:
        if len(self._jobs) >= self.limit:
            raise RuntimeError("inference queue full")
        self._jobs.append(job)

    def take(self) -> InferenceJob | None:
        return self._jobs.pop(0) if self._jobs else None
