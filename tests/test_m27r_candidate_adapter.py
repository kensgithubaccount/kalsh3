from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

from services.kalshi_account_gateway.client import HttpResponse
from services.kalshi_account_gateway.production_read_credentials import (
    API_KEYS_PATH,
    PRODUCTION_ORIGIN,
)
from services.risk_engine.domain import RequiredOrderGroupPolicy
from services.risk_engine.invariants import NewRiskReadiness
from services.supervised_canary import authority_attestation as attestation_mod
from services.supervised_canary.m27d import ExperimentalCandidate
from services.supervised_canary.m27r_candidate_adapter import (
    GetOnlyCandidateEvidenceProvider,
    M27RCandidateAdapterError,
)
from services.supervised_canary.m27r_operator_runner import CandidateEvidenceProvider

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "services" / "supervised_canary" / "m27r_candidate_adapter.py"
NOW = datetime(2026, 8, 23, 17, 30, tzinfo=UTC)


class FakeSigner:
    def __init__(self, key_id: str, private_key_pem: bytes) -> None:
        self.key_id = key_id
        self.private_key_pem = private_key_pem

    def headers(self, timestamp_ms: int, method: str, request_target: str) -> dict[str, str]:
        return {"synthetic-auth": f"{self.key_id}:{timestamp_ms}:{method}:{request_target}"}


class FakeGetOnlyTransport:
    def __init__(self) -> None:
        self.paths: list[str] = []

    def get(self, path: str, headers: Mapping[str, str], *, timeout_seconds: float) -> HttpResponse:
        del headers, timeout_seconds
        self.paths.append(path)
        if "balance" in path:
            return HttpResponse(
                200,
                {
                    "balance": 100000,
                    "portfolio_value": 100000,
                    "updated_ts": 1_700_000_000,
                    "balance_breakdown": [],
                },
            )
        field = (
            "market_positions"
            if "positions" in path
            else next(name for name in ("orders", "fills", "settlements") if name in path)
        )
        return HttpResponse(200, {field: [], "cursor": ""})


def _attestation(*, scopes: list[str] | None = None) -> dict[str, Any]:
    key_id = "candidate"
    return {
        "schema": attestation_mod.SCHEMA,
        "software_version": attestation_mod.SOFTWARE_VERSION,
        "environment": "PRODUCTION",
        "observed_at": NOW.isoformat(),
        "source": {"origin": PRODUCTION_ORIGIN, "path": API_KEYS_PATH},
        "classification": "PASS",
        "candidate": {
            "key_id_hash": hashlib.sha256(key_id.encode()).hexdigest(),
            "server_scopes": scopes or ["read", "write::trade"],
            "server_subaccount": 0,
            "unique_matches": 1,
        },
        "reason": None,
    }


class FakeCandidate:
    market_ticker = "KXHIGHCHI-26AUG23-T90"


def _provider(
    *,
    tmp_path: Path,
    transport: FakeGetOnlyTransport,
    credential_loader: Any,
    attestation_loader: Any,
) -> GetOnlyCandidateEvidenceProvider:
    m27h = tmp_path / "m27h.json"
    m27h.write_text(json.dumps({"placeholder": "M27I validates the real artifact"}))
    return GetOnlyCandidateEvidenceProvider(
        credential_loader=credential_loader,
        authority_attestation_loader=attestation_loader,
        account_transport_factory=lambda: transport,
        m27f_evidence_path=tmp_path / "m27f.json",
        m27h_evidence_path=m27h,
        state_path=tmp_path / "state.json",
        readiness=NewRiskReadiness(),
        order_group=RequiredOrderGroupPolicy(
            policy_id="test",
            required=True,
            expected_subaccount=0,
            contract_limit_ceiling=Decimal("1"),
            group_active=True,
            auto_cancel_ready=True,
        ),
        authorization_service_available=False,
        signer_factory=FakeSigner,
        clock_ms=lambda: 123,
    )


def test_provider_structurally_satisfies_candidate_protocol(tmp_path: Path) -> None:
    transport = FakeGetOnlyTransport()
    provider: CandidateEvidenceProvider = _provider(
        tmp_path=tmp_path,
        transport=transport,
        credential_loader=lambda: ("candidate", b"synthetic-pem-not-real"),
        attestation_loader=_attestation,
    )
    assert callable(provider.collect_candidate_evidence)


def test_successful_candidate_adapter_persists_exact_m27f_and_checks_exposure(
    tmp_path: Path,
) -> None:
    transport = FakeGetOnlyTransport()
    calls = {"credentials": 0}

    def credentials() -> tuple[str, bytes]:
        calls["credentials"] += 1
        return "candidate", b"synthetic-pem-not-real"

    provider = _provider(
        tmp_path=tmp_path,
        transport=transport,
        credential_loader=credentials,
        attestation_loader=_attestation,
    )
    result = provider.collect_candidate_evidence(
        candidate=cast(ExperimentalCandidate, FakeCandidate()),
        clock=lambda: NOW,
    )

    assert calls["credentials"] == 1
    assert result.m27f_bundle.evidence.reconciliation.succeeded is True
    persisted = json.loads(result.m27f_evidence_path.read_text())
    assert persisted == result.m27f_bundle.evidence.to_json()
    assert result.candidate_exposure.succeeded is True
    assert result.candidate_exposure.market_ticker == FakeCandidate.market_ticker
    assert len(transport.paths) == 7
    assert sum("orders" in path for path in transport.paths) == 2
    assert sum("positions" in path for path in transport.paths) == 2
    assert "synthetic-pem-not-real" not in result.m27f_evidence_path.read_text()


def test_failed_m27f_stops_before_candidate_exposure_reads(tmp_path: Path) -> None:
    transport = FakeGetOnlyTransport()
    provider = _provider(
        tmp_path=tmp_path,
        transport=transport,
        credential_loader=lambda: ("candidate", b"synthetic-pem-not-real"),
        attestation_loader=lambda: _attestation(scopes=["read"]),
    )

    with pytest.raises(M27RCandidateAdapterError, match="M27F authenticated GET sweep"):
        provider.collect_candidate_evidence(
            candidate=cast(ExperimentalCandidate, FakeCandidate()),
            clock=lambda: NOW,
        )

    # Authority failure occurs before M27F may make any authenticated portfolio request,
    # therefore the later candidate-specific orders/positions reads are impossible too.
    assert transport.paths == []
    assert provider.m27f_evidence_path.is_file()


def test_missing_m27h_fails_before_credential_access(tmp_path: Path) -> None:
    transport = FakeGetOnlyTransport()
    calls = {"credentials": 0}

    def credentials() -> tuple[str, bytes]:
        calls["credentials"] += 1
        return "candidate", b"synthetic-pem-not-real"

    provider = _provider(
        tmp_path=tmp_path,
        transport=transport,
        credential_loader=credentials,
        attestation_loader=_attestation,
    )
    provider.m27h_evidence_path.unlink()

    with pytest.raises(M27RCandidateAdapterError, match="M27H evidence path"):
        provider.collect_candidate_evidence(
            candidate=cast(ExperimentalCandidate, FakeCandidate()),
            clock=lambda: NOW,
        )

    assert calls["credentials"] == 0
    assert transport.paths == []


def test_naive_time_fails_before_credential_access(tmp_path: Path) -> None:
    transport = FakeGetOnlyTransport()
    calls = {"credentials": 0}

    def credentials() -> tuple[str, bytes]:
        calls["credentials"] += 1
        return "candidate", b"synthetic-pem-not-real"

    provider = _provider(
        tmp_path=tmp_path,
        transport=transport,
        credential_loader=credentials,
        attestation_loader=_attestation,
    )

    with pytest.raises(M27RCandidateAdapterError, match="must be timezone-aware"):
        provider.collect_candidate_evidence(
            candidate=cast(ExperimentalCandidate, FakeCandidate()),
            clock=lambda: datetime(2026, 8, 23, 17, 30),
        )

    assert calls["credentials"] == 0
    assert transport.paths == []


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_candidate_adapter_has_no_mutation_or_protected_write_store_capability() -> None:
    source = MODULE_PATH.read_text()
    imported = _imported_modules(ast.parse(source))
    forbidden_import_prefixes = (
        "services.production_execution.transport",
        "services.production_execution.security_boundary",
        "services.production_execution.m27o",
        "services.supervised_canary.m27o",
        "requests",
        "urllib",
        "http",
        "socket",
        "subprocess",
    )
    for module in imported:
        assert not module.startswith(forbidden_import_prefixes), module

    forbidden_tokens = (
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
