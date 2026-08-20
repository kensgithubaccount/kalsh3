"""M27N.2 -- bounded LOCAL wgrib2 replay of RETAINED raw GRIB evidence.

Never fetches anything over the network. Composes, in order, using ONLY existing reviewed code --
no frozen parser semantics are modified or reimplemented anywhere in this script:

1. read an already-persisted, already-acquired :class:`services.forecasting.
   weather_current_cycle_acquisition.AcquiredForecastSource` JSON payload (produced out-of-band,
   at acquisition time, by that module's own reviewed acquisition path -- this script never
   acquires anything itself);
2. independently re-validate it through the EXISTING, FROZEN :func:`services.forecasting.
   weather_current_cycle_acquisition.validate_acquired_forecast_source` -- never trust the
   payload's own stamped claims;
3. snapshot the exact retained raw bytes into a private temporary file, mirroring
   ``scripts/run_m27_current_weather_evidence.py``'s own reviewed TOCTOU-safe pattern (hash the
   bytes, write them to a private temp path, run wgrib2 against THAT path rather than touching any
   external path a second time);
4. run wgrib2 through the EXISTING reviewed ``_resolve_wgrib2``/``_run_wgrib2``
   (:mod:`scripts.collect_m27c_weather_calibration_coverage`, version-pinned to "3.8.0") -- this
   file adds NO new subprocess capability: it imports and calls the same two functions
   ``scripts/run_m27_current_weather_evidence.py`` already reuses this exact way (a
   scripts-importing-scripts reuse, never a services->scripts dependency);
5. parse the extraction through the FROZEN, unmodified :func:`services.forecasting.
   weather_calibration_grib.parse_wgrib2_max_t_evidence`.

The reviewed ``_resolve_wgrib2`` call's own executable-identity hash for the exact binary
``_run_wgrib2`` is then invoked with is captured and returned as ``wgrib2_executable_sha256`` --
see :class:`WgribReplayResult` -- the same field ``scripts/run_m27_current_weather_evidence.py``
already persists in its own output, closing the one gap between this script and the sibling
pattern it otherwise mirrors exactly.

This script never invokes :func:`services.forecasting.weather_probability.
build_current_weather_forecast_evidence` itself -- that composition step belongs to
:mod:`services.supervised_canary.m27n2_candidate_packet`, which accepts the
:class:`~services.forecasting.weather_calibration_grib.RawGribEvidence` this script produces as a
plain input. No Kalshi, no credentials, no economics, no risk, no execution capability anywhere in
this file.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.collect_m27c_weather_calibration_coverage import _resolve_wgrib2, _run_wgrib2
from services.forecasting.domain import ForecastError
from services.forecasting.weather_calibration_grib import (
    RawGribEvidence,
    parse_wgrib2_max_t_evidence,
)
from services.forecasting.weather_current_cycle_acquisition import (
    AcquisitionValidation,
    validate_acquired_forecast_source,
)
from services.forecasting.weather_prospective_capture import KMDW_LATITUDE, KMDW_LONGITUDE

SOFTWARE_VERSION = "kalsh3.scripts.run_m27n2_wgrib2_replay/1"


@dataclass(frozen=True, slots=True)
class _KmdwPoint:
    latitude: Decimal
    longitude: Decimal


class ReplayError(RuntimeError):
    """Retained acquisition evidence failed independent revalidation, or wgrib2 replay failed."""


@dataclass(frozen=True, slots=True)
class WgribReplayResult:
    """One replay's evidence, bound to the identity of the executable that actually produced it.

    ``wgrib2_executable_sha256`` is exactly the value the EXISTING reviewed ``_resolve_wgrib2``
    call inside :func:`replay` returned for the SAME ``executable`` string
    :func:`~scripts.collect_m27c_weather_calibration_coverage._run_wgrib2` was then invoked with
    -- there is no second resolution path, no caller-suppliable override, and no reconstruction
    from a bare pathname: it is the one value the reviewed resolver itself already computed
    (hashing the resolved executable's own file bytes), read out of the same tuple this function
    already had in hand before invoking the runner. It describes only the *executable identity*
    link in the chain (retained GRIB bytes -> reviewed executable identity -> reviewed fixed
    invocation -> extraction -> frozen parser -> RawGribEvidence) -- it is not itself a claim
    about NOAA/AWS provenance, extraction correctness, or overall weather evidence authenticity;
    those come from the other links, unchanged by this addition.
    """

    evidence: RawGribEvidence
    wgrib2_executable_sha256: str | None


def replay(
    retained_payload: object,
    *,
    expected_day: date,
    now: datetime,
    maximum_age: timedelta,
    wgrib2_bin: str | None = None,
) -> WgribReplayResult:
    """Independently re-validate retained acquisition evidence, then replay it through the
    EXISTING reviewed wgrib2 resolver/runner and the FROZEN GRIB parser.

    Raises :class:`ReplayError` (fail closed) if the retained payload does not itself
    independently validate, if the raw body is not valid strict/canonical base64 (illegal
    characters, malformed padding, and non-canonical encodings are all rejected -- never silently
    decoded permissively), or if the redundant hash re-check after decode disagrees. Anything
    wgrib2 or the frozen parser itself rejects propagates unchanged -- this function never catches
    or downgrades either.
    """
    validation: AcquisitionValidation = validate_acquired_forecast_source(
        retained_payload, expected_day=expected_day, now=now, maximum_age=maximum_age
    )
    if not validation.succeeded or validation.raw_sha256 is None:
        raise ReplayError(
            f"retained acquisition evidence did not validate: {validation.classification} "
            f"({validation.reason})"
        )
    if not isinstance(retained_payload, dict):  # pragma: no cover - succeeded implies dict
        raise ReplayError("retained acquisition payload is not an object")

    # Strict (validate=True) decode -- rejects non-canonical/permissive-only encodings (illegal
    # characters, malformed padding, embedded whitespace) rather than silently dropping them, the
    # same strict-decode convention every other retained-evidence base64 re-decode in this
    # repository uses (m27n2_evidence_reconstruction.py, event_snapshot.py, market_snapshot.py,
    # orderbook_snapshot.py, weather_current_cycle_acquisition.py).
    try:
        raw_bytes = base64.b64decode(retained_payload["raw_body_b64"], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ReplayError(f"retained raw body is not valid canonical base64: {exc}") from exc
    # Redundant, independent of validate_acquired_forecast_source's own internal check --
    # defense in depth against any base64 round-trip corruption before the bytes reach wgrib2.
    if hashlib.sha256(raw_bytes).hexdigest() != validation.raw_sha256:
        raise ReplayError("retained raw bytes hash does not match the validator's own record")

    # The reviewed resolver's own executable-identity hash for the exact `executable` string
    # about to be passed to `_run_wgrib2` below -- captured once, from this one resolution, and
    # carried through unchanged to the returned WgribReplayResult. No second resolution, no
    # caller-suppliable override.
    executable, executable_sha256 = _resolve_wgrib2(wgrib2_bin)
    # Snapshot the exact hash-proven bytes to a private temp file and run wgrib2 against *that*,
    # never touching the (nonexistent, here -- these are retained, not fetched) external path a
    # second time -- mirrors scripts/run_m27_current_weather_evidence.py's exact reviewed pattern.
    with tempfile.TemporaryDirectory(prefix="m27n2-wgrib2-replay-snapshot-") as snapshot_dir:
        snapshot_path = Path(snapshot_dir) / "snapshot.grib2"
        snapshot_path.write_bytes(raw_bytes)
        extraction_text = _run_wgrib2(
            executable, snapshot_path, _KmdwPoint(KMDW_LATITUDE, KMDW_LONGITUDE)
        )
    extraction_sha256 = hashlib.sha256(extraction_text.encode()).hexdigest()

    # FROZEN parser -- never modified, never reimplemented.
    evidence = parse_wgrib2_max_t_evidence(
        extraction_text,
        raw_grib_sha256=validation.raw_sha256,
        extraction_sha256=extraction_sha256,
    )
    return WgribReplayResult(evidence=evidence, wgrib2_executable_sha256=executable_sha256)


def _now() -> datetime:
    return datetime.now(UTC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "M27N.2 bounded LOCAL wgrib2 replay of RETAINED raw GRIB evidence. No network, no "
            "credentials, no economics, no execution. Reuses the existing reviewed wgrib2 "
            "resolver/runner only; adds no new subprocess capability."
        )
    )
    parser.add_argument("--retained-evidence", type=Path, required=True)
    parser.add_argument("--day", type=date.fromisoformat, required=True)
    parser.add_argument("--maximum-age-seconds", type=int, required=True)
    parser.add_argument("--wgrib2-bin", type=str, default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    retained_payload = json.loads(args.retained_evidence.read_text())
    now = _now()
    try:
        result = replay(
            retained_payload,
            expected_day=args.day,
            now=now,
            maximum_age=timedelta(seconds=args.maximum_age_seconds),
            wgrib2_bin=args.wgrib2_bin,
        )
    except (ReplayError, ForecastError) as exc:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps({"classification": "FAILED", "reason": str(exc)}, sort_keys=True, indent=2)
            + "\n"
        )
        print(f"classification=FAILED reason={exc}")
        print(
            "M27N2_REQUEST_TYPE: LOCAL_REPLAY_ONLY  M27N2_CREDENTIAL_ACCESS: NO  "
            "M27N2_MUTATION: NO  M27N2_NETWORK_ACTION: NONE"
        )
        return 2

    evidence = result.evidence
    material = {
        "schema": "kalsh3.m27n2.wgrib2-replay.v1",
        "software_version": SOFTWARE_VERSION,
        "classification": "SUCCESS",
        "family_identity": evidence.family_identity,
        "raw_grib_sha256": evidence.raw_grib_sha256,
        "extraction_sha256": evidence.extraction_sha256,
        "wgrib2_version": evidence.wgrib2_version,
        "wgrib2_executable_sha256": result.wgrib2_executable_sha256,
        "record_count": len(evidence.records),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(material, sort_keys=True, indent=2) + "\n")
    print(f"classification=SUCCESS records={len(evidence.records)}")
    print(
        "M27N2_REQUEST_TYPE: LOCAL_REPLAY_ONLY  M27N2_CREDENTIAL_ACCESS: NO  "
        "M27N2_MUTATION: NO  M27N2_NETWORK_ACTION: NONE"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
