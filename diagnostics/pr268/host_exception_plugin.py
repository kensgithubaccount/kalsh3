"""Passive parent-side observer; generated IPC only, no transport or source changes."""

from __future__ import annotations

import contextlib
import json
import linecache
import os
import resource
import sys
import traceback
from pathlib import Path
from types import FrameType
from typing import Any

import pytest

_RECORDS: list[dict[str, Any]] = []
_NODE = ""
_LIMIT = 4000
_OPERATIONS: dict[tuple[str, int], str] = {}


def snapshot(frame: FrameType) -> dict[str, Any]:
    """Read already existing handles; never poll, wait, open a persistent FD, or retain frames."""
    local = frame.f_locals
    process = local.get("process")
    popen = getattr(process, "_popen", None)
    incoming = local.get("incoming")
    fd = getattr(incoming, "_handle", None)
    descriptors = []
    with contextlib.suppress(OSError):
        descriptors = [int(name) for name in os.listdir("/proc/self/fd") if name.isdigit()]
    tracker_module = sys.modules.get("multiprocessing.resource_tracker")
    tracker = getattr(tracker_module, "_resource_tracker", None)
    return {
        "source_file": frame.f_code.co_filename,
        "pid": getattr(popen, "pid", None),
        "cached_exitcode": getattr(popen, "returncode", None),
        "closed": getattr(process, "_closed", None),
        "incoming_fd": fd,
        "fd_count": len(descriptors),
        "max_fd": max(descriptors, default=None),
        "nofile": list(resource.getrlimit(resource.RLIMIT_NOFILE)),
        "nproc": list(resource.getrlimit(resource.RLIMIT_NPROC)),
        "resource_tracker_pid": getattr(tracker, "_pid", None),
        "resource_tracker_fd": getattr(tracker, "_fd", None),
        "reason": local.get("reason"),
        "reaped": local.get("reaped"),
        "phase_index": local.get("phase_index"),
    }


def observe(frame: FrameType, event: str, arg: Any) -> Any:
    if frame.f_code.co_name != "probe_generated" or not frame.f_code.co_filename.endswith(
        "/services/forecasting/wn_host_simulation_v3.py"
    ):
        return None
    if len(_RECORDS) >= _LIMIT:
        return observe
    if event == "call":
        for number, line in enumerate(linecache.getlines(frame.f_code.co_filename), 1):
            if line.strip() in ("process.start()", "process.close()"):
                _OPERATIONS[(frame.f_code.co_filename, number)] = line.strip()
    if event == "exception":
        kind, error, tb = arg
        _RECORDS.append(
            {
                "nodeid": _NODE,
                "event": event,
                "type": kind.__name__,
                "errno": getattr(error, "errno", None),
                "message": str(error),
                "traceback": "".join(traceback.format_exception(kind, error, tb)),
                "operation": linecache.getline(frame.f_code.co_filename, frame.f_lineno).strip(),
                "line": frame.f_lineno,
                "state": snapshot(frame),
            }
        )
    elif event == "line":
        operation = _OPERATIONS.get((frame.f_code.co_filename, frame.f_lineno))
        if operation is not None:
            _RECORDS.append(
                {
                    "nodeid": _NODE,
                    "event": "before_" + operation,
                    "line": frame.f_lineno,
                    "state": snapshot(frame),
                }
            )
    elif event == "return":
        _RECORDS.append(
            {
                "nodeid": _NODE,
                "event": event,
                "state": snapshot(frame),
                "result_reason": getattr(arg, "reason", None),
                "result_reaped": getattr(arg, "reaped", None),
                "result_pid": getattr(arg, "pid", None),
                "result_forced_kill": getattr(arg, "forced_kill", None),
                "result_phase": getattr(arg, "phase", None),
                "result_max_ipc_buffer": getattr(arg, "max_ipc_buffer", None),
            }
        )
    return observe


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Any:
    global _NODE
    if item.path.name != "test_wn_host_feasibility_v3.py":
        return (yield)
    _NODE = item.nodeid
    previous = sys.gettrace()
    if previous is not None:
        raise RuntimeError("Diagnostic refuses to replace an existing trace hook")
    sys.settrace(observe)
    try:
        return (yield)
    finally:
        sys.settrace(previous)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    target = os.environ.get("WN_DIAGNOSTIC_OUTPUT")
    if target is None:
        return
    payload = {
        "schema": "pr268-parent-exception-diagnostic-v1",
        "python": sys.version,
        "executable": sys.executable,
        "exitstatus": exitstatus,
        "tests_collected": session.testscollected,
        "record_limit": _LIMIT,
        "records_truncated": len(_RECORDS) >= _LIMIT,
        "records": _RECORDS,
    }
    with Path(target).open("x") as output:
        json.dump(payload, output, indent=2)
        output.write("\n")
