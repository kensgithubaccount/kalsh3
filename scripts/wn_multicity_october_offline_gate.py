"""Read-only local October C1 packet gate; never scores outcomes or market edge.

Consumes an operator-exported local directory with frozen GCS object names.
No cloud/source access, changes, outcome fetch, forecast probability or trades.
A zero exit means this structural audit ran without unexplained missing/invalid
objects; it is NOT evidence provenance, weather skill or execution authority.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import math
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

from services.forecasting.wn_multicity_evaluation_spec import (
    D1_SHA256SUMS_SHA256,
    FROZEN_PROTOCOL_SHA256,
    PRE_FIRST_EVENT_AMENDMENT_SHA256,
    PROSPECTIVE_END,
    CaptureState,
    MulticityEvaluationSpecError,
    audit_inventory,
    validate_sealed_preoutcome_packet,
)

MAX_COMPONENT_BYTES = 2_000_000
FILES = ("packet.json", "forecast.json", "market.json", "weather.json")


class OctoberAuditError(ValueError):
    """Invalid or unsafe local-only audit input."""


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OctoberAuditError("duplicate JSON key")
        result[key] = value
    return result


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise OctoberAuditError("nonfinite JSON number")
    return parsed


def _no_nonjson_constant(value: str) -> None:
    raise OctoberAuditError("nonfinite JSON constant: " + value)


def _object(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_no_duplicate_keys,
            parse_float=_finite_float,
            parse_constant=_no_nonjson_constant,
        )
    except (
        UnicodeError,
        json.JSONDecodeError,
        OverflowError,
        ValueError,
        RecursionError,
    ) as exc:
        raise OctoberAuditError("invalid JSON") from exc
    if not isinstance(value, dict):
        raise OctoberAuditError("JSON root must be an object")
    return value


# Linux/macOS dirfd and no-follow semantics are required; never silently
# downgrade to path-based checks or open a symlink outside the supplied export.
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


@contextmanager
def _open_root(root: Path) -> Iterator[int]:
    """Open every absolute ancestor without following symlinks; pin the root fd."""
    if not root.is_absolute() or ".." in root.parts:
        raise OctoberAuditError("snapshot root must be absolute without traversal")
    fd = os.open("/", DIR_FLAGS)
    try:
        for segment in root.parts[1:]:
            child = os.open(segment, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _read(root_fd: int, name: str) -> bytes:
    """Open each relative component beneath a pinned root, then read its regular fd."""
    parts = name.split("/")
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise OctoberAuditError("invalid relative snapshot component")
    parent_fd = os.dup(root_fd)
    try:
        for component in parts[:-1]:
            child_fd = os.open(component, DIR_FLAGS, dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = child_fd
        file_fd = os.open(parts[-1], FILE_FLAGS, dir_fd=parent_fd)
        try:
            before = os.fstat(file_fd)
            if not stat.S_ISREG(before.st_mode):
                raise OctoberAuditError("snapshot component is not a regular file")
            if before.st_size > MAX_COMPONENT_BYTES:
                raise OctoberAuditError("snapshot object exceeds research bound")
            chunks: list[bytes] = []
            count = 0
            while True:
                blob = os.read(file_fd, min(65536, MAX_COMPONENT_BYTES + 1 - count))
                if not blob:
                    break
                chunks.append(blob)
                count += len(blob)
                if count > MAX_COMPONENT_BYTES:
                    raise OctoberAuditError("snapshot object exceeds research bound")
            after = os.fstat(file_fd)
            if (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ) or count != after.st_size:
                raise OctoberAuditError("snapshot object changed during audit read")
            return b"".join(chunks)
        finally:
            os.close(file_fd)
    finally:
        os.close(parent_fd)


def _stamp(metadata: os.stat_result) -> tuple[int, ...]:
    """Detect namespace mutations and same-name replacements without reading bytes."""
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _walk_dir(
    dir_fd: int,
    prefix: str,
    paths: list[str],
    signatures: dict[str, tuple[int, ...]],
) -> None:
    """Every listed entry must remain stable while its directory is scanned."""
    before = _stamp(os.fstat(dir_fd))
    with os.scandir(dir_fd) as entries:
        for entry in entries:
            if entry.name in ("", ".", "..") or "/" in entry.name:
                raise OctoberAuditError("invalid snapshot directory name")
            if entry.is_symlink():
                raise OctoberAuditError("symlink in snapshot")
            item = prefix + "/" + entry.name
            if entry.is_dir(follow_symlinks=False):
                sub_fd = os.open(entry.name, DIR_FLAGS, dir_fd=dir_fd)
                try:
                    _walk_dir(sub_fd, item, paths, signatures)
                finally:
                    os.close(sub_fd)
            elif entry.is_file(follow_symlinks=False):
                metadata = os.stat(entry.name, dir_fd=dir_fd, follow_symlinks=False)
                if not stat.S_ISREG(metadata.st_mode):
                    raise OctoberAuditError("nonregular snapshot entry")
                signatures[item] = _stamp(metadata)
                paths.append(item)
            else:
                raise OctoberAuditError("nonregular snapshot entry")
    after = _stamp(os.fstat(dir_fd))
    if before != after:
        raise OctoberAuditError("directory changed during inventory")
    signatures[prefix] = after


def _inventory(
    root_fd: int,
) -> tuple[list[str], tuple[tuple[str, tuple[int, ...]], ...]]:
    """Capture entire city-day namespace and metadata, rejecting concurrent changes."""
    before_root = _stamp(os.fstat(root_fd))
    signatures: dict[str, tuple[int, ...]] = {}
    result: list[str] = []
    try:
        expected = os.stat("city_days", dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:
        signatures["."] = before_root
        if _stamp(os.fstat(root_fd)) != before_root:
            raise OctoberAuditError("snapshot root changed during inventory") from None
        return result, tuple(sorted(signatures.items()))
    if stat.S_ISLNK(expected.st_mode):
        raise OctoberAuditError("symlink in snapshot")
    if not stat.S_ISDIR(expected.st_mode):
        raise OctoberAuditError("city_days is not a real directory")
    city_fd = os.open("city_days", DIR_FLAGS, dir_fd=root_fd)
    try:
        if _stamp(os.fstat(city_fd)) != _stamp(expected):
            raise OctoberAuditError("city_days replaced during inventory")
        _walk_dir(city_fd, "city_days", result, signatures)
        if _stamp(os.stat("city_days", dir_fd=root_fd, follow_symlinks=False)) != _stamp(
            os.fstat(city_fd)
        ):
            raise OctoberAuditError("city_days replaced during inventory")
    finally:
        os.close(city_fd)
    if _stamp(os.fstat(root_fd)) != before_root:
        raise OctoberAuditError("snapshot root changed during inventory")
    signatures["."] = before_root
    return sorted(result), tuple(sorted(signatures.items()))


def _check_frozen_common(record: dict[str, Any], city: str, target: date) -> None:
    if (
        record.get("city_id") != city
        or record.get("target_date_utc") != target.isoformat()
        or record.get("same_date_cluster") != target.isoformat()
        or record.get("observational_unit") != "CITY_DAY"
        or record.get("city_days_independent") is not False
        or record.get("development_only") is not True
        or record.get("protocol_sha256") != FROZEN_PROTOCOL_SHA256
        or record.get("pre_first_event_amendment_sha256") != PRE_FIRST_EVENT_AMENDMENT_SHA256
        or record.get("d1_sha256sums_sha256") != D1_SHA256SUMS_SHA256
    ):
        raise OctoberAuditError("frozen identity or Boolean mismatch")


def _audit_opened_snapshot(root_fd: int) -> dict[str, Any]:
    """Validate frozen complete denominator and sealed bytes, without outcomes."""
    original_paths, original_metadata = _inventory(root_fd)
    coverage = audit_inventory(original_paths, as_of=PROSPECTIVE_END)
    validated = 0
    invalid: list[str] = []

    for key, state in coverage.states:
        city = key.city_id
        day = key.target_date.strftime("%Y%m%d")
        stem = f"city_days/{city}/{day}"
        if state is CaptureState.MISSING or state is CaptureState.CONFLICT:
            continue
        try:
            if state is CaptureState.FAILED:
                failure = _object(_read(root_fd, stem + "/failure.json"))
                _check_frozen_common(failure, city, key.target_date)
                if (
                    failure.get("record_type") != "WN-MULTICITY-C1-CLOUD-FAILURE-v1"
                    or failure.get("status") != "DATA_NOT_READY"
                    or failure.get("error_code") != "DATA_NOT_READY_IMAGE_COUNT"
                    or failure.get("stage") != "weather"
                    or failure.get("no_retry") is not True
                    or failure.get("no_backfill") is not True
                    or failure.get("no_trade") is not True
                ):
                    raise OctoberAuditError("invalid preserved October 4 failure")
                continue

            blobs = {name: _read(root_fd, stem + "/" + name) for name in FILES}
            for blob in blobs.values():
                _check_frozen_common(_object(blob), city, key.target_date)
            manifest = _object(_read(root_fd, stem + "/SHA256SUMS.json"))
            for name, blob in blobs.items():
                if manifest.get(f"{stem}/{name}") != hashlib.sha256(blob).hexdigest():
                    raise OctoberAuditError("manifest/component hash mismatch")
            sealed = validate_sealed_preoutcome_packet(
                packet_bytes=blobs["packet.json"],
                forecast_bytes=blobs["forecast.json"],
                market_bytes=blobs["market.json"],
                weather_bytes=blobs["weather.json"],
            )
            if sealed.city_id != city or sealed.target_date != key.target_date:
                raise OctoberAuditError("sealed identity mismatch")
            validated += 1
        except (OSError, OctoberAuditError, MulticityEvaluationSpecError) as exc:
            # No raw source bytes, forecasts, or market data in diagnostics.
            invalid.append(f"{city}/{day}:{type(exc).__name__}")

    # Fail closed if writers added, removed, renamed or modified anything during
    # validation, even if the cached denominator remained otherwise complete.
    final_paths, final_metadata = _inventory(root_fd)
    if original_paths != final_paths or original_metadata != final_metadata:
        raise OctoberAuditError("snapshot changed during audit")

    # Frozen retrospective profile, not a generic green light for all-failure exports.
    expected_profile = all(
        state is (CaptureState.FAILED if key.target_date.day == 4 else CaptureState.CAPTURED)
        for key, state in coverage.states
    )
    clean_audit = (
        expected_profile
        and not invalid
        and coverage.captured_city_days == 55
        and coverage.failed_city_days == 5
        and coverage.missing_city_days == 0
        and coverage.conflicted_city_days == 0
        and not coverage.unexpected_paths
        and validated == coverage.captured_city_days
    )
    return {
        "record_type": "WN-MULTICITY-OCTOBER-OFFLINE-STRUCTURAL-GATE-v1",
        "audit_structurally_complete": clean_audit,
        "coverage": coverage.to_dict(),
        "sealed_packets_validated": validated,
        "invalid_city_days": invalid,
        "outcome_data_read": False,
        "probability_or_edge_calculated": False,
        "source_provenance_attested": False,
        "historical_immutability_attested": False,
        "research_only": True,
        "production_influence": "0",
        "paper_or_trading_authority": "NONE",
    }


def audit_local_snapshot(root: Path) -> dict[str, Any]:
    """Pin a local snapshot directory and perform a strictly read-only audit."""
    try:
        with _open_root(root) as root_fd:
            return _audit_opened_snapshot(root_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise OctoberAuditError("snapshot symlink or invalid directory") from exc
        raise OctoberAuditError("snapshot filesystem I/O failure") from exc
    except RecursionError:
        raise OctoberAuditError("snapshot exceeds supported nesting depth") from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path, help="absolute local export root")
    args = parser.parse_args()
    try:
        report = audit_local_snapshot(args.root)
    except (OSError, OctoberAuditError, MulticityEvaluationSpecError) as exc:
        print(
            json.dumps(
                {
                    "record_type": "WN-MULTICITY-OCTOBER-OFFLINE-GATE-FAILURE-v1",
                    "status": "AUDIT_UNVERIFIED",
                    "reason": type(exc).__name__,
                    "paper_or_trading_authority": "NONE",
                },
                sort_keys=True,
            )
        )
        return 4
    report["status"] = (
        "STRUCTURAL_PASS_NOT_ATTESTED"
        if report["audit_structurally_complete"]
        else "AUDIT_UNVERIFIED"
    )
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["audit_structurally_complete"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
