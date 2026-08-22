from __future__ import annotations

import hashlib
import inspect
import sqlite3
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from services.production_execution import m27o_live_canary as live
from services.production_execution.credentials import (
    REQUIRED_LIVE_WRITE_SCOPES,
    ProductionWriteCredential,
)
from services.production_execution.enrollment import (
    OperatorReleaseAuthorization,
    ProtectedWriteCredentialStore,
    _candidate_fingerprint,
)
from services.production_execution.installed_credential_verification import (
    INSTALLED_CREDENTIAL_EVIDENCE_SCHEMA,
    SOFTWARE_VERSION as M27H_SOFTWARE_VERSION,
)
from services.production_execution.requests import create_envelope
from services.production_execution.signer_self_test import (
    SIGNER_SELF_TEST_DOMAIN,
    SignerSelfTestResult,
)
from services.production_execution.store import ProductionJournal
from services.production_execution.transport import ProductionTransportError
from services.risk_engine.authorization import AuthorizationStore, FixedClock
from services.supervised_canary.m27i import GATE_NAMES, GateResult, PreflightArtifact, PreflightGates
from services.supervised_canary.m27o import AtomicReleaseCommit, OneContractCanaryRelease
from services.supervised_canary.store import CanaryStore

NOW = datetime(2026, 8, 22, 3, 30, tzinfo=UTC)
ONE = Decimal("1.00")


class Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


class FakeTransport:
    def __init__(self, *, status: int = 201, body: bytes = b"{}", fail: bool = False) -> None:
        self.status = status
        self.body = body
        self.fail = fail
        self.calls = 0
        self.origin = None
        self.method = None
        self.path = None
        self.request_body = None
        self.headers = None

    def send_exact(self, **kwargs):
        self.calls += 1
        self.origin = kwargs["origin"]
        self.method = kwargs["method"]
        self.path = kwargs["path"]
        self.request_body = kwargs["body"]
        self.headers = kwargs["headers"]
        if self.fail:
            raise ProductionTransportError("synthetic transport failure")
        return self.status, self.body


def genkey() -> bytes:
    return subprocess.run(
        ["/usr/bin/openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048"],
        capture_output=True,
        check=True,
    ).stdout


def install_real_store(path: Path) -> tuple[ProtectedWriteCredentialStore, ProductionWriteCredential]:
    store = ProtectedWriteCredentialStore(path)
    credential = ProductionWriteCredential(
        "m27o-real-key",
        genkey(),
        REQUIRED_LIVE_WRITE_SCOPES,
        fixture_only=False,
    )
    authorization = OperatorReleaseAuthorization(_candidate_fingerprint(credential))

    def self_test(c: ProductionWriteCredential, at: datetime) -> SignerSelfTestResult:
        return SignerSelfTestResult(
            "PASS",
            hashlib.sha256(c.key_id.encode()).hexdigest(),
            SIGNER_SELF_TEST_DOMAIN,
            "1" * 64,
            "2" * 64,
            at,
        )

    store.install_real_credential(
        credential,
        authorization=authorization,
        now=NOW - timedelta(seconds=10),
        self_test=self_test,
    )
    return store, credential


def envelope(*, tif: str = "fill_or_kill", order_group_id: str | None = "og-m27o"):
    return create_envelope(
        execution_id="execution-m27o-live",
        authorization_id="risk-auth-m27o-live",
        decision_id="risk-decision-m27o-live",
        intent_hash="intent-m27o-live",
        ticker="KXHIGHCHI-26AUG22-B80.5",
        outcome_side="NO",
        price=Decimal("0.5400"),
        quantity=ONE,
        tif=tif,
        expiration=None,
        post_only=False,
        reduce_only=False,
        cancel_on_pause=True,
        stp="cancel_newest",
        order_group_id=order_group_id,
        client_order_id="kalsh3-m27o-live-1",
        rules_version="rules-v1",
        candidate_version="candidate-m27o-live",
        portfolio_hash="portfolio-v1",
        reconciliation_hash="reconciliation-v1",
        created_at=NOW,
        expires_at=NOW + timedelta(seconds=4),
    )


def preflight() -> dict[str, object]:
    artifact = PreflightArtifact(
        schema="kalsh3.m27i.live-weather-preflight.v1",
        software_version="kalsh3.m27i.live-weather-preflight/1",
        created_at=NOW,
        expires_at=NOW + timedelta(seconds=4),
        state="PREFLIGHT_READY",
        abstain_reason=None,
        candidate_id="candidate-m27o-live",
        market_ticker="KXHIGHCHI-26AUG22-B80.5",
        event_ticker="KXHIGHCHI-26AUG22",
        target_date="2026-08-22",
        selected_side="NO",
        executable_price="0.5400",
        maximum_fee="0.0174",
        maximum_commitment="0.5574",
        maximum_loss="0.5574",
        proxy_probability="0.7806896551724137931034482759",
        research_probability_discrepancy="0.2232896551724137931034482759",
        model_identity="model-v1",
        forecast_evidence_identity="forecast-v1",
        economics_evidence_identity="economics-v1",
        gates=PreflightGates({name: GateResult(True) for name in GATE_NAMES}),
        missing_gates=(),
        warning="research-only physical-temperature proxy",
    )
    return artifact.to_json()


def release(env, pf: dict[str, object]) -> OneContractCanaryRelease:
    return OneContractCanaryRelease(
        schema="kalsh3.m27o.one-contract-release.v1",
        software_version="kalsh3.m27o.one-contract-release/2",
        created_at=NOW,
        expires_at=NOW + timedelta(seconds=4),
        candidate_id="candidate-m27o-live",
        market_ticker="KXHIGHCHI-26AUG22-B80.5",
        selected_side="NO",
        exact_price=Decimal("0.5400"),
        exact_quantity=ONE,
        maximum_fee=Decimal("0.0174"),
        maximum_loss=Decimal("0.5574"),
        preview_id="preview-m27o-live",
        preview_hash="p" * 64,
        approval_id="approval-m27o-live",
        approval_hash="a" * 64,
        preflight_hash=str(pf["content_hash"]),
        envelope_hash=env.content_hash,
        body_hash=env.body_hash,
        risk_authorization_id="risk-auth-m27o-live",
        risk_decision_id="risk-decision-m27o-live",
        intent_hash="intent-m27o-live",
        client_order_id="kalsh3-m27o-live-1",
        rules_version="rules-v1",
        portfolio_state_hash="portfolio-v1",
        safety_state_hash="safety-v1",
        reconciliation_state_hash="reconciliation-v1",
    )


def commit(r: OneContractCanaryRelease) -> AtomicReleaseCommit:
    return AtomicReleaseCommit(
        schema="kalsh3.m27o.atomic-release-commit.v1",
        committed_at=NOW + timedelta(milliseconds=100),
        session_id="m27o-live-session",
        release_hash=r.content_hash,
        preview_id=r.preview_id,
        approval_id=r.approval_id,
        risk_authorization_id=r.risk_authorization_id,
        client_order_id=r.client_order_id,
    )


def m27h_payload(credential: ProductionWriteCredential, *, completed_at: datetime | None = None):
    at = completed_at or NOW
    return {
        "schema": INSTALLED_CREDENTIAL_EVIDENCE_SCHEMA,
        "software_version": M27H_SOFTWARE_VERSION,
        "environment": "PRODUCTION",
        "observed_at": at.isoformat(),
        "completed_at": at.isoformat(),
        "store_state": "COMMITTED",
        "key_id_hash": hashlib.sha256(credential.key_id.encode()).hexdigest(),
        "credential_fingerprint": _candidate_fingerprint(credential),
        "authority_classification": "PASS",
        "authority_reason": None,
        "signer_classification": "PASS",
        "signer_challenge_domain": SIGNER_SELF_TEST_DOMAIN,
        "signer_reason": None,
        "signer_completed_at": at.isoformat(),
        "classification": "PASS",
        "reason": None,
    }


def seed_phase_b(path: Path, r: OneContractCanaryRelease, c: AtomicReleaseCommit) -> None:
    CanaryStore(path)
    AuthorizationStore(path, FixedClock(NOW))
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE compliance_state SET state='CLEAR',reason='test',changed_at=?,actor='test' "
            "WHERE singleton=1",
            (NOW.isoformat(),),
        )
        db.execute(
            "INSERT INTO canary_approvals VALUES(?,?,?,?,?,?,?)",
            (
                r.approval_id,
                r.preview_hash,
                "owner",
                r.approval_hash,
                NOW.isoformat(),
                (NOW + timedelta(seconds=60)).isoformat(),
                "CONSUMED",
            ),
        )
        db.execute(
            "INSERT INTO canary_sessions("
            "session_id,preview_id,approval_id,client_order_id,state,possibly_submitted,created_at"
            ") VALUES(?,?,?,?,?,?,?)",
            (
                c.session_id,
                r.preview_id,
                r.approval_id,
                r.client_order_id,
                "SUBMISSION_PENDING",
                1,
                c.committed_at.isoformat(),
            ),
        )
        db.execute(
            "UPDATE production_submission_counter SET real_submission_count=1 WHERE singleton=1"
        )
        db.execute(
            "INSERT INTO canary_events(happened_at,event_type,reference_hash,actor) "
            "VALUES(?,?,?,?)",
            (c.committed_at.isoformat(), "M27O_ATOMIC_RELEASE_COMMITTED", r.content_hash, "M27O"),
        )
        db.execute(
            "INSERT INTO risk_authorizations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
            (
                r.risk_authorization_id,
                r.risk_decision_id,
                r.intent_hash,
                r.client_order_id,
                r.market_ticker,
                "KXHIGHCHI-26AUG22",
                r.portfolio_state_hash,
                "risk-policy-v1",
                r.rules_version,
                r.safety_state_hash,
                NOW.isoformat(),
                (NOW + timedelta(seconds=4)).isoformat(),
                "CONSUMED",
            ),
        )
        db.execute(
            "INSERT INTO risk_reservations VALUES(?,?,?,?,?,?,?,?,?)",
            (
                r.risk_authorization_id,
                r.market_ticker,
                "KXHIGHCHI-26AUG22",
                str(r.maximum_loss),
                str(r.maximum_loss),
                str(r.maximum_loss),
                str(r.maximum_loss),
                (NOW + timedelta(seconds=4)).isoformat(),
                0,
            ),
        )


def setup_case(tmp_path: Path):
    env = envelope()
    pf = preflight()
    r = release(env, pf)
    c = commit(r)
    state = tmp_path / "state.db"
    seed_phase_b(state, r, c)
    store, credential = install_real_store(tmp_path / "credential")
    journal = ProductionJournal(tmp_path / "journal.db")
    return env, pf, r, c, state, store, credential, journal


def session_state(path: Path) -> str:
    with sqlite3.connect(path) as db:
        return str(
            db.execute("SELECT state FROM canary_sessions WHERE session_id='m27o-live-session'").fetchone()[0]
        )


def test_exact_one_contract_live_path_is_fixed_and_requires_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env, pf, r, c, state, store, credential, journal = setup_case(tmp_path)
    sender = FakeTransport(status=201)
    monkeypatch.setattr(live, "FixedKalshiProductionTransport", lambda: sender)

    result = live.execute_one_contract_live_canary(
        release=r,
        atomic_commit=c,
        preflight_payload=pf,
        envelope=env,
        m27h_payload=m27h_payload(credential),
        shared_state_path=state,
        credential_store=store,
        journal=journal,
        clock=Clock(NOW + timedelta(seconds=1)),
    )

    assert result.state == "ACKNOWLEDGED_RECONCILIATION_REQUIRED"
    assert result.status == 201
    assert result.reconciliation_required
    assert sender.calls == 1
    assert sender.origin == "https://external-api.kalshi.com"
    assert sender.method == "POST"
    assert sender.path == "/trade-api/v2/portfolio/events/orders"
    assert sender.request_body == env.canonical_body
    assert sender.headers == {}  # exact auth-header dict is cleared immediately after transport
    assert session_state(state) == "SUBMITTED_OR_UNKNOWN"
    assert journal.recover() == (env.execution_id,)


def test_transport_failure_is_unknown_and_never_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env, pf, r, c, state, store, credential, journal = setup_case(tmp_path)
    sender = FakeTransport(fail=True)
    monkeypatch.setattr(live, "FixedKalshiProductionTransport", lambda: sender)

    result = live.execute_one_contract_live_canary(
        release=r,
        atomic_commit=c,
        preflight_payload=pf,
        envelope=env,
        m27h_payload=m27h_payload(credential),
        shared_state_path=state,
        credential_store=store,
        journal=journal,
        clock=Clock(NOW + timedelta(seconds=1)),
    )
    assert result.state == "UNKNOWN_RECONCILIATION_REQUIRED"
    assert sender.calls == 1
    assert session_state(state) == "SUBMITTED_OR_UNKNOWN"

    with pytest.raises(live.LiveCanaryExecutionError):
        live.execute_one_contract_live_canary(
            release=r,
            atomic_commit=c,
            preflight_payload=pf,
            envelope=env,
            m27h_payload=m27h_payload(credential),
            shared_state_path=state,
            credential_store=store,
            journal=journal,
            clock=Clock(NOW + timedelta(seconds=1)),
        )
    assert sender.calls == 1


def test_stale_or_mismatched_credential_evidence_fails_before_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env, pf, r, c, state, store, credential, journal = setup_case(tmp_path)
    sender = FakeTransport()
    monkeypatch.setattr(live, "FixedKalshiProductionTransport", lambda: sender)

    with pytest.raises(live.LiveCanaryExecutionError):
        live.execute_one_contract_live_canary(
            release=r,
            atomic_commit=c,
            preflight_payload=pf,
            envelope=env,
            m27h_payload=m27h_payload(credential, completed_at=NOW - timedelta(seconds=31)),
            shared_state_path=state,
            credential_store=store,
            journal=journal,
            clock=Clock(NOW + timedelta(seconds=1)),
        )
    assert sender.calls == 0

    bad = m27h_payload(credential)
    bad["credential_fingerprint"] = "0" * 64
    with pytest.raises(live.LiveCanaryExecutionError, match="credential changed"):
        live.execute_one_contract_live_canary(
            release=r,
            atomic_commit=c,
            preflight_payload=pf,
            envelope=env,
            m27h_payload=bad,
            shared_state_path=state,
            credential_store=store,
            journal=journal,
            clock=Clock(NOW + timedelta(seconds=1)),
        )
    assert sender.calls == 0


def test_resting_order_or_missing_group_is_impossible_before_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, pf, r, c, state, store, credential, journal = setup_case(tmp_path)
    sender = FakeTransport()
    monkeypatch.setattr(live, "FixedKalshiProductionTransport", lambda: sender)

    gtc = envelope(tif="good_till_canceled")
    r_gtc = replace(r, envelope_hash=gtc.content_hash, body_hash=gtc.body_hash)
    c_gtc = replace(c, release_hash=r_gtc.content_hash)
    with pytest.raises(live.LiveCanaryExecutionError, match="fill-or-kill"):
        live.execute_one_contract_live_canary(
            release=r_gtc,
            atomic_commit=c_gtc,
            preflight_payload=pf,
            envelope=gtc,
            m27h_payload=m27h_payload(credential),
            shared_state_path=state,
            credential_store=store,
            journal=journal,
            clock=Clock(NOW + timedelta(seconds=1)),
        )

    no_group = envelope(order_group_id=None)
    r_group = replace(r, envelope_hash=no_group.content_hash, body_hash=no_group.body_hash)
    c_group = replace(c, release_hash=r_group.content_hash)
    with pytest.raises(live.LiveCanaryExecutionError, match="order group"):
        live.execute_one_contract_live_canary(
            release=r_group,
            atomic_commit=c_group,
            preflight_payload=pf,
            envelope=no_group,
            m27h_payload=m27h_payload(credential),
            shared_state_path=state,
            credential_store=store,
            journal=journal,
            clock=Clock(NOW + timedelta(seconds=1)),
        )
    assert sender.calls == 0


def test_last_millisecond_halt_fails_before_sign_or_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env, pf, r, c, state, store, credential, journal = setup_case(tmp_path)
    with sqlite3.connect(state) as db:
        db.execute("UPDATE global_halt_state SET active=1 WHERE singleton=1")
    sender = FakeTransport()
    monkeypatch.setattr(live, "FixedKalshiProductionTransport", lambda: sender)

    with pytest.raises(live.LiveCanaryExecutionError, match="safety state changed"):
        live.execute_one_contract_live_canary(
            release=r,
            atomic_commit=c,
            preflight_payload=pf,
            envelope=env,
            m27h_payload=m27h_payload(credential),
            shared_state_path=state,
            credential_store=store,
            journal=journal,
            clock=Clock(NOW + timedelta(seconds=1)),
        )
    assert sender.calls == 0


def test_boundary_has_no_sender_destination_or_credential_injection_arguments() -> None:
    parameters = inspect.signature(live.execute_one_contract_live_canary).parameters
    forbidden = {"sender", "origin", "host", "method", "path", "credential", "private_key"}
    assert forbidden.isdisjoint(parameters)
