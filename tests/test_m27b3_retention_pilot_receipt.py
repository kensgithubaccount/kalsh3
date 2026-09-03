"""Tests for the dedicated M27B.3 24-hour retention pilot operator entrypoint.

Mirrors ``tests/test_m27b3_smoke_receipt.py``'s coverage for the bounded-smoke wrapper -- this
file proves the pilot wrapper (``scripts/run_m27b3_retention_pilot_receipt.py``) reuses the exact
same reviewed security/provenance/fail-closed patterns while fixing the reviewed 96-scan/
900-second-cadence/28-GiB-budget/8-GiB-floor pilot policy instead of the smoke's one-scan shape,
and that the operator cannot override any of it. It does not duplicate coverage of the child
watchdog mechanism itself (``structural_measurement_runner._start_parent_watchdog``), which is
unchanged and already covered by ``tests/test_m27b3_smoke_receipt.py``; it does verify this
wrapper wires the watchdog environment identically.
"""

from __future__ import annotations

import json
import signal
from pathlib import Path

import pytest

from scripts import run_m27b3_retention_pilot_receipt as pilot
from scripts import run_m27b3_smoke_receipt as smoke

SHA = "e8c6faff5a72db6010fd4ae22713b0a0831b947e"
TREE = "353aeba5d99c67c5baa4c72901965b323367ecbf"


def identity(*, clean: bool = True, head: str = SHA, tree: str = TREE) -> pilot.RepositoryIdentity:
    return pilot.RepositoryIdentity(Path("/repo"), head, tree, clean)


# -- reviewed pilot policy values are exact and match the smoke wrapper's own reviewed values --
# -- except max_iterations, which is the one deliberate, reviewed difference -------------------


def test_reviewed_policy_constants_are_exact(tmp_path: Path) -> None:
    assert pilot.MAX_ITERATIONS == 96
    assert pilot.CADENCE_SECONDS == 900
    assert pilot.BUDGET_GIB == 28
    assert pilot.FREE_SPACE_FLOOR_GIB == 8
    assert pilot.EXPECTED_SCANS == 96
    assert pilot.HOST == "external-api.kalshi.com"
    assert pilot.MODULE == smoke.MODULE
    assert pilot.HOST == smoke.HOST
    assert pilot.BUDGET_GIB == smoke.BUDGET_GIB
    assert pilot.FREE_SPACE_FLOOR_GIB == smoke.FREE_SPACE_FLOOR_GIB
    assert pilot.EXPECTED_SCANS == smoke.EXPECTED_SCANS
    # The only deliberate policy difference: the smoke wrapper fixes exactly one scan.
    smoke_command = smoke.build_command(Path("/bin/echo"), tmp_path)
    assert smoke_command[smoke_command.index("--max-iterations") + 1] == "1"


def test_command_is_exact_with_fixed_pilot_values_and_database_paths_are_contained(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run"
    run.mkdir()
    command = pilot.build_command(Path("/bin/echo"), run)
    assert command[1:] == [
        "-u",
        "-m",
        pilot.MODULE,
        "--archive",
        str(run / "universe.sqlite"),
        "--evidence-db",
        str(run / "observations.sqlite"),
        "--live-public-read",
        "--cadence-seconds",
        "900",
        "--max-iterations",
        "96",
        "--source-authority",
        pilot.HOST,
        "--storage-budget-gib",
        "28",
        "--free-space-floor-gib",
        "8",
        "--expected-scans",
        "96",
    ]
    escaped = list(command)
    escaped[5] = str(tmp_path / "escape.sqlite")
    with pytest.raises(pilot.ReceiptValidationError, match="escapes"):
        pilot._validate_child_paths(escaped, run)
    pilot._validate_child_paths(command, run)  # must not raise
    with pytest.raises(pilot.ReceiptValidationError, match="executable absolute"):
        pilot.build_command(Path("python"), run)


def test_parser_accepts_only_the_five_fixed_flags_and_rejects_every_override_attempt() -> None:
    for extra in (
        ["--unknown"],
        ["--python", "/bin/echo"],
        ["--source-authority", "other"],
        ["--max-iterations", "1"],
        ["--max-iterations", "97"],
        ["--cadence-seconds", "60"],
        ["--storage-budget-gib", "1000"],
        ["--free-space-floor-gib", "0"],
        ["--expected-scans", "1"],
        ["--api-key", "secret"],
    ):
        with pytest.raises(SystemExit):
            pilot._parser().parse_args(extra)


def test_child_environment_excludes_secrets_and_matches_the_smoke_wrapper_allowlist() -> None:
    environment = pilot.build_environment(
        {"KALSHI_API_KEY": "secret", "BEARER_TOKEN": "secret", "PATH": "/bad", "LANG": "C"}
    )
    assert environment == {"LANG": "C", "PYTHONUNBUFFERED": "1"}
    assert pilot.ENVIRONMENT_ALLOWLIST == smoke.ENVIRONMENT_ALLOWLIST


def test_identity_validation_rejects_false_sha_tree_and_dirty_state() -> None:
    with pytest.raises(pilot.ReceiptValidationError, match="HEAD"):
        pilot._validate_identity("wrong", TREE, identity())
    with pytest.raises(pilot.ReceiptValidationError, match="tree"):
        pilot._validate_identity(SHA, "wrong", identity())
    with pytest.raises(pilot.ReceiptValidationError, match="clean"):
        pilot._validate_identity(SHA, TREE, identity(clean=False))


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"head": "wrong"}, "HEAD"),
        ({"tree": "wrong"}, "tree"),
        ({"clean": False}, "clean"),
    ],
)
def test_wrong_identity_fails_before_any_child_is_launched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, object], match: str
) -> None:
    parent = tmp_path / "runs"
    parent.mkdir()
    monkeypatch.setattr(pilot, "repository_identity", lambda: identity(**kwargs))

    def _fail_if_launched(*_args: object, **_kwargs: object) -> None:
        pytest.fail("child launched despite bad identity")

    monkeypatch.setattr(pilot.subprocess, "Popen", _fail_if_launched)
    with pytest.raises(pilot.ReceiptValidationError, match=match):
        pilot.main(
            [
                "--parent-dir",
                str(parent),
                "--run-dir",
                str(parent / "run"),
                "--expected-code-sha",
                SHA,
                "--expected-tree",
                TREE,
                "--python",
                "/bin/echo",
            ]
        )
    assert not (parent / "run").exists()


def test_run_directory_requires_new_child_inside_existing_parent(tmp_path: Path) -> None:
    parent = tmp_path / "runs"
    parent.mkdir()
    _, created = pilot.validate_run_directory(parent, parent / "new")
    assert created.is_dir()
    with pytest.raises(pilot.ReceiptValidationError, match="already exists"):
        pilot.validate_run_directory(parent, created)
    outside = tmp_path / "outside"
    with pytest.raises(pilot.ReceiptValidationError, match="outside"):
        pilot.validate_run_directory(parent, outside)
    link = parent / "link"
    link.symlink_to(tmp_path / "missing", target_is_directory=True)
    with pytest.raises(pilot.ReceiptValidationError, match="already exists"):
        pilot.validate_run_directory(parent, link)
    nested = parent / "nested" / "child"
    with pytest.raises(pilot.ReceiptValidationError, match="direct child"):
        pilot.validate_run_directory(parent, nested)


def test_atomic_receipt_writes_are_complete(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    pilot._atomic_write(path, {"status": "STARTING", "schema_version": pilot.SCHEMA_VERSION})
    assert json.loads(path.read_text())["status"] == "STARTING"
    assert not list(tmp_path.glob(".process-receipt.*"))


def test_hash_interruption_is_explicitly_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "database.sqlite"
    path.write_bytes(b"data")
    monkeypatch.setattr(
        Path, "open", lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt)
    )
    digest, complete = pilot._hash_file(path)
    assert digest is None and not complete


def test_main_receipt_lifecycle_binds_fixed_policy_and_only_succeeds_after_child_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "runs"
    parent.mkdir()
    events: list[object] = []
    monkeypatch.setattr(pilot, "repository_identity", lambda: identity())

    class Child:
        pid = 123
        returncode = 0

        def poll(self) -> int | None:
            return 0 if "spawned" in events else None

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            events.append("wait")
            return 0

        def send_signal(self, value: int) -> None:
            events.append(("signal", value))

        def kill(self) -> None:
            events.append("kill")

    captured_env: dict[str, str] = {}

    def fake_popen(*args: object, **kwargs: object) -> Child:
        events.append(("shell", kwargs.get("shell")))
        events.append("spawned")
        captured_env.update(kwargs.get("env") or {})
        return Child()

    monkeypatch.setattr(pilot.subprocess, "Popen", fake_popen)
    writes: list[dict[str, object]] = []
    monkeypatch.setattr(pilot, "_atomic_write", lambda path, payload: writes.append(dict(payload)))
    monkeypatch.setattr(pilot, "_hashes", lambda run: {})
    result = pilot.main(
        [
            "--parent-dir",
            str(parent),
            "--run-dir",
            str(parent / "one"),
            "--expected-code-sha",
            SHA,
            "--expected-tree",
            TREE,
            "--python",
            "/bin/echo",
        ]
    )
    assert result == 0
    statuses = [payload["status"] for payload in writes]
    assert statuses[:2] == ["STARTING", "RUNNING"]
    assert statuses[-1] == "COMPLETED"
    assert events[0] == ("shell", False)
    # Terminal success is represented only in the final receipt write, after the child's
    # completion status is known -- never at STARTING/RUNNING time.
    assert writes[0]["status"] != "COMPLETED"
    assert writes[1]["status"] != "COMPLETED"
    terminal = writes[-1]
    assert terminal["production_influence"] == 0
    assert type(terminal["production_influence"]) is int
    assert terminal["cadence_seconds"] == 900
    assert terminal["max_iterations"] == 96
    assert terminal["storage_budget_gib"] == 28
    assert terminal["free_space_floor_gib"] == 8
    assert terminal["expected_scans"] == 96
    assert terminal["source_authority"] == "external-api.kalshi.com"
    assert terminal["schema_version"] == pilot.SCHEMA_VERSION
    assert terminal["experiment_kind"] == pilot.EXPERIMENT_KIND
    assert terminal["schema_version"] != smoke.SCHEMA_VERSION  # never mislabeled as the smoke
    # Watchdog wiring matches the smoke wrapper's env var contract exactly (the child's own
    # watchdog mechanism, structural_measurement_runner._start_parent_watchdog, is unchanged).
    assert "M27B3_SUPERVISOR_PID" in captured_env
    assert "M27B3_PARENT_WATCHDOG_FD" in captured_env


def test_nonzero_child_has_truthfully_failed_terminal_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "runs"
    parent.mkdir()
    monkeypatch.setattr(pilot, "repository_identity", lambda: identity())

    class Child:
        pid = 456
        returncode = 7

        def poll(self) -> int:
            return 7

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return 7

    monkeypatch.setattr(pilot.subprocess, "Popen", lambda *a, **k: Child())
    monkeypatch.setattr(pilot, "_hashes", lambda run: {})
    result = pilot.main(
        [
            "--parent-dir",
            str(parent),
            "--run-dir",
            str(parent / "two"),
            "--expected-code-sha",
            SHA,
            "--expected-tree",
            TREE,
            "--python",
            "/bin/echo",
        ]
    )
    assert result == 1
    payload = json.loads((parent / "two" / "process-receipt.json").read_text())
    assert payload["status"] == "FAILED"
    assert payload["exit_code"] == 7


def test_terminal_receipt_failure_never_returns_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "runs"
    parent.mkdir()
    monkeypatch.setattr(pilot, "repository_identity", lambda: identity())

    class Child:
        pid = 222
        returncode = 0

        def poll(self) -> int:
            return 0

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return 0

    monkeypatch.setattr(pilot.subprocess, "Popen", lambda *a, **k: Child())
    original_write = pilot._atomic_write

    def selective_failure(path: Path, payload: dict[str, object]) -> None:
        if payload.get("status") == "COMPLETED":
            raise OSError("simulated terminal storage failure")
        original_write(path, payload)

    monkeypatch.setattr(pilot, "_atomic_write", selective_failure)
    assert (
        pilot.main(
            [
                "--parent-dir",
                str(parent),
                "--run-dir",
                str(parent / "run"),
                "--expected-code-sha",
                SHA,
                "--expected-tree",
                TREE,
                "--python",
                "/bin/echo",
            ]
        )
        == 1
    )


def test_sigterm_is_forwarded_and_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parent = tmp_path / "runs"
    parent.mkdir()
    monkeypatch.setattr(pilot, "repository_identity", lambda: identity())
    handlers: dict[int, object] = {}
    monkeypatch.setattr(
        pilot.signal, "signal", lambda number, handler: handlers.__setitem__(number, handler)
    )

    class Child:
        pid = 789
        returncode: int | None = None
        signaled = False

        def poll(self) -> int | None:
            if not self.signaled:
                self.signaled = True
                handlers[signal.SIGTERM](signal.SIGTERM, None)
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return self.returncode or 0

        def send_signal(self, value: int) -> None:
            assert value == signal.SIGTERM
            self.signaled = True
            self.returncode = -signal.SIGTERM

        def kill(self) -> None:
            self.returncode = -signal.SIGKILL

    monkeypatch.setattr(pilot.subprocess, "Popen", lambda *a, **k: Child())
    monkeypatch.setattr(pilot, "_hashes", lambda run: {})
    result = pilot.main(
        [
            "--parent-dir",
            str(parent),
            "--run-dir",
            str(parent / "term"),
            "--expected-code-sha",
            SHA,
            "--expected-tree",
            TREE,
            "--python",
            "/bin/echo",
        ]
    )
    assert result == 1
    payload = json.loads((parent / "term" / "process-receipt.json").read_text())
    assert payload["status"] == "SIGNALED"
    assert payload["wrapper_signal"] == "SIGTERM"
    assert payload["child_terminating_signal"] == "SIGTERM"


def test_stale_running_receipt_is_only_interrupted_and_never_completed(tmp_path: Path) -> None:
    path = tmp_path / "process-receipt.json"
    payload = {
        "status": "RUNNING",
        "wrapper_pid": 999999,
        "child_pid": 999998,
    }
    path.write_text(json.dumps(payload))
    (tmp_path / "universe.sqlite").write_bytes(b"database")
    before = path.read_bytes()
    assert pilot.inspect_receipt(path) == "INTERRUPTED"
    assert path.read_bytes() == before


def test_smoke_wrapper_is_completely_unaffected_by_the_new_pilot_wrapper(tmp_path: Path) -> None:
    """Existing smoke-wrapper behavior remains unchanged: its own fixed one-scan command shape,
    schema version, and reviewed policy constants are untouched by adding the pilot entrypoint."""
    command = smoke.build_command(Path("/bin/echo"), tmp_path)
    assert command[command.index("--max-iterations") + 1] == "1"
    assert smoke.SCHEMA_VERSION == "kalsh3.m27b3.process-receipt.v2"
    assert smoke.BUDGET_GIB == 28
    assert smoke.FREE_SPACE_FLOOR_GIB == 8
    assert smoke.EXPECTED_SCANS == 96
