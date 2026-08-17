"""Operator-only, bounded NCEI collection for M27C calibration coverage.

The script is deliberately not imported by forecasting/runtime code.  It uses
only public GET requests and hands every descriptor, point CSV, and outcome to
the reviewed Part 2B1 parsers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.forecasting.domain import ForecastError
from services.forecasting.weather_calibration import (
    CalibrationMeasurement,
    WeatherCalibrationResidual,
    build_residuals,
    parse_ghcnd_daily,
    parse_ndfd_descriptor,
    parse_ndfd_point_csv,
)
from services.forecasting.weather_calibration_coverage import (
    CalibrationFamily,
    build_coverage_artifact,
)
from services.forecasting.weather_calibration_grib import (
    POST2020_GRIB_FAMILY,
    POST2020_MINT_GRIB_FAMILY,
    build_grib_residuals,
    build_min_t_grib_residuals,
    parse_wgrib2_max_t_evidence,
    parse_wgrib2_min_t_evidence,
    validate_kmdw_points,
)
from services.forecasting.weather_source_authority import (
    PHYSICAL_WEATHER_SOURCES,
    parse_nws_station,
)

NCEI_HOST = "www.ncei.noaa.gov"
AWS_HOST = "noaa-ndfd-pds.s3.amazonaws.com"
NCEI_ROOT = "https://www.ncei.noaa.gov/thredds"
GHCND_ROOT = "https://www.ncei.noaa.gov/pub/data/ghcn/daily/all"
# The first live probe is intentionally bounded.  The full archive expansion
# can raise this after review; skipped catalog candidates remain a gap rather
# than silently becoming calibration evidence.
MAX_DATASETS_PER_DAY = 12
RETRIES = 2
TIMEOUT_SECONDS = 20
USER_AGENT = "kalsh3-m27c-research-coverage/1.0 (public NOAA reads only)"
ALLOWED_HOSTS = {NCEI_HOST, "api.weather.gov", AWS_HOST}
WGRIB2_VERSION = "3.8.0"
EXTRACTION_POLICY_VERSION = "m27c-wgrib2-maxt-extraction-v1"
MINT_EXTRACTION_POLICY_VERSION = "m27c-wgrib2-mint-extraction-v1"


class _AllowlistedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: urllib.request.Request, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> urllib.request.Request | None:
        try:
            _validate_public_url(newurl)
        except ForecastError as exc:
            raise ForecastError("redirect escaped the allowlisted HTTPS host") from exc
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_AllowlistedRedirect())


def _validate_public_url(url: str) -> urllib.parse.SplitResult:
    """Validate an exact, credential-free HTTPS public-source authority."""
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ForecastError("network URL has malformed authority") from exc
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.hostname not in ALLOWED_HOSTS
    ):
        raise ForecastError("network URL is not an allowlisted HTTPS public source")
    return parsed


def _get(url: str, *, cache: Path | None = None) -> bytes:
    _validate_public_url(url)
    if cache is not None and cache.exists():
        return cache.read_bytes()
    error: Exception | None = None
    for attempt in range(RETRIES + 1):
        try:
            request = urllib.request.Request(  # noqa: S310
                url, headers={"User-Agent": USER_AGENT}
            )
            with _OPENER.open(request, timeout=TIMEOUT_SECONDS) as response:
                payload = response.read()
            if cache is not None:
                cache.parent.mkdir(parents=True, exist_ok=True)
                if cache.exists() and cache.read_bytes() != payload:
                    raise ForecastError("conflicting cached public evidence")
                _atomic_write(cache, payload)
            return cast(bytes, payload)
        except (OSError, urllib.error.URLError, urllib.error.HTTPError, ForecastError) as exc:
            error = exc
            if attempt < RETRIES:
                time.sleep(0.5 * (attempt + 1))
    raise ForecastError(f"bounded public request failed: {url}") from error


def _catalog_url(day: date) -> str:
    branch = "model-ndfd-file/access" if day >= date(2020, 4, 16) else "model-ndfd-file-old"
    return f"{NCEI_ROOT}/catalog/{branch}/{day:%Y%m}/{day:%Y%m%d}/catalog.xml"


def _datasets(
    catalog: bytes, family: CalibrationFamily, limit: int = MAX_DATASETS_PER_DAY
) -> tuple[str, ...]:
    if b"<!DOCTYPE" in catalog.upper() or b"<!ENTITY" in catalog.upper():
        raise ForecastError("unsafe catalog XML")
    root = ET.fromstring(catalog)  # noqa: S314 — DTD/ENTITY input is rejected above.
    names = tuple(
        sorted(
            {node.attrib["name"] for node in root.iter() if "KWBN_" in node.attrib.get("name", "")}
        )
    )
    prefix = {
        CalibrationFamily.LEGACY_CHICAGO_MAXT_5KM_YGFZ98: "YGFZ98_KWBN_",
        CalibrationFamily.POST2020_CHICAGO_MAXT_2P5KM_YGUZ98_03Z: "YGUZ98_KWBN_",
        CalibrationFamily.POST2020_CHICAGO_MINT_2P5KM_YHUZ98_04Z: "YHUZ98_KWBN_",
    }[family]
    # The WMO prefix is discovery-only; every descriptor still passes the
    # strict Part 2B1 semantic parser below.
    return tuple(name for name in names if name.startswith(prefix))[:limit]


def _url_for_dataset(dataset: str, suffix: str = "dataset.xml") -> str:
    parts = dataset.split("_")
    if len(parts) < 3:
        raise ForecastError("catalog dataset identity is malformed")
    ymd = parts[-1][:8]
    branch = "model-ndfd-file/access" if ymd >= "20200416" else "model-ndfd-file-old"
    return f"{NCEI_ROOT}/ncss/grid/{branch}/{ymd[:6]}/{ymd}/{dataset}/{suffix}"


def _aws_index_url(
    day: date, measurement: CalibrationMeasurement = CalibrationMeasurement.DAILY_MAX
) -> str:
    product_path = "mint" if measurement is CalibrationMeasurement.DAILY_MIN else "maxt"
    query = urllib.parse.urlencode(
        {
            "list-type": "2",
            "prefix": f"wmo/{product_path}/{day:%Y/%m/%d}/",
            "max-keys": "1000",
        }
    )
    return f"https://{AWS_HOST}/?{query}"


def _aws_candidates(payload: bytes, family: CalibrationFamily) -> tuple[str, ...]:
    if b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
        raise ForecastError("unsafe AWS discovery XML")
    root = ET.fromstring(payload)  # noqa: S314 — DTD/ENTITY input is rejected above.
    prefix = {
        CalibrationFamily.LEGACY_CHICAGO_MAXT_5KM_YGFZ98: "YGFZ98_KWBN_",
        CalibrationFamily.POST2020_CHICAGO_MAXT_2P5KM_YGUZ98_03Z: "YGUZ98_KWBN_",
        CalibrationFamily.POST2020_CHICAGO_MINT_2P5KM_YHUZ98_04Z: "YHUZ98_KWBN_",
    }[family]
    return tuple(
        sorted(
            {
                key.text.rsplit("/", 1)[-1]
                for key in root.findall("{*}Contents/{*}Key")
                if key.text and key.text.rsplit("/", 1)[-1].startswith(prefix)
            }
        )
    )


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    if path.exists() and path.read_bytes() != payload:
        raise ForecastError("conflicting cached public evidence")
    if not path.exists():
        temporary.write_bytes(payload)
        os.replace(temporary, path)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _point_url(dataset: str, descriptor: Any, station: Any) -> str:
    # NCSS exposes the end of a 12-hour interval as the returned coordinate;
    # its request range is six hours later than the descriptor coordinate.
    first = (
        (descriptor.valid_time_coordinates[0] + timedelta(hours=6))
        .isoformat()
        .replace("+00:00", "Z")
    )
    last = (
        (descriptor.valid_time_coordinates[-1] + timedelta(hours=6))
        .isoformat()
        .replace("+00:00", "Z")
    )
    query = urllib.parse.urlencode(
        {
            "var": descriptor.variable_name,
            "latitude": str(station.latitude),
            "longitude": str(station.longitude),
            "time_start": first,
            "time_end": last,
            "timeStride": "1",
            "accept": "csv",
        }
    )
    return _url_for_dataset(dataset, f"?{query}")


def collect(args: argparse.Namespace) -> dict[str, Any]:
    source = PHYSICAL_WEATHER_SOURCES.get(args.source)
    if source is None:
        raise ForecastError("source is not a reviewed physical source")
    measurement = CalibrationMeasurement(args.measurement)
    family = CalibrationFamily(args.family)
    if args.start_date > args.end_date:
        raise ForecastError("start date must not exceed end date")
    if args.max_datasets_per_day <= 0 or args.max_datasets_per_day > MAX_DATASETS_PER_DAY:
        raise ForecastError("dataset limit is outside the bounded collector limit")
    checkpoint_path = args.checkpoint or args.output.with_suffix(".checkpoint.json")
    checkpoint = _read_checkpoint(checkpoint_path)
    if checkpoint is not None:
        expected = {
            "source": args.source,
            "measurement": args.measurement,
            "family": family.value,
            "requested_target_start_date": args.start_date.isoformat(),
            "requested_target_end_date": args.end_date.isoformat(),
        }
        if any(checkpoint.get(key) != value for key, value in expected.items()):
            raise ForecastError("checkpoint configuration conflicts with requested collection")
        acquired = datetime.fromisoformat(checkpoint["acquired_at"])
    else:
        acquired = datetime.now(UTC)
    cache = args.output.parent / (args.output.stem + ".cache")
    station_payload = json.loads(
        _get(
            f"https://api.weather.gov/stations/{source.nws_station_id}",
            cache=cache / "station.json",
        )
    )
    station = parse_nws_station(source, station_payload, acquired)
    outcome = parse_ghcnd_daily(
        _get(f"{GHCND_ROOT}/{source.ghcnd_station_id}.dly", cache=cache / "station.dly"),
        source,
        acquired,
    )
    if family in {
        CalibrationFamily.POST2020_CHICAGO_MAXT_2P5KM_YGUZ98_03Z,
        CalibrationFamily.POST2020_CHICAGO_MINT_2P5KM_YHUZ98_04Z,
    }:
        return _collect_post2020_raw_grib(args, source, station, outcome, acquired, cache, family)
    rows: list[WeatherCalibrationResidual] = []
    descriptors: list[dict[str, Any]] = []
    points: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    catalogs: list[str] = []
    aws_requests: list[str] = []
    scan_start = args.start_date - timedelta(days=3)
    scan_day = scan_start
    while scan_day <= args.end_date:
        catalog_url = _catalog_url(scan_day)
        catalogs.append(catalog_url)
        if scan_day >= date(2020, 4, 16):
            aws_url = _aws_index_url(scan_day)
            aws_requests.append(aws_url)
            try:
                aws_names = _aws_candidates(
                    _get(aws_url, cache=cache / "aws" / f"{scan_day:%Y%m%d}.xml"), family
                )
                if not aws_names:
                    rejected.append(
                        {"dataset": str(scan_day), "reason": "AWS reviewed YGFZ98 family absent"}
                    )
            except (ForecastError, ET.ParseError, UnicodeError) as exc:
                rejected.append({"dataset": str(scan_day), "reason": f"AWS discovery: {exc}"})
        try:
            datasets = _datasets(
                _get(catalog_url, cache=cache / "catalogs" / f"{scan_day:%Y%m%d}.xml"),
                family,
                args.max_datasets_per_day,
            )
        except ForecastError as exc:
            rejected.append({"dataset": str(scan_day), "reason": str(exc)})
            scan_day += timedelta(days=1)
            continue
        for dataset in datasets:
            try:
                descriptor_url = _url_for_dataset(dataset)
                descriptor_raw = _get(
                    descriptor_url, cache=cache / "descriptors" / f"{dataset}.xml"
                )
                descriptor = parse_ndfd_descriptor(descriptor_raw, source, measurement, acquired)
                descriptors.append(
                    {
                        "dataset": dataset,
                        "url": descriptor_url,
                        "hash": descriptor.source_hash,
                        "forecast_reference_time": descriptor.forecast_reference_time,
                    }
                )
                point_url = _point_url(dataset, descriptor, station)
                point_raw = _get(point_url, cache=cache / "points" / f"{dataset}.csv")
                point = parse_ndfd_point_csv(point_raw, descriptor, station, acquired)
                points.append({"dataset": dataset, "url": point_url, "hash": point.source_hash})
                rows.extend(
                    row
                    for row in build_residuals(
                        source, descriptor, point, outcome, station, source, acquired
                    )
                    if args.start_date <= row.local_target_date <= args.end_date
                )
            except (ForecastError, ET.ParseError, UnicodeError) as exc:
                rejected.append({"dataset": dataset, "reason": str(exc)})
        scan_day += timedelta(days=1)
        _write_checkpoint(
            checkpoint_path,
            {
                "source": args.source,
                "measurement": args.measurement,
                "family": family.value,
                "requested_target_start_date": args.start_date.isoformat(),
                "requested_target_end_date": args.end_date.isoformat(),
                "actual_catalog_scan_start_date": scan_start.isoformat(),
                "actual_catalog_scan_end_date": args.end_date.isoformat(),
                "acquired_at": acquired.isoformat(),
                "status": "PARTIAL",
                "completed_scan_dates": [
                    day.isoformat() for day in _dates(scan_start, scan_day - timedelta(days=1))
                ],
            },
        )
    missing = tuple(
        day
        for day in _dates(args.start_date, args.end_date)
        if day not in {row.local_target_date for row in rows}
    )
    artifact = build_coverage_artifact(
        source=source.settlement_product_id,
        measurement=measurement.value,
        requested_target_start_date=args.start_date,
        requested_target_end_date=args.end_date,
        actual_catalog_scan_start_date=scan_start,
        actual_catalog_scan_end_date=args.end_date,
        authority_identity=source.authority_identity,
        product_family_identity=family.value,
        status="COMPLETE",
        aws_discovery_requests=tuple(aws_requests),
        archive_catalog_requests=tuple(catalogs),
        descriptors=tuple(descriptors),
        point_csvs=tuple(points),
        rejected=tuple(rejected),
        missing_dates=missing,
        quality_flagged_outcomes=(),
        rows=tuple(rows),
        acquired_at=acquired,
    )
    payload = artifact.content() | {"artifact_sha256": artifact.artifact_sha256()}
    _write_immutable(args.output, payload)
    _write_checkpoint(
        checkpoint_path,
        {
            "source": args.source,
            "measurement": args.measurement,
            "family": family.value,
            "requested_target_start_date": args.start_date.isoformat(),
            "requested_target_end_date": args.end_date.isoformat(),
            "actual_catalog_scan_start_date": scan_start.isoformat(),
            "actual_catalog_scan_end_date": args.end_date.isoformat(),
            "acquired_at": acquired.isoformat(),
            "status": "COMPLETE",
            "completed_scan_dates": [day.isoformat() for day in _dates(scan_start, args.end_date)],
            "artifact_sha256": payload["artifact_sha256"],
        },
    )
    return payload


def _collect_post2020_raw_grib(
    args: argparse.Namespace,
    source: Any,
    station: Any,
    outcome: Any,
    acquired: datetime,
    cache: Path,
    family: CalibrationFamily,
) -> dict[str, Any]:
    is_min = family is CalibrationFamily.POST2020_CHICAGO_MINT_2P5KM_YHUZ98_04Z
    family_identity = POST2020_MINT_GRIB_FAMILY if is_min else POST2020_GRIB_FAMILY
    archive_prefix = "mint" if is_min else "maxt"
    expected_name_hour = "03" if is_min else "02"
    executable, executable_sha256 = _resolve_wgrib2(getattr(args, "wgrib2_bin", None))
    scan_start = args.start_date - timedelta(days=3)
    rows: list[WeatherCalibrationResidual] = []
    raw_objects: list[dict[str, Any]] = []
    accepted_records: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    requests: list[str] = []
    extraction_hashes: list[str] = []
    checkpoint_path = args.checkpoint or args.output.with_suffix(".checkpoint.json")
    for archive_day in _dates(scan_start, args.end_date):
        aws_url = _aws_index_url(archive_day, CalibrationMeasurement(args.measurement))
        requests.append(aws_url)
        try:
            names = _aws_candidates(
                _get(aws_url, cache=cache / "aws" / f"{archive_day:%Y%m%d}.xml"),
                family,
            )
            candidates = tuple(
                name for name in names if name.rsplit("_", 1)[-1][8:10] == expected_name_hour
            )
            valid: list[tuple[str, str, bytes, Any, str]] = []
            candidate_errors: list[str] = []
            for name in candidates:
                url = f"https://{AWS_HOST}/wmo/{archive_prefix}/{archive_day:%Y/%m/%d}/{name}"
                raw_path = cache / "raw-grib" / f"{name}.grib2"
                try:
                    raw = _get(url, cache=raw_path)
                    raw_hash = _sha256(raw)
                    raw_objects.append(
                        {
                            "url": url,
                            "object_key": url.split(".com/", 1)[1],
                            "acquired_at": acquired,
                            "byte_length": len(raw),
                            "sha256": raw_hash,
                            "family_identity": family_identity,
                            "research_only": True,
                            "production_influence": "0",
                            "cache_status": _cache_origin(raw_path),
                        }
                    )
                    extraction = _run_wgrib2(executable, raw_path, station)
                    extraction_hash = _sha256(extraction.encode())
                    if is_min:
                        evidence = parse_wgrib2_min_t_evidence(
                            extraction,
                            family_identity=family_identity,
                            extraction_policy_version=MINT_EXTRACTION_POLICY_VERSION,
                            wgrib2_version=WGRIB2_VERSION,
                            raw_grib_sha256=raw_hash,
                            extraction_sha256=extraction_hash,
                        )
                    else:
                        evidence = parse_wgrib2_max_t_evidence(
                            extraction,
                            family_identity=family_identity,
                            extraction_policy_version=EXTRACTION_POLICY_VERSION,
                            wgrib2_version=WGRIB2_VERSION,
                            raw_grib_sha256=raw_hash,
                            extraction_sha256=extraction_hash,
                        )
                    validate_kmdw_points(evidence, station.latitude, station.longitude)
                    valid.append((name, url, raw, evidence, extraction_hash))
                except (ForecastError, ET.ParseError, UnicodeError) as exc:
                    candidate_errors.append(f"{name}: {exc}")
            if len(valid) != 1:
                reason = (
                    "zero or multiple valid 04Z YHUZ98 candidates"
                    if is_min
                    else "zero or multiple valid 03Z YGUZ98 candidates"
                )
                if valid:
                    reason += "; valid candidates=" + ",".join(item[0] for item in valid)
                if candidate_errors:
                    reason += "; " + " | ".join(candidate_errors)
                raise ForecastError(reason)
            _name, _url, _raw, evidence, extraction_hash = valid[0]
            residuals = (
                build_min_t_grib_residuals(evidence, source, outcome, acquired)
                if is_min
                else build_grib_residuals(evidence, source, outcome, acquired)
            )
            rows.extend(
                row
                for row in residuals
                if args.start_date <= row.local_target_date <= args.end_date
            )
            extraction_hashes.append(extraction_hash)
            accepted_records.extend(_record_json(record) for record in evidence.records)
        except (ForecastError, ET.ParseError, UnicodeError) as exc:
            rejected.append({"dataset": str(archive_day), "reason": str(exc)})
        _write_checkpoint(
            checkpoint_path,
            {
                "source": args.source,
                "measurement": args.measurement,
                "family": family_identity,
                "requested_target_start_date": args.start_date.isoformat(),
                "requested_target_end_date": args.end_date.isoformat(),
                "actual_catalog_scan_start_date": scan_start.isoformat(),
                "actual_catalog_scan_end_date": args.end_date.isoformat(),
                "acquired_at": acquired.isoformat(),
                "status": "PARTIAL",
                "completed_scan_dates": [
                    day.isoformat() for day in _dates(scan_start, archive_day)
                ],
            },
        )
    missing = tuple(
        day
        for day in _dates(args.start_date, args.end_date)
        if day not in {row.local_target_date for row in rows}
    )
    artifact = build_coverage_artifact(
        source=source.settlement_product_id,
        measurement=args.measurement,
        requested_target_start_date=args.start_date,
        requested_target_end_date=args.end_date,
        actual_catalog_scan_start_date=scan_start,
        actual_catalog_scan_end_date=args.end_date,
        authority_identity=source.authority_identity,
        product_family_identity=family_identity,
        status="COMPLETE",
        aws_discovery_requests=tuple(requests),
        archive_catalog_requests=(),
        descriptors=(),
        point_csvs=(),
        rejected=tuple(rejected),
        missing_dates=missing,
        quality_flagged_outcomes=(),
        rows=tuple(rows),
        acquired_at=acquired,
        raw_grib_objects=tuple(raw_objects),
        accepted_forecast_records=tuple(accepted_records),
        extraction_provenance=(
            {
                "wgrib2_version": WGRIB2_VERSION,
                "executable_path": executable,
                "executable_sha256": executable_sha256,
                "extraction_policy_version": MINT_EXTRACTION_POLICY_VERSION
                if is_min
                else EXTRACTION_POLICY_VERSION,
                "extraction_sha256": tuple(extraction_hashes),
            },
        ),
        usable_outcome_count=sum(
            1
            for observation in outcome.observations
            if observation.measurement is CalibrationMeasurement(args.measurement)
            and observation.usable
            and args.start_date <= observation.local_date <= args.end_date
        ),
    )
    payload = artifact.content() | {"artifact_sha256": artifact.artifact_sha256()}
    _write_immutable(args.output, payload)
    _write_checkpoint(
        checkpoint_path,
        {
            "source": args.source,
            "measurement": args.measurement,
            "family": family_identity,
            "requested_target_start_date": args.start_date.isoformat(),
            "requested_target_end_date": args.end_date.isoformat(),
            "actual_catalog_scan_start_date": scan_start.isoformat(),
            "actual_catalog_scan_end_date": args.end_date.isoformat(),
            "acquired_at": acquired.isoformat(),
            "status": "COMPLETE",
            "completed_scan_dates": [day.isoformat() for day in _dates(scan_start, args.end_date)],
            "artifact_sha256": payload["artifact_sha256"],
        },
    )
    return payload


def _run_wgrib2(executable: str, raw_path: Path, station: Any) -> str:
    command = [
        executable,
        str(raw_path),
        "-s",
        "-T",
        "-RT",
        "-varX",
        "-process",
        "-code_table_4.10",
        "-code_table_4.11",
        "-start_FT",
        "-end_FT",
        "-VT",
        "-lon",
        str(station.longitude),
        str(station.latitude),
        "-grid",
    ]
    completed = subprocess.run(  # noqa: S603 — executable is shutil.which("wgrib2").
        command,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        raise ForecastError("wgrib2 extraction failed")
    return completed.stdout


def _resolve_wgrib2(requested: str | None) -> tuple[str, str | None]:
    executable: str
    if requested is not None:
        candidate = Path(requested)
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            raise ForecastError("explicit wgrib2 path is not an executable regular file")
        executable = requested
    else:
        resolved = shutil.which("wgrib2")
        if resolved is None:
            raise ForecastError("wgrib2 3.8.0 is required for post-2020 raw GRIB collection")
        executable = resolved
    version_output = subprocess.run(  # noqa: S603 — path is explicit or shutil.which output.
        [executable, "-version"],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )
    if (version_output.stdout + version_output.stderr).strip() != WGRIB2_VERSION:
        raise ForecastError("wgrib2 executable is not the reviewed 3.8.0 version")
    return executable, _executable_sha256(executable)


def _record_json(record: Any) -> dict[str, Any]:
    return {
        "record_number": record.record_number,
        "reference_time": record.reference_time,
        "interval_start": record.interval_start,
        "interval_end": record.interval_end,
        "verification_time": record.verification_time,
        "valid_time_coordinate": record.midpoint,
        "lead_to_interval_start_seconds": record.lead_to_interval_start_seconds,
        "lead_to_midpoint_seconds": record.lead_to_midpoint_seconds,
        "lead_to_interval_end_seconds": record.lead_to_interval_end_seconds,
        "variable": record.variable,
        "level": record.level,
        "parameter": record.parameter,
        "unit": record.unit,
        "generating_process_code": record.generating_process_code,
        "statistical_process_code": record.statistical_process_code,
        "time_processing_code": record.time_processing_code,
        "latitude": record.latitude,
        "raw_longitude": record.raw_longitude,
        "signed_longitude": record.signed_longitude,
        "forecast_kelvin": record.kelvin,
        "grid_template": record.grid_template,
        "nx": record.nx,
        "ny": record.ny,
        "dx": record.dx,
        "dy": record.dy,
    }


def _executable_sha256(path: str) -> str | None:
    try:
        return _sha256(Path(path).read_bytes())
    except OSError:
        return None


def _cache_origin(path: Path) -> str:
    metadata = path.with_name(path.name + ".meta.json")
    if metadata.exists():
        try:
            value = json.loads(metadata.read_text())
            origin = value.get("cache_origin")
            if origin in {"DOWNLOADED", "REUSED"}:
                return cast(str, origin)
        except (OSError, json.JSONDecodeError, AttributeError) as exc:
            raise ForecastError("raw GRIB cache metadata is corrupt") from exc
        raise ForecastError("raw GRIB cache metadata is malformed")
    _write_immutable(metadata, {"cache_origin": "DOWNLOADED"})
    return "DOWNLOADED"


def _read_checkpoint(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ForecastError("checkpoint is corrupt") from exc
    if not isinstance(value, dict) or not isinstance(value.get("acquired_at"), str):
        raise ForecastError("checkpoint is malformed")
    return value


def _write_checkpoint(path: Path, value: dict[str, Any]) -> None:
    payload = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _write_immutable(path: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, sort_keys=True, indent=2, default=str) + "\n"
    if path.exists() and path.read_text() != encoded:
        raise ForecastError("output exists with conflicting content")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        _atomic_write(path, encoded.encode())


def _dates(start: date, end: date) -> Iterator[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bounded operator-only M27C weather coverage collector"
    )
    parser.add_argument("--source", required=True, choices=["CLIMDW"])
    parser.add_argument(
        "--measurement",
        required=True,
        choices=[measurement.value for measurement in CalibrationMeasurement],
    )
    parser.add_argument(
        "--family", required=True, choices=[family.value for family in CalibrationFamily]
    )
    parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--wgrib2-bin", type=str)
    parser.add_argument("--max-datasets-per-day", type=int, default=MAX_DATASETS_PER_DAY)
    print(json.dumps(collect(parser.parse_args()), sort_keys=True, indent=2, default=str))


if __name__ == "__main__":
    main()
