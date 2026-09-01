"""Run one operator smoke without an implicit timeout and write a process receipt.

This wrapper is deliberately operational glue: it does not import application code, add
credentials, or choose a network host. The command after ``--`` is recorded verbatim and its
Python interpreter is required to be invoked with ``-u`` (or ``PYTHONUNBUFFERED=1`` is set).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import shlex
import signal
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a bounded smoke with a process receipt")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--code-sha", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        raise SystemExit("a command after -- is required")
    requested_command = list(command)
    if Path(command[0]).name.startswith("python") and "-u" not in command[1:2]:
        command.insert(1, "-u")

    args.run_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = args.run_dir / "stdout.log"
    stderr_path = args.run_dir / "stderr.log"
    receipt_path = args.run_dir / "process-receipt.json"
    started_at = _now()
    usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(  # noqa: S603 -- reviewed operator-supplied smoke command
            command,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        pid = process.pid
        return_code = process.wait()
    finished_at = _now()
    usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    signal_name = None
    if return_code < 0:
        try:
            signal_name = signal.Signals(-return_code).name
        except ValueError:
            signal_name = f"SIG{-return_code}"
    receipt = {
        "pid": pid,
        "command": command,
        "requested_command": requested_command,
        "command_display": shlex.join(command),
        "code_sha": args.code_sha,
        "started_at": started_at,
        "finished_at": finished_at,
        "exit_code": return_code if return_code >= 0 else None,
        "terminating_signal": signal_name,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "stdout_sha256": _sha256(stdout_path),
        "stderr_sha256": _sha256(stderr_path),
        "database_hashes": {
            name: _sha256(args.run_dir / name)
            for name in ("universe.sqlite", "observations.sqlite")
        },
        "resource_use": {
            "user_cpu_seconds": usage_after.ru_utime - usage_before.ru_utime,
            "system_cpu_seconds": usage_after.ru_stime - usage_before.ru_stime,
            "max_rss": usage_after.ru_maxrss,
        },
        "timeout": None,
    }
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
