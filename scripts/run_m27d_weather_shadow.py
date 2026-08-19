"""Operator-only M27D shadow entry point; it cannot arm or write."""

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.supervised_canary.m27d import select_experimental_candidate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-evidence", type=Path)
    args = parser.parse_args()
    if args.public_evidence is None:
        result = select_experimental_candidate((), now=datetime.now(UTC))
        print("ABSTAIN_NO_OPEN_MARKET" if not result.candidates else result.state.value)
        print(result.reason)
        return
    evidence = json.loads(args.public_evidence.read_text())
    markets = evidence.get("markets", {})
    if (
        not isinstance(markets, dict)
        or markets.get("classification") != "SUCCESS"
        or markets.get("pagination_complete") is not True
    ):
        raise RuntimeError("public market evidence is incomplete or failed")
    if markets.get("market_count") == 0:
        print("ABSTAIN_NO_OPEN_MARKET")
        print(
            "fresh KXHIGHCHI market discovery succeeded with complete pagination; "
            "contract settlement location identifier remains CLIMDW; "
            f"evidence_sha256={hashlib.sha256(args.public_evidence.read_bytes()).hexdigest()}"
        )
        return
    print("ABSTAIN_NO_VALID_FORECAST")
    print(
        "fresh active KXHIGHCHI market exists; "
        "contract settlement location identifier remains CLIMDW; "
        "no reviewed current forecast bundle was supplied"
    )


if __name__ == "__main__":
    main()
