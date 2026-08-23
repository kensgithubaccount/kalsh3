"""Candidate-gated authenticated evidence adapter for M27R.

This module is intentionally narrower than an operator CLI. It may be invoked only after
:func:`m27r_operator_runner.run_readonly_operator_preflight` has already selected exactly one
M27D experimental candidate from public evidence.

It reuses the reviewed M27F authenticated GET sweep and M27I candidate-exposure check. The
adapter has no mutation transport, no order sender, no M13/M16/M27O authority capability, and
never reads the protected production write-credential store. M27H remains an independently
produced, operator-only local evidence artifact; this adapter merely carries its path onward to
M27Q/M27I, which independently validates freshness and structure.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from services.kalshi_account_gateway.auth import RequestSigner
from services.kalshi_account_gateway.client import KalshiAccountClient, ReadTransport
from services.kalshi_account_gateway.production_read_credentials import ReadSigner
from services.risk_engine.domain import RequiredOrderGroupPolicy
from services.risk_engine.invariants import NewRiskReadiness

from .candidate_exposure_check import check_candidate_market_exposure
from .live_read_acceptance import run_live_read_acceptance_bundle
from .m27d import ExperimentalCandidate
from .m27r_operator_runner import M27RCandidateEvidence

SOFTWARE_VERSION = "kalsh3.m27r.candidate-evidence-adapter/1"

CredentialLoader = Callable[[], tuple[str, bytes]]
AuthorityAttestationLoader = Callable[[], object]
ReadTransportFactory = Callable[[], ReadTransport]


class M27RCandidateAdapterError(RuntimeError):
    """Candidate-specific evidence could not be assembled without weakening a gate."""


def _require_aware(value: datetime, *, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise M27RCandidateAdapterError(f"{field} must be timezone-aware")


def _persist_m27f_evidence(*, payload: dict[str, object], path: Path) -> None:
    """Persist exactly the secret-free M27F evidence that belongs to this sweep."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")


@dataclass(frozen=True, slots=True, repr=False)
class GetOnlyCandidateEvidenceProvider:
    """Concrete M27R candidate provider with deferred credential access.

    ``credential_loader`` is deliberately called inside ``collect_candidate_evidence`` rather
    than at construction time. The M27R coordinator therefore cannot touch authenticated
    credentials unless the unchanged M27D selector has first produced exactly one qualifying
    candidate.
    """

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
        now: datetime,
    ) -> M27RCandidateEvidence:
        _require_aware(now, field="candidate evidence clock")

        # M27H is operator-only evidence. Never invoke its protected-store verifier here.
        if not self.m27h_evidence_path.is_file():
            raise M27RCandidateAdapterError("fresh M27H evidence path is unavailable")

        # Deferred until after the exact-one public candidate gate.
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
            clock=lambda: now.astimezone(UTC),
            clock_ms=self.clock_ms,
            signer_factory=self.signer_factory,
        )
        _persist_m27f_evidence(
            payload=bundle.evidence.to_json(),
            path=self.m27f_evidence_path,
        )

        if not bundle.evidence.reconciliation.succeeded or bundle.account_facts is None:
            raise M27RCandidateAdapterError("M27F authenticated GET sweep did not reconcile")

        # Candidate exposure is intentionally a second fresh GET-only read of orders/positions.
        # It answers a different question than M27F's account-wide hashes/counts: whether this
        # exact selected market has an open order or non-zero position right now.
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
            clock=lambda: now.astimezone(UTC),
        )
        if not exposure.succeeded:
            raise M27RCandidateAdapterError(
                "candidate-specific authenticated exposure check did not pass"
            )

        return M27RCandidateEvidence(
            m27f_bundle=bundle,
            m27f_evidence_path=self.m27f_evidence_path,
            m27h_evidence_path=self.m27h_evidence_path,
            candidate_exposure=exposure,
            state_path=self.state_path,
            readiness=self.readiness,
            order_group=self.order_group,
            authorization_service_available=self.authorization_service_available,
        )
