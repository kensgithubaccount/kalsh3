"""M27L operator-only prospective capture boundary for M27C Part 2B3.

This module builds forecast-only prospective observations exclusively from
validated `RawGribRecord` values.  `RawGribEvidence` is a public,
directly-constructible dataclass, so this module never trusts the mere claim
that a given instance passed through `parse_wgrib2_max_t_evidence`: every
consumed evidence object is re-validated here with the same authoritative
`weather_calibration_grib.validate_raw_grib_max_t_evidence` the parser itself
uses, before any field is read from it.  Every timestamp, temperature,
midpoint lead, and model identity in the resulting payload is *read* from the
record rather than accepted as a caller-supplied field, so a payload cannot
claim a horizon or model that its own reference-time-to-midpoint interval
does not actually have.

Persisted evidence is held to the same standard: `deserialize_prospective_observation`
does not merely restore Decimal types and defer to the older M27C validator —
it independently rederives chronology, midpoint, lead, model identity,
Chicago target date, Fahrenheit-from-Kelvin, and evidence identity from the
persisted fields themselves, and fails closed on any mismatch.

The module performs no I/O, has no outcome/residual/evaluation/market
dependency, and never imports GHCN-Daily outcome acquisition.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from services.market_universe.domain import stable_hash

from .domain import ForecastError
from .weather_calibration_grib import (
    RawGribEvidence,
    RawGribRecord,
    kelvin_to_fahrenheit,
    target_local_date,
    validate_kmdw_points,
    validate_raw_grib_max_t_evidence,
)
from .weather_prospective import (
    _EVIDENCE_FIELDS,
    FAMILY,
    FROZEN_MODEL_IDENTITIES,
    GHCND_STATION,
    MEASUREMENT,
    PROTOCOL_IDENTITY,
    PROTOCOL_VERSION,
    SOURCE,
    STATION,
    SUPPORTED_MIDPOINTS,
    validate_prospective_forecast_evidence,
)
from .weather_source_authority import PHYSICAL_WEATHER_SOURCES

POLICY_VERSION = "m27l-part2b3-prospective-capture-v1"
IDENTITY_MATERIAL_VERSION = "m27l-prospective-evidence-identity-v2"

# Frozen internally; not operator-suppliable.  The only wgrib2-version
# authority is `_resolve_wgrib2` in the capture script, which refuses to run
# any executable that does not report exactly the reviewed "3.8.0" string.
EXTRACTION_POLICY_VERSION = "m27c-wgrib2-maxt-extraction-v1"
WGRIB2_VERSION = "3.8.0"

# Frozen NWS station point for KMDW (Chicago Midway Airport), the same
# reviewed geometry already used throughout M27C Part 2A/2B1.  It is a fixed
# physical fact, not a network-fetched value, so this operator-only boundary
# needs no network access to bind captured records to the correct station.
KMDW_LATITUDE = Decimal("41.78417")
KMDW_LONGITUDE = Decimal("-87.75528")

# Only these fields require a Decimal round trip through JSON; every other
# field is already a plain JSON-safe type (str, bool, int) by construction.
_DECIMAL_FIELDS = ("central_kelvin", "central_deg_f", "production_influence")

_REQUIRED_STRING_FIELDS = (
    "source",
    "station",
    "ghcnd_station",
    "measurement",
    "family",
    "raw_grib_sha256",
    "extraction_sha256",
    "extraction_policy_version",
    "wgrib2_version",
    "quality_status",
    "evidence_identity",
    "model_identity",
)

_SHARED_ACROSS_OBSERVATIONS = (
    "forecast_reference_time",
    "collection_timestamp",
    "raw_grib_sha256",
    "extraction_sha256",
    "extraction_policy_version",
    "wgrib2_version",
)

_SOURCE_AUTHORITY = PHYSICAL_WEATHER_SOURCES[SOURCE]
if (
    _SOURCE_AUTHORITY.nws_station_id != STATION
    or _SOURCE_AUTHORITY.ghcnd_station_id != GHCND_STATION
):
    raise ForecastError("prospective capture authority does not match the frozen protocol")
TIMEZONE = _SOURCE_AUTHORITY.timezone


def capture_prospective_forecast_evidence(
    evidence: RawGribEvidence, *, collected_at: datetime
) -> tuple[dict[str, Any], ...]:
    """Derive and validate one forecast-only observation per raw GRIB record.

    `evidence` is independently re-validated against the authoritative 03Z
    MaxT invariants here; the caller's claim that it already passed
    `parse_wgrib2_max_t_evidence` is never trusted.  This function adds the
    frozen KMDW point binding and derives every prospective evidence field
    from `RawGribRecord`, never from a caller-supplied override.

    `collected_at` must be the actual wall-clock capture time (timezone
    aware); it is normalized to UTC and, for every record, must satisfy
    `forecast_reference_time <= collection_timestamp < interval_start` — the
    forecast must have been captured after it was issued and before the
    interval it forecasts had even begun.
    """
    validate_raw_grib_max_t_evidence(evidence)
    if evidence.extraction_policy_version != EXTRACTION_POLICY_VERSION:
        raise ForecastError("prospective capture requires the frozen extraction policy version")
    if collected_at.tzinfo is None:
        raise ForecastError("prospective collection timestamp must be timezone-aware")
    collected_at_utc = collected_at.astimezone(UTC)
    validate_kmdw_points(evidence, KMDW_LATITUDE, KMDW_LONGITUDE)
    return tuple(
        _build_observation(evidence, record, collected_at_utc) for record in evidence.records
    )


def _build_observation(
    evidence: RawGribEvidence, record: RawGribRecord, collected_at_utc: datetime
) -> dict[str, Any]:
    midpoint_seconds = record.lead_to_midpoint_seconds
    model_identity = FROZEN_MODEL_IDENTITIES.get(midpoint_seconds)
    if model_identity is None:
        raise ForecastError("prospective capture midpoint is unsupported")
    if not (record.reference_time <= collected_at_utc < record.interval_start):
        raise ForecastError(
            "prospective collection timestamp is not between the forecast reference "
            "time and the interval start"
        )
    for field, value in (
        ("raw_grib_sha256", evidence.raw_grib_sha256),
        ("extraction_sha256", evidence.extraction_sha256),
        ("extraction_policy_version", evidence.extraction_policy_version),
        ("wgrib2_version", evidence.wgrib2_version),
    ):
        if not isinstance(value, str) or not value:
            raise ForecastError(f"prospective capture {field} is required")
    target_date = target_local_date(record, TIMEZONE)
    central_kelvin = record.kelvin
    central_deg_f = kelvin_to_fahrenheit(central_kelvin)
    collection_timestamp_iso = collected_at_utc.isoformat()
    payload: dict[str, Any] = {
        "evidence_identity": _compute_evidence_identity(
            target_date_iso=target_date.isoformat(),
            midpoint_seconds=midpoint_seconds,
            model_identity=model_identity,
            reference_time_iso=record.reference_time.isoformat(),
            interval_start_iso=record.interval_start.isoformat(),
            interval_end_iso=record.interval_end.isoformat(),
            midpoint_iso=record.midpoint.isoformat(),
            kelvin_str=str(central_kelvin),
            raw_grib_sha256=evidence.raw_grib_sha256,
            extraction_sha256=evidence.extraction_sha256,
            extraction_policy_version=evidence.extraction_policy_version,
            wgrib2_version=evidence.wgrib2_version,
            collection_timestamp_iso=collection_timestamp_iso,
        ),
        "source": SOURCE,
        "station": STATION,
        "ghcnd_station": GHCND_STATION,
        "measurement": MEASUREMENT,
        "family": FAMILY,
        "target_date": target_date.isoformat(),
        "exact_midpoint_seconds": midpoint_seconds,
        "model_identity": model_identity,
        "forecast_reference_time": record.reference_time.isoformat(),
        "interval_start": record.interval_start.isoformat(),
        "interval_end": record.interval_end.isoformat(),
        "midpoint": record.midpoint.isoformat(),
        "collection_timestamp": collection_timestamp_iso,
        "raw_grib_sha256": evidence.raw_grib_sha256,
        "extraction_sha256": evidence.extraction_sha256,
        "extraction_policy_version": evidence.extraction_policy_version,
        "wgrib2_version": evidence.wgrib2_version,
        "central_kelvin": central_kelvin,
        "central_deg_f": central_deg_f,
        "quality_status": "ACCEPTED",
        "missing_source": False,
        "research_only": True,
        "production_influence": Decimal("0"),
    }
    validate_prospective_forecast_evidence(payload)
    return payload


def _compute_evidence_identity(
    *,
    target_date_iso: str,
    midpoint_seconds: int,
    model_identity: str,
    reference_time_iso: str,
    interval_start_iso: str,
    interval_end_iso: str,
    midpoint_iso: str,
    kelvin_str: str,
    raw_grib_sha256: str,
    extraction_sha256: str,
    extraction_policy_version: str,
    wgrib2_version: str,
    collection_timestamp_iso: str,
) -> str:
    # collection_timestamp is safety-bearing (it proves the forecast was
    # captured before its own target interval began), so it is part of the
    # evidence identity: two captures of identical forecast content at
    # different times are different pieces of evidence.  This is the single
    # source of truth for the identity formula: both `_build_observation`
    # (from a RawGribRecord) and `deserialize_prospective_observation` (from
    # recomputed persisted fields) call it, so a stored identity can only
    # ever match a genuinely-recomputed one.
    material = (
        IDENTITY_MATERIAL_VERSION,
        FAMILY,
        target_date_iso,
        midpoint_seconds,
        model_identity,
        reference_time_iso,
        interval_start_iso,
        interval_end_iso,
        midpoint_iso,
        kelvin_str,
        raw_grib_sha256,
        extraction_sha256,
        extraction_policy_version,
        wgrib2_version,
        collection_timestamp_iso,
    )
    return stable_hash(material)


def _parse_utc_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ForecastError(f"prospective persisted {field} is not a string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ForecastError(f"prospective persisted {field} is not a valid ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ForecastError(f"prospective persisted {field} is not canonical UTC")
    if parsed.isoformat() != value:
        raise ForecastError(f"prospective persisted {field} is not canonically formatted")
    return parsed


def _parse_decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, str):
        raise ForecastError(f"prospective persisted {field} is not a string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ForecastError(f"prospective persisted {field} is not a valid decimal") from exc
    if not parsed.is_finite():
        raise ForecastError(f"prospective persisted {field} is non-finite")
    return parsed


def _recompute_target_date(interval_start: datetime, interval_end: datetime) -> date:
    # Reuses `target_local_date` exactly rather than duplicating its Chicago
    # date-mapping logic; only interval_start/interval_end/midpoint/timezone
    # are ever read by it, so a minimal carrier record is sufficient and
    # honest (its other fields are never consulted for this computation).
    shadow = RawGribRecord(
        record_number=1,
        reference_time=interval_start,
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
    return target_local_date(shadow, TIMEZONE)


def serialize_prospective_observation(observation: Mapping[str, Any]) -> dict[str, Any]:
    """Canonical JSON-safe encoding of one already-validated observation."""
    result = dict(observation)
    for field in _DECIMAL_FIELDS:
        result[field] = str(result[field])
    return result


def deserialize_prospective_observation(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Strictly rederive every M27L safety-bearing field from the persisted
    payload itself.

    This does not merely restore Decimal types and defer to the older M27C
    `validate_prospective_forecast_evidence`: chronology, the midpoint, the
    lead, the model identity, the Chicago target date, the Fahrenheit
    conversion, and the evidence identity are all independently recomputed
    from the persisted fields and cross-checked against the stored values.
    Any mismatch, extra/missing field, or malformed type fails closed with
    `ForecastError`.  `validate_prospective_forecast_evidence` still runs at
    the end as a final, unweakened defense — this never loosens it to accept
    a looser JSON shape.
    """
    keys = frozenset(payload)
    if keys != _EVIDENCE_FIELDS:
        raise ForecastError("prospective persisted evidence schema is not forecast-only and exact")
    for field in _REQUIRED_STRING_FIELDS:
        if not isinstance(payload[field], str) or not payload[field]:
            raise ForecastError(f"prospective persisted {field} is required")
    if payload["source"] != SOURCE or payload["station"] != STATION:
        raise ForecastError(
            "prospective persisted source or station conflicts with frozen protocol"
        )
    if payload["ghcnd_station"] != GHCND_STATION or payload["measurement"] != MEASUREMENT:
        raise ForecastError("prospective persisted GHCN-Daily station or measurement conflicts")
    if payload["family"] != FAMILY:
        raise ForecastError("prospective persisted family conflicts with frozen protocol")
    if payload["extraction_policy_version"] != EXTRACTION_POLICY_VERSION:
        raise ForecastError(
            "prospective persisted extraction policy conflicts with frozen protocol"
        )
    if payload["wgrib2_version"] != WGRIB2_VERSION:
        raise ForecastError("prospective persisted wgrib2 version conflicts with frozen protocol")
    if payload["quality_status"] != "ACCEPTED":
        raise ForecastError("prospective persisted quality status conflicts with frozen protocol")
    if payload["missing_source"] is not False:
        raise ForecastError("prospective persisted evidence cannot claim a missing source")
    if payload["research_only"] is not True:
        raise ForecastError("prospective persisted evidence must be research-only")
    exact_midpoint_seconds = payload["exact_midpoint_seconds"]
    if not isinstance(exact_midpoint_seconds, int) or isinstance(exact_midpoint_seconds, bool):
        raise ForecastError("prospective persisted exact_midpoint_seconds is not an integer")
    production_influence = _parse_decimal(payload["production_influence"], "production_influence")
    if production_influence != Decimal("0"):
        raise ForecastError("prospective persisted evidence must have zero influence")
    central_kelvin = _parse_decimal(payload["central_kelvin"], "central_kelvin")
    central_deg_f = _parse_decimal(payload["central_deg_f"], "central_deg_f")
    if central_deg_f != kelvin_to_fahrenheit(central_kelvin):
        raise ForecastError(
            "prospective persisted Fahrenheit does not match the recomputed Kelvin conversion"
        )
    reference_time = _parse_utc_timestamp(
        payload["forecast_reference_time"], "forecast_reference_time"
    )
    if (
        reference_time.hour != 3
        or reference_time.minute != 0
        or reference_time.second != 0
        or reference_time.microsecond != 0
    ):
        raise ForecastError("prospective persisted reference time is not the reviewed 03Z cycle")
    interval_start = _parse_utc_timestamp(payload["interval_start"], "interval_start")
    interval_end = _parse_utc_timestamp(payload["interval_end"], "interval_end")
    midpoint = _parse_utc_timestamp(payload["midpoint"], "midpoint")
    collection_timestamp = _parse_utc_timestamp(
        payload["collection_timestamp"], "collection_timestamp"
    )
    if midpoint != reference_time + timedelta(seconds=exact_midpoint_seconds):
        raise ForecastError(
            "prospective persisted midpoint does not match reference_time + exact_midpoint_seconds"
        )
    if interval_start != midpoint - timedelta(hours=6):
        raise ForecastError("prospective persisted interval_start does not match midpoint - 6h")
    if interval_end != midpoint + timedelta(hours=6):
        raise ForecastError("prospective persisted interval_end does not match midpoint + 6h")
    if not (reference_time <= collection_timestamp < interval_start):
        raise ForecastError(
            "prospective persisted collection timestamp is not between the forecast "
            "reference time and the interval start"
        )
    model_identity = FROZEN_MODEL_IDENTITIES.get(exact_midpoint_seconds)
    if model_identity is None or model_identity != payload["model_identity"]:
        raise ForecastError(
            "prospective persisted model identity does not match the recomputed frozen identity"
        )
    target_date = _recompute_target_date(interval_start, interval_end)
    if target_date.isoformat() != payload["target_date"]:
        raise ForecastError(
            "prospective persisted target date does not match the recomputed Chicago mapping"
        )
    recomputed_identity = _compute_evidence_identity(
        target_date_iso=target_date.isoformat(),
        midpoint_seconds=exact_midpoint_seconds,
        model_identity=model_identity,
        reference_time_iso=reference_time.isoformat(),
        interval_start_iso=interval_start.isoformat(),
        interval_end_iso=interval_end.isoformat(),
        midpoint_iso=midpoint.isoformat(),
        kelvin_str=str(central_kelvin),
        raw_grib_sha256=payload["raw_grib_sha256"],
        extraction_sha256=payload["extraction_sha256"],
        extraction_policy_version=payload["extraction_policy_version"],
        wgrib2_version=payload["wgrib2_version"],
        collection_timestamp_iso=collection_timestamp.isoformat(),
    )
    if recomputed_identity != payload["evidence_identity"]:
        raise ForecastError(
            "prospective persisted evidence identity does not match the recomputed identity"
        )
    result = dict(payload)
    result["central_kelvin"] = central_kelvin
    result["central_deg_f"] = central_deg_f
    result["production_influence"] = production_influence
    validate_prospective_forecast_evidence(result)
    return result


def serialize_prospective_bundle(
    observations: tuple[dict[str, Any], ...],
    *,
    raw_grib_source: Mapping[str, Any],
) -> dict[str, Any]:
    """Canonical JSON-safe encoding of a full captured bundle."""
    return {
        "protocol_version": PROTOCOL_VERSION,
        "protocol_identity": PROTOCOL_IDENTITY,
        "observations": [serialize_prospective_observation(o) for o in observations],
        "raw_grib_source": dict(raw_grib_source),
    }


def deserialize_prospective_bundle(bundle: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Inverse of `serialize_prospective_bundle`.

    Requires exactly three observations, exactly one for each of the three
    frozen midpoints, all sharing one reference time, collection time, and
    raw/extraction provenance (as a single real capture always produces).
    `raw_grib_source` is required and bound/validated against the
    observations' own hash rather than silently ignored.
    """
    if (
        bundle.get("protocol_version") != PROTOCOL_VERSION
        or bundle.get("protocol_identity") != PROTOCOL_IDENTITY
    ):
        raise ForecastError("prospective bundle protocol identity conflicts with frozen protocol")
    raw_observations = bundle.get("observations")
    if not isinstance(raw_observations, list):
        raise ForecastError("prospective bundle observations are malformed")
    if len(raw_observations) != 3:
        raise ForecastError("prospective bundle must contain exactly three observations")
    observations = tuple(deserialize_prospective_observation(item) for item in raw_observations)
    midpoints = tuple(sorted(o["exact_midpoint_seconds"] for o in observations))
    if midpoints != tuple(sorted(SUPPORTED_MIDPOINTS)):
        raise ForecastError(
            "prospective bundle does not contain exactly one observation per frozen midpoint"
        )
    for field in _SHARED_ACROSS_OBSERVATIONS:
        if len({o[field] for o in observations}) != 1:
            raise ForecastError(f"prospective bundle observations disagree on shared {field}")
    raw_grib_source = bundle.get("raw_grib_source")
    if not isinstance(raw_grib_source, dict):
        raise ForecastError("prospective bundle raw_grib_source is required")
    _validate_raw_grib_source(raw_grib_source, observations)
    return observations


def _validate_raw_grib_source(
    source: Mapping[str, Any], observations: tuple[dict[str, Any], ...]
) -> None:
    """Validate bundle-level raw GRIB provenance and bind it to the
    observations' own hash.

    `local_path` is deliberately excluded from validation: it is
    operator-supplied descriptive text with no cryptographic binding to
    anything, and must stay purely informational, never authoritative.
    """
    if not isinstance(source.get("sha256"), str) or not source["sha256"]:
        raise ForecastError("prospective raw_grib_source sha256 is required")
    if any(o["raw_grib_sha256"] != source["sha256"] for o in observations):
        raise ForecastError(
            "prospective raw_grib_source sha256 does not match the observations' raw hash"
        )
    byte_length = source.get("byte_length")
    if not isinstance(byte_length, int) or isinstance(byte_length, bool) or byte_length < 0:
        raise ForecastError("prospective raw_grib_source byte_length is malformed")
    if source.get("family_identity") != FAMILY:
        raise ForecastError("prospective raw_grib_source family conflicts with frozen protocol")
    if source.get("research_only") is not True:
        raise ForecastError("prospective raw_grib_source must be research-only")
    if source.get("production_influence") != "0":
        raise ForecastError("prospective raw_grib_source must have zero influence")
    executable_sha256 = source.get("wgrib2_executable_sha256")
    if not isinstance(executable_sha256, str) or not executable_sha256:
        raise ForecastError("prospective raw_grib_source wgrib2 executable sha256 is required")
