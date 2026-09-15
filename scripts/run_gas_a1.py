from __future__ import annotations

import argparse
from pathlib import Path

from services.forecasting.gas_a1 import GasA1Store, collect_once


def main() -> None:
    parser = argparse.ArgumentParser(description="GAS-A1 bounded public evidence collection")
    parser.add_argument("--db", type=Path, default=Path("data/gas-a1.sqlite3"))
    args = parser.parse_args()
    run_id = collect_once(GasA1Store(args.db))
    print(f"GAS-A1 COLLECTION REGISTERED {run_id}")


if __name__ == "__main__":
    main()
