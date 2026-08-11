"""Independent deterministic citation, numeric, semantic, and contradiction validation."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from .models import (
    AtomicClaim,
    ClaimCitation,
    ClaimType,
    ContractInterpretation,
    ContractRelation,
    EpistemicStatus,
    EvidenceBundle,
    EvidenceStatus,
    InterpretationStatus,
)


class Entailment(StrEnum):
    ENTAILED = "ENTAILED"
    PARTIALLY_ENTAILED = "PARTIALLY_ENTAILED"
    CONTRADICTED = "CONTRADICTED"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True, slots=True)
class CitationValidation:
    citation_id: str
    span_exists: bool
    numeric_consistent: bool
    attribution_consistent: bool
    entailment: Entailment
    detail: str


def _numbers(text: str) -> tuple[Decimal, ...]:
    output = []
    for match in re.findall(r"(?<![\w.])-?\d+(?:,\d{3})*(?:\.\d+)?", text):
        try:
            output.append(Decimal(match.replace(",", "")))
        except InvalidOperation:
            continue
    return tuple(output)


def validate_citation(
    claim: AtomicClaim, citation: ClaimCitation, bundle: EvidenceBundle
) -> CitationValidation:
    source = next(
        (item for item in bundle.source_records if item.source_id == citation.source_id), None
    )
    span = (
        source is not None
        and source.document_hash == citation.document_hash
        and 0 <= citation.start_offset < citation.end_offset <= len(source.text)
    )
    exact = bool(
        span
        and source
        and source.text[citation.start_offset : citation.end_offset] == citation.exact_text
    )
    numeric = claim.numeric_value is None or claim.numeric_value in _numbers(citation.exact_text)
    unit = claim.unit is None or claim.unit.casefold() in citation.exact_text.casefold()
    attribution = (
        claim.attribution is None
        or claim.attribution.casefold() in citation.exact_text.casefold()
        or claim.epistemic_status == EpistemicStatus.DIRECTLY_ASSERTED
    )
    supported = exact and numeric and unit and attribution
    detail = "validated" if supported else "span, number, unit, or attribution mismatch"
    return CitationValidation(
        citation.citation_id,
        exact,
        numeric and unit,
        attribution,
        Entailment.ENTAILED if supported else Entailment.NOT_SUPPORTED,
        detail,
    )


def validate_claim(
    claim: AtomicClaim, citations: tuple[ClaimCitation, ...], bundle: EvidenceBundle
) -> tuple[AtomicClaim, tuple[CitationValidation, ...]]:
    selected = tuple(
        citation for citation in citations if citation.citation_id in claim.citation_ids
    )
    if claim.claim_type != ClaimType.MODEL_INFERENCE and not selected:
        return replace(claim, status=EvidenceStatus.REJECTED), ()
    results = tuple(validate_citation(claim, citation, bundle) for citation in selected)
    if any(
        result.entailment in {Entailment.NOT_SUPPORTED, Entailment.CONTRADICTED}
        for result in results
    ):
        return replace(claim, status=EvidenceStatus.REJECTED), results
    return replace(claim, status=EvidenceStatus.CITATION_VALIDATED), results


def relation_to_threshold(
    claim: AtomicClaim, interpretation: ContractInterpretation
) -> ContractRelation:
    if claim.numeric_value is None or interpretation.threshold is None:
        return ContractRelation.AMBIGUOUS
    value, threshold, comparator = (
        claim.numeric_value,
        interpretation.threshold,
        interpretation.comparator,
    )
    if comparator == "GT":
        return ContractRelation.SUPPORTS_YES if value > threshold else ContractRelation.SUPPORTS_NO
    if comparator == "GTE":
        return ContractRelation.SUPPORTS_YES if value >= threshold else ContractRelation.SUPPORTS_NO
    if comparator == "LT":
        return ContractRelation.SUPPORTS_YES if value < threshold else ContractRelation.SUPPORTS_NO
    if comparator == "LTE":
        return ContractRelation.SUPPORTS_YES if value <= threshold else ContractRelation.SUPPORTS_NO
    return ContractRelation.AMBIGUOUS


def validate_interpretation(
    value: ContractInterpretation, contract_text: str
) -> ContractInterpretation:
    blockers = list(value.ambiguities)
    if not value.settlement_authority:
        blockers.append("MISSING_SETTLEMENT_AUTHORITY")
    if value.threshold is not None and str(value.threshold) not in contract_text:
        blockers.append("THRESHOLD_NOT_IN_SOURCE")
    status = InterpretationStatus.VALIDATED if not blockers else InterpretationStatus.AMBIGUOUS
    return replace(value, ambiguities=tuple(dict.fromkeys(blockers)), status=status)


class ContradictionType(StrEnum):
    NUMERIC = "NUMERIC"
    OUTCOME = "OUTCOME"
    DATE = "DATE"
    AUTHORITY = "AUTHORITY"
    PROCEDURAL = "PROCEDURAL"
    IDENTITY = "IDENTITY"
    SOURCE_CORRECTION = "SOURCE_CORRECTION"
    REVISION = "REVISION"
    SEMANTIC = "SEMANTIC"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class Contradiction:
    contradiction_id: str
    claim_a: str
    claim_b: str
    contradiction_type: ContradictionType
    materiality: str
    time_relationship: str
    later_is_correction: bool
    sources_independent: bool
    validation_state: str


def detect_contradiction(first: AtomicClaim, second: AtomicClaim) -> Contradiction | None:
    if first.claim_type in {
        ClaimType.ANALYST_FORECAST,
        ClaimType.OFFICIAL_FORECAST,
    } and second.claim_type in {ClaimType.ANALYST_FORECAST, ClaimType.OFFICIAL_FORECAST}:
        return None
    if first.subject != second.subject or first.predicate != second.predicate:
        return None
    if (
        first.numeric_value is not None
        and second.numeric_value is not None
        and (first.numeric_value != second.numeric_value or first.unit != second.unit)
    ):
        kind = (
            ContradictionType.SOURCE_CORRECTION
            if second.claim_type == ClaimType.CORRECTION
            else ContradictionType.NUMERIC
        )
        return Contradiction(
            f"{first.claim_id}:{second.claim_id}",
            first.claim_id,
            second.claim_id,
            kind,
            "MATERIAL",
            "SECOND_AFTER_FIRST",
            kind == ContradictionType.SOURCE_CORRECTION,
            first.source_id != second.source_id,
            "VALIDATED",
        )
    return None
