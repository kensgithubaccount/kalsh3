"""Immutable M7 bundles, claims, citations, interpretations, and run provenance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from services.historical_replay.archive import stable_hash


class ClaimType(StrEnum):
    OBSERVED_FACT = "OBSERVED_FACT"
    OFFICIAL_RESULT = "OFFICIAL_RESULT"
    OFFICIAL_FORECAST = "OFFICIAL_FORECAST"
    SCHEDULED_EVENT = "SCHEDULED_EVENT"
    QUOTED_STATEMENT = "QUOTED_STATEMENT"
    SECONDARY_REPORT = "SECONDARY_REPORT"
    ANALYST_FORECAST = "ANALYST_FORECAST"
    OPINION = "OPINION"
    RUMOR = "RUMOR"
    PROCEDURAL_FACT = "PROCEDURAL_FACT"
    NUMERIC_MEASUREMENT = "NUMERIC_MEASUREMENT"
    CORRECTION = "CORRECTION"
    RETRACTION = "RETRACTION"
    MODEL_INFERENCE = "MODEL_INFERENCE"
    UNKNOWN = "UNKNOWN"


class EpistemicStatus(StrEnum):
    DIRECTLY_ASSERTED = "DIRECTLY_ASSERTED"
    ATTRIBUTED_ASSERTION = "ATTRIBUTED_ASSERTION"
    INFERRED = "INFERRED"
    FORECAST = "FORECAST"
    ESTIMATE = "ESTIMATE"
    OPINION = "OPINION"
    RUMOR = "RUMOR"
    DISPUTED = "DISPUTED"
    CORRECTED = "CORRECTED"
    RETRACTED = "RETRACTED"
    UNKNOWN = "UNKNOWN"


class EvidenceStatus(StrEnum):
    PROPOSED = "PROPOSED"
    SCHEMA_VALID = "SCHEMA_VALID"
    CITATION_VALIDATED = "CITATION_VALIDATED"
    SEMANTICALLY_CLASSIFIED = "SEMANTICALLY_CLASSIFIED"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"
    RETRACTED = "RETRACTED"
    AMBIGUOUS = "AMBIGUOUS"


class ContractRelation(StrEnum):
    SUPPORTS_YES = "SUPPORTS_YES"
    SUPPORTS_NO = "SUPPORTS_NO"
    QUALIFIES_YES = "QUALIFIES_YES"
    QUALIFIES_NO = "QUALIFIES_NO"
    PROCEDURAL_ONLY = "PROCEDURAL_ONLY"
    CONTEXT_ONLY = "CONTEXT_ONLY"
    IRRELEVANT = "IRRELEVANT"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICTS_WITH_CONTRACT_ASSUMPTION = "CONFLICTS_WITH_CONTRACT_ASSUMPTION"


@dataclass(frozen=True, slots=True)
class SourceRecord:
    source_id: str
    document_hash: str
    text: str
    published_at: datetime | None
    replay_available_at: datetime | None
    verification_state: str
    originality_state: str
    correction_state: str


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    evidence_bundle_id: str
    created_at: datetime
    replay_at: datetime
    market_ticker: str
    rules_version: str
    rules_hash: str
    settlement_sources: tuple[str, ...]
    market_question: str
    contract_text: str
    source_records: tuple[SourceRecord, ...]
    content_hash: str
    builder_version: str

    @classmethod
    def build(
        cls,
        *,
        created_at: datetime,
        replay_at: datetime,
        market_ticker: str,
        rules_version: str,
        rules_hash: str,
        settlement_sources: tuple[str, ...],
        market_question: str,
        contract_text: str,
        sources: tuple[SourceRecord, ...],
        builder_version: str = "m7-bundle-v1",
    ) -> EvidenceBundle:
        visible = tuple(
            source
            for source in sources
            if source.replay_available_at is not None and source.replay_available_at <= replay_at
        )
        material = (
            replay_at,
            market_ticker,
            rules_version,
            rules_hash,
            sorted(settlement_sources),
            market_question,
            contract_text,
            visible,
            builder_version,
        )
        digest = stable_hash(material)
        return cls(
            digest,
            created_at,
            replay_at,
            market_ticker,
            rules_version,
            rules_hash,
            tuple(sorted(settlement_sources)),
            market_question,
            contract_text,
            visible,
            digest,
            builder_version,
        )


@dataclass(frozen=True, slots=True)
class ClaimCitation:
    citation_id: str
    source_id: str
    document_hash: str
    locator_type: str
    start_offset: int
    end_offset: int
    exact_text: str


@dataclass(frozen=True, slots=True)
class AtomicClaim:
    claim_id: str
    claim_text: str
    claim_type: ClaimType
    subject: str
    predicate: str
    object_value: str
    numeric_value: Decimal | None
    unit: str | None
    comparator: str | None
    numeric_range: tuple[Decimal, Decimal] | None
    original_numeric_text: str | None
    event_time: datetime | None
    effective_time: datetime | None
    timezone: str | None
    geography: str | None
    named_authority: str | None
    epistemic_status: EpistemicStatus
    attribution: str | None
    source_id: str | None
    citation_ids: tuple[str, ...]
    confidence_category: str
    extraction_model: str
    prompt_version: str
    status: EvidenceStatus = EvidenceStatus.PROPOSED
    supersedes_claim_id: str | None = None


class InterpretationStatus(StrEnum):
    VALIDATED = "VALIDATED"
    PROVISIONAL = "PROVISIONAL"
    AMBIGUOUS = "AMBIGUOUS"
    UNSUPPORTED = "UNSUPPORTED"
    INVALIDATED_BY_RULE_CHANGE = "INVALIDATED_BY_RULE_CHANGE"


@dataclass(frozen=True, slots=True)
class ContractInterpretation:
    interpretation_id: str
    market_ticker: str
    yes_proposition: str
    no_proposition: str
    settlement_authority: str | None
    settlement_sources: tuple[str, ...]
    relevant_at: datetime | None
    timezone: str | None
    threshold: Decimal | None
    comparator: str | None
    inclusivity: str | None
    rounding_policy: str | None
    revision_policy: str | None
    recount_policy: str | None
    cancellation_policy: str | None
    postponement_policy: str | None
    early_close_condition: str | None
    ambiguities: tuple[str, ...]
    required_evidence_fields: tuple[str, ...]
    status: InterpretationStatus
    source_citation_ids: tuple[str, ...]
    rules_version: str
    model_version: str | None
    prompt_version: str | None
    deterministic_validations: tuple[str, ...]


class RunMode(StrEnum):
    MODEL_OUTPUT_OBSERVED_LIVE = "MODEL_OUTPUT_OBSERVED_LIVE"
    RETROSPECTIVE_MODEL_RUN = "RETROSPECTIVE_MODEL_RUN"
    EXPLORATORY_NONREPRODUCIBLE = "EXPLORATORY_NONREPRODUCIBLE"


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int | None
    output_tokens: int | None
    cached_tokens: int | None
    estimated_cost: Decimal | None
    actual_cost: Decimal | None


@dataclass(frozen=True, slots=True)
class InferenceRun:
    run_id: str
    provider: str
    requested_model: str
    returned_model: str
    request_id: str | None
    response_id: str | None
    prompt_version: str
    prompt_hash: str
    schema_version: str
    schema_hash: str
    bundle_hash: str
    code_git_sha: str
    started_at: datetime
    completed_at: datetime
    source_available_at: datetime
    status: str
    stop_reason: str | None
    refusal: str | None
    incomplete: bool
    provider_error: str | None
    usage: Usage
    mode: RunMode

    @property
    def latency(self) -> timedelta:
        return self.completed_at - self.started_at
