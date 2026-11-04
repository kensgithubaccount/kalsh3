"""Create-once local offline journals. No networking, acquisition resume or issuer.

.partial files are never deleted or repaired. A .sealed hardlink is published
only after file fsync; slot commit markers follow durable intent/raw writes.
Read-only recovery checks integrity but ALWAYS returns negative authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from . import wn_october_settlement_recovery as frozen
from .wn_settlement_safety_v2 import (
    BoundaryError,
    BoundedFrame,
    OfflineFrame,
    RequestPolicy,
    bound_frame,
    require,
)

DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
MAX_FILE = 131_072
MAX_FILES = 520


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode() + b"\n"
    )


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        require(key not in result, "DUPLICATE_KEY")
        result[key] = value
    return result


def _constant(_: str) -> object:
    raise BoundaryError("NONFINITE_JSON")


def _json(raw: bytes) -> dict[str, object]:
    try:
        value: object = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise BoundaryError("BAD_JOURNAL_JSON") from exc
    require(type(value) is dict, "JOURNAL_OBJECT")
    return cast(dict[str, object], value)


def _open_dir(path: Path) -> int:
    require(path.is_absolute() and ".." not in path.parts, "ABSOLUTE_PHYSICAL_PATH")
    fd = os.open("/", DIR_FLAGS)
    try:
        for part in path.parts[1:]:
            child = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read(fd: int, name: str) -> bytes:
    handle = os.open(name, READ_FLAGS, dir_fd=fd)
    try:
        before = os.fstat(handle)
        require(stat.S_ISREG(before.st_mode) and before.st_size <= MAX_FILE, "REGULAR_BOUNDED_FILE")
        chunks = []
        count = 0
        while True:
            chunk = os.read(handle, min(65536, MAX_FILE + 1 - count))
            if not chunk:
                break
            count += len(chunk)
            require(count <= MAX_FILE, "FILE_LIMIT")
            chunks.append(chunk)
        after = os.fstat(handle)

        def signature(x: os.stat_result) -> tuple[int, int, int, int, int]:
            return x.st_dev, x.st_ino, x.st_size, x.st_mtime_ns, x.st_ctime_ns

        require(signature(before) == signature(after) and count == after.st_size, "FILE_CHANGED")
        return b"".join(chunks)
    finally:
        os.close(handle)


def _claim() -> bytes:
    return canonical(
        {
            "kind": "WN_SETTLEMENT_OFFLINE_EXECUTION_CLAIM_V2",
            "roster_sha256": frozen.ROSTER_SHA256,
            "tickers": [c.ticker for f in frozen.frozen_families() for c in f.contracts],
            "max_request_slots": 85,
            "simulation_only": True,
            "live_authorized": False,
            "source_custody_attested": False,
            "point_score_authorized": False,
            "trading_authority": "NONE",
        }
    )


def _intent(policy: RequestPolicy, slot: int, claim_sha: str) -> bytes:
    request = policy.slot(slot)
    policy.check(slot, request)
    return canonical(
        {
            "slot": slot,
            "method": request.method,
            "origin": request.origin,
            "path": request.path,
            "headers": request.headers,
            "claim_sha256": claim_sha,
            "simulation_only": True,
        }
    )


def _frame_bytes(slot: int, frame: BoundedFrame, intent_sha: str) -> bytes:
    metadata = canonical(
        {
            "slot": slot,
            "status": frame.status,
            "reason": frame.reason,
            "header_bytes": len(frame.headers),
            "body_bytes": len(frame.body),
            "header_sha256": digest(frame.headers),
            "body_sha256": digest(frame.body),
            "headers_complete": frame.headers_complete,
            "body_complete": frame.body_complete,
            "intent_sha256": intent_sha,
            "simulation_only": True,
            "source_custody_attested": False,
            "point_score_authorized": False,
        }
    )
    return len(metadata).to_bytes(8, "big") + metadata + frame.headers + frame.body


def _decode_frame(raw: bytes, slot: int, intent_sha: str) -> tuple[dict[str, object], bytes]:
    require(len(raw) >= 8, "TRUNCATED_FRAME")
    size = int.from_bytes(raw[:8], "big")
    require(0 < size <= 32768 and 8 + size <= len(raw), "FRAME_HEADER_SIZE")
    metadata = _json(raw[8 : 8 + size])
    h, b = metadata.get("header_bytes"), metadata.get("body_bytes")
    require(type(h) is int and type(b) is int, "FRAME_LENGTH_TYPES")
    h, b = cast(int, h), cast(int, b)
    require(0 <= h <= frozen.MAX_HEADER_BYTES and 0 <= b <= frozen.MAX_BODY_BYTES, "FRAME_CAPS")
    require(len(raw) == 8 + size + h + b, "FRAME_EXACT_LENGTH")
    headers, body = raw[8 + size : 8 + size + h], raw[8 + size + h :]
    require(
        metadata.get("slot") == slot and metadata.get("intent_sha256") == intent_sha,
        "FRAME_IDENTITY",
    )
    require(
        metadata.get("header_sha256") == digest(headers)
        and metadata.get("body_sha256") == digest(body),
        "RAW_DIGEST",
    )
    require(
        metadata.get("simulation_only") is True
        and metadata.get("source_custody_attested") is False
        and metadata.get("point_score_authorized") is False,
        "NEGATIVE_AUTHORITY",
    )
    require(
        type(metadata.get("headers_complete")) is bool
        and type(metadata.get("body_complete")) is bool,
        "FRAME_FLAGS",
    )
    require(type(metadata.get("reason")) is str, "FRAME_REASON")
    return metadata, body


def _routing(body: bytes, cutoff_frame_sha: str) -> bytes:
    policy = RequestPolicy(body)
    return canonical(
        {
            "cutoff_frame_sha256": cutoff_frame_sha,
            "policy_sha256": policy.commitment(),
            "simulation_only": True,
        }
    )


def negative_rows(reason: str) -> tuple[dict[str, object], ...]:
    with frozen.ROSTER.open("rb") as stream:
        raw = stream.read(1_048_577)
    require(digest(raw) == frozen.ROSTER_SHA256, "FROZEN_ROSTER")
    source = _json(raw)["full_city_day_roster"]
    require(type(source) is list, "FULL_ROSTER")
    result = []
    for value in cast(list[object], source):
        require(type(value) is dict, "CITY_DAY")
        row = cast(dict[str, object], value)
        result.append(
            {
                **row,
                "state": "FAILED_CAPTURE"
                if row["capture_state"] == "FAILED"
                else "UNVERIFIED_OFFLINE_ONLY",
                "reason": reason,
                "source_custody_attested": False,
                "point_score_authorized": False,
                "live_authorized": False,
            }
        )
    require(len(result) == 60 and len({r["target_date"] for r in result}) == 12, "FULL_DENOMINATOR")
    return tuple(result)


class OfflineJournal:
    """Local create-once writer. Existing roots can ONLY be inspected, never resumed."""

    def __init__(self, parent_fd: int, root_fd: int, name: str) -> None:
        self._parent, self._fd, self._name = parent_fd, root_fd, name
        info = os.fstat(root_fd)
        self._identity = (info.st_dev, info.st_ino)
        self._failed = False
        self._closed = False
        self._active: int | None = None
        self._next = 0
        self._claim_sha = digest(_claim())
        self._tip = self._claim_sha
        self._policy = RequestPolicy()
        self._states = ["NOT_REQUESTED"] * frozen.MAX_REQUESTS
        self.halt_reason: str | None = None

    @classmethod
    def create(cls, root: Path) -> OfflineJournal:
        require(bool(re.fullmatch(r"[A-Za-z0-9_-]{1,80}", root.name)), "ROOT_NAME")
        parent = _open_dir(root.parent)
        fd: int | None = None
        journal: OfflineJournal | None = None
        try:
            os.mkdir(root.name, mode=0o700, dir_fd=parent)
            os.fsync(parent)
            fd = os.open(root.name, DIR_FLAGS, dir_fd=parent)
            journal = cls(parent, fd, root.name)
            journal._publish("claim", _claim())
            return journal
        except BaseException:
            if fd is not None:
                os.close(fd)
            os.close(parent)
            raise

    def _check(self) -> None:
        require(not self._closed and not self._failed, "WRITER_UNAVAILABLE")
        info = os.stat(self._name, dir_fd=self._parent, follow_symlinks=False)
        require(
            stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == self._identity,
            "ROOT_REPLACED",
        )

    def _publish(self, stem: str, raw: bytes) -> None:
        try:
            self._check()
            require(len(raw) <= MAX_FILE, "PUBLICATION_SIZE")
            name = stem + ".partial"
            handle = os.open(name, WRITE_FLAGS, 0o600, dir_fd=self._fd)
            try:
                position = 0
                while position < len(raw):
                    written = os.write(handle, raw[position:])
                    require(written > 0, "ZERO_WRITE")
                    position += written
                os.fchmod(handle, 0o444)
                os.fsync(handle)
            finally:
                os.close(handle)
            os.link(
                name,
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

    def begin_slot(self, slot: int) -> frozen.PlannedGet:
        self._check()
        require(self.halt_reason is None, "HALTED")
        require(
            type(slot) is int and slot == self._next and slot < frozen.MAX_REQUESTS,
            "SLOT_OWNERSHIP",
        )
        require(self._active is None, "ONE_ACTIVE_SLOT")
        intent = _intent(self._policy, slot, self._claim_sha)
        self._publish(f"s{slot:03d}.intent", intent)
        self._active = slot
        self._states[slot] = "INTENT_DURABLE_ONLY"
        return self._policy.slot(slot)

    def record(self, frame: OfflineFrame) -> None:
        self._check()
        require(self._active is not None and self.halt_reason is None, "NO_ACTIVE_SLOT")
        slot = cast(int, self._active)
        try:
            bounded = bound_frame(frame)
            reason = bounded.reason
            if slot == 0 and reason == "HTTP_200_UNVERIFIED":
                try:
                    RequestPolicy(bounded.body)
                except (BoundaryError, frozen.RecoveryError, ArithmeticError):
                    reason = "INVALID_CUTOFF"
                    bounded = BoundedFrame(
                        bounded.status,
                        bounded.headers,
                        bounded.body,
                        bounded.headers_complete,
                        bounded.body_complete,
                        reason,
                    )
            intent = _read(self._fd, f"s{slot:03d}.intent.sealed")
            require(intent == _intent(self._policy, slot, self._claim_sha), "INTENT_CHANGED")
            raw = _frame_bytes(slot, bounded, digest(intent))
            self._publish(f"s{slot:03d}.response", raw)
            commit = canonical(
                {
                    "slot": slot,
                    "claim_sha256": self._claim_sha,
                    "previous_commit_sha256": self._tip,
                    "intent_sha256": digest(intent),
                    "response_sha256": digest(raw),
                    "simulation_only": True,
                }
            )
            self._publish(f"s{slot:03d}.commit", commit)
            self._tip = digest(commit)
            self._states[slot] = reason
            self._active = None
            self._next += 1
            if slot == 0 and reason == "HTTP_200_UNVERIFIED":
                self._publish("routing", _routing(bounded.body, digest(raw)))
                self._policy = RequestPolicy(bounded.body)
            elif reason != "HTTP_200_UNVERIFIED":
                self.halt_reason = reason
        except BaseException:
            self._failed = True
            raise

    def finalize_negative(self, reason: str = "OFFLINE_STOP") -> tuple[dict[str, object], ...]:
        self._check()
        require(self._active is None, "ACTIVE_SLOT_INTERRUPTED")
        require(
            reason
            in {
                "OFFLINE_STOP",
                "CANCELLED",
                "REQUEST_DEADLINE",
                "TOTAL_DEADLINE",
                "CLOCK_ROLLBACK",
            },
            "FINAL_REASON",
        )
        reason = self.halt_reason or reason
        rows = negative_rows(reason)
        self._publish(
            "ledger",
            canonical(
                {
                    "kind": "FULL_ROSTER_NEGATIVE_ONLY",
                    "reason": reason,
                    "tip_sha256": self._tip,
                    "slots": self._states,
                    "rows": rows,
                    "simulation_only": True,
                }
            ),
        )
        self.halt_reason = reason
        return rows

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            os.close(self._fd)
            os.close(self._parent)

    def __enter__(self) -> OfflineJournal:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


@dataclass(frozen=True, slots=True)
class RecoveryInspection:
    integrity: str
    reason: str
    committed_slots: int
    rows: tuple[dict[str, object], ...]
    acquisition_resume_allowed: Literal[False] = False
    source_custody_attested: Literal[False] = False
    point_score_authorized: Literal[False] = False
    live_authorized: Literal[False] = False

    def __post_init__(self) -> None:
        require(
            self.acquisition_resume_allowed is False
            and self.source_custody_attested is False
            and self.point_score_authorized is False
            and self.live_authorized is False,
            "NEGATIVE_AUTHORITY_ONLY",
        )


def inspect_recovery(root: Path) -> RecoveryInspection:
    """Read-only recovery: inspect existing bytes; never repair/delete/resume them."""
    fd: int | None = None
    committed = 0
    reason = "INCOMPLETE_OR_UNAVAILABLE"
    try:
        fd = _open_dir(root)
        before = os.fstat(fd)
        names = os.listdir(fd)
        require(len(names) <= MAX_FILES, "FILE_COUNT")
        allowed = {
            f"{stem}.{suffix}"
            for stem in [
                "claim",
                "routing",
                "ledger",
                *[f"s{i:03d}.{k}" for i in range(85) for k in ("intent", "response", "commit")],
            ]
            for suffix in ("partial", "sealed")
        }
        require(set(names) <= allowed, "UNEXPECTED_FILE")
        for name in names:
            raw = _read(fd, name)
            if name.endswith(".partial"):
                sealed = name.removesuffix(".partial") + ".sealed"
                require(sealed in names and _read(fd, sealed) == raw, "ORPHAN_PARTIAL")
        claim = _read(fd, "claim.sealed")
        require(claim == _claim(), "CLAIM_CHANGED")
        claim_sha = digest(claim)
        tip = claim_sha
        policy = RequestPolicy()
        states = ["NOT_REQUESTED"] * 85
        interrupted = False
        halted = False
        routing_seen = False
        halt_state: str | None = None
        for slot in range(85):
            stem = f"s{slot:03d}"
            present = [
                f"{stem}.{kind}.sealed" in names for kind in ("intent", "response", "commit")
            ]
            if not any(present):
                continue
            require(not interrupted and not halted and slot == committed, "NONSEQUENTIAL_SLOT")
            if present != [True, True, True]:
                interrupted = True
                states[slot] = "INTERRUPTED_UNSEALED"
                continue
            intent = _read(fd, stem + ".intent.sealed")
            raw = _read(fd, stem + ".response.sealed")
            commit = _read(fd, stem + ".commit.sealed")
            require(intent == _intent(policy, slot, claim_sha), "INTENT_IDENTITY")
            expected = canonical(
                {
                    "slot": slot,
                    "claim_sha256": claim_sha,
                    "previous_commit_sha256": tip,
                    "intent_sha256": digest(intent),
                    "response_sha256": digest(raw),
                    "simulation_only": True,
                }
            )
            require(commit == expected, "COMMIT_INTEGRITY")
            metadata, body = _decode_frame(raw, slot, digest(intent))
            state = cast(str, metadata["reason"])
            states[slot] = state
            if slot == 0 and state == "HTTP_200_UNVERIFIED":
                require(
                    metadata["status"] == 200
                    and metadata["body_complete"] is True
                    and metadata["headers_complete"] is True,
                    "CUTOFF_ENVELOPE",
                )
                require(
                    _read(fd, "routing.sealed") == _routing(body, digest(raw)), "ROUTING_INTEGRITY"
                )
                policy = RequestPolicy(body)
                routing_seen = True
            elif state != "HTTP_200_UNVERIFIED":
                halted = True
                halt_state = state
            tip = digest(commit)
            committed += 1
        require(not interrupted, "INTERRUPTED_SLOT")
        require(("routing.sealed" in names) == routing_seen, "ORPHAN_ROUTING")
        if "ledger.sealed" in names:
            ledger_raw = _read(fd, "ledger.sealed")
            ledger = _json(ledger_raw)
            reason = str(ledger.get("reason"))
            require(
                reason == halt_state
                if halt_state is not None
                else reason
                in {
                    "OFFLINE_STOP",
                    "CANCELLED",
                    "REQUEST_DEADLINE",
                    "TOTAL_DEADLINE",
                    "CLOCK_ROLLBACK",
                },
                "LEDGER_REASON",
            )
            require(
                ledger_raw
                == canonical(
                    {
                        "kind": "FULL_ROSTER_NEGATIVE_ONLY",
                        "reason": reason,
                        "tip_sha256": tip,
                        "slots": states,
                        "rows": negative_rows(reason),
                        "simulation_only": True,
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
            "DIRECTORY_CHANGED",
        )
        return RecoveryInspection(
            "CURRENT_BYTES_VERIFIED_ONLY", reason, committed, negative_rows(reason)
        )
    except (OSError, BoundaryError, frozen.RecoveryError, ValueError, KeyError, TypeError):
        return RecoveryInspection(
            "UNVERIFIED_INCOMPLETE_OR_CORRUPT",
            reason,
            committed,
            negative_rows("PERSISTENCE_UNCERTAIN"),
        )
    finally:
        if fd is not None:
            os.close(fd)
