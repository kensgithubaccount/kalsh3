"""Redacted support snapshot generation."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

SENSITIVE_FRAGMENTS = (
    "key",
    "secret",
    "password",
    "token",
    "signature",
    "pem",
    "cookie",
    "session",
    "recovery",
    "order_id",
    "fill_id",
    "account_id",
    "account_number",
    "client_order_id",
    "authorization",
    "auth_header",
)
SENSITIVE_VALUES = (
    "-----BEGIN ",
    "-----END ",
    "KALSHI-ACCESS-KEY",
    "KALSHI-ACCESS-SIGNATURE",
    "Bearer ",
)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]"
                if any(part in str(key).lower() for part in SENSITIVE_FRAGMENTS)
                else redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str) and any(marker in value for marker in SENSITIVE_VALUES):
        return "[REDACTED]"
    return value


def support_snapshot_json(snapshot: Any, config: dict[str, Any]) -> str:
    raw = asdict(snapshot) if hasattr(snapshot, "__dataclass_fields__") else snapshot
    body = redact(dict(raw) | {"config": config})
    return json.dumps(body, default=str, sort_keys=True, separators=(",", ":"))


def support_snapshot_markdown(snapshot: Any) -> str:
    """Small human-readable diagnostic without raw authenticated response bodies."""
    return "\n".join(
        (
            "# Kalshi Production v3 support snapshot",
            "",
            "- Release: M18 operations hardening (offline verified)",
            f"- Account gateway status: {redact(getattr(snapshot, 'status', 'unknown'))}",
            f"- Last attempt: {getattr(snapshot, 'last_attempt', None) or 'never'}",
            f"- Last success: {getattr(snapshot, 'last_success', None) or 'never'}",
            f"- Failure: {redact(getattr(snapshot, 'failure_reason', None)) or 'none'}",
            "- Operational readiness: NOT VERIFIED",
            "- Production state: DISARMED",
            "- Autonomy: OFF",
            "- Production write credential: NONE",
            "- Real-money execution: NONE",
            "",
        )
    )
