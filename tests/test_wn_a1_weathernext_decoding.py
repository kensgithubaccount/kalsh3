"""WN-A1 WeatherNext decoding-boundary regression tests.

Fixture (a real value decoded live from gs://weathernext3_spatial on 2026-09-21):
store 20260101_00hr_01, Chicago Midway grid cell (41.80N, 272.25E), sample 0,
lead_time 24 h, lead_subtime -5 h -> valid 2026-01-01T19:00Z,
``station_head_temperature_2m`` = 264.22808837890625 K.

Nothing here touches the network: coordinate metadata is passed in as literals, zstd frames
are built in memory, and ``gcs_zarr_reader`` runs against in-memory fake ``gcsfs``/``zarr``.
"""

from __future__ import annotations

import random
import struct
import sys
import types
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import ClassVar

import pytest

from services.forecasting import wn_a1_weathernext_evidence as ev
from services.forecasting.wn_a1_domain import WnA1Error
from services.forecasting.wn_a1_weathernext_evidence import (
    BILLING_PROJECT_ENV,
    LEAD_SUBTIME_HOURS,
    MEMBER_COUNT,
    MODEL,
    UNIT,
    VARIABLE,
    build_ensemble_evidence,
    decode_zstd_prefix_float32,
    valid_time_from_lead,
    verify_time_axes,
)

INIT = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
STORE = (
    "gs://weathernext3_spatial/weathernext_3_0_0/zarr/2026_to_present/"
    "20260101_00hr_01_preds/predictions.zarr/"
)
FIXTURE_VALUE_K = Decimal("264.22808837890625")
FIXTURE_VALID = datetime(2026, 1, 1, 19, 0, tzinfo=UTC)
LEAD_TIMES = list(range(6, 361, 6))  # real coordinate: 6, 12, ... 360 hours
DATETIME_NS_AT_LEAD_24 = 1767312000000000000  # real datetime[3]: 2026-01-02T00:00Z


# --------------------------------------------------------------------------- valid time


def test_fixture_lead24_subtime_minus5_resolves_to_1900z_not_2355z() -> None:
    assert valid_time_from_lead(INIT, 24, -5) == FIXTURE_VALID
    pre_repair_minutes_interpretation = INIT + timedelta(hours=24, minutes=-5)
    assert pre_repair_minutes_interpretation == datetime(2026, 1, 1, 23, 55, tzinfo=UTC)
    assert valid_time_from_lead(INIT, 24, -5) != pre_repair_minutes_interpretation


def _fixture_rows(valid_time: datetime) -> list[dict]:
    return [
        {
            "sample": sample,
            "lead_time_hours": 24,
            "lead_subtime_hours": -5,
            "valid_time": valid_time,
            "value_kelvin": FIXTURE_VALUE_K + Decimal(sample) / Decimal(100),
        }
        for sample in range(MEMBER_COUNT)
    ]


def _build(rows: list[dict]):
    return build_ensemble_evidence(
        model=MODEL,
        source_object=STORE,
        init_time=INIT,
        acquired_at=datetime(2026, 9, 21, tzinfo=UTC),
        variable=VARIABLE,
        requested_latitude=Decimal("41.80"),
        requested_longitude=Decimal("-87.75"),
        selected_latitude=Decimal("41.80"),
        selected_longitude=Decimal("-87.75"),
        unit=UNIT,
        raw_rows=rows,
        source_content_hash="a" * 64,
    )


def test_evidence_accepts_fixture_row_at_1900z_and_binds_hours() -> None:
    result = _build(_fixture_rows(FIXTURE_VALID))
    assert result.evidence is not None
    member = result.evidence.members[0]
    assert member.valid_time == FIXTURE_VALID
    assert (member.lead_time_hours, member.lead_subtime_hours) == (24, -5)
    assert member.value_kelvin == FIXTURE_VALUE_K


def test_evidence_rejects_the_2355z_minutes_interpretation() -> None:
    with pytest.raises(WnA1Error, match="reconstruct"):
        _build(_fixture_rows(datetime(2026, 1, 1, 23, 55, tzinfo=UTC)))


@pytest.mark.parametrize("bad_subtime", [1, -6, 5, 30, 59])
def test_lead_subtime_outside_the_dataset_axis_is_rejected(bad_subtime: int) -> None:
    rows = _fixture_rows(FIXTURE_VALID)
    rows[0]["lead_subtime_hours"] = bad_subtime
    with pytest.raises(WnA1Error, match="lead_time/lead_subtime malformed"):
        _build(rows)


class _NumpyLikeArray:
    """Sequence whose truthiness is ambiguous, exactly like a multi-element numpy array."""

    def __init__(self, values):
        self._values = list(values)

    def __len__(self):
        return len(self._values)

    def __getitem__(self, index):
        return self._values[index]

    def __iter__(self):
        return iter(self._values)

    def __bool__(self):
        raise ValueError("The truth value of an array with more than one element is ambiguous")


def test_coordinate_selection_accepts_numpy_like_arrays_with_ambiguous_truthiness() -> None:
    selection = ev.select_nearest_grid_coordinate(
        lat_0p05=_NumpyLikeArray([41.7, 41.75, 41.79999923706055, 41.85]),
        lon_0p05=_NumpyLikeArray([272.0, 272.25, 272.5]),
        requested_latitude=Decimal("41.80"),
        requested_longitude=Decimal("-87.75"),
    )
    assert (selection.lat_index, selection.lon_index) == (2, 1)
    assert selection.selected_longitude == Decimal("-87.75")
    with pytest.raises(WnA1Error, match="missing or empty"):
        ev.select_nearest_grid_coordinate(
            lat_0p05=_NumpyLikeArray([]),
            lon_0p05=_NumpyLikeArray([272.0]),
            requested_latitude=Decimal("41.80"),
            requested_longitude=Decimal("-87.75"),
        )


# ------------------------------------------------------------------ time-axis verification


def _axes(**overrides):
    kwargs = dict(
        init_time=INIT,
        init_raw=0,
        init_units="days since 2026-01-01 00:00:00",
        lead_times=LEAD_TIMES,
        lead_units="hours",
        lead_subtimes=list(LEAD_SUBTIME_HOURS),
        subtime_units="hours",
        valid_datetime_ns={3: DATETIME_NS_AT_LEAD_24},
        valid_datetime_units="nanoseconds since 1970-01-01",
    )
    kwargs.update(overrides)
    return kwargs


def test_verify_time_axes_accepts_the_real_dataset_coordinates() -> None:
    verify_time_axes(**_axes())


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"subtime_units": "minutes"}, "lead_subtime units"),
        ({"lead_units": "minutes"}, "lead_time units"),
        ({"init_units": "hours since 2026-01-01 00:00:00"}, "init_time units"),
        ({"valid_datetime_units": "seconds since 1970-01-01"}, "datetime units"),
        ({"init_raw": 1}, "init_time coordinate"),
        ({"lead_subtimes": [0, 5, 10, 15, 20, 25]}, "lead_subtime axis"),
        ({"valid_datetime_ns": {3: DATETIME_NS_AT_LEAD_24 - 300_000_000_000}}, "datetime coord"),
    ],
)
def test_verify_time_axes_fails_closed(override: dict, match: str) -> None:
    with pytest.raises(WnA1Error, match=match):
        verify_time_axes(**_axes(**override))


# ------------------------------------------------------------- bounded zstd prefix decoder

try:
    import zstandard
except ImportError:  # optional 'weathernext' dependency group
    zstandard = None  # type: ignore[assignment]

needs_zstd = pytest.mark.skipif(
    zstandard is None, reason="zstandard (weathernext extra) not installed"
)

N_SUB, N_LAT, N_LON = 6, 200, 250  # ~1.2 MB decoded: many 128 KiB zstd blocks
DECODED_BYTES = N_SUB * N_LAT * N_LON * 4


def _planted() -> tuple[list[float], bytes]:
    rng = random.Random(20260921)  # noqa: S311 -- deterministic test data, not security
    values = [
        struct.unpack("<f", struct.pack("<f", rng.random() * 40 + 250))[0]
        for _ in range(N_SUB * N_LAT * N_LON)
    ]
    return values, struct.pack(f"<{len(values)}f", *values)


def _frame() -> tuple[list[float], bytes]:
    values, raw = _planted()
    return values, zstandard.ZstdCompressor(level=3, write_content_size=True).compress(raw)


def _reader(frame: bytes, transferred: list[int]):
    def read(start: int, end: int) -> bytes:
        transferred.append(end - start)
        return frame[start:end]

    return read


def _decode(frame: bytes, offsets, transferred, *, budget=10**9, block=1024):
    return decode_zstd_prefix_float32(
        _reader(frame, transferred),
        object_size=len(frame),
        element_offsets=offsets,
        expected_decoded_bytes=DECODED_BYTES,
        max_range_bytes=budget,
        block_bytes=block,
    )


@needs_zstd
def test_prefix_decode_recovers_exact_values_and_only_fetches_a_prefix() -> None:
    values, frame = _frame()
    early = (0 * N_LAT + 3) * N_LON + 7
    transferred: list[int] = []
    result = _decode(frame, [early], transferred)
    assert result.values[early] == values[early]
    assert result.range_bytes == sum(transferred) < len(frame) // 4
    assert (
        result.range_sha256 == __import__("hashlib").sha256(frame[: result.range_bytes]).hexdigest()
    )
    assert result.decoded_bytes >= (early + 1) * 4


@needs_zstd
def test_prefix_cost_grows_with_position_in_the_chunk() -> None:
    values, frame = _frame()
    early = (0 * N_LAT + 3) * N_LON + 7
    late = (5 * N_LAT + N_LAT - 1) * N_LON + N_LON - 1
    cost_early, cost_late = [], []
    _decode(frame, [early], cost_early)
    result_late = _decode(frame, [late], cost_late)
    assert result_late.values[late] == values[late]
    assert sum(cost_early) < sum(cost_late) <= len(frame)


@needs_zstd
def test_prefix_decode_recovers_many_scattered_offsets_across_zstd_blocks() -> None:
    values, frame = _frame()
    offsets = list(range(1, N_SUB * N_LAT * N_LON, 30011))
    result = _decode(frame, offsets, [], block=4093)  # odd fetch size, unaligned to zstd blocks
    assert {o: result.values[o] for o in offsets} == {o: values[o] for o in offsets}


@needs_zstd
def test_prefix_decode_fails_closed_when_budget_is_exceeded() -> None:
    _, frame = _frame()
    late = (5 * N_LAT + N_LAT - 1) * N_LON + N_LON - 1
    with pytest.raises(WnA1Error, match="budget"):
        _decode(frame, [late], [], budget=500)


@needs_zstd
def test_prefix_decode_rejects_unexpected_frame_content_size() -> None:
    _, frame = _frame()
    with pytest.raises(WnA1Error, match="declares"):
        decode_zstd_prefix_float32(
            _reader(frame, []),
            object_size=len(frame),
            element_offsets=[0],
            expected_decoded_bytes=DECODED_BYTES + 4,
            max_range_bytes=10**9,
        )


@needs_zstd
def test_prefix_decode_rejects_short_range_read_and_out_of_chunk_offsets() -> None:
    _, frame = _frame()
    with pytest.raises(WnA1Error, match="short"):
        decode_zstd_prefix_float32(
            lambda start, end: frame[start : end - 1],
            object_size=len(frame),
            element_offsets=[0],
            expected_decoded_bytes=DECODED_BYTES,
            max_range_bytes=10**9,
        )
    with pytest.raises(WnA1Error, match="outside the reviewed chunk"):
        _decode(frame, [N_SUB * N_LAT * N_LON], [])
    with pytest.raises(WnA1Error, match="outside the reviewed chunk"):
        _decode(frame, [], [])


@needs_zstd
def test_prefix_decode_rejects_a_non_zstd_object() -> None:
    with pytest.raises(WnA1Error, match="zstd"):
        decode_zstd_prefix_float32(
            lambda start, end: b"\x00" * (end - start),
            object_size=4096,
            element_offsets=[0],
            expected_decoded_bytes=DECODED_BYTES,
            max_range_bytes=10**9,
        )


# ------------------------------------------------- gcs_zarr_reader against in-memory fakes

SMALL_LAT = [41.7, 41.75, 41.8, 41.85, 41.9]
SMALL_LON = [272.0 + 0.25 * k for k in range(8)]  # 272.25 is index 1
SMALL_DECODED = N_SUB * len(SMALL_LAT) * len(SMALL_LON) * 4


def _planted_value(sample: int, lead_index: int, sub: int, lat: int, lon: int) -> float:
    raw = 240.0 + sample * 10 + lead_index * 0.5 + sub * 0.125 + lat * 0.01 + lon * 0.001
    return struct.unpack("<f", struct.pack("<f", raw))[0]


class _Array:
    def __init__(self, values, units=None):
        self._values = list(values)
        self.attrs = {} if units is None else {"units": units}

    def __getitem__(self, index):
        if index == slice(None):
            return list(self._values)
        if index == ():
            return self._values[0]
        return self._values[index]


class _Metadata:
    def to_dict(self):
        return {
            "codecs": (
                {"name": "bytes", "configuration": {"endian": "little"}},
                {"name": "zstd", "configuration": {"level": 0, "checksum": False}},
            ),
            "chunk_key_encoding": {"name": "default", "configuration": {"separator": "/"}},
            "dimension_names": ("sample", "lead_time", "lead_subtime", "lat_0p05", "lon_0p05"),
        }

    def encode_chunk_key(self, coords):
        return "c/" + "/".join(str(c) for c in coords)


class _Dataset:
    shape = (2, 60, 6, len(SMALL_LAT), len(SMALL_LON))
    chunks = (1, 1, 6, len(SMALL_LAT), len(SMALL_LON))
    dtype = "float32"
    attrs: ClassVar[dict[str, object]] = {"units": "K", "grid_degrees": 0.05}
    metadata = _Metadata()


class _FakeFs:
    def __init__(self, *, generation_sequence=("g1",)):
        self.objects: dict[str, bytes] = {}
        self.transferred: list[int] = []
        self._generations = list(generation_sequence)
        self._calls = 0
        compressor = zstandard.ZstdCompressor(level=3, write_content_size=True)
        for sample in range(2):
            for lead_index in range(60):
                floats = [
                    _planted_value(sample, lead_index, sub, lat, lon)
                    for sub in range(N_SUB)
                    for lat in range(len(SMALL_LAT))
                    for lon in range(len(SMALL_LON))
                ]
                key = f"STORE/{VARIABLE}/c/{sample}/{lead_index}/0/0/0"
                self.objects[key] = compressor.compress(struct.pack(f"<{len(floats)}f", *floats))

    def get_mapper(self, path):
        return path

    def info(self, key):
        generation = self._generations[min(self._calls, len(self._generations) - 1)]
        self._calls += 1
        return {
            "size": len(self.objects[key]),
            "generation": generation,
            "md5Hash": "md5",
            "crc32c": "crc",
        }

    def cat_file(self, key, start=None, end=None):
        self.transferred.append(end - start)
        return self.objects[key][start:end]


def _install_fakes(monkeypatch, fs: _FakeFs, *, datetime_ns=None, subtime_units="hours"):
    group = {
        VARIABLE: _Dataset(),
        "lat_0p05": _Array(SMALL_LAT),
        "lon_0p05": _Array(SMALL_LON),
        "lead_time": _Array(LEAD_TIMES, "hours"),
        "lead_subtime": _Array(LEAD_SUBTIME_HOURS, subtime_units),
        "init_time": _Array([0], "days since 2026-01-01 00:00:00"),
        "datetime": _Array(
            datetime_ns
            or [
                int((INIT + timedelta(hours=h) - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds())
                * 10**9
                for h in LEAD_TIMES
            ],
            "nanoseconds since 1970-01-01",
        ),
    }
    fake_gcsfs = types.ModuleType("gcsfs")
    fake_gcsfs.GCSFileSystem = lambda **kwargs: fs  # type: ignore[attr-defined]
    fake_zarr = types.ModuleType("zarr")
    fake_zarr.open = lambda mapper, mode="r": group  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gcsfs", fake_gcsfs)
    monkeypatch.setitem(sys.modules, "zarr", fake_zarr)
    monkeypatch.setenv(BILLING_PROJECT_ENV, "test-billing-project")
    monkeypatch.setattr(ev, "DATA_SHAPE", _Dataset.shape)
    monkeypatch.setattr(ev, "DATA_CHUNKS", _Dataset.chunks)
    monkeypatch.setattr(ev, "CHUNK_DECODED_BYTES", SMALL_DECODED)
    monkeypatch.setattr(ev, "MEMBER_COUNT", 2)
    # the fake object keys are rooted at the store path the reader derives from STORE
    store_path = STORE.removeprefix("gs://").rstrip("/")
    fs.objects = {k.replace("STORE", store_path): v for k, v in fs.objects.items()}


def _read(window_start, window_end):
    return ev.gcs_zarr_reader(
        STORE, INIT, Decimal("41.80"), Decimal("-87.75"), window_start, window_end
    )


@needs_zstd
def test_gcs_reader_decodes_fixture_hour_with_verified_time_and_grid(monkeypatch) -> None:
    fs = _FakeFs()
    _install_fakes(monkeypatch, fs)
    rows, content_hash, lat, lon = _read(FIXTURE_VALID, FIXTURE_VALID + timedelta(hours=2))
    assert (lat, lon) == (Decimal("41.8"), Decimal("-87.75"))
    assert len(content_hash) == 64
    assert len(rows) == 2 * 2  # 2 hourly sub-steps x 2 (patched) members
    first = rows[0]
    assert first["sample"] == 0
    assert (first["lead_time_hours"], first["lead_subtime_hours"]) == (24, -5)
    assert first["valid_time"] == FIXTURE_VALID  # 19:00Z, not 23:55Z
    assert first["value_kelvin"] == Decimal(str(_planted_value(0, 3, 0, 2, 1)))
    second_hour = next(r for r in rows if r["sample"] == 1 and r["lead_subtime_hours"] == -4)
    assert second_hour["valid_time"] == FIXTURE_VALID + timedelta(hours=1)
    assert second_hour["value_kelvin"] == Decimal(str(_planted_value(1, 3, 1, 2, 1)))
    assert 0 < sum(fs.transferred) <= 2 * max(len(o) for o in fs.objects.values())


@needs_zstd
def test_gcs_reader_content_hash_binds_object_generation(monkeypatch) -> None:
    window = (FIXTURE_VALID, FIXTURE_VALID + timedelta(hours=1))
    _install_fakes(monkeypatch, _FakeFs(generation_sequence=("g1",)))
    hash_g1 = _read(*window)[1]
    _install_fakes(monkeypatch, _FakeFs(generation_sequence=("g2",)))
    hash_g2 = _read(*window)[1]
    assert hash_g1 != hash_g2


@needs_zstd
def test_gcs_reader_fails_closed_when_datetime_coordinate_disagrees(monkeypatch) -> None:
    ns = [
        (int((INIT + timedelta(hours=h - 5) - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds()))
        * 10**9
        for h in LEAD_TIMES
    ]
    _install_fakes(monkeypatch, _FakeFs(), datetime_ns=ns)
    with pytest.raises(WnA1Error, match="datetime coordinate"):
        _read(FIXTURE_VALID, FIXTURE_VALID + timedelta(hours=1))


@needs_zstd
def test_gcs_reader_fails_closed_on_unreviewed_subtime_units(monkeypatch) -> None:
    _install_fakes(monkeypatch, _FakeFs(), subtime_units="minutes")
    with pytest.raises(WnA1Error, match="lead_subtime units"):
        _read(FIXTURE_VALID, FIXTURE_VALID + timedelta(hours=1))


@needs_zstd
def test_gcs_reader_enforces_the_transfer_budget(monkeypatch) -> None:
    _install_fakes(monkeypatch, _FakeFs())
    monkeypatch.setattr(ev, "MAX_TRANSFER_BYTES", 64)
    with pytest.raises(WnA1Error, match="budget"):
        _read(FIXTURE_VALID, FIXTURE_VALID + timedelta(hours=1))


@needs_zstd
def test_gcs_reader_fails_closed_if_object_generation_changes_mid_read(monkeypatch) -> None:
    _install_fakes(monkeypatch, _FakeFs(generation_sequence=("g1", "g2")))
    with pytest.raises(WnA1Error, match="generation"):
        _read(FIXTURE_VALID, FIXTURE_VALID + timedelta(hours=1))
