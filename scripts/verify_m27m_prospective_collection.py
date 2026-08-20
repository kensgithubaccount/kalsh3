"""M27M read-only prospective archive verifier.

Performs no writes whatsoever. Independently re-reads and re-hashes every
archived artifact under `--archive-root`, reruns the frozen M27L
`deserialize_prospective_bundle` over every referenced bundle, recomputes
every receipt field and receipt identity from scratch, validates
filename/content identity for every artifact, rejects missing/nonregular/
symlinked/tampered/conflicting artifacts, detects orphan pre-commit
artifacts, and classifies each expected prospective 03Z reference cycle as
PENDING / CAPTURED / MISSED for reporting only -- a cycle file that merely
exists but fails reverification is never treated as evidence of capture.
All verification logic lives in
`services.forecasting.weather_prospective_operations.verify_archive`, which
this script only calls and prints.

Exit code is 0 only if the archive has no problems (`report["ok"]`); the
PENDING/CAPTURED/MISSED cycle classification never affects the exit code,
since MISSED/PENDING are expected, non-error states, not integrity
failures.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.forecasting.weather_prospective_operations import verify_archive


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only M27M prospective archive verifier. Performs no "
            "writes; independently reverifies every archived artifact."
        )
    )
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument(
        "--as-of",
        type=str,
        default=None,
        help="Canonical UTC ISO-8601 timestamp to classify coverage against; defaults to now.",
    )
    args = parser.parse_args()
    as_of = datetime.now(UTC) if args.as_of is None else datetime.fromisoformat(args.as_of)
    report = verify_archive(args.archive_root, as_of=as_of)
    print(json.dumps(report.to_dict(), sort_keys=True, indent=2, default=str))
    raise SystemExit(0 if report.ok else 1)


if __name__ == "__main__":
    main()
