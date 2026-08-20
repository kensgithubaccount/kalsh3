"""Operator-only current-03Z MAXT weather evidence composition (M27 B/C live-read delta).

Composes, in order, using ONLY existing reviewed code -- no frozen parser/model/protocol
semantics are modified or reimplemented anywhere in this script:

1. select the one reviewed-selection-rule candidate GRIB object for a given day
   (:mod:`services.forecasting.weather_current_cycle_acquisition`'s
   ``aws_index_url``/``select_candidate_object_name``/``object_url``, invoked internally by
   ``acquire_current_cycle_raw_grib``);
2. fetch the index and object bytes through the EXISTING reviewed M27C ``_get`` (real transport:
   HTTPS-allowlist validation, retries, no redirect-escape) -- ``cache=None`` on every call, so a
   fresh live run always re-fetches rather than reusing any prior cached bytes;
3. hash the exact downloaded object bytes (both inside the acquisition layer and, redundantly,
   again here as defense-in-depth against any round-trip corruption);
4. snapshot those exact bytes into a private temporary file, mirroring
   ``scripts/capture_m27l_prospective_forecast.py``'s exact reviewed TOCTOU-safe pattern, and run
   wgrib2 against that snapshot rather than touching any external path a second time;
5. run wgrib2 through the EXISTING reviewed ``_resolve_wgrib2``/``_run_wgrib2`` (version-pinned;
   rejects any executable that does not report exactly the reviewed "3.8.0" string);
6. parse the extraction through the FROZEN, unmodified
   :func:`services.forecasting.weather_calibration_grib.parse_wgrib2_max_t_evidence`;
7. call :func:`services.forecasting.weather_probability.build_current_weather_forecast_evidence`
   for each of the three supported records;
8. emit a secret-free, content-addressed JSON evidence artifact.

The filename-level ``"02"`` hour-suffix candidate selection
(:func:`services.forecasting.weather_current_cycle_acquisition.select_candidate_object_name`) is
DISCOVERY ONLY -- it never claims 03Z. Only the frozen GRIB parser
(``parse_wgrib2_max_t_evidence``/``validate_raw_grib_max_t_evidence``) and
``build_current_weather_forecast_evidence``'s own internal reference-hour check
(``reference.hour != 3 -> raise``) establish that a fetched object is actually a genuine 03Z
cycle. If the selected object fails that check, this script surfaces the parser's own rejection
verbatim -- it never overrides, catches, or downgrades it.

Freshness authority: this script performs no freshness/staleness gating of its own beyond the
acquisition layer's own internal self-consistency window -- see
``weather_current_cycle_acquisition``'s module docstring "FRESHNESS AUTHORITY" note.
:data:`services.supervised_canary.m27d.MAX_FORECAST_AGE` remains the sole, unmodified authority
for whether a resulting ``CurrentWeatherForecastEvidence`` is fresh enough for canary
eligibility; this script never imports ``services.supervised_canary`` and has no code path that
could widen or bypass that gate.

Never inspects outcomes, Kalshi, credentials, markets, economics, risk, or execution -- this
script's only non-stdlib imports are network transport (M27C's ``_get``), wgrib2 invocation
(``_resolve_wgrib2``/``_run_wgrib2``), the frozen parser, and
``build_current_weather_forecast_evidence``.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import tempfile
from dataclasses import dataclass, fields
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.collect_m27c_weather_calibration_coverage import (
    _get,
    _resolve_wgrib2,
    _run_wgrib2,
)
from services.forecasting.domain import ForecastError
from services.forecasting.weather_calibration_grib import parse_wgrib2_max_t_evidence
from services.forecasting.weather_current_cycle_acquisition import (
    SourceAcquisitionError,
    acquire_current_cycle_raw_grib,
)
from services.forecasting.weather_probability import (
    build_current_weather_forecast_evidence,
)
from services.forecasting.weather_prospective_capture import (
    KMDW_LATITUDE,
    KMDW_LONGITUDE,
)

SCHEMA = "kalsh3.forecasting.current-weather-evidence-composition.v1"
SOFTWARE_VERSION = "kalsh3.scripts.run_m27_current_weather_evidence/1"


@dataclass(frozen=True, slots=True)
class _KmdwPoint:
    latitude: Decimal
    longitude: Decimal


def _now() -> datetime:
    """Capture-time clock seam. Production always reads the real system clock; tests substitute
    this private function directly. No CLI flag or environment variable can change what this
    returns."""
    return datetime.now(UTC)


def _transport(url: str) -> tuple[dict[str, object], bytes]:
    """Adapts the existing reviewed M27C ``_get`` (real transport: HTTPS-allowlist validation,
    retries, no redirect-escape) to the ``(evidence, body)`` shape
    :func:`services.forecasting.weather_current_cycle_acquisition.acquire_current_cycle_raw_grib`
    expects. ``cache=None`` on every call -- no cache reuse; a fresh live run always re-fetches.
    """
    try:
        body = _get(url, cache=None)
    except ForecastError as exc:
        raise SourceAcquisitionError(str(exc)) from exc
    return (
        {
            "url": url,
            "status": 200,
            "classification": "SUCCESS",
            "body_sha256": hashlib.sha256(body).hexdigest(),
        },
        body,
    )


def _value_to_json(value: object) -> object:
    if isinstance(value, bool):
        return value
    if value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    raise TypeError(f"unsupported type for evidence serialization: {type(value)!r}")


def _current_evidence_to_json(evidence: Any) -> dict[str, object]:
    return {f.name: _value_to_json(getattr(evidence, f.name)) for f in fields(evidence)}


def compose(day: date, *, wgrib2_bin: str | None = None) -> dict[str, Any]:
    """Acquire, extract, parse, and bind current-cycle 03Z MAXT weather evidence for ``day``.

    Never inspects outcomes, Kalshi, credentials, markets, economics, risk, or execution.
    Returns a secret-free, content-addressed dict either way: a ``classification``/``reason``
    pair (with ``records`` left ``None``) if acquisition itself did not succeed, or a
    ``classification="SUCCESS"`` artifact with all three records if it did. Anything the
    downstream wgrib2 invocation or the frozen parser rejects propagates as the exact exception
    they raise -- this function never catches or downgrades either.
    """
    acquisition = acquire_current_cycle_raw_grib(day, transport=_transport, clock=_now)
    if not acquisition.succeeded:
        return {
            "schema": SCHEMA,
            "software_version": SOFTWARE_VERSION,
            "classification": acquisition.classification,
            "reason": acquisition.reason,
            "acquisition": acquisition.to_json(),
            "records": None,
        }

    raw_bytes = base64.b64decode(acquisition.raw_body_b64 or "")
    # Re-verify the exact downloaded bytes' hash independently of acquire_current_cycle_raw_grib's
    # own internal check -- defense in depth against any base64 round-trip corruption.
    raw_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    if raw_sha256 != acquisition.raw_sha256:
        raise ForecastError("downloaded bytes hash does not match the acquisition's own record")

    executable, executable_sha256 = _resolve_wgrib2(wgrib2_bin)
    # Snapshot the exact hashed bytes to a private temp file and run wgrib2 against *that*,
    # rather than touching any external path a second time -- closes the TOCTOU window between
    # the hash just computed and wgrib2's own read, mirroring
    # scripts/capture_m27l_prospective_forecast.py's exact reviewed pattern.
    with tempfile.TemporaryDirectory(prefix="m27-current-weather-grib-snapshot-") as snapshot_dir:
        snapshot_path = Path(snapshot_dir) / "snapshot.grib2"
        snapshot_path.write_bytes(raw_bytes)
        extraction_text = _run_wgrib2(
            executable, snapshot_path, _KmdwPoint(KMDW_LATITUDE, KMDW_LONGITUDE)
        )
    extraction_sha256 = hashlib.sha256(extraction_text.encode()).hexdigest()

    # FROZEN parser -- never modified, never reimplemented. This is the ONLY authority that can
    # establish the fetched object's actual GRIB-internal reference time is exactly 03Z; the
    # filename-level "02" hour-suffix selection upstream is discovery only and makes no such
    # claim. If this raises (e.g. reference.hour != 3), it propagates unchanged.
    evidence = parse_wgrib2_max_t_evidence(
        extraction_text,
        raw_grib_sha256=raw_sha256,
        extraction_sha256=extraction_sha256,
    )

    records = [
        _current_evidence_to_json(
            build_current_weather_forecast_evidence(evidence, record_number=number)
        )
        for number in (1, 2, 3)
    ]

    material = {
        "schema": SCHEMA,
        "software_version": SOFTWARE_VERSION,
        "classification": "SUCCESS",
        "reason": None,
        "acquisition": acquisition.to_json(),
        "wgrib2_executable_sha256": executable_sha256,
        "extraction_sha256": extraction_sha256,
        "records": records,
    }
    content_hash = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return material | {"content_hash": content_hash}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Operator-only M27 current-03Z MAXT weather evidence composition. "
            "No Kalshi, no credentials, no economics, no execution."
        )
    )
    parser.add_argument("--day", type=date.fromisoformat, default=None)
    parser.add_argument("--wgrib2-bin", type=str, default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    day = args.day or _now().date()
    result = compose(day, wgrib2_bin=args.wgrib2_bin)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(f"classification={result['classification']}")
    print("CREDENTIAL_ACCESS: NO  MUTATION: NO  REQUEST_TYPE: PUBLIC_GET_ONLY")


if __name__ == "__main__":
    main()
