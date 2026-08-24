"""Bounded unauthenticated Kalshi production read acceptance for M27E.

Thin CLI/operator wrapper only. The reusable M27E business logic now lives beside the shared
GET-only transport in :mod:`services.market_universe.m27e_public_acceptance`, so M27R can compose
the exact same reviewed evidence producer without a forbidden ``services -> scripts`` import.

Only fixed public GET paths are reachable. The output schema remains
``kalsh3.m27e.public-read.v1`` and is unchanged for M27I consumers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from services.market_universe.m27e_public_acceptance import (
    ACTIVE_MARKET_STATUS,
    SERIES_TICKER,
    acquire_public_acceptance,
)
from services.market_universe.m27e_public_acceptance import paged_markets as _paged_markets
from services.market_universe.public_read import (
    BASE,
    HOST,
    MAX_RESPONSE_BYTES,
    PublicReadFailure,
    get,
    get_market,
    get_market_with_body,
)

__all__ = [
    "ACTIVE_MARKET_STATUS",
    "BASE",
    "HOST",
    "MAX_RESPONSE_BYTES",
    "SERIES_TICKER",
    "PublicReadFailure",
    "get",
    "get_market",
    "get_market_with_body",
    "main",
    "paged_markets",
]


def paged_markets() -> dict[str, object]:
    """Preserve the script-level GET seam while delegating to the shared producer."""
    return _paged_markets(getter=get)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        evidence = acquire_public_acceptance()
    except PublicReadFailure as exc:
        print(f"PUBLIC_READ_FAILED: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, sort_keys=True, indent=2) + "\n")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
