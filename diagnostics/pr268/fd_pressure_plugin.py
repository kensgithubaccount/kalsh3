"""Explicit synthetic FD-pressure input; never a historical evidence replacement."""

from __future__ import annotations

import json
import os
import resource
import sys
from pathlib import Path
from typing import Any

import pytest


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Any:
    held: list[int] = []
    mode = os.environ["WN_DESCRIPTOR_PRESSURE"]
    if mode not in ("low", "high"):
        raise RuntimeError("Unknown fixed discriminator mode")
    try:
        if mode == "high":
            if resource.getrlimit(resource.RLIMIT_NOFILE)[0] < 1200:
                raise RuntimeError("Insufficient existing FD allowance; no limit override")
            while not held or held[-1] < 1100:
                held.append(os.open("/dev/null", os.O_RDONLY))
                if len(held) > 1101:
                    raise RuntimeError("Synthetic descriptor cap exceeded")
        print(
            json.dumps(
                {
                    "synthetic_pressure": mode,
                    "held": len(held),
                    "highest_held_fd": max(held, default=None),
                    "python": sys.version,
                }
            )
        )
        return (yield)
    finally:
        for descriptor in held:
            os.close(descriptor)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    import gc
    import sqlite3
    import sysconfig

    target = Path(os.environ["WN_DIAGNOSTIC_OUTPUT"] + ".build.json")
    payload = {
        "sqlite": sqlite3.sqlite_version,
        "Py_GIL_DISABLED": sysconfig.get_config_var("Py_GIL_DISABLED"),
        "Py_DEBUG": sysconfig.get_config_var("Py_DEBUG"),
        "gc_threshold": gc.get_threshold(),
        "gc_stats": gc.get_stats(),
    }
    with target.open("x") as stream:
        json.dump(payload, stream, indent=2)
        stream.write("\n")
