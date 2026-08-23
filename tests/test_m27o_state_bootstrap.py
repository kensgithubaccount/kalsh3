from __future__ import annotations

import ast
import hashlib
import inspect
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from services.supervised_canary import m27o_state_bootstrap as bootstrap


def test_wrong_confirmation_creates_nothing(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"

    with pytest.raises(bootstrap.StateBootstrapError):
        bootstrap.bootstrap_state(
            state_path=path,
            actor="test",
            reason="test bootstrap",
            confirmation="WRONG",
        )

    assert not path.exists()


def test_bootstrap_creates_exact_safe_initial_state(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"

    receipt = bootstrap.bootstrap_state(
        state_path=path,
        actor="test",
        reason="reviewed local bootstrap test",
        confirmation=bootstrap.EXACT_CONFIRMATION,
    )

    assert receipt.production_state == "DISARMED"
    assert receipt.real_submission_count == 0
    assert receipt.real_fill_count == 0
    assert receipt.global_halt_active is False
    assert receipt.compliance_state == "CLEAR"
    assert receipt.kill_states == bootstrap.EXPECTED_KILLS
    assert receipt.loss_holds == (0, 0, 0)
    assert receipt.preview_count == 0
    assert receipt.approval_count == 0
    assert receipt.session_count == 0
    assert receipt.risk_authorization_count == 0
    assert receipt.risk_reservation_count == 0
    assert (path.stat().st_mode & 0o777) == 0o600

    assert not Path(str(path) + "-wal").exists()
    assert not Path(str(path) + "-shm").exists()
    assert not Path(str(path) + "-journal").exists()


def test_second_bootstrap_fails_without_modifying_state(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"

    bootstrap.bootstrap_state(
        state_path=path,
        actor="test",
        reason="first bootstrap",
        confirmation=bootstrap.EXACT_CONFIRMATION,
    )

    before = hashlib.sha256(path.read_bytes()).hexdigest()

    with pytest.raises(bootstrap.StateBootstrapError):
        bootstrap.bootstrap_state(
            state_path=path,
            actor="test",
            reason="must not reopen",
            confirmation=bootstrap.EXACT_CONFIRMATION,
        )

    after = hashlib.sha256(path.read_bytes()).hexdigest()
    assert after == before


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_stale_sqlite_companion_fails_closed(
    tmp_path: Path,
    suffix: str,
) -> None:
    path = tmp_path / "state.sqlite3"
    Path(str(path) + suffix).write_bytes(b"stale")

    with pytest.raises(bootstrap.StateBootstrapError):
        bootstrap.bootstrap_state(
            state_path=path,
            actor="test",
            reason="must reject stale SQLite companion",
            confirmation=bootstrap.EXACT_CONFIRMATION,
        )

    assert not path.exists()


def test_concurrent_bootstrap_has_exactly_one_winner(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"

    def attempt(index: int) -> str:
        try:
            bootstrap.bootstrap_state(
                state_path=path,
                actor=f"test-{index}",
                reason="concurrent create-only bootstrap test",
                confirmation=bootstrap.EXACT_CONFIRMATION,
            )
            return "PASS"
        except bootstrap.StateBootstrapError:
            return "BLOCKED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(attempt, range(2)))

    assert outcomes == ["BLOCKED", "PASS"]

    receipt = bootstrap._verify_state(path)
    assert receipt.real_submission_count == 0
    assert receipt.real_fill_count == 0


def test_cli_is_secret_free_and_remains_disarmed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "state.sqlite3"

    rc = bootstrap.main(
        [
            "--state-path",
            str(path),
            "--actor",
            "test",
            "--reason",
            "CLI bootstrap test",
            "--confirm",
            bootstrap.EXACT_CONFIRMATION,
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert '"classification": "PASS"' in output
    assert "PRODUCTION_ARMED: DISARMED" in output
    assert "REAL_SUBMISSION_COUNT: 0" in output
    assert "M16_APPROVAL: NONE" in output
    assert "M13_AUTHORIZATION: NONE" in output
    assert "M27O_EXECUTION_AUTHORIZATION: NONE" in output
    assert "KALSHI_NETWORK: NONE" in output
    assert "KALSHI_MUTATION: NONE" in output
    assert "ORDER_SENT: NO" in output


def test_bootstrap_module_has_no_network_or_live_execution_imports() -> None:
    source = inspect.getsource(bootstrap)
    tree = ast.parse(source)

    imports: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)

    forbidden_prefixes = (
        "socket",
        "urllib",
        "http.client",
        "requests",
        "httpx",
        "services.kalshi_account_gateway",
        "services.production_execution.m27o_live_canary",
        "services.production_execution.transport",
    )

    assert not any(
        imported.startswith(prefix) for imported in imports for prefix in forbidden_prefixes
    )


def test_default_path_is_outside_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_home = tmp_path / "state-home"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))

    path = bootstrap.default_state_path()

    assert path == (state_home / "kalsh3" / "production-canary" / "m27o-shared.sqlite3")
    assert not os.path.lexists(path)
