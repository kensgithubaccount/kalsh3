"""Pure coverage accounting for M27C Part 2B1.5.

This module intentionally contains no transport and no forecasting.  It keeps
all accepted residuals, then selects at most one residual per local target
date and lead bucket using only forecast metadata.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, cast

from services.market_universe.domain import stable_hash

from .domain import ForecastError
from .weather_calibration import WeatherCalibrationResidual

POLICY_VERSION = "m27c-part2b1.5-calibration-coverage-v1"
ZERO = Decimal("0")


class CalibrationFamily(StrEnum):
    LEGACY_CHICAGO_MAXT_5KM_YGFZ98 = "LEGACY_CHICAGO_MAXT_5KM_YGFZ98"
    POST2020_CHICAGO_MAXT_2P5KM_YGUZ98_03Z = "POST2020_CHICAGO_MAXT_2P5KM_YGUZ98_03Z"


REVIEWED_FAMILY_IDENTITIES = frozenset(family.value for family in CalibrationFamily)


class LeadBucket(StrEnum):
    ZERO_TO_24H = "0-24h"
    TWENTY_FOUR_TO_48H = "24-48h"
    FORTY_EIGHT_TO_72H = "48-72h"

    @property
    def midpoint_seconds(self) -> int:
        return {
            self.ZERO_TO_24H: 43_200,
            self.TWENTY_FOUR_TO_48H: 129_600,
            self.FORTY_EIGHT_TO_72H: 216_000,
        }[self]


def lead_bucket(seconds: int) -> LeadBucket | None:
    if seconds <= 0 or seconds > 72 * 3600:
        return None
    if seconds <= 24 * 3600:
        return LeadBucket.ZERO_TO_24H
    if seconds <= 48 * 3600:
        return LeadBucket.TWENTY_FOUR_TO_48H
    return LeadBucket.FORTY_EIGHT_TO_72H


def residual_identity(row: WeatherCalibrationResidual) -> str:
    return stable_hash(("m27c-residual-v1", _jsonable(_residual_fields(row))))


def select_calibration_residuals(
    rows: tuple[WeatherCalibrationResidual, ...] | list[WeatherCalibrationResidual],
) -> tuple[WeatherCalibrationResidual, ...]:
    """Select deterministically without using outcome or residual magnitude."""
    groups: dict[tuple[str, str, date, LeadBucket], list[WeatherCalibrationResidual]] = {}
    for row in rows:
        bucket = lead_bucket(row.lead_to_valid_coordinate_seconds)
        if bucket is None:
            continue
        key = (row.settlement_product_id, row.measurement.value, row.local_target_date, bucket)
        groups.setdefault(key, []).append(row)
    selected: list[WeatherCalibrationResidual] = []
    for key, candidates in groups.items():
        chosen = min(
            candidates,
            key=lambda row: (
                abs(row.lead_to_valid_coordinate_seconds - key[3].midpoint_seconds),
                row.forecast_reference_time,
                residual_identity(row),
            ),
        )
        selected.append(chosen)
    return tuple(
        sorted(
            selected,
            key=lambda row: (
                row.local_target_date,
                cast(LeadBucket, lead_bucket(row.lead_to_valid_coordinate_seconds)).value,
                row.forecast_reference_time,
            ),
        )
    )


@dataclass(frozen=True, slots=True)
class CoverageArtifact:
    source: str
    measurement: str
    requested_target_start_date: date
    requested_target_end_date: date
    actual_catalog_scan_start_date: date
    actual_catalog_scan_end_date: date
    authority_identity: str
    policy_version: str
    product_family_identity: str
    status: str
    aws_discovery_requests: tuple[str, ...]
    archive_catalog_requests: tuple[str, ...]
    successful_descriptors: tuple[dict[str, Any], ...]
    successful_point_csvs: tuple[dict[str, Any], ...]
    rejected_or_ambiguous_datasets: tuple[dict[str, str], ...]
    missing_dates: tuple[date, ...]
    quality_flagged_outcomes: tuple[date, ...]
    raw_residual_rows: tuple[dict[str, Any], ...]
    selected_residual_rows: tuple[dict[str, Any], ...]
    selected_residual_ids: tuple[str, ...]
    unique_local_target_dates: tuple[date, ...]
    counts_by_lead_bucket: dict[str, int]
    coverage_percent_by_lead_bucket: dict[str, str]
    missing_dates_by_lead_bucket: dict[str, tuple[date, ...]]
    lead_seconds: tuple[int, ...]
    earliest_selected_target_date: date | None
    latest_selected_target_date: date | None
    evidence_identities: tuple[str, ...]
    evidence_hashes: tuple[str, ...]
    acquired_at: datetime
    production_influence: Decimal
    raw_grib_objects: tuple[dict[str, Any], ...] = ()
    accepted_forecast_records: tuple[dict[str, Any], ...] = ()
    extraction_provenance: tuple[dict[str, Any], ...] = ()
    usable_outcome_count: int = 0

    def content(self) -> dict[str, Any]:
        result = asdict(self)
        result["artifact_sha256"] = None
        return cast(dict[str, Any], _jsonable(result))

    def artifact_sha256(self) -> str:
        content = self.content()
        provenance = content.get("extraction_provenance")
        if isinstance(provenance, list):
            for item in provenance:
                if isinstance(item, dict):
                    item.pop("executable_path", None)
        return stable_hash(content)


def build_coverage_artifact(
    *,
    source: str,
    measurement: str,
    requested_target_start_date: date,
    requested_target_end_date: date,
    actual_catalog_scan_start_date: date,
    actual_catalog_scan_end_date: date,
    authority_identity: str,
    product_family_identity: str,
    status: str,
    aws_discovery_requests: tuple[str, ...],
    archive_catalog_requests: tuple[str, ...],
    descriptors: tuple[dict[str, Any], ...],
    point_csvs: tuple[dict[str, Any], ...],
    rejected: tuple[dict[str, str], ...],
    missing_dates: tuple[date, ...],
    quality_flagged_outcomes: tuple[date, ...],
    rows: tuple[WeatherCalibrationResidual, ...],
    acquired_at: datetime,
    raw_grib_objects: tuple[dict[str, Any], ...] = (),
    accepted_forecast_records: tuple[dict[str, Any], ...] = (),
    extraction_provenance: tuple[dict[str, Any], ...] = (),
    usable_outcome_count: int = 0,
) -> CoverageArtifact:
    if requested_target_start_date > requested_target_end_date:
        raise ForecastError("coverage date bounds are reversed")
    if product_family_identity not in REVIEWED_FAMILY_IDENTITIES:
        raise ForecastError("coverage family is not a reviewed calibration family")
    if any(
        row.local_target_date < requested_target_start_date
        or row.local_target_date > requested_target_end_date
        for row in rows
    ):
        raise ForecastError("residual target date is outside the requested target range")
    selected = select_calibration_residuals(rows)
    dates = tuple(sorted({row.local_target_date for row in selected}))
    counts = {bucket.value: 0 for bucket in LeadBucket}
    for row in selected:
        bucket = lead_bucket(row.lead_to_valid_coordinate_seconds)
        if bucket is not None:
            counts[bucket.value] += 1
    identities = sorted(
        {
            value
            for row in rows
            for value in (row.ndfd_evidence_identity, row.ghcnd_evidence_identity)
        }
    )
    hashes = sorted(
        {
            value
            for row in rows
            for value in (row.ndfd_descriptor_hash, row.ndfd_csv_hash, row.ghcnd_snapshot_hash)
        }
    )
    denominator = (requested_target_end_date - requested_target_start_date).days + 1
    coverage = {
        bucket.value: str(
            (Decimal(counts[bucket.value]) * Decimal("100") / Decimal(denominator)).quantize(
                Decimal("0.01")
            )
        )
        for bucket in LeadBucket
    }
    missing_by_bucket = {
        bucket.value: tuple(
            day
            for day in _date_range(requested_target_start_date, requested_target_end_date)
            if not any(
                row.local_target_date == day
                and lead_bucket(row.lead_to_valid_coordinate_seconds) is bucket
                for row in selected
            )
        )
        for bucket in LeadBucket
    }
    return CoverageArtifact(
        source=source,
        measurement=measurement,
        requested_target_start_date=requested_target_start_date,
        requested_target_end_date=requested_target_end_date,
        actual_catalog_scan_start_date=actual_catalog_scan_start_date,
        actual_catalog_scan_end_date=actual_catalog_scan_end_date,
        authority_identity=authority_identity,
        policy_version=POLICY_VERSION,
        product_family_identity=product_family_identity,
        status=status,
        aws_discovery_requests=aws_discovery_requests,
        archive_catalog_requests=archive_catalog_requests,
        successful_descriptors=descriptors,
        successful_point_csvs=point_csvs,
        rejected_or_ambiguous_datasets=rejected,
        missing_dates=missing_dates,
        quality_flagged_outcomes=quality_flagged_outcomes,
        raw_residual_rows=tuple(_residual_json(row) for row in rows),
        selected_residual_rows=tuple(_residual_json(row) for row in selected),
        selected_residual_ids=tuple(residual_identity(row) for row in selected),
        unique_local_target_dates=dates,
        counts_by_lead_bucket=counts,
        coverage_percent_by_lead_bucket=coverage,
        missing_dates_by_lead_bucket=missing_by_bucket,
        lead_seconds=tuple(sorted(row.lead_to_valid_coordinate_seconds for row in rows)),
        earliest_selected_target_date=dates[0] if dates else None,
        latest_selected_target_date=dates[-1] if dates else None,
        evidence_identities=tuple(identities),
        evidence_hashes=tuple(hashes),
        acquired_at=acquired_at,
        production_influence=ZERO,
        raw_grib_objects=raw_grib_objects,
        accepted_forecast_records=accepted_forecast_records,
        extraction_provenance=extraction_provenance,
        usable_outcome_count=usable_outcome_count,
    )


def _date_range(start: date, end: date) -> tuple[date, ...]:
    return tuple(start + timedelta(days=offset) for offset in range((end - start).days + 1))


def _residual_json(row: WeatherCalibrationResidual) -> dict[str, Any]:
    value = _residual_fields(row)
    value["residual_id"] = stable_hash(("m27c-residual-v1", _jsonable(value)))
    return value


def _residual_fields(row: WeatherCalibrationResidual) -> dict[str, Any]:
    return {
        "source": row.settlement_product_id,
        "measurement": row.measurement.value,
        "forecast_reference_time": row.forecast_reference_time,
        "valid_time_coordinate": row.valid_time_coordinate,
        "local_target_date": row.local_target_date,
        "lead_to_valid_coordinate_seconds": row.lead_to_valid_coordinate_seconds,
        "forecast_kelvin": row.forecast_kelvin,
        "forecast_deg_f": row.forecast_deg_f,
        "observed_tenths_c": row.observed_tenths_c,
        "observed_deg_f": row.observed_deg_f,
        "residual_deg_f": row.residual_deg_f,
        "ndfd_descriptor_hash": row.ndfd_descriptor_hash,
        "ndfd_csv_hash": row.ndfd_csv_hash,
        "ghcnd_snapshot_hash": row.ghcnd_snapshot_hash,
        "ndfd_evidence_identity": row.ndfd_evidence_identity,
        "ghcnd_evidence_identity": row.ghcnd_evidence_identity,
        "authority_identity": row.authority_identity,
        "replay_fidelity": row.replay_fidelity.value,
        "research_only": row.research_only,
        "production_influence": row.production_influence,
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value
