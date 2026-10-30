"""Read-only local October C1 packet gate; never scores outcomes or market edge.

Consumes an operator-exported local directory with frozen GCS object names.
No cloud/source access, changes, outcome fetch, forecast probability or trades.
A zero exit means this structural audit ran without unexplained missing/invalid
objects; it is NOT evidence provenance, weather skill or execution authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from datetime import date
from pathlib import Path
from typing import Any

from services.forecasting.wn_multicity_evaluation_spec import (
    EXPECTED_CITY_IDS,
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
    except (UnicodeError, json.JSONDecodeError, OverflowError, ValueError) as exc:
        raise OctoberAuditError("invalid JSON") from exc
    if not isinstance(value, dict):
        raise OctoberAuditError("JSON root must be an object")
    return value


def _no_links(root: Path, path: Path) -> None:
    if not path.is_relative_to(root):
        raise OctoberAuditError("path outside supplied snapshot")
    cursor = path
    while True:
        if cursor.is_symlink():
            raise OctoberAuditError("symlink in snapshot")
        if cursor == root:
            return
        cursor = cursor.parent


def _read(root: Path, name: str) -> bytes:
    path = root / name
    _no_links(root, path)
    if not path.is_file():
        raise OctoberAuditError("required snapshot file missing")
    if path.stat().st_size > MAX_COMPONENT_BYTES:
        raise OctoberAuditError("snapshot object exceeds research bound")
    raw = path.read_bytes()
    if len(raw) > MAX_COMPONENT_BYTES:
        raise OctoberAuditError("snapshot object exceeds research bound")
    return raw


def _inventory(root: Path) -> list[str]:
    result: list[str] = []
    city_days = root / "city_days"
    _no_links(root, city_days)
    if not city_days.exists():
        return result
    for directory, folders, files in os.walk(city_days, followlinks=False):
        parent = Path(directory)
        _no_links(root, parent)
        for name in folders:
            _no_links(root, parent / name)
        for name in files:
            path = parent / name
            _no_links(root, path)
            result.append(path.relative_to(root).as_posix())
    return sorted(result)


def audit_local_snapshot(root: Path) -> dict[str, Any]:
    """Validate frozen complete denominator and sealed bytes, without outcomes."""
    if not root.is_absolute() or not root.is_dir() or root.is_symlink():
        raise OctoberAuditError("local snapshot root must be a real absolute directory")
    coverage = audit_inventory(_inventory(root), as_of=PROSPECTIVE_END)
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
                failure = _object(_read(root, stem + "/failure.json"))
                if (
                    failure.get("record_type") != "WN-MULTICITY-C1-CLOUD-FAILURE-v1"
                    or failure.get("city_id") != city
                    or failure.get("target_date_utc") != key.target_date.isoformat()
                    or failure.get("no_retry") is not True
                    or failure.get("no_backfill") is not True
                    or failure.get("no_trade") is not True
                ):
                    raise OctoberAuditError("invalid preserved failure receipt")
                continue

            blobs = {name: _read(root, stem + "/" + name) for name in FILES}
            for blob in blobs.values():
                _object(blob)  # Reject ambiguous/nonfinite JSON before frozen parser.
            manifest = _object(_read(root, stem + "/SHA256SUMS.json"))
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
        except (OctoberAuditError, MulticityEvaluationSpecError) as exc:
            # No raw source bytes, forecasts, or market data in diagnostics.
            invalid.append(f"{city}/{day}:{type(exc).__name__}")

    clean_audit = (
        not invalid
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path, help="absolute local export root")
    args = parser.parse_args()
    try:
        report = audit_local_snapshot(args.root)
    except (OctoberAuditError, MulticityEvaluationSpecError) as exc:
        print(json.dumps({"record_type": "WN-MULTICITY-OCTOBER-OFFLINE-GATE-FAILURE-v1",
                          "status": "AUDIT_UNVERIFIED",
                          "reason": type(exc).__name__,
                          "paper_or_trading_authority": "NONE"}, sort_keys=True))
        return 4
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["audit_structurally_complete"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
