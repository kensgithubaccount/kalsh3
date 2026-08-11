"""Deterministic offline evaluation metrics and dev/held-out split guardrails."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class FixtureSplit(StrEnum):
    DEVELOPMENT = "DEVELOPMENT"
    HELD_OUT = "HELD_OUT"


@dataclass(frozen=True, slots=True)
class EvalArtifact:
    artifact_id: str
    split: FixtureSplit
    schema_valid: bool
    required_fields_correct: bool
    numeric_exact: bool
    unit_exact: bool
    datetime_exact: bool
    attribution_correct: bool
    citation_span_valid: bool
    citation_entailed: bool
    unsupported_material_claim: bool
    contradiction_correct: bool
    expected_abstain: bool
    actually_abstained: bool
    injection_failure: bool
    contract_fields_correct: bool


@dataclass(frozen=True, slots=True)
class EvalMetrics:
    count: int
    schema_valid_rate: Decimal
    numeric_exact_rate: Decimal
    unit_exact_rate: Decimal
    citation_span_validity: Decimal
    unsupported_material_claim_rate: Decimal
    abstention_accuracy: Decimal
    prompt_injection_failure_rate: Decimal
    contract_semantics_accuracy: Decimal


def evaluate(artifacts: tuple[EvalArtifact, ...]) -> EvalMetrics:
    if not artifacts:
        raise ValueError("evaluation corpus is empty")
    count = Decimal(len(artifacts))

    def rate(predicate: Callable[[EvalArtifact], bool]) -> Decimal:
        return Decimal(sum(1 for item in artifacts if predicate(item))) / count

    return EvalMetrics(
        len(artifacts),
        rate(lambda x: x.schema_valid),
        rate(lambda x: x.numeric_exact),
        rate(lambda x: x.unit_exact),
        rate(lambda x: x.citation_span_valid),
        rate(lambda x: x.unsupported_material_claim),
        rate(lambda x: x.expected_abstain == x.actually_abstained),
        rate(lambda x: x.injection_failure),
        rate(lambda x: x.contract_fields_correct),
    )
