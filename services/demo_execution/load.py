"""Streaming deterministic M14 fault-load fixture."""

import hashlib
from dataclasses import dataclass

from .faults import Fault


@dataclass(frozen=True, slots=True)
class LoadResult:
    lifecycles: int
    unique_orders: int
    duplicate_fills_ignored: int
    unknown: int
    checksum: str


def run_fault_load(count: int = 20_000) -> LoadResult:
    faults = tuple(Fault)
    digest = hashlib.sha256()
    unknown = 0
    duplicates = 0
    for index in range(count):
        fault = faults[index % len(faults)]
        digest.update(f"{index}:{fault}".encode())
        unknown += fault in {
            Fault.TIMEOUT_AFTER_SEND,
            Fault.RATE_LIMIT,
            Fault.SERVER_ERROR,
            Fault.MALFORMED,
            Fault.WS_GAP,
            Fault.DISCONNECT,
        }
        duplicates += fault == Fault.DUPLICATE_MESSAGE
    return LoadResult(count, count, duplicates, unknown, digest.hexdigest())
