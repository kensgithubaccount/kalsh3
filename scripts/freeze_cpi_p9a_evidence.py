#!/usr/bin/env python3
"""Freeze the already-collected P9A runtime cohort without any network access."""

from __future__ import annotations

import argparse
import json
import shutil
from hashlib import sha256
from pathlib import Path

from services.historical_replay.archive import stable_hash
from services.historical_replay.cpi_price_evidence import PROVENANCE_MODE, SCHEMA_VERSION

CANONICAL_BASE = "7aa43ea605fb44bc7db2572385bc61382ad5d5e1"
RUNTIME_HASH = "d671ef2cda78a8e1a720126a73fed4e0228afc69bd72c86878bdcd5acbfc6699"
PUBLIC_ORIGIN = "https://external-api.kalshi.com"


def freeze(runtime: Path, destination: Path) -> dict[str, object]:
    source_manifest = json.loads((runtime / "manifest.json").read_text())
    original = source_manifest.copy()
    original_hash = original.pop("manifest_sha256")
    if original_hash != RUNTIME_HASH or stable_hash(original) != RUNTIME_HASH:
        raise ValueError("runtime manifest does not match the approved P9A hash")
    destination.mkdir(parents=True, exist_ok=True)
    raw_destination = destination / "raw"
    raw_destination.mkdir(exist_ok=True)
    frozen_rows = []
    for source_row in sorted(source_manifest["markets"], key=lambda row: row["market_ticker"]):
        raw_name = Path(source_row["raw_artifact"]).name
        source_raw = runtime / source_row["raw_artifact"]
        destination_raw = raw_destination / raw_name
        shutil.copyfile(source_raw, destination_raw)
        payload = json.loads(source_raw.read_text())
        candles = payload["candlesticks"]
        selected_end = source_row["candle_end_period_ts"]
        selected = next(
            (candle for candle in candles if candle.get("end_period_ts") == selected_end),
            None,
        )
        request_identity = stable_hash(
            (
                source_row["request_path"],
                source_row["request_start_ts"],
                source_row["request_end_ts"],
                60,
            )
        )
        row = dict(source_row)
        row.update(
            {
                "schema_version": SCHEMA_VERSION,
                "canonical_base": CANONICAL_BASE,
                "provenance_mode": PROVENANCE_MODE,
                "endpoint_source_role": "KALSHI_PUBLIC_HISTORICAL_CANDLESTICKS",
                "request_url": PUBLIC_ORIGIN + source_row["request_path"],
                "request_identity": request_identity,
                "selected_candle_hash": None if selected is None else stable_hash(selected),
                "raw_artifact": f"raw/{raw_name}",
                "raw_artifact_sha256": sha256(source_raw.read_bytes()).hexdigest(),
                "retrospective_retrieval": True,
                "actual_bot_ingest_at": None,
                "prospective_observation": False,
            }
        )
        frozen_rows.append(row)
    frozen = {
        "schema_version": SCHEMA_VERSION,
        "policy_version": "cpi-e1-p9a-retrospective-public-historical-v1",
        "canonical_base": CANONICAL_BASE,
        "provenance_mode": PROVENANCE_MODE,
        "endpoint_source_role": "KALSHI_PUBLIC_HISTORICAL_CANDLESTICKS",
        "retrospective_retrieval": True,
        "actual_bot_ingest_at": None,
        "prospective_observation": False,
        "research_only": True,
        "production_influence": "0",
        "event_target": 60,
        "market_target": 474,
        "original_runtime_manifest_sha256": RUNTIME_HASH,
        "events": source_manifest["events"],
        "markets": frozen_rows,
    }
    frozen["final_manifest_sha256"] = stable_hash(frozen)
    (destination / "manifest.json").write_text(json.dumps(frozen, sort_keys=True, indent=2) + "\n")
    return frozen


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, default=Path("state/cpi_p9a_price_evidence"))
    parser.add_argument(
        "--destination", type=Path, default=Path("evidence/cpi_p9a_historical_price")
    )
    args = parser.parse_args()
    result = freeze(args.runtime, args.destination)
    print(
        json.dumps(
            {
                "events": len(result["events"]),
                "siblings": len(result["markets"]),
                "final_manifest_sha256": result["final_manifest_sha256"],
            }
        )
    )
