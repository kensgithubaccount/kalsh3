from __future__ import annotations

import json
import signal
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
