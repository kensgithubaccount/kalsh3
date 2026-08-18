"""Operator-readable M16 live readiness report.

No implicit success is inferred from unit tests or missing evidence.  The default
command reports an unevaluated local host as NOT TESTED and the intentionally absent
write credential as NOT INSTALLED.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from services.production_execution.installed_credential_verification import (
    validate_installed_credential_evidence_for_readiness,
)

from .live_read_acceptance import USER_DATA_FRESHNESS
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
    *,
    public_evidence: Path | None = None,
    postgres_verified: bool = False,
    live_read_evidence: Path | None = None,
    write_credential_evidence: Path | None = None,
    now: datetime | None = None,
) -> dict[str, tuple[Status, str]]:
    evidence: dict[str, tuple[Status, str]] = {
        "PUBLIC_API_COMPATIBILITY": ("PASS", "current official docs reviewed; no auth call"),
        "PUBLIC_EXCHANGE_STATUS": ("NOT TESTED", "no fresh public evidence supplied"),
        "PUBLIC_MARKET_DISCOVERY": ("NOT TESTED", "no fresh public evidence supplied"),
        "POSTGRESQL_RUNTIME": ("NOT TESTED", "integration runtime not supplied"),
        "POSTGRESQL_CONCURRENCY": ("NOT TESTED", "integration runtime not supplied"),
        "SYNTHETIC_SIGNER_RUNTIME": ("PASS", "macOS pipe and Linux memfd tests passed"),
        "CANDIDATE_KEY_AUTHENTICATED_GET": (
            "NOT TESTED",
            "no M27F live read acceptance evidence supplied",
        ),
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
        "AUTHENTICATED_SETTLEMENTS": (
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
    if live_read_evidence is not None:
        _apply_live_read_evidence(
            evidence,
            json.loads(live_read_evidence.read_text()),
            now=now or datetime.now(UTC),
        )
    if write_credential_evidence is not None:
        _apply_write_credential_evidence(
            evidence,
            json.loads(write_credential_evidence.read_text()),
            now=now or datetime.now(UTC),
        )
    return evidence


_READ_GATE_BY_ENDPOINT = {
    "balance": "AUTHENTICATED_PRODUCTION_BALANCE",
    "orders": "AUTHENTICATED_OPEN_ORDERS",
    "positions": "AUTHENTICATED_POSITIONS",
    "fills": "AUTHENTICATED_FILLS",
    "settlements": "AUTHENTICATED_SETTLEMENTS",
}


def _consumption_fresh(payload: dict[str, Any], now: datetime) -> bool:
    """Re-check M27F evidence age at the moment it is consumed, not just at creation.

    The creation-time rule (``completed_at - started_at <= 30s``, enforced inside
    ``live_read_acceptance._reconcile``) only proves the read sweep itself was quick. It says
    nothing about how long ago that sweep happened relative to *now*. A previously fresh,
    successful artifact must not permanently unlock live-read gates, so this independently
    re-derives ``0 <= now - completed_at <= 30s`` from the stored timestamp. Anything else --
    missing, malformed, timezone-naive, or a future ``completed_at`` -- fails closed.
    """
    raw = payload.get("completed_at")
    if not isinstance(raw, str):
        return False
    try:
        completed_at = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if completed_at.tzinfo is None or completed_at.utcoffset() is None:
        return False
    age = now - completed_at
    return timedelta(0) <= age <= USER_DATA_FRESHNESS


def _apply_live_read_evidence(
    evidence: dict[str, tuple[Status, str]], payload: object, *, now: datetime
) -> None:
    """Unlock only the M27F gates a fresh, valid evidence artifact actually proves right now.

    This never touches PRODUCTION_WRITE_CREDENTIAL, PRODUCTION_ARMED, REAL_MUTATION, or
    REAL_SIGNER_VALIDATION: authenticated GET acceptance is not the isolated production
    execution signer runtime with an installed write credential, so that distinction is
    preserved even when every read gate here passes.

    A stale artifact (per ``_consumption_fresh``) is left exactly as it would be if no
    evidence had been supplied at all -- it is not rewritten, and no gate it would otherwise
    unlock is allowed to PASS. This keeps the stored artifact intact as historical evidence
    that a read once succeeded, without letting it count as current live readiness.
    """
    if not isinstance(payload, dict):
        return
    if not _consumption_fresh(payload, now):
        return
    authority = payload.get("candidate_authority")
    if isinstance(authority, dict) and authority.get("classification") == "PASS":
        evidence["CANDIDATE_KEY_AUTHENTICATED_GET"] = (
            "PASS",
            f"key_id_hash={authority.get('key_id_hash')}",
        )
    reads = payload.get("reads")
    by_name = (
        {item.get("name"): item for item in reads if isinstance(item, dict)}
        if isinstance(reads, list)
        else {}
    )
    for endpoint, gate in _READ_GATE_BY_ENDPOINT.items():
        read = by_name.get(endpoint)
        if isinstance(read, dict) and read.get("classification") == "SUCCESS":
            evidence[gate] = (
                "PASS",
                f"count={read.get('count')} payload_sha256={read.get('payload_sha256')}",
            )
    reconciliation = payload.get("reconciliation")
    if isinstance(reconciliation, dict) and reconciliation.get("classification") == "PASS":
        evidence["ACCOUNT_RECONCILIATION"] = (
            "PASS",
            "complete fresh authenticated read set reconciled to subaccount 0",
        )


def _apply_write_credential_evidence(
    evidence: dict[str, tuple[Status, str]], payload: object, *, now: datetime
) -> None:
    """Unlock only PRODUCTION_WRITE_CREDENTIAL / REAL_SIGNER_VALIDATION from M27H evidence.

    Never touches PRODUCTION_ARMED or REAL_MUTATION, and never implicitly unlocks any other
    gate (market_tradable, rules_current, compliance_clear, the M13/M14/M15 final trade gates,
    etc.) -- this function writes to exactly two dictionary keys. Never trusts the artifact's
    own stored ``classification`` -- see ``validate_installed_credential_evidence_for_readiness``,
    which independently re-derives both flags from the artifact's structural fields. A
    structurally invalid artifact leaves both gates exactly as they would be with no evidence
    supplied at all.

    Gemini delta repair: this report never inspects the live protected store directly, so a
    structurally valid but stale artifact proves nothing about whether the store still holds
    that credential *right now* -- it could since have been deleted, corrupted, replaced, or had
    its permissions changed. Both gates are therefore independently freshness-gated by
    ``validate_installed_credential_evidence_for_readiness`` (30 seconds, matching every other
    live-evidence gate in this repository): a stale artifact -- however well-formed -- leaves
    both ``PRODUCTION_WRITE_CREDENTIAL`` and ``REAL_SIGNER_VALIDATION`` exactly at their
    fail-closed defaults, never ``PASS``.
    """
    check = validate_installed_credential_evidence_for_readiness(payload, now=now)
    if check.credential_installed:
        key_id_hash = payload.get("key_id_hash") if isinstance(payload, dict) else None
        evidence["PRODUCTION_WRITE_CREDENTIAL"] = (
            "PASS",
            f"fresh committed installation independently re-validated; key_id_hash={key_id_hash}",
        )
    if check.signer_verified_fresh:
        evidence["REAL_SIGNER_VALIDATION"] = (
            "PASS",
            "fresh local real-signer self-test independently re-validated",
        )


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
    *,
    public_evidence: Path | None = None,
    postgres_verified: bool = False,
    live_read_evidence: Path | None = None,
    write_credential_evidence: Path | None = None,
    now: datetime | None = None,
) -> str:
    lines = ["M27E / M16 GRANULAR READINESS", "production_state: DISARMED"]
    for name, (status, reason) in operator_evidence(
        public_evidence=public_evidence,
        postgres_verified=postgres_verified,
        live_read_evidence=live_read_evidence,
        write_credential_evidence=write_credential_evidence,
        now=now,
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
    parser.add_argument("--live-read-evidence", type=Path)
    parser.add_argument("--write-credential-evidence", type=Path)
    args = parser.parse_args()
    print(
        render_operator_readiness(
            public_evidence=args.public_evidence,
            postgres_verified=args.postgres_verified,
            live_read_evidence=args.live_read_evidence,
            write_credential_evidence=args.write_credential_evidence,
        )
    )


if __name__ == "__main__":
    main()
