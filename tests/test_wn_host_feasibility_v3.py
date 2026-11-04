"""Generated processes and scratch journals only; every source connection denied."""

from __future__ import annotations

import ast
import contextlib
import errno
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import replace
from multiprocessing.process import BaseProcess
from pathlib import Path

import pytest

from services.forecasting import wn_host_registry_v3 as registry
from services.forecasting import wn_host_simulation_v3 as host
from services.forecasting import wn_settlement_journal_v2 as journal
from services.forecasting.wn_settlement_safety_v2 import BoundaryError, RequestPolicy

CUTOFF = json.dumps(
    {
        "market_settled_ts": "2026-09-03T00:00:00Z",
        "orders_updated_ts": "2026-10-15T00:00:00Z",
        "trades_created_ts": "2026-09-03T00:00:00Z",
    }
).encode()


@pytest.fixture(autouse=True)
def deny_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*args: object, **kwargs: object) -> None:
        raise AssertionError("NETWORK_DENIED_OFFLINE_HOST_PROBE")

    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)


def cutoff_recipe(**kwargs: object) -> host.GeneratedScenario:
    return replace(host.GeneratedScenario(pattern=CUTOFF, body_size=len(CUTOFF)), **kwargs)


def negative(result: registry.RegistryInspection) -> None:
    assert len(result.rows) == 60 and len({r["target_date"] for r in result.rows}) == 12
    assert sum(r["capture_state"] == "FAILED" for r in result.rows) == 5
    assert sum(int(r["depth_abstentions"]) for r in result.rows) == 59
    assert sum(r["outcome_evidence_status"] == "UNVERIFIED" for r in result.rows) == 14
    assert (
        not result.live_authorized
        and not result.point_score_authorized
        and not result.resume_allowed
    )
    assert all(r["point_score_authorized"] is False for r in result.rows)


def test_incremental_stream_exact_caps_and_reaped_worker() -> None:
    result = host.probe_generated(host.GeneratedScenario(pattern=b"x", body_size=65536))
    assert result.reason == "HTTP_200_UNVERIFIED" and result.body == b"x" * 65536
    assert result.body_complete and result.headers_complete and result.reaped
    assert result.max_ipc_buffer <= host.MAX_WIRE + 4
    assert result.stopped_ns >= result.started_ns and not result.live_authorized


@pytest.mark.parametrize("phase", [*host.PHASES, "EXIT"])
def test_absolute_deadline_kills_each_blocked_phase_and_reaps(phase: str) -> None:
    result = host.probe_generated(
        host.GeneratedScenario(hang_phase=phase, ignore_terminate=True),
        request_ms=800,
        cleanup_ms=500,
    )
    assert result.reason == "REQUEST_DEADLINE" and result.reaped
    assert result.forced_kill and not result.body_complete
    assert result.phase == ("BODY" if phase == "EXIT" else phase)
    assert (result.stopped_ns - result.started_ns) < 1_800_000_000


def test_phase_delays_and_stream_progress_never_renew_deadline() -> None:
    result = host.probe_generated(host.GeneratedScenario(phase_delay_ms=160), request_ms=500)
    assert result.reason == "REQUEST_DEADLINE" and result.reaped
    streamed = host.probe_generated(
        host.GeneratedScenario(pattern=b"x", body_size=65536, chunk_delay_ms=25), request_ms=600
    )
    assert streamed.reason == "REQUEST_DEADLINE" and 0 < len(streamed.body) < 65536
    assert streamed.reaped and not streamed.body_complete


@pytest.mark.parametrize("phase", host.PHASES)
def test_cancellation_is_parent_owned_and_bounded(phase: str) -> None:
    cancelled = threading.Event()
    timer = threading.Timer(0.3, cancelled.set)
    timer.start()
    try:
        result = host.probe_generated(
            host.GeneratedScenario(hang_phase=phase), request_ms=1500, cancelled=cancelled
        )
    finally:
        timer.cancel()
        timer.join()
    assert result.reason == "CANCELLED" and result.reaped
    assert result.stopped_ns - result.started_ns < 1_300_000_000


def test_pre_cancelled_and_expired_total_launch_no_child() -> None:
    cancelled = threading.Event()
    cancelled.set()
    result = host.probe_generated(host.GeneratedScenario(), cancelled=cancelled)
    assert result.reason == "CANCELLED" and result.pid is None
    result = host.probe_generated(host.GeneratedScenario(), total_end_ns=time.monotonic_ns() - 1)
    assert result.reason == "TOTAL_DEADLINE" and result.pid is None
    result = host.probe_generated(
        host.GeneratedScenario(hang_phase="DNS"),
        request_ms=2000,
        total_end_ns=time.monotonic_ns() + 500_000_000,
    )
    assert result.reason == "TOTAL_DEADLINE" and result.reaped


@pytest.mark.parametrize(
    "fault,reason",
    [("PARTIAL", "REQUEST_DEADLINE"), ("OVERSIZE", "IPC_LIMIT"), ("WRONG_PHASE", "PROTOCOL_ERROR")],
)
def test_partial_or_hostile_ipc_cannot_block_watchdog(fault: str, reason: str) -> None:
    result = host.probe_generated(host.GeneratedScenario(ipc_fault=fault), request_ms=600)
    assert result.reason == reason and result.reaped
    assert result.max_ipc_buffer <= host.MAX_WIRE + 4


@pytest.mark.parametrize("phase", host.PHASES)
def test_worker_exit_never_completes_response(phase: str) -> None:
    result = host.probe_generated(host.GeneratedScenario(exit_phase=phase))
    assert result.reason == "WORKER_EXIT" and result.reaped and not result.body_complete


@pytest.mark.parametrize("phase", host.PHASES)
def test_simulated_clock_rollback_is_negative(phase: str) -> None:
    result = host.probe_generated(host.GeneratedScenario(rollback_phase=phase))
    assert result.reason == "SIMULATED_CLOCK_ROLLBACK" and result.reaped


def test_parent_monotonic_rollback_stops_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    original = host._clock_ns
    calls = 0
    started = original()

    def clock() -> int:
        nonlocal calls
        calls += 1
        return started if calls == 1 else started - 1

    monkeypatch.setattr(host, "_clock_ns", clock)
    result = host.probe_generated(host.GeneratedScenario(hang_phase="DNS"))
    assert result.reason == "CLOCK_ROLLBACK" and result.reaped


@pytest.mark.parametrize(
    "size,kind,reason", [(65537, "body", "BODY_LIMIT"), (16385, "headers", "HEADER_LIMIT")]
)
def test_stream_caps_before_append(size: int, kind: str, reason: str) -> None:
    recipe = (
        host.GeneratedScenario(pattern=b"z", body_size=size)
        if kind == "body"
        else host.GeneratedScenario(headers=b"x" * size)
    )
    result = host.probe_generated(recipe)
    assert result.reason == reason and result.reaped and not result.body_complete
    assert len(result.body) <= 65536 and len(result.headers) <= 16384
    assert result.max_ipc_buffer <= host.MAX_WIRE + 4


@pytest.mark.parametrize("status", [403, 429, 301, 302, 303, 307, 308, 404, 500])
def test_http_global_stop_and_no_retry(tmp_path: Path, status: int) -> None:
    parent = tmp_path.resolve()
    with registry.GeneratedRegistry.create(parent, CUTOFF) as execution:
        result = execution.run_slot(
            0,
            cutoff_recipe(
                status=status,
                headers=b"Location: https://invalid.example/portfolio/orders\r\nRetry-After: 0\r\n",
            ),
        )
        assert result is not None and result.status == status and result.reaped
        assert execution.halt_reason == (
            "REDIRECT_REJECTED" if 300 <= status < 400 else f"HTTP_{status}"
        )
        for slot in (0, 1, 84):
            with pytest.raises(BoundaryError, match="GLOBAL_STOP"):
                execution.run_slot(slot, cutoff_recipe())
    inspection = registry.inspect_registry(parent)
    negative(inspection)
    assert inspection.committed_slots == 1 and inspection.integrity == "CURRENT_BYTES_VERIFIED_ONLY"
    assert not list((parent / registry.EXECUTION_NAME).glob("s001.*"))
    with pytest.raises(FileExistsError):
        registry.GeneratedRegistry.create(parent, CUTOFF)


def test_valid_cutoff_then_exact_market_and_denominator(tmp_path: Path) -> None:
    parent = tmp_path.resolve()
    with registry.GeneratedRegistry.create(parent, CUTOFF) as execution:
        first = execution.run_slot(0, cutoff_recipe())
        assert first is not None and first.body == CUTOFF and execution.halt_reason is None
        # Wait for the unchanged frozen 3-second start interval; no virtual bypass.
        time.sleep(max(0, execution._next_start - time.monotonic_ns()) / 1_000_000_000)
        second = execution.run_slot(
            1, host.GeneratedScenario(), request=RequestPolicy(CUTOFF).slot(1)
        )
        assert second is not None and second.reason == "HTTP_200_UNVERIFIED"
        execution.stop()
    before = {
        p.name: (journal.digest(p.read_bytes()), p.stat().st_mtime_ns)
        for p in (parent / registry.EXECUTION_NAME).iterdir()
    }
    inspection = registry.inspect_registry(parent)
    negative(inspection)
    assert inspection.committed_slots == 2 and inspection.integrity == "CURRENT_BYTES_VERIFIED_ONLY"
    assert before == {
        p.name: (journal.digest(p.read_bytes()), p.stat().st_mtime_ns)
        for p in (parent / registry.EXECUTION_NAME).iterdir()
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("origin", "http://external-api.kalshi.com"),
        ("origin", "https://external-api.kalshi.com.evil.invalid"),
        ("path", "/trade-api/v2/portfolio/orders"),
        ("method", "POST"),
        ("headers", (("Authorization", "forbidden"),)),
        ("ticker", "UNFROZEN"),
    ],
)
def test_invalid_fixed_request_stops_before_worker(
    tmp_path: Path, field: str, value: object
) -> None:
    parent = tmp_path.resolve()
    with registry.GeneratedRegistry.create(parent, CUTOFF) as execution:
        forged = replace(RequestPolicy(CUTOFF).slot(0), **{field: value})
        with pytest.raises(BoundaryError):
            execution.run_slot(0, cutoff_recipe(), request=forged)
        assert execution.halt_reason == "INVALID_REQUEST_OR_SLOT"
    assert not list((parent / registry.EXECUTION_NAME).glob("s000.*"))
    negative(registry.inspect_registry(parent))


def test_all_85_paths_are_frozen_and_swaps_are_rejected() -> None:
    policy = RequestPolicy(CUTOFF)
    assert len({policy.slot(i).path for i in range(85)}) == 85
    for slot in range(85):
        request = policy.slot(slot)
        policy.check(slot, request)
        with pytest.raises(BoundaryError):
            policy.check(slot, policy.slot((slot + 1) % 85))


@pytest.mark.parametrize("slot", [-1, 1, 85, True])
def test_bad_initial_slot_and_no_backfill(tmp_path: Path, slot: int) -> None:
    parent = tmp_path.resolve()
    with registry.GeneratedRegistry.create(parent, CUTOFF) as execution:
        with pytest.raises(BoundaryError):
            execution.run_slot(slot, cutoff_recipe())
        with pytest.raises(BoundaryError, match="GLOBAL_STOP"):
            execution.run_slot(0, cutoff_recipe())
    negative(registry.inspect_registry(parent))


@pytest.mark.parametrize(
    "kind", ["duplicate", "pacing", "conflicting_cutoff", "bad_headers", "rollback"]
)
def test_other_global_stops(tmp_path: Path, kind: str) -> None:
    parent = tmp_path.resolve()
    recipe = cutoff_recipe()
    if kind == "conflicting_cutoff":
        recipe = host.GeneratedScenario()
    elif kind == "bad_headers":
        recipe = cutoff_recipe(headers=b"Content-Type: text/html\r\n")
    elif kind == "rollback":
        recipe = cutoff_recipe(rollback_phase="BODY")
    with registry.GeneratedRegistry.create(parent, CUTOFF) as execution:
        execution.run_slot(0, recipe)
        if kind == "duplicate":
            with pytest.raises(BoundaryError):
                execution.run_slot(0, cutoff_recipe())
        elif kind == "pacing":
            assert execution.run_slot(1, host.GeneratedScenario()) is None
        assert execution.halt_reason is not None
    negative(registry.inspect_registry(parent))


@pytest.mark.parametrize("kind", ["write", "zero", "fsync", "link"])
def test_disk_failures_poison_claim_without_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    parent = tmp_path.resolve()
    execution = registry.GeneratedRegistry.create(parent, CUTOFF)

    def fail(*args: object, **kwargs: object) -> int:
        if kind == "zero":
            return 0
        raise OSError(errno.ENOSPC, "SYNTHETIC_DISK_FAILURE")

    with monkeypatch.context() as patch:
        patch.setattr(os, "write" if kind == "zero" else kind, fail)
        with pytest.raises((OSError, BoundaryError)):
            execution.run_slot(0, cutoff_recipe())
    with pytest.raises(BoundaryError, match="WRITER_UNAVAILABLE"):
        execution.run_slot(0, cutoff_recipe())
    execution.close()
    with pytest.raises(FileExistsError):
        registry.GeneratedRegistry.create(parent, CUTOFF)
    negative(registry.inspect_registry(parent))


@pytest.mark.parametrize("stem", ["s000.intent", "s000.receipt", "s000.commit", "ledger"])
def test_interrupted_partial_writes_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stem: str
) -> None:
    parent = tmp_path.resolve()
    execution = registry.GeneratedRegistry.create(parent, CUTOFF)
    original = execution._publish

    def partial(name: str, raw: bytes) -> None:
        if name == stem:
            execution._failed = True
            path = parent / registry.EXECUTION_NAME / (name + ".partial")
            with path.open("xb") as stream:
                stream.write(raw[:11])
                stream.flush()
                os.fsync(stream.fileno())
            raise OSError(errno.EIO, "SIMULATED_INTERRUPTION")
        original(name, raw)

    monkeypatch.setattr(execution, "_publish", partial)
    with pytest.raises(OSError):
        execution.run_slot(0, cutoff_recipe(status=429))
    execution.close()
    result = registry.inspect_registry(parent)
    negative(result)
    assert result.integrity == "UNVERIFIED_INCOMPLETE_OR_CORRUPT"
    assert (parent / registry.EXECUTION_NAME / (stem + ".partial")).exists()


@pytest.mark.parametrize(
    "mutation", ["claim", "intent", "receipt", "commit", "ledger", "extra", "symlink"]
)
def test_integrity_failures_preserve_negative_rows(tmp_path: Path, mutation: str) -> None:
    parent = tmp_path.resolve()
    with registry.GeneratedRegistry.create(parent, CUTOFF) as execution:
        execution.run_slot(0, cutoff_recipe(status=429))
    root = parent / registry.EXECUTION_NAME
    if mutation == "extra":
        (root / "unexpected").write_bytes(b"x")
    elif mutation == "symlink":
        (root / "s000.commit.sealed").unlink()
        (root / "s000.commit.sealed").symlink_to("claim.sealed")
    else:
        name = mutation if mutation in ("claim", "ledger") else "s000." + mutation
        path = root / (name + ".sealed")
        path.chmod(0o600)
        path.write_bytes(path.read_bytes() + b"corrupt")
    result = registry.inspect_registry(parent)
    negative(result)
    assert result.integrity == "UNVERIFIED_INCOMPLETE_OR_CORRUPT"


def test_real_process_exit_after_intent_is_not_resumable(tmp_path: Path) -> None:
    parent = tmp_path.resolve()
    code = """import os,sys
from pathlib import Path
from services.forecasting.wn_host_registry_v3 import GeneratedRegistry
from services.forecasting.wn_host_simulation_v3 import GeneratedScenario
execution=GeneratedRegistry.create(Path(sys.argv[1]),bytes.fromhex(sys.argv[2]))
publish=execution._publish
def interrupted(stem,raw):
    publish(stem,raw)
    if stem=='s000.intent':os._exit(77)
execution._publish=interrupted
execution.run_slot(0,GeneratedScenario())
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(parent), CUTOFF.hex()], check=False, timeout=20
    )
    assert result.returncode == 77
    inspection = registry.inspect_registry(parent)
    negative(inspection)
    assert inspection.integrity == "UNVERIFIED_INCOMPLETE_OR_CORRUPT"
    with pytest.raises(FileExistsError):
        registry.GeneratedRegistry.create(parent, CUTOFF)


def test_fixed_execution_identity_race(tmp_path: Path) -> None:
    code = """import sys
from pathlib import Path
from services.forecasting.wn_host_registry_v3 import GeneratedRegistry
try:
    with GeneratedRegistry.create(Path(sys.argv[1]),bytes.fromhex(sys.argv[2])) as registry:
        registry.stop()
except FileExistsError:sys.exit(23)
"""
    children = [
        subprocess.Popen([sys.executable, "-c", code, str(tmp_path.resolve()), CUTOFF.hex()])
        for _ in range(2)
    ]
    assert sorted(child.wait(timeout=20) for child in children) == [0, 23]
    negative(registry.inspect_registry(tmp_path.resolve()))


def test_no_client_cli_source_substitution_or_production_imports() -> None:
    forbidden = {"requests", "httpx", "http", "urllib", "socket", "aiohttp"}
    for module in (host, registry):
        source = Path(module.__file__ or "").read_text()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                assert not {a.name.split(".")[0] for a in node.names} & forbidden
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in forbidden
        assert "__main__" not in source


def test_threaded_duplicate_execution_launches_one_worker(tmp_path: Path) -> None:
    parent = tmp_path.resolve()
    results: list[host.HostProbe | None] = []
    errors: list[BaseException] = []
    with registry.GeneratedRegistry.create(parent, CUTOFF) as execution:

        def invoke() -> None:
            try:
                results.append(execution.run_slot(0, cutoff_recipe(status=429)))
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=invoke) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            assert not thread.is_alive()
    assert len(results) == len(errors) == 1
    assert isinstance(errors[0], BoundaryError)
    assert registry.inspect_registry(parent).committed_slots == 1


def test_parent_process_termination_retains_claim_and_orphan_worker_exits(tmp_path: Path) -> None:
    parent = tmp_path.resolve()
    pidfile = parent / "generated_worker_pid"
    code = """import sys
from pathlib import Path
from multiprocessing.process import BaseProcess
from services.forecasting.wn_host_registry_v3 import GeneratedRegistry
from services.forecasting.wn_host_simulation_v3 import GeneratedScenario
original=BaseProcess.start
def start(self):
    original(self)
    Path(sys.argv[3]).write_text(str(self.pid))
BaseProcess.start=start
with GeneratedRegistry.create(Path(sys.argv[1]),bytes.fromhex(sys.argv[2])) as registry:
    registry.run_slot(0,GeneratedScenario(hang_phase='DNS',ignore_terminate=True))
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(parent), CUTOFF.hex(), str(pidfile)]
    )
    worker_pid: int | None = None
    try:
        until = time.monotonic() + 8
        while not pidfile.exists() and time.monotonic() < until:
            time.sleep(0.01)
        assert pidfile.exists()
        worker_pid = int(pidfile.read_text())
        # Allow spawn to enter its controlled blocking phase before killing its parent.
        time.sleep(0.3)
        process.kill()
        assert process.wait(timeout=5) != 0
        terminated = False
        until = time.monotonic() + 5
        while time.monotonic() < until:
            try:
                os.kill(worker_pid, 0)
            except ProcessLookupError:
                terminated = True
                worker_pid = None
                break
            time.sleep(0.02)
        assert terminated  # Reaping of an orphan is the OS's responsibility.
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if worker_pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.kill(worker_pid, signal.SIGKILL)
    negative(registry.inspect_registry(parent))
    with pytest.raises(FileExistsError):
        registry.GeneratedRegistry.create(parent, CUTOFF)


@pytest.mark.parametrize("interruption", [False, True])
def test_failure_after_actual_child_start_still_kills_and_reaps(
    monkeypatch: pytest.MonkeyPatch, interruption: bool
) -> None:
    actual_start = BaseProcess.start
    created: list[BaseProcess] = []

    def start_then_fail(process: BaseProcess) -> None:
        actual_start(process)
        created.append(process)
        if interruption:
            raise KeyboardInterrupt("SIMULATED_INTERRUPTION_AFTER_START")
        raise OSError(errno.EIO, "SIMULATED_FAILURE_AFTER_START")

    monkeypatch.setattr(BaseProcess, "start", start_then_fail)
    try:
        if interruption:
            with pytest.raises(KeyboardInterrupt):
                host.probe_generated(host.GeneratedScenario(ipc_fault="PARTIAL"))
        else:
            result = host.probe_generated(host.GeneratedScenario(ipc_fault="PARTIAL"))
            assert result.reason == "HOST_FAILURE" and result.pid is not None and result.reaped
        assert len(created) == 1
        assert created[0]._closed  # A closed Process has first been joined, never left running.
    finally:
        for process in created:
            if not process._closed:
                if process.is_alive():
                    process.kill()
                process.join(timeout=5)
                process.close()


@pytest.mark.parametrize("field", ["rollback_phase", "exit_phase"])
def test_unsupported_exit_fault_fields_are_rejected(field: str) -> None:
    with pytest.raises(BoundaryError):
        host.GeneratedScenario(**{field: "EXIT"})
