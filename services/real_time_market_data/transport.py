"""Raw receive boundary shared by future transports and deterministic offline scripts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class ReceivedFrame:
    """An immutable frame timestamped by the transport before payload decoding."""

    raw: str | bytes
    received_at: datetime
    received_monotonic_ns: int

    def __post_init__(self) -> None:
        if not isinstance(self.raw, (str, bytes)):
            raise TypeError("raw frame must be text or bytes")
        if self.received_at.tzinfo is None or self.received_at.utcoffset() is None:
            raise ValueError("received_at must be timezone-aware")
        if self.received_monotonic_ns < 0:
            raise ValueError("received_monotonic_ns must be non-negative")
        object.__setattr__(self, "received_at", self.received_at.astimezone(UTC))
