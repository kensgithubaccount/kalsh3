from __future__ import annotations

import ast
import json
from dataclasses import fields
from decimal import Decimal
from pathlib import Path

import pytest

from services.risk_engine.domain import RequiredOrderGroupPolicy
from services.risk_engine.invariants import NewRiskReadiness
from services.supervised_canary import live_read_acceptance as m27f
from services.supervised_canary import m27d
from services.supervised_canary.m27o_state_bootstrap import (
    EXACT_CONFIRMATION,
    bootstrap_state,
)
from services.supervised_canary.m27q_preflight_orchestrator import (
    M27QOrchestrationError,
    build_first_canary_preflight,
)
from services.supervised_canary.m27q_state_inspection import (
    inspect_first_canary_state,
)
from tests.test_m27f_live_read_acceptance import (
    FakeAccountTransport,
    FakeSigner,
    build_attestation,
)
from tests.test_m27i_live_weather_preflight import (
    _authoritative_economics,
    _exposure,
    _m27h_payload,
    _public_payload,
    _raw_market,
    _snapshot_payload,
    _weather,
)


def _all_ready() -> NewRiskReadiness:
    return NewRiskReadiness(
        **{
            field.name: True
            for field in fields(NewRiskReadiness)
        }
    )


def _fixture(tmp_path: Path) -> dict[str, object]:
    probability, forecast = _weather()
    now = forecast.forecast_reference_time + m27d.timedelta(seconds=10)

    raw_market = _raw_market()
    (
        economics,
        binding,
        _expected_snapshot,
        series_observation,
        event_override,
        _fee_regime,
    ) = _authoritative_economics(
        now,
        raw_market=raw_market,
    )

    candidate_inputs = ((probability, forecast, economics),)
    selected = m27d.select_experimental_candidate(
        candidate_inputs,
        now=now,
    )
    assert (
        selected.state
        is m27d.CandidateState.QUALIFYING_EXPERIMENTAL_CANARY
    )
    assert selected.selected is not None
    candidate = selected.selected

    bundle = m27f.run_live_read_acceptance_bundle(
        key_id="candidate",
        private_key_pem=b"synthetic-pem-not-real",
        authority_attestation=build_attestation(),
        account_transport=FakeAccountTransport(),
        signer_factory=FakeSigner,
        clock=lambda: now - m27d.timedelta(seconds=2),
        clock_ms=lambda: 123,
    )
    assert bundle.evidence.reconciliation.succeeded
    assert bundle.account_facts is not None

    m27f_path = tmp_path / "m27f.json"
    m27f_path.write_text(
        json.dumps(
            bundle.evidence.to_json(),
            sort_keys=True,
        )
    )

    m27h_path = tmp_path / "m27h.json"
    m27h_path.write_text(
        json.dumps(
            _m27h_payload(now),
            sort_keys=True,
        )
    )

    public_path = tmp_path / "public.json"
    public_path.write_text(
        json.dumps(
            _public_payload(now, candidate),
            sort_keys=True,
        )
    )

    m27j_path = tmp_path / "m27j.json"
    m27j_path.write_text(
        json.dumps(
            _snapshot_payload(
                now,
                raw_market=raw_market,
            ),
            sort_keys=True,
        )
    )

    binding_path = tmp_path / "m27a-binding.json"
    binding_path.write_text(
        json.dumps(
            binding.to_json(),
            sort_keys=True,
        )
    )

    exposure = _exposure(
        candidate.market_ticker,
        now,
    )

    state_path = (
        tmp_path
        / "production-canary"
        / "m27o-shared.sqlite3"
    )
    bootstrap_state(
        state_path=state_path,
        actor="M27Q TEST",
        reason="offline full preflight orchestration",
        confirmation=EXACT_CONFIRMATION,
    )

    return {
        "now": now,
        "candidate_inputs": candidate_inputs,
        "m27f_bundle": bundle,
        "m27f_evidence_path": m27f_path,
        "m27h_evidence_path": m27h_path,
        "public_evidence_path": public_path,
        "m27j_evidence_path": m27j_path,
        "m27a_binding_evidence_path": binding_path,
        "current_series_fee_observation": series_observation,
        "current_event_fee_override": event_override,
        "current_event_fee_observed_at": now,
        "candidate_exposure": exposure,
        "state_path": state_path,
        "readiness": _all_ready(),
        "order_group": RequiredOrderGroupPolicy(
            "m27q-none",
            False,
            0,
            Decimal("1.00"),
            True,
            True,
        ),
        "authorization_service_available": True,
    }


def test_full_offline_m27q_to_m27i_path_reaches_preflight_ready(
    tmp_path: Path,
) -> None:
    values = _fixture(tmp_path)
    state_path = values["state_path"]
    assert isinstance(state_path, Path)

    before = inspect_first_canary_state(
        state_path=state_path,
        now=values["now"],  # type: ignore[arg-type]
    )

    result = build_first_canary_preflight(
        **values  # type: ignore[arg-type]
    )

    artifact = result.preflight.artifact

    assert result.risk.clean_pass
    assert result.risk.decision.production_write_authorized is False

    assert artifact.state == "PREFLIGHT_READY", artifact.gates.missing
    assert artifact.missing_gates == ()
    assert artifact.gates.all_pass
    assert artifact.gates.results["m13_verified"].passed
    assert artifact.gates.results["rules_current"].passed
    assert artifact.gates.results["write_budget_safe"].passed

    after = inspect_first_canary_state(
        state_path=state_path,
        now=values["now"],  # type: ignore[arg-type]
    )

    # Full orchestration did not mutate durable first-canary state.
    assert before.database_sha256 == after.database_sha256
    assert before.real_submission_count == after.real_submission_count == 0
    assert before.real_fill_count == after.real_fill_count == 0
    assert before.risk_authorization_count == after.risk_authorization_count == 0
    assert before.risk_reservation_count == after.risk_reservation_count == 0
    assert before.approval_count == after.approval_count == 0
    assert before.session_count == after.session_count == 0

    for suffix in ("-wal", "-shm", "-journal"):
        assert not state_path.with_name(
            state_path.name + suffix
        ).exists()


def test_persisted_m27f_must_match_same_sweep_bundle(
    tmp_path: Path,
) -> None:
    values = _fixture(tmp_path)

    path = values["m27f_evidence_path"]
    assert isinstance(path, Path)

    payload = json.loads(path.read_text())
    payload["key_id_hash"] = "0" * 64
    path.write_text(json.dumps(payload))

    with pytest.raises(
        M27QOrchestrationError,
        match="does not match the exact same-sweep bundle",
    ):
        build_first_canary_preflight(
            **values  # type: ignore[arg-type]
        )


def test_orchestrator_has_no_network_credential_or_mutation_boundary() -> None:
    path = Path(
        "services/supervised_canary/"
        "m27q_preflight_orchestrator.py"
    )
    source = path.read_text()
    tree = ast.parse(source)

    imported: set[str] = set()
    calls: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(
                alias.name
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                calls.add(node.func.attr)
            elif isinstance(node.func, ast.Name):
                calls.add(node.func.id)

    forbidden_imports = {
        "socket",
        "urllib",
        "requests",
        "httpx",
        "sqlite3",
        "services.kalshi_account_gateway",
        "services.production_execution",
        "services.risk_engine.authorization",
        "services.supervised_canary.store",
        "services.supervised_canary.m27o",
        "services.supervised_canary.m27o_operator",
    }

    assert not any(
        module == forbidden
        or module.startswith(forbidden + ".")
        for module in imported
        for forbidden in forbidden_imports
    )

    assert "issue" not in calls
    assert "consume" not in calls
    assert "record_submission_attempt" not in calls
    assert "record_fill" not in calls
    assert "bootstrap_state" not in calls
