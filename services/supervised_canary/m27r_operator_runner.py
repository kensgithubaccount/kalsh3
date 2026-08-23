"""M27R read-only operator phase coordinator for the first weather canary.

This module deliberately owns no network transport, signer, credential reader, mutable
store, approval path, authorization issuer, submission-budget burn, sender, or exchange
mutation capability.

Its sole job is to enforce the M27R ordering boundary around already-reviewed producers:

1. collect public/current candidate evidence, retained in exact per-market slices;
2. run the unchanged M27D selector across those candidate inputs;
3. bind the unique selected candidate back to exactly one matching public evidence slice;
4. only then allow the caller-supplied candidate-specific read-only evidence producer to run;
5. feed the exact selected slice plus account evidence into the unchanged M27Q orchestrator;
6. return read-only review evidence.

Concrete live acquisition belongs in a separately capability-tested adapter. Keeping this
coordinator transport-free makes the critical candidate-before-authenticated-read ordering and
candidate-to-public-evidence binding independently testable and prevents live I/O capability from
leaking into M27Q itself.
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

SOFTWARE_VERSION = "kalsh3.m27r.readonly-operator-runner/2"


class M27ROperatorError(RuntimeError):
    """The read-only operator phase coordinator received invalid evidence."""


@dataclass(frozen=True, slots=True)
class M27RMarketEvidence:
    """One candidate input and the public evidence that can authorize only that market's review."""

    market_ticker: str
    candidate_input: CandidateInput
    m27j_evidence_path: Path
    m27a_binding_evidence_path: Path
    current_series_fee_observation: CurrentSeriesFeeObservation
    current_event_fee_override: EventFeeOverride
    current_event_fee_observed_at: datetime

    def __post_init__(self) -> None:
        if not self.market_ticker:
            raise ValueError("M27R market evidence requires a market ticker")
        _require_aware(
            self.current_event_fee_observed_at,
            field=f"current event fee observation for {self.market_ticker}",
        )


@dataclass(frozen=True, slots=True)
class M27RPublicEvidence:
    """Evidence that may be collected before any candidate-specific authenticated sweep."""

    public_evidence_path: Path
    markets: tuple[M27RMarketEvidence, ...]

    def __post_init__(self) -> None:
        tickers = tuple(market.market_ticker for market in self.markets)
        if len(tickers) != len(set(tickers)):
            raise ValueError("M27R public evidence contains duplicate market tickers")

    @property
    def candidate_inputs(self) -> tuple[CandidateInput, ...]:
        return tuple(market.candidate_input for market in self.markets)


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
        artifact: object | None = None
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


def _selected_market_evidence(
    *,
    public: M27RPublicEvidence,
    candidate: ExperimentalCandidate,
    now: datetime,
) -> M27RMarketEvidence:
    matches = tuple(
        market for market in public.markets if market.market_ticker == candidate.market_ticker
    )
    if len(matches) != 1:
        raise M27ROperatorError(
            "selected candidate does not bind to exactly one public market evidence slice"
        )
    market = matches[0]

    # Independently prove that this exact slice, by itself, reconstructs the exact same candidate
    # M27D selected globally. A ticker match alone is not enough: a stale/different economics or
    # weather input for the same ticker must never be substituted after selection.
    slice_selection = select_experimental_candidate((market.candidate_input,), now=now)
    if (
        slice_selection.state is not CandidateState.QUALIFYING_EXPERIMENTAL_CANARY
        or slice_selection.selected is None
        or len(slice_selection.candidates) != 1
        or slice_selection.selected.candidate_id != candidate.candidate_id
    ):
        raise M27ROperatorError(
            "selected candidate identity does not match its public evidence slice"
        )
    return market


def run_readonly_operator_preflight(
    *,
    now: datetime,
    public_provider: PublicEvidenceProvider,
    candidate_provider: CandidateEvidenceProvider,
) -> M27ROperatorRun:
    """Run the read-only M27R phases in their only permitted order.

    The candidate provider is not called unless the unchanged M27D selector proves exactly one
    qualifying experimental candidate and that exact candidate independently binds to one
    per-market public evidence slice. A successful M27Q/M27I ``PREFLIGHT_READY`` remains evidence
    only: this function has no authority object, approval, burn, or execution path and always
    returns ``execution_authorized=False``.
    """

    _require_aware(now, field="operator clock")
    public = public_provider.collect_public_evidence(now=now)

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
    market = _selected_market_evidence(public=public, candidate=candidate, now=now)

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
        m27j_evidence_path=market.m27j_evidence_path,
        m27a_binding_evidence_path=market.m27a_binding_evidence_path,
        current_series_fee_observation=market.current_series_fee_observation,
        current_event_fee_override=market.current_event_fee_override,
        current_event_fee_observed_at=market.current_event_fee_observed_at,
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
