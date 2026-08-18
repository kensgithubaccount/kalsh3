from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from services.demo_execution.domain import DemoWriteCredential
from services.production_execution.boundary import ProductionExecutionBoundary
from services.production_execution.credentials import (
    ProductionWriteCredential,
    enrollment_available,
)
from services.production_execution.domain import (
    PRODUCTION_ORIGIN,
    AuthorityClass,
    Operation,
    ProductionArmState,
    canonical_json,
    validate_operation,
)
from services.production_execution.enrollment import prepare_enrollment_fixture
from services.production_execution.rate_budget import ProductionWriteBudget
from services.production_execution.requests import create_envelope, lifecycle_envelope
from services.production_execution.security_boundary import (
    BoundaryError,
    Preflight,
    SignAndSendBoundary,
)
from services.production_execution.store import ProductionJournal
from services.risk_engine.authorization import AuthorizationState, RiskAuthorization

NOW = datetime(2026, 8, 10, 12, tzinfo=UTC)


class FixedClock:
    def __init__(self, now: datetime = NOW) -> None:
        self.value = now

    def now(self) -> datetime:
        return self.value


class FakeExactSender:
    offline_fixture = True

    def __init__(self, *, timeout: bool = False, status: int = 201) -> None:
        self.timeout = timeout
        self.status = status
        self.calls: list[dict[str, object]] = []

    def send_exact(self, **values: object) -> tuple[int, bytes]:
        self.calls.append(values)
        if self.timeout:
            raise TimeoutError
        return self.status, b"{}"


def key() -> ProductionWriteCredential:
    result = subprocess.run(
        ["/usr/bin/openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048"],
        capture_output=True,
        check=True,
    )
    return ProductionWriteCredential(
        "fixture-key", result.stdout, frozenset({"read", "write::trade"}), fixture_only=True
    )


def envelope(**changes: object):  # type: ignore[no-untyped-def]
    values: dict[str, object] = {
        "execution_id": "prod-exec-1",
        "authorization_id": "auth-1",
        "decision_id": "decision-1",
        "intent_hash": "intent-1",
        "ticker": "MARKET",
        "outcome_side": "YES",
        "price": Decimal(".42"),
        "quantity": Decimal("1"),
        "tif": "good_till_canceled",
        "expiration": None,
        "post_only": True,
        "reduce_only": False,
        "cancel_on_pause": True,
        "stp": "cancel_newest",
        "order_group_id": "group-1",
        "client_order_id": "kalsh3-v1-production-1",
        "rules_version": "rules-1",
        "candidate_version": "candidate-1",
        "portfolio_hash": "portfolio-1",
        "reconciliation_hash": "reconcile-1",
        "created_at": NOW,
        "expires_at": NOW + timedelta(seconds=5),
    }
    values.update(changes)
    return create_envelope(**values)  # type: ignore[arg-type]


def authorization(env=None):  # type: ignore[no-untyped-def]
    env = envelope() if env is None else env
    return RiskAuthorization(
        env.risk_authorization_id,
        env.risk_decision_id,
        env.intent_hash,
        env.portfolio_state_hash,
        "1",
        env.rules_version,
        "safety",
        NOW,
        NOW + timedelta(seconds=5),
        AuthorizationState.ISSUED,
    )


def preflight(env=None, **changes: object) -> Preflight:  # type: ignore[no-untyped-def]
    env = envelope() if env is None else env
    values: dict[str, object] = {
        "intent_hash": env.intent_hash,
        "portfolio_hash": env.portfolio_state_hash,
        "reconciliation_hash": env.reconciliation_state_hash,
        "rules_current": True,
        "market_active": True,
        "market_not_paused": True,
        "reconciliation_fresh": True,
        "global_halt_clear": True,
        "compliance_clear": True,
        "kills_clear": True,
        "order_group_ready": True,
        "client_order_id_unique": True,
    }
    values.update(changes)
    return Preflight(**values)  # type: ignore[arg-type]


def test_sign_and_send_uses_exact_canonical_bytes_and_no_redirect(tmp_path: Path) -> None:
    env = envelope()
    sender = FakeExactSender()
    boundary = SignAndSendBoundary(ProductionJournal(tmp_path / "journal.db"), FixedClock())
    result = boundary.offline_fixture_execute(
        envelope=env,
        authorization=authorization(env),
        preflight=preflight(env),
        credential=key(),
        sender=sender,
    )
    assert result.reconciliation_required and len(sender.calls) == 1
    call = sender.calls[0]
    assert call["body"] is env.canonical_body
    assert call["origin"] == PRODUCTION_ORIGIN
    assert call["verify_tls"] is True and call["follow_redirects"] is False
    headers = call["headers"]
    assert (
        isinstance(headers, dict) and headers == {}
    )  # cleared immediately after the boundary call


@pytest.mark.parametrize(
    "field,value",
    (
        ("canonical_body", canonical_json({"count": "10", "subaccount": 0})),
        ("body_hash", "changed"),
        ("intent_hash", "changed"),
        ("origin", "https://external-api.demo.kalshi.co"),
        ("subaccount", 1),
        ("exchange_index", 1),
    ),
)
def test_body_and_envelope_substitution_is_rejected(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        replace(envelope(), **{field: value})


def test_serialization_is_stable_and_has_no_float_or_legacy_fields() -> None:
    first = envelope()
    assert first.canonical_body == envelope().canonical_body
    assert b'"count":"1"' in first.canonical_body and b'"price":"0.42"' in first.canonical_body
    assert b"yes_price" not in first.canonical_body
    with pytest.raises(TypeError):
        canonical_json({"price": 0.42})


def test_yes_no_translation_is_economically_exact() -> None:
    yes = envelope(outcome_side="YES", price=Decimal(".42"))
    no = envelope(
        outcome_side="NO",
        price=Decimal(".42"),
        execution_id="prod-exec-no",
        client_order_id="kalsh3-v1-production-no",
    )
    assert b'"side":"bid"' in yes.canonical_body and b'"price":"0.42"' in yes.canonical_body
    assert b'"side":"ask"' in no.canonical_body and b'"price":"0.58"' in no.canonical_body


@pytest.mark.parametrize(
    "method,path",
    (
        ("POST", "/trade-api/v2/portfolio/events/orders/batch"),
        ("POST", "/trade-api/v2/communications/rfqs"),
        ("DELETE", "/trade-api/v2/portfolio/events/orders/../keys"),
        ("POST", "/trade-api/v2/portfolio/events/orders?x=1"),
        ("POST", "//trade-api/v2/portfolio/events/orders"),
        ("POST", "/trade-api/v2/portfolio/events/orders/%252e%252e"),
    ),
)
def test_path_confusion_and_excess_authority_are_rejected(method: str, path: str) -> None:
    with pytest.raises(ValueError):
        validate_operation(Operation.CREATE, method, path)


def test_cancel_amend_and_decrease_are_typed_and_bound() -> None:
    template = envelope()
    cancel = lifecycle_envelope(
        operation=Operation.CANCEL,
        order_id="known-bot-order",
        authority=AuthorityClass.EMERGENCY_SAFETY,
        replacement_body={},
        template=template,
        authorization_id="cancel-auth",
        decision_id="cancel-decision",
        intent_hash="cancel-intent",
    )
    amend = lifecycle_envelope(
        operation=Operation.AMEND,
        order_id="known-bot-order",
        authority=AuthorityClass.NEW_RISK,
        replacement_body={"price": "0.43", "count": "1"},
        template=template,
        authorization_id="amend-auth",
        decision_id="amend-decision",
        intent_hash="amend-intent",
    )
    decrease = lifecycle_envelope(
        operation=Operation.DECREASE,
        order_id="known-bot-order",
        authority=AuthorityClass.RISK_REDUCING,
        replacement_body={"reduce_to": "0.5"},
        template=template,
        authorization_id="decrease-auth",
        decision_id="decrease-decision",
        intent_hash="decrease-intent",
    )
    assert (cancel.method, amend.path.endswith("/amend"), decrease.path.endswith("/decrease")) == (
        "DELETE",
        True,
        True,
    )


def test_single_use_is_durable_and_concurrent(tmp_path: Path) -> None:
    env = envelope()
    journal = ProductionJournal(tmp_path / "single.db")
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(lambda _: journal.claim(env, version="v1"), (1, 2)))
    assert sum(outcomes) == 1
    assert ProductionJournal(tmp_path / "single.db").recover() == (env.execution_id,)


def test_timeout_and_429_are_unknown_without_retry(tmp_path: Path) -> None:
    for name, sender in (
        ("timeout", FakeExactSender(timeout=True)),
        ("429", FakeExactSender(status=429)),
    ):
        env = envelope(
            execution_id=f"prod-{name}",
            client_order_id=f"kalsh3-v1-{name}",
            authorization_id=f"auth-{name}",
        )
        result = SignAndSendBoundary(
            ProductionJournal(tmp_path / f"{name}.db"), FixedClock()
        ).offline_fixture_execute(
            envelope=env,
            authorization=authorization(env),
            preflight=preflight(env),
            credential=key(),
            sender=sender,
        )
        assert result.state == "UNKNOWN_RECONCILIATION_REQUIRED"
        assert len(sender.calls) == 1


@pytest.mark.parametrize(
    "change",
    (
        "market_active",
        "market_not_paused",
        "reconciliation_fresh",
        "global_halt_clear",
        "compliance_clear",
        "kills_clear",
        "order_group_ready",
        "client_order_id_unique",
    ),
)
def test_last_millisecond_safety_changes_block_before_sender(tmp_path: Path, change: str) -> None:
    env, sender = envelope(), FakeExactSender()
    with pytest.raises(BoundaryError):
        SignAndSendBoundary(
            ProductionJournal(tmp_path / f"{change}.db"), FixedClock()
        ).offline_fixture_execute(
            envelope=env,
            authorization=authorization(env),
            preflight=preflight(env, **{change: False}),
            credential=key(),
            sender=sender,
        )
    assert not sender.calls


def test_timestamp_regression_and_expiry_fail_closed(tmp_path: Path) -> None:
    env = envelope(expires_at=NOW + timedelta(milliseconds=1))
    with pytest.raises(BoundaryError):
        SignAndSendBoundary(
            ProductionJournal(tmp_path / "expired.db"), FixedClock(NOW + timedelta(seconds=1))
        ).offline_fixture_execute(
            envelope=env,
            authorization=authorization(env),
            preflight=preflight(env),
            credential=key(),
            sender=FakeExactSender(),
        )


def test_credential_classes_and_scopes_do_not_cross(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ProductionWriteCredential("x", b"pem", frozenset({"write"}), fixture_only=True)
    assert not enrollment_available()
    env, sender = envelope(), FakeExactSender()
    with pytest.raises((BoundaryError, AttributeError)):
        SignAndSendBoundary(
            ProductionJournal(tmp_path / "confused.db"), FixedClock()
        ).offline_fixture_execute(
            envelope=env,
            authorization=authorization(env),
            preflight=preflight(env),
            credential=DemoWriteCredential("demo", b"pem"),  # type: ignore[arg-type]
            sender=sender,
        )
    assert not sender.calls


def test_future_enrollment_is_sealed_fixture_only() -> None:
    class Sealer:
        def seal(self, value: bytes) -> str:
            return "sealed:" + str(len(value))

    credential = key()
    package = prepare_enrollment_fixture(
        credential=credential,
        sealer=Sealer(),
        owner_authenticated=True,
        password_reauthenticated=True,
        totp_valid=True,
        explicit_confirmation="PREPARE PRODUCTION WRITE CREDENTIAL",
        validated_environment="PRODUCTION",
        validated_account=0,
        fixture_validator=True,
    )
    assert package.sealed_private_key.startswith("sealed:")
    assert credential.private_key_pem.decode(errors="ignore") not in repr(package)
    with pytest.raises(PermissionError, match="unavailable"):
        prepare_enrollment_fixture(
            credential=replace(credential, fixture_only=False),
            sealer=Sealer(),
            owner_authenticated=True,
            password_reauthenticated=True,
            totp_valid=True,
            explicit_confirmation="PREPARE PRODUCTION WRITE CREDENTIAL",
            validated_environment="PRODUCTION",
            validated_account=0,
            fixture_validator=False,
        )


def test_public_path_is_unconditionally_disarmed_despite_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("PRODUCTION_TRADING", "LIVE", "ARM"):
        monkeypatch.setenv(name, "true")
    boundary = ProductionExecutionBoundary()
    assert boundary.arm_state == ProductionArmState.DISARMED
    assert not boundary.credential.installed
    assert not hasattr(boundary, "signer")
    with pytest.raises(PermissionError, match="DISARMED"):
        boundary.preflight()


def test_rate_budget_preserves_safety_capacity() -> None:
    budget = ProductionWriteBudget(2, 1)
    assert budget.acquire(AuthorityClass.NEW_RISK)
    assert not budget.acquire(AuthorityClass.NEW_RISK)
    assert budget.acquire(AuthorityClass.EMERGENCY_SAFETY)


def test_architecture_has_no_research_or_web_signer_clients() -> None:
    forbidden_roots = (
        "services/document_intelligence",
        "services/breaking_signals",
        "services/forecasting",
        "services/learning",
        "services/opportunity_engine",
        "services/web_dashboard",
    )
    for root in forbidden_roots:
        source = "\n".join(path.read_text() for path in Path(root).glob("*.py"))
        assert "from services.production_execution" not in source
        assert "import services.production_execution" not in source
        assert "SignAndSendBoundary" not in source
    web = Path("services/web_dashboard/app.py").read_text()
    assert not any(
        route in web
        for route in ('path == "/sign"', 'path == "/execute"', 'path == "/submit-order"')
    )


def test_no_real_sender_or_secret_is_present() -> None:
    source = "\n".join(
        path.read_text() for path in Path("services/production_execution").glob("*.py")
    )
    assert (
        "requests.post" not in source and "httpx" not in source and "urllib.request" not in source
    )
    assert "DEMO_WRITE" not in source and "services.demo_execution" not in source
    assert not any(name in os.environ for name in ("PRODUCTION_WRITE_KEY", "KALSHI_WRITE_PEM"))
