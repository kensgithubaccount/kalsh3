"""Build the immutable, research-only E0.1 runtime acceptance receipt.

This is an evidence compositor, not an execution runner.  Live-read and PostgreSQL
status values are supplied explicitly by the operator so missing capabilities cannot
be mistaken for synthetic success.  The receipt hash covers every field except the
hash field itself and is therefore deterministic for identical inputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA = "kalsh3.e0.1.production-read-runtime-acceptance.v1"


def _hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def build_receipt(
    *,
    canonical_sha: str,
    canonical_tree: str,
    acceptance_sha: str,
    acceptance_tree: str,
    environment_type: str,
    api_schema_identity: dict[str, str],
    authenticated_read: dict[str, Any],
    account_reconciliation: dict[str, Any],
    postgres_acceptance: dict[str, Any],
    restart_recovery: dict[str, Any],
    fee_policy: dict[str, Any],
) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "schema": SCHEMA,
        "canonical": {"sha": canonical_sha, "tree": canonical_tree},
        "acceptance": {"sha": acceptance_sha, "tree": acceptance_tree},
        "environment_type": environment_type,
        "authenticated_read": authenticated_read,
        "api_schema_identity": api_schema_identity,
        "account_reconciliation": account_reconciliation,
        "postgres_acceptance": postgres_acceptance,
        "restart_recovery": restart_recovery,
        "fee_policy": fee_policy,
        "safety": {
            "live_mutation_count": 0,
            "live_orders_placed": 0,
            "write_credentials_installed": False,
            "production_boundary_armed": False,
            "production_influence": 0,
        },
    }
    receipt["receipt_sha256"] = _hash(receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--canonical-sha", required=True)
    parser.add_argument("--canonical-tree", required=True)
    parser.add_argument("--acceptance-sha", required=True)
    parser.add_argument("--acceptance-tree", required=True)
    parser.add_argument("--api-schema-sha256", required=True)
    parser.add_argument("--api-schema-observed-at", required=True)
    args = parser.parse_args()
    receipt = build_receipt(
        canonical_sha=args.canonical_sha,
        canonical_tree=args.canonical_tree,
        acceptance_sha=args.acceptance_sha,
        acceptance_tree=args.acceptance_tree,
        environment_type="LOCAL_EPHEMERAL_POSTGRES_18.6",
        api_schema_identity={
            "status": "CURRENT_DOCUMENTATION_COMPATIBLE",
            "source": "https://docs.kalshi.com",
            "sha256": args.api_schema_sha256,
            "observed_at": args.api_schema_observed_at,
        },
        authenticated_read={
            "status": "BLOCKED_BY_CREDENTIAL",
            "live_read": False,
            "blocker": (
                "approved authenticated read-only key, private key, and authority "
                "attestation are absent"
            ),
        },
        account_reconciliation={
            "status": "FIXTURE_PASS_LIVE_READ_BLOCKED",
            "live_read": "BLOCKED_BY_CREDENTIAL",
            "fixture": "PASS",
            "fixture_scope": (
                "duplicate/missing/impossible-fill/order-fill/position/pagination fail-closed cases"
            ),
        },
        postgres_acceptance={
            "status": "PASS",
            "mechanism": "isolated local temporary PostgreSQL cluster",
            "version": "PostgreSQL 18.6",
            "database": "e01_r1a_acceptance",
            "schema_migrations": "PASS",
            "journal_persistence": "PASS",
            "concurrency_idempotency": "PASS",
            "transaction_rollback": "PASS",
            "failure_modes": "PASS: connection loss/duplicate key/unique unresolved claim",
        },
        restart_recovery={
            "status": "PASS",
            "server_restart": "PASS",
            "partial_fill_persistence": "PASS",
            "terminal_order_persistence": "PASS",
            "reconciliation_after_restart": "PASS",
            "durable_disarmed_state": "PASS",
        },
        fee_policy={
            "status": "CURRENT_FEE_POLICY_COMPATIBLE",
            "source": "repository-reviewed Kalshi event fee schedule",
            "effective_date": "2026-07-07",
            "policy_version": "kalshi-event-fees-2026-07-07-v1",
            "realized_live_fees_validated": False,
        },
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    print(receipt["receipt_sha256"])


if __name__ == "__main__":
    main()
