from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts import run_m27b3_smoke_receipt as receipt

SHA = "e8c6faff5a72db6010fd4ae22713b0a0831b947e"
TREE = "353aeba5d99c67c5baa4c72901965b323367ecbf"


def identity(
    *, clean: bool = True, head: str = SHA, tree: str = TREE
) -> receipt.RepositoryIdentity:
    return receipt.RepositoryIdentity(Path("/repo"), head, tree, clean)


def test_identity_validation_rejects_false_sha_tree_and_dirty_state() -> None:
    with pytest.raises(receipt.ReceiptValidationError, match="HEAD"):
        receipt._validate_identity("wrong", TREE, identity())
    with pytest.raises(receipt.ReceiptValidationError, match="tree"):
        receipt._validate_identity(SHA, "wrong", identity())
    with pytest.raises(receipt.ReceiptValidationError, match="clean"):
        receipt._validate_identity(SHA, TREE, identity(clean=False))


def test_run_directory_requires_new_child_inside_existing_parent(tmp_path: Path) -> None:
    parent = tmp_path / "runs"
    parent.mkdir()
    _, created = receipt.validate_run_directory(parent, parent / "new")
    assert created.is_dir()
    with pytest.raises(receipt.ReceiptValidationError, match="already exists"):
        receipt.validate_run_directory(parent, created)
    outside = tmp_path / "outside"
    with pytest.raises(receipt.ReceiptValidationError, match="outside"):
        receipt.validate_run_directory(parent, outside)
    link = parent / "link"
    link.symlink_to(tmp_path / "missing", target_is_directory=True)
    with pytest.raises(receipt.ReceiptValidationError, match="already exists"):
        receipt.validate_run_directory(parent, link)
    nested = parent / "nested" / "child"
    with pytest.raises(receipt.ReceiptValidationError, match="direct child"):
        receipt.validate_run_directory(parent, nested)
    with pytest.raises(receipt.ReceiptValidationError, match="already exists"):
        receipt.validate_run_directory(parent, parent)
    file_parent = tmp_path / "file-parent"
    file_parent.write_text("not a directory")
    with pytest.raises(receipt.ReceiptValidationError, match="not a directory"):
        receipt.validate_run_directory(file_parent, file_parent / "run")


def test_command_is_exact_and_database_paths_are_contained(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    command = receipt.build_command(Path("/bin/echo"), run)
    assert command[1:] == [
        "-u",
        "-m",
        receipt.MODULE,
        "--archive",
        str(run / "universe.sqlite"),
        "--evidence-db",
        str(run / "observations.sqlite"),
        "--live-public-read",
        "--cadence-seconds",
        "900",
        "--max-iterations",
        "1",
        "--source-authority",
        receipt.HOST,
    ]
    escaped = list(command)
    escaped[5] = str(tmp_path / "escape.sqlite")
    with pytest.raises(receipt.ReceiptValidationError, match="escapes"):
        receipt._validate_child_paths(escaped, run)
    with pytest.raises(receipt.ReceiptValidationError, match="executable absolute"):
        receipt.build_command(Path("python"), run)


def test_parser_rejects_unknown_duplicate_and_authenticated_shapes() -> None:
    for extra in (
        ["--unknown"],
        ["--python", "/bin/echo"],
        ["--source-authority", "other"],
        ["--max-iterations", "2"],
        ["--api-key", "secret"],
    ):
        with pytest.raises(SystemExit):
            receipt._parser().parse_args(extra)


def test_child_environment_excludes_secrets_and_unrelated_values() -> None:
    environment = receipt.build_environment(
        {"KALSHI_API_KEY": "secret", "BEARER_TOKEN": "secret", "PATH": "/bad", "LANG": "C"}
    )
    assert environment == {"LANG": "C", "PYTHONUNBUFFERED": "1"}


def test_atomic_receipt_writes_are_complete(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    receipt._atomic_write(path, {"status": "STARTING", "schema_version": receipt.SCHEMA_VERSION})
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
    digest, complete = receipt._hash_file(path)
    assert digest is None and not complete


def test_main_receipt_lifecycle_is_starting_before_spawn_and_shell_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "runs"
    parent.mkdir()
    events: list[object] = []
    monkeypatch.setattr(receipt, "repository_identity", lambda: identity())

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

    def fake_popen(*args: object, **kwargs: object) -> Child:
        events.append(("shell", kwargs.get("shell")))
        events.append("spawned")
        return Child()

    monkeypatch.setattr(receipt.subprocess, "Popen", fake_popen)
    writes: list[str] = []
    monkeypatch.setattr(
        receipt, "_atomic_write", lambda path, payload: writes.append(payload["status"])
    )
    monkeypatch.setattr(receipt, "_hashes", lambda run: {})
    result = receipt.main(
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
    assert writes[:2] == ["STARTING", "RUNNING"]
    assert writes[-1] == "COMPLETED"
    assert events[0] == ("shell", False)


def test_nonzero_child_has_failed_terminal_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "runs"
    parent.mkdir()
    monkeypatch.setattr(receipt, "repository_identity", lambda: identity())

    class Child:
        pid = 456
        returncode = 7

        def poll(self) -> int:
            return 7

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return 7

    monkeypatch.setattr(receipt.subprocess, "Popen", lambda *a, **k: Child())
    monkeypatch.setattr(receipt, "_hashes", lambda run: {})
    result = receipt.main(
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


def test_running_receipt_failure_terminates_and_reaps_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "runs"
    parent.mkdir()
    monkeypatch.setattr(receipt, "repository_identity", lambda: identity())
    events: list[str] = []

    class Child:
        pid = 111
        returncode: int | None = None

        def poll(self) -> int | None:
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            events.append("wait")
            self.returncode = -signal.SIGTERM
            return self.returncode

        def send_signal(self, value: int) -> None:
            assert value == signal.SIGTERM
            events.append("term")

        def kill(self) -> None:
            events.append("kill")
            self.returncode = -signal.SIGKILL

    monkeypatch.setattr(receipt.subprocess, "Popen", lambda *a, **k: Child())
    original_write = receipt._atomic_write

    def fail_running(path: Path, payload: dict[str, object]) -> None:
        if payload.get("status") == "RUNNING":
            raise OSError("simulated receipt storage failure")
        original_write(path, payload)

    monkeypatch.setattr(receipt, "_atomic_write", fail_running)
    result = receipt.main(
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
    assert result == 1
    assert events == ["term", "wait"]


def test_popen_failure_creates_no_child_and_nonzero_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "runs"
    parent.mkdir()
    monkeypatch.setattr(receipt, "repository_identity", lambda: identity())
    monkeypatch.setattr(
        receipt.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(OSError())
    )
    result = receipt.main(
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
    assert result == 1
    assert json.loads((parent / "run" / "process-receipt.json").read_text())["status"] == "FAILED"


def test_terminal_receipt_failure_never_returns_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "runs"
    parent.mkdir()
    monkeypatch.setattr(receipt, "repository_identity", lambda: identity())

    class Child:
        pid = 222
        returncode = 0

        def poll(self) -> int:
            return 0

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return 0

    monkeypatch.setattr(receipt.subprocess, "Popen", lambda *a, **k: Child())
    # Keep STARTING/RUNNING durable while making only the terminal replacement fail.
    original_write = receipt._atomic_write

    def selective_failure(path: Path, payload: dict[str, object]) -> None:
        if payload.get("status") == "COMPLETED":
            raise OSError("simulated terminal storage failure")
        original_write(path, payload)

    monkeypatch.setattr(receipt, "_atomic_write", selective_failure)
    assert (
        receipt.main(
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
    assert receipt.inspect_receipt(path) == "INTERRUPTED"
    assert path.read_bytes() == before


def test_parent_watchdog_kills_orphaned_child(tmp_path: Path) -> None:
    marker = tmp_path / "marker"
    child_pid = tmp_path / "child.pid"
    child_code = (
        "import os,time\n"
        "from services.opportunity_engine.structural_measurement_runner "
        "import _start_parent_watchdog\n"
        f"open({str(child_pid)!r}, 'w').write(str(os.getpid()))\n"
        "_start_parent_watchdog()\n"
        f"\nwhile True:\n  open({str(marker)!r}, 'a').write('x')\n  time.sleep(.05)\n"
    )
    supervisor_code = (
        "import os,subprocess,sys,time\n"
        f"env=os.environ.copy(); env['M27B3_SUPERVISOR_PID']=str(os.getpid())\n"
        f"subprocess.Popen([sys.executable,'-c',{child_code!r}], env=env)\n"
        "time.sleep(60)\n"
    )
    supervisor = subprocess.Popen([sys.executable, "-c", supervisor_code], cwd=Path.cwd())
    try:
        deadline = time.monotonic() + 5
        while not child_pid.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert child_pid.exists()
        pid = int(child_pid.read_text())
        os.kill(supervisor.pid, signal.SIGKILL)
        supervisor.wait(timeout=5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            pytest.fail("watchdog child survived supervisor death")
        size = marker.stat().st_size
        time.sleep(0.2)
        assert marker.stat().st_size == size
    finally:
        if supervisor.poll() is None:
            supervisor.kill()
            supervisor.wait()


def test_operator_document_uses_only_the_fixed_wrapper_interface() -> None:
    document = Path("docs/M27B3R2_OPERATOR_RECEIPT.md").read_text()
    for flag in (
        "--parent-dir",
        "--run-dir",
        "--expected-code-sha",
        "--expected-tree",
        "--python",
    ):
        assert flag in document
    assert "--code-sha" not in document
    assert "-- /Users/" not in document


def test_sigterm_is_forwarded_and_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parent = tmp_path / "runs"
    parent.mkdir()
    monkeypatch.setattr(receipt, "repository_identity", lambda: identity())
    handlers: dict[int, object] = {}
    monkeypatch.setattr(
        receipt.signal, "signal", lambda number, handler: handlers.__setitem__(number, handler)
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

    monkeypatch.setattr(receipt.subprocess, "Popen", lambda *a, **k: Child())
    monkeypatch.setattr(receipt, "_hashes", lambda run: {})
    result = receipt.main(
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
