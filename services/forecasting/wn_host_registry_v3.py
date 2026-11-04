"""Create-once generated-probe registry; never a source acquisition registry."""

from __future__ import annotations

import os
import stat
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, cast

from . import wn_october_settlement_recovery as frozen
from . import wn_settlement_journal_v2 as journal
from .wn_host_simulation_v3 import MAX_WIRE, GeneratedScenario, HostProbe, probe_generated
from .wn_settlement_safety_v2 import (
    BoundaryError,
    OfflineFrame,
    RequestPolicy,
    bound_frame,
    require,
)

EXECUTION_NAME = "wn-v3-" + frozen.ROSTER_SHA256


def _contract(cutoff: bytes, request_ms: int, total_ms: int, started_ns: int) -> dict[str, object]:
    policy = RequestPolicy(cutoff)
    return {
        "version": "OFFLINE_GENERATED_HOST_REGISTRY_V3",
        "roster_sha256": frozen.ROSTER_SHA256,
        "cutoff_hex": cutoff.hex(),
        "policy_sha256": policy.commitment(),
        "requests": [asdict(policy.slot(i)) for i in range(85)],
        "request_ms": request_ms,
        "total_ms": total_ms,
        "started_ns": started_ns,
        "simulation_only": True,
        "live_authorized": False,
        "point_score_authorized": False,
        "trading_authority": "NONE",
    }


def _intent(policy: RequestPolicy, slot: int, claim: str) -> bytes:
    request = policy.slot(slot)
    policy.check(slot, request)
    return journal.canonical(
        {"slot": slot, "request": asdict(request), "claim_sha256": claim, "simulation_only": True}
    )


def _receipt(result: HostProbe, intent: bytes) -> bytes:
    metadata = asdict(result)
    del metadata["headers"], metadata["body"]
    metadata.update(
        {
            "header_size": len(result.headers),
            "body_size": len(result.body),
            "header_sha256": journal.digest(result.headers),
            "body_sha256": journal.digest(result.body),
            "intent_sha256": journal.digest(intent),
            "source_custody_attested": False,
        }
    )
    raw = journal.canonical(metadata)
    return len(raw).to_bytes(8, "big") + raw + result.headers + result.body


def _decode(raw: bytes, intent: bytes) -> dict[str, object]:
    require(len(raw) >= 8, "TRUNCATED_FRAME")
    size = int.from_bytes(raw[:8], "big")
    require(0 < size <= 16384 and 8 + size <= len(raw), "METADATA_SIZE")
    metadata = journal._json(raw[8 : 8 + size])
    h, b = metadata.get("header_size"), metadata.get("body_size")
    require(type(h) is int and type(b) is int, "LENGTH_TYPES")
    h, b = cast(int, h), cast(int, b)
    require(0 <= h <= frozen.MAX_HEADER_BYTES and 0 <= b <= frozen.MAX_BODY_BYTES, "RAW_LIMITS")
    require(len(raw) == 8 + size + h + b, "FRAME_LENGTH")
    require(
        metadata.get("header_sha256") == journal.digest(raw[8 + size : 8 + size + h])
        and metadata.get("body_sha256") == journal.digest(raw[8 + size + h :]),
        "RAW_SHA256",
    )
    require(metadata.get("intent_sha256") == journal.digest(intent), "INTENT_SHA256")
    require(
        metadata.get("simulation_only") is True
        and metadata.get("live_authorized") is False
        and metadata.get("point_score_authorized") is False
        and metadata.get("source_custody_attested") is False,
        "NEGATIVE_AUTHORITY",
    )
    require(
        type(metadata.get("reason")) is str and type(metadata.get("reaped")) is bool,
        "RECEIPT_SCHEMA",
    )
    for flag in ("headers_complete", "body_complete", "forced_kill"):
        require(type(metadata.get(flag)) is bool, "RECEIPT_FLAGS")
    peak = metadata.get("max_ipc_buffer")
    require(type(peak) is int and 0 <= peak <= MAX_WIRE + 4, "IPC_BUFFER_BOUND")
    start, stop = metadata.get("started_ns"), metadata.get("stopped_ns")
    require(
        type(start) is int and type(stop) is int and stop >= start,
        "MONOTONIC_ORDER",
    )
    return metadata


class GeneratedRegistry:
    """One fixed execution name per operator-owned physical research parent."""

    def __init__(
        self, parent: int, fd: int, cutoff: bytes, request_ms: int, total_ms: int, started_ns: int
    ) -> None:
        self._lock = threading.RLock()
        self._parent, self._fd = parent, fd
        info = os.fstat(fd)
        self._identity = info.st_dev, info.st_ino
        self._policy = RequestPolicy(cutoff)
        self._cutoff = cutoff
        self._request_ms = request_ms
        self._end = started_ns + total_ms * 1_000_000
        self._next_start = started_ns
        self._states = ["NOT_STARTED"] * 85
        self._next = 0
        self._closed, self._failed = False, False
        self.halt_reason: str | None = None
        self._claim = journal.canonical(_contract(cutoff, request_ms, total_ms, started_ns))
        self._tip = journal.digest(self._claim)

    @classmethod
    def create(
        cls,
        parent_path: Path,
        cutoff: bytes,
        *,
        request_ms: int = 2500,
        total_ms: int = frozen.TOTAL_DEADLINE_MS,
    ) -> GeneratedRegistry:
        require(type(cutoff) is bytes and len(cutoff) <= 1024, "SYNTHETIC_CUTOFF")
        RequestPolicy(cutoff)
        require(
            type(request_ms) is int and 50 <= request_ms <= frozen.REQUEST_TIMEOUT_MS,
            "REQUEST_BUDGET",
        )
        require(
            type(total_ms) is int and 50 <= total_ms <= frozen.TOTAL_DEADLINE_MS, "TOTAL_BUDGET"
        )
        parent = journal._open_dir(parent_path)
        fd: int | None = None
        started = time.monotonic_ns()
        try:
            os.mkdir(EXECUTION_NAME, mode=0o700, dir_fd=parent)
            os.fsync(parent)
            fd = os.open(EXECUTION_NAME, journal.DIR_FLAGS, dir_fd=parent)
            registry = cls(parent, fd, cutoff, request_ms, total_ms, started)
            registry._publish("claim", registry._claim)
            return registry
        except BaseException:
            if fd is not None:
                os.close(fd)
            os.close(parent)
            raise

    def _check(self) -> None:
        require(not self._closed and not self._failed, "WRITER_UNAVAILABLE")
        info = os.stat(EXECUTION_NAME, dir_fd=self._parent, follow_symlinks=False)
        require(
            stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == self._identity,
            "ROOT_REPLACED",
        )

    def _publish(self, stem: str, raw: bytes) -> None:
        try:
            self._check()
            require(len(raw) <= journal.MAX_FILE, "FILE_LIMIT")
            fd = os.open(stem + ".partial", journal.WRITE_FLAGS, 0o600, dir_fd=self._fd)
            try:
                offset = 0
                while offset < len(raw):
                    size = os.write(fd, raw[offset:])
                    require(size > 0, "ZERO_WRITE")
                    offset += size
                os.fchmod(fd, 0o444)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.link(
                stem + ".partial",
                stem + ".sealed",
                src_dir_fd=self._fd,
                dst_dir_fd=self._fd,
                follow_symlinks=False,
            )
            os.fsync(self._fd)
            self._check()
        except BaseException:
            self._failed = True
            raise

    def stop(self, reason: str = "OFFLINE_STOP") -> None:
        with self._lock:
            self._stop_locked(reason)

    def _stop_locked(self, reason: str) -> None:
        self._check()
        require(self.halt_reason is None, "GLOBAL_STOP")
        try:
            self._publish(
                "ledger",
                journal.canonical(
                    {
                        "reason": reason,
                        "tip_sha256": self._tip,
                        "slots": self._states,
                        "rows": journal.negative_rows(reason),
                        "simulation_only": True,
                        "live_authorized": False,
                        "point_score_authorized": False,
                    }
                ),
            )
            self.halt_reason = reason
        except BaseException:
            self._failed = True
            raise

    def run_slot(
        self,
        slot: int,
        recipe: GeneratedScenario,
        *,
        request: frozen.PlannedGet | None = None,
        cancelled: threading.Event | None = None,
    ) -> HostProbe | None:
        with self._lock:
            return self._run_slot_locked(slot, recipe, request=request, cancelled=cancelled)

    def _run_slot_locked(
        self,
        slot: int,
        recipe: GeneratedScenario,
        *,
        request: frozen.PlannedGet | None = None,
        cancelled: threading.Event | None = None,
    ) -> HostProbe | None:
        self._check()
        require(self.halt_reason is None, "GLOBAL_STOP")
        try:
            require(type(slot) is int and slot == self._next and 0 <= slot < 85, "SLOT_OWNERSHIP")
            self._policy.check(slot, request if request is not None else self._policy.slot(slot))
            require(type(recipe) is GeneratedScenario, "GENERATED_ONLY")
        except (BoundaryError, frozen.RecoveryError):
            self.stop("INVALID_REQUEST_OR_SLOT")
            raise
        now = time.monotonic_ns()
        if now >= self._end:
            self.stop("TOTAL_DEADLINE")
            return None
        if now < self._next_start:
            self.stop("START_PACING")
            return None
        try:
            intent = _intent(self._policy, slot, journal.digest(self._claim))
            self._publish(f"s{slot:03d}.intent", intent)
            self._next_start = time.monotonic_ns() + frozen.MIN_START_INTERVAL_MS * 1_000_000
            result = probe_generated(
                recipe, request_ms=self._request_ms, total_end_ns=self._end, cancelled=cancelled
            )
            reason = result.reason
            if reason == "HTTP_200_UNVERIFIED":
                bounded = bound_frame(
                    OfflineFrame(result.status, result.headers, result.body, result.body_complete)
                )
                reason = bounded.reason
                if slot == 0 and result.body != self._cutoff:
                    reason = "CONFLICTING_CUTOFF"
            raw = _receipt(result, intent)
            self._publish(f"s{slot:03d}.receipt", raw)
            commit = journal.canonical(
                {
                    "slot": slot,
                    "claim_sha256": journal.digest(self._claim),
                    "previous_sha256": self._tip,
                    "receipt_sha256": journal.digest(raw),
                    "intent_sha256": journal.digest(intent),
                    "reason": reason,
                    "simulation_only": True,
                }
            )
            self._publish(f"s{slot:03d}.commit", commit)
            self._tip = journal.digest(commit)
            self._states[slot] = reason
            self._next += 1
            if reason != "HTTP_200_UNVERIFIED" or self._next == 85:
                self.stop(reason if reason != "HTTP_200_UNVERIFIED" else "COMPLETE_OFFLINE_ONLY")
            return result
        except BaseException:
            self._failed = True
            raise

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                os.close(self._fd)
                os.close(self._parent)

    def __enter__(self) -> GeneratedRegistry:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


@dataclass(frozen=True, slots=True)
class RegistryInspection:
    integrity: str
    reason: str
    committed_slots: int
    rows: tuple[dict[str, object], ...]
    simulation_only: Literal[True] = True
    live_authorized: Literal[False] = False
    point_score_authorized: Literal[False] = False
    resume_allowed: Literal[False] = False

    def __post_init__(self) -> None:
        require(
            self.simulation_only is True
            and self.live_authorized is False
            and self.point_score_authorized is False
            and self.resume_allowed is False,
            "NEGATIVE_ONLY",
        )


def inspect_registry(parent_path: Path) -> RegistryInspection:
    """Existing registry bytes are read-only; unavailable evidence stays negative."""
    fd: int | None = None
    count = 0
    reason = "INCOMPLETE_OR_CORRUPT"
    try:
        fd = journal._open_dir(parent_path / EXECUTION_NAME)
        before = os.fstat(fd)
        names: set[str] = set()
        with os.scandir(fd) as entries:
            for entry in entries:
                require(len(names) < 514, "FILE_COUNT")
                names.add(entry.name)
        allowed = {
            f"{stem}.{suffix}"
            for stem in [
                "claim",
                "ledger",
                *[
                    f"s{i:03d}.{kind}"
                    for i in range(85)
                    for kind in ("intent", "receipt", "commit")
                ],
            ]
            for suffix in ("partial", "sealed")
        }
        require(len(names) <= 514 and names <= allowed, "FILE_INVENTORY")
        for name in names:
            raw = journal._read(fd, name)
            if name.endswith(".partial"):
                sealed = name.removesuffix(".partial") + ".sealed"
                require(sealed in names and raw == journal._read(fd, sealed), "ORPHAN_PARTIAL")
        claim_raw = journal._read(fd, "claim.sealed")
        claim = journal._json(claim_raw)
        cutoff = bytes.fromhex(cast(str, claim["cutoff_hex"]))
        request_ms, total_ms, started = claim["request_ms"], claim["total_ms"], claim["started_ns"]
        require(
            type(request_ms) is int and type(total_ms) is int and type(started) is int,
            "CLAIM_TYPES",
        )
        request_ms, total_ms, started = (
            cast(int, request_ms),
            cast(int, total_ms),
            cast(int, started),
        )
        require(
            50 <= request_ms <= frozen.REQUEST_TIMEOUT_MS
            and 50 <= total_ms <= frozen.TOTAL_DEADLINE_MS
            and started >= 0
            and len(cutoff) <= 1024,
            "CLAIM_LIMITS",
        )
        require(
            claim_raw == journal.canonical(_contract(cutoff, request_ms, total_ms, started)),
            "CLAIM_IDENTITY",
        )
        policy, tip = RequestPolicy(cutoff), journal.digest(claim_raw)
        states = ["NOT_STARTED"] * 85
        halted = False
        for slot in range(85):
            stem = f"s{slot:03d}"
            present = [f"{stem}.{kind}.sealed" in names for kind in ("intent", "receipt", "commit")]
            if not any(present):
                continue
            require(all(present) and slot == count and not halted, "SLOT_CONTINUITY")
            intent = journal._read(fd, stem + ".intent.sealed")
            raw = journal._read(fd, stem + ".receipt.sealed")
            commit = journal._read(fd, stem + ".commit.sealed")
            require(intent == _intent(policy, slot, journal.digest(claim_raw)), "INTENT_IDENTITY")
            metadata = _decode(raw, intent)
            item = journal._json(commit)
            state = cast(str, item["reason"])
            require(
                commit
                == journal.canonical(
                    {
                        "slot": slot,
                        "claim_sha256": journal.digest(claim_raw),
                        "previous_sha256": tip,
                        "receipt_sha256": journal.digest(raw),
                        "intent_sha256": journal.digest(intent),
                        "reason": state,
                        "simulation_only": True,
                    }
                ),
                "COMMIT_INTEGRITY",
            )
            require(
                state != "HTTP_200_UNVERIFIED"
                or (
                    metadata["reason"] == state
                    and metadata["reaped"] is True
                    and metadata["body_complete"] is True
                    and metadata["headers_complete"] is True
                    and metadata["status"] == 200
                ),
                "POSITIVE_TRANSPORT_CONSISTENCY",
            )
            states[slot] = state
            halted = state != "HTTP_200_UNVERIFIED"
            tip, count = journal.digest(commit), count + 1
        if "ledger.sealed" in names:
            ledger_raw = journal._read(fd, "ledger.sealed")
            ledger = journal._json(ledger_raw)
            reason = cast(str, ledger["reason"])
            require(type(reason) is str, "LEDGER_REASON")
            require(
                ledger_raw
                == journal.canonical(
                    {
                        "reason": reason,
                        "tip_sha256": tip,
                        "slots": states,
                        "rows": journal.negative_rows(reason),
                        "simulation_only": True,
                        "live_authorized": False,
                        "point_score_authorized": False,
                    }
                ),
                "LEDGER_INTEGRITY",
            )
        else:
            reason = "INCOMPLETE_NO_FINAL_LEDGER"
        after = os.fstat(fd)
        require(
            (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns)
            == (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns),
            "REGISTRY_CHANGED_DURING_READ",
        )
        return RegistryInspection(
            "CURRENT_BYTES_VERIFIED_ONLY", reason, count, journal.negative_rows(reason)
        )
    except (OSError, ValueError, KeyError, TypeError, frozen.RecoveryError):
        return RegistryInspection(
            "UNVERIFIED_INCOMPLETE_OR_CORRUPT",
            reason,
            count,
            journal.negative_rows("PERSISTENCE_UNCERTAIN"),
        )
    finally:
        if fd is not None:
            os.close(fd)
