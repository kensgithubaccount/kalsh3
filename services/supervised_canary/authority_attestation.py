"""M27F -- operator-only candidate-authority attestation generator.

Live discovery (2026-08-18): the least-privilege candidate credential
(``scopes = {"read", "write::trade"}``, ``subaccount = 0``) receives
``HTTP 401`` from ``GET /trade-api/v2/api_keys`` when it authenticates as
itself. It is not entitled to enumerate account API-key metadata, even its
own -- that surface is reserved to a broader account-management identity.
The M27E/M27F design that had the candidate sign its own ``GET /api_keys``
call to prove its own scopes was therefore structurally incompatible with
the least-privilege candidate it was meant to validate.

This module is the separate, narrower half of the fix: a *management*
credential (a broader, separately authorized bootstrap key -- never the
candidate's) performs exactly one GET-only call, ``GET /trade-api/v2/api_keys``,
locates the expected candidate by key ID, and produces a secret-free
"authority attestation" artifact recording the candidate's server-reported
scopes/subaccount and a unique-match count. It never receives, needs, or
touches the candidate's private key -- only the candidate's (non-secret) key
ID, supplied as plain text. This keeps the broad management credential and
the narrow candidate credential out of the same process by construction.

The artifact this module produces is consumed -- and independently
re-validated, not merely trusted -- by
:mod:`services.supervised_canary.live_read_acceptance`, which no longer asks
the candidate to call ``GET /api_keys`` at all.

No raw management key ID, no raw management private key, no raw candidate key
ID, no signature, no header, and no account data ever appears in the
attestation artifact or in any exception message raised here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from services.kalshi_account_gateway.auth import RequestSigner
from services.kalshi_account_gateway.candidate_authority import (
    CANDIDATE_AUTHORITY_ATTESTATION_SCHEMA as SCHEMA,
)
from services.kalshi_account_gateway.candidate_authority import (
    KNOWN_KALSHI_API_KEY_SCOPES,
    AttestationValidation,
    validate_candidate_authority_attestation,
)
from services.kalshi_account_gateway.production_read_credentials import (
    API_KEYS_PATH,
    PRODUCTION_ORIGIN,
    ProductionReadReply,
    ProductionReadTransport,
    ReadSigner,
    UrllibProductionReadTransport,
    read_private_key_fd,
)
from services.production_execution.credentials import (
    REQUIRED_LIVE_WRITE_SCOPES,
    REQUIRED_LIVE_WRITE_SUBACCOUNT,
)

SOFTWARE_VERSION = "kalsh3.m27f.candidate-authority/1"
ENVIRONMENT = "PRODUCTION"

# The classification this attestation asserts about the candidate. "PASS" means the
# management credential's own GET /api_keys uniquely identified a candidate key holding
# exactly {"read", "write::trade"} restricted to subaccount 0. Anything else is "FAIL".


@dataclass(frozen=True, slots=True)
class CandidateAuthorityAttestation:
    """Secret-free proof of one candidate key's server-reported authority.

    Every field here is either non-secret identity/classification metadata or a hash --
    never a raw key ID, private key, signature, header, or account value.
    """

    schema: str
    software_version: str
    environment: str
    observed_at: datetime
    source_origin: str
    source_path: str
    classification: str
    key_id_hash: str
    server_scopes: tuple[str, ...] | None
    server_subaccount: int | None
    unique_matches: int
    reason: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "software_version": self.software_version,
            "environment": self.environment,
            "observed_at": self.observed_at.isoformat(),
            "source": {"origin": self.source_origin, "path": self.source_path},
            "classification": self.classification,
            "candidate": {
                "key_id_hash": self.key_id_hash,
                "server_scopes": (
                    list(self.server_scopes) if self.server_scopes is not None else None
                ),
                "server_subaccount": self.server_subaccount,
                "unique_matches": self.unique_matches,
            },
            "reason": self.reason,
        }

    @property
    def succeeded(self) -> bool:
        return self.classification == "PASS"


def generate_candidate_authority_attestation(
    *,
    management_key_id: str,
    management_private_key_pem: bytes,
    candidate_key_id: str,
    transport: ProductionReadTransport,
    timestamp_ms: int,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    signer_factory: Callable[[str, bytes], ReadSigner] = RequestSigner,
) -> CandidateAuthorityAttestation:
    """Authenticate as the management credential and attest to one candidate's authority.

    This is the only place the management private key is used. It signs exactly one GET
    request; the transport parameter is structurally GET-only
    (:class:`ProductionReadTransport`), so no mutating call is even expressible here. The
    candidate's private key is never an input to this function -- only its (non-secret) key
    ID, used solely to locate the matching server record.
    """
    observed_at = clock()
    key_id_hash = hashlib.sha256(candidate_key_id.encode()).hexdigest()
    signer = signer_factory(management_key_id, management_private_key_pem)
    headers = signer.headers(timestamp_ms, "GET", API_KEYS_PATH)
    try:
        reply = transport.get(PRODUCTION_ORIGIN, API_KEYS_PATH, headers, timeout_seconds=10)
    except (TimeoutError, OSError):
        return _fail(observed_at, key_id_hash, "management credential transport failed")
    return _classify(reply, candidate_key_id, key_id_hash, observed_at)


def _fail(
    observed_at: datetime,
    key_id_hash: str,
    reason: str,
    *,
    scopes: tuple[str, ...] | None = None,
    subaccount: int | None = None,
    unique_matches: int = 0,
) -> CandidateAuthorityAttestation:
    return CandidateAuthorityAttestation(
        SCHEMA,
        SOFTWARE_VERSION,
        ENVIRONMENT,
        observed_at,
        PRODUCTION_ORIGIN,
        API_KEYS_PATH,
        "FAIL",
        key_id_hash,
        scopes,
        subaccount,
        unique_matches,
        reason=reason,
    )


def _classify(
    reply: ProductionReadReply,
    candidate_key_id: str,
    key_id_hash: str,
    observed_at: datetime,
) -> CandidateAuthorityAttestation:
    if reply.location is not None or 300 <= reply.status < 400:
        return _fail(observed_at, key_id_hash, "management credential response redirect rejected")
    if reply.status in {401, 403}:
        return _fail(observed_at, key_id_hash, "management credential authentication rejected")
    if reply.status != 200 or reply.content_type != "application/json":
        return _fail(observed_at, key_id_hash, "management credential response rejected")
    try:
        payload = json.loads(reply.body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _fail(observed_at, key_id_hash, "management credential response malformed")
    if not isinstance(payload, dict) or not isinstance(payload.get("api_keys"), list):
        return _fail(observed_at, key_id_hash, "management credential response malformed")
    records = payload["api_keys"]
    if any(not isinstance(item, dict) for item in records):
        return _fail(observed_at, key_id_hash, "management credential response malformed")
    matches = [item for item in records if item.get("api_key_id") == candidate_key_id]
    if len(matches) != 1:
        return _fail(
            observed_at,
            key_id_hash,
            "candidate key not uniquely identified",
            unique_matches=len(matches),
        )
    match = matches[0]
    raw_scopes = match.get("scopes")
    if (
        not isinstance(raw_scopes, list)
        or not raw_scopes
        or any(type(item) is not str for item in raw_scopes)
        or len(raw_scopes) != len(set(raw_scopes))
        or not set(raw_scopes) <= KNOWN_KALSHI_API_KEY_SCOPES
    ):
        return _fail(
            observed_at, key_id_hash, "candidate key metadata scopes malformed", unique_matches=1
        )
    scopes = tuple(sorted(raw_scopes))
    subaccount: int | None = match.get("subaccount")
    if subaccount is not None and (type(subaccount) is bool or type(subaccount) is not int):
        return _fail(
            observed_at,
            key_id_hash,
            "candidate key metadata subaccount malformed",
            scopes=scopes,
            unique_matches=1,
        )
    if subaccount is not None and not 0 <= subaccount <= 63:
        return _fail(
            observed_at,
            key_id_hash,
            "candidate key metadata subaccount out of range",
            scopes=scopes,
            unique_matches=1,
        )
    if frozenset(raw_scopes) != REQUIRED_LIVE_WRITE_SCOPES:
        return _fail(
            observed_at,
            key_id_hash,
            "candidate key does not hold exactly read and write::trade",
            scopes=scopes,
            subaccount=subaccount,
            unique_matches=1,
        )
    if subaccount != REQUIRED_LIVE_WRITE_SUBACCOUNT:
        return _fail(
            observed_at,
            key_id_hash,
            "candidate key is not restricted to subaccount 0",
            scopes=scopes,
            subaccount=subaccount,
            unique_matches=1,
        )
    return CandidateAuthorityAttestation(
        SCHEMA,
        SOFTWARE_VERSION,
        ENVIRONMENT,
        observed_at,
        PRODUCTION_ORIGIN,
        API_KEYS_PATH,
        "PASS",
        key_id_hash,
        scopes,
        subaccount,
        1,
        reason=None,
    )


def validate_attestation_for_candidate(
    payload: object, *, candidate_key_id: str
) -> AttestationValidation:
    """Require every field of a candidate-authority attestation before trusting it.

    Thin wrapper around the shared, neutral
    :func:`services.kalshi_account_gateway.candidate_authority.validate_candidate_authority_attestation`
    -- see that function's docstring for the exact fail-closed policy. Sharing one reviewed
    validator, rather than each M27F/M27G consumer keeping its own subtly different copy, is
    deliberate; see that neutral module's docstring for the layering reasoning (M27G must not
    depend back on this package).
    """
    return validate_candidate_authority_attestation(
        payload,
        candidate_key_id=candidate_key_id,
        expected_scopes=REQUIRED_LIVE_WRITE_SCOPES,
        expected_subaccount=REQUIRED_LIVE_WRITE_SUBACCOUNT,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Operator-only candidate-authority attestation generator. GET-only: uses a "
            "separately authorized management credential to call GET /trade-api/v2/api_keys "
            "and locate exactly one candidate key. Never receives or touches the candidate's "
            "private key. Produces a secret-free JSON attestation artifact."
        )
    )
    parser.add_argument("--management-key-id-file", required=True, type=Path)
    parser.add_argument("--management-private-key-fd", required=True, type=int)
    parser.add_argument("--candidate-key-id-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        management_key_id = args.management_key_id_file.read_text().strip()
        if not management_key_id:
            raise ValueError("management key id file is empty")
        candidate_key_id = args.candidate_key_id_file.read_text().strip()
        if not candidate_key_id:
            raise ValueError("candidate key id file is empty")
        management_private_key_pem = read_private_key_fd(args.management_private_key_fd)
        attestation = generate_candidate_authority_attestation(
            management_key_id=management_key_id,
            management_private_key_pem=management_private_key_pem,
            candidate_key_id=candidate_key_id,
            transport=UrllibProductionReadTransport(),
            timestamp_ms=time.time_ns() // 1_000_000,
        )
    except Exception as exc:  # boundary: never leak details, only a sanitized class name
        print(
            f"BLOCKER: candidate authority attestation failed ({type(exc).__name__})",
            file=sys.stderr,
        )
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(attestation.to_json(), sort_keys=True, indent=2) + "\n")
    print(f"candidate_authority_attestation={attestation.classification}")
    return 0 if attestation.succeeded else 2


if __name__ == "__main__":
    raise SystemExit(main())
