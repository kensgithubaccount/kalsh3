from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from services.kalshi_account_gateway.production_read_credentials import (
    API_KEYS_PATH,
    PRODUCTION_ORIGIN,
)
from services.production_execution import enrollment as enrollment_mod
from services.production_execution import enrollment_cli, security_boundary
from services.production_execution.credentials import (
    REQUIRED_LIVE_WRITE_SCOPES,
    ProductionWriteCredential,
    enrollment_available,
)
from services.production_execution.enrollment import (
    CONFIRMATION,
    EnrollmentError,
    EnrollmentOutcome,
    InstallationReceipt,
    OperatorReleaseAuthorization,
    ProtectedWriteCredentialStore,
    install_production_write_credential,
    validate_authority_attestation_for_installation,
    validate_live_read_evidence_for_installation,
)
from services.production_execution.security_boundary import BoundaryError, SignAndSendBoundary
from services.production_execution.signer_self_test import (
    SIGNER_SELF_TEST_DOMAIN,
    SignerSelfTestResult,
    run_real_signer_self_test,
)
from services.production_execution.store import ProductionJournal

CANDIDATE_KEY_ID = "candidate-key-id"
CANDIDATE_KEY_ID_HASH = hashlib.sha256(CANDIDATE_KEY_ID.encode()).hexdigest()
NOW = datetime(2026, 8, 18, 12, 0, 0, tzinfo=UTC)


def genkey() -> bytes:
    return subprocess.run(
        ["/usr/bin/openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048"],
        capture_output=True,
        check=True,
    ).stdout


def real_credential(*, key_id: str = CANDIDATE_KEY_ID, private_key_pem: bytes | None = None):
    return ProductionWriteCredential(
        key_id, private_key_pem or genkey(), REQUIRED_LIVE_WRITE_SCOPES, fixture_only=False
    )


def valid_attestation(
    *, key_id_hash: str = CANDIDATE_KEY_ID_HASH, candidate_overrides: dict[str, Any] | None = None
) -> dict[str, Any]:
    candidate = {
        "key_id_hash": key_id_hash,
        "server_scopes": ["read", "write::trade"],
        "server_subaccount": 0,
        "unique_matches": 1,
    }
    candidate.update(candidate_overrides or {})
    return {
        "schema": "kalsh3.m27f.candidate-authority.v1",
        "software_version": "kalsh3.m27f.candidate-authority/1",
        "environment": "PRODUCTION",
        "observed_at": NOW.isoformat(),
        "source": {"origin": PRODUCTION_ORIGIN, "path": API_KEYS_PATH},
        "classification": "PASS",
        "candidate": candidate,
        "reason": None,
    }


def _read(name: str, classification: str = "SUCCESS") -> dict[str, Any]:
    return {
        "name": name,
        "classification": classification,
        "started_at": NOW.isoformat(),
        "completed_at": NOW.isoformat(),
        "count": 0,
        "pagination_complete": True,
        "payload_sha256": "a" * 64,
        "reason": None,
    }


def valid_evidence(
    *,
    key_id_hash: str = CANDIDATE_KEY_ID_HASH,
    schema: str = "kalsh3.m27f.live-read-acceptance.v3",
    completed_at: datetime = NOW,
    reads_override: list[dict[str, Any]] | None = None,
    reconciliation_override: dict[str, Any] | None = None,
    authority_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reads = reads_override
    if reads is None:
        reads = [_read(name) for name in ("balance", "positions", "orders", "fills", "settlements")]
    authority = {
        "classification": "PASS",
        "key_id_hash": key_id_hash,
        "server_scopes": ["read", "write::trade"],
        "server_subaccount": 0,
        "started_at": NOW.isoformat(),
        "completed_at": NOW.isoformat(),
        "source": "EXTERNAL_SERVER_ATTESTATION",
        "reason": None,
    }
    authority.update(authority_override or {})
    reconciliation = {
        "classification": "PASS",
        "balance_succeeded": True,
        "open_orders_complete": True,
        "positions_complete": True,
        "fills_complete": True,
        "settlements_complete": True,
        "subaccount_binding_verified": True,
        "fresh": True,
        "reason": None,
    }
    reconciliation.update(reconciliation_override or {})
    return {
        "schema": schema,
        "software_version": "kalsh3.m27f.live-read-acceptance/3",
        "environment": "PRODUCTION",
        "subaccount": 0,
        "key_id_hash": key_id_hash,
        "started_at": NOW.isoformat(),
        "completed_at": completed_at.isoformat(),
        "candidate_authority": authority,
        "reads": reads,
        "reconciliation": reconciliation,
    }


# --------------------------------------------------------------------------------------
# AUTHORITY
# --------------------------------------------------------------------------------------


def test_valid_attestation_is_accepted_for_installation() -> None:
    result = validate_authority_attestation_for_installation(
        valid_attestation(), candidate_key_id=CANDIDATE_KEY_ID
    )
    assert result.succeeded


@pytest.mark.parametrize(
    "candidate_overrides",
    [
        {"key_id_hash": "0" * 64},  # wrong candidate hash
        {"server_scopes": ["read"]},  # missing write::trade
        {"server_scopes": ["read", "write"]},  # broad write
        {"server_scopes": ["read", "write::trade", "write::transfer"]},  # extra scope
        {"server_subaccount": 1},  # wrong subaccount
        {"server_subaccount": None},  # null subaccount
        {"server_subaccount": True},  # bool as int
        {"unique_matches": 2},  # duplicate-match claim
        {"unique_matches": 0},
    ],
)
def test_attestation_authority_violations_are_rejected(
    candidate_overrides: dict[str, Any],
) -> None:
    payload = valid_attestation(candidate_overrides=candidate_overrides)
    result = validate_authority_attestation_for_installation(
        payload, candidate_key_id=CANDIDATE_KEY_ID
    )
    assert not result.succeeded


def test_non_pass_classification_is_rejected() -> None:
    payload = valid_attestation()
    payload["classification"] = "FAIL"
    result = validate_authority_attestation_for_installation(
        payload, candidate_key_id=CANDIDATE_KEY_ID
    )
    assert not result.succeeded


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"schema": "kalsh3.m27f.candidate-authority.v0"},
        "not-a-dict",
        123,
    ],
)
def test_malformed_attestation_is_rejected(payload: object) -> None:
    result = validate_authority_attestation_for_installation(
        payload, candidate_key_id=CANDIDATE_KEY_ID
    )
    assert not result.succeeded


# --------------------------------------------------------------------------------------
# LIVE M27F EVIDENCE
# --------------------------------------------------------------------------------------


def test_valid_fresh_v3_evidence_is_accepted() -> None:
    result = validate_live_read_evidence_for_installation(
        valid_evidence(), candidate_key_id=CANDIDATE_KEY_ID, now=NOW + timedelta(seconds=5)
    )
    assert result.succeeded


@pytest.mark.parametrize(
    "schema",
    [
        "kalsh3.m27f.candidate-authority.v1",
        "kalsh3.m27f.live-read-acceptance.v1",
        "kalsh3.m27f.live-read-acceptance.v2",
    ],
)
def test_old_schema_versions_are_rejected(schema: str) -> None:
    result = validate_live_read_evidence_for_installation(
        valid_evidence(schema=schema), candidate_key_id=CANDIDATE_KEY_ID, now=NOW
    )
    assert not result.succeeded


def test_stale_evidence_beyond_30_seconds_is_rejected() -> None:
    result = validate_live_read_evidence_for_installation(
        valid_evidence(completed_at=NOW - timedelta(seconds=31)),
        candidate_key_id=CANDIDATE_KEY_ID,
        now=NOW,
    )
    assert not result.succeeded


def test_evidence_exactly_at_30_seconds_is_accepted() -> None:
    result = validate_live_read_evidence_for_installation(
        valid_evidence(completed_at=NOW - timedelta(seconds=30)),
        candidate_key_id=CANDIDATE_KEY_ID,
        now=NOW,
    )
    assert result.succeeded


def test_future_timestamped_evidence_is_rejected() -> None:
    result = validate_live_read_evidence_for_installation(
        valid_evidence(completed_at=NOW + timedelta(seconds=5)),
        candidate_key_id=CANDIDATE_KEY_ID,
        now=NOW,
    )
    assert not result.succeeded


def test_wrong_candidate_hash_evidence_is_rejected() -> None:
    result = validate_live_read_evidence_for_installation(
        valid_evidence(key_id_hash="0" * 64), candidate_key_id=CANDIDATE_KEY_ID, now=NOW
    )
    assert not result.succeeded


def test_authority_not_pass_evidence_is_rejected() -> None:
    result = validate_live_read_evidence_for_installation(
        valid_evidence(authority_override={"classification": "FAIL"}),
        candidate_key_id=CANDIDATE_KEY_ID,
        now=NOW,
    )
    assert not result.succeeded


def test_wrong_authority_source_evidence_is_rejected() -> None:
    result = validate_live_read_evidence_for_installation(
        valid_evidence(authority_override={"source": "CANDIDATE_SELF_CALL"}),
        candidate_key_id=CANDIDATE_KEY_ID,
        now=NOW,
    )
    assert not result.succeeded


@pytest.mark.parametrize(
    "failed_endpoint", ["balance", "positions", "orders", "fills", "settlements"]
)
def test_one_failed_portfolio_read_is_rejected(failed_endpoint: str) -> None:
    reads = [
        _read(name, "SUCCESS" if name != failed_endpoint else "AUTH_FAILURE")
        for name in ("balance", "positions", "orders", "fills", "settlements")
    ]
    result = validate_live_read_evidence_for_installation(
        valid_evidence(reads_override=reads), candidate_key_id=CANDIDATE_KEY_ID, now=NOW
    )
    assert not result.succeeded


@pytest.mark.parametrize("classification", ["BLOCKED", "FAIL"])
def test_reconciliation_not_pass_is_rejected(classification: str) -> None:
    result = validate_live_read_evidence_for_installation(
        valid_evidence(reconciliation_override={"classification": classification}),
        candidate_key_id=CANDIDATE_KEY_ID,
        now=NOW,
    )
    assert not result.succeeded


def test_subaccount_binding_false_is_rejected() -> None:
    result = validate_live_read_evidence_for_installation(
        valid_evidence(reconciliation_override={"subaccount_binding_verified": False}),
        candidate_key_id=CANDIDATE_KEY_ID,
        now=NOW,
    )
    assert not result.succeeded


# --------------------------------------------------------------------------------------
# STORE
# --------------------------------------------------------------------------------------


def authorization_for(credential: ProductionWriteCredential) -> OperatorReleaseAuthorization:
    return OperatorReleaseAuthorization(enrollment_mod._candidate_fingerprint(credential))


def passing_self_test(credential: ProductionWriteCredential, at: datetime) -> SignerSelfTestResult:
    return run_real_signer_self_test(credential=credential, now=at)


def failing_self_test(credential: ProductionWriteCredential, at: datetime) -> SignerSelfTestResult:
    return SignerSelfTestResult(
        "FAIL",
        hashlib.sha256(credential.key_id.encode()).hexdigest(),
        SIGNER_SELF_TEST_DOMAIN,
        None,
        None,
        at,
        reason="synthetic self-test failure",
    )


def test_store_install_real_credential_is_atomic_and_refuses_overwrite(tmp_path: Path) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    credential = real_credential()
    authorization = authorization_for(credential)
    assert not store.is_installed()
    receipt, signer_result = store.install_real_credential(
        credential, authorization=authorization, now=NOW, self_test=passing_self_test
    )
    assert signer_result.succeeded
    assert store.is_installed()
    assert credential.private_key_pem not in store.record_path.read_bytes()
    assert oct(store.directory.stat().st_mode & 0o777) == "0o700"
    assert oct(store.record_path.stat().st_mode & 0o777) == "0o600"
    assert oct(store.master_key_path.stat().st_mode & 0o777) == "0o600"
    assert oct(store.commit_marker_path.stat().st_mode & 0o777) == "0o600"
    assert isinstance(receipt, InstallationReceipt)
    with pytest.raises(PermissionError, match="already installed"):
        store.install_real_credential(
            credential, authorization=authorization, now=NOW, self_test=passing_self_test
        )


def test_store_rejects_mismatched_or_forged_authorization(tmp_path: Path) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    credential = real_credential()
    forged = OperatorReleaseAuthorization("0" * 64)
    with pytest.raises(PermissionError, match="does not match"):
        store.install_real_credential(
            credential, authorization=forged, now=NOW, self_test=passing_self_test
        )
    assert not store.is_installed()


def test_store_install_real_credential_rejects_fixture_only(tmp_path: Path) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    fixture_credential = ProductionWriteCredential(
        "fixture-key", genkey(), REQUIRED_LIVE_WRITE_SCOPES, fixture_only=True
    )
    with pytest.raises(PermissionError, match="non-fixture"):
        store.install_real_credential(
            fixture_credential,
            authorization=OperatorReleaseAuthorization("x"),
            now=NOW,
            self_test=passing_self_test,
        )


def test_ordinary_install_still_refuses_real_credential(tmp_path: Path) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    with pytest.raises(PermissionError, match="explicit operator action"):
        store.install(real_credential(), now=NOW)


def test_store_rolls_back_and_reports_failure_on_self_test_failure(tmp_path: Path) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    credential = real_credential()
    receipt, signer_result = store.install_real_credential(
        credential,
        authorization=authorization_for(credential),
        now=NOW,
        self_test=failing_self_test,
    )
    assert not signer_result.succeeded
    assert isinstance(receipt, InstallationReceipt)
    assert not store.is_installed()
    assert not store.master_key_path.exists()
    assert not store.record_path.exists()
    assert not store.commit_marker_path.exists()


def test_rollback_on_seal_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")

    class ExplodingBox:
        def __init__(self, master_key: bytes) -> None:
            pass

        def seal(self, value: bytes) -> str:
            raise RuntimeError("synthetic seal failure")

    monkeypatch.setattr(enrollment_mod, "SecretBox", ExplodingBox)
    credential = real_credential()
    authorization = authorization_for(credential)
    with pytest.raises(RuntimeError):
        store.install_real_credential(
            credential, authorization=authorization, now=NOW, self_test=passing_self_test
        )
    assert not store.master_key_path.exists()
    assert not store.record_path.exists()
    assert not store.commit_marker_path.exists()
    assert not store.is_installed()


def test_receipt_never_leaks_secret_material(tmp_path: Path) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    credential = real_credential()
    authorization = authorization_for(credential)
    receipt, _ = store.install_real_credential(
        credential, authorization=authorization, now=NOW, self_test=passing_self_test
    )
    dumped = json.dumps(receipt.to_json())
    assert credential.key_id not in dumped
    assert credential.private_key_pem.decode(errors="ignore") not in dumped
    assert credential.key_id not in repr(receipt)
    assert "BEGIN PRIVATE KEY" not in repr(receipt)


# --------------------------------------------------------------------------------------
# CROSS-PROCESS LOCKING / CONCURRENCY (Gemini delta repair: 2026-08-18)
# --------------------------------------------------------------------------------------
#
# fcntl.flock locks are associated with the *open file description*, not the process, so
# two independent os.open() calls on the same lock file -- even from threads inside this
# single test process -- genuinely contend against each other at the kernel level. This lets
# these tests force the exact interleaving that would have raced under the reviewed
# implementation, deterministically, without needing real subprocesses or Postgres.


class ConcurrencyProbe:
    """A self-test callable that records whether two transactions ever overlapped."""

    def __init__(self, *, delay: float = 0.05, outcome: str = "PASS") -> None:
        self.delay = delay
        self.outcome = outcome
        self._guard = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.calls = 0

    def __call__(self, credential: ProductionWriteCredential, at: datetime) -> SignerSelfTestResult:
        with self._guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls += 1
        try:
            time.sleep(self.delay)
            if self.outcome == "PASS":
                return passing_self_test(credential, at)
            return failing_self_test(credential, at)
        finally:
            with self._guard:
                self.active -= 1


def _run_real_install(
    store: ProtectedWriteCredentialStore,
    credential: ProductionWriteCredential,
    self_test: Any,
    results: dict[str, Any],
    key: str,
    barrier: threading.Barrier | None = None,
) -> None:
    if barrier is not None:
        barrier.wait()
    try:
        receipt, signer_result = store.install_real_credential(
            credential,
            authorization=authorization_for(credential),
            now=datetime.now(UTC),
            self_test=self_test,
        )
        results[key] = ("ok", receipt, signer_result)
    except Exception as exc:
        results[key] = ("error", exc, None)


def _run_fixture_install(
    store: ProtectedWriteCredentialStore,
    credential: ProductionWriteCredential,
    results: dict[str, Any],
    key: str,
    barrier: threading.Barrier | None = None,
) -> None:
    if barrier is not None:
        barrier.wait()
    try:
        receipt = store.install(credential, now=datetime.now(UTC))
        results[key] = ("ok", receipt)
    except Exception as exc:
        results[key] = ("error", exc)


def test_two_concurrent_real_installers_only_one_succeeds(tmp_path: Path) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    probe = ConcurrencyProbe(delay=0.08)
    results: dict[str, Any] = {}
    barrier = threading.Barrier(2)
    credential_a = real_credential(key_id="candidate-a")
    credential_b = real_credential(key_id="candidate-b")
    thread_a = threading.Thread(
        target=_run_real_install, args=(store, credential_a, probe, results, "a", barrier)
    )
    thread_b = threading.Thread(
        target=_run_real_install, args=(store, credential_b, probe, results, "b", barrier)
    )
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=10)
    thread_b.join(timeout=10)

    # The exclusive lock spans the entire check/write/self-test/commit sequence: the loser
    # cannot even begin its transaction until the winner has fully committed and released the
    # lock, so by the time the loser checks is_installed() it is already True -- it is
    # rejected there and never reaches the self-test at all. Only one call, never overlapping.
    assert probe.max_active == 1
    assert probe.calls == 1

    outcomes = [results["a"], results["b"]]
    successes = [o for o in outcomes if o[0] == "ok" and o[2] is not None and o[2].succeeded]
    failures = [o for o in outcomes if o[0] == "error"]
    assert len(successes) == 1
    assert len(failures) == 1
    assert "already installed" in str(failures[0][1])
    assert store.is_installed()


def test_failed_installer_never_deletes_concurrently_succeeding_installer(
    tmp_path: Path,
) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    results: dict[str, Any] = {}
    entered_self_test = threading.Event()
    release_a = threading.Event()

    def blocking_failing_self_test(
        credential: ProductionWriteCredential, at: datetime
    ) -> SignerSelfTestResult:
        entered_self_test.set()
        assert release_a.wait(timeout=5)
        return failing_self_test(credential, at)

    credential_a = real_credential(key_id="candidate-fail")
    credential_b = real_credential(key_id="candidate-pass")

    thread_a = threading.Thread(
        target=_run_real_install,
        args=(store, credential_a, blocking_failing_self_test, results, "a"),
    )
    thread_a.start()
    assert entered_self_test.wait(timeout=5)  # A now holds the exclusive lock

    thread_b = threading.Thread(
        target=_run_real_install, args=(store, credential_b, passing_self_test, results, "b")
    )
    thread_b.start()
    time.sleep(0.1)  # B would have finished long ago if it were not genuinely blocked
    assert "b" not in results  # B has not entered its transaction at all yet
    assert not store.is_installed()

    release_a.set()
    thread_a.join(timeout=5)
    thread_b.join(timeout=5)

    assert results["a"][0] == "ok"
    assert results["a"][2] is not None and not results["a"][2].succeeded
    assert results["b"][0] == "ok"
    assert results["b"][2] is not None and results["b"][2].succeeded
    assert store.is_installed()
    installed_receipt = results["b"][1]
    assert installed_receipt.credential_fingerprint == enrollment_mod._candidate_fingerprint(
        credential_b
    )


def test_successful_installer_blocks_second_and_stays_intact(tmp_path: Path) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    credential_a = real_credential(key_id="candidate-first")
    _receipt_a, result_a = store.install_real_credential(
        credential_a,
        authorization=authorization_for(credential_a),
        now=NOW,
        self_test=passing_self_test,
    )
    assert result_a.succeeded and store.is_installed()
    master_before = store.master_key_path.read_bytes()
    record_before = store.record_path.read_bytes()
    marker_before = store.commit_marker_path.read_bytes()

    credential_b = real_credential(key_id="candidate-second")
    with pytest.raises(PermissionError, match="already installed"):
        store.install_real_credential(
            credential_b,
            authorization=authorization_for(credential_b),
            now=NOW,
            self_test=passing_self_test,
        )

    # No silent overwrite: the first installer's artifacts are byte-for-byte unchanged.
    assert store.master_key_path.read_bytes() == master_before
    assert store.record_path.read_bytes() == record_before
    assert store.commit_marker_path.read_bytes() == marker_before
    assert store.is_installed()


def test_fixture_and_real_installs_cannot_both_succeed_against_same_store(
    tmp_path: Path,
) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    fixture_credential = ProductionWriteCredential(
        "fixture-key", genkey(), REQUIRED_LIVE_WRITE_SCOPES, fixture_only=True
    )
    real_cred = real_credential(key_id="candidate-real")
    probe = ConcurrencyProbe(delay=0.05)
    results: dict[str, Any] = {}
    barrier = threading.Barrier(2)
    thread_fixture = threading.Thread(
        target=_run_fixture_install, args=(store, fixture_credential, results, "fixture", barrier)
    )
    thread_real = threading.Thread(
        target=_run_real_install, args=(store, real_cred, probe, results, "real", barrier)
    )
    thread_fixture.start()
    thread_real.start()
    thread_fixture.join(timeout=10)
    thread_real.join(timeout=10)

    successes = [name for name in ("fixture", "real") if results[name][0] == "ok"]
    assert len(successes) == 1
    assert store.is_installed()
    if "real" in successes:
        assert results["real"][2] is not None and results["real"][2].succeeded
    else:
        assert results["fixture"][0] == "ok"
        # the fixture install must never be reachable for a non-fixture credential's
        # fingerprint, and vice versa -- confirmed by the mutually-exclusive success above.


def test_rollback_never_touches_pre_existing_valid_installation(tmp_path: Path) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    existing = real_credential(key_id="candidate-existing")
    _receipt, result = store.install_real_credential(
        existing, authorization=authorization_for(existing), now=NOW, self_test=passing_self_test
    )
    assert result.succeeded
    master_before = store.master_key_path.read_bytes()
    record_before = store.record_path.read_bytes()

    other = real_credential(key_id="candidate-other")
    calls: list[str] = []

    def tracking_failing_self_test(
        credential: ProductionWriteCredential, at: datetime
    ) -> SignerSelfTestResult:
        calls.append(credential.key_id)
        return failing_self_test(credential, at)

    with pytest.raises(PermissionError, match="already installed"):
        store.install_real_credential(
            other,
            authorization=authorization_for(other),
            now=NOW,
            self_test=tracking_failing_self_test,
        )
    assert calls == []  # rejected before persistence -- the self-test never even ran
    assert store.master_key_path.read_bytes() == master_before
    assert store.record_path.read_bytes() == record_before
    assert store.is_installed()


def test_partial_write_failure_is_recoverable_by_next_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    credential = real_credential(key_id="candidate-partial")
    original = enrollment_mod._create_only_text

    def flaky_create_only_text(path: Path, value: str) -> None:
        raise RuntimeError("synthetic failure after master key write")

    monkeypatch.setattr(enrollment_mod, "_create_only_text", flaky_create_only_text)
    with pytest.raises(RuntimeError):
        store.install_real_credential(
            credential,
            authorization=authorization_for(credential),
            now=NOW,
            self_test=passing_self_test,
        )
    assert not store.master_key_path.exists()
    assert not store.record_path.exists()
    assert not store.is_installed()

    monkeypatch.setattr(enrollment_mod, "_create_only_text", original)
    _receipt, result = store.install_real_credential(
        credential,
        authorization=authorization_for(credential),
        now=NOW,
        self_test=passing_self_test,
    )
    assert result.succeeded
    assert store.is_installed()


@pytest.mark.parametrize(
    "make_partial_state",
    [
        lambda store: store.master_key_path.write_bytes(b"partial-master-only"),
        lambda store: (
            store.master_key_path.write_bytes(b"m"),
            store.record_path.write_bytes(b"r"),
        ),
        lambda store: (
            store.master_key_path.write_bytes(b"m"),
            store.record_path.write_bytes(b"r"),
            store.commit_marker_path.write_bytes(b"CORRUPTED-MARKER\n"),
        ),
        lambda store: store.commit_marker_path.write_bytes(enrollment_mod._COMMIT_MARKER_CONTENT),
    ],
)
def test_partial_state_is_never_installed_and_is_recoverable_under_lock(
    tmp_path: Path, make_partial_state: Any
) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    store.directory.mkdir(parents=True, exist_ok=True)
    os.chmod(store.directory, 0o700)
    make_partial_state(store)
    assert not store.is_installed()

    credential = real_credential(key_id="candidate-recovery")
    _receipt, result = store.install_real_credential(
        credential,
        authorization=authorization_for(credential),
        now=NOW,
        self_test=passing_self_test,
    )
    assert result.succeeded
    assert store.is_installed()


def test_lock_is_released_after_exception_inside_transaction(tmp_path: Path) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    with pytest.raises(RuntimeError), store.exclusive():
        raise RuntimeError("synthetic mid-transaction failure")

    acquired = threading.Event()

    def try_acquire() -> None:
        with store.exclusive():
            acquired.set()

    thread = threading.Thread(target=try_acquire)
    thread.start()
    thread.join(timeout=5)
    assert acquired.is_set()  # a fresh acquisition did not hang behind the released lock


def test_lock_file_permissions_and_no_secret_content(tmp_path: Path) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    with store.exclusive():
        pass
    assert oct(store.lock_path.stat().st_mode & 0o777) == "0o600"
    assert store.lock_path.read_bytes() == b""


# --------------------------------------------------------------------------------------
# SIGNER
# --------------------------------------------------------------------------------------


def test_signer_self_test_signs_and_locally_verifies_non_request_challenge() -> None:
    credential = real_credential()
    result = run_real_signer_self_test(credential=credential, now=NOW)
    assert result.succeeded
    assert result.signature_sha256 is not None
    assert result.public_key_sha256 is not None
    assert result.challenge_domain == SIGNER_SELF_TEST_DOMAIN


def test_signer_self_test_challenge_can_never_be_a_valid_request_message() -> None:
    from services.production_execution.signer_self_test import _self_test_challenge

    challenge = _self_test_challenge("abc123", NOW)
    # A real Kalshi request-signature message is exactly f"{timestamp_ms}{METHOD}{PATH}":
    # digits first, immediately followed by an HTTP method. The self-test challenge always
    # begins with a non-numeric domain string, so it can never be confused with one.
    assert not challenge[:1].isdigit()
    assert challenge.startswith(SIGNER_SELF_TEST_DOMAIN.encode())
    for method in (b"GET", b"POST", b"DELETE"):
        assert not challenge.lstrip(b"0123456789").startswith(method)


def test_signer_self_test_fails_closed_on_malformed_private_key() -> None:
    credential = ProductionWriteCredential(
        "bad-key", b"not-a-real-private-key", REQUIRED_LIVE_WRITE_SCOPES, fixture_only=True
    )
    result = run_real_signer_self_test(credential=credential, now=NOW)
    assert not result.succeeded
    assert result.reason is not None
    assert result.signature_sha256 is None


def test_signer_self_test_reuses_production_signer_primitive() -> None:
    from services.production_execution.signer_self_test import _rsa_pss_sha256

    assert _rsa_pss_sha256 is security_boundary._rsa_pss_sha256


def test_signer_self_test_module_has_no_network_or_journal_access() -> None:
    source = Path("services/production_execution/signer_self_test.py").read_text()
    for forbidden in (
        "urllib.request",
        "http.client",
        "requests.post",
        "requests.get",
        "httpx",
        "ProductionJournal",
        "ExactByteSender",
        "send_exact",
    ):
        assert forbidden not in source


def test_production_execute_remains_disarmed(tmp_path: Path) -> None:
    class FixedClock:
        def now(self) -> datetime:
            return NOW

    boundary = SignAndSendBoundary(ProductionJournal(tmp_path / "journal.db"), FixedClock())
    with pytest.raises(BoundaryError, match="DISARMED"):
        boundary.production_execute(None)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------
# FULL ORCHESTRATION
# --------------------------------------------------------------------------------------


def test_full_installation_succeeds_and_runs_signer_self_test(tmp_path: Path) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    credential = real_credential()
    outcome = install_production_write_credential(
        credential=credential,
        store=store,
        authority_attestation=valid_attestation(),
        live_read_evidence=valid_evidence(),
        explicit_confirmation=CONFIRMATION,
        now=NOW + timedelta(seconds=2),
    )
    assert isinstance(outcome, EnrollmentOutcome)
    assert outcome.signer_self_test.succeeded
    assert store.is_installed()
    dumped = json.dumps(outcome.to_json())
    assert credential.key_id not in dumped
    assert credential.private_key_pem.decode(errors="ignore") not in dumped


def test_installation_rejects_fixture_only_credential(tmp_path: Path) -> None:
    store_credential = ProductionWriteCredential(
        "fixture-key", genkey(), REQUIRED_LIVE_WRITE_SCOPES, fixture_only=True
    )
    with pytest.raises(EnrollmentError, match="non-fixture"):
        install_production_write_credential(
            credential=store_credential,
            store=ProtectedWriteCredentialStore(tmp_path / "write"),
            authority_attestation=valid_attestation(),
            live_read_evidence=valid_evidence(),
            explicit_confirmation=CONFIRMATION,
            now=NOW,
        )


def test_installation_rejects_invalid_authority(tmp_path: Path) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    with pytest.raises(EnrollmentError, match="authority"):
        install_production_write_credential(
            credential=real_credential(),
            store=store,
            authority_attestation=valid_attestation(candidate_overrides={"server_subaccount": 1}),
            live_read_evidence=valid_evidence(),
            explicit_confirmation=CONFIRMATION,
            now=NOW,
        )
    assert not store.is_installed()


def test_installation_rejects_stale_evidence(tmp_path: Path) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    with pytest.raises(EnrollmentError, match="evidence"):
        install_production_write_credential(
            credential=real_credential(),
            store=store,
            authority_attestation=valid_attestation(),
            live_read_evidence=valid_evidence(completed_at=NOW - timedelta(seconds=60)),
            explicit_confirmation=CONFIRMATION,
            now=NOW,
        )
    assert not store.is_installed()


def test_installation_rejects_wrong_confirmation(tmp_path: Path) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    with pytest.raises(EnrollmentError, match="confirmation"):
        install_production_write_credential(
            credential=real_credential(),
            store=store,
            authority_attestation=valid_attestation(),
            live_read_evidence=valid_evidence(),
            explicit_confirmation="wrong",
            now=NOW,
        )
    assert not store.is_installed()


def test_installation_rolls_back_when_signer_self_test_fails(tmp_path: Path) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    credential = ProductionWriteCredential(
        CANDIDATE_KEY_ID,
        b"not-a-real-private-key-but-truthy",
        REQUIRED_LIVE_WRITE_SCOPES,
        fixture_only=False,
    )
    with pytest.raises(EnrollmentError, match="rolled back"):
        install_production_write_credential(
            credential=credential,
            store=store,
            authority_attestation=valid_attestation(),
            live_read_evidence=valid_evidence(),
            explicit_confirmation=CONFIRMATION,
            now=NOW,
        )
    assert not store.is_installed()


def test_enrollment_available_remains_false() -> None:
    assert enrollment_available() is False


# --------------------------------------------------------------------------------------
# SECRET INPUT / CLI
# --------------------------------------------------------------------------------------


def test_cli_parser_only_accepts_private_key_via_fd() -> None:
    options = {
        option for action in enrollment_cli._parser()._actions for option in action.option_strings
    }
    assert "--private-key-fd" in options
    assert not any("private-key" in option and option != "--private-key-fd" for option in options)
    assert "--private-key" not in options
    assert "--key" not in options


def test_cli_source_has_no_env_argv_or_stdin_secret_route() -> None:
    # os.environ is used only for XDG_STATE_HOME (a non-secret default store path, matching
    # the read-credential CLI's own convention) -- never for a secret value.
    source = Path("services/production_execution/enrollment_cli.py").read_text()
    assert "PRODUCTION_WRITE_KEY" not in source
    assert "KALSHI_WRITE_PEM" not in source
    assert "private_key_pem" not in source or "read_private_key_fd" in source
    assert "sys.argv" not in source
    assert "input(" not in source
    assert "--private-key" not in source or "--private-key-fd" in source


def test_cli_end_to_end_installs_and_prints_disarmed_guarantees(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    credential_pem = genkey()
    key_id_path = tmp_path / "key-id"
    key_id_path.write_text(CANDIDATE_KEY_ID)
    attestation_path = tmp_path / "attestation.json"
    attestation_path.write_text(json.dumps(valid_attestation()))
    evidence_path = tmp_path / "evidence.json"
    # The CLI stamps installation time with the real wall clock, so the evidence must be
    # fresh relative to *now*, not the fixed NOW constant other tests use.
    evidence_path.write_text(json.dumps(valid_evidence(completed_at=datetime.now(UTC))))
    store_dir = tmp_path / "store"

    read_fd, write_fd = os.pipe()
    os.write(write_fd, credential_pem)
    os.close(write_fd)

    exit_code = enrollment_cli.main(
        [
            "--key-id-file",
            str(key_id_path),
            "--private-key-fd",
            str(read_fd),
            "--authority-attestation",
            str(attestation_path),
            "--live-read-evidence",
            str(evidence_path),
            "--store-dir",
            str(store_dir),
            "--confirm",
            CONFIRMATION,
        ]
    )
    os.close(read_fd)
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "PRODUCTION_ARMED: DISARMED" in captured.out
    assert "REAL_MUTATION: NOT TESTED" in captured.out
    assert "ORDER_SENT: NO" in captured.out
    assert CANDIDATE_KEY_ID not in captured.out
    assert credential_pem.decode(errors="ignore") not in captured.out
    assert ProtectedWriteCredentialStore(store_dir).is_installed()


def test_cli_rejects_wrong_confirmation_without_leaking_secrets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    credential_pem = genkey()
    key_id_path = tmp_path / "key-id"
    key_id_path.write_text(CANDIDATE_KEY_ID)
    attestation_path = tmp_path / "attestation.json"
    attestation_path.write_text(json.dumps(valid_attestation()))
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(valid_evidence()))
    store_dir = tmp_path / "store"

    read_fd, write_fd = os.pipe()
    os.write(write_fd, credential_pem)
    os.close(write_fd)

    exit_code = enrollment_cli.main(
        [
            "--key-id-file",
            str(key_id_path),
            "--private-key-fd",
            str(read_fd),
            "--authority-attestation",
            str(attestation_path),
            "--live-read-evidence",
            str(evidence_path),
            "--store-dir",
            str(store_dir),
            "--confirm",
            "wrong",
        ]
    )
    os.close(read_fd)
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "BLOCKER" in captured.err
    assert CANDIDATE_KEY_ID not in captured.err
    assert credential_pem.decode(errors="ignore") not in captured.err
    assert not ProtectedWriteCredentialStore(store_dir).is_installed()


def test_no_secret_environment_variables_are_read_by_m27g() -> None:
    assert not any(name in os.environ for name in ("PRODUCTION_WRITE_KEY", "KALSHI_WRITE_PEM"))


# --------------------------------------------------------------------------------------
# SAFETY
# --------------------------------------------------------------------------------------


def test_m27g_modules_never_reference_transport_or_journal_mutation() -> None:
    for path in (
        Path("services/production_execution/enrollment.py"),
        Path("services/production_execution/enrollment_cli.py"),
    ):
        source = path.read_text()
        assert "http.client" not in source
        assert "urllib.request" not in source
        assert "requests.post" not in source
        assert "ExactByteSender" not in source
        assert "send_exact" not in source
        assert "possibly_sent" not in source


def test_m27d_and_forecasting_are_untouched_by_this_module_set() -> None:
    for root in ("services/supervised_canary/m27d.py", "services/forecasting"):
        assert Path(root).exists()
    m27d_source = Path("services/supervised_canary/m27d.py").read_text()
    assert "production_execution.enrollment" not in m27d_source
    assert "signer_self_test" not in m27d_source
