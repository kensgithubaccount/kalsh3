"""Bounded append-only gzip archive batches for later M6 point-in-time replay."""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RawRecord:
    epoch: UUID
    channel: str
    sid: int | None
    seq: int | None
    exchange_at: datetime | None
    receive_wall_at: datetime
    receive_monotonic_ns: int
    payload_hash: str
    payload: dict[str, Any]
    gap_marker: bool = False

    @classmethod
    def create(
        cls,
        epoch: UUID,
        channel: str,
        sid: int | None,
        seq: int | None,
        receive_wall_at: datetime,
        receive_monotonic_ns: int,
        payload: dict[str, Any],
        exchange_at: datetime | None = None,
        gap_marker: bool = False,
    ) -> RawRecord:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return cls(
            epoch,
            channel,
            sid,
            seq,
            exchange_at,
            receive_wall_at,
            receive_monotonic_ns,
            hashlib.sha256(canonical).hexdigest(),
            payload,
            gap_marker,
        )

    def wire(self) -> dict[str, Any]:
        return {
            "epoch": str(self.epoch),
            "channel": self.channel,
            "sid": self.sid,
            "seq": self.seq,
            "exchange_at": None if self.exchange_at is None else self.exchange_at.isoformat(),
            "receive_wall_at": self.receive_wall_at.isoformat(),
            "receive_monotonic_ns": self.receive_monotonic_ns,
            "payload_hash": self.payload_hash,
            "payload": self.payload,
            "gap_marker": self.gap_marker,
        }


@dataclass(frozen=True, slots=True)
class ArchiveBatch:
    compressed: bytes
    sha256: str
    event_count: int
    gap_markers: int


@dataclass(slots=True)
class ArchiveBuffer:
    max_events: int = 1000
    records: list[RawRecord] = field(default_factory=list)

    def append(self, record: RawRecord) -> ArchiveBatch | None:
        self.records.append(record)
        return self.flush() if len(self.records) >= self.max_events else None

    def flush(self) -> ArchiveBatch | None:
        if not self.records:
            return None
        raw = (
            b"\n".join(
                json.dumps(item.wire(), sort_keys=True, separators=(",", ":")).encode()
                for item in self.records
            )
            + b"\n"
        )
        compressed = gzip.compress(raw, compresslevel=6, mtime=0)
        batch = ArchiveBatch(
            compressed,
            hashlib.sha256(compressed).hexdigest(),
            len(self.records),
            sum(x.gap_marker for x in self.records),
        )
        self.records.clear()
        return batch


def replay(batch: ArchiveBatch) -> list[dict[str, Any]]:
    raw = gzip.decompress(batch.compressed)
    records = [json.loads(line) for line in raw.splitlines()]
    if (
        len(records) != batch.event_count
        or hashlib.sha256(batch.compressed).hexdigest() != batch.sha256
    ):
        raise ValueError("archive integrity failure")
    return records
