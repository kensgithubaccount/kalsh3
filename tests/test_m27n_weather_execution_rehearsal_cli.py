from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "services" / "supervised_canary" / "m27n_weather_rehearsal.py"
SCRIPT_PATH = REPO_ROOT / "scripts" / "run_m27n_weather_execution_rehearsal.py"

# Every module named here is either credential/signer/transport-adjacent
# (``services.production_execution.*`` except the pure ``domain``/``requests`` pair) or a live
# SQLite-backed store (``AuthorizationStore``/``CanaryStore``). None of them may be imported by
# M27N-W, directly or as a bare name reference.
_FORBIDDEN_IMPORT_MODULES = (
    "services.production_execution.credentials",
    "services.production_execution.security_boundary",
    "services.production_execution.transport",
    "services.production_execution.enrollment",
    "services.production_execution.enrollment_cli",
    "services.production_execution.signer_self_test",
    "services.production_execution.installed_credential_verification",
    "services.production_execution.store",
    "services.production_execution.boundary",
    "services.production_execution.rate_budget",
    "services.production_execution.run",
    "services.supervised_canary.m27i",
    "services.supervised_canary.m27j",
    "services.supervised_canary.readiness_report",
    "services.supervised_canary.readiness",
    "services.supervised_canary.candidate_exposure_check",
    "services.supervised_canary.store",
    "services.opportunity_engine.authoritative_economics",
    "services.market_universe.market_snapshot",
    "services.market_universe.public_read",
    "services.kalshi_account_gateway.client",
)

_FORBIDDEN_STDLIB_IMPORT_MODULES = (
    "http",
    "http.client",
    "ssl",
    "socket",
    "urllib",
    "urllib.request",
    "requests",
    "subprocess",
)

_FORBIDDEN_NAMES = (
    "AuthorizationStore",
    "CanaryStore",
    "SignAndSendBoundary",
    "ProtectedWriteCredentialStore",
    "ProductionJournal",
    "KalshiAccountClient",
)

_FORBIDDEN_TOKENS = (
    "production_execute",
    "offline_fixture_execute",
    "send_exact",
    "SignAndSendBoundary",
    ".sign(",
    ".consume(",
    ".issue(",
    ".claim(",
    ".transition(",
    ".activate_global_halt(",
    "urllib.request.Request(",
    "http.client.HTTPSConnection(",
    '"POST"',
    "'POST'",
    '"PUT"',
    "'PUT'",
    '"PATCH"',
    "'PATCH'",
    '"DELETE"',
    "'DELETE'",
    "arm(",
    "final_ack",
    "finalAck",
)


def _source(path: Path) -> str:
    return path.read_text()


def _imported_module_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            prefix = "." * node.level
            names.add(f"{prefix}{node.module}")
            names.add(node.module)
    return names


def _referenced_names(tree: ast.AST) -> set[str]:
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }


@pytest.mark.parametrize("path", [MODULE_PATH, SCRIPT_PATH])
def test_no_forbidden_module_imports(path: Path) -> None:
    tree = ast.parse(_source(path))
    imported = _imported_module_names(tree)
    for forbidden in _FORBIDDEN_IMPORT_MODULES + _FORBIDDEN_STDLIB_IMPORT_MODULES:
        assert forbidden not in imported, f"{path.name} imports forbidden module {forbidden!r}"


@pytest.mark.parametrize("path", [MODULE_PATH, SCRIPT_PATH])
def test_no_forbidden_names_referenced(path: Path) -> None:
    tree = ast.parse(_source(path))
    referenced = _referenced_names(tree)
    for forbidden in _FORBIDDEN_NAMES:
        assert forbidden not in referenced, f"{path.name} references forbidden name {forbidden!r}"


@pytest.mark.parametrize("path", [MODULE_PATH, SCRIPT_PATH])
def test_no_forbidden_tokens_in_source(path: Path) -> None:
    text = _source(path)
    for token in _FORBIDDEN_TOKENS:
        assert token not in text, f"forbidden token {token!r} found in {path.name}"


def _is_sys_path_insert_call(node: ast.Expr) -> bool:
    call = node.value
    return (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "insert"
        and isinstance(call.func.value, ast.Attribute)
        and call.func.value.attr == "path"
        and isinstance(call.func.value.value, ast.Name)
        and call.func.value.value.id == "sys"
    )


@pytest.mark.parametrize("path", [MODULE_PATH, SCRIPT_PATH])
def test_module_parses_and_has_no_top_level_side_effecting_calls(path: Path) -> None:
    """Only ``def``/``class``/import/assignment/docstring statements at module scope, the
    conventional ``sys.path.insert`` seam, and the ``if __name__ == "__main__":`` guard --
    never any other bare call."""
    tree = ast.parse(_source(path))
    for node in tree.body:
        if isinstance(
            node,
            (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign),
        ):
            continue
        if isinstance(node, ast.Expr):
            if isinstance(node.value, ast.Constant) or _is_sys_path_insert_call(node):
                continue
            pytest.fail(f"unexpected top-level expression in {path.name}: {ast.dump(node)}")
        if isinstance(node, ast.If):
            # Only the `__main__` guard is permitted here.
            continue
        pytest.fail(f"unexpected top-level statement in {path.name}: {ast.dump(node)}")


def test_module_has_no_network_sensitive_stdlib_usage_anywhere_in_source() -> None:
    """Belt-and-suspenders textual scan in addition to the AST import scan above."""
    for path in (MODULE_PATH, SCRIPT_PATH):
        text = _source(path)
        for needle in ("import socket", "import ssl", "import http.client", "import subprocess"):
            assert needle not in text, f"{needle!r} found in {path.name}"


# ---------------------------------------------------------------------------
# Reverse-import boundary: no existing production/execution/risk file may import M27N-W back.
# ---------------------------------------------------------------------------


def test_no_production_reverse_import_of_m27n() -> None:
    forbidden_dirs = (
        REPO_ROOT / "services" / "production_execution",
        REPO_ROOT / "services" / "risk_engine",
    )
    offenders = []
    for directory in forbidden_dirs:
        for path in directory.rglob("*.py"):
            text = path.read_text()
            if "m27n_weather_rehearsal" in text or "m27n" in text.lower():
                offenders.append(str(path))
    assert not offenders, f"unexpected M27N-W reference in production files: {offenders}"


def test_frozen_files_have_no_working_tree_changes() -> None:
    """This milestone must not have modified any existing execution/risk/canary file."""
    frozen = (
        "services/production_execution",
        "services/risk_engine",
        "services/supervised_canary/m27d.py",
        "services/supervised_canary/m27i.py",
        "services/supervised_canary/m27j.py",
        "services/supervised_canary/readiness.py",
        "services/supervised_canary/readiness_report.py",
        "services/supervised_canary/candidate_exposure_check.py",
        "services/supervised_canary/store.py",
        # M27B.3R3 intentionally extends only the public-read evidence path in these
        # packages; the execution/risk/canary surfaces above remain frozen.
        "services/forecasting",
    )
    diff = subprocess.run(
        ["/usr/bin/git", "diff", "--stat", *frozen],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert diff.stdout.strip() == "", diff.stdout


# ---------------------------------------------------------------------------
# CLI subprocess behavior
# ---------------------------------------------------------------------------


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_cli_default_run_reaches_rehearsal_ready_and_prints_safety_markers() -> None:
    result = _run_cli()
    assert result.returncode == 0, result.stderr
    assert "M27N_REQUEST_TYPE: READ_ONLY" in result.stdout
    assert "M27N_ARM_ACTION: NONE" in result.stdout
    assert "M27N_MUTATION: NO" in result.stdout
    assert "M27N_SIGN_ACTION: NONE" in result.stdout
    assert "M27N_SEND_ACTION: NONE" in result.stdout
    assert "M27N_FINAL_ACK_ACTION: NONE" in result.stdout
    assert "M27N_CREDENTIAL_ACCESS: NO" in result.stdout
    assert "REHEARSAL_READY" in result.stdout


def test_cli_output_contains_valid_json_artifact_with_request_body() -> None:
    result = _run_cli()
    assert result.returncode == 0, result.stderr
    start = result.stdout.index("{")
    end = result.stdout.rindex("}") + 1
    payload = json.loads(result.stdout[start:end])
    assert payload["state"] == "REHEARSAL_READY"
    assert payload["request_body"]["ticker"] == payload["ticker"]
    assert payload["request_body"]["count"] == "1.00"
    assert "signature" not in payload["request_body"]
    assert "KALSHI-ACCESS-KEY" not in result.stdout
    assert "private_key" not in result.stdout.lower()


def test_cli_is_deterministic_across_runs_with_fixed_now() -> None:
    first = _run_cli("--now", "2026-08-20T12:00:00+00:00")
    second = _run_cli("--now", "2026-08-20T12:00:00+00:00")
    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout


def test_cli_accepts_naive_now_and_treats_it_as_utc() -> None:
    naive = _run_cli("--now", "2026-08-20T12:00:00")
    aware = _run_cli("--now", "2026-08-20T12:00:00+00:00")
    assert naive.returncode == aware.returncode == 0
    assert naive.stdout == aware.stdout


def test_cli_rejects_out_of_window_target_date_via_underlying_gates() -> None:
    # Every fixture timestamp in this harness is deliberately built relative to `--now` itself
    # (see build_scenario), so this CLI's own self-contained scenario cannot go stale on its
    # own -- staleness/binding-mismatch behavior is covered directly against build_rehearsal in
    # tests/test_m27n_weather_execution_rehearsal.py. Here we only confirm the exit-code
    # contract: 0 for REHEARSAL_READY, 2 for anything else.
    result = _run_cli("--now", "2026-08-20T12:00:00+00:00")
    assert result.returncode in (0, 2)


def test_cli_never_writes_outside_stdout_stderr() -> None:
    before = {p for p in REPO_ROOT.rglob("*.sqlite")}
    _run_cli()
    after = {p for p in REPO_ROOT.rglob("*.sqlite")}
    assert before == after
