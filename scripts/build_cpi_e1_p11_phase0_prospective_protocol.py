#!/usr/bin/env python3
"""Regenerate the frozen CPI-E1-P11 Phase-0 prospective protocol."""

from __future__ import annotations

import json
from pathlib import Path

from services.forecasting.cpi_p11_prospective_protocol import build_phase0_protocol

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/reviews/artifacts/cpi-p11-phase0-prospective-protocol/spec.json"


def main() -> int:
    spec = build_phase0_protocol()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(spec, sort_keys=True, indent=2) + "\n")
    print(OUTPUT)
    print(spec["spec_digest_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
