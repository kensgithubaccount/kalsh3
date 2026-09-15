"""Run one bounded RT-A1 research collection cycle.

This operator entrypoint uses public unauthenticated GETs only and writes no
orders, alerts, account state, or strategy output.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

# Direct execution places ``scripts`` first on sys.path; bind imports to this
# checkout rather than relying on an installed package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.rt_a1.runner import run_cycle
from services.rt_a1.store import RtA1Store


def main() -> int:
    parser = argparse.ArgumentParser(description="RT-A1 research-only collector")
    parser.add_argument("--store", type=Path, default=Path("evidence/rt_a1/evidence.sqlite3"))
    args = parser.parse_args()
    result = run_cycle(
        RtA1Store(args.store),
        run_id="rt-a1-" + uuid4().hex,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
