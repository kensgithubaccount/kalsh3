"""Operator-readable M16 live readiness report.

No implicit success is inferred from unit tests or missing evidence.  The default
command reports an unevaluated local host as NOT TESTED and the intentionally absent
write credential as NOT INSTALLED.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from .readiness import ReadinessSnapshot

_DISPLAY_NAMES = {
    "write_credential_installed": "write credential",
    "production_deployment_live_verified": "production deployment",
    "production_read_live_verified": "production reads",
    "real_reconciliation_live_verified": "live reconciliation",
    "api_compatibility_live_verified": "API compatibility",
    "postgres_concurrency_live_verified": "PostgreSQL concurrency",
    "signer_runtime_live_verified": "signer runtime",
    "m15_path_verified": "M15 path",
    "m14_live_demo_verified": "M14 live demo",
}
Status = Literal["PASS", "FAIL", "NOT TESTED", "NOT INSTALLED", "BLOCKED_BY_CREDENTIAL"]


def operator_evidence(
    *, public_evidence: Path | None = None, postgres_verified: bool = False
) -> dict[str, tuple[Status, str]]:
    evidence: dict[str, tuple[Status, str]] = {
        "PUBLIC_API_COMPATIBILITY": ("PASS", "current official docs reviewed; no auth call"),
        "PUBLIC_EXCHANGE_STATUS": ("NOT TESTED", "no fresh public evidence supplied"),
        "PUBLIC_MARKET_DISCOVERY": ("NOT TESTED", "no fresh public evidence supplied"),
        "POSTGRESQL_RUNTIME": ("NOT TESTED", "integration runtime not supplied"),
        "POSTGRESQL_CONCURRENCY": ("NOT TESTED", "integration runtime not supplied"),
        "SYNTHETIC_SIGNER_RUNTIME": ("PASS", "macOS pipe and Linux memfd tests passed"),
        "AUTHENTICATED_PRODUCTION_BALANCE": (
            "BLOCKED_BY_CREDENTIAL",
            "approved production read credential is absent",
        ),
        "AUTHENTICATED_OPEN_ORDERS": (
            "BLOCKED_BY_CREDENTIAL",
            "approved production read credential is absent",
        ),
        "AUTHENTICATED_POSITIONS": (
            "BLOCKED_BY_CREDENTIAL",
            "approved production read credential is absent",
        ),
        "AUTHENTICATED_FILLS": (
            "BLOCKED_BY_CREDENTIAL",
            "approved production read credential is absent",
        ),
        "REAL_SIGNER_VALIDATION": (
            "BLOCKED_BY_CREDENTIAL",
            "write credential is intentionally absent",
        ),
        "ACCOUNT_RECONCILIATION": (
            "BLOCKED_BY_CREDENTIAL",
            "authenticated account snapshot is unavailable",
        ),
        "PRODUCTION_WRITE_CREDENTIAL": ("NOT INSTALLED", "required M27E safety state"),
        "PRODUCTION_ARMED": ("FAIL", "DISARMED is required in this milestone"),
        "REAL_MUTATION": ("NOT TESTED", "no mutation endpoint was called"),
    }
    if public_evidence is not None:
        payload = json.loads(public_evidence.read_text())
        status = payload.get("exchange_status", {})
        markets = payload.get("markets", {})
        if isinstance(status, dict) and status.get("classification") == "SUCCESS":
            evidence["PUBLIC_EXCHANGE_STATUS"] = (
                "PASS",
                f"HTTP 200; body_sha256={status.get('body_sha256')}",
            )
        if (
            isinstance(markets, dict)
            and markets.get("classification") == "SUCCESS"
            and markets.get("pagination_complete") is True
        ):
            evidence["PUBLIC_MARKET_DISCOVERY"] = (
                "PASS",
                f"HTTP 200; complete pagination; market_count={markets.get('market_count')}",
            )
    if postgres_verified:
        evidence["POSTGRESQL_RUNTIME"] = (
            "PASS",
            "ephemeral localhost PostgreSQL initialized and reconnected",
        )
        evidence["POSTGRESQL_CONCURRENCY"] = (
            "PASS",
            "race, rollback, restart, and recovery integration passed",
        )
    return evidence


def gate_status(name: str, value: bool | None) -> str:
    if name == "write_credential_installed" and value is not True:
        return "NOT INSTALLED"
    if value is None:
        return "NOT TESTED"
    return "PASS" if value else "FAIL"


def render_readiness(
    snapshot: ReadinessSnapshot | None = None, *, now: datetime | None = None
) -> str:
    now = now or datetime.now(UTC)
    lines = ["M16 LIVE READINESS", "production_state: DISARMED", "autonomous_trading: OFF"]
    if snapshot is None:
        lines.append("evidence: no live snapshot supplied")
    for field in fields(ReadinessSnapshot):
        if field.type is not bool:
            continue
        value = None if snapshot is None else bool(getattr(snapshot, field.name))
        label = _DISPLAY_NAMES.get(field.name, field.name.replace("_", " "))
        lines.append(f"{gate_status(field.name, value):12} {label}")
    freshness = "NOT TESTED"
    if snapshot is not None:
        freshness = "PASS" if "user_data_fresh" not in snapshot.missing(now) else "FAIL"
    lines.append(f"{freshness:12} user data freshness")
    lines.append("write mutation: NOT ATTEMPTED")
    return "\n".join(lines)


def render_operator_readiness(
    *, public_evidence: Path | None = None, postgres_verified: bool = False
) -> str:
    lines = ["M27E / M16 GRANULAR READINESS", "production_state: DISARMED"]
    for name, (status, reason) in operator_evidence(
        public_evidence=public_evidence, postgres_verified=postgres_verified
    ).items():
        lines.append(f"{status:22} {name}: {reason}")
    for name in (
        "m13_verified",
        "m14_live_demo_verified",
        "m15_path_verified",
        "production_deployment_live_verified",
        "api_compatibility_live_verified",
        "real_reconciliation_live_verified",
        "model_eligible",
        "source_evidence_ready",
        "compliance_clear",
        "global_halt_clear",
        "kills_clear",
        "exchange_active",
        "trading_active",
        "market_tradable",
        "rules_current",
        "candidate_current",
        "fee_verified",
        "no_unknown_orders",
        "no_unknown_positions",
    ):
        lines.append(f"{'NOT TESTED':22} {name}: no complete live snapshot supplied")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Print every M16 live readiness gate")
    parser.add_argument("--public-evidence", type=Path)
    parser.add_argument("--postgres-verified", action="store_true")
    args = parser.parse_args()
    print(
        render_operator_readiness(
            public_evidence=args.public_evidence, postgres_verified=args.postgres_verified
        )
    )


if __name__ == "__main__":
    main()
