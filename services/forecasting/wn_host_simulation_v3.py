"""Killable, generated-stream host probes. No HTTP transport or acquisition CLI."""

from __future__ import annotations

import contextlib
import multiprocessing
import os
import selectors
import signal
import struct
import threading
import time
from dataclasses import dataclass
from multiprocessing.connection import Connection
from typing import Literal

from . import wn_october_settlement_recovery as frozen
from .wn_settlement_safety_v2 import require

PHASES = ("DNS", "TLS", "HEADERS", "BODY")
CHUNK = 1024
MAX_WIRE = CHUNK + 1


def _clock_ns() -> int:
    return time.monotonic_ns()


@dataclass(frozen=True, slots=True)
class GeneratedScenario:
    """Finite byte recipe only: never accepts a URL, file, callback or credential."""

    status: int = 200
    headers: bytes = b"Content-Type: application/json\r\n"
    pattern: bytes = b"{}"
    body_size: int = 2
    hang_phase: str | None = None
    exit_phase: str | None = None
    rollback_phase: str | None = None
    phase_delay_ms: int = 0
    chunk_delay_ms: int = 0
    ignore_terminate: bool = False
    ipc_fault: str | None = None

    def __post_init__(self) -> None:
        require(type(self.status) is int and 100 <= self.status <= 599, "STATUS")
        require(
            type(self.headers) is bytes and len(self.headers) <= frozen.MAX_HEADER_BYTES + 1,
            "HEADER_RECIPE",
        )
        require(type(self.pattern) is bytes and 0 < len(self.pattern) <= CHUNK, "BYTE_RECIPE")
        require(
            type(self.body_size) is int and 0 <= self.body_size <= frozen.MAX_BODY_BYTES + 1,
            "BODY_RECIPE",
        )
        for phase in (self.exit_phase, self.rollback_phase):
            require(phase is None or (type(phase) is str and phase in PHASES), "PHASE")
        for phase in (self.hang_phase,):
            require(phase is None or (type(phase) is str and phase in (*PHASES, "EXIT")), "PHASE")
        for delay in (self.phase_delay_ms, self.chunk_delay_ms):
            require(type(delay) is int and 0 <= delay <= 5000, "DELAY")
        require(type(self.ignore_terminate) is bool, "SIGNAL_FLAG")
        require(self.ipc_fault in (None, "PARTIAL", "OVERSIZE", "WRONG_PHASE"), "IPC_RECIPE")


@dataclass(frozen=True, slots=True)
class HostProbe:
    reason: str
    phase: str
    status: int | None
    headers: bytes
    body: bytes
    headers_complete: bool
    body_complete: bool
    started_ns: int
    stopped_ns: int
    pid: int | None
    reaped: bool
    forced_kill: bool
    max_ipc_buffer: int
    simulation_only: Literal[True] = True
    live_authorized: Literal[False] = False
    point_score_authorized: Literal[False] = False

    def __post_init__(self) -> None:
        require(
            self.simulation_only is True
            and self.live_authorized is False
            and self.point_score_authorized is False,
            "NEGATIVE_ONLY",
        )


def _worker(
    output: Connection, acknowledgements: Connection, recipe: GeneratedScenario, parent_pid: int
) -> None:
    """Fixed generated script, not a general worker function or transport adapter."""
    if recipe.ignore_terminate:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)

    def write(raw: bytes) -> None:
        position = 0
        while position < len(raw):
            position += os.write(output.fileno(), raw[position:])

    def emit(raw: bytes) -> None:
        write(struct.pack("!I", len(raw)) + raw)
        if os.read(acknowledgements.fileno(), 1) != b"A":
            raise BrokenPipeError("PARENT_UNAVAILABLE")

    def block() -> None:
        # Cooperative orphan exit is a simulation feature, not native DNS proof.
        while os.getppid() == parent_pid:
            time.sleep(0.01)
        os._exit(79)

    try:
        if recipe.ipc_fault == "PARTIAL":
            write(struct.pack("!I", 10) + b"P")
            block()
        if recipe.ipc_fault == "OVERSIZE":
            write(struct.pack("!I", MAX_WIRE + 1))
            block()
        if recipe.ipc_fault == "WRONG_PHASE":
            emit(b"PBODY")
        for phase in PHASES:
            emit(b"P" + phase.encode("ascii"))
            if recipe.exit_phase == phase:
                os._exit(77)
            if recipe.rollback_phase == phase:
                emit(b"R")
            if recipe.hang_phase == phase:
                block()
            time.sleep(recipe.phase_delay_ms / 1000)
            if phase == "HEADERS":
                emit(b"S" + str(recipe.status).encode("ascii"))
                for start in range(0, len(recipe.headers), CHUNK):
                    emit(b"H" + recipe.headers[start : start + CHUNK])
            if phase == "BODY":
                for start in range(0, recipe.body_size, CHUNK):
                    size = min(CHUNK, recipe.body_size - start)
                    chunk = (recipe.pattern * (size // len(recipe.pattern) + 1))[:size]
                    time.sleep(recipe.chunk_delay_ms / 1000)
                    emit(b"B" + chunk)
        emit(b"E")
        if recipe.hang_phase == "EXIT":
            block()
    except (OSError, EOFError):
        return
    finally:
        output.close()
        acknowledgements.close()


def probe_generated(
    recipe: GeneratedScenario,
    *,
    request_ms: int = 2500,
    total_end_ns: int | None = None,
    cleanup_ms: int = 500,
    cancelled: threading.Event | None = None,
) -> HostProbe:
    """Parent owns time and nonblocking IPC; no supplied worker timestamp is trusted.

    Cleanup has its own explicit allowance. OS scheduling/syscall stalls and
    uninterruptible processes prevent hard real-time guarantees on this host.
    """
    require(type(recipe) is GeneratedScenario, "GENERATED_ONLY")
    require(type(request_ms) is int and 50 <= request_ms <= frozen.REQUEST_TIMEOUT_MS, "BUDGET")
    require(type(cleanup_ms) is int and 50 <= cleanup_ms <= 2000, "CLEANUP_BUDGET")
    require(total_end_ns is None or type(total_end_ns) is int, "TOTAL_DEADLINE_TYPE")
    require(cancelled is None or type(cancelled) is threading.Event, "CANCEL_TOKEN")
    started = _clock_ns()
    require(type(started) is int and started >= 0, "MONOTONIC_CLOCK")
    last_ns = started
    deadline = min(
        started + request_ms * 1_000_000,
        total_end_ns
        if total_end_ns is not None
        else started + frozen.TOTAL_DEADLINE_MS * 1_000_000,
    )
    status: int | None = None
    headers, body, buffered = bytearray(), bytearray(), bytearray()
    phase_index = -1
    headers_complete = body_complete = False
    peak = 0
    reason = "REQUEST_DEADLINE"
    pid: int | None = None
    reaped, forced = True, False
    context = multiprocessing.get_context("spawn")
    incoming, outgoing = context.Pipe(duplex=False)
    ack_read, ack_write = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(outgoing, ack_read, recipe, os.getpid()))
    selector: selectors.BaseSelector | None = None
    try:
        if cancelled is not None and cancelled.is_set():
            reason = "CANCELLED"
        elif started >= deadline:
            reason = "TOTAL_DEADLINE"
        else:
            process.start()
            pid = process.pid
            outgoing.close()
            ack_read.close()
            os.set_blocking(incoming.fileno(), False)
            selector = selectors.DefaultSelector()
            selector.register(incoming, selectors.EVENT_READ)
            ended = False
            while True:
                now = _clock_ns()
                if type(now) is not int or now < last_ns:
                    reason = "CLOCK_ROLLBACK"
                    break
                last_ns = now
                if cancelled is not None and cancelled.is_set():
                    reason = "CANCELLED"
                    break
                if now >= deadline:
                    reason = (
                        "TOTAL_DEADLINE"
                        if total_end_ns is not None and now >= total_end_ns
                        else "REQUEST_DEADLINE"
                    )
                    break
                if ended:
                    if not process.is_alive():
                        process.join(0)
                        reason = "HTTP_200_UNVERIFIED" if process.exitcode == 0 else "WORKER_EXIT"
                        break
                    time.sleep(min(0.005, (deadline - now) / 1_000_000_000))
                    continue
                ready = selector.select(min(0.01, (deadline - now) / 1_000_000_000))
                if not ready:
                    continue
                chunk = os.read(incoming.fileno(), min(CHUNK, MAX_WIRE + 4 - len(buffered)))
                if not chunk:
                    reason = "WORKER_EXIT"
                    break
                buffered.extend(chunk)
                peak = max(peak, len(buffered))
                if len(buffered) < 4:
                    continue
                size = struct.unpack("!I", buffered[:4])[0]
                if size == 0 or size > MAX_WIRE:
                    reason = "IPC_LIMIT"
                    break
                if len(buffered) < 4 + size:
                    continue
                raw = bytes(buffered[4 : 4 + size])
                del buffered[: 4 + size]
                tag, payload = raw[:1], raw[1:]
                if (
                    tag == b"P"
                    and phase_index + 1 < 4
                    and payload == PHASES[phase_index + 1].encode()
                ):
                    phase_index += 1
                    if phase_index == 3:
                        headers_complete = True
                elif (
                    tag == b"S"
                    and phase_index == 2
                    and status is None
                    and len(payload) == 3
                    and payload.isdigit()
                ):
                    status = int(payload)
                    if status != 200:
                        reason = "REDIRECT_REJECTED" if 300 <= status < 400 else f"HTTP_{status}"
                        break
                elif (
                    tag in (b"H", b"B")
                    and status == 200
                    and phase_index == (2 if tag == b"H" else 3)
                ):
                    target, limit = (
                        (headers, frozen.MAX_HEADER_BYTES)
                        if tag == b"H"
                        else (body, frozen.MAX_BODY_BYTES)
                    )
                    available = limit - len(target)
                    target.extend(payload[:available])
                    if len(payload) > available:
                        reason = "HEADER_LIMIT" if tag == b"H" else "BODY_LIMIT"
                        break
                elif tag == b"E" and not payload and phase_index == 3 and status == 200:
                    body_complete = True
                    ended = True
                elif tag == b"R" and not payload:
                    reason = "SIMULATED_CLOCK_ROLLBACK"
                    break
                else:
                    reason = "PROTOCOL_ERROR"
                    break
                # A one-byte ACK cannot accumulate: the child waits for it per frame.
                os.write(ack_write.fileno(), b"A")
    except (OSError, EOFError, ValueError):
        reason = "HOST_FAILURE"
    finally:
        try:
            if selector is not None:
                selector.close()
        except (OSError, ValueError):
            reason = "HOST_FAILURE"
        finally:
            # start() can raise or be interrupted after creating a child. The
            # actual process handle, not a post-return flag, owns cleanup.
            if process.pid is not None:
                pid = process.pid
                cleanup_end = time.monotonic_ns() + cleanup_ms * 1_000_000
                if process.is_alive():
                    process.terminate()
                    process.join(min(0.05, cleanup_ms / 2000))
                if process.is_alive():
                    forced = True
                    process.kill()
                    process.join(max(0, cleanup_end - time.monotonic_ns()) / 1_000_000_000)
                reaped = not process.is_alive()
                if reaped:
                    process.join(0)
                    process.close()
                else:
                    reason = "WORKER_UNREAPED"
            for connection in (incoming, outgoing, ack_read, ack_write):
                with contextlib.suppress(OSError):
                    connection.close()
    complete = reason == "HTTP_200_UNVERIFIED" and reaped
    return HostProbe(
        reason,
        PHASES[phase_index] if phase_index >= 0 else "STARTUP",
        status,
        bytes(headers),
        bytes(body),
        headers_complete,
        body_complete and complete,
        started,
        time.monotonic_ns(),
        pid,
        reaped,
        forced,
        peak,
    )
