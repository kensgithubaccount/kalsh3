"""Run exactly one M27B.3 smoke with a fail-closed process receipt."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import resource
import signal
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "kalsh3.m27b3.process-receipt.v2"
MODULE = "services.opportunity_engine.structural_measurement_runner"
HOST = "external-api.kalshi.com"
GRACE_SECONDS = 10.0
ENVIRONMENT_ALLOWLIST = frozenset(
    {
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LC_MESSAGES",
        "LC_MONETARY",
        "LC_NUMERIC",
        "LC_TIME",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TZ",
    }
)


class ReceiptValidationError(ValueError):
    """The reviewed smoke shape or evidence boundary is invalid."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(  # noqa: S603 -- fixed git argv, no shell
        ["git", "-C", str(repo), *args],  # noqa: S607 -- fixed executable name
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ReceiptValidationError("repository identity could not be observed")
    return result.stdout.strip()


@dataclass(frozen=True, slots=True)
class RepositoryIdentity:
    root: Path
    head: str
    tree: str
    clean: bool


def repository_identity() -> RepositoryIdentity:
    """Observe identity from the repository containing this wrapper."""
    root = Path(__file__).resolve().parents[1]
    actual_root = Path(_git(root, "rev-parse", "--show-toplevel")).resolve()
    return RepositoryIdentity(
        actual_root,
        _git(actual_root, "rev-parse", "HEAD"),
        _git(actual_root, "rev-parse", "HEAD^{tree}"),
        _git(actual_root, "status", "--porcelain", "--untracked-files=all") == "",
    )


def _under(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return child != parent


def validate_run_directory(parent_arg: Path, run_arg: Path) -> tuple[Path, Path]:
    parent_candidate = parent_arg.expanduser()
    if any(component.is_symlink() for component in _path_components(parent_candidate)):
        raise ReceiptValidationError("parent directory must not be a symlink")
    parent = parent_candidate.resolve(strict=True)
    if not parent.is_dir():
        raise ReceiptValidationError("parent path is not a directory")
    run = run_arg.expanduser()
    if run.exists() or run.is_symlink():
        raise ReceiptValidationError("run directory already exists")
    if any(component.is_symlink() for component in _path_components(run)):
        raise ReceiptValidationError("run directory path contains a symlink")
    run = run.resolve(strict=False)
    if run.parent != parent:
        if not _under(parent, run):
            raise ReceiptValidationError("run directory is outside the supplied parent")
        raise ReceiptValidationError("run directory must be a direct child of the supplied parent")
    try:
        run.mkdir(parents=False, exist_ok=False)
    except FileExistsError:
        raise ReceiptValidationError("run directory already exists") from None
    return parent, run


def _path_components(path: Path) -> list[Path]:
    absolute = path.absolute()
    return [Path(*absolute.parts[:index]) for index in range(1, len(absolute.parts) + 1)]


def _validate_identity(expected_sha: str, expected_tree: str, identity: RepositoryIdentity) -> None:
    if not identity.clean:
        raise ReceiptValidationError("repository working tree is not clean")
    if identity.head != expected_sha:
        raise ReceiptValidationError("repository HEAD does not match expected code SHA")
    if identity.tree != expected_tree:
        raise ReceiptValidationError("repository tree does not match expected tree")


def build_command(python: Path, run_dir: Path) -> list[str]:
    if not python.is_absolute() or not python.is_file() or not os.access(python, os.X_OK):
        raise ReceiptValidationError("Python interpreter must be an executable absolute path")
    return [
        str(python),
        "-u",
        "-m",
        MODULE,
        "--archive",
        str(run_dir / "universe.sqlite"),
        "--evidence-db",
        str(run_dir / "observations.sqlite"),
        "--live-public-read",
        "--cadence-seconds",
        "900",
        "--max-iterations",
        "1",
        "--source-authority",
        HOST,
    ]


def _validate_child_paths(command: list[str], run_dir: Path) -> None:
    for index in (5, 7):
        path = Path(command[index]).resolve(strict=False)
        if path.parent != run_dir or path.name not in {"universe.sqlite", "observations.sqlite"}:
            raise ReceiptValidationError("database path escapes the new run directory")


def build_environment(source: dict[str, str] | None = None) -> dict[str, str]:
    source_values: Mapping[str, str] = os.environ if source is None else source
    environment = {key: source_values[key] for key in ENVIRONMENT_ALLOWLIST if key in source_values}
    environment["PYTHONUNBUFFERED"] = "1"
    return environment


def _child_is_alive(process: subprocess.Popen[bytes]) -> bool:
    return process.poll() is None


def _terminate_and_reap(process: subprocess.Popen[bytes]) -> None:
    if _child_is_alive(process):
        with contextlib.suppress(OSError):
            process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(OSError):
                process.kill()
            process.wait()
        except OSError:
            with contextlib.suppress(OSError):
                process.kill()
            process.wait()
    else:
        process.wait()


def _signal_name(value: int | None) -> str | None:
    if value is None:
        return None
    try:
        return signal.Signals(value).name
    except ValueError:
        return f"SIG{value}"


def _failure_receipt(
    base: dict[str, Any],
    *,
    reason: str,
    child_pid: int | None = None,
    wrapper_signal: int | None = None,
) -> dict[str, Any]:
    return {
        **base,
        "status": "FAILED",
        "finished_at": _now(),
        "child_pid": child_pid,
        "exit_code": None,
        "child_terminating_signal": None,
        "wrapper_signal": _signal_name(wrapper_signal),
        "failure_classification": reason,
    }


def inspect_receipt(path: Path) -> str:
    """Classify a receipt without changing it or inferring completion from files."""
    payload = json.loads(path.read_text())
    status = payload.get("status")
    if status != "RUNNING":
        return str(status)
    pids = [payload.get("wrapper_pid"), payload.get("child_pid")]
    for value in pids:
        if not isinstance(value, int):
            continue
        try:
            os.kill(value, 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            return "RUNNING"
        else:
            return "RUNNING"
    return "INTERRUPTED"


def _hash_file(path: Path) -> tuple[str | None, bool]:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest(), True
    except (OSError, KeyboardInterrupt):
        return None, False


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    fd, temporary = tempfile.mkstemp(prefix=".process-receipt.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _hashes(run_dir: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for label, name in (
        ("stdout", "stdout.log"),
        ("stderr", "stderr.log"),
        ("universe", "universe.sqlite"),
        ("observations", "observations.sqlite"),
    ):
        digest, complete = _hash_file(run_dir / name)
        result[f"{label}_sha256"] = digest
        result[f"{label}_hash_complete"] = complete
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the reviewed M27B.3 smoke with a receipt")
    parser.add_argument("--parent-dir", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--expected-code-sha", required=True)
    parser.add_argument("--expected-tree", required=True)
    parser.add_argument("--python", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    identity = repository_identity()
    _validate_identity(args.expected_code_sha, args.expected_tree, identity)
    parent, run_dir = validate_run_directory(args.parent_dir, args.run_dir)
    command = build_command(args.python, run_dir)
    _validate_child_paths(command, run_dir)
    receipt_path = run_dir / "process-receipt.json"
    base = {
        "schema_version": SCHEMA_VERSION,
        "expected_code_sha": args.expected_code_sha,
        "expected_tree": args.expected_tree,
        "observed_head": identity.head,
        "observed_tree": identity.tree,
        "working_tree_clean": identity.clean,
        "command": command,
        "run_directory": str(run_dir),
        "parent_directory": str(parent),
        "source_authority": HOST,
        "production_influence": 0,
        "environment_allowlist": sorted(
            (*ENVIRONMENT_ALLOWLIST, "PYTHONUNBUFFERED", "M27B3_SUPERVISOR_PID")
        ),
        "started_at": _now(),
        "wrapper_pid": os.getpid(),
        "supervisor_pid": os.getpid(),
        "child_pid": None,
        "wrapper_signal": None,
    }
    _atomic_write(receipt_path, {**base, "status": "STARTING"})
    stdout_path, stderr_path = run_dir / "stdout.log", run_dir / "stderr.log"
    usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    wrapper_signal: int | None = None
    process: subprocess.Popen[bytes] | None = None

    def forward(received: int, _frame: Any) -> None:
        nonlocal wrapper_signal
        wrapper_signal = received
        if process is not None and process.poll() is None:
            process.send_signal(received)

    signal.signal(signal.SIGINT, forward)
    signal.signal(signal.SIGTERM, forward)
    stdout = stderr = None
    try:
        stdout = stdout_path.open("wb")
        stderr = stderr_path.open("wb")
        child_environment = build_environment()
        child_environment["M27B3_SUPERVISOR_PID"] = str(os.getpid())
        process = subprocess.Popen(  # noqa: S603 -- command is constructed above
            command,
            cwd=identity.root,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            env=child_environment,
            shell=False,
        )
        try:
            _atomic_write(receipt_path, {**base, "status": "RUNNING", "child_pid": process.pid})
        except BaseException:
            _terminate_and_reap(process)
            try:
                _atomic_write(
                    receipt_path,
                    _failure_receipt(
                        base,
                        reason="running_receipt_write_failed",
                        child_pid=process.pid,
                    ),
                )
            except Exception:
                print(
                    "M27B3 receipt failure after spawn; child terminated and reaped",
                    file=sys.stderr,
                )
            return 1
        try:
            while process.poll() is None:
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    if wrapper_signal is not None:
                        _terminate_and_reap(process)
                        break
        except BaseException:
            _terminate_and_reap(process)
            try:
                _atomic_write(
                    receipt_path,
                    _failure_receipt(base, reason="child_wait_failed", child_pid=process.pid),
                )
            except BaseException:
                print(
                    "M27B3 receipt failure after wait error; child terminated and reaped",
                    file=sys.stderr,
                )
            return 1
    except (OSError, subprocess.SubprocessError):
        if process is not None:
            _terminate_and_reap(process)
        try:
            _atomic_write(receipt_path, _failure_receipt(base, reason="child_start_failed"))
        except Exception:
            print("M27B3 receipt failure before child start", file=sys.stderr)
        return 1
    finally:
        if stderr is not None:
            stderr.close()
        if stdout is not None:
            stdout.close()

    return_code = process.returncode
    usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    child_signal = (
        _signal_name(-return_code) if return_code is not None and return_code < 0 else None
    )
    status = (
        "SIGNALED"
        if return_code is not None and return_code < 0
        else ("COMPLETED" if return_code == 0 and wrapper_signal is None else "FAILED")
    )
    try:
        hashes = _hashes(run_dir)
    except BaseException:
        hashes = {
            key: value
            for label in ("stdout", "stderr", "universe", "observations")
            for key, value in (
                (f"{label}_sha256", None),
                (f"{label}_hash_complete", False),
            )
        }
    terminal = {
        **base,
        "status": status,
        "finished_at": _now(),
        "child_pid": process.pid,
        "exit_code": return_code if return_code is not None and return_code >= 0 else None,
        "child_terminating_signal": child_signal,
        "wrapper_signal": _signal_name(wrapper_signal),
        "resource_use": {
            "user_cpu_seconds": usage_after.ru_utime - usage_before.ru_utime,
            "system_cpu_seconds": usage_after.ru_stime - usage_before.ru_stime,
            "max_rss": usage_after.ru_maxrss,
        },
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        **hashes,
    }
    try:
        _atomic_write(receipt_path, terminal)
    except BaseException:
        print("M27B3 terminal receipt write failed", file=sys.stderr)
        return 1
    return 0 if status == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
