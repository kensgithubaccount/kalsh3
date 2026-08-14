"""Manual M25B3 demo credential enrollment and provenance verification CLI."""

from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime
from pathlib import Path

from .domain import ShadowResearchError
from .exact_read_credentials import (
    CredentialBoundaryError,
    ExactReadCredentialStore,
    default_store_directory,
    read_private_key_fd,
)
from .live_boundary import MarginEnvironment, UrllibMarginHttpTransport


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Manual DEMO exact-read credential lifecycle")
    value.add_argument("action", choices=("enroll", "verify"))
    value.add_argument(
        "--environment", required=True, choices=[item.value for item in MarginEnvironment]
    )
    value.add_argument("--key-id")
    value.add_argument("--credential-fd", type=int, default=0)
    value.add_argument("--store-dir", type=Path, default=default_store_directory())
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.environment != MarginEnvironment.DEMO.value:
        print("BLOCKER: M25B3 supports DEMO credential composition only")
        return 2
    store = ExactReadCredentialStore(args.store_dir)
    try:
        if args.action == "enroll":
            if not args.key_id:
                raise CredentialBoundaryError("key identifier is required")
            private_key = read_private_key_fd(args.credential_fd)
            store.enroll_demo(args.key_id, private_key, now=datetime.now(UTC))
            print("DEMO credential enrolled UNVERIFIED; verification is still required")
            return 0
        result = store.verify_demo(
            UrllibMarginHttpTransport(),
            timestamp_ms=time.time_ns() // 1_000_000,
            now=datetime.now(UTC),
        )
        entitlement = "enabled" if result.perps_enabled else "not enabled"
        print(f"DEMO environment provenance verified; Perps entitlement is {entitlement}")
        return 0
    except (CredentialBoundaryError, ShadowResearchError):
        print("BLOCKER: DEMO credential lifecycle operation failed")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
