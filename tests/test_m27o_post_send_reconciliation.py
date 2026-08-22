from __future__ import annotations

import hashlib
import inspect
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from services.kalshi_account_gateway.client import HttpResponse
from services.production_execution import m27o_reconciliation as rec
from services.production_execution.credentials import (
    REQUIRED_LIVE_WRITE_SCOPES,
    ProductionWriteCredential,
)
from services.production_execution.enrollment import (
    OperatorReleaseAuthorization,
    ProtectedWriteCredentialStore,
    _candidate_fingerprint,
)
from services.production_execution.requests import create_envelope
from services.production_execution.signer_self_test import (
    SIGNER_SELF_TEST_DOMAIN,
    SignerSelfTestResult,
)
from services.production_execution.store import ProductionJournal
from services.risk_engine.authorization import AuthorizationStore, FixedClock
from services.supervised_canary.m27o import AtomicReleaseCommit, OneContractCanaryRelease
from services.supervised_canary.store import CanaryStore

NOW = datetime(2026, 8, 22, 3, 30, tzinfo=UTC)
ONE = Decimal("1.00")


class Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


class FakeReadTransport:
    def __init__(
        self,
        *,
        orders: list[dict[str, object]] | None = None,
        fills: list[dict[str, object]] | None = None,
        positions: list[dict[str, object]] | None = None,
        fail: bool = False,
    ) -> None:
        self.orders = orders or []
        self.fills = fills or []
        self.positions = positions or []
        self.fail = fail
        self.paths: list[str] = []

    def get(self, path: str, headers, *, timeout_seconds: float) -> HttpResponse:
        del headers
        assert timeout_seconds == 3
        self.paths.append(path)
        if self.fail:
            raise TimeoutError("synthetic reconciliation timeout")
        if "/orders" in path:
            return HttpResponse(200, {"orders": self.orders, "cursor": ""})
        if "/fills" in path:
            return HttpResponse(200, {"fills": self.fills, "cursor": ""})
        if "/positions" in path:
            return HttpResponse(200, {"market_positions": self.positions, "cursor": ""})
        raise AssertionError(f"unexpected GET path {path}")


def genkey() -> bytes:
    return subprocess.run(
        ["/usr/bin/openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048"],
        capture_output=True,
        check=True,
    ).stdout


def envelope():
    return create_envelope(
        execution_id="execution-m27o-live",
        authorization_id="risk-auth-m27o-live",
        decision_id="risk-decision-m27o-live",
        intent_hash="intent-m27o-live",
        ticker="KXHIGHCHI-26AUG22-B80.5",
        outcome_side="NO",
        price=Decimal("0.5400"),
        quantity=ONE,
        tif="fill_or_kill",
        expiration=None,
        post_only=False,
        reduce_only=False,
        cancel_on_pause=True,
        stp="cancel_newest",
        order_group_id="og-m27o",
        client_order_id="kalsh3-m27o-live-1",
        rules_version="rules-v1",
        candidate_version="candidate-m27o-live",
        portfolio_hash="portfolio-v1",
        reconciliation_hash="reconciliation-v1",
        created_at=NOW,
        expires_at=NOW + timedelta(seconds=4),
    )


def release(env) -> OneContractCanaryRelease:
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
        preflight_hash="f" * 64,
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


def install_store(path: Path) -> ProtectedWriteCredentialStore:
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
        now=NOW,
        self_test=self_test,
    )
    return store


def seed_state(path: Path, r: OneContractCanaryRelease, c: AtomicReleaseCommit) -> None:
    CanaryStore(path)
    AuthorizationStore(path, FixedClock(NOW))
    with sqlite3.connect(path) as db:
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
                "SUBMITTED_OR_UNKNOWN",
                1,
                c.committed_at.isoformat(),
            ),
        )
        db.execute(
            "UPDATE production_submission_counter SET real_submission_count=1 WHERE singleton=1"
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


def seed_journal(journal: ProductionJournal, env) -> None:
    assert journal.claim(env, version="m27o-one-contract-live-send-v1")
    journal.transition(
        env.execution_id,
        "ACKNOWLEDGED_RECONCILIATION_REQUIRED",
        possibly_sent=True,
    )


def setup_case(tmp_path: Path):
    env = envelope()
    r = release(env)
    c = commit(r)
    state = tmp_path / "state.db"
    seed_state(state, r, c)
    store = install_store(tmp_path / "credential")
    journal = ProductionJournal(tmp_path / "journal.db")
    seed_journal(journal, env)
    return env, r, c, state, store, journal


def order(
    *,
    status: str = "executed",
    fill: str = "1.00",
    remaining: str = "0.00",
    **changes: object,
) -> dict[str, object]:
    value: dict[str, object] = {
        "order_id": "order-m27o-1",
        "client_order_id": "kalsh3-m27o-live-1",
        "ticker": "KXHIGHCHI-26AUG22-B80.5",
        "outcome_side": "no",
        "status": status,
        "fill_count_fp": fill,
        "remaining_count_fp": remaining,
        "initial_count_fp": "1.00",
        "subaccount_number": 0,
    }
    value.update(changes)
    return value


def fill(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "fill_id": "fill-m27o-1",
        "order_id": "order-m27o-1",
        "ticker": "KXHIGHCHI-26AUG22-B80.5",
        "market_ticker": "KXHIGHCHI-26AUG22-B80.5",
        "outcome_side": "no",
        "count_fp": "1.00",
        "no_price_dollars": "0.5400",
        "is_taker": True,
        "fee_cost": "0.0174",
        "subaccount_number": 0,
    }
    value.update(changes)
    return value


def session(path: Path) -> tuple[str, int, int, int]:
    with sqlite3.connect(path) as db:
        row = db.execute(
            "SELECT state,filled_atoms,remaining_atoms,possibly_submitted "
            "FROM canary_sessions WHERE session_id='m27o-live-session'"
        ).fetchone()
        assert row is not None
        return str(row[0]), int(row[1]), int(row[2]), int(row[3])


def fill_counter(path: Path) -> int:
    with sqlite3.connect(path) as db:
        row = db.execute(
            "SELECT real_fill_count FROM production_fill_counter WHERE singleton=1"
        ).fetchone()
        assert row is not None
        return int(row[0])


def run_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, transport: FakeReadTransport):
    env, r, c, state, store, journal = setup_case(tmp_path)
    monkeypatch.setattr(rec, "UrllibReadTransport", lambda: transport)
    result = rec.reconcile_one_contract_live_canary(
        release=r,
        atomic_commit=c,
        execution_id=env.execution_id,
        shared_state_path=state,
        credential_store=store,
        journal=journal,
        clock=Clock(NOW + timedelta(seconds=10)),
    )
    return result, env, r, c, state, store, journal


def test_exact_full_fill_terminally_reconciles_and_disarms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = FakeReadTransport(
        orders=[order()],
        fills=[fill()],
        positions=[{"ticker": "KXHIGHCHI-26AUG22-B80.5", "position_fp": "-1.00"}],
    )
    result, env, *_rest, state, _store, journal = run_case(tmp_path, monkeypatch, transport)
    assert result.classification == "FILLED"
    assert result.filled_quantity == ONE
    assert result.maximum_fill_price == Decimal("0.5400")
    assert result.total_fee == Decimal("0.0174")
    assert result.terminal_state == "CANARY_COMPLETE"
    assert not result.reconciliation_required
    assert session(state) == ("CANARY_COMPLETE", 1_000_000, 0, 0)
    assert fill_counter(state) == 1
    assert journal.recover() == ()
    assert transport.paths and all("subaccount=0" in path for path in transport.paths)
    assert env.execution_id == result.execution_id


def test_canceled_fok_with_zero_fill_is_terminal_no_fill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = FakeReadTransport(orders=[order(status="canceled", fill="0.00")], fills=[])
    result, *_rest, state, _store, journal = run_case(tmp_path, monkeypatch, transport)
    assert result.classification == "NO_FILL"
    assert result.filled_quantity == Decimal(0)
    assert session(state) == ("CANARY_COMPLETE", 0, 1_000_000, 0)
    assert fill_counter(state) == 0
    assert journal.recover() == ()


def test_read_failure_stays_unknown_and_never_clears_canary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, env, *_rest, state, _store, journal = run_case(
        tmp_path, monkeypatch, FakeReadTransport(fail=True)
    )
    assert result.classification == "UNKNOWN"
    assert result.reconciliation_required
    assert session(state)[0] == "SUBMITTED_OR_UNKNOWN"
    assert journal.recover() == (env.execution_id,)


def test_duplicate_client_order_id_is_ambiguous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    duplicate = order(order_id="order-m27o-2")
    result, *_rest, state, _store, journal = run_case(
        tmp_path,
        monkeypatch,
        FakeReadTransport(orders=[order(), duplicate], fills=[fill()]),
    )
    assert result.classification == "UNKNOWN"
    assert "exactly one order" in (result.reason or "")
    assert session(state)[0] == "SUBMITTED_OR_UNKNOWN"
    assert journal.recover()


@pytest.mark.parametrize(
    ("orders", "fills"),
    [
        ([order(status="resting", fill="0.00", remaining="1.00")], []),
        ([order(status="executed", fill="0.50", remaining="0.50")], [fill(count_fp="0.50")]),
        ([order()], [fill(subaccount_number=1)]),
        ([order()], [fill(outcome_side="yes")]),
    ],
)
def test_resting_partial_or_identity_drift_never_terminally_reconciles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    orders: list[dict[str, object]],
    fills: list[dict[str, object]],
) -> None:
    result, *_rest, state, _store, journal = run_case(
        tmp_path, monkeypatch, FakeReadTransport(orders=orders, fills=fills)
    )
    assert result.classification == "UNKNOWN"
    assert result.reconciliation_required
    assert session(state)[0] == "SUBMITTED_OR_UNKNOWN"
    assert journal.recover()


@pytest.mark.parametrize(
    "positions",
    [
        [{"ticker": "KXHIGHCHI-26AUG22-B80.5", "position_fp": "1.00"}],
        [{"ticker": "KXHIGHCHI-26AUG22-B80.5", "position_fp": "-0.50"}],
        [
            {"ticker": "KXHIGHCHI-26AUG22-B80.5", "position_fp": "-1.00"},
            {"ticker": "KXHIGHCHI-26AUG22-B80.5", "position_fp": "-1.00"},
        ],
        [{"ticker": "KXHIGHCHI-26AUG22-B80.5", "position_fp": 1}],
    ],
)
def test_full_fill_requires_exact_candidate_market_position(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    positions: list[dict[str, object]],
) -> None:
    result, env, *_rest, state, _store, journal = run_case(
        tmp_path,
        monkeypatch,
        FakeReadTransport(
            orders=[order()],
            fills=[fill()],
            positions=positions,
        ),
    )
    assert result.classification == "UNKNOWN"
    assert "position" in (result.reason or "")
    assert result.reconciliation_required
    assert session(state)[0] == "SUBMITTED_OR_UNKNOWN"
    assert journal.recover() == (env.execution_id,)


def test_zero_fill_requires_zero_candidate_market_position(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, env, *_rest, state, _store, journal = run_case(
        tmp_path,
        monkeypatch,
        FakeReadTransport(
            orders=[order(status="canceled", fill="0.00")],
            fills=[],
            positions=[{"ticker": "KXHIGHCHI-26AUG22-B80.5", "position_fp": "-1.00"}],
        ),
    )
    assert result.classification == "UNKNOWN"
    assert "position" in (result.reason or "")
    assert result.reconciliation_required
    assert session(state)[0] == "SUBMITTED_OR_UNKNOWN"
    assert journal.recover() == (env.execution_id,)


def test_fill_economics_violation_is_known_terminal_but_explicitly_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = FakeReadTransport(
        orders=[order()],
        fills=[fill(no_price_dollars="0.5500")],
        positions=[{"ticker": "KXHIGHCHI-26AUG22-B80.5", "position_fp": "-1.00"}],
    )
    result, *_rest, state, _store, journal = run_case(tmp_path, monkeypatch, transport)
    assert result.classification == "FILLED_POLICY_VIOLATION"
    assert "price or fee ceiling" in (result.reason or "")
    assert result.terminal_state == "CANARY_FAILED"
    assert not result.reconciliation_required
    assert session(state)[0] == "CANARY_FAILED"
    assert fill_counter(state) == 1
    assert journal.recover() == ()


def test_terminal_reconciliation_is_idempotent_and_fill_counter_cannot_double_increment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env, r, c, state, store, journal = setup_case(tmp_path)
    transport = FakeReadTransport(
        orders=[order()],
        fills=[fill()],
        positions=[{"ticker": "KXHIGHCHI-26AUG22-B80.5", "position_fp": "-1.00"}],
    )
    monkeypatch.setattr(rec, "UrllibReadTransport", lambda: transport)
    kwargs = dict(
        release=r,
        atomic_commit=c,
        execution_id=env.execution_id,
        shared_state_path=state,
        credential_store=store,
        journal=journal,
        clock=Clock(NOW + timedelta(seconds=10)),
    )
    first = rec.reconcile_one_contract_live_canary(**kwargs)
    second = rec.reconcile_one_contract_live_canary(**kwargs)
    assert first.classification == second.classification == "FILLED"
    assert fill_counter(state) == 1
    assert session(state)[0] == "CANARY_COMPLETE"
    assert journal.recover() == ()


def test_reconciliation_surface_has_no_exchange_mutation_or_transport_injection() -> None:
    parameters = inspect.signature(rec.reconcile_one_contract_live_canary).parameters
    for forbidden in ("sender", "transport", "url", "origin", "method", "path", "credential"):
        assert forbidden not in parameters
    source = inspect.getsource(rec.reconcile_one_contract_live_canary)
    assert "send_exact" not in source
    assert "POST" not in source
    assert "DELETE" not in source
    assert "PATCH" not in source
