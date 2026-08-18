from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from services.kalshi_account_gateway.production_read_credentials import (
    API_KEYS_PATH,
    PRODUCTION_ORIGIN,
    ProductionReadReply,
)
from services.production_execution import security_boundary
from services.production_execution.credentials import (
    REQUIRED_LIVE_WRITE_SCOPES,
    ProductionWriteCredential,
)
from services.production_execution.enrollment import (
    CONFIRMATION,
    ProtectedWriteCredentialStore,
    WriteCredentialAuthorityError,
    WriteCredentialServerProof,
    enroll_live_write_credential,
    require_live_write_authority,
    verify_live_write_credential_authority,
)
from services.production_execution.requests import create_envelope
from services.production_execution.transport import (
    FixedKalshiProductionTransport,
    ProductionTransportError,
)
from services.supervised_canary.readiness_report import operator_evidence

VALID_PROOF = WriteCredentialServerProof("synthetic-write", frozenset({"read", "write::trade"}), 0)


class FakeMetadataSigner:
    def __init__(self, key_id: str, private_key_pem: bytes) -> None:
        self.key_id = key_id
        self.private_key_pem = private_key_pem

    def headers(self, timestamp_ms: int, method: str, request_target: str) -> dict[str, str]:
        return {"synthetic-auth": f"{self.key_id}:{timestamp_ms}:{method}:{request_target}"}


class FakeMetadataTransport:
    def __init__(self, reply: ProductionReadReply | Exception) -> None:
        self.reply = reply
        self.calls: list[tuple[str, str, dict[str, str], float]] = []

    def get(
        self, origin: str, path: str, headers: Any, *, timeout_seconds: float
    ) -> ProductionReadReply:
        self.calls.append((origin, path, dict(headers), timeout_seconds))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def api_keys_reply(records: Any) -> ProductionReadReply:
    return ProductionReadReply(200, json.dumps({"api_keys": records}).encode())


def fetch_proof(
    reply: ProductionReadReply | Exception, *, key_id: str = "synthetic-write"
) -> WriteCredentialServerProof:
    transport = FakeMetadataTransport(reply)
    return verify_live_write_credential_authority(
        transport,
        ProductionWriteCredential(
            key_id, b"pem-not-used-by-fake-signer", REQUIRED_LIVE_WRITE_SCOPES, fixture_only=True
        ),
        timestamp_ms=123,
        signer_factory=FakeMetadataSigner,
    )


@pytest.fixture
def synthetic_key() -> bytes:
    return subprocess.run(
        [
            "/usr/bin/openssl",
            "genpkey",
            "-algorithm",
            "RSA",
            "-pkeyopt",
            "rsa_keygen_bits:2048",
        ],
        check=True,
        capture_output=True,
    ).stdout


def test_pipe_signer_fallback_cleans_descriptors(
    monkeypatch: pytest.MonkeyPatch, synthetic_key: bytes
) -> None:
    monkeypatch.delattr(os, "memfd_create", raising=False)
    signature = security_boundary._rsa_pss_sha256(synthetic_key, b"m27e")
    assert signature


def test_pipe_signer_failure_closes_both_descriptors(monkeypatch: pytest.MonkeyPatch) -> None:
    descriptors: list[tuple[int, int]] = []
    real_pipe = os.pipe

    def recording_pipe() -> tuple[int, int]:
        pair = real_pipe()
        descriptors.append(pair)
        return pair

    monkeypatch.setattr(security_boundary.os, "pipe", recording_pipe)
    monkeypatch.setattr(
        security_boundary.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("synthetic child failure")),
    )
    with pytest.raises(security_boundary.BoundaryError):
        security_boundary._sign_from_pipe(b"synthetic", b"message")
    for descriptor in descriptors[0]:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_live_enrollment_requires_exact_warning_and_rolls_back_on_seal_failure(
    tmp_path: Path, synthetic_key: bytes
) -> None:
    credential = ProductionWriteCredential(
        "synthetic-write", synthetic_key, REQUIRED_LIVE_WRITE_SCOPES, fixture_only=True
    )
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    with pytest.raises(PermissionError, match="exact real-money"):
        enroll_live_write_credential(
            credential=credential,
            store=store,
            owner_authenticated=True,
            password_reauthenticated=True,
            totp_valid=True,
            csrf_valid=True,
            explicit_confirmation="wrong",
            validated_environment="PRODUCTION",
            validated_account=0,
            validated_scopes=REQUIRED_LIVE_WRITE_SCOPES,
            server_proof=VALID_PROOF,
        )
    assert not store.record_path.exists()

    receipt = enroll_live_write_credential(
        credential=credential,
        store=store,
        owner_authenticated=True,
        password_reauthenticated=True,
        totp_valid=True,
        csrf_valid=True,
        explicit_confirmation=CONFIRMATION,
        validated_environment="PRODUCTION",
        validated_account=0,
        validated_scopes=REQUIRED_LIVE_WRITE_SCOPES,
        server_proof=VALID_PROOF,
    )
    assert receipt.key_id_hash != credential.key_id
    assert credential.private_key_pem not in store.record_path.read_bytes()
    assert oct(store.record_path.stat().st_mode & 0o777) == "0o600"
    assert receipt.scopes == ("read", "write::trade")


def _base_enrollment_kwargs(
    credential: ProductionWriteCredential, store: ProtectedWriteCredentialStore
) -> dict[str, Any]:
    return {
        "credential": credential,
        "store": store,
        "owner_authenticated": True,
        "password_reauthenticated": True,
        "totp_valid": True,
        "csrf_valid": True,
        "explicit_confirmation": CONFIRMATION,
        "validated_environment": "PRODUCTION",
        "validated_account": 0,
        "validated_scopes": REQUIRED_LIVE_WRITE_SCOPES,
    }


def test_enrollment_rejects_server_proof_disagreeing_with_caller_declared_scopes(
    tmp_path: Path, synthetic_key: bytes
) -> None:
    credential = ProductionWriteCredential(
        "synthetic-write", synthetic_key, REQUIRED_LIVE_WRITE_SCOPES, fixture_only=True
    )
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    disagreeing_proof = WriteCredentialServerProof(
        "synthetic-write", frozenset({"read", "write"}), 0
    )
    with pytest.raises(WriteCredentialAuthorityError, match="read and write::trade"):
        enroll_live_write_credential(
            **_base_enrollment_kwargs(credential, store), server_proof=disagreeing_proof
        )
    assert not store.record_path.exists()
    assert not store.master_key_path.exists()


@pytest.mark.parametrize(
    "proof",
    [
        WriteCredentialServerProof("synthetic-write", frozenset({"read", "write"}), 0),
        WriteCredentialServerProof("synthetic-write", frozenset({"write"}), 0),
        WriteCredentialServerProof("synthetic-write", frozenset({"read"}), 0),
        WriteCredentialServerProof(
            "synthetic-write", frozenset({"read", "write::trade", "write::transfer"}), 0
        ),
        WriteCredentialServerProof("synthetic-write", frozenset({"read", "write::transfer"}), 0),
        WriteCredentialServerProof(
            "synthetic-write", frozenset({"read", "write::block_trade_accept"}), 0
        ),
    ],
)
def test_broad_extra_and_wrong_child_scopes_are_rejected(
    proof: WriteCredentialServerProof,
) -> None:
    with pytest.raises(WriteCredentialAuthorityError, match="read and write::trade"):
        require_live_write_authority(proof, expected_key_id="synthetic-write")


@pytest.mark.parametrize("subaccount", [None, 1, 63])
def test_unrestricted_and_wrong_subaccount_are_rejected(subaccount: int | None) -> None:
    proof = WriteCredentialServerProof(
        "synthetic-write", frozenset({"read", "write::trade"}), subaccount
    )
    with pytest.raises(WriteCredentialAuthorityError, match="subaccount 0"):
        require_live_write_authority(proof, expected_key_id="synthetic-write")


def test_subaccount_zero_and_exact_scopes_are_accepted() -> None:
    require_live_write_authority(VALID_PROOF, expected_key_id="synthetic-write")


def test_server_metadata_identity_mismatch_is_rejected() -> None:
    with pytest.raises(WriteCredentialAuthorityError, match="identity mismatch"):
        require_live_write_authority(VALID_PROOF, expected_key_id="some-other-key")


def test_exact_single_match_proves_scope_and_subaccount() -> None:
    proof = fetch_proof(
        api_keys_reply(
            [
                {"api_key_id": "other", "scopes": ["read", "write"], "subaccount": None},
                {
                    "api_key_id": "synthetic-write",
                    "scopes": ["read", "write::trade"],
                    "subaccount": 0,
                },
            ]
        )
    )
    assert proof.key_id == "synthetic-write"
    assert proof.scopes == frozenset({"read", "write::trade"})
    assert proof.subaccount == 0
    require_live_write_authority(proof, expected_key_id="synthetic-write")


@pytest.mark.parametrize(
    "records",
    [
        [],
        [{"api_key_id": "other", "scopes": ["read"], "subaccount": 0}],
        [
            {"api_key_id": "synthetic-write", "scopes": ["read"], "subaccount": 0},
            {"api_key_id": "synthetic-write", "scopes": ["read"], "subaccount": 0},
        ],
    ],
)
def test_missing_or_duplicate_key_identity_is_rejected(records: list[dict[str, Any]]) -> None:
    with pytest.raises(WriteCredentialAuthorityError, match="uniquely identified"):
        fetch_proof(api_keys_reply(records))


@pytest.mark.parametrize(
    "record",
    [
        {"api_key_id": "synthetic-write"},
        {"api_key_id": "synthetic-write", "scopes": []},
        {"api_key_id": "synthetic-write", "scopes": "read"},
        {"api_key_id": "synthetic-write", "scopes": [1]},
        {"api_key_id": "synthetic-write", "scopes": [True]},
        {"api_key_id": "synthetic-write", "scopes": ["read", "read"]},
        {"api_key_id": "synthetic-write", "scopes": ["read", "admin"]},
        {"api_key_id": "synthetic-write", "scopes": ["read", "write::trade"], "subaccount": "0"},
        {"api_key_id": "synthetic-write", "scopes": ["read", "write::trade"], "subaccount": True},
        {"api_key_id": "synthetic-write", "scopes": ["read", "write::trade"], "subaccount": 64},
        {"api_key_id": "synthetic-write", "scopes": ["read", "write::trade"], "subaccount": -1},
    ],
)
def test_malformed_api_key_metadata_is_rejected(record: dict[str, Any]) -> None:
    with pytest.raises(WriteCredentialAuthorityError):
        fetch_proof(api_keys_reply([record]))


@pytest.mark.parametrize(
    "reply",
    [
        ProductionReadReply(200, b"{}"),
        ProductionReadReply(200, b'{"api_keys":{}}'),
        ProductionReadReply(200, b'{"api_keys":[1]}'),
        ProductionReadReply(200, b"not-json"),
        ProductionReadReply(200, b'{"api_keys":[]}', content_type="text/plain"),
        ProductionReadReply(500, b"{}"),
        ProductionReadReply(429, b"{}"),
    ],
)
def test_malformed_or_non_success_response_is_rejected(reply: ProductionReadReply) -> None:
    with pytest.raises(WriteCredentialAuthorityError):
        fetch_proof(reply)


@pytest.mark.parametrize("status", [401, 403])
def test_authentication_failure_is_not_treated_as_valid_metadata(status: int) -> None:
    with pytest.raises(WriteCredentialAuthorityError, match="authentication rejected"):
        fetch_proof(ProductionReadReply(status, b""))


def test_redirect_and_transport_failure_are_rejected() -> None:
    with pytest.raises(WriteCredentialAuthorityError, match="redirect"):
        fetch_proof(ProductionReadReply(302, b"", location="https://attacker.example"))
    with pytest.raises(WriteCredentialAuthorityError, match="transport failed"):
        fetch_proof(TimeoutError("synthetic timeout"))
    with pytest.raises(WriteCredentialAuthorityError, match="transport failed"):
        fetch_proof(OSError("synthetic connection reset"))


def test_metadata_call_targets_exact_production_api_keys_endpoint() -> None:
    transport = FakeMetadataTransport(api_keys_reply([{"api_key_id": "k"}]))
    credential = ProductionWriteCredential(
        "k", b"placeholder-pem", REQUIRED_LIVE_WRITE_SCOPES, fixture_only=True
    )
    with pytest.raises(WriteCredentialAuthorityError):
        verify_live_write_credential_authority(
            transport,
            credential,
            timestamp_ms=999,
            signer_factory=FakeMetadataSigner,
        )
    assert transport.calls == [
        (PRODUCTION_ORIGIN, API_KEYS_PATH, {"synthetic-auth": "k:999:GET:" + API_KEYS_PATH}, 10)
    ]


def test_every_metadata_failure_leaves_credential_not_installed(
    tmp_path: Path, synthetic_key: bytes
) -> None:
    credential = ProductionWriteCredential(
        "synthetic-write", synthetic_key, REQUIRED_LIVE_WRITE_SCOPES, fixture_only=True
    )
    store = ProtectedWriteCredentialStore(tmp_path / "write")
    bad_proofs = [
        WriteCredentialServerProof("synthetic-write", frozenset({"read", "write"}), 0),
        WriteCredentialServerProof("synthetic-write", frozenset({"read", "write::trade"}), None),
        WriteCredentialServerProof("synthetic-write", frozenset({"read", "write::trade"}), 5),
        WriteCredentialServerProof("wrong-key-id", frozenset({"read", "write::trade"}), 0),
    ]
    for proof in bad_proofs:
        with pytest.raises(WriteCredentialAuthorityError):
            enroll_live_write_credential(
                **_base_enrollment_kwargs(credential, store), server_proof=proof
            )
        assert not store.record_path.exists()
        assert not store.master_key_path.exists()
    receipt = enroll_live_write_credential(
        **_base_enrollment_kwargs(credential, store), server_proof=VALID_PROOF
    )
    assert store.record_path.exists()
    with pytest.raises(PermissionError, match="already installed"):
        enroll_live_write_credential(
            **_base_enrollment_kwargs(credential, store), server_proof=VALID_PROOF
        )
    assert receipt.environment == "PRODUCTION"


def test_fixed_transport_rejects_redirect_and_unsupported_path() -> None:
    transport = FixedKalshiProductionTransport()
    with pytest.raises(ProductionTransportError):
        transport.send_exact(
            origin="https://attacker.example",
            method="POST",
            path="/trade-api/v2/portfolio/events/orders",
            body=b"{}",
            headers={},
            verify_tls=True,
            follow_redirects=False,
            timeout_seconds=3,
            maximum_response_bytes=1_000_000,
        )
    with pytest.raises(ProductionTransportError):
        transport.send_exact(
            origin=transport.origin,
            method="POST",
            path="/trade-api/v2/communications/rfqs",
            body=b"{}",
            headers={},
            verify_tls=True,
            follow_redirects=False,
            timeout_seconds=3,
            maximum_response_bytes=1_000_000,
        )


def test_readiness_report_distinguishes_evidence_classes(tmp_path: Path) -> None:
    evidence = tmp_path / "public.json"
    evidence.write_text(
        '{"exchange_status":{"classification":"SUCCESS","body_sha256":"a"},'
        '"markets":{"classification":"SUCCESS","pagination_complete":true,"market_count":0}}'
    )
    statuses = operator_evidence(public_evidence=evidence, postgres_verified=True)
    assert statuses["PUBLIC_EXCHANGE_STATUS"][0] == "PASS"
    assert statuses["PUBLIC_MARKET_DISCOVERY"][0] == "PASS"
    assert statuses["POSTGRESQL_CONCURRENCY"][0] == "PASS"
    assert statuses["AUTHENTICATED_PRODUCTION_BALANCE"][0] == "BLOCKED_BY_CREDENTIAL"
    assert statuses["PRODUCTION_WRITE_CREDENTIAL"][0] == "NOT INSTALLED"


def test_current_v2_wire_shape_translates_legacy_policy_label() -> None:
    envelope = create_envelope(
        execution_id="e",
        authorization_id="a",
        decision_id="d",
        intent_hash="i",
        ticker="M",
        outcome_side="YES",
        price=Decimal("0.42"),
        quantity=Decimal("1.00"),
        tif="good_till_canceled",
        expiration=None,
        post_only=True,
        reduce_only=False,
        cancel_on_pause=True,
        stp="cancel_newest",
        order_group_id=None,
        client_order_id="c",
        rules_version="r",
        candidate_version="v",
        portfolio_hash="p",
        reconciliation_hash="q",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(seconds=1),
    )
    assert b'"self_trade_prevention_type":"taker_at_cross"' in envelope.canonical_body
    assert envelope.path == "/trade-api/v2/portfolio/events/orders"
