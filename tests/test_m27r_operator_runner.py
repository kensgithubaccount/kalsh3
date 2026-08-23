from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from services.supervised_canary.m27d import (
    CandidateState,
    ExperimentalCandidate,
    select_experimental_candidate,
)
from services.supervised_canary.m27q_state_inspection import inspect_first_canary_state
from services.supervised_canary.m27r_operator_runner import (
    M27RCandidateEvidence,
    M27RMarketEvidence,
    M27ROperatorError,
    M27ROperatorRun,
    M27RPublicEvidence,
    run_readonly_operator_preflight,
)
from tests.test_m27q_preflight_orchestrator import _fixture

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "services" / "supervised_canary" / "m27r_operator_runner.py"


def _public_evidence() -> M27RPublicEvidence:
    return M27RPublicEvidence(
        public_evidence_path=Path("public.json"),
        markets=(),
    )


class _PublicProvider:
    def __init__(self, evidence: M27RPublicEvidence) -> None:
        self.evidence = evidence
        self.calls = 0

    def collect_public_evidence(self, *, now: datetime) -> M27RPublicEvidence:
        self.calls += 1
        return self.evidence


class _ForbiddenCandidateProvider:
    def __init__(self) -> None:
        self.calls = 0

    def collect_candidate_evidence(self, **_: object) -> Any:
        self.calls += 1
        raise AssertionError("authenticated candidate phase must not run")


class _FixtureCandidateProvider:
    def __init__(self, evidence: M27RCandidateEvidence, candidate_id: str) -> None:
        self.evidence = evidence
        self.candidate_id = candidate_id
        self.calls = 0

    def collect_candidate_evidence(
        self,
        *,
        candidate: ExperimentalCandidate,
        now: datetime,
    ) -> M27RCandidateEvidence:
        del now
        self.calls += 1
        assert candidate.candidate_id == self.candidate_id
        return self.evidence


def test_zero_candidate_abstains_before_authenticated_phase() -> None:
    now = datetime(2026, 8, 23, 17, tzinfo=UTC)
    public = _PublicProvider(_public_evidence())
    candidate = _ForbiddenCandidateProvider()

    result = run_readonly_operator_preflight(
        now=now,
        public_provider=public,
        candidate_provider=candidate,
    )

    assert public.calls == 1
    assert candidate.calls == 0
    assert result.state == "ABSTAIN"
    assert result.candidate_id is None
    assert result.authenticated_phase_performed is False
    assert result.read_only is True
    assert result.execution_authorized is False
    assert result.preflight is None


def test_multiple_candidates_abstain_before_authenticated_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 23, 17, tzinfo=UTC)
    public = _PublicProvider(_public_evidence())
    candidate_provider = _ForbiddenCandidateProvider()
    selected = SimpleNamespace(candidate_id="candidate-a")
    other = SimpleNamespace(candidate_id="candidate-b")

    monkeypatch.setattr(
        "services.supervised_canary.m27r_operator_runner.select_experimental_candidate",
        lambda *_args, **_kwargs: SimpleNamespace(
            state=CandidateState.QUALIFYING_EXPERIMENTAL_CANARY,
            selected=selected,
            candidates=(selected, other),
        ),
    )

    result = run_readonly_operator_preflight(
        now=now,
        public_provider=public,
        candidate_provider=candidate_provider,
    )

    assert candidate_provider.calls == 0
    assert result.state == "ABSTAIN"
    assert result.authenticated_phase_performed is False
    assert result.execution_authorized is False


def test_duplicate_public_market_tickers_are_rejected() -> None:
    now = datetime(2026, 8, 23, 17, tzinfo=UTC)
    market = M27RMarketEvidence(
        market_ticker="KXHIGHCHI-TEST",
        candidate_input=cast(Any, (None, None, None)),
        m27j_evidence_path=Path("m27j.json"),
        m27a_binding_evidence_path=Path("m27a.json"),
        current_series_fee_observation=cast(Any, None),
        current_event_fee_override=cast(Any, None),
        current_event_fee_observed_at=now,
    )
    with pytest.raises(ValueError, match="duplicate market tickers"):
        M27RPublicEvidence(public_evidence_path=Path("public.json"), markets=(market, market))


def test_exact_one_fixture_reaches_real_m27q_preflight_without_state_mutation(
    tmp_path: Path,
) -> None:
    values = _fixture(tmp_path)
    now = cast(datetime, values["now"])
    candidate_inputs = cast(Any, values["candidate_inputs"])
    selection = select_experimental_candidate(candidate_inputs, now=now)
    candidate = cast(ExperimentalCandidate, selection.selected)
    market_evidence = M27RMarketEvidence(
        market_ticker=candidate.market_ticker,
        candidate_input=candidate_inputs[0],
        m27j_evidence_path=cast(Path, values["m27j_evidence_path"]),
        m27a_binding_evidence_path=cast(Path, values["m27a_binding_evidence_path"]),
        current_series_fee_observation=cast(
            Any,
            values["current_series_fee_observation"],
        ),
        current_event_fee_override=cast(Any, values["current_event_fee_override"]),
        current_event_fee_observed_at=cast(
            datetime,
            values["current_event_fee_observed_at"],
        ),
    )
    public_evidence = M27RPublicEvidence(
        public_evidence_path=cast(Path, values["public_evidence_path"]),
        markets=(market_evidence,),
    )

    state_path = cast(Path, values["state_path"])
    before = inspect_first_canary_state(state_path=state_path, now=now)
    candidate_evidence = M27RCandidateEvidence(
        m27f_bundle=cast(Any, values["m27f_bundle"]),
        m27f_evidence_path=cast(Path, values["m27f_evidence_path"]),
        m27h_evidence_path=cast(Path, values["m27h_evidence_path"]),
        candidate_exposure=cast(Any, values["candidate_exposure"]),
        state_path=state_path,
        readiness=cast(Any, values["readiness"]),
        order_group=cast(Any, values["order_group"]),
        authorization_service_available=cast(
            bool,
            values["authorization_service_available"],
        ),
    )
    public_provider = _PublicProvider(public_evidence)
    candidate_provider = _FixtureCandidateProvider(candidate_evidence, candidate.candidate_id)

    result = run_readonly_operator_preflight(
        now=now,
        public_provider=public_provider,
        candidate_provider=candidate_provider,
    )

    assert public_provider.calls == 1
    assert candidate_provider.calls == 1
    assert result.state == "PREFLIGHT_READY"
    assert result.candidate_id == candidate.candidate_id
    assert result.authenticated_phase_performed is True
    assert result.read_only is True
    assert result.execution_authorized is False
    assert result.preflight is not None
    assert result.preflight.preflight.artifact.state == "PREFLIGHT_READY"
    assert result.preflight.risk.decision.production_write_authorized is False

    after = inspect_first_canary_state(state_path=state_path, now=now)
    assert before.database_sha256 == after.database_sha256
    assert after.real_submission_count == 0
    assert after.real_fill_count == 0
    assert after.risk_authorization_count == 0
    assert after.risk_reservation_count == 0
    assert after.approval_count == 0
    assert after.session_count == 0


def test_naive_operator_clock_is_rejected_before_any_provider_call() -> None:
    public = _PublicProvider(_public_evidence())
    candidate = _ForbiddenCandidateProvider()

    with pytest.raises(M27ROperatorError, match="operator clock must be timezone-aware"):
        run_readonly_operator_preflight(
            now=datetime(2026, 8, 23, 17),
            public_provider=public,
            candidate_provider=candidate,
        )

    assert public.calls == 0
    assert candidate.calls == 0


def test_result_can_never_claim_execution_authority() -> None:
    with pytest.raises(ValueError, match="can never authorize execution"):
        M27ROperatorRun(
            software_version="test",
            state="PREFLIGHT_READY",
            reason=None,
            candidate_id="candidate-a",
            authenticated_phase_performed=True,
            read_only=True,
            execution_authorized=True,
            preflight=None,
        )


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_m27r_coordinator_has_no_live_or_mutating_capability_imports() -> None:
    source = MODULE_PATH.read_text()
    tree = ast.parse(source)
    imported = _imported_modules(tree)

    forbidden_prefixes = (
        "services.production_execution",
        "services.kalshi_account_gateway",
        "urllib",
        "http",
        "requests",
        "socket",
        "ssl",
        "subprocess",
    )
    for module in imported:
        assert not module.startswith(forbidden_prefixes), module

    forbidden_tokens = (
        "m27o_live_canary",
        "m27o_operator",
        "ProtectedWriteCredentialStore",
        "SignAndSendBoundary",
        "production_execute",
        "send_exact",
        '"POST"',
        "'POST'",
        '"PUT"',
        "'PUT'",
        '"PATCH"',
        "'PATCH'",
        '"DELETE"',
        "'DELETE'",
    )
    for token in forbidden_tokens:
        assert token not in source, token
