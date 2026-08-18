"""M27G -- narrowly scoped operator CLI for real production write-credential enrollment.

No network dependency: this CLI never imports a Kalshi transport. It reads secret-safe
inputs from files and an inherited file descriptor, constructs the candidate credential,
independently re-validates a pre-generated candidate-authority attestation and a *fresh*
M27F v3 live-read evidence artifact against this exact candidate, requires the exact
real-money confirmation string, atomically installs the sealed credential, runs the
non-network real-signer self-test against the key just installed, and prints a secret-free
receipt plus the disarmed guarantees below. It never arms production and never sends a
request.

The intended future operator sequence (never run automatically, and not run by this
milestone) is:

  1. run a fresh GET-only M27F live-read-acceptance sweep for the exact candidate
  2. immediately invoke this CLI, pointing --live-read-evidence at that fresh artifact
  3. this CLI consumes it within the 30-second freshness window it independently enforces

Historical M27F evidence is rejected -- see
:func:`services.production_execution.enrollment.validate_live_read_evidence_for_installation`.

The private key is accepted only through an inherited file descriptor, never argv, an
environment variable, echoed stdin, or a repository file. No raw key ID, PEM, signature, or
auth header is ever printed.
"""

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from services.kalshi_account_gateway.production_read_credentials import read_private_key_fd

from .credentials import REQUIRED_LIVE_WRITE_SCOPES, ProductionWriteCredential
from .enrollment import (
    ProtectedWriteCredentialStore,
    install_production_write_credential,
)


def default_production_write_store_directory() -> Path:
    state_home = os.environ.get("XDG_STATE_HOME")
    root = Path(state_home) if state_home else Path.home() / ".local" / "state"
    return root / "kalsh3" / "production-write"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Operator-only real production write-credential enrollment. No network "
            "dependency; never arms production; never sends a request."
        )
    )
    parser.add_argument("--key-id-file", required=True, type=Path)
    parser.add_argument("--private-key-fd", required=True, type=int)
    parser.add_argument("--authority-attestation", required=True, type=Path)
    parser.add_argument("--live-read-evidence", required=True, type=Path)
    parser.add_argument(
        "--store-dir", type=Path, default=default_production_write_store_directory()
    )
    parser.add_argument(
        "--confirm", required=True, help="exact real-money installation confirmation string"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        key_id = args.key_id_file.read_text().strip()
        if not key_id:
            raise ValueError("key id file is empty")
        private_key_pem = read_private_key_fd(args.private_key_fd)
        credential = ProductionWriteCredential(
            key_id, private_key_pem, REQUIRED_LIVE_WRITE_SCOPES, fixture_only=False
        )
        authority_attestation = json.loads(args.authority_attestation.read_text())
        live_read_evidence = json.loads(args.live_read_evidence.read_text())
        outcome = install_production_write_credential(
            credential=credential,
            store=ProtectedWriteCredentialStore(args.store_dir),
            authority_attestation=authority_attestation,
            live_read_evidence=live_read_evidence,
            explicit_confirmation=args.confirm,
            now=datetime.now(UTC),
        )
    except Exception as exc:  # boundary: never leak secrets, only a sanitized class name
        print(
            f"BLOCKER: production write-credential enrollment failed ({type(exc).__name__})",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(outcome.to_json(), sort_keys=True, indent=2))
    print("PRODUCTION_ARMED: DISARMED")
    print("REAL_MUTATION: NOT TESTED")
    print("ORDER_SENT: NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
