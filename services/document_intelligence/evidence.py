"""Structured, cited evidence extraction with a hostile-document boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class EvidenceError(ValueError):
    pass


class ProviderKind(StrEnum):
    FIXTURE = "FIXTURE"
    OPENAI = "OPENAI"
    ANTHROPIC = "ANTHROPIC"


class EvidenceDisposition(StrEnum):
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    NEUTRAL = "NEUTRAL"
    ABSTAINS = "ABSTAINS"


class AbstentionReason(StrEnum):
    INSUFFICIENT_SOURCE_TEXT = "INSUFFICIENT_SOURCE_TEXT"
    AMBIGUOUS_CONTRACT = "AMBIGUOUS_CONTRACT"
    MISSING_SETTLEMENT_AUTHORITY = "MISSING_SETTLEMENT_AUTHORITY"
    CONFLICTING_PRIMARY_SOURCES = "CONFLICTING_PRIMARY_SOURCES"
    UNRESOLVED_NUMERIC_CONFLICT = "UNRESOLVED_NUMERIC_CONFLICT"
    CITATION_FAILURE = "CITATION_FAILURE"
    MALFORMED_PROVIDER_RESPONSE = "MALFORMED_PROVIDER_RESPONSE"
    PROVIDER_REFUSAL = "PROVIDER_REFUSAL"
    UNSUPPORTED_LANGUAGE = "UNSUPPORTED_LANGUAGE"
    DOCUMENT_TOO_LARGE = "DOCUMENT_TOO_LARGE"
    MODEL_UNCERTAIN = "MODEL_UNCERTAIN"
    PROMPT_INJECTION_RISK = "PROMPT_INJECTION_RISK"
    OTHER_VALIDATED_REASON = "OTHER_VALIDATED_REASON"


@dataclass(frozen=True, slots=True)
class Document:
    document_id: str
    content_hash: str
    text: str
    source_locator: str
    retrieved_at: str


@dataclass(frozen=True, slots=True)
class Citation:
    document_id: str
    locator: str
    excerpt: str


@dataclass(frozen=True, slots=True)
class EvidenceClaim:
    claim_id: str
    statement: str
    disposition: EvidenceDisposition
    citations: tuple[Citation, ...]
    explicit_ambiguities: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EvidenceResult:
    claims: tuple[EvidenceClaim, ...]
    contradictions: tuple[str, ...]
    abstained: bool
    model_version: str
    prompt_version: str
    schema_version: str
    injection_flags: tuple[str, ...]
    abstention_reasons: tuple[AbstentionReason, ...] = ()


class EvidenceProvider(Protocol):
    kind: ProviderKind

    def extract(self, documents: tuple[Document, ...], question: str) -> EvidenceResult: ...


INJECTION_PHRASES = ("ignore previous", "system prompt", "developer message", "call tool")


def injection_flags(documents: tuple[Document, ...]) -> tuple[str, ...]:
    text = " ".join(document.text.casefold() for document in documents)
    return tuple(phrase for phrase in INJECTION_PHRASES if phrase in text)


def validate(result: EvidenceResult, document_ids: set[str]) -> EvidenceResult:
    if not result.model_version or not result.prompt_version or not result.schema_version:
        raise EvidenceError("model, prompt, and schema versions are required")
    for claim in result.claims:
        if claim.disposition != EvidenceDisposition.ABSTAINS and not claim.citations:
            raise EvidenceError("material claims require citations")
        if any(citation.document_id not in document_ids for citation in claim.citations):
            raise EvidenceError("citation references an unknown document")
    if result.injection_flags and not result.abstained:
        raise EvidenceError("hostile document requires abstention")
    return result


class FixtureProvider:
    kind = ProviderKind.FIXTURE

    def __init__(self, result: EvidenceResult) -> None:
        self.result = result

    def extract(self, documents: tuple[Document, ...], question: str) -> EvidenceResult:
        del question
        flags = injection_flags(documents)
        result = EvidenceResult(
            self.result.claims,
            self.result.contradictions,
            self.result.abstained or bool(flags),
            self.result.model_version,
            self.result.prompt_version,
            self.result.schema_version,
            flags,
            self.result.abstention_reasons
            or ((AbstentionReason.PROMPT_INJECTION_RISK,) if flags else ()),
        )
        return validate(result, {document.document_id for document in documents})
