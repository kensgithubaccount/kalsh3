"""M27F -- operator-only live authenticated production read acceptance.

This module never installs the production write credential, never arms production, and
never calls a mutating Kalshi endpoint.  It performs exactly two things, in order:

1. Independent, structural re-validation of a previously generated, secret-free candidate
   authority attestation (:mod:`services.supervised_canary.authority_attestation`) -- it is
   never merely trusted; every field is re-checked here against the exact live policy.
2. GET-only authenticated *portfolio* reads (balance, positions, orders, fills, settlements)
   using the reviewed M25/M27 :class:`KalshiAccountClient` boundary, reused directly rather
   than a second HTTP stack.

Live discovery (2026-08-18, first pass): the least-privilege candidate credential
(``scopes = {"read", "write::trade"}``, ``subaccount = 0``) itself receives ``HTTP 401`` from
``GET /trade-api/v2/api_keys`` -- it is not entitled to enumerate account API-key metadata,
even its own. The earlier design, where this module asked the *candidate* to sign that call
directly (``verify_live_write_credential_authority`` / ``require_live_write_authority``), was
therefore structurally incompatible with the least-privilege candidate it was meant to
validate. This module no longer calls, or accepts a transport capable of calling,
``GET /api_keys`` at all: candidate-authority proof is now produced out of band by a separate
management credential (see :mod:`authority_attestation`) and supplied here only as an
already-rendered, secret-free artifact.

Live discovery (2026-08-18, second pass): with a valid attestation supplied, the same
candidate receives ``HTTP 403`` from ``GET /account/limits``. Kalshi's portfolio endpoints
(``balance``/``positions``/``orders``/``fills``/``settlements``) accept an explicit
``subaccount`` parameter; ``/account/limits`` is account-tier metadata for the authenticated
user with no ``subaccount`` parameter at all, and is not entitled to the least-privilege
candidate. This module therefore never calls ``GET /account/limits``: it is account-level
metadata, not a subaccount-scoped portfolio read, and is out of scope for what this narrow
candidate needs to prove. Subaccount-0 attribution instead comes from the attestation's
server-reported ``subaccount`` plus the structurally fixed ``?subaccount=0`` request paths
:class:`KalshiAccountClient` already uses for every portfolio call -- see
``subaccount_binding_verified`` below.

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
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

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
from services.kalshi_account_gateway.production_read_credentials import (
    ReadSigner,
    read_private_key_fd,
)
from services.production_execution.credentials import REQUIRED_LIVE_WRITE_SUBACCOUNT

from .authority_attestation import validate_attestation_for_candidate

SOFTWARE_VERSION = "kalsh3.m27f.live-read-acceptance/3"
SCHEMA = "kalsh3.m27f.live-read-acceptance.v3"
USER_DATA_FRESHNESS = timedelta(seconds=30)

# The candidate authority proof now always comes from an independently generated,
# out-of-band attestation -- never from the candidate calling GET /api_keys itself.
AUTHORITY_SOURCE = "EXTERNAL_SERVER_ATTESTATION"

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


def _validate_balance_schema(payload: dict[str, Any]) -> None:
    """Prove ``payload`` is a current, well-shaped balance response without storing values.

    Raises :class:`AccountGatewayError` (classified ``SCHEMA_OR_HTTP_FAILURE``, same as any
    other malformed-response rejection) on the first violation. Never records ``balance`` or
    ``portfolio_value`` themselves anywhere -- only that their types were acceptable.
    """
    if isinstance(payload.get("balance"), bool) or not isinstance(payload.get("balance"), int):
        raise AccountGatewayError("balance field must be an integer")
    if isinstance(payload.get("portfolio_value"), bool) or not isinstance(
        payload.get("portfolio_value"), int
    ):
        raise AccountGatewayError("portfolio_value field must be an integer")
    if isinstance(payload.get("updated_ts"), bool) or not isinstance(
        payload.get("updated_ts"), int
    ):
        raise AccountGatewayError("updated_ts field must be an integer")
    breakdown = payload.get("balance_breakdown")
    if breakdown is not None and (
        not isinstance(breakdown, list) or any(not isinstance(item, dict) for item in breakdown)
    ):
        raise AccountGatewayError("balance_breakdown must be an array of objects")


def _validated_balance(client: KalshiAccountClient) -> dict[str, Any]:
    payload = client.get_balance()
    _validate_balance_schema(payload)
    return payload


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
    source: str = AUTHORITY_SOURCE
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
    """M27F-specific reconciliation over the five required subaccount-0 portfolio reads.

    There is deliberately no ``limits_succeeded`` field: ``GET /account/limits`` is
    account-tier metadata with no ``subaccount`` parameter, is not part of this candidate's
    acceptance contract, and is never called (see module docstring). ``subaccount_binding_verified``
    does *not* mean a ``subaccount`` field was found inside a portfolio response body -- none
    of these responses carry one. It means: the independently re-validated authority
    attestation reported ``server_subaccount == 0`` for this exact candidate key, every
    required portfolio read was issued against the structurally fixed ``?subaccount=0``
    request path :class:`KalshiAccountClient` always uses, and every one of those reads
    succeeded. That is the strongest subaccount-0 attribution obtainable from GET-only
    evidence; it is never claimed to be a value independently echoed back by the server.
    """

    classification: str
    balance_succeeded: bool
    open_orders_complete: bool
    positions_complete: bool
    fills_complete: bool
    settlements_complete: bool
    subaccount_binding_verified: bool
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


@dataclass(frozen=True, slots=True)
class TransientAccountFacts:
    """Reduced in-memory facts from the exact M27F authenticated sweep.

    This object is deliberately NOT part of the persisted M27F evidence schema.
    It retains no raw account rows, no key material, and no unvalidated
    ``portfolio_value`` interpretation. Its sole financial value is the
    independently schema-validated available-cash balance converted from cents.

    The four collection counts bind the producer to the exact same portfolio
    sweep represented by ``LiveReadAcceptanceEvidence``. For the first canary,
    downstream risk code may require all four counts to be zero rather than
    attempting to infer liability from previously unseen account activity.
    """

    cash: Decimal
    balance_payload_sha256: str
    position_count: int
    order_count: int
    fill_count: int
    settlement_count: int
    completed_at: datetime

    @property
    def pristine_account_activity(self) -> bool:
        return all(
            count == 0
            for count in (
                self.position_count,
                self.order_count,
                self.fill_count,
                self.settlement_count,
            )
        )


@dataclass(frozen=True, slots=True)
class LiveReadAcceptanceBundle:
    """M27F evidence plus optional transient account facts from that same sweep."""

    evidence: LiveReadAcceptanceEvidence
    account_facts: TransientAccountFacts | None


def _build_transient_account_facts(
    *,
    balance: object,
    positions: object,
    orders: object,
    fills: object,
    settlements: object,
    completed_at: datetime,
) -> TransientAccountFacts:
    if not isinstance(balance, dict):
        raise AccountGatewayError("validated balance payload disappeared")
    collections = (positions, orders, fills, settlements)
    if any(not isinstance(value, list) for value in collections):
        raise AccountGatewayError("validated portfolio collection disappeared")

    raw_cash = balance.get("balance")
    if isinstance(raw_cash, bool) or not isinstance(raw_cash, int) or raw_cash < 0:
        raise AccountGatewayError("balance cannot produce conservative cash facts")

    return TransientAccountFacts(
        cash=Decimal(raw_cash) / Decimal(100),
        balance_payload_sha256=_hash_json(balance),
        position_count=len(cast(list[object], positions)),
        order_count=len(cast(list[object], orders)),
        fill_count=len(cast(list[object], fills)),
        settlement_count=len(cast(list[object], settlements)),
        completed_at=completed_at,
    )


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
    authority: CandidateAuthorityResult,
    started_at: datetime,
    completed_at: datetime,
) -> ReconciliationResult:
    """Reconcile the five required subaccount-0 portfolio reads. Never calls ``/account/limits``.

    Only invoked once the caller has already confirmed ``authority.succeeded`` -- see
    :func:`run_live_read_acceptance`, which returns a ``BLOCKED`` result directly, without
    calling this function or attempting any read, when the attestation itself fails. The
    ``authority.succeeded`` check below is therefore always true in practice; it is kept so
    ``subaccount_binding_verified`` documents its own derivation in full rather than relying on
    an invariant enforced only by the caller.
    """
    balance = _read_lookup(reads, "balance")
    orders = _read_lookup(reads, "orders")
    positions = _read_lookup(reads, "positions")
    fills = _read_lookup(reads, "fills")
    settlements = _read_lookup(reads, "settlements")
    balance_ok = balance is not None and balance.succeeded
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
    all_reads_complete = all((balance_ok, orders_ok, positions_ok, fills_ok, settlements_ok))
    subaccount_binding_verified = (
        authority.succeeded
        and authority.server_subaccount == REQUIRED_LIVE_WRITE_SUBACCOUNT
        and all_reads_complete
    )
    if not all_reads_complete:
        classification = "BLOCKED"
        reason: str | None = "one or more required authenticated portfolio reads did not complete"
    elif not fresh:
        classification = "FAIL"
        reason = "evidence exceeded the 30 second user-data freshness bound"
    elif not subaccount_binding_verified:
        classification = "FAIL"
        reason = "candidate was not verifiably bound to subaccount 0"
    else:
        classification = "PASS"
        reason = None
    return ReconciliationResult(
        classification,
        balance_ok,
        orders_ok,
        positions_ok,
        fills_ok,
        settlements_ok,
        subaccount_binding_verified,
        fresh,
        reason,
    )


def run_live_read_acceptance_bundle(
    *,
    key_id: str,
    private_key_pem: bytes,
    authority_attestation: object,
    account_transport: ReadTransport,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
    signer_factory: Callable[[str, bytes], ReadSigner] = RequestSigner,
) -> LiveReadAcceptanceBundle:
    """Perform one M27F sweep and retain only reduced transient account facts.

    The persisted evidence shape is exactly ``LiveReadAcceptanceEvidence``.
    ``TransientAccountFacts`` never becomes part of that JSON artifact and
    carries no raw account rows. No extra network read is performed.
    """
    started_at = clock()
    key_id_hash = hashlib.sha256(key_id.encode()).hexdigest()
    validation = validate_attestation_for_candidate(
        authority_attestation,
        candidate_key_id=key_id,
    )

    if not validation.succeeded:
        completed_at = clock()
        authority = CandidateAuthorityResult(
            "FAIL",
            key_id_hash,
            validation.server_scopes,
            validation.server_subaccount,
            started_at,
            completed_at,
            reason=validation.reason,
        )
        evidence = LiveReadAcceptanceEvidence(
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
                reason="candidate authority attestation did not pass",
            ),
        )
        return LiveReadAcceptanceBundle(evidence, None)

    authority = CandidateAuthorityResult(
        "PASS",
        key_id_hash,
        validation.server_scopes,
        validation.server_subaccount,
        started_at,
        clock(),
    )

    signer = signer_factory(key_id, private_key_pem)
    client = KalshiAccountClient(
        signer,
        account_transport,
        clock_ms=clock_ms,
        max_retries=0,
    )

    reads: list[EndpointReadResult] = []

    balance = _attempt_read(
        reads,
        "balance",
        lambda: _validated_balance(client),
        clock,
    )
    positions = _attempt_read(
        reads,
        "positions",
        lambda: client.get_collection("positions"),
        clock,
    )
    orders = _attempt_read(
        reads,
        "orders",
        lambda: client.get_collection("orders"),
        clock,
    )
    fills = _attempt_read(
        reads,
        "fills",
        lambda: client.get_collection("fills"),
        clock,
    )
    settlements = _attempt_read(
        reads,
        "settlements",
        lambda: client.get_collection("settlements"),
        clock,
    )

    completed_at = clock()
    reconciliation = _reconcile(
        tuple(reads),
        authority,
        started_at,
        completed_at,
    )

    evidence = LiveReadAcceptanceEvidence(
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

    facts: TransientAccountFacts | None = None
    if reconciliation.succeeded:
        facts = _build_transient_account_facts(
            balance=balance,
            positions=positions,
            orders=orders,
            fills=fills,
            settlements=settlements,
            completed_at=completed_at,
        )

        balance_read = _read_lookup(tuple(reads), "balance")
        if (
            balance_read is None
            or balance_read.payload_sha256 is None
            or facts.balance_payload_sha256 != balance_read.payload_sha256
        ):
            raise AccountGatewayError(
                "transient account facts do not bind to M27F balance evidence"
            )

    return LiveReadAcceptanceBundle(evidence, facts)


def run_live_read_acceptance(
    *,
    key_id: str,
    private_key_pem: bytes,
    authority_attestation: object,
    account_transport: ReadTransport,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
    signer_factory: Callable[[str, bytes], ReadSigner] = RequestSigner,
) -> LiveReadAcceptanceEvidence:
    """Compatibility wrapper returning the unchanged secret-free M27F artifact."""
    return run_live_read_acceptance_bundle(
        key_id=key_id,
        private_key_pem=private_key_pem,
        authority_attestation=authority_attestation,
        account_transport=account_transport,
        clock=clock,
        clock_ms=clock_ms,
        signer_factory=signer_factory,
    ).evidence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "M27F operator-only live authenticated production read acceptance. GET-only: "
            "never installs a write credential, never arms production, never mutates. Never "
            "calls GET /api_keys itself -- consumes a pre-generated candidate authority "
            "attestation instead (see authority_attestation.py)."
        )
    )
    parser.add_argument("--key-id-file", required=True, type=Path)
    parser.add_argument("--private-key-fd", required=True, type=int)
    parser.add_argument("--authority-attestation", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        key_id = args.key_id_file.read_text().strip()
        if not key_id:
            raise ValueError("key id file is empty")
        private_key_pem = read_private_key_fd(args.private_key_fd)
        authority_attestation = json.loads(args.authority_attestation.read_text())
        evidence = run_live_read_acceptance(
            key_id=key_id,
            private_key_pem=private_key_pem,
            authority_attestation=authority_attestation,
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
