#!/usr/bin/env python3
"""Read-only fail-closed P9B.4R validator."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from services.forecasting.cpi_p9b_authority import (
    APPROVED_ARTIFACTS,
    APPROVED_RECEIPT_SHA256,
    AUTHORITY_METADATA,
    CANONICAL_BASE,
    CANONICAL_TREE,
    P8_AUTHORITY_ARTIFACT_SHA256,
    P8_REFERENCE_EVENTS,
    P9A_ACQUISITION_SHA256,
    P9A_APPROVED_ACQUISITION_DIGEST,
    P9A_EVENT_COUNT,
    P9A_FINAL_MANIFEST_SHA256,
    P9A_MANIFEST_SHA256,
    P9A_MARKET_COUNT,
    approved_receipt_digest,
)
from services.historical_replay.cpi_price_evidence import validate_frozen_cohort

ROOT = Path(__file__).parents[1]
PACKAGE = ROOT / "evidence/cpi_p9b_fee_authority"
P9A = ROOT / "evidence/cpi_p9a_historical_price"


def load(path: Path) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in items:
            if key in out:
                raise ValueError(f"duplicate JSON key: {key}")
            out[key] = value
        return out

    return json.loads(
        path.read_bytes(),
        object_pairs_hook=pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"non-standard JSON constant: {value}")
        ),
    )


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def authority_date(value: str) -> datetime.date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def p9a() -> dict[str, Any]:
    raw = P9A / "manifest.json"
    manifest = load(raw)
    if (
        digest(raw) != P9A_MANIFEST_SHA256
        or manifest.get("final_manifest_sha256") != P9A_FINAL_MANIFEST_SHA256
    ):
        raise ValueError("approved P9A manifest identity mismatch")
    receipt = P9A / "acquisition_manifest.json"
    if (
        digest(receipt) != P9A_ACQUISITION_SHA256
        or manifest.get("approved_acquisition_manifest_sha256") != P9A_APPROVED_ACQUISITION_DIGEST
    ):
        raise ValueError("approved P9A acquisition receipt identity mismatch")
    if (
        len(manifest.get("markets", [])) != P9A_MARKET_COUNT
        or len(manifest.get("events", [])) != P9A_EVENT_COUNT
    ):
        raise ValueError("P9A multiplicity mismatch")
    validate_frozen_cohort(P9A)
    return manifest


def _validate(package: Path = PACKAGE, *, require_frozen_coverage: bool = True) -> dict[str, Any]:
    if approved_receipt_digest() != APPROVED_RECEIPT_SHA256:
        raise ValueError("reviewed authority receipt code digest mismatch")
    manifest = load(package / "manifest.json")
    if (manifest.get("canonical_base"), manifest.get("canonical_tree")) != (
        CANONICAL_BASE,
        CANONICAL_TREE,
    ):
        raise ValueError("canonical identity mismatch")
    if (
        manifest.get("p9a_manifest_sha256") != P9A_MANIFEST_SHA256
        or manifest.get("p8_authority_sha256") != P8_AUTHORITY_ARTIFACT_SHA256
    ):
        raise ValueError("canonical input identity was changed")
    if manifest.get("authority_receipt_sha256") != APPROVED_RECEIPT_SHA256:
        raise ValueError("manifest authority receipt identity mismatch")
    if manifest.get("schema_version") != "cpi-p9b-fee-authority-v1":
        raise ValueError("unsupported authority manifest schema")
    if manifest.get("authority_metadata") != list(AUTHORITY_METADATA):
        raise ValueError("authority metadata identity mismatch")
    expected = [dict(row) for row in APPROVED_ARTIFACTS]
    actual = [{key: row.get(key) for key in expected[0]} for row in manifest.get("artifacts", [])]
    if actual != expected:
        raise ValueError("manifest artifact set is not the reviewed approved set")
    for row in APPROVED_ARTIFACTS:
        path = package / row["path"]
        if (
            not path.is_file()
            or path.stat().st_size != row["bytes"]
            or digest(path) != row["sha256"]
        ):
            raise ValueError(f"artifact path, byte count, or hash mismatch: {row['path']}")
    timeline = load(package / "authority_timeline.json")
    if timeline.get("schema_version") != "cpi-p9b-authority-timeline-v1":
        raise ValueError("unsupported authority timeline schema")
    if {kind: timeline[kind] for kind in ("taker", "maker")} != manifest.get("timelines"):
        raise ValueError("authority timeline content mismatch")
    for kind in ("taker", "maker"):
        previous = None
        for row in timeline[kind]:
            start = None if row["start_date"] is None else authority_date(row["start_date"])
            end = None if row["end_date"] is None else authority_date(row["end_date"])
            if start != previous:
                raise ValueError(f"gap or overlap in {kind} timeline")
            if start is not None and end is not None and start >= end:
                raise ValueError(f"invalid {kind} interval")
            previous = end
        if previous is not None:
            raise ValueError(f"{kind} timeline does not end open")
    source = p9a()
    if set(manifest.get("p8_reference_events", [])) != P8_REFERENCE_EVENTS:
        raise ValueError("mutable P8 event list does not match reviewed authority")
    coverage: list[dict[str, Any]] = []
    event_rows: dict[str, list[dict[str, Any]]] = {}
    market_ids: set[str] = set()
    for market in source["markets"]:
        if market["market_ticker"] in market_ids:
            raise ValueError("duplicate market coverage")
        market_ids.add(market["market_ticker"])
        quote = datetime.fromtimestamp(market["candle_end_period_ts"], UTC)
        matches = [
            row
            for row in timeline["taker"]
            if (row["start_date"] is None or quote.date() >= authority_date(row["start_date"]))
            and (row["end_date"] is None or quote.date() < authority_date(row["end_date"]))
        ]
        if len(matches) != 1:
            raise ValueError("quote timestamp has conflicting or unexplained regime")
        regime = matches[0]
        row = {
            "event_ticker": market["event_ticker"],
            "market_ticker": market["market_ticker"],
            "selected_quote_timestamp": quote.isoformat().replace("+00:00", "Z"),
            "status": regime["status"],
            "authority_chain_identity": APPROVED_RECEIPT_SHA256,
            "authority_identity": regime["authority_identity"],
            "formula": regime["formula"] if regime["status"] == "exact" else None,
            "rounding": regime["rounding"] if regime["status"] == "exact" else None,
            "reason": None
            if regime["status"] == "exact"
            else regime.get("notes", regime["status"]),
        }
        coverage.append(row)
        event_rows.setdefault(market["event_ticker"], []).append(row)
    events = []
    for event, rows in sorted(event_rows.items()):
        statuses = sorted({row["status"] for row in rows})
        events.append(
            {
                "event_ticker": event,
                "market_count": len(rows),
                "status": "exact"
                if statuses == ["exact"]
                else "mixed_authority"
                if len(statuses) > 1
                else statuses[0],
                "statuses": statuses,
            }
        )
    intersection = sorted(
        row["event_ticker"]
        for row in events
        if row["event_ticker"] in P8_REFERENCE_EVENTS and row["status"] == "exact"
    )
    result = {
        "schema_version": "cpi-p9b-event-coverage-v2",
        "market_rows": coverage,
        "events": events,
        "counts": {
            s: sum(row["status"] == s for row in coverage)
            for s in ("exact", "locator_only", "unknown", "mixed_authority")
        },
        "event_counts": {
            s: sum(row["status"] == s for row in events)
            for s in ("exact", "locator_only", "unknown", "mixed_authority")
        },
        "p8_reference_events": sorted(P8_REFERENCE_EVENTS),
        "p8_p9a_exact_taker_intersection": intersection,
        "intersection_count": len(intersection),
        "exact_taker_p8_usable_quote_rows": sum(
            row["event_ticker"] in P8_REFERENCE_EVENTS and row["status"] == "exact"
            for row in coverage
        ),
        "boundary_observations": [
            row
            for row in coverage
            if row["selected_quote_timestamp"][:10]
            in {"2022-09-22", "2022-09-23", "2022-09-24", "2025-05-05", "2025-05-06"}
        ],
    }
    if (
        require_frozen_coverage
        and (package / "event_coverage.json").read_bytes()
        != (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()
    ):
        raise ValueError("event coverage content mismatch")
    return result


def validate(package: Path = PACKAGE) -> dict[str, Any]:
    """Validate without writing any tracked evidence."""
    return _validate(package)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, default=PACKAGE)
    print(json.dumps(validate(parser.parse_args().package), indent=2, sort_keys=True))
