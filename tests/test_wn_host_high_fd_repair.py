"""High-numbered real pipes and selector lifecycle regressions; no source transport."""

from __future__ import annotations

import errno
import fcntl
import multiprocessing
import os
import resource
import selectors
import stat
import threading
import time
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

import pytest

from services.forecasting import wn_host_simulation_v3 as host


def require_high_fd(target: int) -> None:
    if resource.getrlimit(resource.RLIMIT_NOFILE)[0] <= target + 8:
        pytest.fail(f"HIGH_FD_ENVIRONMENT_UNSUPPORTED target={target}; high case NOT TESTED")
    if not hasattr(fcntl, "F_DUPFD_CLOEXEC"):
        pytest.fail("HIGH_FD_ENVIRONMENT_UNSUPPORTED F_DUPFD_CLOEXEC; high case NOT TESTED")


def relocate_pipe(monkeypatch: pytest.MonkeyPatch, target: int) -> list[int]:
    require_high_fd(target)
    context = multiprocessing.get_context("spawn")
    original = context.Pipe
    recorded: list[int] = []

    def pipe(duplex: bool = True) -> tuple[Connection, Connection]:
        incoming, outgoing = original(duplex)
        try:
            fd = fcntl.fcntl(incoming.fileno(), fcntl.F_DUPFD_CLOEXEC, target)
        except OSError as error:
            incoming.close()
            outgoing.close()
            if error.errno in (errno.EMFILE, errno.ENFILE, errno.EINVAL):
                pytest.fail(f"HIGH_FD_ENVIRONMENT_UNSUPPORTED errno={error.errno}; NOT TESTED")
            raise
        incoming.close()
        recorded.append(fd)
        return Connection(fd, readable=True, writable=False), outgoing

    monkeypatch.setattr(context, "Pipe", pipe)
    return recorded


def observe_selector(
    monkeypatch: pytest.MonkeyPatch, fault: str | None = None
) -> tuple[list[selectors.BaseSelector], list[int], list[float | None], list[str]]:
    original = selectors.DefaultSelector
    instances: list[selectors.BaseSelector] = []
    registered: list[int] = []
    timeouts: list[float | None] = []
    closed: list[str] = []

    def create() -> selectors.BaseSelector:
        if fault == "construct":
            raise OSError(errno.EMFILE, "SYNTHETIC_SELECTOR_CONSTRUCTION")
        selector = original()
        instances.append(selector)
        fileno = getattr(selector, "fileno", None)
        selector_fd = fileno() if fileno is not None else None
        register, poll, close = selector.register, selector.select, selector.close

        def register_once(fileobj: Any, events: int, data: Any = None) -> selectors.SelectorKey:
            assert events == selectors.EVENT_READ
            fd = fileobj.fileno()
            assert stat.S_ISFIFO(os.fstat(fd).st_mode)
            registered.append(fd)
            if fault == "register":
                raise OSError(errno.EIO, "SYNTHETIC_SELECTOR_REGISTRATION")
            return register(fileobj, events, data)

        def select(timeout: float | None = None) -> list[tuple[selectors.SelectorKey, int]]:
            timeouts.append(timeout)
            if fault == "poll":
                raise ValueError("SYNTHETIC_SELECTOR_POLL")
            return poll(timeout)

        def close_once() -> None:
            closed.append("close")
            close()
            if selector_fd is not None:
                with pytest.raises(OSError) as error:
                    os.fstat(selector_fd)
                assert error.value.errno == errno.EBADF
            if fault == "close":
                raise OSError(errno.EIO, "SYNTHETIC_SELECTOR_CLOSE_AFTER_RELEASE")

        monkeypatch.setattr(selector, "register", register_once)
        monkeypatch.setattr(selector, "select", select)
        monkeypatch.setattr(selector, "close", close_once)
        return selector

    monkeypatch.setattr(selectors, "DefaultSelector", create)
    return instances, registered, timeouts, closed


def assert_reaped(result: host.HostProbe) -> None:
    assert result.reaped and result.pid is not None
    with pytest.raises(ChildProcessError):
        os.waitpid(result.pid, os.WNOHANG)
    assert not result.live_authorized and not result.point_score_authorized


def test_native_pipe_registration_and_release(monkeypatch: pytest.MonkeyPatch) -> None:
    instances, registered, timeouts, closed = observe_selector(monkeypatch)
    result = host.probe_generated(host.GeneratedScenario())
    assert result.reason == "HTTP_200_UNVERIFIED"
    assert len(registered) == 1 and timeouts and closed == ["close"]
    assert instances[0].get_map() is None
    assert_reaped(result)
    print(f"NATIVE_REGISTERED_FD={registered[0]}")


@pytest.mark.parametrize("target", [1024, 2753])
def test_real_high_pipe_completes_once_and_releases_selector(
    monkeypatch: pytest.MonkeyPatch, target: int
) -> None:
    pipes = relocate_pipe(monkeypatch, target)
    instances, registered, timeouts, closed = observe_selector(monkeypatch)
    result = host.probe_generated(host.GeneratedScenario(pattern=b"x", body_size=65536))
    assert registered == [pipes[0]] and registered[0] >= target
    print(f"HIGH_REGISTERED_FD={registered[0]} REQUIRED={target}")
    assert result.reason == "HTTP_200_UNVERIFIED" and result.body == b"x" * 65536
    assert result.max_ipc_buffer <= host.MAX_WIRE + 4
    assert timeouts and all(t is not None and 0 <= t <= 0.01 for t in timeouts)
    assert closed == ["close"] and instances[0].get_map() is None
    assert_reaped(result)
    for fd in pipes:
        with pytest.raises(OSError, match="Bad file descriptor"):
            os.fstat(fd)


@pytest.mark.parametrize("fault", ["construct", "register", "poll", "close"])
def test_selector_failures_are_negative_close_resources_and_reap(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    pipes = relocate_pipe(monkeypatch, 2753)
    instances, registered, _, closed = observe_selector(monkeypatch, fault)
    result = host.probe_generated(
        host.GeneratedScenario() if fault == "close" else host.GeneratedScenario(hang_phase="DNS")
    )
    assert result.reason == "HOST_FAILURE" and not result.body_complete
    assert_reaped(result)
    assert len(instances) == (0 if fault == "construct" else 1)
    assert len(registered) == (0 if fault == "construct" else 1)
    assert closed == ([] if fault == "construct" else ["close"])
    assert all(selector.get_map() is None for selector in instances)
    for fd in pipes:
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.parametrize("mode", ["cancel", "expired"])
def test_no_selector_or_child_before_admission(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    instances, registered, _, closed = observe_selector(monkeypatch)
    token = threading.Event()
    token.set()
    result = (
        host.probe_generated(host.GeneratedScenario(), cancelled=token)
        if mode == "cancel"
        else host.probe_generated(host.GeneratedScenario(), total_end_ns=time.monotonic_ns() - 1)
    )
    assert result.reason == ("CANCELLED" if mode == "cancel" else "TOTAL_DEADLINE")
    assert result.pid is None
    assert not instances and not registered and not closed


def open_fds() -> set[int]:
    root = Path("/proc/self/fd" if Path("/proc/self/fd").exists() else "/dev/fd")
    result = set()
    for path in root.iterdir():
        try:
            fd = int(path.name)
            os.fstat(fd)
            result.add(fd)
        except (OSError, ValueError):
            continue
    return result


def test_repeated_high_probes_leave_no_pipe_or_selector_descriptors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Warm only the persistent multiprocessing resource tracker before measuring.
    assert_reaped(host.probe_generated(host.GeneratedScenario()))
    before = open_fds()
    pipes = relocate_pipe(monkeypatch, 2753)
    instances, _, _, closed = observe_selector(monkeypatch)
    for _ in range(4):
        result = host.probe_generated(host.GeneratedScenario())
        assert result.reason == "HTTP_200_UNVERIFIED"
        assert_reaped(result)
    assert len(instances) == len(closed) == 4
    assert all(selector.get_map() is None for selector in instances)
    for fd in pipes:
        with pytest.raises(OSError):
            os.fstat(fd)
    # Unowned deferred SQLite descriptors may close; new owned descriptors may not remain.
    assert open_fds() <= before


def test_start_failure_allocates_no_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    from multiprocessing.process import BaseProcess

    def failed_start(self: BaseProcess) -> None:
        raise OSError(errno.EAGAIN, "SYNTHETIC_START_FAILURE")

    instances, _, _, closed = observe_selector(monkeypatch)
    monkeypatch.setattr(BaseProcess, "start", failed_start)
    pipes = relocate_pipe(monkeypatch, 2753)
    before = open_fds()
    result = host.probe_generated(host.GeneratedScenario())
    assert result.reason == "HOST_FAILURE" and result.pid is None and result.reaped
    assert not instances and not closed and open_fds() <= before
    for fd in pipes:
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.parametrize("stage", ["register", "select", "close"])
def test_interruption_does_not_bypass_selector_or_worker_cleanup(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    from multiprocessing.process import BaseProcess

    # The resource tracker is persistent process infrastructure, not a leaked selector.
    assert_reaped(host.probe_generated(host.GeneratedScenario()))
    original_factory, original_start = selectors.DefaultSelector, BaseProcess.start
    selectors_created: list[selectors.BaseSelector] = []
    selector_fds: list[int] = []
    workers: list[BaseProcess] = []

    def start(process: BaseProcess) -> None:
        original_start(process)
        workers.append(process)

    def create() -> selectors.BaseSelector:
        selector = original_factory()
        selectors_created.append(selector)
        fileno = getattr(selector, "fileno", None)
        if fileno is not None:
            selector_fds.append(fileno())
        original_close = selector.close

        def interrupted(*args: Any, **kwargs: Any) -> Any:
            if stage == "close":
                original_close()
            raise KeyboardInterrupt("SYNTHETIC_SELECTOR_INTERRUPTION")

        monkeypatch.setattr(selector, stage, interrupted)
        return selector

    relocate_pipe(monkeypatch, 2753)
    monkeypatch.setattr(selectors, "DefaultSelector", create)
    monkeypatch.setattr(BaseProcess, "start", start)
    before = open_fds()
    with pytest.raises(KeyboardInterrupt, match="SYNTHETIC_SELECTOR_INTERRUPTION"):
        host.probe_generated(host.GeneratedScenario())
    assert len(workers) == len(selectors_created) == 1
    with pytest.raises(ValueError, match="process object is closed"):
        workers[0].is_alive()
    assert selectors_created[0].get_map() is None
    for fd in selector_fds:
        with pytest.raises(OSError):
            os.fstat(fd)
    assert open_fds() <= before
