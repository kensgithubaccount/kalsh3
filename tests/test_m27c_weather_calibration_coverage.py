from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from scripts.collect_m27c_weather_calibration_coverage import (
    _atomic_write,
    _cache_origin,
    _read_checkpoint,
    _write_checkpoint,
    _write_immutable,
)
from services.forecasting.weather_calibration import build_residuals
from services.forecasting.weather_calibration_coverage import (
    CalibrationFamily,
    LeadBucket,
    build_coverage_artifact,
    lead_bucket,
    residual_identity,
    select_calibration_residuals,
)
from tests.test_m27c_weather_calibration_evidence import evidence


def _rows():
    source, station, descriptor, point, outcome = evidence()
    base = build_residuals(
        source, descriptor, point, outcome, station, source, datetime(2026, 8, 16, tzinfo=UTC)
    )
    row = base[0]
    return tuple(
        replace(
            row,
            local_target_date=row.local_target_date,
            lead_to_valid_coordinate_seconds=lead,
            forecast_reference_time=datetime(2018, 6, 20, 7, tzinfo=UTC)
            + (datetime.min - datetime.min),
            residual_deg_f=residual,
        )
        for lead, residual in (
            (3600, Decimal("99")),
            (43200, Decimal("-99")),
            (86400, Decimal("1")),
            (129600, Decimal("2")),
            (172800, Decimal("3")),
            (216000, Decimal("4")),
        )
    )


def test_lead_bucket_boundaries_are_closed_on_upper_bound() -> None:
    assert lead_bucket(1) is LeadBucket.ZERO_TO_24H
    assert lead_bucket(86_400) is LeadBucket.ZERO_TO_24H
    assert lead_bucket(86_401) is LeadBucket.TWENTY_FOUR_TO_48H
    assert lead_bucket(172_800) is LeadBucket.TWENTY_FOUR_TO_48H
    assert lead_bucket(172_801) is LeadBucket.FORTY_EIGHT_TO_72H
    assert lead_bucket(259_200) is LeadBucket.FORTY_EIGHT_TO_72H
    assert lead_bucket(259_201) is None


def test_selection_uses_midpoint_not_residual_and_is_order_invariant() -> None:
    rows = _rows()
    selected = select_calibration_residuals(rows)
    reverse = select_calibration_residuals(tuple(reversed(rows)))
    assert [row.lead_to_valid_coordinate_seconds for row in selected] == [43_200, 129_600, 216_000]
    assert tuple(residual_identity(row) for row in selected) == tuple(
        residual_identity(row) for row in reverse
    )
    assert (
        len(
            {
                (row.local_target_date, lead_bucket(row.lead_to_valid_coordinate_seconds))
                for row in selected
            }
        )
        == 3
    )


def test_artifact_identity_excludes_acquisition_time_and_production_is_zero() -> None:
    rows = _rows()[:1]
    kwargs = dict(
        source="CLIMDW",
        measurement="DAILY_MAX",
        requested_target_start_date=rows[0].local_target_date,
        requested_target_end_date=rows[0].local_target_date,
        actual_catalog_scan_start_date=rows[0].local_target_date,
        actual_catalog_scan_end_date=rows[0].local_target_date,
        authority_identity=rows[0].authority_identity,
        product_family_identity=CalibrationFamily.LEGACY_CHICAGO_MAXT_5KM_YGFZ98.value,
        status="COMPLETE",
        aws_discovery_requests=(),
        archive_catalog_requests=("https://www.ncei.noaa.gov/thredds/catalog.xml",),
        descriptors=(),
        point_csvs=(),
        rejected=(),
        missing_dates=(),
        quality_flagged_outcomes=(),
        rows=rows,
    )
    first = build_coverage_artifact(**kwargs, acquired_at=datetime(2026, 8, 16, 1, tzinfo=UTC))
    second = build_coverage_artifact(**kwargs, acquired_at=datetime(2026, 8, 17, 1, tzinfo=UTC))
    assert first.artifact_sha256() != second.artifact_sha256()
    assert first.production_influence == Decimal("0")
    assert first.selected_residual_rows[0]["residual_id"] == residual_identity(rows[0])


def test_calibration_family_is_material_and_unreviewed_family_fails_closed() -> None:
    rows = _rows()[:1]
    common = dict(
        source="CLIMDW",
        measurement="DAILY_MAX",
        requested_target_start_date=rows[0].local_target_date,
        requested_target_end_date=rows[0].local_target_date,
        actual_catalog_scan_start_date=rows[0].local_target_date,
        actual_catalog_scan_end_date=rows[0].local_target_date,
        authority_identity=rows[0].authority_identity,
        status="COMPLETE",
        aws_discovery_requests=(),
        archive_catalog_requests=(),
        descriptors=(),
        point_csvs=(),
        rejected=(),
        missing_dates=(),
        quality_flagged_outcomes=(),
        rows=rows,
        acquired_at=datetime(2026, 8, 16, tzinfo=UTC),
    )
    legacy = build_coverage_artifact(
        **common,
        product_family_identity=CalibrationFamily.LEGACY_CHICAGO_MAXT_5KM_YGFZ98.value,
    )
    post2020 = build_coverage_artifact(
        **common,
        product_family_identity=CalibrationFamily.POST2020_CHICAGO_MAXT_2P5KM_YGUZ98_03Z.value,
    )
    assert legacy.artifact_sha256() != post2020.artifact_sha256()
    with pytest.raises(Exception, match="reviewed calibration family"):
        build_coverage_artifact(**common, product_family_identity="YGUZ98_KWBN_")


def test_executable_path_is_nonportable_but_tool_and_policy_are_material() -> None:
    rows = _rows()[:1]
    common = dict(
        source="CLIMDW",
        measurement="DAILY_MAX",
        requested_target_start_date=rows[0].local_target_date,
        requested_target_end_date=rows[0].local_target_date,
        actual_catalog_scan_start_date=rows[0].local_target_date,
        actual_catalog_scan_end_date=rows[0].local_target_date,
        authority_identity=rows[0].authority_identity,
        product_family_identity=CalibrationFamily.POST2020_CHICAGO_MAXT_2P5KM_YGUZ98_03Z.value,
        status="COMPLETE",
        aws_discovery_requests=(),
        archive_catalog_requests=(),
        descriptors=(),
        point_csvs=(),
        rejected=(),
        missing_dates=(),
        quality_flagged_outcomes=(),
        rows=rows,
        acquired_at=datetime(2026, 8, 16, tzinfo=UTC),
    )
    first = build_coverage_artifact(
        **common,
        extraction_provenance=(
            {
                "executable_path": "/one/wgrib2",
                "executable_sha256": "same",
                "wgrib2_version": "3.8.0",
                "extraction_policy_version": "policy-v1",
            },
        ),
    )
    second = build_coverage_artifact(
        **common,
        extraction_provenance=(
            {
                "executable_path": "/two/wgrib2",
                "executable_sha256": "same",
                "wgrib2_version": "3.8.0",
                "extraction_policy_version": "policy-v1",
            },
        ),
    )
    changed_tool = build_coverage_artifact(
        **common,
        extraction_provenance=(
            {
                "executable_path": "/two/wgrib2",
                "executable_sha256": "same",
                "wgrib2_version": "3.7.0",
                "extraction_policy_version": "policy-v1",
            },
        ),
    )
    changed_policy = build_coverage_artifact(
        **common,
        extraction_provenance=(
            {
                "executable_path": "/two/wgrib2",
                "executable_sha256": "same",
                "wgrib2_version": "3.8.0",
                "extraction_policy_version": "policy-v2",
            },
        ),
    )
    assert first.artifact_sha256() == second.artifact_sha256()
    assert first.artifact_sha256() != changed_tool.artifact_sha256()
    assert first.artifact_sha256() != changed_policy.artifact_sha256()


def test_artifact_rejects_left_and_right_target_boundary_leakage() -> None:
    rows = _rows()
    leaked_rows = (
        replace(rows[0], local_target_date=rows[0].local_target_date - timedelta(days=1)),
        replace(rows[0], local_target_date=rows[0].local_target_date + timedelta(days=1)),
    )
    for leaked in leaked_rows:
        with pytest.raises(Exception, match="outside"):
            build_coverage_artifact(
                source="CLIMDW",
                measurement="DAILY_MAX",
                requested_target_start_date=rows[0].local_target_date,
                requested_target_end_date=rows[0].local_target_date,
                actual_catalog_scan_start_date=rows[0].local_target_date - timedelta(days=3),
                actual_catalog_scan_end_date=rows[0].local_target_date,
                authority_identity=rows[0].authority_identity,
                product_family_identity=CalibrationFamily.LEGACY_CHICAGO_MAXT_5KM_YGFZ98.value,
                status="COMPLETE",
                aws_discovery_requests=(),
                archive_catalog_requests=(),
                descriptors=(),
                point_csvs=(),
                rejected=(),
                missing_dates=(),
                quality_flagged_outcomes=(),
                rows=(leaked,),
                acquired_at=datetime(2026, 8, 16, tzinfo=UTC),
            )


def test_interrupted_collection_resume_has_same_portable_identity(tmp_path) -> None:
    source, station, descriptor, point, outcome = evidence()
    acquired = datetime(2026, 8, 16, tzinfo=UTC)
    start = date(2018, 6, 20)
    target_dates = (start, start + timedelta(days=1), start + timedelta(days=2))
    base = build_residuals(source, descriptor, point, outcome, station, source, acquired)[0]
    fixture_rows = tuple(
        replace(
            base,
            local_target_date=target_date,
            lead_to_valid_coordinate_seconds=43_200,
            residual_deg_f=Decimal(str(index + 1)),
        )
        for index, target_date in enumerate(target_dates)
    )

    def run(root, interrupt_after=None):
        cache = root / "cache"
        checkpoint_path = root / "collection.checkpoint.json"
        output = root / "coverage.json"
        checkpoint = _read_checkpoint(checkpoint_path)
        completed = set(checkpoint.get("completed_dates", [])) if checkpoint else set()
        for index, target_date in enumerate(target_dates):
            key = target_date.isoformat()
            capture = cache / f"raw-{key}.json"
            if key not in completed:
                _atomic_write(capture, f"fixture:{key}".encode())
                _cache_origin(capture)
                completed.add(key)
            _write_checkpoint(
                checkpoint_path,
                {
                    "acquired_at": acquired.isoformat(),
                    "completed_dates": sorted(completed),
                    "status": "PARTIAL",
                },
            )
            if interrupt_after is not None and index + 1 == interrupt_after:
                raise KeyboardInterrupt
        rows = tuple(row for row in fixture_rows if row.local_target_date.isoformat() in completed)
        artifact = build_coverage_artifact(
            source="CLIMDW",
            measurement="DAILY_MAX",
            requested_target_start_date=start,
            requested_target_end_date=target_dates[-1],
            actual_catalog_scan_start_date=start - timedelta(days=3),
            actual_catalog_scan_end_date=target_dates[-1],
            authority_identity=base.authority_identity,
            product_family_identity=CalibrationFamily.LEGACY_CHICAGO_MAXT_5KM_YGFZ98.value,
            status="COMPLETE",
            aws_discovery_requests=(),
            archive_catalog_requests=(),
            descriptors=(),
            point_csvs=(),
            rejected=(),
            missing_dates=(),
            quality_flagged_outcomes=(),
            rows=rows,
            acquired_at=acquired,
        )
        payload = artifact.content() | {"artifact_sha256": artifact.artifact_sha256()}
        _write_immutable(output, payload)
        _write_checkpoint(
            checkpoint_path,
            {
                "acquired_at": acquired.isoformat(),
                "completed_dates": sorted(completed),
                "status": "COMPLETE",
                "artifact_sha256": payload["artifact_sha256"],
            },
        )
        return artifact, payload

    uninterrupted, uninterrupted_payload = run(tmp_path / "uninterrupted")
    with pytest.raises(KeyboardInterrupt):
        run(tmp_path / "resumed", interrupt_after=1)
    resumed, resumed_payload = run(tmp_path / "resumed")

    assert uninterrupted.artifact_sha256() == resumed.artifact_sha256()
    assert uninterrupted_payload["artifact_sha256"] == resumed_payload["artifact_sha256"]
    assert uninterrupted.selected_residual_ids == resumed.selected_residual_ids
    assert len(resumed.selected_residual_ids) == len(set(resumed.selected_residual_ids)) == 3
    assert (
        _read_checkpoint(tmp_path / "resumed" / "collection.checkpoint.json")["status"]
        == "COMPLETE"
    )
