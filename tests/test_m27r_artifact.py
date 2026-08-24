from __future__ import annotations

import ast
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from services.supervised_canary.m27r_artifact import (
    M27RArtifactError,
    build_review_artifact,
    write_review_artifact,
)
from services.supervised_canary.m27r_operator_runner import M27ROperatorRun

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "services" / "supervised_canary" / "m27r_artifact.py"


def _abstain_run() -> M27ROperatorRun:
    return M27ROperatorRun(
        software_version="kalsh3.m27r.readonly-operator-runner/1",
        state="ABSTAIN",
        reason="NO_EXACTLY_ONE_QUALIFYING_EXPERIMENTAL_CANDIDATE",
        candidate_id=None,
        authenticated_phase_performed=False,
        read_only=True,
        execution_authorized=False,
        preflight=None,
    )


def test_review_artifact_is_deterministic_and_never_authorizes_execution() -> None:
    created_at = datetime(2026, 8, 23, 17, 30, tzinfo=UTC)
    first = build_review_artifact(run=_abstain_run(), created_at=created_at)
    second = build_review_artifact(run=_abstain_run(), created_at=created_at)

    assert first.to_json() == second.to_json()
    assert first.content_hash == second.content_hash
    assert first.read_only is True
    assert first.execution_authorized is False
    assert first.to_json()["result"]["execution_authorized"] is False


def test_review_artifact_rejects_naive_clock() -> None:
    with pytest.raises(M27RArtifactError, match="created_at must be timezone-aware"):
        build_review_artifact(
            run=_abstain_run(),
            created_at=datetime(2026, 8, 23, 17, 30),
        )


def test_writer_persists_only_the_review_envelope(tmp_path: Path) -> None:
    created_at = datetime(2026, 8, 23, 17, 30, tzinfo=UTC)
    artifact = build_review_artifact(run=_abstain_run(), created_at=created_at)
    output = tmp_path / "nested" / "m27r.json"

    write_review_artifact(artifact=artifact, path=output)

    payload = json.loads(output.read_text())
    assert payload == artifact.to_json()
    assert payload["read_only"] is True
    assert payload["execution_authorized"] is False
    assert "private_key" not in output.read_text().lower()
    assert "bearer " not in output.read_text().lower()


class _SecretBearingRun:
    read_only = True
    execution_authorized = False

    def to_json(self) -> dict[str, object]:
        return {
            "state": "ABSTAIN",
            "private_key": "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----",
        }


def test_secret_bearing_payload_fails_closed() -> None:
    with pytest.raises(M27RArtifactError, match="forbidden secret-bearing field"):
        build_review_artifact(
            run=_SecretBearingRun(),  # type: ignore[arg-type]
            created_at=datetime(2026, 8, 23, 17, 30, tzinfo=UTC),
        )


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_artifact_module_has_no_live_or_execution_capability_imports() -> None:
    source = MODULE_PATH.read_text()
    imported = _imported_modules(ast.parse(source))
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
        "m27o_operator",
        "ProtectedWriteCredentialStore",
        "SignAndSendBoundary",
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
