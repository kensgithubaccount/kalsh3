"""Explicit historical availability provenance and immutable replay envelopes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol


class ReplayError(ValueError):
    pass


class Partition(StrEnum):
    LIVE = "LIVE"
    HISTORICAL = "HISTORICAL"


class AvailabilityBasis(StrEnum):
    OBSERVED_LIVE = "OBSERVED_LIVE"
    RECONSTRUCTED_EXCHANGE = "RECONSTRUCTED_EXCHANGE"
    RECONSTRUCTED_PRIMARY_SOURCE = "RECONSTRUCTED_PRIMARY_SOURCE"
    RECONSTRUCTED_EXTERNAL = "RECONSTRUCTED_EXTERNAL"
    UNKNOWN = "UNKNOWN"


class AvailabilityQuality(StrEnum):
    MEASURED = "MEASURED"
    AUTHORITATIVE_RECONSTRUCTION = "AUTHORITATIVE_RECONSTRUCTION"
    CONSERVATIVE_ASSUMPTION = "CONSERVATIVE_ASSUMPTION"
    DESCRIPTIVE_ONLY = "DESCRIPTIVE_ONLY"


@dataclass(frozen=True, slots=True)
class Availability:
    source_event_at: datetime | None
    source_publish_at: datetime | None
    provider_receive_at: datetime | None
    actual_bot_ingest_at: datetime
    replay_available_at: datetime | None
    basis: AvailabilityBasis
    quality: AvailabilityQuality
    assumed_latency: timedelta | None

    def __post_init__(self) -> None:
        if self.basis == AvailabilityBasis.OBSERVED_LIVE:
            if (
                self.replay_available_at != self.actual_bot_ingest_at
                or self.assumed_latency is not None
                or self.quality != AvailabilityQuality.MEASURED
            ):
                raise ReplayError("observed-live availability must use actual ingest")
        elif self.basis == AvailabilityBasis.UNKNOWN:
            if self.replay_available_at is not None:
                raise ReplayError("unknown availability cannot enter causal replay")
        else:
            if (
                self.assumed_latency is None
                or self.assumed_latency < timedelta(0)
                or self.replay_available_at is None
            ):
                raise ReplayError("reconstruction requires explicit conservative latency")
            anchor = (
                self.source_event_at
                if self.basis == AvailabilityBasis.RECONSTRUCTED_EXCHANGE
                else self.source_publish_at
            )
            if anchor is None or self.replay_available_at != anchor + self.assumed_latency:
                raise ReplayError("reconstructed availability does not match basis")

    @classmethod
    def observed_live(
        cls,
        source_event_at: datetime | None,
        source_publish_at: datetime | None,
        provider_receive_at: datetime | None,
        actual_bot_ingest_at: datetime,
    ) -> Availability:
        return cls(
            source_event_at,
            source_publish_at,
            provider_receive_at,
            actual_bot_ingest_at,
            actual_bot_ingest_at,
            AvailabilityBasis.OBSERVED_LIVE,
            AvailabilityQuality.MEASURED,
            None,
        )

    @classmethod
    def reconstructed(
        cls,
        basis: AvailabilityBasis,
        *,
        source_event_at: datetime | None,
        source_publish_at: datetime | None,
        actual_bot_ingest_at: datetime,
        assumed_latency: timedelta,
        quality: AvailabilityQuality = AvailabilityQuality.CONSERVATIVE_ASSUMPTION,
    ) -> Availability:
        if basis in {AvailabilityBasis.OBSERVED_LIVE, AvailabilityBasis.UNKNOWN}:
            raise ReplayError("invalid reconstructed basis")
        anchor = (
            source_event_at
            if basis == AvailabilityBasis.RECONSTRUCTED_EXCHANGE
            else source_publish_at
        )
        if anchor is None:
            raise ReplayError("reconstruction anchor missing")
        return cls(
            source_event_at,
            source_publish_at,
            None,
            actual_bot_ingest_at,
            anchor + assumed_latency,
            basis,
            quality,
            assumed_latency,
        )

    @classmethod
    def unknown(
        cls,
        *,
        source_event_at: datetime | None,
        source_publish_at: datetime | None,
        actual_bot_ingest_at: datetime,
    ) -> Availability:
        return cls(
            source_event_at,
            source_publish_at,
            None,
            actual_bot_ingest_at,
            None,
            AvailabilityBasis.UNKNOWN,
            AvailabilityQuality.DESCRIPTIVE_ONLY,
            None,
        )

    @property
    def measured_latency(self) -> timedelta | None:
        return (
            self.actual_bot_ingest_at - self.source_publish_at
            if self.basis == AvailabilityBasis.OBSERVED_LIVE and self.source_publish_at
            else None
        )


@dataclass(frozen=True, slots=True)
class ReplayEvent:
    event_id: str
    event_type: str
    provider: str
    availability: Availability
    provider_sequence: int | None
    receive_monotonic_sequence: int | None
    ingest_sequence: int | None
    entity_ids: tuple[str, ...]
    raw_hash: str
    normalized_hash: str
    parser_version: str
    schema_version: str
    payload: dict[str, Any]
    gap_marker: bool = False
    correction_of_event_id: str | None = None
    ordering_ambiguous: bool = False


class ReplayConsumer(Protocol):
    def apply(self, event: ReplayEvent) -> None: ...


def partition_for(timestamp: datetime, cutoff: datetime) -> Partition:
    return Partition.HISTORICAL if timestamp < cutoff else Partition.LIVE


def order_key(event: ReplayEvent) -> tuple[datetime, int, int, int, str]:
    available = event.availability.replay_available_at
    if available is None:
        raise ReplayError("unknown-availability event cannot be ordered")
    maximum = 2**63 - 1
    return (
        available,
        event.provider_sequence if event.provider_sequence is not None else maximum,
        event.receive_monotonic_sequence
        if event.receive_monotonic_sequence is not None
        else maximum,
        event.ingest_sequence if event.ingest_sequence is not None else maximum,
        event.event_id,
    )


def point_in_time(events: list[ReplayEvent], as_of: datetime) -> list[ReplayEvent]:
    if as_of.tzinfo is None:
        raise ReplayError("as_of must be timezone-aware")
    return sorted(
        (
            event
            for event in events
            if event.availability.replay_available_at is not None
            and event.availability.replay_available_at <= as_of
        ),
        key=order_key,
    )


def replay(events: list[ReplayEvent], as_of: datetime, consumer: ReplayConsumer) -> int:
    selected = point_in_time(events, as_of)
    for event in selected:
        consumer.apply(event)
    return len(selected)
