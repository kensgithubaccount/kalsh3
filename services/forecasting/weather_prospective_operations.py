"""M27M operator-only prospective capture *archival* boundary.

This module owns the immutable local archive that turns an already-captured
and already-validated M27L prospective bundle (the exact output of
`scripts/capture_m27l_prospective_forecast.py`, or any byte-identical
re-derivation of it) into a durable, tamper-evident record with exactly one
accepted capture per forecast reference cycle:

    bundles/<sha256>.json  -- the exact validated M27L bundle bytes, verbatim
    receipts/<receipt_id>.json -- canonical metadata derived *only* after
                                   the frozen `deserialize_prospective_bundle`
                                   succeeds; never a caller-supplied field
    cycles/<reference-cycle>.json -- the create-only COMMIT POINT, published
                                      last, that marks one forecast reference
                                      cycle as accepted

`register_prospective_capture` never overwrites or deletes anything: every
write is create-only, crash-safe (temp file + explicit short-write handling
+ fsync + no-replace hard-link publish), and idempotent on byte-identical
content. A *different* valid bundle for a reference cycle that already has a
published cycle file is rejected -- the cycle file is the single source of
truth for "this reference cycle has been captured," and it can never be
replaced.

`verify_archive` is the independent, read-only counterpart: it performs no
writes, re-reads and re-hashes every artifact from scratch, reruns the
frozen M27L `deserialize_prospective_bundle` over every referenced bundle,
recomputes every receipt field and receipt identity with `derive_receipt`,
and validates filename/content identity at every layer. A cycle file that
merely exists on disk is never trusted as evidence of capture -- only a
cycle file that survives every one of those independent checks counts.
Pre-commit artifacts left behind by an interrupted or rejected registration
(a bundle or receipt with no accepted cycle referencing it) are reported as
orphans, never silently treated as accepted.

Coverage classification (PENDING / CAPTURED / MISSED) is derived using only
the frozen M27C prospective-window constants (`PROSPECTIVE_START`,
`PROSPECTIVE_END`) and the frozen M27L midpoint set (`SUPPORTED_MIDPOINTS`)
and date mapper (`target_local_date`) -- this module never invents its own
prospective calendar policy, and classification is for reporting only. No
miss, operator note, or any artifact other than a fully reverified cycle
file is ever allowed to count as evidence of capture.

This module performs no network I/O, has no market/risk/execution
dependency and no access to Kalshi account secrets, and never imports
GHCN-Daily result acquisition.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from services.market_universe.domain import stable_hash

from .domain import ForecastError
from .weather_calibration_grib import RawGribRecord, target_local_date
from .weather_prospective import (
    PROSPECTIVE_END,
    PROSPECTIVE_START,
    PROTOCOL_IDENTITY,
    PROTOCOL_VERSION,
    SUPPORTED_MIDPOINTS,
)
from .weather_prospective_capture import TIMEZONE, deserialize_prospective_bundle

RECEIPT_SCHEMA_VERSION = "m27m-prospective-receipt-v1"
CYCLE_SCHEMA_VERSION = "m27m-prospective-cycle-v1"
BUNDLES_DIRNAME = "bundles"
RECEIPTS_DIRNAME = "receipts"
CYCLES_DIRNAME = "cycles"
DEFAULT_COVERAGE_MARGIN_DAYS = 5

_CYCLE_REQUIRED_FIELDS = frozenset(
    {
        "cycle_schema_version",
        "cycle_key",
        "cycle_reference_time",
        "bundle_sha256",
        "receipt_id",
        "target_dates",
        "research_only",
        "production_influence",
    }
)


# ---------------------------------------------------------------------------
# Archive layout
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArchiveLayout:
    root: Path

    @property
    def bundles_dir(self) -> Path:
        return self.root / BUNDLES_DIRNAME

    @property
    def receipts_dir(self) -> Path:
        return self.root / RECEIPTS_DIRNAME

    @property
    def cycles_dir(self) -> Path:
        return self.root / CYCLES_DIRNAME


def archive_layout(root: Path) -> ArchiveLayout:
    return ArchiveLayout(Path(root))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_cycle_key(reference_time: datetime) -> str:
    """The archive's one and only reference-cycle identity function.

    A forecast reference cycle is identified purely by its 03Z UTC reference
    timestamp. This is the single place that maps a timestamp to the
    `cycles/<key>.json` filename, used identically by registration and
    verification, so a mismatch between what was written and what a
    filename claims is always structurally detectable.
    """
    if reference_time.tzinfo is None or reference_time.utcoffset() != timedelta(0):
        raise ForecastError("prospective cycle reference time must be canonical UTC")
    if (
        reference_time.hour != 3
        or reference_time.minute != 0
        or reference_time.second != 0
        or reference_time.microsecond != 0
    ):
        raise ForecastError("prospective cycle reference time is not the reviewed 03Z cycle")
    return reference_time.strftime("%Y%m%dT%H%M%SZ")


# ---------------------------------------------------------------------------
# Create-only, crash-safe, symlink-safe artifact publication
# ---------------------------------------------------------------------------


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise ForecastError("prospective archive temp write made no progress")
        view = view[written:]


def _publish_no_replace(temp_path: Path, final_path: Path, encoded: bytes) -> bool:
    try:
        os.link(temp_path, final_path)
        return True
    except FileExistsError:
        pass
    try:
        existing_stat = final_path.lstat()
    except OSError as exc:
        raise ForecastError(f"prospective archive path {final_path} cannot be inspected") from exc
    if not stat.S_ISREG(existing_stat.st_mode):
        raise ForecastError(
            f"prospective archive path {final_path} exists and is not a regular file"
        )
    if final_path.read_bytes() != encoded:
        raise ForecastError(
            f"prospective archive path {final_path} exists with conflicting content"
        )
    return False


def _write_create_only(path: Path, encoded: bytes) -> bool:
    """Crash-safe, create-only, symlink-safe publication of `encoded` bytes
    at `path`.

    Never opens or writes `path` directly. Writes to a unique 0600 temp file
    in the same directory (handling short writes explicitly), fsyncs it,
    then publishes it to `path` via a no-replace hard link -- atomic,
    kernel-arbitrated, and it fails rather than silently replacing an
    existing file. Byte-identical content already at `path` is treated
    idempotently (returns `False`); anything else there -- different,
    malformed, partial, or symlinked -- fails closed. The temp file is
    removed on every path, success or failure. Returns `True` only if this
    call newly created the artifact.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    fd = os.open(temp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        try:
            _write_all(fd, encoded)
            os.fsync(fd)
        finally:
            os.close(fd)
        return _publish_no_replace(temp_path, path, encoded)
    finally:
        temp_path.unlink(missing_ok=True)


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, sort_keys=True, indent=2, default=str) + "\n").encode()


# ---------------------------------------------------------------------------
# Bundle validation (never trusts caller framing; always re-derives)
# ---------------------------------------------------------------------------


def parse_and_validate_bundle(
    raw_bytes: bytes,
) -> tuple[tuple[dict[str, Any], ...], dict[str, Any], str]:
    """Parse `raw_bytes` as an exact bundle payload and run the frozen M27L
    `deserialize_prospective_bundle` over it.

    Returns `(observations, parsed_bundle, bundle_sha256)`. Every artifact
    this module ever archives is derived strictly *after* this succeeds;
    `raw_bytes` is never mutated or re-serialized before being hashed or
    archived, so the archived bundle bytes are always exactly what was
    validated.
    """
    if not isinstance(raw_bytes, bytes):
        raise ForecastError("prospective bundle input must be raw bytes")
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ForecastError("prospective bundle bytes are not valid UTF-8") from exc
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ForecastError("prospective bundle bytes are not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ForecastError("prospective bundle JSON must be an object")
    observations = deserialize_prospective_bundle(parsed)
    return observations, parsed, _sha256_bytes(raw_bytes)


# ---------------------------------------------------------------------------
# Receipt derivation
# ---------------------------------------------------------------------------


def _receipt_observation(observation: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "evidence_identity": str(observation["evidence_identity"]),
        "target_date": str(observation["target_date"]),
        "exact_midpoint_seconds": int(observation["exact_midpoint_seconds"]),
        "model_identity": str(observation["model_identity"]),
        "central_kelvin": str(observation["central_kelvin"]),
        "central_deg_f": str(observation["central_deg_f"]),
    }


def derive_receipt(
    observations: tuple[Mapping[str, Any], ...],
    *,
    bundle_sha256: str,
    raw_grib_source: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive canonical receipt metadata purely from already-validated M27L
    evidence (the `deserialize_prospective_bundle` output) and the archived
    bundle's own hash.

    Every field is *read* from the validated observations or recomputed;
    none is accepted as a caller override. Calling this twice on
    byte-identical evidence always yields a byte-identical receipt,
    including `receipt_id` -- that determinism is what makes exact
    re-registration idempotent and makes a tampered receipt on disk
    detectable by simple re-derivation and comparison.
    """
    if len(observations) != 3:
        raise ForecastError("prospective receipt requires exactly three observations")
    ordered = tuple(sorted(observations, key=lambda o: int(o["exact_midpoint_seconds"])))
    if tuple(int(o["exact_midpoint_seconds"]) for o in ordered) != tuple(
        sorted(SUPPORTED_MIDPOINTS)
    ):
        raise ForecastError(
            "prospective receipt requires exactly one observation per frozen midpoint"
        )
    if len({o["forecast_reference_time"] for o in ordered}) != 1:
        raise ForecastError("prospective receipt observations disagree on reference time")
    if len({o["collection_timestamp"] for o in ordered}) != 1:
        raise ForecastError("prospective receipt observations disagree on collection timestamp")
    if len({o["raw_grib_sha256"] for o in ordered}) != 1:
        raise ForecastError("prospective receipt observations disagree on raw GRIB provenance")
    for observation in ordered:
        if observation["research_only"] is not True or observation[
            "production_influence"
        ] != Decimal("0"):
            raise ForecastError(
                "prospective receipt requires research-only zero-influence evidence"
            )

    executable_sha256 = raw_grib_source.get("wgrib2_executable_sha256")
    if not isinstance(executable_sha256, str) or not executable_sha256:
        raise ForecastError("prospective receipt requires wgrib2 executable provenance")

    reference_time_iso = str(ordered[0]["forecast_reference_time"])
    collection_timestamp_iso = str(ordered[0]["collection_timestamp"])
    raw_grib_sha256 = str(ordered[0]["raw_grib_sha256"])
    extraction_sha256 = str(ordered[0]["extraction_sha256"])
    extraction_policy_version = str(ordered[0]["extraction_policy_version"])
    wgrib2_version = str(ordered[0]["wgrib2_version"])
    receipt_observations = tuple(_receipt_observation(o) for o in ordered)
    target_dates = tuple(o["target_date"] for o in receipt_observations)

    material = (
        RECEIPT_SCHEMA_VERSION,
        PROTOCOL_VERSION,
        PROTOCOL_IDENTITY,
        bundle_sha256,
        reference_time_iso,
        collection_timestamp_iso,
        target_dates,
        tuple(
            (
                o["evidence_identity"],
                o["target_date"],
                o["exact_midpoint_seconds"],
                o["model_identity"],
                o["central_kelvin"],
                o["central_deg_f"],
            )
            for o in receipt_observations
        ),
        raw_grib_sha256,
        extraction_sha256,
        extraction_policy_version,
        wgrib2_version,
        executable_sha256,
        True,
        "0",
    )
    receipt_id = stable_hash(material)
    return {
        "receipt_schema_version": RECEIPT_SCHEMA_VERSION,
        "receipt_id": receipt_id,
        "protocol_version": PROTOCOL_VERSION,
        "protocol_identity": PROTOCOL_IDENTITY,
        "bundle_sha256": bundle_sha256,
        "cycle_reference_time": reference_time_iso,
        "collection_timestamp": collection_timestamp_iso,
        "target_dates": list(target_dates),
        "observations": list(receipt_observations),
        "raw_grib_sha256": raw_grib_sha256,
        "extraction_sha256": extraction_sha256,
        "extraction_policy_version": extraction_policy_version,
        "wgrib2_version": wgrib2_version,
        "wgrib2_executable_sha256": executable_sha256,
        "research_only": True,
        "production_influence": "0",
    }


# ---------------------------------------------------------------------------
# Registration (bundle -> receipt -> cycle, cycle published last)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    cycle_key: str
    cycle_reference_time: str
    bundle_sha256: str
    receipt_id: str
    bundle_created: bool
    receipt_created: bool
    cycle_created: bool
    target_dates: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_key": self.cycle_key,
            "cycle_reference_time": self.cycle_reference_time,
            "bundle_sha256": self.bundle_sha256,
            "receipt_id": self.receipt_id,
            "bundle_created": self.bundle_created,
            "receipt_created": self.receipt_created,
            "cycle_created": self.cycle_created,
            "target_dates": list(self.target_dates),
        }


def _archive_bundle_bytes(raw_bytes: bytes, bundle_sha256: str, layout: ArchiveLayout) -> bool:
    bundle_path = layout.bundles_dir / f"{bundle_sha256}.json"
    return _write_create_only(bundle_path, raw_bytes)


def _archive_receipt(receipt: Mapping[str, Any], layout: ArchiveLayout) -> bool:
    receipt_path = layout.receipts_dir / f"{receipt['receipt_id']}.json"
    return _write_create_only(receipt_path, _canonical_json(receipt))


def _publish_cycle(cycle_payload: Mapping[str, Any], cycle_key: str, layout: ArchiveLayout) -> bool:
    """Publish the cycle commit point. `_write_create_only` already
    distinguishes idempotent identical content (returns `False`, no error)
    from every failure mode -- conflicting content, a symlink, or any other
    non-regular file already at the path -- which it reports verbatim via
    `ForecastError`. This is intentionally a thin pass-through: it must
    never blur a symlink-substitution or corruption failure into a generic
    "different accepted capture" message, since that would hide exactly the
    kind of tampering this archive exists to detect.
    """
    cycle_path = layout.cycles_dir / f"{cycle_key}.json"
    return _write_create_only(cycle_path, _canonical_json(cycle_payload))


def register_prospective_capture(raw_bytes: bytes, root: Path) -> RegistrationResult:
    """Validate `raw_bytes` as an exact M27L prospective bundle and archive
    it exactly once per forecast reference cycle.

    Order of operations is safety-bearing: the bundle bytes are archived
    first, the receipt derived from the validated evidence is archived
    second (both create-only and idempotent on identical content), and the
    cycle file -- the single COMMIT POINT that marks a reference cycle as
    accepted -- is published last, only after both succeed. If a process is
    interrupted at any point before the cycle file is published, nothing has
    been "accepted" yet: bundles/ and receipts/ artifacts written so far are
    pre-commit, and re-registering the identical bytes safely resumes and
    completes the commit (every step is independently idempotent). A
    *different* valid bundle for a reference cycle that already has a
    published cycle file is rejected -- exactly one accepted capture per
    forecast reference cycle, ever; any bundle/receipt written for the
    rejected attempt is left on disk as a pre-commit orphan, never silently
    overwritten or deleted, and is reported by `verify_archive`.
    """
    observations, parsed_bundle, bundle_sha256 = parse_and_validate_bundle(raw_bytes)
    layout = archive_layout(root)

    bundle_created = _archive_bundle_bytes(raw_bytes, bundle_sha256, layout)

    receipt = derive_receipt(
        observations, bundle_sha256=bundle_sha256, raw_grib_source=parsed_bundle["raw_grib_source"]
    )
    receipt_created = _archive_receipt(receipt, layout)

    reference_time = datetime.fromisoformat(receipt["cycle_reference_time"])
    cycle_key = canonical_cycle_key(reference_time)
    cycle_payload = {
        "cycle_schema_version": CYCLE_SCHEMA_VERSION,
        "cycle_key": cycle_key,
        "cycle_reference_time": receipt["cycle_reference_time"],
        "bundle_sha256": bundle_sha256,
        "receipt_id": receipt["receipt_id"],
        "target_dates": receipt["target_dates"],
        "research_only": True,
        "production_influence": "0",
    }
    cycle_created = _publish_cycle(cycle_payload, cycle_key, layout)

    return RegistrationResult(
        cycle_key=cycle_key,
        cycle_reference_time=receipt["cycle_reference_time"],
        bundle_sha256=bundle_sha256,
        receipt_id=receipt["receipt_id"],
        bundle_created=bundle_created,
        receipt_created=receipt_created,
        cycle_created=cycle_created,
        target_dates=tuple(receipt["target_dates"]),
    )


# ---------------------------------------------------------------------------
# Expected prospective 03Z coverage -- derived, never a new calendar policy
# ---------------------------------------------------------------------------


def _shadow_record(
    reference_time: datetime, interval_start: datetime, interval_end: datetime
) -> RawGribRecord:
    """A minimal carrier record so the frozen `target_local_date` mapper can
    be called directly. Only `interval_start`/`interval_end`/the `midpoint`
    property it derives from them are ever read by that function; every
    other field here is an inert placeholder, never consulted."""
    return RawGribRecord(
        record_number=1,
        reference_time=reference_time,
        variable="TMAX",
        level="2 m above ground",
        generating_process_code=2,
        statistical_process_code=2,
        time_processing_code=2,
        parameter=(0, 0, 4),
        unit="Kelvin",
        interval_start=interval_start,
        interval_end=interval_end,
        verification_time=interval_end,
        latitude=Decimal("0"),
        raw_longitude=Decimal("0"),
        signed_longitude=Decimal("0"),
        kelvin=Decimal("0"),
        grid_template=0,
        nx=0,
        ny=0,
        dx=Decimal("0"),
        dy=Decimal("0"),
    )


@dataclass(frozen=True, slots=True)
class ExpectedCycle:
    reference_time: datetime
    cycle_key: str
    target_dates: tuple[date, ...]
    capture_deadline: datetime


def expected_reference_cycles(
    *, margin_days: int = DEFAULT_COVERAGE_MARGIN_DAYS
) -> tuple[ExpectedCycle, ...]:
    """Enumerate every 03Z forecast reference cycle whose three frozen
    midpoints (`SUPPORTED_MIDPOINTS`) all map -- via the exact frozen
    `target_local_date` date mapper the real validator uses -- to a target
    date inside the frozen M27C prospective confirmation window
    (`PROSPECTIVE_START`..`PROSPECTIVE_END`).

    This is deliberately not a new prospective calendar policy: every
    boundary consulted here is read from the frozen M27C manifest
    (`weather_prospective`) and every date mapping is delegated to the
    frozen M27L authority (`weather_calibration_grib.target_local_date`);
    nothing about local-date mapping or DST handling is reimplemented.
    `margin_days` only widens the UTC calendar-day scan so a boundary cycle
    is never missed by an off-by-one scan window -- it never changes which
    cycles are actually included, since inclusion is decided solely by
    whether every one of the three recomputed target dates falls inside the
    frozen window.
    """
    cycles: list[ExpectedCycle] = []
    day = PROSPECTIVE_START - timedelta(days=margin_days)
    last_day = PROSPECTIVE_END + timedelta(days=1)
    ordered_midpoints = tuple(sorted(SUPPORTED_MIDPOINTS))
    while day <= last_day:
        reference_time = datetime(day.year, day.month, day.day, 3, 0, 0, tzinfo=UTC)
        target_dates: list[date] = []
        interval_starts: list[datetime] = []
        for midpoint_seconds in ordered_midpoints:
            midpoint = reference_time + timedelta(seconds=midpoint_seconds)
            interval_start = midpoint - timedelta(hours=6)
            interval_end = midpoint + timedelta(hours=6)
            shadow = _shadow_record(reference_time, interval_start, interval_end)
            target_dates.append(target_local_date(shadow, TIMEZONE))
            interval_starts.append(interval_start)
        if all(PROSPECTIVE_START <= target <= PROSPECTIVE_END for target in target_dates):
            cycles.append(
                ExpectedCycle(
                    reference_time=reference_time,
                    cycle_key=canonical_cycle_key(reference_time),
                    target_dates=tuple(target_dates),
                    capture_deadline=min(interval_starts),
                )
            )
        day += timedelta(days=1)
    return tuple(cycles)


class CycleStatus(StrEnum):
    CAPTURED = "CAPTURED"
    PENDING = "PENDING"
    MISSED = "MISSED"


def classify_cycle(expected: ExpectedCycle, *, captured: bool, as_of: datetime) -> CycleStatus:
    """Classify one expected reference cycle for reporting only.

    `captured` must come solely from `verify_archive`'s independently
    reverified cycle set -- never from a note, a miss record, or any other
    signal. A cycle with no captured evidence is MISSED once `as_of` has
    passed its capture deadline (the earliest record's `interval_start`,
    the same 03Z+9h boundary the frozen validator itself enforces as the
    latest legal collection instant) and PENDING before that.
    """
    if as_of.tzinfo is None or as_of.utcoffset() != timedelta(0):
        raise ForecastError("prospective coverage classification requires a canonical UTC as_of")
    if captured:
        return CycleStatus.CAPTURED
    if as_of < expected.capture_deadline:
        return CycleStatus.PENDING
    return CycleStatus.MISSED


# ---------------------------------------------------------------------------
# Read-only verification
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ArtifactProblem:
    category: str
    path: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"category": self.category, "path": self.path, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class AcceptedCycle:
    cycle_key: str
    cycle_reference_time: str
    bundle_sha256: str
    receipt_id: str
    target_dates: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_key": self.cycle_key,
            "cycle_reference_time": self.cycle_reference_time,
            "bundle_sha256": self.bundle_sha256,
            "receipt_id": self.receipt_id,
            "target_dates": list(self.target_dates),
        }


@dataclass(frozen=True, slots=True)
class VerificationReport:
    as_of: datetime
    accepted_cycles: tuple[AcceptedCycle, ...]
    orphan_bundles: tuple[str, ...]
    orphan_receipts: tuple[str, ...]
    problems: tuple[ArtifactProblem, ...]
    cycle_classifications: tuple[tuple[str, CycleStatus], ...]

    @property
    def ok(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "ok": self.ok,
            "accepted_cycles": [c.to_dict() for c in self.accepted_cycles],
            "orphan_bundles": list(self.orphan_bundles),
            "orphan_receipts": list(self.orphan_receipts),
            "problems": [p.to_dict() for p in self.problems],
            "cycle_classifications": [
                {"cycle_key": key, "status": status.value}
                for key, status in self.cycle_classifications
            ],
        }


def _scan_directory(directory: Path) -> tuple[dict[str, bytes], tuple[ArtifactProblem, ...]]:
    """Read every direct child of `directory`. Never writes. Returns
    (name -> bytes for every regular, non-symlink `.json` file, problems for
    anything else found there)."""
    contents: dict[str, bytes] = {}
    problems: list[ArtifactProblem] = []
    if not directory.exists() and not directory.is_symlink():
        return contents, tuple(problems)
    if directory.is_symlink() or not directory.is_dir():
        problems.append(
            ArtifactProblem(
                "archive-layout", str(directory), "archive directory is not a real directory"
            )
        )
        return contents, tuple(problems)
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        if entry.is_symlink():
            problems.append(
                ArtifactProblem("symlink", str(entry), "archived artifact path is a symlink")
            )
            continue
        if not entry.is_file():
            problems.append(
                ArtifactProblem(
                    "non-regular", str(entry), "archived artifact path is not a regular file"
                )
            )
            continue
        if entry.suffix != ".json":
            problems.append(
                ArtifactProblem(
                    "unexpected-file", str(entry), "archive directory contains a non-.json file"
                )
            )
            continue
        contents[entry.name] = entry.read_bytes()
    return contents, tuple(problems)


def _verify_one_cycle(
    filename: str,
    raw: bytes,
    bundle_files: Mapping[str, bytes],
    receipt_files: Mapping[str, bytes],
    layout: ArchiveLayout,
) -> ArtifactProblem | tuple[AcceptedCycle, str, str]:
    path = str(layout.cycles_dir / filename)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ArtifactProblem("malformed-cycle", path, "cycle file is not valid JSON")
    if not isinstance(payload, dict) or frozenset(payload) != _CYCLE_REQUIRED_FIELDS:
        return ArtifactProblem("malformed-cycle", path, "cycle schema is not exact")
    if payload["cycle_schema_version"] != CYCLE_SCHEMA_VERSION:
        return ArtifactProblem(
            "malformed-cycle",
            path,
            "cycle schema version conflicts with the frozen archive protocol",
        )
    if payload["research_only"] is not True or payload["production_influence"] != "0":
        return ArtifactProblem(
            "malformed-cycle", path, "cycle must be research-only with zero influence"
        )
    if not isinstance(payload["cycle_reference_time"], str):
        return ArtifactProblem("malformed-cycle", path, "cycle reference time is not a string")
    try:
        reference_time = datetime.fromisoformat(payload["cycle_reference_time"])
    except ValueError:
        return ArtifactProblem(
            "malformed-cycle", path, "cycle reference time is not a valid ISO timestamp"
        )
    try:
        expected_key = canonical_cycle_key(reference_time)
    except ForecastError as exc:
        return ArtifactProblem("malformed-cycle", path, str(exc))
    if expected_key != payload["cycle_key"] or expected_key != filename.removesuffix(".json"):
        return ArtifactProblem(
            "filename-mismatch", path, "cycle filename does not match its own reference time"
        )

    bundle_sha256 = payload["bundle_sha256"]
    receipt_id = payload["receipt_id"]
    if not isinstance(bundle_sha256, str) or not isinstance(receipt_id, str):
        return ArtifactProblem(
            "malformed-cycle", path, "cycle bundle_sha256/receipt_id are not strings"
        )
    bundle_name = f"{bundle_sha256}.json"
    receipt_name = f"{receipt_id}.json"
    bundle_raw = bundle_files.get(bundle_name)
    if bundle_raw is None:
        return ArtifactProblem(
            "missing-artifact", path, f"cycle references missing bundle {bundle_name}"
        )
    receipt_raw = receipt_files.get(receipt_name)
    if receipt_raw is None:
        return ArtifactProblem(
            "missing-artifact", path, f"cycle references missing receipt {receipt_name}"
        )

    if _sha256_bytes(bundle_raw) != bundle_sha256:
        return ArtifactProblem(
            "hash-mismatch",
            str(layout.bundles_dir / bundle_name),
            "bundle content does not hash to its own filename",
        )

    try:
        observations, parsed_bundle, _ = parse_and_validate_bundle(bundle_raw)
    except ForecastError as exc:
        return ArtifactProblem("invalid-bundle", str(layout.bundles_dir / bundle_name), str(exc))

    try:
        recomputed_receipt = derive_receipt(
            observations,
            bundle_sha256=bundle_sha256,
            raw_grib_source=parsed_bundle["raw_grib_source"],
        )
    except ForecastError as exc:
        return ArtifactProblem("invalid-bundle", str(layout.bundles_dir / bundle_name), str(exc))

    if recomputed_receipt["receipt_id"] != receipt_id:
        return ArtifactProblem(
            "filename-mismatch",
            str(layout.receipts_dir / receipt_name),
            "receipt filename does not match its recomputed identity",
        )

    try:
        stored_receipt = json.loads(receipt_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ArtifactProblem(
            "malformed-receipt",
            str(layout.receipts_dir / receipt_name),
            "receipt file is not valid JSON",
        )
    if stored_receipt != recomputed_receipt:
        return ArtifactProblem(
            "tampered-receipt",
            str(layout.receipts_dir / receipt_name),
            "receipt content does not match the independently recomputed receipt",
        )

    if tuple(payload["target_dates"]) != tuple(recomputed_receipt["target_dates"]):
        return ArtifactProblem(
            "tampered-cycle", path, "cycle target dates do not match the recomputed receipt"
        )

    accepted = AcceptedCycle(
        cycle_key=expected_key,
        cycle_reference_time=recomputed_receipt["cycle_reference_time"],
        bundle_sha256=bundle_sha256,
        receipt_id=receipt_id,
        target_dates=tuple(recomputed_receipt["target_dates"]),
    )
    return accepted, bundle_name, receipt_name


def _check_orphan_bundle(name: str, raw: bytes, problems: list[ArtifactProblem]) -> None:
    bundle_sha256 = name.removesuffix(".json")
    if _sha256_bytes(raw) != bundle_sha256:
        problems.append(
            ArtifactProblem(
                "hash-mismatch", name, "orphan bundle content does not hash to its own filename"
            )
        )
        return
    try:
        parse_and_validate_bundle(raw)
    except ForecastError as exc:
        problems.append(
            ArtifactProblem("invalid-bundle", name, f"orphan bundle fails validation: {exc}")
        )


def _check_orphan_receipt(
    name: str, raw: bytes, bundle_files: Mapping[str, bytes], problems: list[ArtifactProblem]
) -> None:
    receipt_id = name.removesuffix(".json")
    try:
        stored_receipt = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        problems.append(
            ArtifactProblem("malformed-receipt", name, "orphan receipt is not valid JSON")
        )
        return
    if not isinstance(stored_receipt, dict) or stored_receipt.get("receipt_id") != receipt_id:
        problems.append(
            ArtifactProblem(
                "filename-mismatch",
                name,
                "orphan receipt filename does not match its own receipt_id",
            )
        )
        return
    bundle_sha256 = stored_receipt.get("bundle_sha256")
    bundle_raw = (
        bundle_files.get(f"{bundle_sha256}.json") if isinstance(bundle_sha256, str) else None
    )
    if bundle_raw is None:
        problems.append(
            ArtifactProblem(
                "missing-artifact", name, "orphan receipt references a bundle that is not archived"
            )
        )
        return
    try:
        observations, parsed_bundle, actual_bundle_sha256 = parse_and_validate_bundle(bundle_raw)
    except ForecastError as exc:
        problems.append(
            ArtifactProblem(
                "invalid-bundle",
                name,
                f"orphan receipt's referenced bundle fails validation: {exc}",
            )
        )
        return
    recomputed = derive_receipt(
        observations,
        bundle_sha256=actual_bundle_sha256,
        raw_grib_source=parsed_bundle["raw_grib_source"],
    )
    if recomputed != stored_receipt:
        problems.append(
            ArtifactProblem(
                "tampered-receipt",
                name,
                "orphan receipt content does not match its independently recomputed receipt",
            )
        )


def verify_archive(root: Path, *, as_of: datetime) -> VerificationReport:
    """Read-only reconciliation of the M27M immutable local archive.

    Performs no writes whatsoever. Independently re-reads and hashes every
    artifact found under `root`, reruns the frozen M27L
    `deserialize_prospective_bundle` over every referenced bundle,
    recomputes every receipt field and receipt identity with
    `derive_receipt`, and validates filename/content identity at every
    layer. A cycle counts as CAPTURED only if every one of those checks
    passes; a cycle file that exists but fails any check is reported as a
    problem and its expected reference cycle is classified from `as_of`
    exactly as if no cycle file existed -- nothing short of a fully
    reverified cycle is ever treated as evidence of capture.
    """
    if as_of.tzinfo is None or as_of.utcoffset() != timedelta(0):
        raise ForecastError(
            "prospective archive verification requires a canonical UTC as_of timestamp"
        )
    layout = archive_layout(root)
    bundle_files, bundle_dir_problems = _scan_directory(layout.bundles_dir)
    receipt_files, receipt_dir_problems = _scan_directory(layout.receipts_dir)
    cycle_files, cycle_dir_problems = _scan_directory(layout.cycles_dir)

    problems: list[ArtifactProblem] = [
        *bundle_dir_problems,
        *receipt_dir_problems,
        *cycle_dir_problems,
    ]
    accepted_cycles: list[AcceptedCycle] = []
    referenced_bundles: set[str] = set()
    referenced_receipts: set[str] = set()
    captured_reference_times: set[datetime] = set()

    for name in sorted(cycle_files):
        cycle_result = _verify_one_cycle(
            name, cycle_files[name], bundle_files, receipt_files, layout
        )
        if isinstance(cycle_result, ArtifactProblem):
            problems.append(cycle_result)
            continue
        accepted, bundle_name, receipt_name = cycle_result
        accepted_cycles.append(accepted)
        referenced_bundles.add(bundle_name)
        referenced_receipts.add(receipt_name)
        captured_reference_times.add(datetime.fromisoformat(accepted.cycle_reference_time))

    orphan_bundles = tuple(sorted(set(bundle_files) - referenced_bundles))
    orphan_receipts = tuple(sorted(set(receipt_files) - referenced_receipts))

    for name in orphan_bundles:
        _check_orphan_bundle(name, bundle_files[name], problems)
    for name in orphan_receipts:
        _check_orphan_receipt(name, receipt_files[name], bundle_files, problems)

    expected = expected_reference_cycles()
    classifications = tuple(
        (
            cycle.cycle_key,
            classify_cycle(
                cycle, captured=cycle.reference_time in captured_reference_times, as_of=as_of
            ),
        )
        for cycle in expected
    )

    return VerificationReport(
        as_of=as_of,
        accepted_cycles=tuple(accepted_cycles),
        orphan_bundles=orphan_bundles,
        orphan_receipts=orphan_receipts,
        problems=tuple(problems),
        cycle_classifications=classifications,
    )
