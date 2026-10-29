"""One-shot predecision WeatherNext image-METADATA capture; no model or trade authority.

Manual-only. Source acquisition is one logical EE getInfo and cannot attest
provider publication times, host-clock integrity, or real-time data use rights.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast

from scripts.audit_wn_p1_source_metadata import (
    ASSET,
    CITY_HOURS,
    MAX_METADATA_IMAGES,
    REQUIRED_BANDS,
    acquire_metadata,
    validate,
)

RECORD_TYPE = "WN-P1-PROSPECTIVE-EE-UNATTESTED-METADATA-v1"
FIRST_TARGET = date(2026, 11, 1)
MAX_METADATA_BYTES = 32_768


class ProspectiveCanaryError(ValueError):
    """No retry, source promotion, or positive paper authority on error."""


def _utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ProspectiveCanaryError("clock must return a timezone-aware datetime")
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def policy() -> dict[str, object]:
    return {
        "record_type": RECORD_TYPE,
        "asset": ASSET,
        "first_target": FIRST_TARGET.isoformat(),
        "city_windows": {city: list(hours) for city, hours in sorted(CITY_HOURS.items())},
        "first_image_required_bands": sorted(REQUIRED_BANDS),
        "max_images_including_overflow_sentinel": MAX_METADATA_IMAGES,
        "open_time_utc": "08:00:00",
        "strict_cutoff_utc": "09:00:00",
        "timezone": "FROZEN_FIXED_STANDARD_TIME_NOT_DST_WALL_CLOCK",
        "source_bytes_independently_attested": False,
        "local_clock_independently_attested": False,
        "all_image_bands_checked": False,
        "rights_approved_by_program": False,
        "probability_authorized": False,
        "paper_signal_authorized": False,
        "trading_authority": "NONE",
        "production_influence": "0",
    }


def _canonical_metadata(value: dict[str, object]) -> bytes:
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProspectiveCanaryError("noncanonical metadata") from exc
    if len(encoded) > MAX_METADATA_BYTES:
        raise ProspectiveCanaryError("metadata exceeds fixed local size bound")
    return encoded


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _receipt_write_once(directory: Path, data: dict[str, object]) -> None:
    try:
        blob = (json.dumps(data, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(directory / "receipt.json", flags, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(blob)
            stream.flush()
            os.fsync(stream.fileno())
        _sync_directory(directory)
    except (OSError, ValueError, TypeError) as exc:
        raise ProspectiveCanaryError("receipt incomplete; slot permanently poisoned") from exc


def capture_one(
    *,
    target: date,
    project: str,
    receipt_root: Path,
    getter: Callable[[str, date], dict[str, object]] | None = None,
    clock: Callable[[], datetime] | None = None,
    monotonic: Callable[[], int] | None = None,
) -> tuple[int, dict[str, object]]:
    """One target and one reserved slot, with no refetch or client-side backfill."""
    if type(target) is not date or target < FIRST_TARGET:
        raise ProspectiveCanaryError("target not in prospective date scope")
    if not project or any(not (ch.isalnum() or ch in "-_") for ch in project):
        raise ProspectiveCanaryError("invalid explicit EE project")
    if not receipt_root.is_dir() or receipt_root.is_symlink():
        raise ProspectiveCanaryError("existing non-symlink receipt directory required")

    now: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))
    ticks: Callable[[], int] = monotonic or time.monotonic_ns
    started = _utc(now())
    tick_start = ticks()
    if type(tick_start) is not int or tick_start < 0:
        raise ProspectiveCanaryError("bad monotonic start clock")

    slot = receipt_root / target.isoformat()
    try:
        # Atomic reservation is before ANY source request; a crash poisons this date.
        os.mkdir(slot, 0o700)
    except FileExistsError as exc:
        raise ProspectiveCanaryError("date already claimed; never retry") from exc
    except OSError as exc:
        raise ProspectiveCanaryError("unable to claim date") from exc
    try:
        _sync_directory(receipt_root)
    except OSError as exc:
        raise ProspectiveCanaryError("claim fsync failed; date poisoned") from exc

    midnight = datetime.combine(target, datetime.min.time(), tzinfo=UTC)
    opens = midnight + timedelta(hours=8)
    deadline = midnight + timedelta(hours=9)
    raw: dict[str, object] | None = None
    diagnostic: dict[str, object] | None = None
    digest: str | None = None
    attempted = False
    result = "FAIL_CLOSED_NO_SOURCE_CLAIM"
    reason = "OUTSIDE_WINDOW"
    ended = started
    try:
        if started.date() != target or not opens <= started < deadline:
            raise ProspectiveCanaryError("outside fixed target-day 08:00-09:00Z window")
        attempted = True
        transport = acquire_metadata if getter is None else getter
        raw = transport(project, target)
        digest = hashlib.sha256(_canonical_metadata(raw)).hexdigest()
        diagnostic = validate(target, raw)
        reason = "STRUCTURAL_EVALUATION"
        result = "ABSTAIN_INCOMPLETE_OR_LATE"
    except Exception as exc:
        reason = "FAILED_" + type(exc).__name__
        result = "FAIL_CLOSED_NO_SOURCE_CLAIM"
        # Do not poison the failure receipt with an oversized/non-JSON response.
        if digest is None:
            raw = None
    finally:
        try:
            ended = _utc(now())
            tick_end = ticks()
        except Exception:
            ended = started
            tick_end = -1

    clock_ok = type(tick_end) is int and tick_end >= tick_start and started <= ended < deadline
    if raw is not None and diagnostic is not None and clock_ok:
        ingestions = cast(list[float], raw["ingestion_times"])
        if any(ingested > ended.timestamp() for ingested in ingestions):
            clock_ok = False
            reason = "SOURCE_INGESTION_LATER_THAN_OBSERVATION"
    if not clock_ok:
        result = "FAIL_CLOSED_CLOCK_OR_DEADLINE"
        if reason != "SOURCE_INGESTION_LATER_THAN_OBSERVATION":
            reason = "CLOCK_ROLLBACK_OR_DEADLINE"
    elif diagnostic is not None:
        cities = cast(dict[str, dict[str, object]], diagnostic["cities"])
        if all(c["status"] == "HISTORICAL_METADATA_PASS_NOT_SOURCE_PROOF" for c in cities.values()):
            result = "METADATA_OBSERVED_IN_WINDOW_NOT_ATTESTED"

    fixed = policy()
    receipt: dict[str, object] = {
        "record_type": RECORD_TYPE,
        "policy": fixed,
        "policy_sha256": hashlib.sha256(
            json.dumps(fixed, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "target_date_utc": target.isoformat(),
        "ee_project": project,
        "acquisition_mode": (
            "TEST_INJECTED_UNTRUSTED" if getter is not None else "EE_LOGICAL_GETINFO"
        ),
        "started_host_utc": _iso(started),
        "finished_host_utc": _iso(ended),
        "monotonic_elapsed_ns": (
            tick_end - tick_start if type(tick_end) is int and tick_end >= tick_start else None
        ),
        "source_request_attempted": attempted,
        "status": result,
        "failure_class": reason if result.startswith("FAIL_CLOSED") else None,
        "canonical_structured_metadata_sha256_not_raw_transport": digest,
        "returned_metadata": raw,
        "five_city_diagnostic": diagnostic["cities"] if diagnostic else None,
        "source_attestation": "NONE",
        "scientific_evidence_authorized": False,
        "probability_authorized": False,
        "paper_signal_authorized": False,
        "trading_authority": "NONE",
        "production_influence": "0",
    }
    _receipt_write_once(slot, receipt)
    code = (
        0
        if result == "METADATA_OBSERVED_IN_WINDOW_NOT_ATTESTED"
        else (2 if result.startswith("FAIL_CLOSED") else 1)
    )
    return code, receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=date.fromisoformat, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--receipt-root", type=Path, required=True)
    parser.add_argument("--execute-readonly", action="store_true")
    args = parser.parse_args(argv)
    if not args.execute_readonly:
        print(json.dumps({"mode": "PLAN_ONLY_NO_SOURCE_RPC", "policy": policy()}, indent=2))
        return 0
    try:
        code, result = capture_one(
            target=args.target, project=args.project, receipt_root=args.receipt_root
        )
    except ProspectiveCanaryError as exc:
        print(f"FAIL_CLOSED: {exc}", file=sys.stderr)
        return 4
    print(
        json.dumps(
            {
                "status": result["status"],
                "target_date_utc": result["target_date_utc"],
                "probability_authorized": False,
                "paper_signal_authorized": False,
                "trading_authority": "NONE",
            },
            sort_keys=True,
        )
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
