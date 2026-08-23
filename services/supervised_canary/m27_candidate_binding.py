"""Exact binding of one already-selected M27D candidate across later read-only phases.

M27D candidate IDs intentionally include the original selection timestamp. Re-running M27D a
few seconds later therefore creates a different candidate ID even when every market, weather,
side, price, and economics input is unchanged. Later phases must re-check current qualification
without silently replacing the candidate whose authenticated reads were collected.

This module performs that distinction explicitly: it proves the supplied candidate is exactly
reconstructible from its original evidence at its original selection time, proves there is still
exactly one currently qualifying candidate, and proves that current candidate is economically
and evidentially the same candidate while preserving the original immutable candidate ID.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from .m27d import CandidateState, ExperimentalCandidate, select_experimental_candidate
from .m27q_preflight_orchestrator_types import CandidateInput


class CandidateBindingError(RuntimeError):
    """An already-selected candidate could not be proven current and unchanged."""


@dataclass(frozen=True, slots=True)
class CandidateBindingResult:
    candidate: ExperimentalCandidate
    candidate_input: CandidateInput


def _stable_identity(candidate: ExperimentalCandidate) -> tuple[object, ...]:
    eligibility = candidate.eligibility
    return (
        candidate.market_ticker,
        candidate.event_ticker,
        candidate.series_ticker,
        candidate.predicate,
        candidate.selected_side,
        candidate.executable_price,
        candidate.available_quantity,
        candidate.maximum_fee,
        candidate.maximum_commitment,
        candidate.maximum_loss,
        candidate.all_in_break_even_probability,
        candidate.research_probability_discrepancy,
        candidate.ranking,
        candidate.economics_evidence_identity,
        candidate.truth_warning,
        eligibility.status,
        eligibility.weather_result_identity,
        eligibility.model_identity,
        eligibility.claim_type,
        eligibility.settlement_mapping_status,
        eligibility.source_family,
        eligibility.forecast_evidence_identity,
        eligibility.contract_identity,
        eligibility.target_date,
        eligibility.exact_midpoint_seconds,
        eligibility.market_evidence_identity,
        eligibility.selection_policy_identity,
        eligibility.research_warning,
        eligibility.human_approval_required,
    )


def validate_selected_candidate_binding(
    *,
    candidate: ExperimentalCandidate,
    candidate_inputs: Sequence[CandidateInput],
    now: datetime,
) -> CandidateBindingResult:
    if now.tzinfo is None or now.utcoffset() is None:
        raise CandidateBindingError("candidate binding clock must be timezone-aware")
    if now < candidate.eligibility.created_at or now > candidate.eligibility.expires_at:
        raise CandidateBindingError("original selected candidate expired before later consumption")

    matching_inputs = tuple(
        item
        for item in candidate_inputs
        if item[2].evidence_id == candidate.economics_evidence_identity
    )
    if len(matching_inputs) != 1:
        raise CandidateBindingError(
            "selected candidate does not bind uniquely to its economics evidence"
        )
    candidate_input = matching_inputs[0]

    original = select_experimental_candidate(
        (candidate_input,),
        now=candidate.eligibility.created_at,
    )
    if (
        original.state is not CandidateState.QUALIFYING_EXPERIMENTAL_CANARY
        or original.selected is None
        or len(original.candidates) != 1
        or original.selected != candidate
    ):
        raise CandidateBindingError(
            "selected candidate is not exactly reproducible from its original evidence"
        )

    current = select_experimental_candidate(candidate_inputs, now=now)
    if (
        current.state is not CandidateState.QUALIFYING_EXPERIMENTAL_CANARY
        or current.selected is None
        or len(current.candidates) != 1
        or current.selected != current.candidates[0]
    ):
        raise CandidateBindingError(
            "later consumption no longer has exactly one qualifying experimental candidate"
        )
    if _stable_identity(current.selected) != _stable_identity(candidate):
        raise CandidateBindingError(
            "currently qualifying candidate differs from the originally authenticated candidate"
        )

    return CandidateBindingResult(candidate=candidate, candidate_input=candidate_input)
