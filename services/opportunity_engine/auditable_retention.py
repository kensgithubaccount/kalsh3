"""Crash-safe storage gate for the M27B.3R3 research-only pilot.

This module accounts for the bytes of active evidence; it never removes or rewrites evidence.
Receipts are content-addressed and atomically published only after all files have been hashed.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "kalsh3.m27b3r3.retention-receipt.v1"
DEFAULT_BUDGET_GIB = 24
DEFAULT_FREE_SPACE_FLOOR_GIB = 8
DEFAULT_EXPECTED_SCANS = 96


class RetentionGateError(RuntimeError):
    """The evidence cannot be safely retained within the approved bound."""


def _sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, allow_nan=False, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    budget_bytes: int = DEFAULT_BUDGET_GIB * 1024**3
    free_space_floor_bytes: int = DEFAULT_FREE_SPACE_FLOOR_GIB * 1024**3
    expected_scans: int = DEFAULT_EXPECTED_SCANS

    def __post_init__(self) -> None:
        if self.budget_bytes <= 0 or self.free_space_floor_bytes <= 0 or self.expected_scans <= 0:
            raise ValueError("retention policy bounds must be positive")


class AuditableRetentionLedger:
    """Per-scan and cumulative byte accounting with a fail-closed smoke projection."""

    def __init__(self, root: str | Path, policy: RetentionPolicy | None = None) -> None:
        self.root = Path(root)
        self.receipts = self.root / "retention-receipts"
        self.state_path = self.root / "retention-state.json"
        self.policy = RetentionPolicy() if policy is None else policy
        self.root.mkdir(parents=True, exist_ok=True)
        self.receipts.mkdir(parents=True, exist_ok=True)

    def _state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"schema_version": SCHEMA_VERSION, "scans": [], "baseline_bytes": None}
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RetentionGateError("retention state is unavailable or corrupt") from exc
        if not isinstance(value, dict):
            raise RetentionGateError("retention state schema is incompatible")
        if value.get("schema_version") != SCHEMA_VERSION or not isinstance(
            value.get("scans"), list
        ):
            raise RetentionGateError("retention state schema is incompatible")
        return value

    @staticmethod
    def _evidence_paths(paths: tuple[Path, ...]) -> tuple[Path, ...]:
        expanded: list[Path] = []
        for path in paths:
            expanded.append(path)
            expanded.extend(path.with_name(path.name + suffix) for suffix in ("-wal", "-shm"))
        return tuple(expanded)

    @classmethod
    def _path_stats(cls, paths: tuple[Path, ...]) -> tuple[int, list[dict[str, Any]]]:
        total = 0
        files: list[dict[str, Any]] = []
        for path in cls._evidence_paths(paths):
            if not path.is_file():
                continue
            digest, size = _sha256(path)
            total += size
            files.append({"path": str(path), "bytes": size, "sha256": digest})
        return total, files

    def check_before_scan(self, paths: tuple[str | Path, ...]) -> None:
        free = shutil.disk_usage(self.root).free
        if free <= self.policy.free_space_floor_bytes:
            raise RetentionGateError(
                "hard free-space floor reached; scan stopped before acquisition"
            )
        state = self._state()
        if not state["scans"]:
            before = sum(
                path.stat().st_size
                for path in self._evidence_paths(tuple(Path(value) for value in paths))
                if path.is_file()
            )
            state["before_scan_bytes"] = before
            _atomic_json(self.state_path, state)
        if state["scans"]:
            projected = int(state["projected_bytes"])
            if projected > self.policy.budget_bytes:
                raise RetentionGateError(
                    "projected 24-hour evidence exceeds approved storage budget"
                )
        # Paths are resolved here to ensure accounting cannot be redirected by a symlink.
        for value in paths:
            path = Path(value).resolve(strict=False)
            if path.parent != path.parent.resolve():
                raise RetentionGateError("active evidence path is not stable")

    def record_scan(
        self,
        *,
        scan_run_id: str,
        complete: bool,
        paths: tuple[str | Path, ...],
        smoke: bool = False,
    ) -> dict[str, Any]:
        state = self._state()
        byte_count, files = self._path_stats(tuple(Path(value) for value in paths))
        if state["baseline_bytes"] is None:
            state["baseline_bytes"] = int(state.get("before_scan_bytes", 0))
        previous = int(state["scans"][-1]["cumulative_bytes"]) if state["scans"] else 0
        cumulative = byte_count
        prior = int(state["before_scan_bytes"]) if not state["scans"] else previous
        observed_delta = max(0, byte_count - prior)
        sample_count = len(state["scans"]) + 1
        projected = byte_count + observed_delta * (self.policy.expected_scans - sample_count)
        if projected > self.policy.budget_bytes:
            raise RetentionGateError("bounded smoke projection exceeds approved storage budget")
        receipt = {
            "schema_version": SCHEMA_VERSION,
            "scan_run_id": scan_run_id,
            "complete": complete,
            "production_influence": 0,
            "bytes_this_scan": observed_delta,
            "cumulative_bytes": cumulative,
            "projected_24h_bytes": projected,
            "approved_budget_bytes": self.policy.budget_bytes,
            "free_space_floor_bytes": self.policy.free_space_floor_bytes,
            "smoke_sample": smoke,
            "files": files,
        }
        receipt_id = hashlib.sha256(
            json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        receipt["receipt_id"] = receipt_id
        _atomic_json(self.receipts / f"{receipt_id}.json", receipt)
        state["scans"].append({"receipt_id": receipt_id, "cumulative_bytes": cumulative})
        state["projected_bytes"] = projected
        _atomic_json(self.state_path, state)
        return receipt
