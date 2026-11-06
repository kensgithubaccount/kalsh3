"""Observer self-tests: no V3 source edits or synthetic success evidence."""
# ruff: noqa: S101 -- assertions are the self-test oracle

import errno
import importlib.util
import sys
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "observer_under_test", Path(__file__).with_name("host_exception_plugin.py")
)
assert SPEC is not None and SPEC.loader is not None
observer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(observer)


def test_original_exception_and_cleanup_are_observed() -> None:
    namespace = {}
    code = compile(
        "def probe_generated():\n"
        "    reason = 'INITIAL'\n"
        "    reaped = False\n"
        "    try:\n"
        "        raise OSError(24, 'diagnostic synthetic failure')\n"
        "    except OSError:\n"
        "        reason = 'HOST_FAILURE'\n"
        "    finally:\n"
        "        reaped = True\n"
        "    return reason\n",
        "/synthetic/services/forecasting/wn_host_simulation_v3.py",
        "exec",
    )
    exec(code, namespace)  # noqa: S102 -- fixed literal synthetic code
    observer._RECORDS.clear()
    sys.settrace(observer.observe)
    try:
        assert namespace["probe_generated"]() == "HOST_FAILURE"
    finally:
        sys.settrace(None)
    records = observer._RECORDS
    error = next(row for row in records if row["event"] == "exception")
    assert error["type"] == "OSError"
    assert error["errno"] == errno.EMFILE
    assert "diagnostic synthetic failure" in error["traceback"]
    assert records[-1]["state"]["reaped"] is True
    assert isinstance(records[-1]["state"]["nofile"], list)


def test_unrelated_functions_are_not_observed() -> None:
    observer._RECORDS.clear()
    sys.settrace(observer.observe)
    try:
        assert sum((1, 2)) == 3
    finally:
        sys.settrace(None)
    assert observer._RECORDS == []
