#!/usr/bin/env python3
"""Explicit builder for the deterministic P9B.4R derived coverage file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.validate_cpi_p9b_fee_authority import PACKAGE, _validate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, default=PACKAGE)
    package = parser.parse_args().package
    result = _validate(package, require_frozen_coverage=False)
    (package / "event_coverage.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps({"market_rows": len(result["market_rows"]), "events": len(result["events"])}))


if __name__ == "__main__":
    main()
