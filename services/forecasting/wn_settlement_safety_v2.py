"""Offline request/phase safety contracts; no network, clock sampler or runner."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Never, cast

from . import wn_october_settlement_recovery as frozen


class BoundaryError(ValueError):
    """A negative-only offline boundary refused an input or transition."""


def require(ok: bool, code: str) -> None:
    if not ok:
        raise BoundaryError(code)


class RequestPolicy:
    """No URL argument: derive only cutoff and 84 immutable singular-market slots."""

    def __init__(self, cutoff_body: bytes | None = None) -> None:
        self._requests: tuple[frozen.PlannedGet, ...] = (frozen.cutoff_request(),)
        if cutoff_body is not None:
            self._requests += frozen.plan_after_cutoff(cutoff_body)

    def slot(self, index: int) -> frozen.PlannedGet:
        require(type(index) is int and 0 <= index < len(self._requests), "UNBOUND_OR_BAD_SLOT")
        return self._requests[index]

    def check(self, index: int, candidate: frozen.PlannedGet) -> None:
        require(type(candidate) is frozen.PlannedGet, "BUILTIN_REQUEST_REQUIRED")
        expected = self.slot(index)
        require(candidate == expected, "FIXED_REQUEST_MISMATCH")
        require(candidate.method == "GET" and candidate.origin == frozen.ORIGIN, "FIXED_ORIGIN_GET")
        require(candidate.headers == frozen.HEADERS, "NO_CREDENTIAL_OR_EXTRA_HEADERS")

    def commitment(self) -> str:
        parts = [frozen.ROSTER_SHA256]
        for i, request in enumerate(self._requests):
            self.check(i, request)
            parts.append(request.method + " " + request.origin + request.path)
        return hashlib.sha256("\n".join(parts).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ClockSample:
    """Caller-supplied offline sample; never proof of a real synchronized host clock."""

    monotonic_ns: int
    wall_utc: str

    def wall(self) -> datetime:
        require(type(self.monotonic_ns) is int and self.monotonic_ns >= 0, "MONOTONIC_TYPE")
        require(type(self.wall_utc) is str and len(self.wall_utc) <= 64, "WALL_TYPE")
        try:
            value = datetime.fromisoformat(self.wall_utc.replace("Z", "+00:00"))
            require(value.tzinfo is not None and value.utcoffset() is not None, "WALL_ZONE")
            return value.astimezone(UTC)
        except (ValueError, OverflowError) as exc:
            raise BoundaryError("WALL_FORMAT") from exc


class AbsoluteDeadlineModel:
    """One absolute total/request deadline; phase transitions cannot renew either.

    This checks supplied samples. It does not interrupt a real DNS/TLS/syscall
    block, run a watchdog, sample time or attest host clock synchronization.
    """

    PHASES = ("DNS", "TLS", "HEADERS", "BODY")

    def __init__(self, started: ClockSample) -> None:
        require(type(started) is ClockSample, "SAMPLE_TYPE")
        self._last = started
        started.wall()
        self.total_end_ns = started.monotonic_ns + frozen.TOTAL_DEADLINE_MS * 1_000_000
        self.request_end_ns: int | None = None
        self.next_start_ns = started.monotonic_ns
        self.started_requests = 0
        self._phase = -1
        self.halt_reason: str | None = None

    def _halt(self, reason: str) -> Never:
        self.halt_reason = reason
        raise BoundaryError(reason)

    def _guard(self, ok: bool, code: str) -> None:
        if not ok:
            self._halt(code)

    def _sample(self, sample: ClockSample, cancelled: bool) -> None:
        if self.halt_reason is not None:
            raise BoundaryError("ALREADY_HALTED")
        self._guard(type(sample) is ClockSample and type(cancelled) is bool, "SAMPLE_TYPE")
        try:
            sample.wall()
        except BoundaryError:
            self._halt("INVALID_CLOCK")
        if cancelled:
            self._halt("CANCELLED")
        if sample.monotonic_ns < self._last.monotonic_ns:
            self._halt("MONOTONIC_ROLLBACK")
        if sample.wall() < self._last.wall():
            self._halt("WALL_ROLLBACK")
        self._last = sample
        if sample.monotonic_ns >= self.total_end_ns:
            self._halt("TOTAL_DEADLINE")
        if self.request_end_ns is not None and sample.monotonic_ns >= self.request_end_ns:
            self._halt("REQUEST_DEADLINE")

    def begin(self, sample: ClockSample, *, cancelled: bool = False) -> int:
        self._sample(sample, cancelled)
        self._guard(self.request_end_ns is None, "ONE_ACTIVE_REQUEST")
        self._guard(sample.monotonic_ns >= self.next_start_ns, "START_PACING")
        self._guard(self.started_requests < frozen.MAX_REQUESTS, "REQUEST_CAP")
        self.started_requests += 1
        self.next_start_ns = sample.monotonic_ns + frozen.MIN_START_INTERVAL_MS * 1_000_000
        self.request_end_ns = min(
            self.total_end_ns, sample.monotonic_ns + frozen.REQUEST_TIMEOUT_MS * 1_000_000
        )
        self._phase = -1
        return self.request_end_ns

    def phase(self, name: str, sample: ClockSample, *, cancelled: bool = False) -> int:
        self._sample(sample, cancelled)
        self._guard(type(name) is str, "PHASE_TYPE")
        self._guard(self.request_end_ns is not None, "NO_ACTIVE_REQUEST")
        self._guard(self._phase + 1 < len(self.PHASES), "PHASE_COMPLETE")
        self._guard(name == self.PHASES[self._phase + 1], "PHASE_ORDER")
        self._phase += 1
        return cast(int, self.request_end_ns) - sample.monotonic_ns

    def finish(self, sample: ClockSample, *, cancelled: bool = False) -> None:
        self._sample(sample, cancelled)
        self._guard(self.request_end_ns is not None and self._phase == 3, "BODY_NOT_COMPLETE")
        self.request_end_ns = None


@dataclass(frozen=True, slots=True)
class OfflineFrame:
    status: int | None
    raw_headers: bytes
    raw_body: bytes
    complete: bool = True
    transport_failure: str | None = None


@dataclass(frozen=True, slots=True)
class BoundedFrame:
    status: int | None
    headers: bytes
    body: bytes
    headers_complete: bool
    body_complete: bool
    reason: str
    simulation_only: Literal[True] = True

    def __post_init__(self) -> None:
        require(self.simulation_only is True, "OFFLINE_ONLY")


def bound_frame(frame: OfflineFrame) -> BoundedFrame:
    """Retain raw prefixes first, without following server instructions or URLs."""
    require(type(frame) is OfflineFrame, "BUILTIN_FRAME")
    require(type(frame.raw_headers) is bytes and type(frame.raw_body) is bytes, "RAW_BYTES")
    require(type(frame.complete) is bool, "COMPLETE_FLAG")
    require(frame.status is None or type(frame.status) is int, "STATUS_TYPE")
    require(frame.status is None or 100 <= frame.status <= 599, "STATUS_RANGE")
    require(frame.status is None or frame.transport_failure is None, "AMBIGUOUS_TRANSPORT")
    h_ok = len(frame.raw_headers) <= frozen.MAX_HEADER_BYTES
    b_ok = frame.complete and len(frame.raw_body) <= frozen.MAX_BODY_BYTES
    reason = "HTTP_200_UNVERIFIED"
    if frame.status is None:
        require(
            frame.transport_failure
            in {
                "DNS_FAILURE",
                "TLS_FAILURE",
                "TIMEOUT",
                "CANCELLED",
                "CLOCK_ROLLBACK",
                "PROCESS_INTERRUPTED",
                "REQUEST_DEADLINE",
                "TOTAL_DEADLINE",
            },
            "TRANSPORT_FAILURE_CODE",
        )
        reason = str(frame.transport_failure)
    elif frame.status in (403, 429):
        reason = f"HTTP_{frame.status}"
    elif 300 <= frame.status < 400:
        reason = "REDIRECT_REJECTED"
    elif frame.status != 200:
        reason = f"HTTP_{frame.status}"
    elif not h_ok:
        reason = "HEADER_LIMIT"
    elif not b_ok:
        reason = "BODY_LIMIT_OR_PARTIAL"
    else:
        headers: dict[bytes, bytes] = {}
        for line in frame.raw_headers.split(b"\r\n"):
            if not line:
                continue
            if b"\n" in line or b"\r" in line:
                reason = "MALFORMED_HEADER"
                break
            key, sep, value = line.partition(b":")
            key, value = key.strip().lower(), value.strip()
            if not sep or not re.fullmatch(rb"[a-z0-9-]+", key) or b"\n" in value or b"\r" in value:
                reason = "MALFORMED_HEADER"
                break
            if key in headers:
                reason = "DUPLICATE_HEADER"
                break
            headers[key] = value
        if reason == "HTTP_200_UNVERIFIED":
            if headers.get(b"content-type", b"").split(b";")[0] != b"application/json":
                reason = "CONTENT_TYPE"
            elif b"transfer-encoding" in headers:
                reason = "TRANSFER_ENCODING"
            elif b"content-encoding" in headers and headers[b"content-encoding"] != b"identity":
                reason = "CONTENT_ENCODING"
            elif b"content-length" in headers and (
                not headers[b"content-length"].isdigit()
                or len(headers[b"content-length"]) > 12
                or int(headers[b"content-length"]) != len(frame.raw_body)
            ):
                reason = "CONTENT_LENGTH"
    return BoundedFrame(
        frame.status,
        frame.raw_headers[: frozen.MAX_HEADER_BYTES],
        frame.raw_body[: frozen.MAX_BODY_BYTES],
        h_ok,
        b_ok,
        reason,
    )
