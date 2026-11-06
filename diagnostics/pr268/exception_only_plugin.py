"""Exception-only observation. No per-call trace or pre-probe resource reads."""

from __future__ import annotations

import sys
from types import CodeType
from typing import Any

_CODE: CodeType | None = None
_CAPTURE: dict[str, Any] | None = None
_ENABLED = False
_TOOL = 4
_PREFIX = "tests/test_wn_host_feasibility_v3.py::"


def state(frame: Any) -> dict[str, Any]:
    process = frame.f_locals.get("process")
    popen = getattr(process, "_popen", None)
    return {
        "pid": getattr(popen, "pid", None),
        "cached_exitcode": getattr(popen, "returncode", None),
        "closed": getattr(process, "_closed", None),
        "reaped": frame.f_locals.get("reaped"),
        "incoming_fd": getattr(frame.f_locals.get("incoming"), "_handle", None),
        "reason": frame.f_locals.get("reason"),
    }


def handled(code: CodeType, offset: int, exception: BaseException) -> None:
    global _CAPTURE
    if code is not _CODE or _CAPTURE is not None:
        return
    # Expected deliberate fault injections are not unexplained CI exceptions.
    if str(exception) in (
        "[Errno 5] SIMULATED_FAILURE_AFTER_START",
        "SIMULATED_INTERRUPTION_AFTER_START",
    ):
        return
    tb = exception.__traceback__
    while tb is not None and tb.tb_frame.f_code is not code:
        tb = tb.tb_next
    if tb is None:
        raise RuntimeError("Original exception lacks probe traceback")
    frame = tb.tb_frame
    # Capture decisive primitive state BEFORE importing formatters or scanning FDs.
    _CAPTURE = {
        "type": type(exception).__name__,
        "errno": getattr(exception, "errno", None),
        "message": str(exception),
        "source_file": code.co_filename,
        "line": tb.tb_lineno,
        "handler_offset": offset,
        "at_exception": state(frame),
    }
    import os
    import resource
    import traceback

    _CAPTURE["traceback"] = "".join(traceback.format_exception(exception))
    _CAPTURE["nofile"] = list(resource.getrlimit(resource.RLIMIT_NOFILE))
    _CAPTURE["nproc"] = list(resource.getrlimit(resource.RLIMIT_NPROC))
    targets = {}
    try:
        names = os.listdir("/proc/self/fd")
    except OSError:
        names = []
    for name in names[:4096]:
        try:
            targets[name] = os.readlink("/proc/self/fd/" + name)
        except OSError:
            targets[name] = "UNAVAILABLE_AFTER_EXCEPTION"
    _CAPTURE["fd_targets_after_exception"] = targets
    _CAPTURE["fd_scan_truncated"] = len(names) > 4096
    sys.monitoring.set_local_events(
        _TOOL, code, sys.monitoring.events.LINE | sys.monitoring.events.PY_RETURN
    )


def line_event(code: CodeType, line: int) -> None:
    if _CAPTURE is None or code is not _CODE or line != 314:
        return
    # Frozen SHA2ab58998... has process.close() at314. No source reads before failure.
    _CAPTURE["before_process_close"] = state(sys._getframe(1))


def return_event(code: CodeType, offset: int, result: Any) -> None:
    if _CAPTURE is None or code is not _CODE or "result" in _CAPTURE:
        return
    _CAPTURE["result"] = {
        name: getattr(result, name, None)
        for name in ("reason", "phase", "pid", "reaped", "forced_kill", "max_ipc_buffer")
    }
    _CAPTURE["after_return"] = state(sys._getframe(1))
    sys.monitoring.set_local_events(_TOOL, code, 0)


def pytest_collection_finish(session: Any) -> None:
    global _CODE
    module = sys.modules["services.forecasting.wn_host_simulation_v3"]
    _CODE = module.probe_generated.__code__
    sys.monitoring.use_tool_id(_TOOL, "WN_EXCEPTION_ONLY_DIAGNOSTIC")
    sys.monitoring.register_callback(_TOOL, sys.monitoring.events.EXCEPTION_HANDLED, handled)
    sys.monitoring.register_callback(_TOOL, sys.monitoring.events.LINE, line_event)
    sys.monitoring.register_callback(_TOOL, sys.monitoring.events.PY_RETURN, return_event)


def pytest_runtest_setup(item: Any) -> None:
    global _ENABLED
    if not _ENABLED and item.nodeid.startswith(_PREFIX):
        sys.monitoring.set_events(_TOOL, sys.monitoring.events.EXCEPTION_HANDLED)
        _ENABLED = True


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    import json
    import os
    from pathlib import Path

    payload = {
        "schema": "exception-only-v1",
        "python": sys.version,
        "tests_collected": session.testscollected,
        "exitstatus": exitstatus,
        "activated": _ENABLED,
        "capture": _CAPTURE,
        "limitation": "Monitoring setup/callbacks may perturb state; no live-host proof.",
    }
    with Path(os.environ["WN_EXCEPTION_ONLY_OUTPUT"]).open("x") as output:
        json.dump(payload, output, indent=2)
        output.write("\n")
    sys.monitoring.free_tool_id(_TOOL)
