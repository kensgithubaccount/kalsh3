"""Streaming deterministic load fixture; it never retains the full event corpus."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class LoadSummary:
    attempts: int
    unique_events: int
    partial_fills: int
    cancellations: int
    gaps: int
    pauses: int
    cross_venue_one_leg: int
    checksum: str


def stream_load(count: int = 100_000, event_count: int = 5_000) -> LoadSummary:
    digest = hashlib.sha256()
    partial = cancellations = gaps = pauses = cross = 0
    events: set[int] = set()
    for index in range(count):
        event = index % event_count
        events.add(event)
        quantity = Decimal(index % 17 + 1) / Decimal(10)
        price = Decimal(3000 + index % 4000) / Decimal(10_000)
        partial += index % 11 == 0
        cancellations += index % 17 == 0
        gaps += index % 1009 == 0
        pauses += index % 2027 == 0
        cross += index % 1237 == 0
        digest.update(f"{index}:{event}:{quantity}:{price}".encode())
    return LoadSummary(
        count, len(events), partial, cancellations, gaps, pauses, cross, digest.hexdigest()
    )
