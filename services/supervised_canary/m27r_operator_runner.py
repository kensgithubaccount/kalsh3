"""M27R read-only operator phase coordinator for the first weather canary.

The public production entry point owns its runtime clock. Callers cannot supply or freeze live
time. A monotonic elapsed-time floor is combined with UTC wall time so a backward wall-clock jump
cannot make already-acquired evidence appear younger. Deterministic clocks remain available only
through the explicitly test-only private seam.

The coordinator owns no network transport, signer, credential reader, mutable store, approval,
authorization issuer, burn, sender, or exchange mutation capability.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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

SOFTWARE_VERSION = "kalsh3.m27r.readonly-operator-runner/4"
Clock = Callable[[], datetime]


class M27ROperatorError(RuntimeError):
    """The read-only operator phase coordinator received invalid evidence."""


class _TrustedRuntimeClock:
    """Production UTC clock protected by a monotonic elapsed-time floor."""

    def __init__(self) -> None:
        self._wall_start = datetime.now(UTC)
        self._mono_start = time.monotonic()
        self._last = self._wall_start

    def now(self) -> datetime:
        wall = datetime.now(UTC)
        elapsed = time.monotonic() - self._mono_start
        if elapsed < 0:
            raise M27ROperatorError("monotonic production clock moved backward")
        monotonic_floor = self._wall_start + timedelta(seconds=elapsed)
        value = max(wall, monotonic_floor)
        if value <= self._last:
            value = self._last + timedelta(microseconds=1)
        self._last = value
        return value


@dataclass(frozen=True, slots=True)
class M27RMarketEvidence:
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
    candidate_id: str
    market_ticker: str
    m27f_bundle: LiveReadAcceptanceBundle
    m27f_evidence_path: Path
    m27h_evidence_path: Path
    candidate_exposure: CandidateExposureEvidence
    state_path: Path
    readiness: NewRiskReadiness
    order_group: RequiredOrderGroupPolicy
    authorization_service_available: bool

    def __post_init__(self) -> None:
        if not self.candidate_id or not self.market_ticker:
            raise ValueError("candidate-specific evidence identity is missing")
        if self.candidate_exposure.market_ticker != self.market_ticker:
            raise ValueError("candidate exposure is bound to a different market")


class PublicEvidenceProvider(Protocol):
    def collect_public_evidence(self, *, clock: Clock) -> M27RPublicEvidence: ...


class CandidateEvidenceProvider(Protocol):
    def collect_candidate_evidence(
        self,
        *,
        clock: Clock,
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
        risk_identities: object | None = None
        state_identity: object | None = None
        if self.preflight is not None:
            artifact = self.preflight.preflight.artifact.to_json()
            risk = self.preflight.risk
            inspection = self.preflight.state_inspection
            risk_identities = {
                "intent_content_hash": risk.intent.content_hash,
                "snapshot_content_hash": risk.snapshot.content_hash,
                "decision_content_hash": risk.decision.content_hash,
                "decision_id": risk.decision.decision_id,
                "production_write_authorized": risk.decision.production_write_authorized,
            }
            state_identity = {
                "database_sha256": inspection.database_sha256,
                "inspected_at": inspection.inspected_at.isoformat(),
                "loss_state_version": inspection.loss_state_version,
                "compliance_state_version": inspection.compliance_state_version,
                "kill_state_version": inspection.kill_state_version,
                "pristine_first_canary": inspection.pristine_first_canary,
            }
        return {
            "software_version": self.software_version,
            "state": self.state,
            "reason": self.reason,
            "candidate_id": self.candidate_id,
            "authenticated_phase_performed": self.authenticated_phase_performed,
            "read_only": self.read_only,
            "execution_authorized": self.execution_authorized,
            "risk_identities": risk_identities,
            "state_inspection_identity": state_identity,
            "preflight": artifact,
        }


def _require_aware(value: datetime, *, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise M27ROperatorError(f"{field} must be timezone-aware")


def _clock_now(clock: Clock, *, field: str) -> datetime:
    value = clock()
    _require_aware(value, field=field)
    return value


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


def _run_readonly_operator_preflight_for_test(
    *,
    clock: Clock,
    public_provider: PublicEvidenceProvider,
    candidate_provider: CandidateEvidenceProvider,
) -> M27ROperatorRun:
    """Explicit test-only deterministic seam. Production callers must use the public wrapper."""

    _clock_now(clock, field="operator start clock")
    public = public_provider.collect_public_evidence(clock=clock)

    selection_now = _clock_now(clock, field="candidate selection clock")
    selection = select_experimental_candidate(public.candidate_inputs, now=selection_now)
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
    market = _selected_market_evidence(public=public, candidate=candidate, now=selection_now)

    candidate_evidence = candidate_provider.collect_candidate_evidence(
        clock=clock,
        candidate=candidate,
    )
    if (
        candidate_evidence.candidate_id != candidate.candidate_id
        or candidate_evidence.market_ticker != candidate.market_ticker
    ):
        raise M27ROperatorError(
            "candidate-specific authenticated evidence is bound to a different candidate"
        )

    preflight_now = _clock_now(clock, field="M27Q consumption clock")
    orchestrated = build_first_canary_preflight(
        now=preflight_now,
        selected_candidate=candidate,
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
    if artifact.candidate_id != candidate.candidate_id:
        raise M27ROperatorError("final preflight candidate identity changed after authentication")
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


def run_readonly_operator_preflight(
    *,
    public_provider: PublicEvidenceProvider,
    candidate_provider: CandidateEvidenceProvider,
) -> M27ROperatorRun:
    """Production M27R entry point with internally owned trusted wall+monotonic time."""

    runtime_clock = _TrustedRuntimeClock()
    return _run_readonly_operator_preflight_for_test(
        clock=runtime_clock.now,
        public_provider=public_provider,
        candidate_provider=candidate_provider,
    )
