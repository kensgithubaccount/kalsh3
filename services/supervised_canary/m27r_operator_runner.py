"""M27R read-only operator phase coordinator for the first weather canary.

This module deliberately owns no network transport, signer, credential reader, mutable
store, approval path, authorization issuer, submission-budget burn, sender, or exchange
mutation capability.

Its sole job is to enforce the M27R ordering boundary around already-reviewed producers:

1. collect public/current candidate evidence;
2. run the unchanged M27D selector;
3. only when there is exactly one qualifying candidate, allow the caller-supplied
   candidate-specific read-only evidence producer to run;
4. feed the resulting evidence into the unchanged M27Q orchestrator;
5. return read-only review evidence.

Concrete live acquisition belongs in a separately capability-tested adapter. Keeping this
coordinator transport-free makes the critical candidate-before-authenticated-read ordering
independently testable and prevents live I/O capability from leaking into M27Q itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from services.opportunity_engine.live_fees import (
    CurrentSeriesFeeObservation,
    EventFeeOverride,
)
from services.risk_engine.domain import RequiredOrderGroupPolicy
from services.risk_engine.invariants import NewRiskReadiness

from .candidate_exposure_check import CandidateExposureEvidence
from .live_read_acceptance import LiveReadAcceptanceBundle
from .m27d import CandidateState, ExperimentalCandidate, select_experimental_candidate
from .m27q_preflight_orchestrator import (
    CandidateInput,
    M27QOrchestratedPreflight,
    build_first_canary_preflight,
)

SOFTWARE_VERSION = "kalsh3.m27r.readonly-operator-runner/1"


class M27ROperatorError(RuntimeError):
    """The read-only operator phase coordinator received invalid evidence."""


@dataclass(frozen=True, slots=True)
class M27RPublicEvidence:
    """Evidence that may be collected before any candidate-specific authenticated sweep."""

    candidate_inputs: tuple[CandidateInput, ...]
    public_evidence_path: Path
    m27j_evidence_path: Path
    m27a_binding_evidence_path: Path
    current_series_fee_observation: CurrentSeriesFeeObservation
    current_event_fee_override: EventFeeOverride
    current_event_fee_observed_at: datetime


@dataclass(frozen=True, slots=True)
class M27RCandidateEvidence:
    """Candidate-specific evidence collected only after exact-one M27D selection."""

    m27f_bundle: LiveReadAcceptanceBundle
    m27f_evidence_path: Path
    m27h_evidence_path: Path
    candidate_exposure: CandidateExposureEvidence
    state_path: Path
    readiness: NewRiskReadiness
    order_group: RequiredOrderGroupPolicy
    authorization_service_available: bool


class PublicEvidenceProvider(Protocol):
    def collect_public_evidence(self, *, now: datetime) -> M27RPublicEvidence: ...


class CandidateEvidenceProvider(Protocol):
    def collect_candidate_evidence(
        self,
        *,
        now: datetime,
        candidate: ExperimentalCandidate,
    ) -> M27RCandidateEvidence: ...


@dataclass(frozen=True, slots=True)
class M27ROperatorRun:
    software_version: str
    state: str
    reason: str | None
    candidate_id: str | None
    authenticated_phase_performed: bool
    read_only: bool
    execution_authorized: bool
    preflight: M27QOrchestratedPreflight | None

    def __post_init__(self) -> None:
        if not self.read_only:
            raise ValueError("M27R operator run must remain read-only")
        if self.execution_authorized:
            raise ValueError("M27R can never authorize execution")
        if self.authenticated_phase_performed and self.candidate_id is None:
            raise ValueError("authenticated M27R phase requires an exact candidate")
        if self.preflight is not None and not self.authenticated_phase_performed:
            raise ValueError("M27Q preflight cannot exist before candidate-specific reads")

    def to_json(self) -> dict[str, object]:
        artifact = None
        if self.preflight is not None:
            artifact = self.preflight.preflight.artifact.to_json()
        return {
            "software_version": self.software_version,
            "state": self.state,
            "reason": self.reason,
            "candidate_id": self.candidate_id,
            "authenticated_phase_performed": self.authenticated_phase_performed,
            "read_only": self.read_only,
            "execution_authorized": self.execution_authorized,
            "preflight": artifact,
        }


def _require_aware(value: datetime, *, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise M27ROperatorError(f"{field} must be timezone-aware")


def run_readonly_operator_preflight(
    *,
    now: datetime,
    public_provider: PublicEvidenceProvider,
    candidate_provider: CandidateEvidenceProvider,
) -> M27ROperatorRun:
    """Run the read-only M27R phases in their only permitted order.

    The candidate provider is not called unless the unchanged M27D selector proves exactly one
    qualifying experimental candidate. A successful M27Q/M27I ``PREFLIGHT_READY`` remains
    evidence only: this function has no authority object, approval, burn, or execution path and
    always returns ``execution_authorized=False``.
    """

    _require_aware(now, field="operator clock")
    public = public_provider.collect_public_evidence(now=now)
    _require_aware(
        public.current_event_fee_observed_at,
        field="current event fee observation",
    )

    selection = select_experimental_candidate(public.candidate_inputs, now=now)
    if (
        selection.state is not CandidateState.QUALIFYING_EXPERIMENTAL_CANARY
        or selection.selected is None
        or len(selection.candidates) != 1
        or selection.selected != selection.candidates[0]
    ):
        return M27ROperatorRun(
            software_version=SOFTWARE_VERSION,
            state="ABSTAIN",
            reason="NO_EXACTLY_ONE_QUALIFYING_EXPERIMENTAL_CANDIDATE",
            candidate_id=None,
            authenticated_phase_performed=False,
            read_only=True,
            execution_authorized=False,
            preflight=None,
        )

    candidate = selection.selected
    candidate_evidence = candidate_provider.collect_candidate_evidence(
        now=now,
        candidate=candidate,
    )

    orchestrated = build_first_canary_preflight(
        now=now,
        candidate_inputs=public.candidate_inputs,
        m27f_bundle=candidate_evidence.m27f_bundle,
        m27f_evidence_path=candidate_evidence.m27f_evidence_path,
        m27h_evidence_path=candidate_evidence.m27h_evidence_path,
        public_evidence_path=public.public_evidence_path,
        m27j_evidence_path=public.m27j_evidence_path,
        m27a_binding_evidence_path=public.m27a_binding_evidence_path,
        current_series_fee_observation=public.current_series_fee_observation,
        current_event_fee_override=public.current_event_fee_override,
        current_event_fee_observed_at=public.current_event_fee_observed_at,
        candidate_exposure=candidate_evidence.candidate_exposure,
        state_path=candidate_evidence.state_path,
        readiness=candidate_evidence.readiness,
        order_group=candidate_evidence.order_group,
        authorization_service_available=candidate_evidence.authorization_service_available,
    )

    artifact = orchestrated.preflight.artifact
    return M27ROperatorRun(
        software_version=SOFTWARE_VERSION,
        state=artifact.state,
        reason=artifact.abstain_reason,
        candidate_id=candidate.candidate_id,
        authenticated_phase_performed=True,
        read_only=True,
        execution_authorized=False,
        preflight=orchestrated,
    )
