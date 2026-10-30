"""Offline source-geometry NO-GO preflight for prospective WN3 64-member access.

NO HTTP, GCS, Earth Engine, file writes, credentials, source observation,
probability modelling, paper authority, scheduling, or orders. Computes only
the global-Zarr member-by-lead geometry from previously reviewed constants.
Decoded allocation is NOT observed network transfer, compression or a bill.
Nothing here relaxes the inherited two-GiB reader transfer cap.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from scripts.audit_wn_p1_source_metadata import CITY_HOURS
from services.forecasting.wn_a1_weathernext_evidence import (
    CHUNK_DECODED_BYTES,
    DATA_CHUNKS,
    DATA_SHAPE,
    MAX_TRANSFER_BYTES,
    MEMBER_COUNT,
    validate_source_object,
)

POLICY = "wn-p2b-offline-global-chunk-no-go-v1"
FIRST_TARGET = date(2026, 11, 1)
NO_GO = "NO_EXECUTABLE_MEMBER_SOURCE_DEMONSTRATED"
SOURCE_ROOT = "gs://weathernext3_spatial/weathernext_3_0_0/zarr/2026_to_present/"
EXPECTED_HOURS_PER_CITY = 24
HOURS_PER_LEAD = 6


class MemberAccessPreflightError(ValueError):
    """A frozen target window or source layout does not match the audit."""


@dataclass(frozen=True, slots=True)
class CityChunkRequirement:
    city: str
    valid_hour_offsets: tuple[int, ...]
    six_hour_lead_endpoints: tuple[int, ...]
    global_member_lead_chunks: int


@dataclass(frozen=True, slots=True)
class OfflineMemberAccessNoGo:
    target_local_date: date
    hypothetical_source_object: str
    cities: tuple[CityChunkRequirement, ...]
    required_lead_endpoints: tuple[int, ...]
    required_global_member_lead_keys: tuple[tuple[int, int], ...]
    decoded_bytes_per_global_chunk: int
    total_global_decoded_bytes: int
    inherited_reader_network_cap_bytes: int
    @property
    def measured_network_transfer_bytes(self) -> None:
        return None

    @property
    def measured_network_request_count(self) -> None:
        return None

    @property
    def observed_source_generation(self) -> None:
        return None

    @property
    def observed_source_before_decision(self) -> bool:
        return False

    @property
    def source_access_demonstrated(self) -> bool:
        return False

    @property
    def model_probability_authorized(self) -> bool:
        return False

    @property
    def settlement_station_authorized(self) -> bool:
        return False

    @property
    def paper_authorized(self) -> bool:
        return False

    @property
    def trading_authority(self) -> str:
        return "NONE"

    @property
    def decision(self) -> str:
        return NO_GO

    @property
    def policy(self) -> str:
        return POLICY

    @property
    def research_only(self) -> bool:
        return True


def _lead_endpoint(valid_hour: int) -> int:
    """Choose the six-hour lead containing the valid hour, not its nearest lead."""
    return ((valid_hour + HOURS_PER_LEAD - 1) // HOURS_PER_LEAD) * HOURS_PER_LEAD


def plan_global_member_access_no_go(target_local_date: date) -> OfflineMemberAccessNoGo:
    """Compute needed full-grid chunks, without attempting to acquire any.

    A successful return is still a NO-GO. The calculation is based on one
    prospective 00Z forecast initialization, six hourly substeps per lead,
    64 source members, and the predeclared five-city fixed-standard-time
    development windows. It cannot verify real TWC settlement time windows.
    """
    if type(target_local_date) is not date or not FIRST_TARGET <= target_local_date <= date(
        2099, 12, 31
    ):
        raise MemberAccessPreflightError("prospective target date outside frozen scope")
    if (
        MEMBER_COUNT != 64
        or DATA_SHAPE != (64, 60, 6, 3601, 7200)
        or DATA_CHUNKS != (1, 1, 6, 3601, 7200)
        or CHUNK_DECODED_BYTES != 6 * 3601 * 7200 * 4
        or MAX_TRANSFER_BYTES != 2 * 1024**3
    ):
        raise MemberAccessPreflightError("reviewed source geometry or safety cap changed")
    frozen_windows = {
        "boston": (5, 28),
        "miami": (5, 28),
        "denver": (7, 30),
        "los_angeles": (8, 31),
        "seattle": (8, 31),
    }
    if CITY_HOURS != frozen_windows:
        raise MemberAccessPreflightError("frozen five-city windows changed")

    init = datetime.combine(target_local_date, datetime.min.time(), tzinfo=UTC)
    source_object = (
        SOURCE_ROOT + init.strftime("%Y%m%d") + "_00hr_XX_preds/predictions.zarr/"
    )
    validate_source_object(source_object, init)
    cities: list[CityChunkRequirement] = []
    lead_union: set[int] = set()
    for city, (first, last) in sorted(frozen_windows.items()):
        valid_hours = tuple(range(first, last + 1))
        if len(valid_hours) != EXPECTED_HOURS_PER_CITY:
            raise MemberAccessPreflightError("target city must cover all 24 valid hours")
        leads = tuple(sorted({_lead_endpoint(h) for h in valid_hours}))
        if not leads or leads[0] < 6 or leads[-1] > 360:
            raise MemberAccessPreflightError("invalid six-hour source leads")
        lead_union.update(leads)
        cities.append(
            CityChunkRequirement(
                city=city,
                valid_hour_offsets=valid_hours,
                six_hour_lead_endpoints=leads,
                global_member_lead_chunks=MEMBER_COUNT * len(leads),
            )
        )
    union = tuple(sorted(lead_union))
    keys = tuple((member, lead) for lead in union for member in range(MEMBER_COUNT))
    return OfflineMemberAccessNoGo(
        target_local_date=target_local_date,
        hypothetical_source_object=source_object,
        cities=tuple(cities),
        required_lead_endpoints=union,
        required_global_member_lead_keys=keys,
        decoded_bytes_per_global_chunk=CHUNK_DECODED_BYTES,
        total_global_decoded_bytes=len(keys) * CHUNK_DECODED_BYTES,
        inherited_reader_network_cap_bytes=MAX_TRANSFER_BYTES,
    )
