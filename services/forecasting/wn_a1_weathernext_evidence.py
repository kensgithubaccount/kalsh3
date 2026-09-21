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
(``gcsfs``, ``zarr``, ``google-cloud-storage``, ``zstandard``). ``gcs_zarr_reader``'s
coordinate/metadata access and its bounded chunk decoder were exercised live against the real
WeatherNext bucket on 2026-09-21 (see the WN-A1 review doc); tests themselves exclusively
inject a fake in-memory reader / in-memory zstd frames and never require the optional
dependency group or network access.

Time semantics (verified from the dataset's own coordinate metadata, never inferred from
object names): ``lead_time`` is 6, 12, ... 360 HOURS; ``lead_subtime`` is -5 ... 0 HOURS (the
six hourly steps ENDING at ``lead_time``); the companion ``datetime`` coordinate is
``init_time + lead_time``. Therefore ``valid_time = init_time + lead_time + lead_subtime``
with BOTH offsets in hours. ``valid_time_from_lead`` is the single implementation of that rule.

Expected source layout (from the supplied Google starter guide), never downloaded in full:

    gs://weathernext3_spatial/weathernext_3_0_0/zarr/2026_to_present/
        <YYYYMMDD_HHhr_XX_preds>/predictions.zarr/

Only the smallest Chicago spatial slice, the exact ``station_head_temperature_2m``
variable, one exact initialization, and the exact valid times needed for one target local
day are ever requested.
"""

from __future__ import annotations

import hashlib
import os
import re
import struct
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from services.market_universe.domain import stable_hash

from .wn_a1_domain import PRODUCTION_INFLUENCE, RESEARCH_ONLY, WnA1Error

MODEL = "weathernext_3_0_0"
VARIABLE = "station_head_temperature_2m"
UNIT = "K"
MEMBER_COUNT = 64
GRID_DEGREES = Decimal("0.05")

# Dataset-declared time-axis semantics (from the store's own array attributes). The reader
# verifies these against the live metadata and fails closed on any difference.
LEAD_TIME_UNITS = "hours"
LEAD_SUBTIME_UNITS = "hours"
INIT_TIME_UNITS = "days since 2026-01-01 00:00:00"
VALID_DATETIME_UNITS = "nanoseconds since 1970-01-01"
LEAD_SUBTIME_HOURS = (-5, -4, -3, -2, -1, 0)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_INIT_TIME_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)

# Reviewed chunk layout of ``station_head_temperature_2m``: one chunk is ONE member, ONE
# 6-hourly lead, all six hourly sub-steps, the ENTIRE global 0.05-degree grid (~0.47 GB
# compressed), stored as a single zstd frame with no sharding.
DATA_SHAPE = (MEMBER_COUNT, 60, 6, 3601, 7200)
DATA_CHUNKS = (1, 1, 6, 3601, 7200)
CHUNK_DECODED_BYTES = 6 * 3601 * 7200 * 4
_FETCH_BLOCK_BYTES = 8 * 1024 * 1024
# Hard cap on payload bytes one ``gcs_zarr_reader`` call may pull from the Requester Pays
# bucket. A full 64-member local-day window needs hundreds of chunks (see the review doc), so
# this deliberately fails closed rather than silently spending tens of GB.
MAX_TRANSFER_BYTES = 2 * 1024**3

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
    lead_subtime_hours: int
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


def valid_time_from_lead(
    init_time: datetime, lead_time_hours: int, lead_subtime_hours: int
) -> datetime:
    """The ONE valid-time rule: ``init_time + lead_time + lead_subtime``, both in HOURS.

    ``lead_subtime`` is the dataset's -5..0 hour offset back from the 6-hourly ``lead_time``
    (verified against the store's own ``datetime`` companion coordinate by
    ``verify_time_axes``). Interpreting it as minutes -- the pre-repair behaviour -- turns
    lead 24 h / sub-step -5 into 23:55Z instead of 19:00Z.
    """
    _aware(init_time)
    return init_time.astimezone(UTC) + timedelta(hours=lead_time_hours + lead_subtime_hours)


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
        lead_subtime = row.get("lead_subtime_hours")
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
            or isinstance(lead_subtime, bool)
            or not isinstance(lead_subtime, int)
            or lead_subtime not in LEAD_SUBTIME_HOURS
        ):
            raise WnA1Error("WeatherNext lead_time/lead_subtime malformed")
        if not isinstance(valid_time, datetime):
            raise WnA1Error("WeatherNext valid_time missing or malformed")
        _aware(valid_time)
        expected_valid = valid_time_from_lead(init_utc, lead_hours, lead_subtime)
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
            sample, lead_hours, lead_subtime, valid_time.astimezone(UTC), value
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
            "wn-a1-weathernext-ensemble-evidence-v3-lead-subtime-hours",
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
                    m.lead_subtime_hours,
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
    # len(), not truthiness: real zarr coordinate arrays are numpy arrays, whose bool() is ambiguous
    if len(lat_0p05) == 0 or len(lon_0p05) == 0:
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

RangeReader = Callable[[int, int], bytes]


def verify_time_axes(
    *,
    init_time: datetime,
    init_raw: int,
    init_units: object,
    lead_times: Sequence[int],
    lead_units: object,
    lead_subtimes: Sequence[int],
    subtime_units: object,
    valid_datetime_ns: Mapping[int, int],
    valid_datetime_units: object,
) -> None:
    """Fail closed unless the dataset's OWN coordinates confirm the time semantics.

    Checks the declared units of every time axis, that the ``init_time`` coordinate equals the
    claimed initialization, that ``lead_subtime`` is exactly the reviewed -5..0 hour axis, and
    -- for every ``lead_time`` index the caller will use -- that the companion ``datetime``
    coordinate (nanoseconds since the Unix epoch) equals ``init_time + lead_time`` in hours.
    ``valid_datetime_ns`` maps lead index -> the ``datetime`` coordinate value read from the
    store. Nothing here looks at object names.
    """
    _aware(init_time)
    init_utc = init_time.astimezone(UTC)
    for name, actual, expected in (
        ("init_time", init_units, INIT_TIME_UNITS),
        ("lead_time", lead_units, LEAD_TIME_UNITS),
        ("lead_subtime", subtime_units, LEAD_SUBTIME_UNITS),
        ("datetime", valid_datetime_units, VALID_DATETIME_UNITS),
    ):
        if actual != expected:
            raise WnA1Error(
                f"WeatherNext {name} units are {actual!r}, expected {expected!r}; refusing to "
                "decode time semantics that were not reviewed"
            )
    if _INIT_TIME_EPOCH + timedelta(days=init_raw) != init_utc:
        raise WnA1Error(
            "WeatherNext init_time coordinate does not equal the claimed initialization "
            f"(coordinate={(_INIT_TIME_EPOCH + timedelta(days=init_raw)).isoformat()}, "
            f"claimed={init_utc.isoformat()})"
        )
    if tuple(lead_subtimes) != LEAD_SUBTIME_HOURS:
        raise WnA1Error(
            f"WeatherNext lead_subtime axis is {tuple(lead_subtimes)!r}, "
            f"expected {LEAD_SUBTIME_HOURS!r}"
        )
    init_ns = ((init_utc - _EPOCH) // timedelta(microseconds=1)) * 1000
    for lead_index, actual_ns in valid_datetime_ns.items():
        expected_ns = init_ns + lead_times[lead_index] * 3_600_000_000_000
        if actual_ns != expected_ns:
            raise WnA1Error(
                f"WeatherNext datetime coordinate at lead index {lead_index} is {actual_ns} ns, "
                f"but init_time + lead_time({lead_times[lead_index]} h) is {expected_ns} ns"
            )


@dataclass(frozen=True, slots=True)
class PrefixDecode:
    """Result of one bounded zstd-prefix lookup.

    ``range_bytes`` is the payload byte count actually transferred for the prefix
    ``[0, range_bytes)``; ``range_sha256`` hashes exactly those bytes and is NOT the hash of
    the whole stored object.
    """

    values: Mapping[int, float]
    range_bytes: int
    range_sha256: str
    decoded_bytes: int


@dataclass(frozen=True, slots=True)
class ChunkReadProvenance:
    """Identity of every chunk byte a lookup used.

    The ``object_*`` fields identify the WHOLE stored chunk object as GCS reports it; the
    ``range_*`` fields identify only the prefix bytes transferred. Both are bound into the
    reader's content hash so a value can be traced to an exact object generation.
    """

    object_path: str
    object_size: int
    object_generation: str
    object_md5_base64: str
    object_crc32c_base64: str
    range_start: int
    range_end: int
    range_sha256: str
    decoded_bytes: int


def decode_zstd_prefix_float32(
    read_range: RangeReader,
    *,
    object_size: int,
    element_offsets: Sequence[int],
    expected_decoded_bytes: int,
    max_range_bytes: int,
    block_bytes: int = _FETCH_BLOCK_BYTES,
) -> PrefixDecode:
    """Recover float32 elements from a single-frame zstd chunk by fetching only a PREFIX.

    A zstd frame decodes strictly front to back, so element ``k`` of the C-ordered decoded
    chunk needs only the compressed bytes up to wherever the decoder has emitted
    ``4 * (k + 1)`` bytes. Prefix cost therefore grows with the element's position in the
    chunk (an early sub-step is cheap, a late one approaches the whole object); it is NOT
    constant. Fails closed unless the frame header declares exactly ``expected_decoded_bytes``,
    the range reads are complete, the frame decodes cleanly, and ``max_range_bytes`` (the
    transfer budget) is not exceeded before every requested element is recovered.
    """
    try:
        import zstandard  # type: ignore[import-not-found,unused-ignore]
    except ImportError as exc:
        raise WnA1Error(
            "WeatherNext bounded chunk decoding requires the optional 'weathernext' "
            "dependency group (zstandard)"
        ) from exc

    targets = sorted(set(element_offsets))
    if not targets or targets[0] < 0 or (targets[-1] + 1) * 4 > expected_decoded_bytes:
        raise WnA1Error("WeatherNext requested element offsets fall outside the reviewed chunk")
    decompressor = zstandard.ZstdDecompressor().decompressobj()
    digest = hashlib.sha256()
    buffers = {offset: bytearray(4) for offset in targets}
    filled = dict.fromkeys(targets, 0)
    pending = list(targets)
    fetched = 0
    decoded = 0
    while pending:
        end = min(fetched + block_bytes, object_size, max_range_bytes)
        if end <= fetched:
            reason = "transfer budget exhausted" if fetched < object_size else "object exhausted"
            raise WnA1Error(
                f"WeatherNext chunk lookup failed: {reason} after {fetched} bytes before all "
                "requested elements were decoded"
            )
        piece = read_range(fetched, end)
        if len(piece) != end - fetched:
            raise WnA1Error("WeatherNext range read returned a short or oversized payload")
        try:
            if fetched == 0:
                declared = zstandard.get_frame_parameters(piece[:18]).content_size
                if declared != expected_decoded_bytes:
                    raise WnA1Error(
                        f"WeatherNext zstd frame declares {declared} decoded bytes, "
                        f"expected {expected_decoded_bytes}"
                    )
            output = decompressor.decompress(piece)
        except zstandard.ZstdError as exc:
            raise WnA1Error("WeatherNext chunk is not a decodable zstd frame") from exc
        digest.update(piece)
        fetched = end
        block_start, decoded = decoded, decoded + len(output)
        for offset in tuple(pending):
            position = offset * 4 + filled[offset]
            if position >= decoded:
                continue
            take = min(4 - filled[offset], decoded - position)
            buffers[offset][filled[offset] : filled[offset] + take] = output[
                position - block_start : position - block_start + take
            ]
            filled[offset] += take
            if filled[offset] == 4:
                pending.remove(offset)
        if pending and decompressor.eof:
            raise WnA1Error("WeatherNext zstd frame ended before all requested elements")
    values = {offset: struct.unpack("<f", bytes(buf))[0] for offset, buf in buffers.items()}
    return PrefixDecode(values, fetched, digest.hexdigest(), decoded)


def _verify_dataset_layout(dataset: Any) -> None:
    """Fail closed unless the data array has the reviewed shape/chunking/codecs/units."""
    layout = dataset.metadata.to_dict()
    codecs = layout.get("codecs", ())
    if (
        tuple(dataset.shape) != DATA_SHAPE
        or tuple(dataset.chunks) != DATA_CHUNKS
        or str(dataset.dtype) != "float32"
        or tuple(layout.get("dimension_names", ()))
        != ("sample", "lead_time", "lead_subtime", "lat_0p05", "lon_0p05")
        or tuple(c.get("name") for c in codecs) != ("bytes", "zstd")
        or codecs[0].get("configuration", {}).get("endian") != "little"
        or layout.get("chunk_key_encoding")
        != {"name": "default", "configuration": {"separator": "/"}}
        or dataset.attrs.get("units") != UNIT
        or dataset.attrs.get("grid_degrees") != float(GRID_DEGREES)
    ):
        raise WnA1Error(
            "WeatherNext data array does not match the reviewed layout "
            "(shape/chunks/dtype/dimensions/codecs/chunk-key encoding/units)"
        )


def _range_reader(fs: Any, key: str) -> RangeReader:
    def read(start: int, end: int) -> bytes:
        return bytes(fs.cat_file(key, start=start, end=end))

    return read


def _coordinate_digest(*arrays: Sequence[object]) -> str:
    digest = hashlib.sha256()
    for array in arrays:
        digest.update(struct.pack(f"<{len(array)}d", *(float(v) for v in array)))  # type: ignore[arg-type]
    return digest.hexdigest()


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
    ``google-cloud-storage``, ``zstandard``) and Application Default Credentials with billing
    access to an explicit Requester Pays project (``WN_A1_WEATHERNEXT_BILLING_PROJECT``);
    fails clearly, never falling back to synthetic evidence, when either is unavailable. Never
    logs or persists credential JSON, tokens, secrets, or private keys -- only the non-secret
    billing project id ever appears in an error message.

    The dataset's own coordinate metadata is verified first (``verify_time_axes``,
    ``_verify_dataset_layout``); valid times are ``valid_time_from_lead``. Values are read with
    ``decode_zstd_prefix_float32`` -- one bounded prefix read per (member, lead) chunk, never
    the whole ~0.47 GB chunk unless the requested sub-step sits at its end -- and total payload
    is capped by ``MAX_TRANSFER_BYTES`` (fails closed beyond it). Selects the nearest 0.05-degree
    grid cell to ``(latitude, longitude)`` by actual ``lat_0p05``/``lon_0p05`` coordinate value
    (never raw index arithmetic). The returned content hash binds every chunk's whole-object
    identity (generation/md5/crc32c/size), the exact byte range read and its SHA-256, and a
    digest of the coordinate values used. Returns both the raw member rows and the actual
    selected grid coordinate, so the caller can bind it into evidence identity and detect an
    unexpectedly distant selection.
    """
    try:
        import gcsfs  # type: ignore[import-not-found,unused-ignore]
        import zarr  # type: ignore[import-not-found,unused-ignore]
        import zstandard  # type: ignore[import-not-found,unused-ignore]  # noqa: F401
    except ImportError as exc:
        raise WnA1Error(
            "WeatherNext GCS reader requires the optional 'weathernext' dependency group "
            "(gcsfs, zarr, google-cloud-storage, zstandard); install it to use gcs_zarr_reader"
        ) from exc

    validate_source_object(source_object, init_time)
    _aware(window_start)
    _aware(window_end)
    billing_project = _resolve_billing_project()
    store_path = source_object.removeprefix("gs://").rstrip("/")
    try:
        fs = gcsfs.GCSFileSystem(
            project=billing_project,
            requester_pays=billing_project,
            token="google_default",  # noqa: S106 -- gcsfs auth-mode selector, not a secret
        )
        group = zarr.open(fs.get_mapper(store_path), mode="r")
        dataset = group[VARIABLE]
        lat_values = group["lat_0p05"][:]
        lon_values = group["lon_0p05"][:]
        lead_times = [int(v) for v in group["lead_time"][:]]
        lead_subtimes = [int(v) for v in group["lead_subtime"][:]]
        init_raw = int(group["init_time"][()])
        time_attrs = {
            name: group[name].attrs.get("units")
            for name in ("init_time", "lead_time", "lead_subtime", "datetime")
        }
    except Exception as exc:
        raise WnA1Error(
            "WeatherNext GCS read failed (Application Default Credentials unavailable, "
            "Requester Pays access denied, or transport error); no synthetic evidence is "
            "substituted"
        ) from exc

    _verify_dataset_layout(dataset)
    selection = select_nearest_grid_coordinate(
        lat_0p05=lat_values,
        lon_0p05=lon_values,
        requested_latitude=latitude,
        requested_longitude=longitude,
    )
    init_utc = init_time.astimezone(UTC)
    needed: dict[int, list[int]] = {}
    for lead_index, lead_hours in enumerate(lead_times):
        for subtime_index, subtime_hours in enumerate(lead_subtimes):
            valid_time = valid_time_from_lead(init_utc, lead_hours, subtime_hours)
            if window_start <= valid_time < window_end:
                needed.setdefault(lead_index, []).append(subtime_index)
    try:
        valid_datetime_ns = {int(i): int(group["datetime"][i]) for i in needed}
    except Exception as exc:
        raise WnA1Error("WeatherNext datetime coordinate read failed") from exc
    verify_time_axes(
        init_time=init_utc,
        init_raw=init_raw,
        init_units=time_attrs["init_time"],
        lead_times=lead_times,
        lead_units=time_attrs["lead_time"],
        lead_subtimes=lead_subtimes,
        subtime_units=time_attrs["lead_subtime"],
        valid_datetime_ns=valid_datetime_ns,
        valid_datetime_units=time_attrs["datetime"],
    )

    remaining = MAX_TRANSFER_BYTES
    provenance: list[ChunkReadProvenance] = []
    values: dict[tuple[int, int, int], float] = {}
    for lead_index in sorted(needed):
        offsets = {
            subtime_index: (subtime_index * DATA_SHAPE[3] + selection.lat_index) * DATA_SHAPE[4]
            + selection.lon_index
            for subtime_index in needed[lead_index]
        }
        for sample in range(MEMBER_COUNT):
            chunk_key = dataset.metadata.encode_chunk_key((sample, lead_index, 0, 0, 0))
            key = f"{store_path}/{VARIABLE}/{chunk_key}"
            try:
                identity = fs.info(key)
                decode = decode_zstd_prefix_float32(
                    _range_reader(fs, key),
                    object_size=int(identity["size"]),
                    element_offsets=list(offsets.values()),
                    expected_decoded_bytes=CHUNK_DECODED_BYTES,
                    max_range_bytes=remaining,
                )
                if fs.info(key)["generation"] != identity["generation"]:
                    raise WnA1Error("WeatherNext chunk object changed generation during read")
            except WnA1Error:
                raise
            except Exception as exc:
                raise WnA1Error(
                    "WeatherNext GCS chunk read failed (Requester Pays access denied, or "
                    "transport error); no synthetic evidence is substituted"
                ) from exc
            remaining -= decode.range_bytes
            provenance.append(
                ChunkReadProvenance(
                    object_path=f"gs://{key}",
                    object_size=int(identity["size"]),
                    object_generation=str(identity["generation"]),
                    object_md5_base64=str(identity.get("md5Hash", "")),
                    object_crc32c_base64=str(identity.get("crc32c", "")),
                    range_start=0,
                    range_end=decode.range_bytes,
                    range_sha256=decode.range_sha256,
                    decoded_bytes=decode.decoded_bytes,
                )
            )
            for subtime_index, offset in offsets.items():
                value = decode.values[offset]
                if not (value == value and abs(value) != float("inf")):
                    raise WnA1Error("WeatherNext decoded a non-finite member value")
                values[(sample, lead_index, subtime_index)] = value

    rows: list[dict[str, object]] = []
    for lead_index in sorted(needed):
        for subtime_index in sorted(needed[lead_index]):
            valid_time = valid_time_from_lead(
                init_utc, lead_times[lead_index], lead_subtimes[subtime_index]
            )
            for sample in range(MEMBER_COUNT):
                rows.append(
                    {
                        "sample": sample,
                        "lead_time_hours": lead_times[lead_index],
                        "lead_subtime_hours": lead_subtimes[subtime_index],
                        "valid_time": valid_time,
                        "value_kelvin": Decimal(str(values[(sample, lead_index, subtime_index)])),
                    }
                )
    content_hash = stable_hash(
        (
            "wn-a1-weathernext-chunk-provenance-v1",
            source_object,
            _coordinate_digest(lat_values, lon_values, lead_times, lead_subtimes, [init_raw]),
            tuple(sorted((i, ns) for i, ns in valid_datetime_ns.items())),
            tuple(
                (
                    p.object_path,
                    p.object_size,
                    p.object_generation,
                    p.object_md5_base64,
                    p.object_crc32c_base64,
                    p.range_start,
                    p.range_end,
                    p.range_sha256,
                    p.decoded_bytes,
                )
                for p in provenance
            ),
        )
    )
    return rows, content_hash, selection.selected_latitude, selection.selected_longitude
