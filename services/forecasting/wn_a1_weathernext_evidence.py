"""WN-A1 WeatherNext 3 ensemble evidence: acquisition boundary + independent validation.

WeatherNext is a FORECAST SOURCE ONLY. It is never settlement authority here, and its
temperature is never claimed to equal the settlement (currently The Weather Company) value
used to settle a Kalshi contract -- see ``wn_a1_current_daily_high_authority.py`` for the
actual settlement-source binding, which this module never touches or imports.

This module performs no network I/O of its own for validation: ``build_ensemble_evidence``
is a pure function over already-retained rows, mirroring the acquisition/parsing separation
used throughout this repository (see ``weather_current_cycle_acquisition.py``). A caller-
supplied ``zarr_reader`` performs the actual GCS read; production code should pass
``gcs_zarr_reader`` below, which requires the optional ``weathernext`` dependency group
(``gcsfs``, ``zarr``, ``google-cloud-storage``). Those packages, and any Google Cloud
credential, were NOT available in this development environment (no ``gcloud``, no
``GOOGLE_APPLICATION_CREDENTIALS``, packages not installed), so ``gcs_zarr_reader`` is
UNTESTED against the real WeatherNext bucket here -- see the WN-A1 review doc's blockers
section. Tests exclusively inject a fake in-memory reader and never require the optional
dependency group or network access.

Expected source layout (from the supplied Google starter guide), never downloaded in full:

    gs://weathernext3_spatial/weathernext_3_0_0/zarr/2026_to_present/
        <YYYYMMDD_HHhr_XX_preds>/predictions.zarr/

Only the smallest Chicago spatial slice, the exact ``station_head_temperature_2m``
variable, one exact initialization, and the exact valid times needed for one target local
day are ever requested.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from services.market_universe.domain import stable_hash

from .wn_a1_domain import PRODUCTION_INFLUENCE, RESEARCH_ONLY, WnA1Error

MODEL = "weathernext_3_0_0"
VARIABLE = "station_head_temperature_2m"
UNIT = "K"
MEMBER_COUNT = 64
GRID_DEGREES = Decimal("0.05")

_SOURCE_OBJECT_RE = re.compile(
    r"\Ags://weathernext3_spatial/weathernext_3_0_0/zarr/2026_to_present/"
    r"(?P<yyyymmdd>\d{8})_(?P<hour>\d{2})hr_(?P<member>XX|[0-9]{2})_preds/predictions\.zarr/?\Z"
)


def validate_source_object(source_object: str, init_time: datetime) -> None:
    """Fail closed unless ``source_object`` is the exact reviewed layout for ``init_time``."""
    match = _SOURCE_OBJECT_RE.fullmatch(source_object)
    if match is None:
        raise WnA1Error("WeatherNext source object does not match the reviewed layout")
    _aware(init_time)
    init_utc = init_time.astimezone(UTC)
    if match.group("yyyymmdd") != init_utc.strftime("%Y%m%d") or match.group(
        "hour"
    ) != init_utc.strftime("%H"):
        raise WnA1Error("WeatherNext source object does not encode the claimed init_time")


@dataclass(frozen=True, slots=True)
class MemberHourValue:
    sample: int
    lead_time_hours: int
    lead_subtime_minutes: int
    valid_time: datetime
    value_kelvin: Decimal


@dataclass(frozen=True, slots=True)
class WeatherNextEnsembleEvidence:
    model: str
    source_object: str
    init_time: datetime
    acquired_at: datetime
    variable: str
    latitude: Decimal
    longitude: Decimal
    grid_degrees: Decimal
    unit: str
    members: tuple[MemberHourValue, ...]
    source_content_hash: str
    evidence_identity: str
    research_only: bool = RESEARCH_ONLY
    production_influence: Decimal = PRODUCTION_INFLUENCE


class EnsembleStatus(StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"


@dataclass(frozen=True, slots=True)
class EnsembleAcquisitionResult:
    status: EnsembleStatus
    evidence: WeatherNextEnsembleEvidence | None
    missing_samples_by_hour: Mapping[str, tuple[int, ...]]


def build_ensemble_evidence(
    *,
    model: str,
    source_object: str,
    init_time: datetime,
    acquired_at: datetime,
    variable: str,
    latitude: Decimal,
    longitude: Decimal,
    unit: str,
    raw_rows: Sequence[Mapping[str, object]],
    source_content_hash: str,
) -> EnsembleAcquisitionResult:
    """Validate and bind one exact WeatherNext ensemble slice; never smooths or subsets.

    Hard rejects (``WnA1Error``): wrong model/version, wrong variable, wrong/mismatched
    init_time, invalid unit, out-of-range sample id, duplicate sample within one valid
    hour, or a lead_time/lead_subtime/valid_time reconstruction mismatch. Missing members
    for one or more retained hours are NOT an error -- they are reported as
    ``EnsembleStatus.INCOMPLETE`` so the caller can alert ``DATA NOT READY`` rather than
    silently dropping a sample.
    """
    if model != MODEL:
        raise WnA1Error(f"unreviewed WeatherNext model/version: {model!r}")
    if variable != VARIABLE:
        raise WnA1Error(f"unreviewed WeatherNext variable: {variable!r}")
    if unit != UNIT:
        raise WnA1Error(f"unsupported WeatherNext unit: {unit!r}; must be Kelvin ('K')")
    validate_source_object(source_object, init_time)
    _aware(init_time)
    _aware(acquired_at)
    init_utc = init_time.astimezone(UTC)
    if not isinstance(source_content_hash, str) or len(source_content_hash) != 64:
        raise WnA1Error("WeatherNext source content hash missing or malformed")

    by_hour: dict[datetime, dict[int, MemberHourValue]] = {}
    for row in raw_rows:
        sample = row.get("sample")
        lead_hours = row.get("lead_time_hours")
        lead_minutes = row.get("lead_subtime_minutes")
        value = row.get("value_kelvin")
        valid_time = row.get("valid_time")
        if (
            isinstance(sample, bool)
            or not isinstance(sample, int)
            or not (0 <= sample < MEMBER_COUNT)
        ):
            raise WnA1Error("WeatherNext member sample id out of the reviewed 0..63 range")
        if (
            isinstance(lead_hours, bool)
            or not isinstance(lead_hours, int)
            or lead_hours < 0
            or isinstance(lead_minutes, bool)
            or not isinstance(lead_minutes, int)
            or not (0 <= lead_minutes < 60)
        ):
            raise WnA1Error("WeatherNext lead_time/lead_subtime malformed")
        if not isinstance(valid_time, datetime):
            raise WnA1Error("WeatherNext valid_time missing or malformed")
        _aware(valid_time)
        expected_valid = init_utc + timedelta(hours=lead_hours, minutes=lead_minutes)
        if valid_time.astimezone(UTC) != expected_valid:
            raise WnA1Error(
                "WeatherNext valid_time does not reconstruct from init_time + "
                "lead_time + lead_subtime"
            )
        if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
            raise WnA1Error("WeatherNext member value must be a positive finite Kelvin Decimal")
        hour_key = expected_valid
        bucket = by_hour.setdefault(hour_key, {})
        if sample in bucket:
            raise WnA1Error(f"duplicate WeatherNext member sample {sample} for one valid hour")
        bucket[sample] = MemberHourValue(
            sample, lead_hours, lead_minutes, valid_time.astimezone(UTC), value
        )

    missing: dict[str, tuple[int, ...]] = {}
    for hour_key, bucket in by_hour.items():
        gap = tuple(i for i in range(MEMBER_COUNT) if i not in bucket)
        if gap:
            missing[hour_key.isoformat()] = gap
    if missing:
        return EnsembleAcquisitionResult(EnsembleStatus.INCOMPLETE, None, missing)

    members = tuple(
        bucket[sample]
        for hour_key in sorted(by_hour)
        for sample in range(MEMBER_COUNT)
        for bucket in (by_hour[hour_key],)
    )
    identity = stable_hash(
        (
            "wn-a1-weathernext-ensemble-evidence-v1",
            model,
            source_object,
            init_utc.isoformat(),
            variable,
            str(latitude),
            str(longitude),
            unit,
            source_content_hash,
            tuple(
                (
                    m.sample,
                    m.lead_time_hours,
                    m.lead_subtime_minutes,
                    m.valid_time.isoformat(),
                    str(m.value_kelvin),
                )
                for m in members
            ),
        )
    )
    evidence = WeatherNextEnsembleEvidence(
        model=model,
        source_object=source_object,
        init_time=init_utc,
        acquired_at=acquired_at.astimezone(UTC),
        variable=variable,
        latitude=latitude,
        longitude=longitude,
        grid_degrees=GRID_DEGREES,
        unit=unit,
        members=members,
        source_content_hash=source_content_hash,
        evidence_identity=identity,
    )
    return EnsembleAcquisitionResult(EnsembleStatus.COMPLETE, evidence, {})


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise WnA1Error("WeatherNext evidence timestamp must be timezone-aware")


ZarrReader = Callable[
    [str, datetime, Decimal, Decimal, datetime, datetime],
    tuple[Sequence[Mapping[str, object]], str],
]


def gcs_zarr_reader(
    source_object: str,
    init_time: datetime,
    latitude: Decimal,
    longitude: Decimal,
    window_start: datetime,
    window_end: datetime,
) -> tuple[Sequence[Mapping[str, object]], str]:
    """Read the smallest Chicago slice of one WeatherNext Zarr store from GCS.

    Requires the optional ``weathernext`` dependency group. NOT exercised against the real
    WeatherNext bucket in this development environment (no Google Cloud credentials, no
    ``gcloud``/``gsutil``, and ``gcsfs``/``zarr``/``google-cloud-storage`` were not
    installed) -- see the WN-A1 review doc's blockers section before relying on this
    function in production research use. Never downloads global data: it opens only the
    exact object path, selects the nearest 0.05-degree grid cell to ``(latitude,
    longitude)``, and reads only ``station_head_temperature_2m`` for the requested window.
    """
    try:
        import gcsfs  # type: ignore[import-not-found]
        import zarr  # type: ignore[import-not-found]
    except ImportError as exc:
        raise WnA1Error(
            "WeatherNext GCS reader requires the optional 'weathernext' dependency group "
            "(gcsfs, zarr, google-cloud-storage); install it to use gcs_zarr_reader"
        ) from exc

    validate_source_object(source_object, init_time)
    store = gcsfs.GCSFileSystem().get_mapper(source_object.removeprefix("gs://"))
    group = zarr.open(store, mode="r")
    dataset = group[VARIABLE]
    lat_index = round(float(latitude) / float(GRID_DEGREES))
    lon_index = round(float(longitude) / float(GRID_DEGREES))
    rows: list[dict[str, object]] = []
    lead_times = group["lead_time"][:]
    lead_subtimes = group["lead_subtime"][:]
    for lead_index, lead_hours in enumerate(lead_times):
        for subtime_index, lead_minutes in enumerate(lead_subtimes):
            valid_time = init_time.astimezone(UTC) + timedelta(
                hours=int(lead_hours), minutes=int(lead_minutes)
            )
            if not (window_start <= valid_time < window_end):
                continue
            for sample in range(MEMBER_COUNT):
                kelvin = dataset[sample, lead_index, subtime_index, lat_index, lon_index]
                rows.append(
                    {
                        "sample": sample,
                        "lead_time_hours": int(lead_hours),
                        "lead_subtime_minutes": int(lead_minutes),
                        "valid_time": valid_time,
                        "value_kelvin": Decimal(str(float(kelvin))),
                    }
                )
    content_hash = stable_hash({"source_object": source_object, "rows": len(rows)})
    return rows, content_hash
