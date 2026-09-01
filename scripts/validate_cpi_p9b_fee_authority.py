#!/usr/bin/env python3
"""Fail-closed validator and deterministic builder for CPI-E1-P9B.4."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[1]
PACKAGE = ROOT / "evidence/cpi_p9b_fee_authority"
P9A = ROOT / "evidence/cpi_p9a_historical_price/manifest.json"


def strict_load(path: Path) -> Any:
    def duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(
        path.read_bytes(),
        object_pairs_hook=duplicate,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"non-standard JSON constant: {value}")
        ),
    )


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def validate(package: Path = PACKAGE) -> dict[str, Any]:
    manifest = strict_load(package / "manifest.json")
    if manifest["schema_version"] != "cpi-p9b-fee-authority-v1":
        raise ValueError("unsupported authority schema")
    artifacts = manifest["artifacts"]
    seen: set[str] = set()
    exact_artifact_ids: set[str] = set()
    for artifact in artifacts:
        ident = artifact["authority_identity"]
        if ident in seen:
            raise ValueError("duplicate authority identity")
        seen.add(ident)
        if artifact.get("path") is not None:
            path = package / artifact["path"]
            if not path.is_file():
                raise ValueError(f"missing retained artifact: {artifact['path']}")
            actual = digest(path)
            if actual != artifact["sha256"]:
                raise ValueError(f"artifact hash mismatch: {artifact['path']}")
        else:
            actual = None
        if artifact["status"] == "exact" and artifact["authority_identity"] != actual:
            raise ValueError("exact authority identity is not bound to artifact hash")
        if artifact["status"] == "exact":
            exact_artifact_ids.add(artifact["authority_identity"])
        if artifact["status"] == "locator_only" and artifact.get("path") is not None:
            raise ValueError("locator-only evidence cannot retain an exact artifact path")

    excluded = manifest["excluded_authorities"]
    if any(
        row["filing"] == "61349" and row["authority_status"] == "general_kxcpi" for row in excluded
    ):
        raise ValueError("excluded CFTC 61349 was substituted into general authority")

    timelines = manifest["timelines"]
    for kind in ("taker", "maker"):
        rows = timelines[kind]
        previous: datetime | None = None
        for row in rows:
            start = None if row["start"] is None else ts(row["start"])
            end = None if row["end"] is None else ts(row["end"])
            if start is not None and end is not None and start >= end:
                raise ValueError(f"invalid {kind} interval")
            if previous != start:
                raise ValueError(f"gap or overlap in {kind} timeline")
            previous = end
        if previous is not None:
            raise ValueError(f"{kind} timeline does not end open")
    exact = [r for r in timelines["taker"] if r["status"] == "exact"]
    for row in exact:
        if row["formula"] != "round_up(0.07 * C * P * (1-P))" or row["rounding"] != "next_cent":
            raise ValueError("taker formula or rounding identity mismatch")
        if row["authority_type"] != "taker" or row["kxcpi_applicability"] != "general":
            raise ValueError("maker/taker or KXCPI applicability substitution")
        if row["authority_identity"] not in exact_artifact_ids:
            raise ValueError("exact authority identity lacks an approved exact artifact")

    p9a = strict_load(P9A)
    by_event: dict[str, list[dict[str, Any]]] = {}
    for market in p9a["markets"]:
        by_event.setdefault(market["event_ticker"], []).append(market)
    coverage = []
    for event, markets in sorted(by_event.items()):
        decision = min(ts(m["market_close"]) for m in markets)
        applicable = [
            r
            for r in timelines["taker"]
            if (r["start"] is None or decision >= ts(r["start"]))
            and (r["end"] is None or decision < ts(r["end"]))
        ]
        if len(applicable) != 1:
            raise ValueError(f"unmapped or conflicting taker regime for {event}")
        regime = applicable[0]
        coverage.append(
            {
                "event_ticker": event,
                "decision_timestamp": decision.isoformat().replace("+00:00", "Z"),
                "status": regime["status"],
                "authority_id": regime["authority_identity"],
            }
        )

    p8_events = set(manifest["p8_reference_events"])
    intersection = sorted(
        row["event_ticker"]
        for row in coverage
        if row["event_ticker"] in p8_events and row["status"] == "exact"
    )
    result = {
        "p9a_events": coverage,
        "counts": {
            status: sum(row["status"] == status for row in coverage)
            for status in ("exact", "locator_only", "unknown")
        },
        "p8_p9a_exact_taker_intersection": intersection,
        "intersection_count": len(intersection),
    }
    (package / "event_coverage.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, default=PACKAGE)
    args = parser.parse_args()
    print(json.dumps(validate(args.package), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
