"""Offline reconciliation of the accepted M27C Part 2A public probe artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.forecasting.weather_source_authority import (
    AUTHORITY_IDENTITY,
    PHYSICAL_WEATHER_SOURCES,
)

_SCHEMA = "m27c-part2a-static-ncei-candidate-probe-v1"
_MANUALLY_REVIEWED = {"CLIATL", "CLIDCA", "CLIMSP", "CLIMSY", "CLISAT"}


def replay(path: Path) -> dict[str, object]:
    """Reconcile one immutable input snapshot without network access or writes."""
    raw = path.read_bytes()
    document: Any = json.loads(raw)
    if not isinstance(document, dict) or document.get("schema") != _SCHEMA:
        raise ValueError("unsupported M27C Part 2A probe schema")
    rows = document.get("rows")
    if not isinstance(rows, list) or len(rows) != 20:
        raise ValueError("accepted probe must contain exactly 20 rows")
    if any(
        document.get(field) != 0 for field in ("credentials", "kalshi_calls", "production_writes")
    ):
        raise ValueError("probe violates the read-only acceptance boundary")
    if document.get("production_influence") != 0:
        raise ValueError("probe production influence must be zero")

    seen: set[str] = set()
    station_matches = timezone_matches = grid_observations = 0
    ghcnd_matches = tmax = tmin = current_2026 = 0
    reviewed_exceptions: set[str] = set()
    for value in rows:
        if not isinstance(value, dict):
            raise ValueError("probe row must be an object")
        cli_id = value.get("cli_identifier")
        if not isinstance(cli_id, str) or cli_id in seen:
            raise ValueError("probe CLI identifiers must be unique strings")
        seen.add(cli_id)
        authority = PHYSICAL_WEATHER_SOURCES.get(cli_id)
        if authority is None:
            raise ValueError(f"unreviewed probe CLI identifier: {cli_id}")
        if (value.get("kalshi_location"), value.get("expected_timezone")) != (
            authority.canonical_location,
            authority.timezone,
        ):
            raise ValueError(f"Part 1 authority conflict: {cli_id}")
        if value.get("candidate_icao") != authority.nws_station_id:
            raise ValueError(f"candidate NWS identifier conflict: {cli_id}")
        if value.get("nws_station_identifier") == authority.nws_station_id:
            station_matches += 1
        if value.get("nws_timezone") == authority.timezone:
            timezone_matches += 1
        grid = (
            value.get("forecast_grid_id"),
            value.get("forecast_grid_x"),
            value.get("forecast_grid_y"),
            value.get("forecast_grid_data"),
        )
        if (
            isinstance(grid[0], str)
            and grid[0]
            and all(isinstance(item, int) and not isinstance(item, bool) for item in grid[1:3])
            and grid[3] == f"https://api.weather.gov/gridpoints/{grid[0]}/{grid[1]},{grid[2]}"
        ):
            grid_observations += 1
        candidates = value.get("ghcnd_candidates")
        if (
            not isinstance(candidates, list)
            or not candidates
            or not isinstance(candidates[0], dict)
        ):
            raise ValueError(f"missing GHCN-Daily candidate evidence: {cli_id}")
        selected = candidates[0]
        if selected.get("id") == authority.ghcnd_station_id:
            ghcnd_matches += 1
        maximum, minimum = selected.get("TMAX"), selected.get("TMIN")
        if isinstance(maximum, dict):
            tmax += 1
        if isinstance(minimum, dict):
            tmin += 1
        if (
            isinstance(maximum, dict)
            and isinstance(minimum, dict)
            and maximum.get("last_year") == 2026
            and minimum.get("last_year") == 2026
        ):
            current_2026 += 1
        if value.get("unique_within_1km") is False:
            reviewed_exceptions.add(cli_id)

    if seen != set(PHYSICAL_WEATHER_SOURCES):
        raise ValueError("probe coverage differs from repository authority")
    expected = (20, 20, 20, 20, 20, 20, 20)
    actual = (
        station_matches,
        timezone_matches,
        grid_observations,
        ghcnd_matches,
        tmax,
        tmin,
        current_2026,
    )
    if actual != expected:
        raise ValueError(f"accepted probe reconciliation failed: expected {expected}, got {actual}")
    if reviewed_exceptions != _MANUALLY_REVIEWED:
        raise ValueError("manual-review rows differ from accepted repository review")
    return {
        "probe_rows": len(rows),
        "nws_station_identities": station_matches,
        "timezone_matches": timezone_matches,
        "complete_grid_observations": grid_observations,
        "reviewed_ghcnd_mappings": ghcnd_matches,
        "ghcnd_with_tmax": tmax,
        "ghcnd_with_tmin": tmin,
        "ghcnd_tmax_tmin_through_2026": current_2026,
        "manual_review_rows": sorted(reviewed_exceptions),
        "authority_identity": AUTHORITY_IDENTITY,
        "artifact_sha256": hashlib.sha256(raw).hexdigest(),
        "production_influence": "0",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay M27C Part 2A source authority offline")
    parser.add_argument("artifact", type=Path, help="operator-supplied static public probe JSON")
    print(json.dumps(replay(parser.parse_args().artifact), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
