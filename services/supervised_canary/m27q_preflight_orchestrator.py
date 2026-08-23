"""M27Q offline-capable first-canary M13 -> M27I preflight orchestration.

This module adds no market, forecasting, risk, settlement, or execution policy.

It composes already-reviewed components:

1. exact-one M27D candidate selection;
2. exact persisted-M27F <-> same-sweep transient bundle binding;
3. immutable M27P shared-state inspection;
4. pure M13 first-canary risk production;
5. existing M27I final read-only preflight consumption.

It has no network transport, signer, credential reader, mutable store, approval,
authorization issuance, burn, execution authorization, or order capability.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from services.forecasting.weather_probability import (
    CurrentWeatherForecastEvidence,
    PhysicalTemperatureProxyProbability,
)
from services.opportunity_engine.live_economics import MarketEconomicsEvidence
from services.opportunity_engine.live_fees import (
    CurrentSeriesFeeObservation,
    EventFeeOverride,
)
from services.risk_engine.domain import (
    ReconciliationStatus,
    RequiredOrderGroupPolicy,
)
from services.risk_engine.invariants import NewRiskReadiness

from .candidate_exposure_check import CandidateExposureEvidence
from .live_read_acceptance import LiveReadAcceptanceBundle
from .m27d import CandidateState, select_experimental_candidate
from .m27i import PreflightResult, build_preflight
from .m27q_risk_preflight import (
    M27QRiskTriple,
    RiskContextVersions,
    build_first_canary_risk_triple,
)
from .m27q_state_inspection import (
    FirstCanaryStateInspection,
    M27IImmutableStateView,
    build_safety_state,
    inspect_first_canary_state,
)

SOFTWARE_VERSION = "kalsh3.m27q.first-canary-preflight-orchestrator/1"

CandidateInput = tuple[
    PhysicalTemperatureProxyProbability,
    CurrentWeatherForecastEvidence,
    MarketEconomicsEvidence,
]


class M27QOrchestrationError(RuntimeError):
    """Required evidence could not be bound safely for first-canary preflight."""


@dataclass(frozen=True, slots=True)
class M27QOrchestratedPreflight:
    software_version: str
    state_inspection: FirstCanaryStateInspection
    risk: M27QRiskTriple
    preflight: PreflightResult


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    )


def _require_exact_m27f_artifact(
    bundle: LiveReadAcceptanceBundle,
    path: Path,
) -> None:
    try:
        stored = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise M27QOrchestrationError("persisted M27F evidence is unavailable or malformed") from exc

    expected = bundle.evidence.to_json()
    if _canonical_json(stored) != _canonical_json(expected):
        raise M27QOrchestrationError(
            "persisted M27F evidence does not match the exact same-sweep bundle"
        )


def build_first_canary_preflight(
    *,
    now: datetime,
    candidate_inputs: Sequence[CandidateInput],
    m27f_bundle: LiveReadAcceptanceBundle,
    m27f_evidence_path: Path,
    m27h_evidence_path: Path,
    public_evidence_path: Path,
    m27j_evidence_path: Path,
    m27a_binding_evidence_path: Path,
    current_series_fee_observation: CurrentSeriesFeeObservation,
    current_event_fee_override: EventFeeOverride,
    current_event_fee_observed_at: datetime,
    candidate_exposure: CandidateExposureEvidence,
    state_path: Path,
    readiness: NewRiskReadiness,
    order_group: RequiredOrderGroupPolicy,
    authorization_service_available: bool,
) -> M27QOrchestratedPreflight:
    """Build an M13 pass candidate and consume it through the real M27I boundary.

    A returned ``PREFLIGHT_READY`` artifact remains read-only preflight evidence.
    This function never creates M13 authorization, M16 approval, M27O execution
    authorization, or any exchange request.
    """

    if now.tzinfo is None or now.utcoffset() is None:
        raise M27QOrchestrationError("orchestration clock must be timezone-aware")

    if not isinstance(authorization_service_available, bool):
        raise M27QOrchestrationError("authorization_service_available must be bool")

    selection = select_experimental_candidate(
        candidate_inputs,
        now=now,
    )

    if (
        selection.state is not CandidateState.QUALIFYING_EXPERIMENTAL_CANARY
        or selection.selected is None
        or len(selection.candidates) != 1
        or selection.selected != selection.candidates[0]
    ):
        raise M27QOrchestrationError(
            "M27Q first-canary risk assembly requires exactly one qualifying candidate"
        )

    candidate = selection.selected

    matching_inputs = tuple(
        item
        for item in candidate_inputs
        if item[2].evidence_id == candidate.economics_evidence_identity
    )
    if len(matching_inputs) != 1:
        raise M27QOrchestrationError(
            "selected candidate does not bind uniquely to economics evidence"
        )

    economics = matching_inputs[0][2]

    _require_exact_m27f_artifact(
        m27f_bundle,
        m27f_evidence_path,
    )

    if not m27f_bundle.evidence.reconciliation.succeeded:
        raise M27QOrchestrationError("same-sweep M27F reconciliation did not pass")

    if not candidate_exposure.succeeded:
        raise M27QOrchestrationError("candidate-specific exposure evidence did not pass")

    inspection = inspect_first_canary_state(
        state_path=state_path,
        now=now,
    )
    durable_state = inspection.first_canary_durable_state()

    safety = build_safety_state(
        inspection,
        now=now,
        reconciliation_status=ReconciliationStatus.RECONCILED,
    )

    versions = RiskContextVersions(
        rules_version=economics.market_rules_hash,
        rules_hash=economics.market_rules_hash,
        contract_interpretation_version=candidate.eligibility.contract_identity,
        market_data_version=economics.evidence_id,
        loss_state_version=inspection.loss_state_version,
        compliance_state_version=inspection.compliance_state_version,
        kill_state_version=inspection.kill_state_version,
    )

    risk = build_first_canary_risk_triple(
        candidate=candidate,
        m27f_bundle=m27f_bundle,
        candidate_exposure=candidate_exposure,
        durable_state=durable_state,
        readiness=readiness,
        safety=safety,
        order_group=order_group,
        versions=versions,
        # First-canary state plus an entirely empty M27F order collection proves
        # there is no existing bot/account order with this newly-derived ID.
        client_order_id_unique=True,
        conflicting_bot_order=False,
        authorization_service_available=authorization_service_available,
        now=now,
    )

    state_view = M27IImmutableStateView(inspection)

    # M27I is intentionally frozen from the earlier reviewed milestone and
    # retains concrete store annotations. Runtime consumption is duck-typed:
    # it uses only the three read methods implemented by this immutable view.
    # Explicit Any confines that legacy annotation mismatch to this boundary
    # without importing or constructing either mutable store class.
    state_view_for_m27i: Any = state_view

    preflight = build_preflight(
        now=now,
        candidate_inputs=candidate_inputs,
        m27f_evidence_path=m27f_evidence_path,
        m27h_evidence_path=m27h_evidence_path,
        public_evidence_path=public_evidence_path,
        m27j_evidence_path=m27j_evidence_path,
        m27a_binding_evidence_path=m27a_binding_evidence_path,
        current_series_fee_observation=current_series_fee_observation,
        current_event_fee_override=current_event_fee_override,
        current_event_fee_observed_at=current_event_fee_observed_at,
        candidate_exposure=candidate_exposure,
        risk_decision=risk.decision,
        risk_intent=risk.intent,
        risk_snapshot=risk.snapshot,
        authorization_store=state_view_for_m27i,
        canary_store=state_view_for_m27i,
    )

    return M27QOrchestratedPreflight(
        software_version=SOFTWARE_VERSION,
        state_inspection=inspection,
        risk=risk,
        preflight=preflight,
    )
