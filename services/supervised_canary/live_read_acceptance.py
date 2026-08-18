"""M27F -- operator-only live authenticated production read acceptance.

This module never installs the production write credential, never arms production, and
never calls a mutating Kalshi endpoint.  It performs exactly two things, in order:

1. The reviewed M27E candidate-authority check (``verify_live_write_credential_authority`` /
   ``require_live_write_authority``) against ``GET /trade-api/v2/api_keys`` -- unchanged and
   reused as-is.
2. GET-only authenticated account reads (balance, limits, positions, orders, fills,
   settlements) using the reviewed M25/M27 :class:`KalshiAccountClient` boundary, reused
   directly rather than a second HTTP stack.

The candidate private key is only ever accepted through an inherited file descriptor.  It is
never written to argv, an environment variable, a log, an exception message, or the evidence
artifact produced here.  The evidence artifact is secret-free: hashes and counts only, never
raw account content, and never the PEM.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from services.kalshi_account_gateway.auth import RequestSigner
from services.kalshi_account_gateway.client import (
    AccountGatewayError,
    AuthenticationRejected,
    KalshiAccountClient,
    PaginationError,
    RateLimited,
    ReadTransport,
    UpstreamUnavailable,
    UrllibReadTransport,
)
from services.kalshi_account_gateway.models import AccountSnapshot, SnapshotValidationError
from services.kalshi_account_gateway.production_read_credentials import (
    ProductionCredentialError,
    ProductionReadTransport,
    ReadSigner,
    UrllibProductionReadTransport,
    read_private_key_fd,
)
from services.production_execution.credentials import (
    REQUIRED_LIVE_WRITE_SCOPES,
    REQUIRED_LIVE_WRITE_SUBACCOUNT,
    ProductionWriteCredential,
)
from services.production_execution.enrollment import (
    WriteCredentialAuthorityError,
    require_live_write_authority,
    verify_live_write_credential_authority,
)

SOFTWARE_VERSION = "kalsh3.m27f.live-read-acceptance/1"
SCHEMA = "kalsh3.m27f.live-read-acceptance.v1"
USER_DATA_FRESHNESS = timedelta(seconds=30)

# Failures from the candidate-authority boundary itself: the M27E-reviewed metadata check
# (WriteCredentialAuthorityError) and any concrete transport-boundary failure such as an
# oversized response (ProductionCredentialError). Both carry only generic, secret-free text.
_AUTHORITY_FAILURES = (WriteCredentialAuthorityError, ProductionCredentialError)

# Failures from an individual authenticated account read; each is classified separately so a
# failure on one endpoint never disguises itself as another endpoint's empty success.
_READ_FAILURES: dict[type[Exception], str] = {
    AuthenticationRejected: "AUTH_FAILURE",
    RateLimited: "RATE_LIMITED",
    UpstreamUnavailable: "UPSTREAM_UNAVAILABLE",
    PaginationError: "PAGINATION_FAILURE",
    AccountGatewayError: "SCHEMA_OR_HTTP_FAILURE",
}


def _hash_json(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class EndpointReadResult:
    name: str
    classification: str
    started_at: datetime
    completed_at: datetime
    count: int | None = None
    pagination_complete: bool | None = None
    payload_sha256: str | None = None
    reason: str | None = None

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["started_at"] = self.started_at.isoformat()
        payload["completed_at"] = self.completed_at.isoformat()
        return payload

    @property
    def succeeded(self) -> bool:
        return self.classification == "SUCCESS"


@dataclass(frozen=True, slots=True)
class CandidateAuthorityResult:
    classification: str
    key_id_hash: str
    server_scopes: tuple[str, ...] | None
    server_subaccount: int | None
    started_at: datetime
    completed_at: datetime
    reason: str | None = None

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["started_at"] = self.started_at.isoformat()
        payload["completed_at"] = self.completed_at.isoformat()
        if self.server_scopes is not None:
            payload["server_scopes"] = list(self.server_scopes)
        return payload

    @property
    def succeeded(self) -> bool:
        return self.classification == "PASS"


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    classification: str
    balance_succeeded: bool
    limits_succeeded: bool
    open_orders_complete: bool
    positions_complete: bool
    fills_complete: bool
    settlements_complete: bool
    subaccount_consistent: bool
    fresh: bool
    reason: str | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def succeeded(self) -> bool:
        return self.classification == "PASS"


@dataclass(frozen=True, slots=True)
class LiveReadAcceptanceEvidence:
    schema: str
    software_version: str
    environment: str
    subaccount: int
    key_id_hash: str
    started_at: datetime
    completed_at: datetime
    candidate_authority: CandidateAuthorityResult
    reads: tuple[EndpointReadResult, ...]
    reconciliation: ReconciliationResult

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "software_version": self.software_version,
            "environment": self.environment,
            "subaccount": self.subaccount,
            "key_id_hash": self.key_id_hash,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "candidate_authority": self.candidate_authority.to_json(),
            "reads": [item.to_json() for item in self.reads],
            "reconciliation": self.reconciliation.to_json(),
        }


def _attempt_read(
    reads: list[EndpointReadResult],
    name: str,
    action: Callable[[], Any],
    clock: Callable[[], datetime],
) -> Any | None:
    started_at = clock()
    try:
        payload = action()
    except tuple(_READ_FAILURES) as exc:
        classification = next(
            value for klass, value in _READ_FAILURES.items() if isinstance(exc, klass)
        )
        reads.append(EndpointReadResult(name, classification, started_at, clock(), reason=str(exc)))
        return None
    completed_at = clock()
    if isinstance(payload, list):
        reads.append(
            EndpointReadResult(
                name,
                "SUCCESS",
                started_at,
                completed_at,
                count=len(payload),
                pagination_complete=True,
                payload_sha256=_hash_json(payload),
            )
        )
    else:
        reads.append(
            EndpointReadResult(
                name, "SUCCESS", started_at, completed_at, payload_sha256=_hash_json(payload)
            )
        )
    return payload


def _read_lookup(reads: tuple[EndpointReadResult, ...], name: str) -> EndpointReadResult | None:
    for item in reads:
        if item.name == name:
            return item
    return None


def _reconcile(
    reads: tuple[EndpointReadResult, ...],
    snapshot: AccountSnapshot | None,
    snapshot_error: str | None,
    started_at: datetime,
    completed_at: datetime,
) -> ReconciliationResult:
    balance = _read_lookup(reads, "balance")
    limits = _read_lookup(reads, "limits")
    orders = _read_lookup(reads, "orders")
    positions = _read_lookup(reads, "positions")
    fills = _read_lookup(reads, "fills")
    settlements = _read_lookup(reads, "settlements")
    balance_ok = balance is not None and balance.succeeded
    limits_ok = limits is not None and limits.succeeded
    orders_ok = orders is not None and orders.succeeded and orders.pagination_complete is True
    positions_ok = (
        positions is not None and positions.succeeded and positions.pagination_complete is True
    )
    fills_ok = fills is not None and fills.succeeded and fills.pagination_complete is True
    settlements_ok = (
        settlements is not None
        and settlements.succeeded
        and settlements.pagination_complete is True
    )
    fresh = completed_at - started_at <= USER_DATA_FRESHNESS
    subaccount_consistent = snapshot is not None and snapshot.subaccount == 0
    all_reads_complete = all(
        (balance_ok, limits_ok, orders_ok, positions_ok, fills_ok, settlements_ok)
    )
    if snapshot_error is not None:
        classification = "FAIL"
        reason: str | None = snapshot_error
    elif not all_reads_complete:
        classification = "BLOCKED"
        reason = "one or more required authenticated reads did not complete"
    elif not fresh:
        classification = "FAIL"
        reason = "evidence exceeded the 30 second user-data freshness bound"
    elif not subaccount_consistent:
        classification = "FAIL"
        reason = "account data was not attributable to subaccount 0"
    else:
        classification = "PASS"
        reason = None
    return ReconciliationResult(
        classification,
        balance_ok,
        limits_ok,
        orders_ok,
        positions_ok,
        fills_ok,
        settlements_ok,
        subaccount_consistent,
        fresh,
        reason,
    )


def run_live_read_acceptance(
    *,
    key_id: str,
    private_key_pem: bytes,
    authority_transport: ProductionReadTransport,
    account_transport: ReadTransport,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
    signer_factory: Callable[[str, bytes], ReadSigner] = RequestSigner,
) -> LiveReadAcceptanceEvidence:
    """Verify candidate write-credential authority, then perform bounded GET-only reads.

    Never installs a credential, never arms production, and never issues a mutating call.
    A failed authenticated read is never reinterpreted as a successful empty account.
    """
    started_at = clock()
    key_id_hash = hashlib.sha256(key_id.encode()).hexdigest()
    credential = ProductionWriteCredential(
        key_id=key_id,
        private_key_pem=private_key_pem,
        scopes=REQUIRED_LIVE_WRITE_SCOPES,
    )
    try:
        proof = verify_live_write_credential_authority(
            authority_transport,
            credential,
            timestamp_ms=clock_ms(),
            signer_factory=signer_factory,
        )
    except _AUTHORITY_FAILURES as exc:
        completed_at = clock()
        authority = CandidateAuthorityResult(
            "FAIL", key_id_hash, None, None, started_at, completed_at, reason=str(exc)
        )
        return LiveReadAcceptanceEvidence(
            SCHEMA,
            SOFTWARE_VERSION,
            "PRODUCTION",
            REQUIRED_LIVE_WRITE_SUBACCOUNT,
            key_id_hash,
            started_at,
            completed_at,
            authority,
            (),
            ReconciliationResult(
                "BLOCKED",
                False,
                False,
                False,
                False,
                False,
                False,
                False,
                False,
                reason="candidate authority check did not pass",
            ),
        )
    try:
        require_live_write_authority(proof, expected_key_id=credential.key_id)
    except WriteCredentialAuthorityError as exc:
        completed_at = clock()
        authority = CandidateAuthorityResult(
            "FAIL",
            key_id_hash,
            tuple(sorted(proof.scopes)),
            proof.subaccount,
            started_at,
            completed_at,
            reason=str(exc),
        )
        return LiveReadAcceptanceEvidence(
            SCHEMA,
            SOFTWARE_VERSION,
            "PRODUCTION",
            REQUIRED_LIVE_WRITE_SUBACCOUNT,
            key_id_hash,
            started_at,
            completed_at,
            authority,
            (),
            ReconciliationResult(
                "BLOCKED",
                False,
                False,
                False,
                False,
                False,
                False,
                False,
                False,
                reason="candidate authority check did not pass",
            ),
        )
    authority = CandidateAuthorityResult(
        "PASS",
        key_id_hash,
        tuple(sorted(proof.scopes)),
        proof.subaccount,
        started_at,
        clock(),
    )

    signer = signer_factory(key_id, private_key_pem)
    client = KalshiAccountClient(signer, account_transport, clock_ms=clock_ms, max_retries=0)
    reads: list[EndpointReadResult] = []
    balance_payload = _attempt_read(reads, "balance", client.get_balance, clock)
    limits_payload = _attempt_read(reads, "limits", client.get_limits, clock)
    positions_payload = _attempt_read(
        reads, "positions", lambda: client.get_collection("positions"), clock
    )
    orders_payload = _attempt_read(reads, "orders", lambda: client.get_collection("orders"), clock)
    fills_payload = _attempt_read(reads, "fills", lambda: client.get_collection("fills"), clock)
    settlements_payload = _attempt_read(
        reads, "settlements", lambda: client.get_collection("settlements"), clock
    )

    snapshot: AccountSnapshot | None = None
    snapshot_error: str | None = None
    if all(
        payload is not None
        for payload in (
            balance_payload,
            limits_payload,
            positions_payload,
            orders_payload,
            fills_payload,
            settlements_payload,
        )
    ):
        try:
            snapshot = AccountSnapshot.from_payloads(
                {
                    "balance": balance_payload,
                    "limits": limits_payload,
                    "positions": positions_payload,
                    "orders": orders_payload,
                    "fills": fills_payload,
                    "settlements": settlements_payload,
                },
                clock(),
                client.read_budget,
            )
        except SnapshotValidationError as exc:
            snapshot_error = str(exc)

    completed_at = clock()
    reconciliation = _reconcile(tuple(reads), snapshot, snapshot_error, started_at, completed_at)
    return LiveReadAcceptanceEvidence(
        SCHEMA,
        SOFTWARE_VERSION,
        "PRODUCTION",
        REQUIRED_LIVE_WRITE_SUBACCOUNT,
        key_id_hash,
        started_at,
        completed_at,
        authority,
        tuple(reads),
        reconciliation,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "M27F operator-only live authenticated production read acceptance. GET-only: "
            "never installs a write credential, never arms production, never mutates."
        )
    )
    parser.add_argument("--key-id-file", required=True, type=Path)
    parser.add_argument("--private-key-fd", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        key_id = args.key_id_file.read_text().strip()
        if not key_id:
            raise ValueError("key id file is empty")
        private_key_pem = read_private_key_fd(args.private_key_fd)
        evidence = run_live_read_acceptance(
            key_id=key_id,
            private_key_pem=private_key_pem,
            authority_transport=UrllibProductionReadTransport(),
            account_transport=UrllibReadTransport(),
        )
    except Exception as exc:  # boundary: never leak details, only a sanitized class name
        print(f"BLOCKER: M27F live read acceptance failed ({type(exc).__name__})", file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence.to_json(), sort_keys=True, indent=2) + "\n")
    print(
        f"candidate_authority={evidence.candidate_authority.classification} "
        f"reconciliation={evidence.reconciliation.classification}"
    )
    print("PRODUCTION_WRITE_CREDENTIAL: NOT INSTALLED  PRODUCTION_ARMED: DISARMED")
    return 0 if evidence.reconciliation.succeeded else 2


if __name__ == "__main__":
    raise SystemExit(main())
