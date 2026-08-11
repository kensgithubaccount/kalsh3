"""Independent connectors, bounded queues, reaction schedules and shadow termination."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from .models import SignalObservation


class ConnectorState(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    CONNECTING = "CONNECTING"
    SHADOW = "SHADOW"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"
    QUARANTINED = "QUARANTINED"
    SETUP_REQUIRED = "SETUP_REQUIRED"


@dataclass(slots=True)
class ConnectorHealth:
    connector_id: str
    state: ConnectorState = ConnectorState.NOT_STARTED
    queue_depth: int = 0
    queue_limit: int = 10000
    processing_lag_ms: int = 0
    reconnects: int = 0
    dropped_events: int = 0
    invalid_events: int = 0
    parse_failures: int = 0
    heartbeats_healthy: bool = False
    provider_requests: int = 0
    events_ingested: int = 0
    bytes_ingested: int = 0
    processing_ms: int = 0
    estimated_cost: Decimal = Decimal(0)
    gap_markers: int = 0
    consecutive_failures: int = 0
    circuit_open_until: datetime | None = None


@dataclass(slots=True)
class Connector:
    health: ConnectorHealth
    queue: deque[dict[str, Any]] = field(default_factory=deque)

    def enqueue(self, event: dict[str, Any], now: datetime) -> bool:
        if len(self.queue) >= self.health.queue_limit:
            self.health.state = ConnectorState.DEGRADED
            self.health.dropped_events += 1
            self.health.gap_markers += 1
            return False
        self.queue.append(event)
        self.health.queue_depth = len(self.queue)
        self.health.bytes_ingested += len(str(event).encode())
        self.health.events_ingested += 1
        return True

    def failed(self, now: datetime) -> float:
        self.health.consecutive_failures += 1
        self.health.reconnects += 1
        delay = float(min(300, 2 ** min(self.health.consecutive_failures, 8)))
        self.health.circuit_open_until = now + timedelta(seconds=delay)
        self.health.state = ConnectorState.DEGRADED
        return delay

    def succeeded(self) -> None:
        self.health.consecutive_failures = 0
        self.health.circuit_open_until = None
        self.health.state = ConnectorState.SHADOW


class ConnectorSupervisor:
    def __init__(self) -> None:
        self.connectors: dict[str, Connector] = {}

    def add(self, connector: Connector) -> None:
        self.connectors[connector.health.connector_id] = connector

    def state(self, connector_id: str) -> ConnectorHealth:
        return self.connectors[connector_id].health


@dataclass(frozen=True, slots=True)
class ReactionSchedule:
    signal_id: str
    due_at: tuple[datetime, ...]


def schedule_reactions(signal_id: str, detected_at: datetime) -> ReactionSchedule:
    return ReactionSchedule(
        signal_id,
        tuple(
            detected_at + offset
            for offset in (
                timedelta(seconds=30),
                timedelta(minutes=2),
                timedelta(minutes=5),
                timedelta(minutes=15),
                timedelta(hours=1),
            )
        ),
    )


def shadow_sink(signal: SignalObservation) -> str:
    if signal.production_influence != 0:
        raise ValueError("production influence forbidden")
    return "SHADOW RESEARCH DATA"
