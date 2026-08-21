"""Operator-only current-03Z MAXT weather evidence composition.

The frozen parse-verified selector owns public-source enumeration, exact-object selection,
wgrib2 extraction, frozen parsing, and ambiguity policy. This operator composes only the
selector's already-validated ``RawGribEvidence`` into the existing forecast evidence records.
It has no account, credential, signer, store, or mutation capability.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable
from dataclasses import fields
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from scripts.collect_m27c_weather_calibration_coverage import _get
from scripts.select_parse_verified_current_weather_source import (
    ParseVerifiedSelectionResult,
    select_parse_verified_current_source,
)
from services.forecasting.weather_probability import build_current_weather_forecast_evidence

SCHEMA = "kalsh3.forecasting.current-weather-evidence-composition.v1"
SOFTWARE_VERSION = "kalsh3.scripts.run_m27_current_weather_evidence/2"


def _now() -> datetime:
    return datetime.now(UTC)


def _public_transport(url: str) -> bytes:
    """CLI-only adapter to the existing bounded public M27C GET."""
    return _get(url, cache=None)


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
    return {field.name: _value_to_json(getattr(evidence, field.name)) for field in fields(evidence)}


def _selection_to_json(result: ParseVerifiedSelectionResult) -> dict[str, object]:
    """Persist bounded selector provenance without serializing raw GRIB bytes or extraction."""
    selected = result.selected
    return {
        "schema": result.schema,
        "day": result.day.isoformat(),
        "classification": result.classification,
        "reason": result.reason,
        "candidate_names": list(result.candidate_names),
        "candidate_errors": list(result.candidate_errors),
        "blocked_candidate_errors": list(result.blocked_candidate_errors),
        "selected_name": selected.name if selected else None,
        "selected_object_url": selected.url if selected else None,
        "wgrib2_executable_sha256": result.wgrib2_executable_sha256,
        "raw_grib_byte_length": selected.raw_byte_length if selected else None,
        "raw_grib_sha256": selected.raw_sha256 if selected else None,
        "extraction_sha256": selected.extraction_sha256 if selected else None,
        "evidence_family_identity": selected.evidence.family_identity if selected else None,
        "evidence_record_count": len(selected.evidence.records) if selected else None,
    }


def compose(
    day: date,
    *,
    transport: Callable[[str], bytes],
    wgrib2_bin: str | None = None,
) -> dict[str, Any]:
    """Select and bind current-cycle 03Z MAXT weather evidence for ``day``.

    ``transport`` is mandatory: only the CLI explicitly supplies the reviewed public GET
    adapter. The selector performs all wgrib2/parser work; this function never repeats it.
    """
    selection = select_parse_verified_current_source(
        day, transport=transport, wgrib2_bin=wgrib2_bin
    )
    selection_json = _selection_to_json(selection)
    if not selection.succeeded:
        return {
            "schema": SCHEMA,
            "software_version": SOFTWARE_VERSION,
            "classification": selection.classification,
            "reason": selection.reason,
            # Preserve the established top-level key while recording selector-shaped evidence.
            "acquisition": selection_json,
            "records": None,
        }

    selected = selection.selected
    assert selected is not None  # noqa: S101 -- SUCCESS structurally carries provenance.
    evidence = selected.evidence
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
        "acquisition": selection_json,
        "wgrib2_executable_sha256": selection.wgrib2_executable_sha256,
        "extraction_sha256": selected.extraction_sha256,
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
    result = compose(day, transport=_public_transport, wgrib2_bin=args.wgrib2_bin)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(f"classification={result['classification']}")
    print("CREDENTIAL_ACCESS: NO  MUTATION: NO  REQUEST_TYPE: PUBLIC_GET_ONLY")


if __name__ == "__main__":
    main()
