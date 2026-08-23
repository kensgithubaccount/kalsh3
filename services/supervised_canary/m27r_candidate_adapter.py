"""Candidate-gated authenticated evidence adapter for M27R.

This module may be invoked only after the M27R coordinator has selected exactly one M27D
experimental candidate from independently validated public evidence. It reuses the reviewed M27F
GET-only account sweep and candidate-exposure check; it owns no mutation or execution authority.

The returned evidence explicitly carries the exact candidate ID and market ticker supplied to the
adapter so the coordinator can reject a provider that returns evidence for another candidate.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from services.kalshi_account_gateway.auth import RequestSigner
from services.kalshi_account_gateway.client import KalshiAccountClient, ReadTransport
from services.kalshi_account_gateway.production_read_credentials import ReadSigner
from services.risk_engine.domain import RequiredOrderGroupPolicy
from services.risk_engine.invariants import NewRiskReadiness

from .candidate_exposure_check import check_candidate_market_exposure
from .live_read_acceptance import run_live_read_acceptance_bundle
from .m27d import ExperimentalCandidate
from .m27r_operator_runner import Clock, M27RCandidateEvidence

SOFTWARE_VERSION = "kalsh3.m27r.candidate-evidence-adapter/3"

CredentialLoader = Callable[[], tuple[str, bytes]]
AuthorityAttestationLoader = Callable[[], object]
ReadTransportFactory = Callable[[], ReadTransport]


class M27RCandidateAdapterError(RuntimeError):
    """Candidate-specific evidence could not be assembled without weakening a gate."""


def _clock_now(clock: Clock, *, field: str) -> None:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise M27RCandidateAdapterError(f"{field} must be timezone-aware")


def _persist_m27f_evidence(*, payload: dict[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")


@dataclass(frozen=True, slots=True, repr=False)
class GetOnlyCandidateEvidenceProvider:
    credential_loader: CredentialLoader
    authority_attestation_loader: AuthorityAttestationLoader
    account_transport_factory: ReadTransportFactory
    m27f_evidence_path: Path
    m27h_evidence_path: Path
    state_path: Path
    readiness: NewRiskReadiness
    order_group: RequiredOrderGroupPolicy
    authorization_service_available: bool
    signer_factory: Callable[[str, bytes], ReadSigner] = RequestSigner
    clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000

    def collect_candidate_evidence(
        self,
        *,
        candidate: ExperimentalCandidate,
        clock: Clock,
    ) -> M27RCandidateEvidence:
        _clock_now(clock, field="candidate evidence start clock")

        if not self.m27h_evidence_path.is_file():
            raise M27RCandidateAdapterError("fresh M27H evidence path is unavailable")

        key_id, private_key_pem = self.credential_loader()
        if not key_id or not isinstance(private_key_pem, bytes) or not private_key_pem:
            raise M27RCandidateAdapterError(
                "candidate read credential loader returned invalid data"
            )

        authority_attestation = self.authority_attestation_loader()
        transport = self.account_transport_factory()
        bundle = run_live_read_acceptance_bundle(
            key_id=key_id,
            private_key_pem=private_key_pem,
            authority_attestation=authority_attestation,
            account_transport=transport,
            clock=clock,
            clock_ms=self.clock_ms,
            signer_factory=self.signer_factory,
        )
        _persist_m27f_evidence(
            payload=bundle.evidence.to_json(),
            path=self.m27f_evidence_path,
        )

        if not bundle.evidence.reconciliation.succeeded or bundle.account_facts is None:
            raise M27RCandidateAdapterError("M27F authenticated GET sweep did not reconcile")

        signer = self.signer_factory(key_id, private_key_pem)
        client = KalshiAccountClient(
            signer,
            transport,
            clock_ms=self.clock_ms,
            max_retries=0,
        )
        exposure = check_candidate_market_exposure(
            client=client,
            market_ticker=candidate.market_ticker,
            clock=clock,
        )
        if not exposure.succeeded:
            raise M27RCandidateAdapterError(
                "candidate-specific authenticated exposure check did not pass"
            )
        if exposure.market_ticker != candidate.market_ticker:
            raise M27RCandidateAdapterError(
                "candidate-specific exposure check returned a different market"
            )

        return M27RCandidateEvidence(
            candidate_id=candidate.candidate_id,
            market_ticker=candidate.market_ticker,
            m27f_bundle=bundle,
            m27f_evidence_path=self.m27f_evidence_path,
            m27h_evidence_path=self.m27h_evidence_path,
            candidate_exposure=exposure,
            state_path=self.state_path,
            readiness=self.readiness,
            order_group=self.order_group,
            authorization_service_available=self.authorization_service_available,
        )
