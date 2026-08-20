from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from services.forecasting.domain import ForecastError
from services.forecasting.weather_calibration_grib import (
    RawGribEvidence,
    RawGribRecord,
    kelvin_to_fahrenheit,
    parse_wgrib2_max_t_evidence,
    target_local_date,
)
from services.forecasting.weather_prospective import (
    FAMILY,
    FROZEN_MODEL_IDENTITIES,
    GHCND_STATION,
    MEASUREMENT,
    PROSPECTIVE_START,
    SOURCE,
    STATION,
    validate_prospective_forecast_evidence,
)
from services.forecasting.weather_prospective_capture import (
    KMDW_LATITUDE,
    KMDW_LONGITUDE,
    TIMEZONE,
    _build_observation,
    _compute_evidence_identity,
    capture_prospective_forecast_evidence,
    deserialize_prospective_bundle,
    deserialize_prospective_observation,
    serialize_prospective_bundle,
    serialize_prospective_observation,
)

_OFFSETS = ((9, 21), (33, 45), (57, 69))
_REFERENCE = datetime(2026, 9, 1, 3, 0, 0, tzinfo=UTC)
# Record 1's interval starts 9h after reference (12:00Z); every valid
# collection timestamp in these tests must fall in [reference, interval_start).
_COLLECTED_AT = datetime(2026, 9, 1, 4, 0, 0, tzinfo=UTC)
_GRID = {
    "grid_template": 30,
    "nx": 2145,
    "ny": 1377,
    "dx": Decimal("2539.703"),
    "dy": Decimal("2539.703"),
}


def _extraction(
    *,
    reference: str = "20260901030000",
    lat: str = "41.794091",
    lon: str = "272.260017",
    values: tuple[str, str, str] = ("302", "307", "309.3"),
    end_override: dict[int, str] | None = None,
) -> str:
    ref_dt = datetime.strptime(reference, "%Y%m%d%H%M%S")
    end_override = end_override or {}
    sections = []
    for number, (start_h, end_h), value in zip((1, 2, 3), _OFFSETS, values, strict=True):
        start = (ref_dt + timedelta(hours=start_h)).strftime("%Y%m%d%H%M%S")
        end = end_override.get(number, (ref_dt + timedelta(hours=end_h)).strftime("%Y%m%d%H%M%S"))
        sections.append(
            f"""record {number}:
reference = {reference}
variable = TMAX
level = 2 m above ground
generating_process = 2
statistical_process = 2
time_processing = 2
parameter = 0/0/4
unit = Kelvin
start = {start}
end = {end}
verification = {end}
lat = {lat}
lon = {lon}
value = {value}
grid_template = 30
nx = 2145
ny = 1377
dx = 2539.703
dy = 2539.703
"""
        )
    return "\n".join(sections)


def _evidence(**kwargs: object) -> RawGribEvidence:
    text = _extraction()
    return parse_wgrib2_max_t_evidence(
        text,
        raw_grib_sha256=kwargs.pop("raw_grib_sha256", "raw-sha-1"),  # type: ignore[arg-type]
        extraction_sha256=kwargs.pop("extraction_sha256", "extraction-sha-1"),  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


def _record(
    number: int, start_hours: int, end_hours: int, kelvin: str, *, reference: datetime = _REFERENCE
) -> RawGribRecord:
    start = reference + timedelta(hours=start_hours)
    end = reference + timedelta(hours=end_hours)
    return RawGribRecord(
        record_number=number,
        reference_time=reference,
        variable="TMAX",
        level="2 m above ground",
        generating_process_code=2,
        statistical_process_code=2,
        time_processing_code=2,
        parameter=(0, 0, 4),
        unit="Kelvin",
        interval_start=start,
        interval_end=end,
        verification_time=end,
        latitude=Decimal("41.794091"),
        raw_longitude=Decimal("272.260017"),
        signed_longitude=Decimal("-87.739983"),
        kelvin=Decimal(kelvin),
        **_GRID,  # type: ignore[arg-type]
    )


def _valid_records() -> tuple[RawGribRecord, RawGribRecord, RawGribRecord]:
    return (_record(1, 9, 21, "302"), _record(2, 33, 45, "307"), _record(3, 57, 69, "309.3"))


def _direct_evidence(records: tuple[RawGribRecord, ...]) -> RawGribEvidence:
    return RawGribEvidence(FAMILY, records, "policy-v1", "3.8.0", "raw-hash", "extract-hash")


_RAW_GRIB_SOURCE: dict[str, Any] = {
    "local_path": "example.grib2",
    "byte_length": 12345,
    "sha256": "raw-sha-1",
    "family_identity": FAMILY,
    "research_only": True,
    "production_influence": "0",
    "wgrib2_executable_sha256": "exe-sha-1",
}


def _bundle() -> dict[str, Any]:
    observations = capture_prospective_forecast_evidence(_evidence(), collected_at=_COLLECTED_AT)
    return serialize_prospective_bundle(observations, raw_grib_source=dict(_RAW_GRIB_SOURCE))


# ---------------------------------------------------------------------------
# Core derivation behavior
# ---------------------------------------------------------------------------


def test_three_valid_records_produce_three_observations() -> None:
    observations = capture_prospective_forecast_evidence(_evidence(), collected_at=_COLLECTED_AT)
    assert len(observations) == 3


def test_actual_leads_map_to_frozen_model_identities() -> None:
    observations = capture_prospective_forecast_evidence(_evidence(), collected_at=_COLLECTED_AT)
    assert [o["exact_midpoint_seconds"] for o in observations] == [54_000, 140_400, 226_800]
    assert [o["model_identity"] for o in observations] == [
        FROZEN_MODEL_IDENTITIES[54_000],
        FROZEN_MODEL_IDENTITIES[140_400],
        FROZEN_MODEL_IDENTITIES[226_800],
    ]


def test_target_dates_derive_from_reviewed_chicago_mapper() -> None:
    assert TIMEZONE == "America/Chicago"
    evidence = _evidence()
    observations = capture_prospective_forecast_evidence(evidence, collected_at=_COLLECTED_AT)
    expected = [target_local_date(record, TIMEZONE).isoformat() for record in evidence.records]
    assert [o["target_date"] for o in observations] == expected
    assert expected == ["2026-09-01", "2026-09-02", "2026-09-03"]


def test_all_timestamps_derive_from_raw_grib_record() -> None:
    evidence = _evidence()
    observations = capture_prospective_forecast_evidence(evidence, collected_at=_COLLECTED_AT)
    for record, observation in zip(evidence.records, observations, strict=True):
        assert observation["forecast_reference_time"] == record.reference_time.isoformat()
        assert observation["interval_start"] == record.interval_start.isoformat()
        assert observation["interval_end"] == record.interval_end.isoformat()
        assert observation["midpoint"] == record.midpoint.isoformat()
        assert observation["exact_midpoint_seconds"] == record.lead_to_midpoint_seconds


def test_central_kelvin_and_fahrenheit_derive_from_record() -> None:
    evidence = _evidence()
    observations = capture_prospective_forecast_evidence(evidence, collected_at=_COLLECTED_AT)
    for record, observation in zip(evidence.records, observations, strict=True):
        assert observation["central_kelvin"] == record.kelvin
        assert observation["central_deg_f"] == kelvin_to_fahrenheit(record.kelvin)


def test_raw_and_extraction_hash_and_wgrib2_identity_preserved() -> None:
    evidence = _evidence(
        raw_grib_sha256="raw-sha-xyz",
        extraction_sha256="extraction-sha-xyz",
        wgrib2_version="3.8.0",
    )
    observations = capture_prospective_forecast_evidence(evidence, collected_at=_COLLECTED_AT)
    for observation in observations:
        assert observation["raw_grib_sha256"] == "raw-sha-xyz"
        assert observation["extraction_sha256"] == "extraction-sha-xyz"
        assert observation["extraction_policy_version"] == evidence.extraction_policy_version
        assert observation["wgrib2_version"] == "3.8.0"


def test_wrong_grib_family_fails() -> None:
    evidence = _evidence()
    wrong = RawGribEvidence(
        "LEGACY_CHICAGO_MAXT_5KM_YGFZ98",
        evidence.records,
        evidence.extraction_policy_version,
        evidence.wgrib2_version,
        evidence.raw_grib_sha256,
        evidence.extraction_sha256,
    )
    with pytest.raises(ForecastError, match="family"):
        capture_prospective_forecast_evidence(wrong, collected_at=_COLLECTED_AT)


def test_wrong_03z_cycle_fails() -> None:
    with pytest.raises(ForecastError):
        parse_wgrib2_max_t_evidence(_extraction(reference="20260901020000"))


def test_malformed_intervals_fail() -> None:
    text = _extraction(end_override={1: "20260902010000"})
    with pytest.raises(ForecastError):
        parse_wgrib2_max_t_evidence(text)


def test_wrong_kmdw_point_fails() -> None:
    text = _extraction(lat="40.712776", lon="285.94")
    evidence = parse_wgrib2_max_t_evidence(
        text, raw_grib_sha256="raw-sha-1", extraction_sha256="extraction-sha-1"
    )
    with pytest.raises(ForecastError, match="KMDW"):
        capture_prospective_forecast_evidence(evidence, collected_at=_COLLECTED_AT)


def test_caller_cannot_override_timestamp_model_temperature_source_hash() -> None:
    parameters = inspect.signature(capture_prospective_forecast_evidence).parameters
    assert set(parameters) == {"evidence", "collected_at"}
    assert parameters["evidence"].annotation in ("RawGribEvidence", RawGribEvidence)


def test_outcome_residual_evaluation_and_market_fields_cannot_appear() -> None:
    observations = capture_prospective_forecast_evidence(_evidence(), collected_at=_COLLECTED_AT)
    forbidden = (
        "observed_deg_f",
        "outcome",
        "residual",
        "residual_deg_f",
        "crps",
        "coverage",
        "evaluation",
        "market",
        "market_data",
        "price",
        "fair_value",
        "edge",
        "ev",
    )
    for observation in observations:
        for field in forbidden:
            assert field not in observation
        validate_prospective_forecast_evidence(observation)


def test_august_31_target_is_rejected_september_1_is_accepted() -> None:
    aug31 = _extraction(reference="20260831030000")
    # record 1's interval_start is 20260831T12:00:00Z; a collection time inside
    # [reference, interval_start) is required to even reach the target-date check.
    aug31_collected_at = datetime(2026, 8, 31, 4, 0, 0, tzinfo=UTC)
    with pytest.raises(ForecastError, match="outside"):
        capture_prospective_forecast_evidence(
            parse_wgrib2_max_t_evidence(
                aug31, raw_grib_sha256="raw-sha-1", extraction_sha256="extraction-sha-1"
            ),
            collected_at=aug31_collected_at,
        )
    sep_evidence = _evidence()
    (first, *_rest) = capture_prospective_forecast_evidence(
        sep_evidence, collected_at=_COLLECTED_AT
    )
    assert first["target_date"] == PROSPECTIVE_START.isoformat() == "2026-09-01"


def test_research_only_and_production_influence_always_zero() -> None:
    observations = capture_prospective_forecast_evidence(_evidence(), collected_at=_COLLECTED_AT)
    for observation in observations:
        assert observation["research_only"] is True
        assert observation["production_influence"] == Decimal("0")


def test_source_station_ghcnd_measurement_family_are_frozen_constants() -> None:
    observations = capture_prospective_forecast_evidence(_evidence(), collected_at=_COLLECTED_AT)
    for observation in observations:
        assert observation["source"] == SOURCE == "CLIMDW"
        assert observation["station"] == STATION == "KMDW"
        assert observation["ghcnd_station"] == GHCND_STATION == "USW00014819"
        assert observation["measurement"] == MEASUREMENT == "DAILY_MAX"
        assert observation["family"] == FAMILY


def test_kmdw_point_constants_match_reviewed_fixture() -> None:
    assert Decimal("41.78417") == KMDW_LATITUDE
    assert Decimal("-87.75528") == KMDW_LONGITUDE


# ---------------------------------------------------------------------------
# BLOCKER 1 — authoritative capture time
# ---------------------------------------------------------------------------


def test_collection_before_reference_time_fails() -> None:
    too_early = _REFERENCE - timedelta(seconds=1)
    with pytest.raises(ForecastError, match="collection timestamp"):
        capture_prospective_forecast_evidence(_evidence(), collected_at=too_early)


def test_collection_at_or_after_interval_start_fails() -> None:
    record1_interval_start = _REFERENCE + timedelta(hours=9)
    for at_or_after in (record1_interval_start, record1_interval_start + timedelta(seconds=1)):
        with pytest.raises(ForecastError, match="collection timestamp"):
            capture_prospective_forecast_evidence(_evidence(), collected_at=at_or_after)


def test_collection_timestamp_is_normalized_to_utc() -> None:
    # Same instant as _COLLECTED_AT (2026-09-01T04:00:00Z), expressed in a
    # different timezone.
    chicago_time = datetime(2026, 8, 31, 23, 0, 0, tzinfo=ZoneInfo("America/Chicago"))
    assert chicago_time.astimezone(UTC) == _COLLECTED_AT
    (observation, *_rest) = capture_prospective_forecast_evidence(
        _evidence(), collected_at=chicago_time
    )
    assert observation["collection_timestamp"] == "2026-09-01T04:00:00+00:00"


def test_naive_collection_timestamp_rejected() -> None:
    with pytest.raises(ForecastError, match="timezone-aware"):
        capture_prospective_forecast_evidence(_evidence(), collected_at=datetime(2026, 9, 1, 4, 0))


def test_no_cli_parameter_or_environment_mechanism_for_capture_time() -> None:
    """The private clock seam `_now` is the only source of collection time;
    the operator-facing argparse surface has no flag for it."""
    from scripts.capture_m27l_prospective_forecast import main

    source = inspect.getsource(main)
    assert "collected-at" not in source
    assert "collected_at" not in source
    assert "os.environ" not in Path("scripts/capture_m27l_prospective_forecast.py").read_text()


# ---------------------------------------------------------------------------
# BLOCKER 2 — raw GRIB authority revalidation (direct-construction attacks)
# ---------------------------------------------------------------------------


def test_fewer_than_three_records_fails() -> None:
    with pytest.raises(ForecastError, match="exactly three"):
        capture_prospective_forecast_evidence(
            _direct_evidence(_valid_records()[:2]), collected_at=_COLLECTED_AT
        )


def test_more_than_three_records_fails() -> None:
    extra = (*_valid_records(), _record(4, 81, 93, "310"))
    with pytest.raises(ForecastError, match="exactly three"):
        capture_prospective_forecast_evidence(_direct_evidence(extra), collected_at=_COLLECTED_AT)


def test_record_numbering_not_1_2_3_fails() -> None:
    r1, r2, r3 = _valid_records()
    bad = (r1, r2, replace(r3, record_number=4))
    with pytest.raises(ForecastError, match="numbering"):
        capture_prospective_forecast_evidence(_direct_evidence(bad), collected_at=_COLLECTED_AT)


def test_non_03z_reference_fails() -> None:
    off_cycle_reference = _REFERENCE.replace(hour=2)
    records = tuple(
        _record(number, start, end, kelvin, reference=off_cycle_reference)
        for number, (start, end), kelvin in zip(
            (1, 2, 3), _OFFSETS, ("302", "307", "309.3"), strict=True
        )
    )
    with pytest.raises(ForecastError, match="03Z"):
        capture_prospective_forecast_evidence(_direct_evidence(records), collected_at=_COLLECTED_AT)


def test_inconsistent_reference_times_across_records_fails() -> None:
    r1, r2, r3 = _valid_records()
    bad = (r1, replace(r2, reference_time=_REFERENCE + timedelta(hours=1)), r3)
    with pytest.raises(ForecastError, match="different reference"):
        capture_prospective_forecast_evidence(_direct_evidence(bad), collected_at=_COLLECTED_AT)


def test_wrong_interval_offsets_fails() -> None:
    """Record #1 claims record #2's (39h) horizon instead of its own (15h)."""
    _r1, r2, r3 = _valid_records()
    swapped_first = _record(1, 33, 45, "302")
    with pytest.raises(ForecastError):
        capture_prospective_forecast_evidence(
            _direct_evidence((swapped_first, r2, r3)), collected_at=_COLLECTED_AT
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("variable", "TMIN"),
        ("level", "surface"),
        ("generating_process_code", 0),
        ("statistical_process_code", 3),
        ("time_processing_code", 1),
        ("parameter", (0, 0, 5)),
        ("unit", "Celsius"),
    ),
)
def test_wrong_variable_level_process_parameter_unit_fails(field: str, value: object) -> None:
    r1, r2, r3 = _valid_records()
    bad = (replace(r1, **{field: value}), r2, r3)
    with pytest.raises(ForecastError):
        capture_prospective_forecast_evidence(_direct_evidence(bad), collected_at=_COLLECTED_AT)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("grid_template", 31),
        ("nx", 2144),
        ("ny", 1376),
        ("dx", Decimal("2539.704")),
        ("dy", Decimal("2539.704")),
    ),
)
def test_wrong_grid_signature_fails(field: str, value: object) -> None:
    r1, r2, r3 = _valid_records()
    bad = (replace(r1, **{field: value}), r2, r3)
    with pytest.raises(ForecastError):
        capture_prospective_forecast_evidence(_direct_evidence(bad), collected_at=_COLLECTED_AT)


def test_overlapping_intervals_fails() -> None:
    """The three canonical offset windows ((9,21),(33,45),(57,69)) are fully
    disjoint with a 12h gap between each, so any attempt to make two
    intervals overlap necessarily also breaks that record's own fixed
    start/end horizon check first — an even stricter rejection than the
    dedicated overlap check. Either way, the attack must fail closed.
    """
    r1, r2, r3 = _valid_records()
    overlapping = (r1, replace(r2, interval_start=r1.interval_end - timedelta(hours=1)), r3)
    with pytest.raises(ForecastError):
        capture_prospective_forecast_evidence(
            _direct_evidence(overlapping), collected_at=_COLLECTED_AT
        )


def test_internal_midpoint_guard_is_defense_in_depth() -> None:
    """Unreachable via the public `capture_prospective_forecast_evidence`
    entrypoint once `validate_raw_grib_max_t_evidence` has run (it pins every
    record's horizon to one of the three frozen offsets) — but the internal
    builder still defends independently, in case that invariant ever drifts.
    """
    off_schedule = replace(
        _record(1, 9, 21, "302"),
        interval_start=_REFERENCE + timedelta(hours=2),
        interval_end=_REFERENCE + timedelta(hours=4),
    )
    evidence = _direct_evidence((off_schedule,))
    with pytest.raises(ForecastError, match="unsupported"):
        _build_observation(evidence, off_schedule, _COLLECTED_AT)


# ---------------------------------------------------------------------------
# IMPORTANT 1 — evidence identity includes collection time
# ---------------------------------------------------------------------------


def test_evidence_identity_is_deterministic_for_identical_input() -> None:
    evidence = _evidence()
    first = capture_prospective_forecast_evidence(evidence, collected_at=_COLLECTED_AT)
    second = capture_prospective_forecast_evidence(evidence, collected_at=_COLLECTED_AT)
    assert [o["evidence_identity"] for o in first] == [o["evidence_identity"] for o in second]
    assert len({o["evidence_identity"] for o in first}) == 3


def test_evidence_identity_varies_with_collection_time() -> None:
    evidence = _evidence()
    later_valid_collected_at = _COLLECTED_AT + timedelta(hours=1)
    assert later_valid_collected_at < _REFERENCE + timedelta(hours=9)
    first = capture_prospective_forecast_evidence(evidence, collected_at=_COLLECTED_AT)
    second = capture_prospective_forecast_evidence(evidence, collected_at=later_valid_collected_at)
    assert [o["evidence_identity"] for o in first] != [o["evidence_identity"] for o in second]
    assert [o["collection_timestamp"] for o in first] != [o["collection_timestamp"] for o in second]


# ---------------------------------------------------------------------------
# BLOCKER 4 — serialization round trip (in-memory)
# ---------------------------------------------------------------------------


def test_observation_serialize_deserialize_round_trip() -> None:
    observations = capture_prospective_forecast_evidence(_evidence(), collected_at=_COLLECTED_AT)
    for observation in observations:
        serialized = serialize_prospective_observation(observation)
        assert isinstance(serialized["central_kelvin"], str)
        assert isinstance(serialized["production_influence"], str)
        restored = deserialize_prospective_observation(serialized)
        assert restored == observation
        validate_prospective_forecast_evidence(restored)


def test_bundle_serialize_deserialize_round_trip_validates_every_observation() -> None:
    observations = capture_prospective_forecast_evidence(_evidence(), collected_at=_COLLECTED_AT)
    bundle = serialize_prospective_bundle(observations, raw_grib_source=dict(_RAW_GRIB_SOURCE))
    restored = deserialize_prospective_bundle(bundle)
    assert restored == observations
    for observation in restored:
        validate_prospective_forecast_evidence(observation)


def test_deserialize_bundle_rejects_protocol_identity_mismatch() -> None:
    bundle = _bundle()
    bundle["protocol_identity"] = "tampered"
    with pytest.raises(ForecastError, match="protocol identity"):
        deserialize_prospective_bundle(bundle)


# ---------------------------------------------------------------------------
# PERSISTED EVIDENCE AUTHORITY — strict re-derivation, not mere Decimal-cast
# ---------------------------------------------------------------------------


def test_valid_bundle_deserializes_cleanly() -> None:
    observations = deserialize_prospective_bundle(_bundle())
    assert len(observations) == 3
    assert {o["exact_midpoint_seconds"] for o in observations} == {54_000, 140_400, 226_800}


def test_deleting_one_horizon_fails() -> None:
    bundle = _bundle()
    bundle["observations"] = bundle["observations"][:2]
    with pytest.raises(ForecastError, match="exactly three"):
        deserialize_prospective_bundle(bundle)


def test_duplicating_a_horizon_fails() -> None:
    bundle = _bundle()
    bundle["observations"] = [
        bundle["observations"][0],
        bundle["observations"][0],
        bundle["observations"][2],
    ]
    with pytest.raises(ForecastError, match="one observation per frozen midpoint"):
        deserialize_prospective_bundle(bundle)


def test_inserting_an_extra_observation_fails() -> None:
    bundle = _bundle()
    bundle["observations"] = [*bundle["observations"], bundle["observations"][0]]
    with pytest.raises(ForecastError, match="exactly three"):
        deserialize_prospective_bundle(bundle)


@pytest.mark.parametrize(
    "field",
    (
        "forecast_reference_time",
        "interval_start",
        "interval_end",
        "midpoint",
        "collection_timestamp",
    ),
)
def test_mutating_a_timestamp_fails(field: str) -> None:
    bundle = _bundle()
    tampered = dict(bundle["observations"][0])
    tampered[field] = "2026-01-01T00:00:00+00:00"
    bundle["observations"] = [tampered, bundle["observations"][1], bundle["observations"][2]]
    with pytest.raises(ForecastError):
        deserialize_prospective_bundle(bundle)


def test_non_canonical_utc_timestamp_fails() -> None:
    """A timestamp with a non-zero UTC offset, or one not in Python's exact
    canonical isoformat() rendering, must fail even if it denotes a valid
    instant."""
    bundle = _bundle()
    tampered = dict(bundle["observations"][0])
    original = tampered["forecast_reference_time"]
    assert original.endswith("+00:00")
    tampered["forecast_reference_time"] = original.replace("+00:00", "+00:00Z")
    bundle["observations"] = [tampered, bundle["observations"][1], bundle["observations"][2]]
    with pytest.raises(ForecastError):
        deserialize_prospective_bundle(bundle)


def test_mutating_kelvin_fails() -> None:
    """Kelvin and its matching Fahrenheit are both tampered consistently, so
    the Fahrenheit-conversion check passes and the recomputed evidence
    identity is what catches the substitution."""
    bundle = _bundle()
    tampered = dict(bundle["observations"][0])
    tampered_kelvin = Decimal("999")
    tampered["central_kelvin"] = str(tampered_kelvin)
    tampered["central_deg_f"] = str(kelvin_to_fahrenheit(tampered_kelvin))
    bundle["observations"] = [tampered, bundle["observations"][1], bundle["observations"][2]]
    with pytest.raises(ForecastError, match="evidence identity"):
        deserialize_prospective_bundle(bundle)


def test_mutating_fahrenheit_without_kelvin_fails() -> None:
    bundle = _bundle()
    tampered = dict(bundle["observations"][0])
    tampered["central_deg_f"] = "999"
    bundle["observations"] = [tampered, bundle["observations"][1], bundle["observations"][2]]
    with pytest.raises(ForecastError, match="Fahrenheit"):
        deserialize_prospective_bundle(bundle)


def test_mutating_evidence_identity_fails() -> None:
    bundle = _bundle()
    tampered = dict(bundle["observations"][0])
    tampered["evidence_identity"] = "0" * 64
    bundle["observations"] = [tampered, bundle["observations"][1], bundle["observations"][2]]
    with pytest.raises(ForecastError, match="evidence identity"):
        deserialize_prospective_bundle(bundle)


def test_mutating_quality_status_fails() -> None:
    bundle = _bundle()
    tampered = dict(bundle["observations"][0])
    tampered["quality_status"] = "REJECTED"
    bundle["observations"] = [tampered, bundle["observations"][1], bundle["observations"][2]]
    with pytest.raises(ForecastError, match="quality status"):
        deserialize_prospective_bundle(bundle)


def test_mutating_exact_midpoint_seconds_fails() -> None:
    bundle = _bundle()
    tampered = dict(bundle["observations"][0])
    tampered["exact_midpoint_seconds"] = 140_400
    bundle["observations"] = [tampered, bundle["observations"][1], bundle["observations"][2]]
    with pytest.raises(ForecastError):
        deserialize_prospective_bundle(bundle)


def test_mutating_model_identity_fails() -> None:
    bundle = _bundle()
    tampered = dict(bundle["observations"][0])
    tampered["model_identity"] = FROZEN_MODEL_IDENTITIES[140_400]
    bundle["observations"] = [tampered, bundle["observations"][1], bundle["observations"][2]]
    with pytest.raises(ForecastError, match="model identity"):
        deserialize_prospective_bundle(bundle)


def test_mutating_target_date_fails() -> None:
    bundle = _bundle()
    tampered = dict(bundle["observations"][0])
    tampered["target_date"] = "2026-09-02"
    bundle["observations"] = [tampered, bundle["observations"][1], bundle["observations"][2]]
    with pytest.raises(ForecastError, match="target date"):
        deserialize_prospective_bundle(bundle)


def test_observations_disagreeing_on_shared_fields_fails() -> None:
    bundle = _bundle()
    tampered = dict(bundle["observations"][0])
    tampered["raw_grib_sha256"] = "different-sha"
    tampered["evidence_identity"] = "0" * 64  # would fail identity anyway; prove shared-field too
    bundle["observations"] = [tampered, bundle["observations"][1], bundle["observations"][2]]
    with pytest.raises(ForecastError):
        deserialize_prospective_bundle(bundle)


def test_extra_or_missing_field_fails() -> None:
    bundle = _bundle()
    with_extra = dict(bundle["observations"][0])
    with_extra["unexpected_field"] = "x"
    bundle["observations"] = [with_extra, bundle["observations"][1], bundle["observations"][2]]
    with pytest.raises(ForecastError, match="exact"):
        deserialize_prospective_bundle(bundle)

    bundle2 = _bundle()
    missing_field = dict(bundle2["observations"][0])
    del missing_field["central_kelvin"]
    bundle2["observations"] = [
        missing_field,
        bundle2["observations"][1],
        bundle2["observations"][2],
    ]
    with pytest.raises(ForecastError):
        deserialize_prospective_bundle(bundle2)


def test_malformed_type_fails() -> None:
    bundle = _bundle()
    tampered = dict(bundle["observations"][0])
    tampered["exact_midpoint_seconds"] = "54000"  # string, not int
    bundle["observations"] = [tampered, bundle["observations"][1], bundle["observations"][2]]
    with pytest.raises(ForecastError):
        deserialize_prospective_bundle(bundle)


# ---------------------------------------------------------------------------
# EXACT EXTRACTION POLICY
# ---------------------------------------------------------------------------


def test_capture_rejects_non_frozen_extraction_policy_version() -> None:
    r1, r2, r3 = _valid_records()
    evidence = RawGribEvidence(
        FAMILY, (r1, r2, r3), "some-other-nonempty-policy", "3.8.0", "raw-hash", "extract-hash"
    )
    with pytest.raises(ForecastError, match="extraction policy"):
        capture_prospective_forecast_evidence(evidence, collected_at=_COLLECTED_AT)


def test_persisted_wrong_extraction_policy_version_fails() -> None:
    bundle = _bundle()
    tampered_observations = []
    for observation in bundle["observations"]:
        tampered = dict(observation)
        tampered["extraction_policy_version"] = "some-other-nonempty-policy"
        tampered_observations.append(tampered)
    bundle["observations"] = tampered_observations
    with pytest.raises(ForecastError, match="extraction policy"):
        deserialize_prospective_bundle(bundle)


# ---------------------------------------------------------------------------
# ADVERSARIAL: fully self-consistent forged bundles, with a genuinely
# recomputed evidence_identity for the forged material.  These must fail for
# the *specific* reviewed-cycle / exact-integer-offset reason, not merely
# because a stale identity was left behind.
# ---------------------------------------------------------------------------


def _reidentify(observation: dict[str, Any]) -> dict[str, Any]:
    """Recompute evidence_identity to exactly match `observation`'s own
    (possibly forged) material, so a test attacking some other field cannot
    accidentally pass or fail merely because of an unrelated identity
    mismatch."""
    tampered = dict(observation)
    tampered["evidence_identity"] = _compute_evidence_identity(
        target_date_iso=tampered["target_date"],
        midpoint_seconds=tampered["exact_midpoint_seconds"],
        model_identity=tampered["model_identity"],
        reference_time_iso=tampered["forecast_reference_time"],
        interval_start_iso=tampered["interval_start"],
        interval_end_iso=tampered["interval_end"],
        midpoint_iso=tampered["midpoint"],
        kelvin_str=str(Decimal(tampered["central_kelvin"])),
        raw_grib_sha256=tampered["raw_grib_sha256"],
        extraction_sha256=tampered["extraction_sha256"],
        extraction_policy_version=tampered["extraction_policy_version"],
        wgrib2_version=tampered["wgrib2_version"],
        collection_timestamp_iso=tampered["collection_timestamp"],
    )
    return tampered


def test_persisted_self_consistent_02z_schedule_with_recomputed_identity_fails() -> None:
    """Shift the ENTIRE schedule (reference, interval, midpoint) by one hour
    to an off-cycle 02Z reference, recompute a genuinely matching
    evidence_identity and target_date for the forged material, and confirm
    it still fails — only because reference_time is pinned to exactly the
    reviewed 03Z cycle."""
    bundle = _bundle()
    original = bundle["observations"][0]
    shift = timedelta(hours=1)
    fake_reference = datetime.fromisoformat(original["forecast_reference_time"]) - shift
    fake_midpoint = datetime.fromisoformat(original["midpoint"]) - shift
    fake_interval_start = datetime.fromisoformat(original["interval_start"]) - shift
    fake_interval_end = datetime.fromisoformat(original["interval_end"]) - shift
    assert fake_reference.hour == 2

    tampered = dict(original)
    tampered["forecast_reference_time"] = fake_reference.isoformat()
    tampered["midpoint"] = fake_midpoint.isoformat()
    tampered["interval_start"] = fake_interval_start.isoformat()
    tampered["interval_end"] = fake_interval_end.isoformat()
    fake_local_date = fake_interval_start.astimezone(ZoneInfo("America/Chicago")).date()
    tampered["target_date"] = fake_local_date.isoformat()
    tampered = _reidentify(tampered)

    bundle["observations"] = [tampered, bundle["observations"][1], bundle["observations"][2]]
    with pytest.raises(ForecastError, match="03Z"):
        deserialize_prospective_bundle(bundle)


def test_persisted_self_consistent_fractional_offset_with_integer_midpoint_field_fails() -> None:
    """Shift the interval/midpoint (but not reference_time) by exactly 500
    microseconds while still claiming the supported integer
    exact_midpoint_seconds=54000, recompute a genuinely matching
    evidence_identity for the forged material, and confirm it still fails —
    only because midpoint is checked for EXACT equality to
    reference_time + timedelta(seconds=exact_midpoint_seconds), never via a
    lossy int(total_seconds()) derivation."""
    bundle = _bundle()
    original = bundle["observations"][0]
    assert original["exact_midpoint_seconds"] == 54_000
    shift = timedelta(microseconds=500_000)
    fake_midpoint = datetime.fromisoformat(original["midpoint"]) + shift
    fake_interval_start = datetime.fromisoformat(original["interval_start"]) + shift
    fake_interval_end = datetime.fromisoformat(original["interval_end"]) + shift

    tampered = dict(original)
    tampered["midpoint"] = fake_midpoint.isoformat()
    tampered["interval_start"] = fake_interval_start.isoformat()
    tampered["interval_end"] = fake_interval_end.isoformat()
    # exact_midpoint_seconds is left exactly as the original supported 54000
    tampered = _reidentify(tampered)

    bundle["observations"] = [tampered, bundle["observations"][1], bundle["observations"][2]]
    with pytest.raises(ForecastError, match="midpoint"):
        deserialize_prospective_bundle(bundle)


# ---------------------------------------------------------------------------
# PROVENANCE CONSISTENCY
# ---------------------------------------------------------------------------


def test_raw_grib_source_sha256_must_match_observations() -> None:
    bundle = _bundle()
    bundle["raw_grib_source"]["sha256"] = "different-sha"
    with pytest.raises(ForecastError, match="raw hash"):
        deserialize_prospective_bundle(bundle)


def test_raw_grib_source_required_on_load() -> None:
    bundle = _bundle()
    del bundle["raw_grib_source"]
    with pytest.raises(ForecastError, match="raw_grib_source"):
        deserialize_prospective_bundle(bundle)


def test_raw_grib_source_family_and_influence_are_validated() -> None:
    bundle = _bundle()
    bundle["raw_grib_source"]["family_identity"] = "LEGACY_CHICAGO_MAXT_5KM_YGFZ98"
    with pytest.raises(ForecastError, match="family"):
        deserialize_prospective_bundle(bundle)

    bundle2 = _bundle()
    bundle2["raw_grib_source"]["production_influence"] = "0.01"
    with pytest.raises(ForecastError, match="zero influence"):
        deserialize_prospective_bundle(bundle2)


def test_raw_grib_source_local_path_is_not_authoritative() -> None:
    """local_path can be any string; it is never checked against anything."""
    bundle = _bundle()
    bundle["raw_grib_source"]["local_path"] = "/completely/different/unverified/path.grib2"
    deserialize_prospective_bundle(bundle)  # must not raise


def test_raw_grib_source_missing_wgrib2_executable_sha256_fails() -> None:
    bundle = _bundle()
    del bundle["raw_grib_source"]["wgrib2_executable_sha256"]
    with pytest.raises(ForecastError, match="executable"):
        deserialize_prospective_bundle(bundle)

    bundle2 = _bundle()
    bundle2["raw_grib_source"]["wgrib2_executable_sha256"] = ""
    with pytest.raises(ForecastError, match="executable"):
        deserialize_prospective_bundle(bundle2)


# ---------------------------------------------------------------------------
# Source scan
# ---------------------------------------------------------------------------


def test_no_production_execution_credential_or_outcome_imports_by_source_scan() -> None:
    source = Path("services/forecasting/weather_prospective_capture.py").read_text()
    forbidden = (
        "production_execution",
        "credential",
        "risk_engine",
        "parse_ghcnd_daily",
        "GhcndDailySnapshotEvidence",
        "build_grib_residuals",
        "build_min_t_grib_residuals",
        "WeatherCalibrationResidual",
        "CRPS",
        "crps",
        "urllib",
        "subprocess",
        "socket",
    )
    for token in forbidden:
        assert token not in source
    assert "market_universe" in source  # only stable hashing is permitted


def test_capture_cli_has_no_network_execution_or_credential_imports() -> None:
    source = Path("scripts/capture_m27l_prospective_forecast.py").read_text()
    forbidden = (
        "production_execution",
        "credential",
        "risk_engine",
        "parse_ghcnd_daily",
        "GhcndDailySnapshotEvidence",
        "urllib",
        "socket",
        "requests",
    )
    for token in forbidden:
        assert token not in source
