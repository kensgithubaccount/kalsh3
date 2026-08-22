"""M27H -- operator-only, local, read-only verification of the already-installed
production write credential.

M27G installed a real production write credential (``ProtectedWriteCredentialStore.
install_real_credential``, run once by the operator via ``enrollment_cli.py``). That
installation is durable, but nothing in the repository could subsequently re-prove, on
demand, that the committed store still holds a coherent, correctly-scoped, real credential
whose private key still works with the real RSA-PSS signer primitive -- so
``services/supervised_canary/readiness_report.py`` kept reporting ``PRODUCTION_WRITE_CREDENTIAL
= NOT INSTALLED`` and ``REAL_SIGNER_VALIDATION = BLOCKED_BY_CREDENTIAL`` even after a
successful M27G install. This module closes exactly that evidence gap and nothing else.

This is evidence plumbing, not execution plumbing:

* it never calls a Kalshi endpoint, never touches transport, the production journal, or
  ``SignAndSendBoundary``;
* it never arms production and never sends a request;
* it decrypts the installed credential only long enough, inside this process, to run the
  existing unmodified real-signer self-test (:func:`services.production_execution.
  signer_self_test.run_real_signer_self_test`) against it, and to compute secret-free hashes
  -- the decrypted key never leaves this module, is never logged, and is never written to the
  evidence artifact it produces;
* it is invoked only by an operator running this file's CLI by hand (or by test code); no
  runtime path in this repository calls :func:`verify_installed_write_credential`
  automatically, and ordinary application/trading runtime has no access to it.

Gemini M27H delta repair (2026-08-18), two blockers fixed here:

1. **Credential escape.** The store previously exposed a *public*
   ``read_committed_credential_for_verification`` method returning a usable
   ``ProductionWriteCredential``. Combined with the already-public ``store.exclusive()``, that
   was a de facto runtime credential-provider API in disguise. The store now exposes only a
   private :meth:`services.production_execution.enrollment.ProtectedWriteCredentialStore.
   _decode_committed_credential`. Its sanctioned consumers are deliberately limited to this
   M27H verifier and the narrow M27O send/reconciliation boundaries. M27H uses the decrypted
   credential only to compute hashes / bind authority / run the signer self-test; M27O may use
   it only inside the protected credential-store lock to sign the exact already-authorized
   request or, after a possible send, perform authenticated GET-only reconciliation. None of
   these paths returns, logs, serializes, persists, or hands the credential to caller-supplied
   code. There is deliberately no generic
   ``with_credential(callback)`` primitive either -- that would just be the same escape under a
   different name (a caller could pass ``lambda cred: cred``). Python cannot guarantee memory
   zeroization; the containment here is architectural (no public return path), not
   cryptographic erasure, and this module makes no stronger claim than that.
2. **Evidence that never expires.** ``PRODUCTION_WRITE_CREDENTIAL`` previously stayed
   unlocked from any structurally valid *historical* artifact, on the theory that "committed
   installation" is a durable fact. Gemini correctly rejected that: readiness never inspects
   the live store, so a stale artifact proves nothing about the store's *current* state (it
   could since have been deleted, corrupted, replaced, or permission-changed). Both
   ``PRODUCTION_WRITE_CREDENTIAL`` and ``REAL_SIGNER_VALIDATION`` are now independently
   freshness-gated against the same 30-second window at consumption time -- see
   :func:`validate_installed_credential_evidence_for_readiness`.

Also removed per Gemini (important, non-blocker): the evidence schema no longer carries
``production_armed``/``real_mutation`` fields. This verifier has no authority to inspect or
prove those states -- ``readiness_report.py`` already independently owns them, and always did;
carrying look-alike fields in the M27H artifact only invited a future reader to trust them by
mistake. The CLI still prints the same literal ``PRODUCTION_ARMED: DISARMED`` /
``REAL_MUTATION: NOT TESTED`` / ``ORDER_SENT: NO`` lines to the operator's terminal, exactly as
``enrollment_cli.py`` already does -- that is informational operator text, not part of the
evidence artifact, and it is never written to ``--output``.

The evidence artifact this module produces (``kalsh3.m27h.installed-write-credential.v1``) is
consumed only by ``services.supervised_canary.readiness_report``, which independently
re-validates it (see :func:`validate_installed_credential_evidence_for_readiness`) rather than
trusting its stored classification, and which can use it to unlock only
``PRODUCTION_WRITE_CREDENTIAL`` and ``REAL_SIGNER_VALIDATION`` -- never ``PRODUCTION_ARMED``,
``REAL_MUTATION``, or any other M13/M14/M15 final trade gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from services.kalshi_account_gateway.candidate_authority import USER_DATA_FRESHNESS

from .enrollment import (
    ProtectedWriteCredentialStore,
    _candidate_fingerprint,
    validate_authority_attestation_for_installation,
)
from .enrollment_cli import default_production_write_store_directory
from .signer_self_test import run_real_signer_self_test

INSTALLED_CREDENTIAL_EVIDENCE_SCHEMA = "kalsh3.m27h.installed-write-credential.v1"
SOFTWARE_VERSION = "kalsh3.m27h.installed-credential-verification/1"

# Reuses the exact 30-second freshness window M27F/M27G already apply to live authenticated
# evidence, for consistency across every "current operational proof, not permanent proof" gate
# in this repository -- see module docstring and readiness_report.py's own
# ``_consumption_fresh``. Applies to BOTH gates this module can unlock (Gemini delta repair):
# readiness never inspects the live store, so neither "committed installation" nor "signer
# self-test passed" may be trusted from an artifact older than this window.
EVIDENCE_FRESHNESS = USER_DATA_FRESHNESS


class VerificationError(PermissionError):
    """A M27H verification step failed. Never carries a secret in its message."""


@dataclass(frozen=True, slots=True, repr=False)
class InstalledCredentialVerification:
    """Secret-free evidence that the installed credential decrypts, binds to the
    independently re-validated candidate authority, and passes the real signer self-test.

    Never carries the private key, PEM, signature, decrypted record, or auth header -- only
    hashes and classifications, matching every other M27 evidence artifact. Carries no
    ``production_armed``/``real_mutation`` fields: this verifier has no authority to inspect or
    prove either state, and ``readiness_report.py`` already independently owns both (Gemini
    delta repair).
    """

    schema: str
    software_version: str
    environment: str
    observed_at: datetime
    completed_at: datetime
    store_state: str
    key_id_hash: str | None
    credential_fingerprint: str | None
    authority_classification: str
    authority_reason: str | None
    signer_classification: str
    signer_challenge_domain: str | None
    signer_reason: str | None
    signer_completed_at: datetime | None
    classification: str
    reason: str | None

    def __repr__(self) -> str:
        return "InstalledCredentialVerification(<secret-free>)"

    @property
    def succeeded(self) -> bool:
        return self.classification == "PASS"

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "software_version": self.software_version,
            "environment": self.environment,
            "observed_at": self.observed_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "store_state": self.store_state,
            "key_id_hash": self.key_id_hash,
            "credential_fingerprint": self.credential_fingerprint,
            "authority_classification": self.authority_classification,
            "authority_reason": self.authority_reason,
            "signer_classification": self.signer_classification,
            "signer_challenge_domain": self.signer_challenge_domain,
            "signer_reason": self.signer_reason,
            "signer_completed_at": (
                self.signer_completed_at.isoformat() if self.signer_completed_at else None
            ),
            "classification": self.classification,
            "reason": self.reason,
        }


def _not_installed(observed_at: datetime, reason: str) -> InstalledCredentialVerification:
    return InstalledCredentialVerification(
        schema=INSTALLED_CREDENTIAL_EVIDENCE_SCHEMA,
        software_version=SOFTWARE_VERSION,
        environment="PRODUCTION",
        observed_at=observed_at,
        completed_at=observed_at,
        store_state="NOT_COMMITTED",
        key_id_hash=None,
        credential_fingerprint=None,
        authority_classification="NOT_RUN",
        authority_reason=None,
        signer_classification="NOT_RUN",
        signer_challenge_domain=None,
        signer_reason=None,
        signer_completed_at=None,
        classification="FAIL",
        reason=reason,
    )


def verify_installed_write_credential(
    *,
    store: ProtectedWriteCredentialStore,
    authority_attestation: object,
    now: datetime,
) -> InstalledCredentialVerification:
    """Read-only, local, operator-run verification of an already-committed installation.

    Acquires the exact same cross-process exclusive store lock M27G's installer uses (see
    :meth:`ProtectedWriteCredentialStore.exclusive`) and holds it for the whole verification, so
    it always observes a coherent snapshot -- never a concurrently-mutating one. Never writes to
    the store.

    Credential containment: the decrypted ``ProductionWriteCredential`` obtained from
    :meth:`ProtectedWriteCredentialStore._decode_committed_credential` exists only in this
    function's local ``credential`` variable, for the sole purpose of (a) independently binding
    it to ``authority_attestation`` by its own key ID, and (b) running the unmodified real-signer
    self-test against it. It is never assigned to a wider scope, never returned, never logged,
    and never passed to caller-supplied code -- only the secret-free
    :class:`InstalledCredentialVerification` this function returns escapes this call.
    """
    observed_at = now.astimezone(UTC)
    with store.exclusive() as lock:
        try:
            credential = store._decode_committed_credential(lock)
        except PermissionError as exc:
            return _not_installed(observed_at, str(exc))
        key_id_hash = hashlib.sha256(credential.key_id.encode()).hexdigest()
        fingerprint = _candidate_fingerprint(credential)
        authority_result = validate_authority_attestation_for_installation(
            authority_attestation, candidate_key_id=credential.key_id
        )
        if not authority_result.succeeded:
            completed_at = datetime.now(UTC)
            return InstalledCredentialVerification(
                schema=INSTALLED_CREDENTIAL_EVIDENCE_SCHEMA,
                software_version=SOFTWARE_VERSION,
                environment="PRODUCTION",
                observed_at=observed_at,
                completed_at=completed_at,
                store_state="COMMITTED",
                key_id_hash=key_id_hash,
                credential_fingerprint=fingerprint,
                authority_classification=authority_result.classification,
                authority_reason=authority_result.reason,
                signer_classification="NOT_RUN",
                signer_challenge_domain=None,
                signer_reason=None,
                signer_completed_at=None,
                classification="FAIL",
                reason="candidate authority attestation did not independently validate",
            )
        signer_result = run_real_signer_self_test(credential=credential, now=datetime.now(UTC))
        completed_at = datetime.now(UTC)
        return InstalledCredentialVerification(
            schema=INSTALLED_CREDENTIAL_EVIDENCE_SCHEMA,
            software_version=SOFTWARE_VERSION,
            environment="PRODUCTION",
            observed_at=observed_at,
            completed_at=completed_at,
            store_state="COMMITTED",
            key_id_hash=key_id_hash,
            credential_fingerprint=fingerprint,
            authority_classification=authority_result.classification,
            authority_reason=authority_result.reason,
            signer_classification=signer_result.classification,
            signer_challenge_domain=signer_result.challenge_domain,
            signer_reason=signer_result.reason,
            signer_completed_at=signer_result.completed_at,
            classification="PASS" if signer_result.succeeded else "FAIL",
            reason=None if signer_result.succeeded else "real signer self-test failed",
        )


@dataclass(frozen=True, slots=True)
class InstalledCredentialEvidenceCheck:
    """What ``readiness_report`` may independently conclude from a M27H evidence artifact."""

    credential_installed: bool
    signer_verified_fresh: bool
    reason: str | None


def _invalid(reason: str) -> InstalledCredentialEvidenceCheck:
    return InstalledCredentialEvidenceCheck(False, False, reason)


def _parse_utc_timestamp(raw: object) -> datetime | None:
    """Parse a required timezone-aware ISO timestamp; ``None`` on anything malformed."""
    if not isinstance(raw, str):
        return None
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(UTC)


def validate_installed_credential_evidence_for_readiness(
    payload: object, *, now: datetime
) -> InstalledCredentialEvidenceCheck:
    """Independently re-validate a M27H evidence artifact; never trust its own classification.

    Gemini delta repair: readiness never inspects the live protected store directly, so a
    structurally valid but *historical* artifact proves nothing about whether the store still
    holds that credential right now -- it could since have been deleted, corrupted, replaced, or
    had its permissions changed. Both ``credential_installed`` and ``signer_verified_fresh`` are
    therefore independently re-derived as ``0 <= now - <their timestamp> <= EVIDENCE_FRESHNESS``
    at the moment of *this* call (30 seconds inclusive at the boundary), exactly as
    ``readiness_report._consumption_fresh`` re-derives M27F freshness at consumption time rather
    than trusting a previously-fresh artifact forever. Neither flag can ever be ``True`` for an
    artifact whose relevant timestamp is stale or in the future.

    Also independently checks timestamp ordering sanity (``observed_at <= signer_completed_at
    <= completed_at``, exactly the sequence :func:`verify_installed_write_credential` actually
    produces) -- an artifact that claims signer completion after overall completion, or
    observation after either, is malformed/tampered and fails closed.

    A structurally invalid artifact (wrong schema/environment/store state, malformed or missing
    key-ID-hash, or an authority validation that did not independently pass) yields both flags
    ``False`` before any timestamp is even examined -- it can never partially unlock readiness.
    """
    if not isinstance(payload, dict):
        return _invalid("installed credential evidence malformed")
    if payload.get("schema") != INSTALLED_CREDENTIAL_EVIDENCE_SCHEMA:
        return _invalid("installed credential evidence schema mismatch")
    if payload.get("environment") != "PRODUCTION":
        return _invalid("installed credential evidence environment mismatch")
    if payload.get("store_state") != "COMMITTED":
        return _invalid("installed credential evidence store state is not committed")
    key_id_hash = payload.get("key_id_hash")
    if not isinstance(key_id_hash, str) or len(key_id_hash) != 64:
        return _invalid("installed credential evidence key id hash malformed")
    if payload.get("authority_classification") != "PASS":
        return _invalid("installed credential evidence authority validation did not pass")

    now_utc = now.astimezone(UTC)
    observed_at = _parse_utc_timestamp(payload.get("observed_at"))
    completed_at = _parse_utc_timestamp(payload.get("completed_at"))
    if observed_at is None or completed_at is None:
        return _invalid("installed credential evidence timestamps malformed")
    if observed_at > completed_at:
        return _invalid("installed credential evidence timestamp ordering violated")
    credential_age = now_utc - completed_at
    if not timedelta(0) <= credential_age <= EVIDENCE_FRESHNESS:
        return _invalid("installed credential evidence is stale or has a future timestamp")
    credential_installed = True

    signer_classification = payload.get("signer_classification")
    signer_completed_at = _parse_utc_timestamp(payload.get("signer_completed_at"))
    if signer_classification != "PASS" or signer_completed_at is None:
        return InstalledCredentialEvidenceCheck(
            credential_installed, False, "signer self-test evidence missing or did not pass"
        )
    if not observed_at <= signer_completed_at <= completed_at:
        return InstalledCredentialEvidenceCheck(
            credential_installed, False, "signer self-test evidence timestamp ordering violated"
        )
    signer_age = now_utc - signer_completed_at
    if not timedelta(0) <= signer_age <= EVIDENCE_FRESHNESS:
        return InstalledCredentialEvidenceCheck(
            credential_installed,
            False,
            "signer self-test evidence is stale or has a future timestamp",
        )
    return InstalledCredentialEvidenceCheck(True, True, None)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Operator-only local, read-only verification of the already-installed production "
            "write credential. No network dependency; never arms production; never sends a "
            "request; never prints secret material."
        )
    )
    parser.add_argument(
        "--store-dir", type=Path, default=default_production_write_store_directory()
    )
    parser.add_argument("--authority-attestation", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        authority_attestation = json.loads(args.authority_attestation.read_text())
        result = verify_installed_write_credential(
            store=ProtectedWriteCredentialStore(args.store_dir),
            authority_attestation=authority_attestation,
            now=datetime.now(UTC),
        )
    except Exception as exc:  # boundary: never leak secrets, only a sanitized class name
        print(
            f"BLOCKER: installed credential verification failed ({type(exc).__name__})",
            file=sys.stderr,
        )
        return 2
    rendered = json.dumps(result.to_json(), sort_keys=True, indent=2)
    args.output.write_text(rendered)
    print(rendered)
    # Informational operator text only, matching enrollment_cli.py's own convention -- never
    # part of the evidence schema/JSON file, and never derived from this verifier's own
    # authority (it has none to inspect arm state or mutation history).
    print("PRODUCTION_ARMED: DISARMED")
    print("REAL_MUTATION: NOT TESTED")
    print("ORDER_SENT: NO")
    return 0 if result.succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
