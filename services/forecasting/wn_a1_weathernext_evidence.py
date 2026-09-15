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

import os
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

# Chicago Midway (KMDW / CLIMDW), the same physical station WN-A1's Kalshi authority module
# binds to -- pure geography, requested independently of which entity currently settles the
# contract. See wn_a1_current_daily_high_authority.py's STATION_ID/NWS_STATION_ID docstring.
CHICAGO_STATION_LATITUDE = Decimal("41.80")
CHICAGO_STATION_LONGITUDE = Decimal("-87.75")

# Transport billing configuration ONLY -- never forecast authority, never part of any
# evidence identity. weathernext3_spatial is a Requester Pays GCS bucket (per the supplied
# Google WeatherNext GCS starter guide); every read must be billed to an explicit,
# non-secret Google Cloud project. This is deliberately an environment variable rather than
# a function parameter, so no caller of the reviewed ``ZarrReader`` interface can silently
# omit or vary it.
BILLING_PROJECT_ENV = "WN_A1_WEATHERNEXT_BILLING_PROJECT"

# The nearest 0.05-degree grid coordinate should never be more than one full grid cell away
# from the requested station coordinate; a larger distance means the coordinate arrays or
# the lookup are wrong, not that this is simply the closest available cell.
MAX_GRID_SELECTION_DISTANCE_DEGREES = GRID_DEGREES

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
    requested_latitude: Decimal
    requested_longitude: Decimal
    selected_latitude: Decimal
    selected_longitude: Decimal
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
    requested_latitude: Decimal,
    requested_longitude: Decimal,
    selected_latitude: Decimal,
    selected_longitude: Decimal,
    unit: str,
    raw_rows: Sequence[Mapping[str, object]],
    source_content_hash: str,
) -> EnsembleAcquisitionResult:
    """Validate and bind one exact WeatherNext ensemble slice; never smooths or subsets.

    Hard rejects (``WnA1Error``): wrong model/version, wrong variable, wrong/mismatched
    init_time, an ``init_time`` after ``acquired_at`` (a forecast cannot exist before it is
    acquired -- see the chronology check below), invalid unit, out-of-range sample id,
    duplicate sample within one valid hour, or a lead_time/lead_subtime/valid_time
    reconstruction mismatch. Missing members
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
    acquired_utc = acquired_at.astimezone(UTC)
    if init_utc > acquired_utc:
        raise WnA1Error(
            "WeatherNext forecast initialization time is after its acquisition/evaluation "
            f"time (init_time={init_utc.isoformat()}, acquired_at={acquired_utc.isoformat()}); "
            "a forecast cannot be acquired before it is initialized -- retrospective data "
            "must never be relabeled as prospective"
        )
    if not isinstance(source_content_hash, str) or len(source_content_hash) != 64:
        raise WnA1Error("WeatherNext source content hash missing or malformed")
    requested_lon_0_360 = to_0_360(requested_longitude)
    selected_lon_0_360 = to_0_360(selected_longitude)
    lat_distance = abs(selected_latitude - requested_latitude)
    lon_distance = abs(selected_lon_0_360 - requested_lon_0_360)
    if (
        lat_distance > MAX_GRID_SELECTION_DISTANCE_DEGREES
        or lon_distance > MAX_GRID_SELECTION_DISTANCE_DEGREES
    ):
        raise WnA1Error(
            "selected WeatherNext grid coordinate is unexpectedly distant from the "
            f"requested station coordinate (lat_distance={lat_distance}, "
            f"lon_distance={lon_distance})"
        )

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
            "wn-a1-weathernext-ensemble-evidence-v2-grid-coordinate-bound",
            model,
            source_object,
            init_utc.isoformat(),
            variable,
            str(requested_latitude),
            str(requested_longitude),
            str(selected_latitude),
            str(selected_longitude),
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
        requested_latitude=requested_latitude,
        requested_longitude=requested_longitude,
        selected_latitude=selected_latitude,
        selected_longitude=selected_longitude,
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


def to_0_360(longitude: Decimal) -> Decimal:
    """Convert a -180..180 longitude to the WeatherNext starter guide's 0..360 convention."""
    return longitude + Decimal(360) if longitude < 0 else longitude


def _from_0_360(longitude_0_360: Decimal) -> Decimal:
    return longitude_0_360 - Decimal(360) if longitude_0_360 > 180 else longitude_0_360


@dataclass(frozen=True, slots=True)
class SelectedGridCoordinate:
    lat_index: int
    lon_index: int
    selected_latitude: Decimal
    selected_longitude: Decimal  # -180..180, same convention as the requested coordinate


def select_nearest_grid_coordinate(
    *,
    lat_0p05: Sequence[object],
    lon_0p05: Sequence[object],
    requested_latitude: Decimal,
    requested_longitude: Decimal,
) -> SelectedGridCoordinate:
    """Select the deterministic nearest ``(lat_0p05, lon_0p05)`` grid coordinate by actual
    coordinate VALUE, never by ``round(degrees / 0.05)`` raw index arithmetic -- the WN-A1
    review's supplied Google starter guide selects real coordinates this way, and a global
    grid is not guaranteed to start at 0 degrees or be indexed by that arithmetic (e.g. a
    latitude axis running 90 -> -90 makes ``round(41.80 / 0.05) == 836`` select the wrong
    cell entirely; see ``test_coordinate_selection_by_value_not_raw_index_arithmetic``).

    ``lon_0p05`` is expected in the starter guide's 0..360 convention; ``requested_longitude``
    is accepted in the ordinary -180..180 convention and converted before lookup.
    """
    if not lat_0p05 or not lon_0p05:
        raise WnA1Error("WeatherNext lat_0p05/lon_0p05 coordinate arrays are missing or empty")
    lat_values = _to_decimal_sequence(lat_0p05, "lat_0p05")
    lon_values = _to_decimal_sequence(lon_0p05, "lon_0p05")
    requested_lon_0_360 = to_0_360(requested_longitude)
    lat_index = _nearest_index(lat_values, requested_latitude)
    lon_index = _nearest_index(lon_values, requested_lon_0_360)
    return SelectedGridCoordinate(
        lat_index=lat_index,
        lon_index=lon_index,
        selected_latitude=lat_values[lat_index],
        selected_longitude=_from_0_360(lon_values[lon_index]),
    )


def _to_decimal_sequence(values: Sequence[object], name: str) -> tuple[Decimal, ...]:
    try:
        # Real coordinate arrays are numpy scalars (from zarr/gcsfs), not plain Python
        # floats -- float() accepts them at runtime even though mypy only knows `object`.
        return tuple(Decimal(str(float(v))) for v in values)  # type: ignore[arg-type]
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise WnA1Error(f"WeatherNext {name} coordinate array is malformed") from exc


def _nearest_index(values: tuple[Decimal, ...], target: Decimal) -> int:
    best_index = 0
    best_distance = abs(values[0] - target)
    for index in range(1, len(values)):
        distance = abs(values[index] - target)
        if distance < best_distance:
            best_index, best_distance = index, distance
    return best_index


def _resolve_billing_project() -> str:
    """The Requester Pays billing project: transport configuration only, never forecast
    authority, and never part of any WN-A1 evidence identity. Always an explicit,
    non-secret Google Cloud project id -- never inferred silently from credentials."""
    billing_project = os.environ.get(BILLING_PROJECT_ENV, "").strip()
    if not billing_project:
        raise WnA1Error(
            f"WeatherNext GCS reader requires an explicit Requester Pays billing project "
            f"in the {BILLING_PROJECT_ENV} environment variable; "
            "weathernext3_spatial is a Requester Pays bucket and refuses anonymous reads"
        )
    return billing_project


ZarrReader = Callable[
    [str, datetime, Decimal, Decimal, datetime, datetime],
    tuple[Sequence[Mapping[str, object]], str, Decimal, Decimal],
]


def gcs_zarr_reader(
    source_object: str,
    init_time: datetime,
    latitude: Decimal,
    longitude: Decimal,
    window_start: datetime,
    window_end: datetime,
) -> tuple[Sequence[Mapping[str, object]], str, Decimal, Decimal]:
    """Read the smallest Chicago slice of one WeatherNext Zarr store from GCS.

    Requires the optional ``weathernext`` dependency group (``gcsfs``, ``zarr``,
    ``google-cloud-storage``) and Application Default Credentials with billing access to an
    explicit Requester Pays project (``WN_A1_WEATHERNEXT_BILLING_PROJECT``); fails clearly,
    never falling back to synthetic evidence, when either is unavailable. Never logs or
    persists credential JSON, tokens, secrets, or private keys -- only the non-secret
    billing project id ever appears in an error message. Never downloads global data: it
    opens only the exact object path, selects the nearest 0.05-degree grid cell to
    ``(latitude, longitude)`` by actual ``lat_0p05``/``lon_0p05`` coordinate value (never
    raw index arithmetic), and reads only ``station_head_temperature_2m`` for the requested
    window. Returns both the raw member rows and the actual selected grid coordinate, so the
    caller can bind it into evidence identity and detect an unexpectedly distant selection.
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
    billing_project = _resolve_billing_project()
    try:
        store = gcsfs.GCSFileSystem(
            project=billing_project,
            requester_pays=billing_project,
            token="google_default",  # noqa: S106 -- gcsfs auth-mode selector, not a secret
        ).get_mapper(source_object.removeprefix("gs://"))
        group = zarr.open(store, mode="r")
    except Exception as exc:
        raise WnA1Error(
            "WeatherNext GCS read failed (Application Default Credentials unavailable, "
            "Requester Pays access denied, or transport error); no synthetic evidence is "
            "substituted"
        ) from exc

    dataset = group[VARIABLE]
    selection = select_nearest_grid_coordinate(
        lat_0p05=group["lat_0p05"][:],
        lon_0p05=group["lon_0p05"][:],
        requested_latitude=latitude,
        requested_longitude=longitude,
    )
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
                kelvin = dataset[
                    sample, lead_index, subtime_index, selection.lat_index, selection.lon_index
                ]
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
    return rows, content_hash, selection.selected_latitude, selection.selected_longitude
