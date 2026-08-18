"""M27G -- protected operator-only real write-credential enrollment boundary.

This module is intentionally never invoked automatically: no runtime path in this
repository calls :func:`install_production_write_credential` implicitly, and
:func:`services.production_execution.credentials.enrollment_available` continues to report
``False`` (see that function's docstring for what it now means post-M27G). Only an operator
running ``enrollment_cli.py`` by hand, supplying real secrets and fresh evidence that do not
exist anywhere in this repository, can reach the real-install path.

Live discovery (2026-08-18) proved the least-privilege candidate (``{"read","write::trade"}``,
subaccount 0) receives ``HTTP 401`` from ``GET /trade-api/v2/api_keys`` when it authenticates
as itself -- it cannot prove its own authority to itself. This module therefore no longer
authenticates as the candidate to call that endpoint (the ``verify_live_write_credential_authority``
/ ``require_live_write_authority`` / ``WriteCredentialServerProof`` machinery this file used
to contain is removed, not merely disabled). Authority proof instead comes from two
independently re-validated, already-rendered, secret-free artifacts supplied by the operator:

* a ``kalsh3.m27f.candidate-authority.v1`` attestation, produced out of band by a *separate*
  management credential (see :mod:`services.supervised_canary.authority_attestation`); and
* a **fresh** ``kalsh3.m27f.live-read-acceptance.v3`` evidence artifact (see
  :mod:`services.supervised_canary.live_read_acceptance`), re-checked for freshness *at
  installation time*, not merely at its own creation time -- historical M27F evidence never
  authorizes installation.

Both artifacts are validated against the exact same live policy
(``REQUIRED_LIVE_WRITE_SCOPES`` / ``REQUIRED_LIVE_WRITE_SUBACCOUNT``) that
``services.supervised_canary`` already applies to the attestation, via one shared neutral
validator (:mod:`services.kalshi_account_gateway.candidate_authority`) rather than two subtly
different copies. That module has no dependency on either ``production_execution`` or
``supervised_canary`` -- ``supervised_canary`` already depends on this package for the
write-candidate domain constants, so this package must never depend back on it.

This module accepts synthetic (``fixture_only=True``) credentials for the ordinary,
always-available fixture/test path (:func:`prepare_enrollment_fixture`,
``ProtectedWriteCredentialStore.install``), and accepts a *real*, non-fixture credential only
through the narrowly reviewed operator-release path added by M27G
(``ProtectedWriteCredentialStore.install_real_credential`` /
:func:`install_production_write_credential`). It stores only sealed material and emits only
secret-free receipts and evidence -- never a raw key ID, PEM, signature, or auth header.

Cross-process transaction safety (Gemini delta repair, 2026-08-18): an earlier revision of
this module checked ``is_installed()`` and then wrote sealed artifacts with no cross-process
exclusion, so two concurrent installers could both observe "not installed" before either
published, and a failed installer's rollback could delete a concurrently-succeeding
installer's valid artifacts. Every write-capable store operation now runs inside one
``fcntl.flock`` exclusive lock (:meth:`ProtectedWriteCredentialStore.exclusive`) that spans
the *entire* transaction -- state inspection, artifact writes, the real-signer self-test, and
commit-or-rollback -- never released mid-transaction. See the store's docstring for the full
locking/commit-marker design.
"""

import fcntl
import hashlib
import json
import os
import secrets
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from services.kalshi_account_gateway.candidate_authority import (
    USER_DATA_FRESHNESS,
    AttestationValidation,
    validate_candidate_authority_attestation,
)
from services.neutral_security import SecretBox

from .credentials import (
    REQUIRED_LIVE_WRITE_SCOPES,
    REQUIRED_LIVE_WRITE_SUBACCOUNT,
    ProductionWriteCredential,
)
from .signer_self_test import SignerSelfTestResult, run_real_signer_self_test


class SecretSealer(Protocol):
    def seal(self, value: bytes) -> str: ...


class EnrollmentError(PermissionError):
    """A M27G installation gate failed. Never carries a secret in its message."""


@dataclass(frozen=True, slots=True)
class SealedCredentialPackage:
    sealed_key_id: str
    sealed_private_key: str
    scope_state: str = "READ_WRITE_TRADE_VALIDATED"
    account: int = 0
    environment: str = "PRODUCTION"


CONFIRMATION = "INSTALL PRODUCTION WRITE CREDENTIAL — REAL MONEY — ONE CONTRACT ONLY"


def _candidate_fingerprint(credential: ProductionWriteCredential) -> str:
    key_id_hash = hashlib.sha256(credential.key_id.encode()).hexdigest()
    private_key_hash = hashlib.sha256(credential.private_key_pem).hexdigest()
    return hashlib.sha256(f"{key_id_hash}:{private_key_hash}".encode()).hexdigest()


@dataclass(frozen=True, slots=True, repr=False)
class InstallationReceipt:
    key_id_hash: str
    private_key_hash: str
    credential_fingerprint: str
    installed_at: datetime
    environment: str = "PRODUCTION"
    account: int = 0
    scopes: tuple[str, ...] = ("read", "write::trade")

    def __repr__(self) -> str:
        return "InstallationReceipt(<secret-free>)"

    def to_json(self) -> dict[str, Any]:
        return {
            "key_id_hash": self.key_id_hash,
            "private_key_hash": self.private_key_hash,
            "credential_fingerprint": self.credential_fingerprint,
            "installed_at": self.installed_at.isoformat(),
            "environment": self.environment,
            "account": self.account,
            "scopes": list(self.scopes),
        }


@dataclass(frozen=True, slots=True)
class OperatorReleaseAuthorization:
    """Constructed only by :func:`install_production_write_credential` once every M27G gate
    (authority attestation, fresh live-read evidence, exact confirmation) has independently
    passed. Python cannot prevent direct construction of a frozen dataclass, but keeping the
    real-install entry point's authorization argument a distinct type -- checked against the
    exact candidate fingerprint -- keeps "ordinary runtime forgot a check" and "an operator
    explicitly completed enrollment" structurally different call sites, and makes a stale or
    mismatched authorization (e.g. reused against a swapped-in credential) fail closed.
    """

    candidate_fingerprint: str


_COMMIT_MARKER_CONTENT = b"COMMITTED\n"


@dataclass(frozen=True, slots=True)
class _StoreLock:
    """Proof that ``store``'s exclusive cross-process transaction lock is currently held.

    Every method that can mutate store state requires one of these, obtained only from
    :meth:`ProtectedWriteCredentialStore.exclusive`. This keeps every write-capable operation
    provably inside the same lock span -- there is no second, unlocked writer path.
    """

    store: "ProtectedWriteCredentialStore"


class ProtectedWriteCredentialStore:
    """Atomic, cross-process-locked sealed store for the production write-credential signer.

    ``install`` accepts only ``fixture_only`` credentials -- it is the always-available path
    ordinary tests and tooling use, and it can never durably persist a real production key.
    ``install_real_credential`` is the separate, narrowly reviewed M27G real-installation
    entry point: it accepts only a non-fixture credential, and only together with an
    :class:`OperatorReleaseAuthorization` bound to that exact credential's fingerprint.
    Nothing in ordinary application runtime constructs an ``OperatorReleaseAuthorization`` --
    only :func:`install_production_write_credential`, after independently validating the
    authority attestation, fresh M27F v3 live-read evidence, and the exact operator
    confirmation string.

    Cross-process exclusion: both paths acquire the same ``fcntl.flock`` exclusive lock (see
    :meth:`exclusive`) and hold it for the *entire* transaction -- state inspection, artifact
    writes, and (for the real path) the caller-supplied signer self-test and commit-or-rollback
    -- so two installers (fixture or real, same process or different processes) can never
    interleave. A stale ``is_installed()`` read followed by a later unlocked write is
    structurally impossible: every write-capable method below requires a ``_StoreLock`` token,
    which only ``exclusive()`` can mint.

    Commit marker / crash safety: an installation is considered valid only once a small
    fixed-content commit marker file has been written, strictly *after* the sealed master key
    and encrypted record and *after* the real path's signer self-test has already succeeded.
    ``is_installed()`` requires the marker (with exact expected content) plus both sealed
    artifacts -- a process that crashes between writing the sealed artifacts and writing the
    marker leaves an incomplete set that reports as NOT installed (fail closed). Only a later
    transaction, holding the same exclusive lock, may clear that leftover state and retry;
    this can never touch a *complete, committed* installation, because ``is_installed()``
    (checked immediately before any clearing) would already be ``True`` for one and the
    transaction would refuse to proceed at all. A commit marker is required here specifically
    because two files are always published in temporal order (master key, then record, then --
    only for the real path -- a self-test that itself takes real wall-clock time): without an
    explicit "everything, including the self-test, is done" marker, a reader could not
    distinguish "both sealed files exist because installation finished" from "both sealed
    files exist because installation crashed after writing them but before the self-test/commit
    step," which is exactly the ambiguity M27F/M27G evidence must never paper over.

    There is no general "uninstall" or "rollback" API: rollback of a failed real-install
    transaction happens privately, via :meth:`_discard_install`, only while the same exclusive
    lock that created those artifacts is still held, and only for artifacts this exact
    transaction itself just wrote (a commit marker is never present at that point, since commit
    is the very last step) -- so a failed transaction can never delete a credential that
    existed before that transaction began.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.master_key_path = directory / "master.key"
        self.record_path = directory / "credential.enc"
        self.commit_marker_path = directory / "installed.marker"
        self.lock_path = directory / "store.lock"

    def is_installed(self) -> bool:
        """True only for a complete, committed installation -- never a partial/crashed one."""
        try:
            marker_ok = self.commit_marker_path.read_bytes() == _COMMIT_MARKER_CONTENT
        except OSError:
            marker_ok = False
        return marker_ok and self.master_key_path.exists() and self.record_path.exists()

    @contextmanager
    def exclusive(self) -> Iterator[_StoreLock]:
        """Acquire the store's cross-process exclusive transaction lock.

        Uses ``fcntl.flock(LOCK_EX)`` on a dedicated lock file inside the (0700) store
        directory -- an advisory kernel lock, not a "lock file exists" convention, so it can
        never go stale: the kernel automatically releases it if the holding process crashes or
        exits, and this also releases it explicitly in ``finally`` so a clean exception (or
        same-process reuse, as in tests) does not wedge later callers. This call blocks
        (no timeout, no non-blocking fallback) until the lock is available -- it never proceeds
        unlocked. Callers must hold the returned token for the *entire* transaction; every
        write-capable store method requires it.
        """
        self.directory.mkdir(parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError as exc:
            os.close(descriptor)
            raise PermissionError("could not acquire the exclusive credential-store lock") from exc
        try:
            yield _StoreLock(self)
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def install(
        self, credential: ProductionWriteCredential, *, now: datetime
    ) -> InstallationReceipt:
        if not credential.fixture_only:
            raise PermissionError("real enrollment requires an explicit operator action")
        with self.exclusive() as lock:
            receipt = self._begin_install(lock, credential, now=now)
            self._commit_install(lock)
            return receipt

    def install_real_credential(
        self,
        credential: ProductionWriteCredential,
        *,
        authorization: OperatorReleaseAuthorization,
        now: datetime,
        self_test: Callable[[ProductionWriteCredential, datetime], SignerSelfTestResult],
    ) -> tuple[InstallationReceipt, SignerSelfTestResult]:
        """The entire real-install transaction: gates, then one locked check/write/test/commit.

        Operator-only entry point; never reachable from ordinary application runtime. The
        non-cryptographic gates (non-fixture credential, authorization/fingerprint match) are
        checked *before* the lock is acquired -- they need no exclusion. The exclusive lock is
        then held across state inspection, the sealed writes, ``self_test`` (the caller-supplied
        real-signer self-test), and commit-or-rollback, and is never released mid-transaction.
        This method itself never raises on a failed self-test -- it returns the (uncommitted,
        rolled-back) result so the caller can decide how to report it; nothing is left resident
        on the real credential-store paths in that case.
        """
        if credential.fixture_only:
            raise PermissionError("install_real_credential requires a real, non-fixture credential")
        if authorization.candidate_fingerprint != _candidate_fingerprint(credential):
            raise PermissionError(
                "operator release authorization does not match this exact credential"
            )
        with self.exclusive() as lock:
            receipt = self._begin_install(lock, credential, now=now)
            signer_result = self_test(credential, now)
            if not signer_result.succeeded:
                self._discard_install(lock)
                return receipt, signer_result
            self._commit_install(lock)
            return receipt, signer_result

    def _require_lock(self, lock: _StoreLock) -> None:
        if lock.store is not self:
            raise PermissionError("lock does not belong to this credential store")

    def _has_incomplete_artifacts(self) -> bool:
        return not self.is_installed() and (
            self.master_key_path.exists()
            or self.record_path.exists()
            or self.commit_marker_path.exists()
        )

    def _begin_install(
        self, lock: _StoreLock, credential: ProductionWriteCredential, *, now: datetime
    ) -> InstallationReceipt:
        """Write sealed artifacts, WITHOUT the commit marker, while ``lock`` is held."""
        self._require_lock(lock)
        if self.is_installed():
            raise PermissionError("write credential is already installed")
        if self._has_incomplete_artifacts():
            # A prior transaction crashed or errored between writing sealed artifacts and
            # either committing or rolling back, without ever completing under the exclusive
            # lock we are now holding -- since is_installed() just proved False, these leftover
            # files can only be that abandoned attempt's own artifacts, never a committed
            # installation, so it is safe to clear them under this same lock before retrying.
            self._clear_uncommitted_artifacts()
        master = secrets.token_bytes(32)
        _create_only_bytes(self.master_key_path, master)
        payload = json.dumps(
            {
                "key_id": credential.key_id,
                "private_key_pem": credential.private_key_pem.decode("ascii"),
            },
            sort_keys=True,
        ).encode("ascii")
        try:
            _create_only_text(self.record_path, SecretBox(master).seal(payload))
        except Exception:
            self._clear_uncommitted_artifacts()
            raise
        return InstallationReceipt(
            hashlib.sha256(credential.key_id.encode()).hexdigest(),
            hashlib.sha256(credential.private_key_pem).hexdigest(),
            _candidate_fingerprint(credential),
            now.astimezone(UTC),
        )

    def _commit_install(self, lock: _StoreLock) -> None:
        """Publish the commit marker, while ``lock`` is still held. Last step of a transaction."""
        self._require_lock(lock)
        _create_only_bytes(self.commit_marker_path, _COMMIT_MARKER_CONTENT)

    def _discard_install(self, lock: _StoreLock) -> None:
        """Roll back this transaction's own just-written, never-committed artifacts.

        Only reachable while the same exclusive lock that created those artifacts is still
        held, and only after ``_begin_install`` in this same transaction (which already proved
        ``is_installed()`` was ``False``) -- so the commit marker was never written, and this
        can never remove a pre-existing valid installation.
        """
        self._require_lock(lock)
        self._clear_uncommitted_artifacts()

    def _clear_uncommitted_artifacts(self) -> None:
        self.master_key_path.unlink(missing_ok=True)
        self.record_path.unlink(missing_ok=True)
        self.commit_marker_path.unlink(missing_ok=True)


def validate_authority_attestation_for_installation(
    payload: object, *, candidate_key_id: str
) -> AttestationValidation:
    """Independently re-validate a candidate-authority attestation before installation.

    Delegates to the shared neutral validator (see
    :mod:`services.kalshi_account_gateway.candidate_authority`) with this package's own
    ``REQUIRED_LIVE_WRITE_SCOPES``/``REQUIRED_LIVE_WRITE_SUBACCOUNT`` -- the identical policy
    :mod:`services.supervised_canary.authority_attestation` already applies to the same
    schema. The candidate is never asked to call ``GET /api_keys`` itself; see module
    docstring.
    """
    return validate_candidate_authority_attestation(
        payload,
        candidate_key_id=candidate_key_id,
        expected_scopes=REQUIRED_LIVE_WRITE_SCOPES,
        expected_subaccount=REQUIRED_LIVE_WRITE_SUBACCOUNT,
    )


LIVE_READ_ACCEPTANCE_SCHEMA = "kalsh3.m27f.live-read-acceptance.v3"
LIVE_READ_AUTHORITY_SOURCE = "EXTERNAL_SERVER_ATTESTATION"
REQUIRED_LIVE_READ_ENDPOINTS = ("balance", "positions", "orders", "fills", "settlements")


@dataclass(frozen=True, slots=True)
class LiveReadEvidenceValidation:
    classification: str
    reason: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.classification == "PASS"


def _evidence_invalid(reason: str) -> LiveReadEvidenceValidation:
    return LiveReadEvidenceValidation("FAIL", reason)


def validate_live_read_evidence_for_installation(
    payload: object, *, candidate_key_id: str, now: datetime
) -> LiveReadEvidenceValidation:
    """Independently re-validate a *fresh* M27F v3 live-read-acceptance evidence artifact.

    Historical M27F evidence must never authorize installation: this re-derives
    ``0 <= now - completed_at <= 30s`` from the artifact's own timestamp at the moment of
    installation, exactly as ``services.supervised_canary.readiness_report`` re-derives
    freshness at the moment of consumption for its display gates -- a previously fresh
    artifact does not stay valid forever. Never trusts the artifact's own classification
    alone: schema, environment, candidate key-ID-hash binding, authority source, every
    required portfolio read, and reconciliation/subaccount-binding are all independently
    re-checked here too.
    """
    if not isinstance(payload, dict):
        return _evidence_invalid("live read evidence malformed")
    if payload.get("schema") != LIVE_READ_ACCEPTANCE_SCHEMA:
        return _evidence_invalid("live read evidence schema mismatch")
    if payload.get("environment") != "PRODUCTION":
        return _evidence_invalid("live read evidence environment mismatch")
    expected_hash = hashlib.sha256(candidate_key_id.encode()).hexdigest()
    if payload.get("key_id_hash") != expected_hash:
        return _evidence_invalid("live read evidence candidate key id mismatch")
    authority = payload.get("candidate_authority")
    if not isinstance(authority, dict) or authority.get("classification") != "PASS":
        return _evidence_invalid("live read evidence candidate authority did not pass")
    if authority.get("source") != LIVE_READ_AUTHORITY_SOURCE:
        return _evidence_invalid("live read evidence authority source rejected")
    reads = payload.get("reads")
    if not isinstance(reads, list):
        return _evidence_invalid("live read evidence reads malformed")
    by_name = {
        item.get("name"): item
        for item in reads
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    for endpoint in REQUIRED_LIVE_READ_ENDPOINTS:
        read = by_name.get(endpoint)
        if not isinstance(read, dict) or read.get("classification") != "SUCCESS":
            return _evidence_invalid(f"live read evidence {endpoint} read did not succeed")
    reconciliation = payload.get("reconciliation")
    if not isinstance(reconciliation, dict) or reconciliation.get("classification") != "PASS":
        return _evidence_invalid("live read evidence reconciliation did not pass")
    if reconciliation.get("subaccount_binding_verified") is not True:
        return _evidence_invalid("live read evidence subaccount binding not verified")
    completed_raw = payload.get("completed_at")
    if not isinstance(completed_raw, str):
        return _evidence_invalid("live read evidence completed_at malformed")
    try:
        completed_at = datetime.fromisoformat(completed_raw)
    except ValueError:
        return _evidence_invalid("live read evidence completed_at malformed")
    if completed_at.tzinfo is None or completed_at.utcoffset() is None:
        return _evidence_invalid("live read evidence completed_at not timezone-aware")
    age = now.astimezone(UTC) - completed_at.astimezone(UTC)
    if not timedelta(0) <= age <= USER_DATA_FRESHNESS:
        return _evidence_invalid("live read evidence is stale or has a future timestamp")
    return LiveReadEvidenceValidation("PASS", None)


@dataclass(frozen=True, slots=True)
class EnrollmentOutcome:
    receipt: InstallationReceipt
    signer_self_test: SignerSelfTestResult

    def to_json(self) -> dict[str, Any]:
        return {
            "receipt": self.receipt.to_json(),
            "signer_self_test": self.signer_self_test.to_json(),
        }


def install_production_write_credential(
    *,
    credential: ProductionWriteCredential,
    store: ProtectedWriteCredentialStore,
    authority_attestation: object,
    live_read_evidence: object,
    explicit_confirmation: str,
    now: datetime,
) -> EnrollmentOutcome:
    """Install the reviewed narrow candidate as the real production write credential.

    This is the only path in the repository that can durably persist a non-fixture
    :class:`ProductionWriteCredential`. Every gate below is independently re-validated here --
    a caller-declared claim, or a merely-serialized ``PASS``, is never sufficient:

    1. the credential itself must not be ``fixture_only`` (this function is never the M15
       ephemeral-fixture path -- see :func:`prepare_enrollment_fixture` /
       ``store.install`` for that);
    2. the supplied candidate-authority attestation must independently re-validate to
       ``PASS`` for this exact candidate key ID;
    3. the supplied M27F v3 live-read evidence must independently re-validate to ``PASS``
       for the same candidate key ID, and must be fresh *right now*, not merely at its own
       creation time -- historical M27F evidence never authorizes installation;
    4. the operator must supply the exact real-money confirmation string;
    5. the store's own real-install boundary re-checks the candidate fingerprint before
       writing anything.

    After a successful atomic install, this runs the non-network real-signer self-test (see
    :mod:`services.production_execution.signer_self_test`) against the exact key just
    installed -- never touching transport, never constructing a valid mutating request
    signature -- and rolls the install back if that self-test fails, so a key that cannot
    actually sign with the real signer primitive is never left resident. Neither this
    function nor anything it calls arms production or sends a request.
    """
    if credential.fixture_only:
        raise EnrollmentError("real production installation requires a non-fixture credential")
    attestation_result = validate_authority_attestation_for_installation(
        authority_attestation, candidate_key_id=credential.key_id
    )
    if not attestation_result.succeeded:
        raise EnrollmentError("candidate authority attestation did not independently validate")
    evidence_result = validate_live_read_evidence_for_installation(
        live_read_evidence, candidate_key_id=credential.key_id, now=now
    )
    if not evidence_result.succeeded:
        raise EnrollmentError("fresh M27F live-read evidence did not independently validate")
    if explicit_confirmation != CONFIRMATION:
        raise EnrollmentError("exact real-money warning confirmation required")
    authorization = OperatorReleaseAuthorization(_candidate_fingerprint(credential))
    receipt, signer_result = store.install_real_credential(
        credential,
        authorization=authorization,
        now=now,
        self_test=lambda cred, at: run_real_signer_self_test(credential=cred, now=at),
    )
    if not signer_result.succeeded:
        raise EnrollmentError("real signer self-test failed after installation; rolled back")
    return EnrollmentOutcome(receipt, signer_result)


def _create_only_bytes(path: Path, value: bytes) -> None:
    """Publish ``value`` at ``path`` with true create-only (no silent overwrite) semantics.

    ``O_EXCL`` on the destination itself makes the kernel atomically refuse to create the file
    if it already exists -- there is no check-then-write gap a concurrent writer could land in
    (unlike a temp-file-then-``os.replace`` dance, where the final ``os.replace`` would
    silently clobber an existing destination). This is safe to call without its own additional
    locking specifically because every call site here is already inside
    ``ProtectedWriteCredentialStore``'s exclusive cross-process transaction lock, which is the
    only writer of these paths.
    """
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, value)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory_descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def _create_only_text(path: Path, value: str) -> None:
    _create_only_bytes(path, value.encode("ascii"))


def prepare_enrollment_fixture(
    *,
    credential: ProductionWriteCredential,
    sealer: SecretSealer,
    owner_authenticated: bool,
    password_reauthenticated: bool,
    totp_valid: bool,
    explicit_confirmation: str,
    validated_environment: str,
    validated_account: int,
    fixture_validator: bool,
) -> SealedCredentialPackage:
    """Models future backend workflow using fixtures; it never installs the package."""
    if not fixture_validator or not credential.fixture_only:
        raise PermissionError("live production enrollment is unavailable in M15")
    if not all((owner_authenticated, password_reauthenticated, totp_valid)):
        raise PermissionError("strong owner reauthentication required")
    if (
        explicit_confirmation != "PREPARE PRODUCTION WRITE CREDENTIAL"
        or validated_environment != "PRODUCTION"
    ):
        raise PermissionError("explicit production warning confirmation required")
    if (
        validated_account != REQUIRED_LIVE_WRITE_SUBACCOUNT
        or credential.scopes != REQUIRED_LIVE_WRITE_SCOPES
    ):
        raise PermissionError("scope or account validation failed")
    return SealedCredentialPackage(
        sealer.seal(credential.key_id.encode()), sealer.seal(credential.private_key_pem)
    )
