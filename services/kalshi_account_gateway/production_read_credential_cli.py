"""Manual production read-only credential enrollment and verification CLI."""

from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime
from pathlib import Path

from .production_read_credentials import (
    ProductionCredentialError,
    ProductionCredentialState,
    ProductionReadCredentialStore,
    UrllibProductionReadTransport,
    default_production_store_directory,
    read_private_key_fd,
)
from .read_credentials import ReadEnvironment


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Manual production server-proven read-only credential lifecycle"
    )
    value.add_argument("action", choices=("enroll", "verify"))
    value.add_argument("--environment", required=True, choices=[ReadEnvironment.PRODUCTION.value])
    value.add_argument("--key-id")
    value.add_argument("--credential-fd", type=int, default=0)
    value.add_argument("--store-dir", type=Path, default=default_production_store_directory())
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    store = ProductionReadCredentialStore(args.store_dir)
    try:
        if args.action == "enroll":
            if not args.key_id:
                raise ProductionCredentialError("key identifier is required")
            private_key = read_private_key_fd(args.credential_fd)
            store.enroll(args.key_id, private_key, now=datetime.now(UTC))
            print("PRODUCTION credential enrolled UNVERIFIED; scope verification is required")
            return 0
        record = store.load()
        if record.state is not ProductionCredentialState.ENROLLED_UNVERIFIED:
            raise ProductionCredentialError("credential is not awaiting verification")
        result = store.verify(
            UrllibProductionReadTransport(),
            timestamp_ms=time.time_ns() // 1_000_000,
            now=datetime.now(UTC),
        )
        if result.server_scopes != frozenset({"read"}):
            raise ProductionCredentialError("server scope verification failed")
        print("PRODUCTION credential verified with exact server-side read scope")
        return 0
    except ProductionCredentialError:
        print("BLOCKER: production read credential lifecycle operation failed")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
