"""Run the unchanged V3 suite with real pipe read handles >=2753. Generated IPC only."""

from __future__ import annotations

import fcntl
import json
import multiprocessing
import resource
import sys
from multiprocessing.connection import Connection

import pytest

TARGET = 2753


def main() -> int:
    if resource.getrlimit(resource.RLIMIT_NOFILE)[0] <= TARGET + 8:
        print("HIGH_FD_ENVIRONMENT_UNSUPPORTED: target2753 NOT TESTED", flush=True)
        return 2
    context = multiprocessing.get_context("spawn")
    original = context.Pipe
    observed: list[int] = []

    def pipe(duplex: bool = True) -> tuple[Connection, Connection]:
        incoming, outgoing = original(duplex)
        try:
            fd = fcntl.fcntl(incoming.fileno(), fcntl.F_DUPFD_CLOEXEC, TARGET)
        except OSError:
            incoming.close()
            outgoing.close()
            raise
        incoming.close()
        observed.append(fd)
        return Connection(fd, readable=True, writable=False), outgoing

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(context, "Pipe", pipe)
        result = pytest.main(["-q", "tests/test_wn_host_feasibility_v3.py"])
    print(
        json.dumps(
            {
                "scope": "GENERATED_IPC_UNCHANGED_V3_SUITE",
                "required_minimum": TARGET,
                "actual_minimum": min(observed, default=None),
                "real_high_read_handles": len(observed),
                "pytest_exit": int(result),
                "live_authorized": False,
                "scientific_scoring_authorized": False,
                "trading_authorized": False,
            }
        ),
        flush=True,
    )
    if not observed or min(observed) < TARGET:
        return 2
    return int(result)


if __name__ == "__main__":
    sys.exit(main())
