"""Regenerate the CPI-E1-P10C Phase 1 per-sibling-cutoff acquisition manifest.

Prints the deterministic manifest JSON to stdout. Does not write the frozen
evidence file in place; compare output against
`docs/reviews/artifacts/cpi-p10c-manifest-freeze/manifest.json` to check for
drift.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from services.forecasting.cpi_p10c_manifest import build_phase1_manifest

    print(json.dumps(build_phase1_manifest(root), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
