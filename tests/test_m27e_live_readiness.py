from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from services.production_execution import security_boundary
from services.production_execution.requests import create_envelope
from services.production_execution.transport import (
    FixedKalshiProductionTransport,
    ProductionTransportError,
)
from services.supervised_canary.readiness_report import operator_evidence


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
