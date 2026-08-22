from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import secrets
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
from services.neutral_security import SecretBox
from services.production_execution import enrollment as enrollment_mod
from services.production_execution import installed_credential_verification as m27h
from services.production_execution import signer_self_test as signer_self_test_mod
from services.production_execution.credentials import (
    REQUIRED_LIVE_WRITE_SCOPES,
    ProductionWriteCredential,
    enrollment_available,
)
from services.production_execution.enrollment import (
    OperatorReleaseAuthorization,
    ProtectedWriteCredentialStore,
)
from services.production_execution.signer_self_test import run_real_signer_self_test
from services.supervised_canary.readiness_report import operator_evidence, render_operator_readiness

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


def authorization_for(credential: ProductionWriteCredential) -> OperatorReleaseAuthorization:
    return OperatorReleaseAuthorization(enrollment_mod._candidate_fingerprint(credential))


def passing_self_test(credential: ProductionWriteCredential, at: datetime):
    return run_real_signer_self_test(credential=credential, now=at)


def install_committed_real_credential(
    store: ProtectedWriteCredentialStore, *, credential: ProductionWriteCredential | None = None
) -> ProductionWriteCredential:
    credential = credential or real_credential()
    _receipt, result = store.install_real_credential(
        credential,
        authorization=authorization_for(credential),
        now=NOW,
        self_test=passing_self_test,
    )
    assert result.succeeded
    return credential


_OMIT = object()


def write_raw_store(
    store: ProtectedWriteCredentialStore,
    *,
    key_id: str = CANDIDATE_KEY_ID,
    private_key_pem: bytes | None = None,
    fixture_only: Any = False,
    record_fields: dict[str, Any] | None = None,
    master: bytes | None = None,
    marker: bytes | None = enrollment_mod._COMMIT_MARKER_CONTENT,
    write_master: bool = True,
    write_record: bool = True,
    write_marker: bool = True,
    corrupt_record: bool = False,
) -> bytes:
    """Directly construct raw store files from synthetic material, bypassing self-test gating.

    This exists only so STORE-level adversarial tests can reach store states
    ``install``/``install_real_credential`` structurally cannot produce (a committed record
    whose signer fails, a corrupt ciphertext, a mismatched master key) without weakening the
    real install path itself. All key material used here is synthetic/openssl-generated.
    """
    store.directory.mkdir(parents=True, exist_ok=True)
    os.chmod(store.directory, 0o700)
    master = master if master is not None else secrets.token_bytes(32)
    if write_master:
        store.master_key_path.write_bytes(master)
        os.chmod(store.master_key_path, 0o600)
    if write_record:
        if record_fields is not None:
            record = record_fields
        else:
            record = {
                "key_id": key_id,
                "private_key_pem": (private_key_pem or genkey()).decode("ascii"),
            }
            if fixture_only is not _OMIT:
                record["fixture_only"] = fixture_only
        payload = json.dumps(record, sort_keys=True).encode("ascii")
        content = (
            "###not-a-valid-sealed-envelope###"
            if corrupt_record
            else SecretBox(master).seal(payload)
        )
        store.record_path.write_text(content)
        os.chmod(store.record_path, 0o600)
    if write_marker:
        store.commit_marker_path.write_bytes(marker if marker is not None else b"BAD-MARKER\n")
        os.chmod(store.commit_marker_path, 0o600)
    return master


def valid_m27h_payload(
    *,
    key_id_hash: str = CANDIDATE_KEY_ID_HASH,
    schema: str = m27h.INSTALLED_CREDENTIAL_EVIDENCE_SCHEMA,
    environment: str = "PRODUCTION",
    store_state: str = "COMMITTED",
    authority_classification: str = "PASS",
    signer_classification: str = "PASS",
    observed_at: datetime = NOW,
    completed_at: datetime = NOW,
    signer_completed_at: datetime = NOW,
    classification: str = "PASS",
) -> dict[str, Any]:
    """A structurally valid M27H artifact. Defaults produce a fully fresh, ordered artifact
    (``observed_at == signer_completed_at == completed_at == NOW``); callers shift individual
    timestamps to build stale/future/misordered adversarial variants. Deliberately carries no
    ``production_armed``/``real_mutation`` keys -- the M27H schema no longer includes them
    (Gemini delta repair): this verifier has no authority to prove either state.
    """
    return {
        "schema": schema,
        "software_version": m27h.SOFTWARE_VERSION,
        "environment": environment,
        "observed_at": observed_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "store_state": store_state,
        "key_id_hash": key_id_hash,
        "credential_fingerprint": "f" * 64,
        "authority_classification": authority_classification,
        "authority_reason": None,
        "signer_classification": signer_classification,
        "signer_challenge_domain": signer_self_test_mod.SIGNER_SELF_TEST_DOMAIN,
        "signer_reason": None,
        "signer_completed_at": signer_completed_at.isoformat(),
        "classification": classification,
        "reason": None,
    }


# --------------------------------------------------------------------------------------
# STORE
# --------------------------------------------------------------------------------------


def test_valid_committed_installation_is_read_and_verified(tmp_path: Path) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    credential = install_committed_real_credential(store)
    result = m27h.verify_installed_write_credential(
        store=store, authority_attestation=valid_attestation(), now=NOW + timedelta(seconds=1)
    )
    assert result.succeeded
    assert result.store_state == "COMMITTED"
    assert result.key_id_hash == hashlib.sha256(credential.key_id.encode()).hexdigest()
    assert result.credential_fingerprint == enrollment_mod._candidate_fingerprint(credential)
    assert result.authority_classification == "PASS"
    assert result.signer_classification == "PASS"


def test_missing_marker_is_rejected(tmp_path: Path) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    write_raw_store(store, write_marker=False)
    result = m27h.verify_installed_write_credential(
        store=store, authority_attestation=valid_attestation(), now=NOW
    )
    assert not result.succeeded
    assert result.store_state == "NOT_COMMITTED"


def test_bad_marker_is_rejected(tmp_path: Path) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    write_raw_store(store, marker=b"NOT-THE-REAL-MARKER\n")
    result = m27h.verify_installed_write_credential(
        store=store, authority_attestation=valid_attestation(), now=NOW
    )
    assert not result.succeeded


def test_missing_master_is_rejected(tmp_path: Path) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    write_raw_store(store, write_master=False)
    result = m27h.verify_installed_write_credential(
        store=store, authority_attestation=valid_attestation(), now=NOW
    )
    assert not result.succeeded


def test_missing_record_is_rejected(tmp_path: Path) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    write_raw_store(store, write_record=False)
    result = m27h.verify_installed_write_credential(
        store=store, authority_attestation=valid_attestation(), now=NOW
    )
    assert not result.succeeded


def test_corrupt_encrypted_record_is_rejected(tmp_path: Path) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    write_raw_store(store, corrupt_record=True)
    result = m27h.verify_installed_write_credential(
        store=store, authority_attestation=valid_attestation(), now=NOW
    )
    assert not result.succeeded
    assert "decrypt" in (result.reason or "").lower()


def test_wrong_master_key_is_rejected(tmp_path: Path) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    write_raw_store(store)
    store.master_key_path.write_bytes(secrets.token_bytes(32))  # swap for an unrelated key
    result = m27h.verify_installed_write_credential(
        store=store, authority_attestation=valid_attestation(), now=NOW
    )
    assert not result.succeeded
    assert "decrypt" in (result.reason or "").lower()


def test_incomplete_crashed_state_is_rejected(tmp_path: Path) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    write_raw_store(store, write_marker=False)  # crashed after sealed writes, before commit
    assert not store.is_installed()
    result = m27h.verify_installed_write_credential(
        store=store, authority_attestation=valid_attestation(), now=NOW
    )
    assert not result.succeeded
    assert result.store_state == "NOT_COMMITTED"


def test_malformed_decoded_record_is_rejected(tmp_path: Path) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    write_raw_store(store, record_fields={"unexpected": "shape"})
    result = m27h.verify_installed_write_credential(
        store=store, authority_attestation=valid_attestation(), now=NOW
    )
    assert not result.succeeded


def test_lock_held_during_coherent_read_blocks_concurrent_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    install_committed_real_credential(store)
    entered = threading.Event()
    release = threading.Event()

    def slow_self_test(*, credential: ProductionWriteCredential, now: datetime):
        entered.set()
        assert release.wait(timeout=5)
        return passing_self_test(credential, now)

    monkeypatch.setattr(m27h, "run_real_signer_self_test", slow_self_test)
    result_holder: dict[str, Any] = {}

    def run_verification() -> None:
        result_holder["result"] = m27h.verify_installed_write_credential(
            store=store, authority_attestation=valid_attestation(), now=NOW
        )

    thread = threading.Thread(target=run_verification)
    thread.start()
    assert entered.wait(timeout=5)  # verification now holds the exclusive lock mid-self-test

    second_acquired = threading.Event()

    def try_acquire() -> None:
        with store.exclusive():
            second_acquired.set()

    second = threading.Thread(target=try_acquire)
    second.start()
    time.sleep(0.1)
    assert not second_acquired.is_set()  # genuinely blocked behind the still-held lock

    release.set()
    thread.join(timeout=5)
    second.join(timeout=5)
    assert second_acquired.is_set()
    assert result_holder["result"].succeeded


def test_foreign_lock_token_is_rejected(tmp_path: Path) -> None:
    store_a = ProtectedWriteCredentialStore(tmp_path / "write")
    store_b = ProtectedWriteCredentialStore(tmp_path / "write")
    write_raw_store(store_a)
    with store_a.exclusive() as lock_a, pytest.raises(PermissionError, match="does not belong"):
        store_b._decode_committed_credential(lock_a)


def test_fixture_committed_store_fails_closed(tmp_path: Path) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    fixture_credential = ProductionWriteCredential(
        "fixture-key", genkey(), REQUIRED_LIVE_WRITE_SCOPES, fixture_only=True
    )
    receipt = store.install(fixture_credential, now=NOW)
    assert receipt is not None
    assert store.is_installed()
    result = m27h.verify_installed_write_credential(
        store=store,
        authority_attestation=valid_attestation(
            key_id_hash=hashlib.sha256(b"fixture-key").hexdigest()
        ),
        now=NOW,
    )
    assert not result.succeeded
    assert "fixture" in (result.reason or "").lower()


def test_pre_m27h_real_install_without_fixture_flag_is_still_treated_as_real(
    tmp_path: Path,
) -> None:
    """Legacy item 12: a synthetic representation of the actual legacy-real (pre-M27H) format
    can still PASS -- but only because the exact candidate authority binds correctly below."""
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    write_raw_store(store, fixture_only=_OMIT)  # schema predating this milestone's flag
    result = m27h.verify_installed_write_credential(
        store=store, authority_attestation=valid_attestation(), now=NOW
    )
    assert result.succeeded


def test_legacy_record_without_discriminator_cannot_pass_against_wrong_candidate(
    tmp_path: Path,
) -> None:
    """Legacy item 11: lacking ``fixture_only`` makes a record *eligible* to be treated as
    real (see above), but eligibility alone is never sufficient -- authority binding still
    requires the decrypted key ID to match the exact independently attested candidate. An
    unrelated key stored under the legacy schema can never PASS merely by lacking the
    discriminator.
    """
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    write_raw_store(store, key_id="unrelated-legacy-key", fixture_only=_OMIT)
    result = m27h.verify_installed_write_credential(
        store=store,
        authority_attestation=valid_attestation(),
        now=NOW,  # attests CANDIDATE_KEY_ID
    )
    assert not result.succeeded
    assert result.authority_classification != "PASS"


# --------------------------------------------------------------------------------------
# CREDENTIAL CONTAINMENT (Gemini delta repair blocker 1)
# --------------------------------------------------------------------------------------


def test_store_exposes_no_public_credential_returning_api() -> None:
    """The store must never again expose a public method that hands out a decrypted
    credential -- neither the old ``read_committed_credential_for_verification`` name nor any
    differently-named equivalent (``load_real_credential``, ``get_production_credential``,
    a generic ``with_credential(callback)``, etc.). ``install``/``install_real_credential`` are
    the only public methods that legitimately mention a credential, and they *accept* one --
    they never return one.
    """
    forbidden_tokens = (
        "credential",
        "private_key",
        "decode",
        "decrypt",
        "load_real",
        "get_production",
        "with_credential",
    )
    allowed_public_methods = {"install", "install_real_credential"}
    public_methods = [
        name
        for name in dir(ProtectedWriteCredentialStore)
        if not name.startswith("_") and callable(getattr(ProtectedWriteCredentialStore, name, None))
    ]
    suspicious = [
        name
        for name in public_methods
        if name not in allowed_public_methods
        and any(token in name.lower() for token in forbidden_tokens)
    ]
    assert suspicious == []
    # sanity: the old rejected public name is gone, not merely absent from this heuristic scan
    assert not hasattr(ProtectedWriteCredentialStore, "read_committed_credential_for_verification")


def test_verify_installed_write_credential_never_returns_a_credential_object(
    tmp_path: Path,
) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    install_committed_real_credential(store)
    result = m27h.verify_installed_write_credential(
        store=store, authority_attestation=valid_attestation(), now=NOW
    )
    assert isinstance(result, m27h.InstalledCredentialVerification)
    assert not isinstance(result, ProductionWriteCredential)
    for field in dataclasses.fields(result):
        field_value = getattr(result, field.name)
        assert not isinstance(field_value, ProductionWriteCredential)
        assert not isinstance(field_value, bytes)  # no raw PEM/key bytes anywhere in evidence


def test_decode_helper_is_referenced_only_by_sanctioned_consumers() -> None:
    """Only the store definition, M27H verifier, M27O send boundary, and M27O
    reconciliation boundary may reference the private decrypt helper. No other runtime
    module may acquire plaintext production credential material.
    """
    allowed = {
        Path("services/production_execution/enrollment.py"),
        Path("services/production_execution/installed_credential_verification.py"),
        Path("services/production_execution/m27o_live_canary.py"),
        Path("services/production_execution/m27o_reconciliation.py"),
    }
    offenders = [
        str(path)
        for path in Path("services").rglob("*.py")
        if path not in allowed and "_decode_committed_credential" in path.read_text()
    ]
    assert offenders == []
    # sanity: the symbol really is used by every allowed file, so this test isn't vacuous
    for path in allowed:
        assert "_decode_committed_credential" in path.read_text()


def test_no_generic_credential_callback_primitive_exists() -> None:
    """Guards against merely renaming the loader into a generic callback primitive -- a public
    ``with_credential(callback)``-style method would still hand plaintext credential material
    to arbitrary caller code (e.g. ``lambda cred: cred``), reproducing the exact escape this
    delta repairs under a different name.
    """
    enrollment_source = Path("services/production_execution/enrollment.py").read_text()
    verifier_source = Path(
        "services/production_execution/installed_credential_verification.py"
    ).read_text()
    for source in (enrollment_source, verifier_source):
        assert "def with_credential" not in source
        assert "def load_real_credential" not in source
        assert "def get_production_credential" not in source


# --------------------------------------------------------------------------------------
# AUTHORITY BINDING
# --------------------------------------------------------------------------------------


def test_valid_exact_candidate_binds_successfully(tmp_path: Path) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    install_committed_real_credential(store)
    result = m27h.verify_installed_write_credential(
        store=store, authority_attestation=valid_attestation(), now=NOW
    )
    assert result.authority_classification == "PASS"
    assert result.succeeded


@pytest.mark.parametrize(
    "candidate_overrides",
    [
        {"key_id_hash": "0" * 64},
        {"server_scopes": ["read"]},
        {"server_scopes": ["read", "write"]},
        {"server_scopes": ["read", "write::trade", "write::transfer"]},
        {"server_subaccount": 1},
        {"server_subaccount": None},
        {"server_subaccount": True},
        {"unique_matches": 2},
        {"unique_matches": 0},
    ],
)
def test_authority_binding_violations_fail_closed_end_to_end(
    tmp_path: Path, candidate_overrides: dict[str, Any]
) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    install_committed_real_credential(store)
    result = m27h.verify_installed_write_credential(
        store=store,
        authority_attestation=valid_attestation(candidate_overrides=candidate_overrides),
        now=NOW,
    )
    assert not result.succeeded
    assert result.signer_classification == "NOT_RUN"  # never re-signs when authority fails


@pytest.mark.parametrize("payload", [None, {}, "not-a-dict", 123, {"schema": "wrong"}])
def test_malformed_attestation_fails_closed(tmp_path: Path, payload: object) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    install_committed_real_credential(store)
    result = m27h.verify_installed_write_credential(
        store=store, authority_attestation=payload, now=NOW
    )
    assert not result.succeeded


def test_authority_validator_is_reused_not_reimplemented() -> None:
    assert (
        m27h.validate_authority_attestation_for_installation
        is enrollment_mod.validate_authority_attestation_for_installation
    )


# --------------------------------------------------------------------------------------
# SIGNER RE-VALIDATION
# --------------------------------------------------------------------------------------


def test_signer_success_yields_overall_pass(tmp_path: Path) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    install_committed_real_credential(store)
    result = m27h.verify_installed_write_credential(
        store=store, authority_attestation=valid_attestation(), now=NOW
    )
    assert result.signer_classification == "PASS"
    assert result.signer_challenge_domain == signer_self_test_mod.SIGNER_SELF_TEST_DOMAIN


def test_signer_failure_on_malformed_key_fails_closed(tmp_path: Path) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    write_raw_store(store, private_key_pem=b"not-a-real-private-key")
    result = m27h.verify_installed_write_credential(
        store=store, authority_attestation=valid_attestation(), now=NOW
    )
    assert not result.succeeded
    assert result.signer_classification == "FAIL"


def test_signer_primitive_is_reused_not_reimplemented() -> None:
    assert m27h.run_real_signer_self_test is signer_self_test_mod.run_real_signer_self_test


def test_verifier_module_has_no_network_or_transport_markers() -> None:
    source = Path("services/production_execution/installed_credential_verification.py").read_text()
    for forbidden in (
        "urllib.request",
        "http.client",
        "requests.post",
        "requests.get",
        "httpx",
        "ExactByteSender",
        "send_exact",
        "possibly_sent",
        "ProductionJournal",
        "SignAndSendBoundary(",
        "production_execute(",
        "import security_boundary",
    ):
        assert forbidden not in source


# --------------------------------------------------------------------------------------
# EVIDENCE
# --------------------------------------------------------------------------------------


def test_evidence_is_secret_free(tmp_path: Path) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    credential = install_committed_real_credential(store)
    result = m27h.verify_installed_write_credential(
        store=store, authority_attestation=valid_attestation(), now=NOW
    )
    dumped = json.dumps(result.to_json())
    assert credential.key_id not in dumped
    assert credential.private_key_pem.decode(errors="ignore") not in dumped
    assert "BEGIN PRIVATE KEY" not in dumped
    assert credential.key_id not in repr(result)
    assert "BEGIN PRIVATE KEY" not in repr(result)


def test_evidence_schema_and_static_fields(tmp_path: Path) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    install_committed_real_credential(store)
    result = m27h.verify_installed_write_credential(
        store=store, authority_attestation=valid_attestation(), now=NOW
    )
    assert result.schema == "kalsh3.m27h.installed-write-credential.v1"
    assert result.software_version == m27h.SOFTWARE_VERSION


def test_evidence_never_carries_arm_or_mutation_declarations(tmp_path: Path) -> None:
    """Gemini important finding: this verifier has no authority to inspect or prove arm/mutation
    state, so the evidence schema must not carry ``production_armed``/``real_mutation`` (or any
    differently-named equivalent) -- ``readiness_report.py`` already independently owns both.
    """
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    install_committed_real_credential(store)
    result = m27h.verify_installed_write_credential(
        store=store, authority_attestation=valid_attestation(), now=NOW
    )
    dumped = result.to_json()
    assert "production_armed" not in dumped
    assert "real_mutation" not in dumped
    assert not hasattr(result, "production_armed")
    assert not hasattr(result, "real_mutation")


def test_readiness_validation_rejects_classification_lie() -> None:
    payload = valid_m27h_payload(authority_classification="FAIL", classification="PASS")
    check = m27h.validate_installed_credential_evidence_for_readiness(payload, now=NOW)
    assert not check.credential_installed
    assert not check.signer_verified_fresh


def test_readiness_validation_candidate_mismatch(tmp_path: Path) -> None:
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    install_committed_real_credential(store)
    result = m27h.verify_installed_write_credential(
        store=store,
        authority_attestation=valid_attestation(key_id_hash="0" * 64),
        now=NOW,
    )
    assert not result.succeeded
    check = m27h.validate_installed_credential_evidence_for_readiness(result.to_json(), now=NOW)
    assert not check.credential_installed


def test_readiness_validation_future_artifact_is_rejected() -> None:
    """Item 7: any future completed/signer timestamps grant no PASS -- for either gate."""
    future = NOW + timedelta(seconds=5)
    payload = valid_m27h_payload(
        observed_at=future, completed_at=future, signer_completed_at=future
    )
    check = m27h.validate_installed_credential_evidence_for_readiness(payload, now=NOW)
    assert not check.credential_installed
    assert not check.signer_verified_fresh


def test_readiness_validation_31_seconds_stale_rejects_both_gates() -> None:
    """Item 5: a uniformly 31-second-old otherwise-valid artifact must not PASS either gate --
    Gemini blocker 2's core fix (PRODUCTION_WRITE_CREDENTIAL is no longer freshness-exempt).
    """
    stale = NOW - timedelta(seconds=31)
    payload = valid_m27h_payload(observed_at=stale, completed_at=stale, signer_completed_at=stale)
    check = m27h.validate_installed_credential_evidence_for_readiness(payload, now=NOW)
    assert not check.credential_installed
    assert not check.signer_verified_fresh


def test_readiness_validation_exactly_at_freshness_boundary_passes() -> None:
    """Item 6: exactly 30 seconds old is inclusive -- both gates PASS."""
    boundary = NOW - timedelta(seconds=30)
    payload = valid_m27h_payload(
        observed_at=boundary, completed_at=boundary, signer_completed_at=boundary
    )
    check = m27h.validate_installed_credential_evidence_for_readiness(payload, now=NOW)
    assert check.credential_installed
    assert check.signer_verified_fresh


def test_readiness_validation_31_seconds_is_rejected_one_second_past_boundary() -> None:
    just_past = NOW - timedelta(seconds=31)
    payload = valid_m27h_payload(
        observed_at=just_past, completed_at=just_past, signer_completed_at=just_past
    )
    check = m27h.validate_installed_credential_evidence_for_readiness(payload, now=NOW)
    assert not check.credential_installed
    assert not check.signer_verified_fresh


def test_readiness_validation_credential_fresh_but_signer_individually_stale() -> None:
    """The two gates are independently derived: a fresh overall artifact whose embedded signer
    timestamp is itself individually stale must still withhold REAL_SIGNER_VALIDATION, even
    though the store-commit portion remains fresh.
    """
    old_observed = NOW - timedelta(seconds=40)
    stale_signer = NOW - timedelta(seconds=31)
    payload = valid_m27h_payload(
        observed_at=old_observed, completed_at=NOW, signer_completed_at=stale_signer
    )
    check = m27h.validate_installed_credential_evidence_for_readiness(payload, now=NOW)
    assert check.credential_installed
    assert not check.signer_verified_fresh


def test_readiness_validation_rejects_signer_completed_before_observed() -> None:
    """Ordering sanity: the artifact promises observed_at <= signer_completed_at <=
    completed_at; a signer timestamp claiming to precede observation is malformed/tampered."""
    payload = valid_m27h_payload(
        observed_at=NOW, completed_at=NOW, signer_completed_at=NOW - timedelta(seconds=5)
    )
    check = m27h.validate_installed_credential_evidence_for_readiness(payload, now=NOW)
    assert not check.signer_verified_fresh


def test_readiness_validation_rejects_completed_before_observed() -> None:
    payload = valid_m27h_payload(
        observed_at=NOW,
        completed_at=NOW - timedelta(seconds=5),
        signer_completed_at=NOW - timedelta(seconds=5),
    )
    check = m27h.validate_installed_credential_evidence_for_readiness(payload, now=NOW)
    assert not check.credential_installed
    assert not check.signer_verified_fresh


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        "not-a-dict",
        123,
        {"schema": "kalsh3.m27h.installed-write-credential.v0"},
    ],
)
def test_readiness_validation_malformed_payload_grants_nothing(payload: object) -> None:
    check = m27h.validate_installed_credential_evidence_for_readiness(payload, now=NOW)
    assert not check.credential_installed
    assert not check.signer_verified_fresh


def test_readiness_validation_wrong_environment_or_store_state_grants_nothing() -> None:
    for override in ({"environment": "SANDBOX"}, {"store_state": "NOT_COMMITTED"}):
        payload = valid_m27h_payload()
        payload.update(override)
        check = m27h.validate_installed_credential_evidence_for_readiness(payload, now=NOW)
        assert not check.credential_installed


# --------------------------------------------------------------------------------------
# READINESS INTEGRATION
# --------------------------------------------------------------------------------------


def test_no_m27h_artifact_keeps_defaults() -> None:
    evidence = operator_evidence(now=NOW)
    assert evidence["PRODUCTION_WRITE_CREDENTIAL"][0] == "NOT INSTALLED"
    assert evidence["REAL_SIGNER_VALIDATION"][0] == "BLOCKED_BY_CREDENTIAL"


def test_valid_fresh_artifact_unlocks_both_gates(tmp_path: Path) -> None:
    path = tmp_path / "m27h.json"
    path.write_text(json.dumps(valid_m27h_payload()))
    evidence = operator_evidence(write_credential_evidence=path, now=NOW + timedelta(seconds=2))
    assert evidence["PRODUCTION_WRITE_CREDENTIAL"][0] == "PASS"
    assert evidence["REAL_SIGNER_VALIDATION"][0] == "PASS"


def test_valid_but_stale_artifact_unlocks_neither_gate(tmp_path: Path) -> None:
    """Gemini blocker 2: a structurally valid but historical (5-minutes-old) artifact must not
    leave PRODUCTION_WRITE_CREDENTIAL at PASS -- readiness never inspects the live store, so
    stale evidence cannot prove the store's *current* state. Both gates stay at their
    fail-closed defaults, exactly as if no evidence had been supplied.
    """
    stale = NOW - timedelta(minutes=5)
    path = tmp_path / "m27h.json"
    path.write_text(
        json.dumps(
            valid_m27h_payload(observed_at=stale, completed_at=stale, signer_completed_at=stale)
        )
    )
    evidence = operator_evidence(write_credential_evidence=path, now=NOW)
    assert evidence["PRODUCTION_WRITE_CREDENTIAL"][0] == "NOT INSTALLED"
    assert evidence["REAL_SIGNER_VALIDATION"][0] == "BLOCKED_BY_CREDENTIAL"


def test_historical_artifact_alone_cannot_prove_current_installation(tmp_path: Path) -> None:
    """Item 8: demonstrate that an artifact that was genuinely fresh and PASS-worthy *at the
    time it was produced* cannot continue to unlock PRODUCTION_WRITE_CREDENTIAL once readiness
    consumes it outside the freshness window -- the exact same artifact bytes, evaluated only
    a bit later, must flip from unlocking to not unlocking.
    """
    path = tmp_path / "m27h.json"
    path.write_text(json.dumps(valid_m27h_payload()))  # fresh relative to NOW at creation
    fresh_evidence = operator_evidence(write_credential_evidence=path, now=NOW)
    assert fresh_evidence["PRODUCTION_WRITE_CREDENTIAL"][0] == "PASS"

    later_evidence = operator_evidence(
        write_credential_evidence=path, now=NOW + timedelta(seconds=31)
    )
    assert later_evidence["PRODUCTION_WRITE_CREDENTIAL"][0] == "NOT INSTALLED"
    assert later_evidence["REAL_SIGNER_VALIDATION"][0] == "BLOCKED_BY_CREDENTIAL"


def test_malformed_artifact_grants_no_pass(tmp_path: Path) -> None:
    path = tmp_path / "m27h.json"
    path.write_text(json.dumps({"schema": "wrong"}))
    evidence = operator_evidence(write_credential_evidence=path, now=NOW)
    assert evidence["PRODUCTION_WRITE_CREDENTIAL"][0] == "NOT INSTALLED"
    assert evidence["REAL_SIGNER_VALIDATION"][0] == "BLOCKED_BY_CREDENTIAL"


@pytest.mark.parametrize(
    "raw_payload",
    [
        json.dumps(valid_m27h_payload()),  # fresh, valid
        json.dumps(
            valid_m27h_payload(
                observed_at=NOW - timedelta(minutes=5),
                completed_at=NOW - timedelta(minutes=5),
                signer_completed_at=NOW - timedelta(minutes=5),
            )
        ),  # stale
        json.dumps({"schema": "wrong"}),  # malformed
        json.dumps(
            valid_m27h_payload(authority_classification="FAIL", classification="PASS")
        ),  # malicious: lying top-level classification
    ],
)
def test_m27h_never_alters_production_armed_or_real_mutation(
    tmp_path: Path, raw_payload: str
) -> None:
    """Item 10: valid fresh, stale, malformed, and malicious M27H evidence all leave
    PRODUCTION_ARMED and REAL_MUTATION exactly at their independent defaults."""
    path = tmp_path / "m27h.json"
    path.write_text(raw_payload)
    evidence = operator_evidence(write_credential_evidence=path, now=NOW)
    assert evidence["PRODUCTION_ARMED"] == ("FAIL", "DISARMED is required in this milestone")
    assert evidence["REAL_MUTATION"] == ("NOT TESTED", "no mutation endpoint was called")


def test_unrelated_final_trade_gates_remain_not_tested(tmp_path: Path) -> None:
    path = tmp_path / "m27h.json"
    path.write_text(json.dumps(valid_m27h_payload(signer_completed_at=NOW)))
    rendered = render_operator_readiness(
        write_credential_evidence=path, now=NOW + timedelta(seconds=2)
    )
    for gate in (
        "market_tradable",
        "rules_current",
        "candidate_current",
        "fee_verified",
        "model_eligible",
        "compliance_clear",
        "global_halt_clear",
        "kills_clear",
        "no_unknown_orders",
        "no_unknown_positions",
    ):
        assert f"{gate}: no complete live snapshot supplied" in rendered


def test_write_credential_evidence_cli_flag_is_wired() -> None:
    source = Path("services/supervised_canary/readiness_report.py").read_text()
    assert "--write-credential-evidence" in source
    assert "write_credential_evidence" in source


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def test_cli_end_to_end_writes_secret_free_evidence_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store_dir = tmp_path / "store"
    store = ProtectedWriteCredentialStore(store_dir)
    credential = install_committed_real_credential(store)
    attestation_path = tmp_path / "attestation.json"
    attestation_path.write_text(json.dumps(valid_attestation()))
    output_path = tmp_path / "evidence.json"

    exit_code = m27h.main(
        [
            "--store-dir",
            str(store_dir),
            "--authority-attestation",
            str(attestation_path),
            "--output",
            str(output_path),
        ]
    )
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "PRODUCTION_ARMED: DISARMED" in captured.out
    assert "REAL_MUTATION: NOT TESTED" in captured.out
    assert credential.key_id not in captured.out
    assert credential.private_key_pem.decode(errors="ignore") not in captured.out

    payload = json.loads(output_path.read_text())
    assert payload["schema"] == "kalsh3.m27h.installed-write-credential.v1"
    assert payload["classification"] == "PASS"
    assert "production_armed" not in payload
    assert "real_mutation" not in payload
    assert credential.key_id not in output_path.read_text()
    assert credential.private_key_pem.decode(errors="ignore") not in output_path.read_text()


def test_cli_produced_evidence_is_fresh_and_unlocks_readiness_immediately(tmp_path: Path) -> None:
    """Item 4, end to end: real wall-clock CLI output, consumed by readiness moments later,
    unlocks both gates."""
    store_dir = tmp_path / "store"
    store = ProtectedWriteCredentialStore(store_dir)
    install_committed_real_credential(store)
    attestation_path = tmp_path / "attestation.json"
    attestation_path.write_text(json.dumps(valid_attestation()))
    output_path = tmp_path / "evidence.json"

    exit_code = m27h.main(
        [
            "--store-dir",
            str(store_dir),
            "--authority-attestation",
            str(attestation_path),
            "--output",
            str(output_path),
        ]
    )
    assert exit_code == 0
    evidence = operator_evidence(write_credential_evidence=output_path, now=datetime.now(UTC))
    assert evidence["PRODUCTION_WRITE_CREDENTIAL"][0] == "PASS"
    assert evidence["REAL_SIGNER_VALIDATION"][0] == "PASS"


def test_cli_reports_failure_without_exception_when_not_installed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    attestation_path = tmp_path / "attestation.json"
    attestation_path.write_text(json.dumps(valid_attestation()))
    output_path = tmp_path / "evidence.json"

    exit_code = m27h.main(
        [
            "--store-dir",
            str(tmp_path / "store"),
            "--authority-attestation",
            str(attestation_path),
            "--output",
            str(output_path),
        ]
    )
    assert exit_code == 1
    payload = json.loads(output_path.read_text())
    assert payload["classification"] == "FAIL"


def test_cli_exit_code_2_on_missing_attestation_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = m27h.main(
        [
            "--store-dir",
            str(tmp_path / "store"),
            "--authority-attestation",
            str(tmp_path / "does-not-exist.json"),
            "--output",
            str(tmp_path / "evidence.json"),
        ]
    )
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "BLOCKER" in captured.err


def test_cli_source_has_no_secret_input_route() -> None:
    source = Path("services/production_execution/installed_credential_verification.py").read_text()
    assert "--private-key" not in source
    assert "--key" not in source
    assert "sys.argv" not in source
    assert "input(" not in source
    assert "PRODUCTION_WRITE_KEY" not in source
    assert "KALSHI_WRITE_PEM" not in source


# --------------------------------------------------------------------------------------
# SAFETY REGRESSION
# --------------------------------------------------------------------------------------


def test_enrollment_available_remains_false() -> None:
    assert enrollment_available() is False


def test_m27d_forecasting_and_security_boundary_are_untouched_by_m27h() -> None:
    for root in ("services/supervised_canary/m27d.py", "services/forecasting"):
        assert Path(root).exists()
    m27d_source = Path("services/supervised_canary/m27d.py").read_text()
    security_boundary_source = Path(
        "services/production_execution/security_boundary.py"
    ).read_text()
    transport_source = Path("services/production_execution/transport.py").read_text()
    for source in (m27d_source, security_boundary_source, transport_source):
        assert "installed_credential_verification" not in source
        assert "m27h" not in source.lower()


def test_no_secret_environment_variables_are_read_by_m27h() -> None:
    assert not any(name in os.environ for name in ("PRODUCTION_WRITE_KEY", "KALSHI_WRITE_PEM"))
