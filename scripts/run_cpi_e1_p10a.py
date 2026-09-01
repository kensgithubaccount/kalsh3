"""Run the CPI-E1-P10A offline binding and market-baseline report."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from services.forecasting.cpi_p10a_binding import build_binding

    print(json.dumps(build_binding(root), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
