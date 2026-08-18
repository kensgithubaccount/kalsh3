"""Operator-only M27D shadow entry point; it cannot arm or write."""

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.supervised_canary.m27d import CandidateState, select_experimental_candidate


def main() -> None:
    # Live evidence is intentionally supplied by a separately reviewed read-only
    # collector.  An absent evidence bundle is a safe, explicit shadow abstention.
    result = select_experimental_candidate((), now=datetime.now(UTC))
    print(result.state.value)
    print(result.reason)
    if result.state is CandidateState.QUALIFYING_EXPERIMENTAL_CANARY:
        raise RuntimeError("shadow command unexpectedly received no transport-free evidence")


if __name__ == "__main__":
    main()
