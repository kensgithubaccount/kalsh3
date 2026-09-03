"""Fail-closed crash-safe storage gate for the M27B.3 research-only pilot.

This module accounts for the bytes of active evidence; it never removes or rewrites evidence.
Receipts are content-addressed and atomically published only after all files have been hashed.

M27B.3R4 repairs every confirmed M27B.3R3 vulnerability:

* every configured primary evidence file must exist and be a regular file at preflight and at
  receipt creation -- a missing primary raises :class:`RetentionGateError`, it is never skipped;
* every reopen and every scan reloads and fully validates the receipt chain referenced by state,
  reconstructing file totals, deltas, high-water projections, and the top-level state summary;
* path safety opens each parent and leaf by descriptor-relative ``O_NOFOLLOW`` traversal, hashes
  through the held file descriptor, and re-verifies ancestry, identity, and mutation metadata;
* the smoke projection uses a persisted growth high-water mark -- ``max`` over every observed
  nonnegative scan delta -- so a later small scan can never erase evidence of an earlier large one;
* free-space reservation blocks a scan when free space *minus the known per-scan growth trend*
  would cross the hard floor, not only when free space alone is already below it;
* an flock-based exclusive single-writer lease spans preflight/reservation through
  ``record_scan``/``abort_scan`` so a second concurrent process fails closed instead of mutating
  evidence, and duplicate ``scan_run_id`` values are handled exactly-once (idempotent replay of an
  identical scan, or a hard rejection of a mismatched one -- never a second append);
* every persisted numeric field is validated as an exact nonnegative (or positive) ``int`` -- never
  a ``bool``, ``float`` or numeric string -- and policy identity (budget/floor/expected-scans) is
  pinned at first use and rejected on any later mismatch without an explicit reviewed migration.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

SCHEMA_VERSION = "kalsh3.m27b3r4_1.retention-receipt.v1"
STATE_SCHEMA_VERSION = "kalsh3.m27b3r4_1.retention-state.v1"
DEFAULT_BUDGET_GIB = 24
DEFAULT_FREE_SPACE_FLOOR_GIB = 8
DEFAULT_EXPECTED_SCANS = 96

_CHUNK_BYTES = 1024 * 1024
_SIDECAR_SUFFIXES: Final[tuple[str, ...]] = ("-wal", "-shm")

_RECEIPT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "scan_run_id",
        "complete",
        "production_influence",
        "bytes_this_scan",
        "cumulative_bytes",
        "projected_24h_bytes",
        "approved_budget_bytes",
        "free_space_floor_bytes",
        "expected_scans",
        "growth_high_water_bytes",
        "sample_count",
        "smoke_sample",
        "files",
        "receipt_id",
    }
)
_RECEIPT_FILE_FIELDS: Final[frozenset[str]] = frozenset({"path", "bytes", "sha256", "role"})
_STATE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "evidence_paths",
        "policy",
        "before_scan_bytes",
        "growth_high_water_bytes",
        "projected_bytes",
        "scans",
    }
)
_STATE_SCAN_FIELDS: Final[frozenset[str]] = frozenset(
    {"scan_run_id", "receipt_id", "cumulative_bytes", "sample_count"}
)
_POLICY_FIELDS: Final[frozenset[str]] = frozenset(
    {"budget_bytes", "free_space_floor_bytes", "expected_scans"}
)


class RetentionGateError(RuntimeError):
    """The evidence cannot be safely retained within the approved bound."""


def _after_first_chunk_hook(path: Path) -> None:  # pragma: no cover - test seam
    """No-op hook invoked after the first chunk of a hash read.

    Tests monkeypatch this to simulate a concurrent path swap while a file is being hashed
    through an already-open file descriptor.
    """


def _after_parent_directory_open_hook(path: Path) -> None:  # pragma: no cover - test seam
    """No-op hook after secure parent traversal and before the leaf open.

    Tests replace the pathname's parent at this boundary.  The final open must continue through
    the already-open directory descriptor and the post-read ancestry check must reject the swap.
    """


def _require_nonneg_int(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise RetentionGateError(f"{name} must be a nonnegative integer")
    return value


def _require_positive_int(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise RetentionGateError(f"{name} must be a positive integer")
    return value


def _require_bool(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise RetentionGateError(f"{name} must be a boolean")
    return value


def _require_str(value: object, name: str, *, allow_empty: bool = False) -> str:
    if type(value) is not str or (not allow_empty and not value):
        raise RetentionGateError(f"{name} must be a non-empty string")
    return value


def _require_hex_digest(value: object, name: str) -> str:
    text = _require_str(value, name)
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise RetentionGateError(f"{name} must be a 64-character lowercase sha256 hex digest")
    return text


def _require_exact_keys(payload: object, expected: frozenset[str], name: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RetentionGateError(f"{name} must be a JSON object")
    keys = set(payload.keys())
    if keys != expected:
        raise RetentionGateError(f"{name} has an unexpected or incomplete field set")
    return payload


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, allow_nan=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _canonical_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


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


def _absolute_path(path: Path) -> Path:
    """Return a lexical absolute path without following any filesystem symlink."""
    return Path(os.path.abspath(os.fspath(path)))


@dataclass(slots=True)
class _SecureEvidenceHandle:
    """A leaf and its parent opened without pathname-following authority."""

    path: Path
    fd: int
    parent_fd: int
    ancestor_identities: tuple[tuple[str, int, int], ...]

    def close(self) -> None:
        os.close(self.fd)
        os.close(self.parent_fd)


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _open_parent_chain(path: Path) -> tuple[int, tuple[tuple[str, int, int], ...]]:
    """Open every parent component relative to a held descriptor.

    A pathname-level ``lstat`` followed by ``open`` has a race: an already-checked parent can be
    replaced by a symlink between the two calls.  Descriptor-relative traversal makes each next
    component relative to the directory object already opened, never to a re-resolved pathname.
    """
    absolute = _absolute_path(path)
    current_fd = os.open(os.sep, os.O_RDONLY | os.O_DIRECTORY)
    identities: list[tuple[str, int, int]] = []
    try:
        for component in absolute.parent.parts[1:]:
            next_fd = os.open(component, _directory_flags(), dir_fd=current_fd)
            info = os.fstat(next_fd)
            if not stat.S_ISDIR(info.st_mode):
                os.close(next_fd)
                raise OSError(f"path component is not a directory: {component}")
            identities.append((component, info.st_dev, info.st_ino))
            os.close(current_fd)
            current_fd = next_fd
        return current_fd, tuple(identities)
    except BaseException:
        os.close(current_fd)
        raise


def _verify_parent_chain(path: Path, expected: tuple[tuple[str, int, int], ...]) -> None:
    verification_fd: int | None = None
    try:
        verification_fd, observed = _open_parent_chain(path)
        if observed != expected:
            raise RetentionGateError(
                f"evidence path ancestry changed during access (possible redirection): {path}"
            )
    except OSError as exc:
        raise RetentionGateError(
            f"evidence path ancestry changed during access (possible symlink redirection): {path}"
        ) from exc
    finally:
        if verification_fd is not None:
            os.close(verification_fd)


def _secure_open(path: Path) -> _SecureEvidenceHandle:
    absolute = _absolute_path(path)
    parent_fd, identities = _open_parent_chain(absolute)
    try:
        _after_parent_directory_open_hook(absolute)
        fd = os.open(absolute.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
    except BaseException:
        os.close(parent_fd)
        raise
    return _SecureEvidenceHandle(absolute, fd, parent_fd, identities)


def _stable_file_metadata(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _verify_secure_handle(
    handle: _SecureEvidenceHandle, opened_stat: os.stat_result
) -> os.stat_result:
    post_stat = os.fstat(handle.fd)
    try:
        entry_stat = os.stat(handle.path.name, dir_fd=handle.parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise RetentionGateError(f"evidence file disappeared during access: {handle.path}") from exc
    if stat.S_ISLNK(entry_stat.st_mode) or (
        entry_stat.st_dev,
        entry_stat.st_ino,
    ) != (opened_stat.st_dev, opened_stat.st_ino):
        raise RetentionGateError(
            f"evidence file identity changed during hashing (possible swap): {handle.path}"
        )
    if _stable_file_metadata(post_stat) != _stable_file_metadata(opened_stat):
        raise RetentionGateError(
            f"evidence file changed during access (possible in-place mutation): {handle.path}"
        )
    _verify_parent_chain(handle.path, handle.ancestor_identities)
    return post_stat


def _primary_stat(path: Path) -> os.stat_result:
    """Verify a required primary evidence file exists, is stable, and is a regular file."""
    try:
        handle = _secure_open(path)
    except OSError as exc:
        raise RetentionGateError(
            f"required primary evidence file is missing or unstable (symlink rejected): {path}"
        ) from exc
    try:
        info = os.fstat(handle.fd)
        if not stat.S_ISREG(info.st_mode):
            raise RetentionGateError(
                f"required primary evidence path is not a regular file: {path}"
            )
        return _verify_secure_handle(handle, info)
    finally:
        handle.close()


def _sidecar_stat(path: Path) -> os.stat_result | None:
    """Optional sidecar: absence is fine, but presence must still be a stable regular file."""
    try:
        handle = _secure_open(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RetentionGateError(
            f"optional sidecar evidence path is unstable (symlink rejected): {path}"
        ) from exc
    try:
        info = os.fstat(handle.fd)
        if not stat.S_ISREG(info.st_mode):
            raise RetentionGateError(
                f"optional sidecar evidence path is not a regular file: {path}"
            )
        return _verify_secure_handle(handle, info)
    finally:
        handle.close()


def _hash_open_handle(
    handle: _SecureEvidenceHandle, opened_stat: os.stat_result
) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    first_chunk = True
    os.lseek(handle.fd, 0, os.SEEK_SET)
    while True:
        chunk = os.read(handle.fd, _CHUNK_BYTES)
        if not chunk:
            break
        digest.update(chunk)
        size += len(chunk)
        if first_chunk:
            first_chunk = False
            _after_first_chunk_hook(handle.path)
    _verify_secure_handle(handle, opened_stat)
    return digest.hexdigest(), size


def _hash_primary(path: Path) -> tuple[str, int]:
    try:
        handle = _secure_open(path)
    except OSError as exc:
        raise RetentionGateError(
            f"required primary evidence file is missing or unstable (symlink rejected): {path}"
        ) from exc
    try:
        opened_stat = os.fstat(handle.fd)
        if not stat.S_ISREG(opened_stat.st_mode):
            raise RetentionGateError(
                f"required primary evidence path is not a regular file: {path}"
            )
        return _hash_open_handle(handle, opened_stat)
    finally:
        handle.close()


def _hash_sidecar(path: Path) -> tuple[str, int] | None:
    try:
        handle = _secure_open(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RetentionGateError(
            f"optional sidecar evidence path is unstable (symlink rejected): {path}"
        ) from exc
    try:
        opened_stat = os.fstat(handle.fd)
        if not stat.S_ISREG(opened_stat.st_mode):
            raise RetentionGateError(
                f"optional sidecar evidence path is not a regular file: {path}"
            )
        return _hash_open_handle(handle, opened_stat)
    finally:
        handle.close()


def _sidecar_paths(primary: Path) -> tuple[Path, ...]:
    return tuple(primary.with_name(primary.name + suffix) for suffix in _SIDECAR_SUFFIXES)


def _canonical_evidence_paths(primaries: tuple[Path, ...]) -> list[str]:
    return [str(_absolute_path(p)) for p in primaries]


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    budget_bytes: int = DEFAULT_BUDGET_GIB * 1024**3
    free_space_floor_bytes: int = DEFAULT_FREE_SPACE_FLOOR_GIB * 1024**3
    expected_scans: int = DEFAULT_EXPECTED_SCANS

    def __post_init__(self) -> None:
        for name, value in (
            ("budget_bytes", self.budget_bytes),
            ("free_space_floor_bytes", self.free_space_floor_bytes),
            ("expected_scans", self.expected_scans),
        ):
            if type(value) is not int or value <= 0:
                raise ValueError(f"retention policy {name} must be a positive integer")

    def as_dict(self) -> dict[str, int]:
        return {
            "budget_bytes": self.budget_bytes,
            "free_space_floor_bytes": self.free_space_floor_bytes,
            "expected_scans": self.expected_scans,
        }


class AuditableRetentionLedger:
    """Per-scan and cumulative byte accounting with a fail-closed smoke projection.

    Every scan is gated by an exclusive single-writer lease (:meth:`check_before_scan` acquires
    it, :meth:`record_scan` or :meth:`abort_scan` releases it). A second concurrent
    ``AuditableRetentionLedger`` pointed at the same ``root`` fails closed instead of acquiring or
    mutating evidence.
    """

    def __init__(self, root: str | Path, policy: RetentionPolicy | None = None) -> None:
        self.root = Path(root)
        self.receipts = self.root / "retention-receipts"
        self.state_path = self.root / "retention-state.json"
        self.lock_path = self.root / "retention.lock"
        self.policy = RetentionPolicy() if policy is None else policy
        self.root.mkdir(parents=True, exist_ok=True)
        self.receipts.mkdir(parents=True, exist_ok=True)
        self._lease_handle: Any = None

    # -- lease -----------------------------------------------------------------------------

    def _acquire_lease(self) -> None:
        if self._lease_handle is not None:
            raise RetentionGateError(
                "a retention scan reservation is already active for this ledger instance"
            )
        handle = self.lock_path.open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RetentionGateError(
                "another process holds the retention lease; scan stopped before acquisition"
            ) from exc
        self._lease_handle = handle

    def _release_lease(self) -> None:
        handle = self._lease_handle
        if handle is None:
            return
        self._lease_handle = None
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def abort_scan(self) -> None:
        """Release an active reservation without recording a scan (crash/abort recovery path)."""
        self._release_lease()

    # -- state and receipt chain validation -------------------------------------------------

    def _read_receipt_file(self, receipt_id: str) -> dict[str, Any]:
        path = self.receipts / f"{receipt_id}.json"
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RetentionGateError(
                f"referenced retention receipt is missing: {receipt_id}"
            ) from exc
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RetentionGateError(
                f"referenced retention receipt is corrupt: {receipt_id}"
            ) from exc
        return _require_exact_keys(value, _RECEIPT_FIELDS, f"retention receipt {receipt_id}")

    def _validate_receipt(self, receipt: dict[str, Any], *, receipt_id: str) -> dict[str, Any]:
        if receipt.get("schema_version") != SCHEMA_VERSION:
            raise RetentionGateError(f"retention receipt {receipt_id} schema is incompatible")
        _require_str(receipt["scan_run_id"], f"receipt {receipt_id} scan_run_id")
        _require_bool(receipt["complete"], f"receipt {receipt_id} complete")
        if receipt["production_influence"] != 0 or type(receipt["production_influence"]) is not int:
            raise RetentionGateError(f"receipt {receipt_id} production_influence must be exactly 0")
        _require_nonneg_int(receipt["bytes_this_scan"], f"receipt {receipt_id} bytes_this_scan")
        _require_nonneg_int(receipt["cumulative_bytes"], f"receipt {receipt_id} cumulative_bytes")
        _require_nonneg_int(
            receipt["projected_24h_bytes"], f"receipt {receipt_id} projected_24h_bytes"
        )
        _require_positive_int(
            receipt["approved_budget_bytes"], f"receipt {receipt_id} approved_budget_bytes"
        )
        _require_positive_int(
            receipt["free_space_floor_bytes"], f"receipt {receipt_id} free_space_floor_bytes"
        )
        _require_positive_int(receipt["expected_scans"], f"receipt {receipt_id} expected_scans")
        _require_nonneg_int(
            receipt["growth_high_water_bytes"], f"receipt {receipt_id} growth_high_water_bytes"
        )
        _require_positive_int(receipt["sample_count"], f"receipt {receipt_id} sample_count")
        _require_bool(receipt["smoke_sample"], f"receipt {receipt_id} smoke_sample")
        if (
            receipt["approved_budget_bytes"] != self.policy.budget_bytes
            or receipt["free_space_floor_bytes"] != self.policy.free_space_floor_bytes
            or receipt["expected_scans"] != self.policy.expected_scans
        ):
            raise RetentionGateError(
                f"receipt {receipt_id} was recorded under a different retention policy; "
                "an explicit reviewed migration is required"
            )
        files = receipt["files"]
        if not isinstance(files, list) or not files:
            raise RetentionGateError(f"receipt {receipt_id} files must be a non-empty list")
        for entry in files:
            _require_exact_keys(entry, _RECEIPT_FILE_FIELDS, f"receipt {receipt_id} file entry")
            _require_str(entry["path"], f"receipt {receipt_id} file path")
            _require_nonneg_int(entry["bytes"], f"receipt {receipt_id} file bytes")
            _require_hex_digest(entry["sha256"], f"receipt {receipt_id} file sha256")
            if entry["role"] not in ("primary", "sidecar"):
                raise RetentionGateError(f"receipt {receipt_id} file role is invalid")
        recomputed = {k: v for k, v in receipt.items() if k != "receipt_id"}
        if _canonical_hash(recomputed) != receipt_id or receipt["receipt_id"] != receipt_id:
            raise RetentionGateError(f"retention receipt {receipt_id} fails identity verification")
        return receipt

    def _load_receipt(self, receipt_id: str) -> dict[str, Any]:
        _require_hex_digest(receipt_id, "receipt_id")
        receipt = self._read_receipt_file(receipt_id)
        return self._validate_receipt(receipt, receipt_id=receipt_id)

    @staticmethod
    def _validate_receipt_files(receipt: dict[str, Any], evidence_paths: list[str]) -> None:
        """Bind receipt byte accounting to the exact pinned primary/sidecar path set."""
        seen_paths: set[str] = set()
        primary_paths: list[str] = []
        allowed_sidecars = {
            str(_absolute_path(Path(primary).with_name(Path(primary).name + suffix)))
            for primary in evidence_paths
            for suffix in _SIDECAR_SUFFIXES
        }
        byte_total = 0
        for entry in receipt["files"]:
            path = entry["path"]
            if path in seen_paths:
                raise RetentionGateError("retention receipt contains a duplicate evidence path")
            seen_paths.add(path)
            if path != str(_absolute_path(Path(path))):
                raise RetentionGateError("retention receipt evidence path is not canonical")
            if entry["role"] == "primary":
                primary_paths.append(path)
            elif path not in allowed_sidecars:
                raise RetentionGateError(
                    "retention receipt sidecar is not bound to a pinned primary path"
                )
            byte_total += entry["bytes"]
        if primary_paths != evidence_paths:
            raise RetentionGateError(
                "retention receipt primary files do not match pinned evidence paths"
            )
        if byte_total != receipt["cumulative_bytes"]:
            raise RetentionGateError(
                "retention receipt file bytes do not equal cumulative accounting"
            )

    def _fresh_state(self) -> dict[str, Any]:
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "evidence_paths": None,
            "policy": self.policy.as_dict(),
            "before_scan_bytes": None,
            "growth_high_water_bytes": 0,
            "projected_bytes": 0,
            "scans": [],
        }

    def _load_and_validate_state(self) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        """Reload and fully validate ``retention-state.json`` and every receipt it references.

        Never silently resets corrupt or incomplete state -- every failure raises
        :class:`RetentionGateError`.
        """
        if not self.state_path.exists():
            return self._fresh_state(), {}
        try:
            raw = self.state_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RetentionGateError("retention state is unavailable") from exc
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RetentionGateError("retention state is corrupt") from exc
        state = _require_exact_keys(value, _STATE_FIELDS, "retention state")
        if state.get("schema_version") != STATE_SCHEMA_VERSION:
            raise RetentionGateError("retention state schema is incompatible")

        policy = _require_exact_keys(state["policy"], _POLICY_FIELDS, "retention state policy")
        for name in _POLICY_FIELDS:
            _require_positive_int(policy[name], f"retention state policy {name}")
        if policy != self.policy.as_dict():
            raise RetentionGateError(
                "retention policy has changed without an explicit reviewed migration"
            )

        evidence_paths = state["evidence_paths"]
        if evidence_paths is not None and (
            not isinstance(evidence_paths, list)
            or not evidence_paths
            or any(type(p) is not str or not p for p in evidence_paths)
            or len(set(evidence_paths)) != len(evidence_paths)
        ):
            raise RetentionGateError("retention state evidence_paths is malformed")
        if evidence_paths is not None and any(
            path != str(_absolute_path(Path(path))) for path in evidence_paths
        ):
            raise RetentionGateError("retention state evidence_paths are not canonical")

        if state["before_scan_bytes"] is not None:
            _require_nonneg_int(state["before_scan_bytes"], "retention state before_scan_bytes")
        _require_nonneg_int(
            state["growth_high_water_bytes"], "retention state growth_high_water_bytes"
        )
        _require_nonneg_int(state["projected_bytes"], "retention state projected_bytes")

        scans = state["scans"]
        if not isinstance(scans, list):
            raise RetentionGateError("retention state scans must be a list")
        if scans and (evidence_paths is None or state["before_scan_bytes"] is None):
            raise RetentionGateError(
                "retention state with scans requires pinned evidence paths and a baseline"
            )

        receipts: dict[str, dict[str, Any]] = {}
        seen_scan_ids: set[str] = set()
        seen_receipt_ids: set[str] = set()
        previous_cumulative = 0
        derived_prior_cumulative = int(state["before_scan_bytes"] or 0)
        derived_high_water = 0
        derived_projection = 0
        for index, entry in enumerate(scans):
            entry = _require_exact_keys(entry, _STATE_SCAN_FIELDS, "retention state scan entry")
            scan_run_id = _require_str(entry["scan_run_id"], "scan entry scan_run_id")
            receipt_id = _require_hex_digest(entry["receipt_id"], "scan entry receipt_id")
            cumulative_bytes = _require_nonneg_int(
                entry["cumulative_bytes"], "scan entry cumulative_bytes"
            )
            sample_count = _require_positive_int(entry["sample_count"], "scan entry sample_count")
            if sample_count != index + 1:
                raise RetentionGateError(
                    "retention state scan ordering is inconsistent with sample_count"
                )
            if scan_run_id in seen_scan_ids:
                raise RetentionGateError(f"duplicate scan_run_id in retention state: {scan_run_id}")
            if receipt_id in seen_receipt_ids:
                raise RetentionGateError(f"duplicate receipt_id in retention state: {receipt_id}")
            if cumulative_bytes < previous_cumulative:
                raise RetentionGateError(
                    "retention state cumulative accounting decreased between scans"
                )
            previous_cumulative = cumulative_bytes
            seen_scan_ids.add(scan_run_id)
            seen_receipt_ids.add(receipt_id)

            receipt = self._load_receipt(receipt_id)
            if (
                receipt["scan_run_id"] != scan_run_id
                or receipt["cumulative_bytes"] != cumulative_bytes
                or receipt["sample_count"] != sample_count
            ):
                raise RetentionGateError(
                    f"retention state entry does not match its receipt: {receipt_id}"
                )
            if evidence_paths is None:  # pragma: no cover - guarded before the loop
                raise RetentionGateError("retention state evidence paths are unavailable")
            self._validate_receipt_files(receipt, evidence_paths)
            if cumulative_bytes < derived_prior_cumulative:
                raise RetentionGateError(
                    "retention receipt cumulative accounting decreased from its prior baseline"
                )
            derived_delta = cumulative_bytes - derived_prior_cumulative
            if receipt["bytes_this_scan"] != derived_delta:
                raise RetentionGateError(
                    "retention receipt per-scan byte accounting is inconsistent"
                )
            derived_high_water = max(derived_high_water, derived_delta)
            if receipt["growth_high_water_bytes"] != derived_high_water:
                raise RetentionGateError(
                    "retention receipt growth high-water accounting is inconsistent"
                )
            remaining_scans = max(0, self.policy.expected_scans - sample_count)
            derived_projection = cumulative_bytes + derived_high_water * remaining_scans
            if receipt["projected_24h_bytes"] != derived_projection:
                raise RetentionGateError(
                    "retention receipt projected byte accounting is inconsistent"
                )
            derived_prior_cumulative = cumulative_bytes
            receipts[receipt_id] = receipt

        if scans:
            if (
                state["growth_high_water_bytes"] != derived_high_water
                or state["projected_bytes"] != derived_projection
            ):
                raise RetentionGateError(
                    "retention state summary does not match the validated receipt chain"
                )
        elif state["growth_high_water_bytes"] != 0 or state["projected_bytes"] != 0:
            raise RetentionGateError(
                "retention state without scans cannot contain derived accounting"
            )

        try:
            on_disk = {p.stem for p in self.receipts.glob("*.json")}
        except OSError as exc:
            raise RetentionGateError("retention receipts directory is unavailable") from exc
        orphans = on_disk - seen_receipt_ids
        if orphans:
            raise RetentionGateError(
                "orphan retention receipt(s) not referenced by state (crash between receipt "
                f"and state publication requires manual review): {sorted(orphans)}"
            )

        return state, receipts

    # -- evidence path safety ----------------------------------------------------------------

    def _validate_evidence_paths_pinned(
        self, state: dict[str, Any], primaries: tuple[Path, ...]
    ) -> list[str]:
        canonical = _canonical_evidence_paths(primaries)
        pinned = state["evidence_paths"]
        if pinned is not None and pinned != canonical:
            raise RetentionGateError(
                "evidence paths do not match the paths approved at the first scan; "
                "redirection is not permitted"
            )
        return canonical

    # -- public API ---------------------------------------------------------------------------
    #
    # Ledger writes (``self.state_path``, ``self.receipts``, ``self.lock_path``) are always
    # derived from ``self.root`` and never from caller-supplied evidence paths, so they cannot be
    # redirected outside the intended retention directory.

    def check_before_scan(self, paths: tuple[str | Path, ...]) -> None:
        self._acquire_lease()
        try:
            primaries = tuple(Path(value) for value in paths)
            state, _receipts = self._load_and_validate_state()
            canonical = self._validate_evidence_paths_pinned(state, primaries)

            free = shutil.disk_usage(self.root).free
            growth = state["growth_high_water_bytes"]
            if free - growth < self.policy.free_space_floor_bytes:
                raise RetentionGateError(
                    "hard free-space floor would be crossed by the next scan's known growth; "
                    "scan stopped before acquisition"
                )

            for primary in primaries:
                _primary_stat(primary)
                for sidecar in _sidecar_paths(primary):
                    _sidecar_stat(sidecar)

            if state["scans"] and int(state["projected_bytes"]) > self.policy.budget_bytes:
                raise RetentionGateError(
                    "projected 24-hour evidence exceeds approved storage budget"
                )

            if state["evidence_paths"] is None:
                state["evidence_paths"] = canonical
            if not state["scans"] and state["before_scan_bytes"] is None:
                before = 0
                for primary in primaries:
                    before += _primary_stat(primary).st_size
                    for sidecar in _sidecar_paths(primary):
                        info = _sidecar_stat(sidecar)
                        if info is not None:
                            before += info.st_size
                state["before_scan_bytes"] = before
            _atomic_json(self.state_path, state)
        except BaseException:
            self._release_lease()
            raise

    def record_scan(
        self,
        *,
        scan_run_id: str,
        complete: bool,
        paths: tuple[str | Path, ...],
        smoke: bool = False,
    ) -> dict[str, Any]:
        if self._lease_handle is None:
            raise RetentionGateError(
                "record_scan requires an active reservation from check_before_scan"
            )
        try:
            _require_str(scan_run_id, "scan_run_id")
            if type(complete) is not bool:
                raise RetentionGateError("complete must be a boolean")
            if type(smoke) is not bool:
                raise RetentionGateError("smoke must be a boolean")

            state, receipts = self._load_and_validate_state()
            primaries = tuple(Path(value) for value in paths)
            if state["evidence_paths"] is None:
                raise RetentionGateError(
                    "record_scan called before check_before_scan established approved evidence "
                    "paths"
                )
            self._validate_evidence_paths_pinned(state, primaries)

            files: list[dict[str, Any]] = []
            byte_count = 0
            for primary in primaries:
                digest, size = _hash_primary(primary)
                byte_count += size
                files.append(
                    {
                        "path": str(_absolute_path(primary)),
                        "bytes": size,
                        "sha256": digest,
                        "role": "primary",
                    }
                )
                for sidecar in _sidecar_paths(primary):
                    hashed = _hash_sidecar(sidecar)
                    if hashed is not None:
                        digest_s, size_s = hashed
                        byte_count += size_s
                        files.append(
                            {
                                "path": str(_absolute_path(sidecar)),
                                "bytes": size_s,
                                "sha256": digest_s,
                                "role": "sidecar",
                            }
                        )

            existing_entry = next(
                (entry for entry in state["scans"] if entry["scan_run_id"] == scan_run_id), None
            )
            if existing_entry is not None:
                existing_receipt = receipts[existing_entry["receipt_id"]]
                if (
                    existing_receipt["complete"] is complete
                    and existing_receipt["smoke_sample"] is smoke
                    and existing_receipt["cumulative_bytes"] == byte_count
                    and existing_receipt["files"] == files
                ):
                    return existing_receipt
                raise RetentionGateError(
                    f"scan_run_id {scan_run_id!r} was already recorded with different evidence; "
                    "duplicate scan identifiers must be exactly-once"
                )

            sample_count = len(state["scans"]) + 1
            if state["scans"]:
                prior_cumulative = state["scans"][-1]["cumulative_bytes"]
                prior_high_water = receipts[state["scans"][-1]["receipt_id"]][
                    "growth_high_water_bytes"
                ]
            else:
                prior_cumulative = int(state["before_scan_bytes"] or 0)
                prior_high_water = state["growth_high_water_bytes"]
            observed_delta = max(0, byte_count - prior_cumulative)
            growth_high_water = max(prior_high_water, observed_delta)
            remaining_scans = max(0, self.policy.expected_scans - sample_count)
            projected = byte_count + growth_high_water * remaining_scans
            if projected > self.policy.budget_bytes:
                raise RetentionGateError("bounded smoke projection exceeds approved storage budget")

            receipt_without_id = {
                "schema_version": SCHEMA_VERSION,
                "scan_run_id": scan_run_id,
                "complete": complete,
                "production_influence": 0,
                "bytes_this_scan": observed_delta,
                "cumulative_bytes": byte_count,
                "projected_24h_bytes": projected,
                "approved_budget_bytes": self.policy.budget_bytes,
                "free_space_floor_bytes": self.policy.free_space_floor_bytes,
                "expected_scans": self.policy.expected_scans,
                "growth_high_water_bytes": growth_high_water,
                "sample_count": sample_count,
                "smoke_sample": smoke,
                "files": files,
            }
            receipt_id = _canonical_hash(receipt_without_id)
            receipt = {**receipt_without_id, "receipt_id": receipt_id}

            # Receipt publication is content-addressed and happens before state publication. A
            # crash between the two leaves an orphan receipt, which the next reopen's
            # _load_and_validate_state detects and fails closed on -- it is never silently
            # adopted or dropped.
            _atomic_json(self.receipts / f"{receipt_id}.json", receipt)
            state["scans"].append(
                {
                    "scan_run_id": scan_run_id,
                    "receipt_id": receipt_id,
                    "cumulative_bytes": byte_count,
                    "sample_count": sample_count,
                }
            )
            state["growth_high_water_bytes"] = growth_high_water
            state["projected_bytes"] = projected
            _atomic_json(self.state_path, state)
            return receipt
        finally:
            self._release_lease()
