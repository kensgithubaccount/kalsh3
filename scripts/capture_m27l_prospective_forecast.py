"""M27L operator-only prospective forecast capture (no network, no outcomes).

Reads one operator-supplied raw GRIB2 file already on disk, snapshots the
exact bytes it read to a private temp file, extracts that snapshot with a
version-pinned local `wgrib2`, and hands the extraction to the reviewed
Part 2B1 parser and the M27L capture boundary.  This script never performs
network I/O, never acquires or imports a GHCN-Daily outcome, and never
computes a residual, evaluation metric, or market/price value.  Every
persisted observation has already passed
`validate_prospective_forecast_evidence` before being written, and the
written file is re-loaded and strictly re-derived once more before this
script returns, so the on-disk bytes are proven to round-trip back into the
exact typed form the frozen validator requires.

Capture time authority: the collection timestamp is read internally from the
system clock (`_now`) and is not settable by any CLI flag or environment
variable — an operator cannot claim a capture time other than the one this
process actually ran at.  Tests substitute the private `_now` seam directly;
production always calls `datetime.now(UTC)`.

Extraction policy and wgrib2 version authority: both are frozen constants in
`weather_prospective_capture` (this script imports rather than redefines
them), not operator-suppliable flags.  The only wgrib2 authority is
`_resolve_wgrib2`, which itself refuses to run any executable that does not
report exactly the reviewed "3.8.0" string; its executable SHA-256 is
recorded as provenance.

Raw-byte/extraction TOCTOU: the SHA-256 recorded as `raw_grib_source.sha256`
is computed from the exact bytes this script read from the operator's path.
wgrib2 is then run against a private snapshot of those same bytes (written
immediately after the read, in a process-private temp directory) rather than
against the operator's path a second time — so even if the operator's path is
mutated or replaced the instant after this script reads it, the extraction
this script actually persists is provably the one from the hashed bytes, not
from whatever now sits at that path.

Acquisition provenance: M27C's only reviewed acquisition-receipt shape
(`raw_grib_objects` in `weather_calibration_coverage.py` /
`collect_m27c_weather_calibration_coverage.py`) is bound to the bounded
NCEI/AWS network collector — it carries a source `url` and S3 `object_key`
that only exist for a network fetch.  There is no reviewed acquisition
authority for a raw GRIB2 file the operator already has on local disk, and
this script does not invent one.  It instead records the parts of that same
receipt shape that are meaningful without a network origin (byte length,
SHA-256, family identity, research-only/zero-influence flags, and the
reviewed wgrib2 executable's own SHA-256) as `raw_grib_source` provenance on
the bundle, bound/validated on load against every observation's own hash.
`local_path` is recorded purely as informational, operator-supplied text; it
carries no cryptographic binding and is never treated as authoritative.

Publication: `_write_create_only` never creates or writes the final
destination pathname directly. It writes the complete payload to a unique
0600 temp file in the same directory (handling short writes explicitly),
fsyncs it, and publishes it to the final path with a no-replace hard link —
so a crash or a failed write can never leave a partial file at the
authoritative pathname, and two concurrent writers can never both "win".
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.collect_m27c_weather_calibration_coverage import (
    _resolve_wgrib2,
    _run_wgrib2,
)
from services.forecasting.domain import ForecastError
from services.forecasting.weather_calibration_grib import parse_wgrib2_max_t_evidence
from services.forecasting.weather_prospective import FAMILY
from services.forecasting.weather_prospective_capture import (
    EXTRACTION_POLICY_VERSION,
    KMDW_LATITUDE,
    KMDW_LONGITUDE,
    WGRIB2_VERSION,
    capture_prospective_forecast_evidence,
    deserialize_prospective_bundle,
    serialize_prospective_bundle,
)


@dataclass(frozen=True, slots=True)
class _KmdwPoint:
    latitude: Decimal
    longitude: Decimal


def _now() -> datetime:
    """Capture-time clock seam. Production always reads the real system
    clock; tests substitute this private function directly. No CLI flag or
    environment variable can change what this returns."""
    return datetime.now(UTC)


def capture(args: argparse.Namespace) -> dict[str, Any]:
    executable, executable_sha256 = _resolve_wgrib2(args.wgrib2_bin)
    if executable_sha256 is None:
        raise ForecastError("prospective capture requires a computable wgrib2 executable digest")
    raw_bytes = args.raw_grib.read_bytes()
    raw_grib_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    # Snapshot the bytes we just hashed to a private temp file and run
    # wgrib2 against *that*, rather than re-reading the operator's path a
    # second time — closes the TOCTOU window between our read and wgrib2's.
    with tempfile.TemporaryDirectory(prefix="m27l-grib-snapshot-") as snapshot_dir:
        snapshot_path = Path(snapshot_dir) / "snapshot.grib2"
        snapshot_path.write_bytes(raw_bytes)
        extraction_text = _run_wgrib2(
            executable, snapshot_path, _KmdwPoint(KMDW_LATITUDE, KMDW_LONGITUDE)
        )
    extraction_sha256 = hashlib.sha256(extraction_text.encode()).hexdigest()
    evidence = parse_wgrib2_max_t_evidence(
        extraction_text,
        extraction_policy_version=EXTRACTION_POLICY_VERSION,
        wgrib2_version=WGRIB2_VERSION,
        raw_grib_sha256=raw_grib_sha256,
        extraction_sha256=extraction_sha256,
    )
    collected_at = _now()
    observations = capture_prospective_forecast_evidence(evidence, collected_at=collected_at)
    raw_grib_source = {
        "local_path": str(args.raw_grib),
        "byte_length": len(raw_bytes),
        "sha256": raw_grib_sha256,
        "family_identity": FAMILY,
        "research_only": True,
        "production_influence": "0",
        "wgrib2_executable_sha256": executable_sha256,
    }
    bundle = serialize_prospective_bundle(observations, raw_grib_source=raw_grib_source)
    _write_create_only(args.output, bundle)
    # Prove the bytes actually on disk strictly rederive back into the exact
    # typed form the frozen validator requires, rather than trusting our own
    # in-memory `bundle` object.
    on_disk = json.loads(args.output.read_text())
    deserialize_prospective_bundle(on_disk)
    return bundle


def _write_create_only(path: Path, payload: dict[str, Any]) -> None:
    """Crash-safe, no-replace publication of the final destination file.

    Never opens or writes `path` directly. Writes the complete payload to a
    unique 0600 temp file in the same directory (handling short writes
    explicitly), fsyncs it, then publishes it to `path` via a no-replace
    hard link — atomic, kernel-arbitrated, and fails rather than silently
    replacing an existing file. If `path` already exists: byte-identical
    content is treated idempotently; a different, malformed, partial,
    symlinked, or otherwise non-regular file at `path` fails closed. The
    temp file is removed on every path, success or failure.
    """
    encoded = (json.dumps(payload, sort_keys=True, indent=2, default=str) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    fd = os.open(temp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        try:
            _write_all(fd, encoded)
            os.fsync(fd)
        finally:
            os.close(fd)
        _publish_no_replace(temp_path, path, encoded)
    finally:
        temp_path.unlink(missing_ok=True)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise ForecastError("prospective capture temp write made no progress")
        view = view[written:]


def _publish_no_replace(temp_path: Path, final_path: Path, encoded: bytes) -> None:
    try:
        os.link(temp_path, final_path)
        return
    except FileExistsError:
        pass
    try:
        existing_stat = final_path.lstat()
    except OSError as exc:
        raise ForecastError("prospective capture output cannot be inspected") from exc
    if not stat.S_ISREG(existing_stat.st_mode):
        raise ForecastError("prospective capture output exists and is not a regular file")
    if final_path.read_bytes() != encoded:
        raise ForecastError("prospective capture output exists with conflicting content")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Operator-only M27L prospective forecast-only capture. "
            "No network, no GHCN outcomes, no evaluation, no market data."
        )
    )
    parser.add_argument("--raw-grib", type=Path, required=True)
    parser.add_argument("--wgrib2-bin", type=str)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(capture(parser.parse_args()), sort_keys=True, indent=2, default=str))


if __name__ == "__main__":
    main()
