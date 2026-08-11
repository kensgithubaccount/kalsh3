"""Secret-safe structured events, bounded metrics, and diagnostic health output."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from services.reporting_service.support_snapshot import redact

SAFE_METRIC_NAMES = frozenset(
    {
        "dependency_failures_total",
        "queue_depth",
        "worker_restarts_total",
        "reconciliation_age_seconds",
        "unknown_mutations_total",
        "backup_age_seconds",
        "restore_drill_age_seconds",
        "disk_used_ratio",
        "monthly_cost_usd",
    }
)


@dataclass(frozen=True, slots=True)
class TraceContext:
    """Opaque correlation identifiers only; never baggage or credentials."""

    trace_id: str
    span_id: str

    def __post_init__(self) -> None:
        if len(self.trace_id) != 32 or len(self.span_id) != 16:
            raise ValueError("trace identifiers have invalid length")
        if any(character not in "0123456789abcdef" for character in self.trace_id + self.span_id):
            raise ValueError("trace identifiers must be lowercase hexadecimal")


def structured_event(
    event: str, fields: dict[str, object], trace: TraceContext | None = None
) -> str:
    if not event or any(character.isspace() for character in event):
        raise ValueError("event name must be a stable machine code")
    body = {
        "timestamp": datetime.now(UTC).isoformat(),
        "event": event,
        "fields": redact(fields),
    }
    if trace is not None:
        body["trace_id"] = trace.trace_id
        body["span_id"] = trace.span_id
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(slots=True)
class MetricRegistry:
    values: dict[str, Decimal] = field(default_factory=dict)

    def set(self, name: str, value: Decimal | int) -> None:
        if name not in SAFE_METRIC_NAMES:
            raise ValueError("metric name is not allowlisted")
        exact = Decimal(value)
        if not exact.is_finite() or exact < 0:
            raise ValueError("metric value must be finite and non-negative")
        self.values[name] = exact

    def prometheus(self) -> str:
        return "".join(f"kpv3_{name} {self.values[name]}\n" for name in sorted(self.values))


def safe_health_payload(
    *, process_healthy: bool, ready: bool, blockers: tuple[str, ...], version: str
) -> str:
    return json.dumps(
        {
            "status": "healthy" if process_healthy else "failed",
            "ready": ready,
            "blockers": blockers,
            "version": version,
            "production_state": "DISARMED",
            "autonomy": "OFF",
            "production_write_credential": "NONE",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
