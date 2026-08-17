"""M27C Part 2B2 pure physical-temperature proxy probabilities.

This module deliberately has no transport, filesystem, market-pricing, risk, or
execution dependency.  Its output models GHCN-Daily physical temperature, not
The Weather Company settlement.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from services.market_universe.domain import stable_hash

from .daily_temperature import (
    SETTLEMENT_AUTHORITY,
    DailyTemperatureRoute,
    DailyTemperatureRouteState,
)
from .domain import ForecastError
from .weather_calibration import CalibrationMeasurement, ReplayFidelity
from .weather_calibration_coverage import POLICY_VERSION as COVERAGE_POLICY_VERSION
from .weather_calibration_coverage import LeadBucket
from .weather_calibration_grib import (
    EXPECTED_DX,
    EXPECTED_DY,
    EXPECTED_GRID_TEMPLATE,
    EXPECTED_NX,
    EXPECTED_NY,
    EXPECTED_PARAMETER,
    POST2020_GRIB_FAMILY,
    RawGribEvidence,
    kelvin_to_fahrenheit,
    target_local_date,
)
from .weather_source_authority import PHYSICAL_WEATHER_SOURCES, PhysicalWeatherSource

POLICY_VERSION = "m27c-part2b2-physical-temperature-proxy-v1"
MODEL_KIND = "HORIZON_SPECIFIC_UNWEIGHTED_EMPIRICAL_RESIDUAL_V1"
CLAIM_TYPE = "GHCND_PHYSICAL_TEMPERATURE_PROXY"
SETTLEMENT_MAPPING_STATUS = "UNVALIDATED_GHCND_PROXY"
MINIMUM_SAMPLES = 365
MINIMUM_SAMPLE_POLICY = "V1_OPERATING_SAFETY_FLOOR"
INTERVAL_LEVELS = (Decimal("0.50"), Decimal("0.80"), Decimal("0.90"))
SOURCE = PHYSICAL_WEATHER_SOURCES["CLIMDW"]
ZERO = Decimal("0")
_ARTIFACT_FIELDS = {
    "source",
    "measurement",
    "requested_target_start_date",
    "requested_target_end_date",
    "actual_catalog_scan_start_date",
    "actual_catalog_scan_end_date",
    "authority_identity",
    "policy_version",
    "product_family_identity",
    "status",
    "aws_discovery_requests",
    "archive_catalog_requests",
    "successful_descriptors",
    "successful_point_csvs",
    "rejected_or_ambiguous_datasets",
    "missing_dates",
    "quality_flagged_outcomes",
    "raw_residual_rows",
    "selected_residual_rows",
    "selected_residual_ids",
    "unique_local_target_dates",
    "counts_by_lead_bucket",
    "coverage_percent_by_lead_bucket",
    "missing_dates_by_lead_bucket",
    "lead_seconds",
    "earliest_selected_target_date",
    "latest_selected_target_date",
    "evidence_identities",
    "evidence_hashes",
    "acquired_at",
    "production_influence",
    "raw_grib_objects",
    "accepted_forecast_records",
    "extraction_provenance",
    "usable_outcome_count",
    "artifact_sha256",
}


class WeatherProbabilityAbstentionReason(StrEnum):
    INVALID_ARTIFACT = "INVALID_ARTIFACT"
    INSUFFICIENT_TRAINING_DATA = "INSUFFICIENT_TRAINING_DATA"
    MISSING_CALENDAR_MONTH_SUPPORT = "MISSING_CALENDAR_MONTH_SUPPORT"
    EVIDENCE_MISMATCH = "EVIDENCE_MISMATCH"
    CONTRACT_MISMATCH = "CONTRACT_MISMATCH"


@dataclass(frozen=True, slots=True)
class WeatherProbabilityAbstention:
    reason: WeatherProbabilityAbstentionReason
    detail: str
    research_only: bool = True
    production_influence: Decimal = ZERO


@dataclass(frozen=True, slots=True)
class TypedResidual:
    residual_id: str
    settlement_product_id: str
    nws_station_id: str
    ghcnd_station_id: str
    measurement: CalibrationMeasurement
    forecast_reference_time: datetime
    valid_time_coordinate: datetime
    local_target_date: date
    lead_to_valid_coordinate_seconds: int
    forecast_deg_f: Decimal
    observed_deg_f: Decimal
    residual_deg_f: Decimal
    authority_identity: str
    replay_fidelity: ReplayFidelity
    research_only: bool
    production_influence: Decimal


@dataclass(frozen=True, slots=True)
class WeatherCalibrationModelIdentity:
    model_id: str
    policy_version: str
    model_kind: str
    claim_type: str
    settlement_mapping_status: str
    authority_identity: str
    lead_bucket: LeadBucket
    exact_midpoint_seconds: int
    training_start: date
    training_end: date
    coverage_artifact_identity: str
    residual_population_hash: str
    sample_count: int
    minimum_samples: int
    minimum_sample_policy: str


@dataclass(frozen=True, slots=True)
class WeatherResidualPopulation:
    identity: WeatherCalibrationModelIdentity
    rows: tuple[TypedResidual, ...]
    residual_ids: tuple[str, ...]
    residuals: tuple[Decimal, ...]
    replay_fidelity: ReplayFidelity
    research_only: bool = True
    production_influence: Decimal = ZERO


@dataclass(frozen=True, slots=True)
class EmpiricalResidualDistribution:
    values: tuple[Decimal, ...]

    def __post_init__(self) -> None:
        if not self.values or any(not value.is_finite() for value in self.values):
            raise ForecastError("empirical distribution must be nonempty and finite")
        object.__setattr__(self, "values", tuple(sorted(self.values)))

    @property
    def count(self) -> int:
        return len(self.values)

    @property
    def probability_resolution(self) -> Decimal:
        return Decimal(1) / Decimal(self.count)

    def shifted(self, central: Decimal) -> EmpiricalResidualDistribution:
        if not central.is_finite():
            raise ForecastError("central forecast is non-finite")
        return EmpiricalResidualDistribution(tuple(central + value for value in self.values))

    def predicate_count(self, comparator: str, lower: Decimal, upper: Decimal | None = None) -> int:
        if not lower.is_finite() or (upper is not None and not upper.is_finite()):
            raise ForecastError("predicate bound is non-finite")
        if comparator == "GT" and upper is None:
            return sum(value > lower for value in self.values)
        if comparator == "LT" and upper is None:
            return sum(value < lower for value in self.values)
        if comparator == "RANGE" and upper is not None and lower < upper:
            return sum(lower <= value <= upper for value in self.values)
        raise ForecastError("unsupported physical-temperature predicate")

    def quantile(self, probability: Decimal) -> Decimal:
        if not probability.is_finite() or not ZERO <= probability <= Decimal(1):
            raise ForecastError("invalid empirical quantile")
        index = max(1, min(self.count, math.ceil(self.count * probability)))
        return self.values[index - 1]

    def interval(self, level: Decimal) -> tuple[Decimal, Decimal]:
        if level not in INTERVAL_LEVELS:
            raise ForecastError("unsupported empirical interval level")
        tail = (Decimal(1) - level) / Decimal(2)
        return self.quantile(tail), self.quantile(Decimal(1) - tail)

    @property
    def mean(self) -> Decimal:
        return sum(self.values, ZERO) / Decimal(self.count)

    @property
    def median(self) -> Decimal:
        midpoint = self.count // 2
        if self.count % 2:
            return self.values[midpoint]
        return (self.values[midpoint - 1] + self.values[midpoint]) / Decimal(2)

    def crps(self, observed: Decimal) -> Decimal:
        if not observed.is_finite():
            raise ForecastError("observed temperature is non-finite")
        first = sum((abs(value - observed) for value in self.values), ZERO) / Decimal(self.count)
        pairwise = sum((abs(left - right) for left in self.values for right in self.values), ZERO)
        return first - pairwise / (Decimal(2) * Decimal(self.count) ** 2)


@dataclass(frozen=True, slots=True)
class CurrentWeatherForecastEvidence:
    evidence_identity: str
    family_identity: str
    authority_identity: str
    settlement_product_id: str
    nws_station_id: str
    ghcnd_station_id: str
    forecast_reference_time: datetime
    interval_start: datetime
    interval_end: datetime
    midpoint: datetime
    local_target_date: date
    exact_midpoint_seconds: int
    lead_bucket: LeadBucket
    central_kelvin: Decimal
    central_deg_f: Decimal
    record_number: int
    raw_grib_sha256: str
    extraction_sha256: str
    extraction_policy_version: str
    wgrib2_version: str
    research_only: bool = True
    production_influence: Decimal = ZERO


@dataclass(frozen=True, slots=True)
class PhysicalTemperatureProxyProbability:
    result_identity: str
    model_identity: str
    residual_population_identity: str
    route_source_identity: str
    route_policy_identity: str
    market_ticker: str
    event_ticker: str
    series_ticker: str
    current_forecast_evidence_identity: str
    central_forecast_deg_f: Decimal
    exact_midpoint_seconds: int
    lead_bucket: LeadBucket
    sample_count: int
    numerator: int
    denominator: int
    probability: Decimal
    probability_resolution: Decimal
    intervals: tuple[tuple[Decimal, Decimal, Decimal], ...]
    distribution_min: Decimal
    distribution_max: Decimal
    distribution_mean: Decimal
    distribution_median: Decimal
    diagnostic: str | None
    replay_fidelity: ReplayFidelity
    settlement_mapping_status: str
    claim_type: str
    research_only: bool = True
    production_influence: Decimal = ZERO


def load_weather_residual_population(
    payload: Mapping[str, Any],
    *,
    exact_midpoint_seconds: int,
    training_start: date,
    training_end: date,
    minimum_samples: int = MINIMUM_SAMPLES,
) -> WeatherResidualPopulation | WeatherProbabilityAbstention:
    """Strictly deserialize selected coverage rows into one typed population."""
    try:
        return _load_population(
            payload, exact_midpoint_seconds, training_start, training_end, minimum_samples
        )
    except ForecastError as exc:
        return WeatherProbabilityAbstention(
            WeatherProbabilityAbstentionReason.INVALID_ARTIFACT, str(exc)
        )


def _load_population(
    payload: Mapping[str, Any],
    midpoint: int,
    start: date,
    end: date,
    minimum: int,
) -> WeatherResidualPopulation | WeatherProbabilityAbstention:
    if midpoint not in {54_000, 140_400, 226_800} or start > end or minimum != MINIMUM_SAMPLES:
        raise ForecastError("unsupported model policy")
    if set(payload) != _ARTIFACT_FIELDS:
        raise ForecastError("coverage artifact schema is not exact")
    if payload["status"] != "COMPLETE":
        raise ForecastError("coverage artifact is incomplete")
    if payload["source"] != "CLIMDW" or payload["measurement"] != "DAILY_MAX":
        raise ForecastError("coverage artifact lane is unsupported")
    if payload["authority_identity"] != SOURCE.authority_identity:
        raise ForecastError("coverage artifact authority conflicts")
    if payload["policy_version"] != COVERAGE_POLICY_VERSION:
        raise ForecastError("coverage policy is unsupported")
    if payload["product_family_identity"] != POST2020_GRIB_FAMILY:
        raise ForecastError("coverage family is unsupported")
    artifact_start = _date(payload["requested_target_start_date"], "requested start")
    artifact_end = _date(payload["requested_target_end_date"], "requested end")
    if (artifact_start, artifact_end) != (date(2024, 1, 1), date(2026, 7, 31)):
        raise ForecastError("coverage artifact requested range is unsupported")
    if artifact_start > start or artifact_end < end:
        raise ForecastError("coverage artifact does not contain training range")
    expected_hash = payload["artifact_sha256"]
    if not isinstance(expected_hash, str) or expected_hash != _artifact_hash(payload):
        raise ForecastError("coverage artifact identity mismatch")
    raw_rows = payload["selected_residual_rows"]
    raw_ids = payload["selected_residual_ids"]
    if not isinstance(raw_rows, list) or not isinstance(raw_ids, list):
        raise ForecastError("selected residual collections are malformed")
    typed_all = tuple(_typed_residual(row) for row in raw_rows)
    if tuple(row.residual_id for row in typed_all) != tuple(raw_ids):
        raise ForecastError("selected residual IDs mismatch")
    if len(set(raw_ids)) != len(raw_ids):
        raise ForecastError("duplicate selected residual ID")
    keys = [
        (
            row.settlement_product_id,
            row.measurement,
            row.local_target_date,
            _bucket(row.lead_to_valid_coordinate_seconds),
        )
        for row in typed_all
    ]
    if len(set(keys)) != len(keys):
        raise ForecastError("duplicate selected residual key")
    artifact_raw_rows = payload["raw_residual_rows"]
    if not isinstance(artifact_raw_rows, list):
        raise ForecastError("raw residual collection is malformed")
    typed_raw = tuple(_typed_residual(row) for row in artifact_raw_rows)
    independently_selected = _select_typed_residuals(typed_raw)
    if tuple(row.residual_id for row in independently_selected) != tuple(raw_ids):
        raise ForecastError("selected rows do not match deterministic raw-row selection")
    expected_counts = {
        bucket.value: sum(
            _bucket(row.lead_to_valid_coordinate_seconds) is bucket for row in typed_all
        )
        for bucket in LeadBucket
    }
    if payload["counts_by_lead_bucket"] != expected_counts:
        raise ForecastError("selected residual counts conflict with artifact")
    rows = tuple(
        row
        for row in typed_all
        if start <= row.local_target_date <= end
        and row.lead_to_valid_coordinate_seconds == midpoint
    )
    canonical = tuple(
        sorted(
            rows,
            key=lambda row: (row.local_target_date, row.forecast_reference_time, row.residual_id),
        )
    )
    if rows != canonical:
        raise ForecastError("selected residual rows are not canonical")
    if len(rows) < minimum:
        return WeatherProbabilityAbstention(
            WeatherProbabilityAbstentionReason.INSUFFICIENT_TRAINING_DATA,
            f"{len(rows)} rows is below {MINIMUM_SAMPLE_POLICY} {minimum}",
        )
    required_months = {(start.year, month) for month in range(1, 13)} | {
        (end.year, month) for month in range(1, end.month + 1)
    }
    present = {(row.local_target_date.year, row.local_target_date.month) for row in rows}
    if not required_months <= present:
        return WeatherProbabilityAbstention(
            WeatherProbabilityAbstentionReason.MISSING_CALENDAR_MONTH_SUPPORT,
            "training population lacks required calendar-month support",
        )
    residual_ids = tuple(row.residual_id for row in rows)
    residuals = tuple(row.residual_deg_f for row in rows)
    population_hash = stable_hash(
        (
            POLICY_VERSION,
            midpoint,
            start.isoformat(),
            end.isoformat(),
            tuple(zip(residual_ids, map(str, residuals), strict=True)),
        )
    )
    bucket = _bucket(midpoint)
    material = _model_material(
        payload, bucket, midpoint, start, end, residual_ids, population_hash, len(rows), minimum
    )
    model_id = stable_hash(material)
    identity = WeatherCalibrationModelIdentity(
        model_id,
        POLICY_VERSION,
        MODEL_KIND,
        CLAIM_TYPE,
        SETTLEMENT_MAPPING_STATUS,
        SOURCE.authority_identity,
        bucket,
        midpoint,
        start,
        end,
        str(expected_hash),
        population_hash,
        len(rows),
        minimum,
        MINIMUM_SAMPLE_POLICY,
    )
    return WeatherResidualPopulation(
        identity,
        rows,
        residual_ids,
        residuals,
        ReplayFidelity.FORECAST_VINTAGED_CURRENT_OUTCOME_SNAPSHOT,
    )


def build_current_weather_forecast_evidence(
    evidence: RawGribEvidence,
    *,
    record_number: int,
    source: PhysicalWeatherSource = SOURCE,
) -> CurrentWeatherForecastEvidence:
    """Revalidate and bind one exact reviewed GRIB record without accepting a bare center."""
    if source is not SOURCE or evidence.family_identity != POST2020_GRIB_FAMILY:
        raise ForecastError("current forecast source or family is unsupported")
    if evidence.wgrib2_version != "3.8.0" or not evidence.extraction_policy_version:
        raise ForecastError("current forecast extraction policy is unsupported")
    if not evidence.raw_grib_sha256 or not evidence.extraction_sha256:
        raise ForecastError("current forecast hashes are required")
    records = tuple(sorted(evidence.records, key=lambda value: value.record_number))
    if len(records) != 3 or tuple(value.record_number for value in records) != (1, 2, 3):
        raise ForecastError("current forecast must contain exactly three records")
    expected = ((9, 21, 54_000), (33, 45, 140_400), (57, 69, 226_800))
    reference = records[0].reference_time
    if reference.hour != 3 or reference.minute or reference.second or reference.microsecond:
        raise ForecastError("current forecast reference is not exact 03Z")
    for candidate, (start_hour, end_hour, midpoint) in zip(records, expected, strict=True):
        if (
            candidate.reference_time != reference
            or candidate.variable != "TMAX"
            or candidate.level != "2 m above ground"
            or candidate.generating_process_code != 2
            or candidate.statistical_process_code != 2
            or candidate.time_processing_code != 2
            or candidate.parameter != EXPECTED_PARAMETER
            or candidate.unit != "Kelvin"
            or candidate.lead_to_interval_start_seconds != start_hour * 3600
            or candidate.lead_to_interval_end_seconds != end_hour * 3600
            or candidate.lead_to_midpoint_seconds != midpoint
            or candidate.verification_time != candidate.interval_end
            or candidate.grid_template != EXPECTED_GRID_TEMPLATE
            or candidate.nx != EXPECTED_NX
            or candidate.ny != EXPECTED_NY
            or candidate.dx != EXPECTED_DX
            or candidate.dy != EXPECTED_DY
            or not candidate.kelvin.is_finite()
        ):
            raise ForecastError("current forecast record semantics are unsupported")
    record = next((value for value in records if value.record_number == record_number), None)
    if record is None:
        raise ForecastError("current forecast record is missing")
    midpoint = record.lead_to_midpoint_seconds
    bucket = _bucket(midpoint)
    local_date = target_local_date(record, source.timezone)
    central = kelvin_to_fahrenheit(record.kelvin)
    identity = stable_hash(
        (
            POLICY_VERSION,
            evidence.family_identity,
            source.authority_identity,
            source.settlement_product_id,
            source.nws_station_id,
            source.ghcnd_station_id,
            reference.isoformat(),
            record.interval_start.isoformat(),
            record.interval_end.isoformat(),
            record.midpoint.isoformat(),
            local_date.isoformat(),
            midpoint,
            bucket.value,
            str(record.kelvin),
            str(central),
            record.record_number,
            evidence.raw_grib_sha256,
            evidence.extraction_sha256,
            evidence.extraction_policy_version,
            evidence.wgrib2_version,
        )
    )
    return CurrentWeatherForecastEvidence(
        identity,
        evidence.family_identity,
        source.authority_identity,
        source.settlement_product_id,
        source.nws_station_id,
        source.ghcnd_station_id,
        reference,
        record.interval_start,
        record.interval_end,
        record.midpoint,
        local_date,
        midpoint,
        bucket,
        record.kelvin,
        central,
        record.record_number,
        evidence.raw_grib_sha256,
        evidence.extraction_sha256,
        evidence.extraction_policy_version,
        evidence.wgrib2_version,
    )


def physical_temperature_proxy_probability(
    *,
    route: DailyTemperatureRoute,
    population: WeatherResidualPopulation,
    current: CurrentWeatherForecastEvidence,
) -> PhysicalTemperatureProxyProbability | WeatherProbabilityAbstention:
    if route.state is not DailyTemperatureRouteState.SUPPORTED or route.contract is None:
        return WeatherProbabilityAbstention(
            WeatherProbabilityAbstentionReason.CONTRACT_MISMATCH, "route is not supported"
        )
    contract = route.contract
    if (
        contract.station_id != "CLIMDW"
        or contract.location != "Chicago"
        or contract.measurement != "DAILY_MAX"
        or contract.timezone != "America/Chicago"
        or contract.unit != "degF"
        or contract.settlement_authority != SETTLEMENT_AUTHORITY
        or contract.rounding is not None
        or contract.comparator not in {"GT", "LT", "RANGE"}
        or contract.local_date != current.local_target_date
    ):
        return WeatherProbabilityAbstention(
            WeatherProbabilityAbstentionReason.CONTRACT_MISMATCH,
            "contract conflicts with physical-temperature proxy policy",
        )
    identity = population.identity
    if (
        identity.authority_identity != current.authority_identity
        or identity.exact_midpoint_seconds != current.exact_midpoint_seconds
        or identity.lead_bucket is not current.lead_bucket
        or current.family_identity != POST2020_GRIB_FAMILY
        or current.settlement_product_id != "CLIMDW"
        or current.nws_station_id != "KMDW"
        or current.ghcnd_station_id != "USW00014819"
    ):
        return WeatherProbabilityAbstention(
            WeatherProbabilityAbstentionReason.EVIDENCE_MISMATCH,
            "current forecast and residual population conflict",
        )
    distribution = EmpiricalResidualDistribution(population.residuals).shifted(
        current.central_deg_f
    )
    numerator = distribution.predicate_count(contract.comparator, contract.lower, contract.upper)
    probability = Decimal(numerator) / Decimal(distribution.count)
    intervals = tuple((level, *distribution.interval(level)) for level in INTERVAL_LEVELS)
    diagnostic = "EMPIRICAL_BOUNDARY_MASS" if probability in {ZERO, Decimal(1)} else None
    material = (
        POLICY_VERSION,
        identity.model_id,
        identity.residual_population_hash,
        route.source_identity,
        route.policy_identity,
        route.market_ticker,
        route.event_ticker,
        route.series_ticker,
        current.evidence_identity,
        numerator,
        distribution.count,
        str(probability),
        tuple((str(level), str(low), str(high)) for level, low, high in intervals),
        diagnostic,
        CLAIM_TYPE,
        SETTLEMENT_MAPPING_STATUS,
        True,
        "0",
    )
    return PhysicalTemperatureProxyProbability(
        stable_hash(material),
        identity.model_id,
        identity.residual_population_hash,
        route.source_identity,
        route.policy_identity,
        route.market_ticker,
        route.event_ticker,
        route.series_ticker,
        current.evidence_identity,
        current.central_deg_f,
        current.exact_midpoint_seconds,
        current.lead_bucket,
        distribution.count,
        numerator,
        distribution.count,
        probability,
        distribution.probability_resolution,
        intervals,
        distribution.values[0],
        distribution.values[-1],
        distribution.mean,
        distribution.median,
        diagnostic,
        population.replay_fidelity,
        SETTLEMENT_MAPPING_STATUS,
        CLAIM_TYPE,
    )


def _typed_residual(value: Any) -> TypedResidual:
    expected = {
        "source",
        "measurement",
        "forecast_reference_time",
        "valid_time_coordinate",
        "local_target_date",
        "lead_to_valid_coordinate_seconds",
        "forecast_kelvin",
        "forecast_deg_f",
        "observed_tenths_c",
        "observed_deg_f",
        "residual_deg_f",
        "ndfd_descriptor_hash",
        "ndfd_csv_hash",
        "ghcnd_snapshot_hash",
        "ndfd_evidence_identity",
        "ghcnd_evidence_identity",
        "authority_identity",
        "replay_fidelity",
        "research_only",
        "production_influence",
        "residual_id",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ForecastError("selected residual row schema is not exact")
    if value["source"] != "CLIMDW" or value["measurement"] != "DAILY_MAX":
        raise ForecastError("selected residual row lane is unsupported")
    if value["authority_identity"] != SOURCE.authority_identity:
        raise ForecastError("selected residual authority conflicts")
    if value["ndfd_evidence_identity"] != POST2020_GRIB_FAMILY:
        raise ForecastError("selected residual family is unsupported")
    forecast, observed, residual = (
        _decimal(value[name], name)
        for name in ("forecast_deg_f", "observed_deg_f", "residual_deg_f")
    )
    if residual != observed - forecast:
        raise ForecastError("selected residual arithmetic mismatch")
    if (
        value["research_only"] is not True
        or _decimal(value["production_influence"], "influence") != ZERO
    ):
        raise ForecastError("selected residual safety boundary mismatch")
    try:
        replay = ReplayFidelity(value["replay_fidelity"])
    except (TypeError, ValueError) as exc:
        raise ForecastError("selected residual replay fidelity is unsupported") from exc
    lead = value["lead_to_valid_coordinate_seconds"]
    if (
        isinstance(lead, bool)
        or not isinstance(lead, int)
        or lead not in {54_000, 140_400, 226_800}
    ):
        raise ForecastError("selected residual horizon is unsupported")
    material = {key: value[key] for key in expected - {"residual_id"}}
    residual_id = stable_hash(("m27c-residual-v1", _jsonable(material)))
    if value["residual_id"] != residual_id:
        raise ForecastError("selected residual identity mismatch")
    return TypedResidual(
        residual_id,
        "CLIMDW",
        "KMDW",
        "USW00014819",
        CalibrationMeasurement.DAILY_MAX,
        _datetime(value["forecast_reference_time"], "reference"),
        _datetime(value["valid_time_coordinate"], "valid"),
        _date(value["local_target_date"], "target"),
        lead,
        forecast,
        observed,
        residual,
        str(value["authority_identity"]),
        replay,
        True,
        ZERO,
    )


def _model_material(
    payload: Mapping[str, Any],
    bucket: LeadBucket,
    midpoint: int,
    start: date,
    end: date,
    ids: tuple[str, ...],
    population_hash: str,
    count: int,
    minimum: int,
) -> tuple[Any, ...]:
    return (
        POLICY_VERSION,
        MODEL_KIND,
        CLAIM_TYPE,
        SETTLEMENT_MAPPING_STATUS,
        SOURCE.authority_identity,
        "CLIMDW",
        "KMDW",
        "USW00014819",
        "America/Chicago",
        "degF",
        "DAILY_MAX",
        POST2020_GRIB_FAMILY,
        "03Z",
        ("TMAX", "2 m above ground", (0, 0, 4), "Forecast", "Maximum", "12h"),
        bucket.value,
        midpoint,
        start.isoformat(),
        end.isoformat(),
        COVERAGE_POLICY_VERSION,
        payload["artifact_sha256"],
        ids,
        population_hash,
        count,
        minimum,
        MINIMUM_SAMPLE_POLICY,
        "UNIFORM_EMPIRICAL_NO_POOLING",
        "NONE",
        "NO_EXTRAPOLATION",
        "NONE",
        "NONE",
        "NONE",
        "NEAREST_RANK_CEIL_ENDPOINT_CLAMPED",
        tuple(map(str, INTERVAL_LEVELS)),
        "GT_STRICT_LT_STRICT_RANGE_INCLUSIVE_V1",
        ReplayFidelity.FORECAST_VINTAGED_CURRENT_OUTCOME_SNAPSHOT.value,
        True,
        "0",
    )


def _select_typed_residuals(rows: tuple[TypedResidual, ...]) -> tuple[TypedResidual, ...]:
    groups: dict[tuple[str, CalibrationMeasurement, date, LeadBucket], list[TypedResidual]] = {}
    for row in rows:
        key = (
            row.settlement_product_id,
            row.measurement,
            row.local_target_date,
            _bucket(row.lead_to_valid_coordinate_seconds),
        )
        groups.setdefault(key, []).append(row)
    selected = [
        min(candidates, key=lambda row: (row.forecast_reference_time, row.residual_id))
        for candidates in groups.values()
    ]
    return tuple(
        sorted(
            selected,
            key=lambda row: (
                row.local_target_date,
                _bucket(row.lead_to_valid_coordinate_seconds).value,
                row.forecast_reference_time,
            ),
        )
    )


def _artifact_hash(payload: Mapping[str, Any]) -> str:
    content = dict(payload)
    content["artifact_sha256"] = None
    provenance = content.get("extraction_provenance")
    if isinstance(provenance, list):
        provenance = [dict(item) if isinstance(item, Mapping) else item for item in provenance]
        for item in provenance:
            if isinstance(item, dict):
                item.pop("executable_path", None)
        content["extraction_provenance"] = provenance
    return stable_hash(content)


def _bucket(seconds: int) -> LeadBucket:
    mapping = {
        54_000: LeadBucket.ZERO_TO_24H,
        140_400: LeadBucket.TWENTY_FOUR_TO_48H,
        226_800: LeadBucket.FORTY_EIGHT_TO_72H,
    }
    try:
        return mapping[seconds]
    except KeyError as exc:
        raise ForecastError("lead is not an exact supported midpoint") from exc


def _decimal(value: Any, field: str) -> Decimal:
    if not isinstance(value, str):
        raise ForecastError(f"{field} must be an exact Decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ForecastError(f"{field} is malformed") from exc
    if not result.is_finite():
        raise ForecastError(f"{field} is non-finite")
    return result


def _date(value: Any, field: str) -> date:
    if not isinstance(value, str):
        raise ForecastError(f"{field} must be an ISO date string")
    try:
        result = date.fromisoformat(value)
    except ValueError as exc:
        raise ForecastError(f"{field} is malformed") from exc
    if value != result.isoformat():
        raise ForecastError(f"{field} is not canonical")
    return result


def _datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ForecastError(f"{field} must be an ISO datetime string")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ForecastError(f"{field} is malformed") from exc
    if result.tzinfo is None or result.utcoffset() is None or value != result.isoformat():
        raise ForecastError(f"{field} is not canonical and timezone-aware")
    return result


def _jsonable(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value
