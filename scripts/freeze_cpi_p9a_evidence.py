#!/usr/bin/env python3
"""Freeze the already-collected P9A runtime cohort without any network access."""

from __future__ import annotations

import argparse
import json
import shutil
from hashlib import sha256
from pathlib import Path
from typing import cast

from services.contract_intelligence.specification import (
    ContractSpecificationParser,
    SemanticsInputBundle,
)
from services.historical_replay.archive import stable_hash
from services.historical_replay.cpi_price_evidence import (
    KXCPI_INVENTORY_CURSOR,
    KXCPI_INVENTORY_PATH,
    PROVENANCE_MODE,
    SCHEMA_VERSION,
    canonical_candle_request,
    strict_json_loads,
    validate_candle_payload,
)

CANONICAL_BASE = "7aa43ea605fb44bc7db2572385bc61382ad5d5e1"
RUNTIME_HASH = "d671ef2cda78a8e1a720126a73fed4e0228afc69bd72c86878bdcd5acbfc6699"
PUBLIC_ORIGIN = "https://external-api.kalshi.com"


def freeze(runtime: Path, destination: Path, inventory_path: Path) -> dict[str, object]:
    source_manifest = strict_json_loads((runtime / "manifest.json").read_bytes())
    original = source_manifest.copy()
    original_hash = original.pop("manifest_sha256")
    if original_hash != RUNTIME_HASH or stable_hash(original) != RUNTIME_HASH:
        raise ValueError("runtime manifest does not match the approved P9A hash")
    inventory_raw = inventory_path.read_bytes()
    inventory_hash = sha256(inventory_raw).hexdigest()
    if inventory_hash != source_manifest["market_inventory"]["sha256"]:
        raise ValueError("market inventory does not match the original runtime hash")
    inventory = strict_json_loads(inventory_raw)
    inventory_rows = inventory.get("markets")
    if not isinstance(inventory_rows, list) or len(inventory_rows) != 474:
        raise ValueError("market inventory is not the original complete cohort")
    inventory_by_ticker = {row["ticker"]: row for row in inventory_rows}
    destination.mkdir(parents=True, exist_ok=True)
    inventory_destination = destination / "market_inventory.json"
    if inventory_path.resolve() != inventory_destination.resolve():
        shutil.copyfile(inventory_path, inventory_destination)
    raw_destination = destination / "raw"
    raw_destination.mkdir(exist_ok=True)
    frozen_rows = []
    for source_row in sorted(source_manifest["markets"], key=lambda row: row["market_ticker"]):
        raw_name = Path(source_row["raw_artifact"]).name
        source_raw = runtime / source_row["raw_artifact"]
        destination_raw = raw_destination / raw_name
        shutil.copyfile(source_raw, destination_raw)
        payload = strict_json_loads(source_raw.read_bytes())
        inventory_row = inventory_by_ticker.get(source_row["market_ticker"])
        if inventory_row is None:
            raise ValueError("runtime market is absent from inventory")
        request = canonical_candle_request(inventory_row)
        candles = validate_candle_payload(
            payload,
            market_ticker=inventory_row["ticker"],
            request_start_ts=request.start_ts,
            request_end_ts=request.end_ts,
            period_interval_minutes=request.period_interval_minutes,
        )
        selected_end = source_row["candle_end_period_ts"]
        selected = next(
            (candle for candle in candles if candle.get("end_period_ts") == selected_end),
            None,
        )
        semantics = ContractSpecificationParser().parse(
            SemanticsInputBundle.build(
                inventory_row,
                {"event_ticker": inventory_row["event_ticker"], "series_ticker": "KXCPI"},
                {"ticker": "KXCPI", "category": "Economics"},
            )
        )
        if semantics.comparator.value != ">" or semantics.threshold_value is None:
            raise ValueError(f"unresolved CPI semantics for {source_row['market_ticker']}")
        row = dict(source_row)
        row.pop("historical_total_volume", None)
        row.update(
            {
                "schema_version": SCHEMA_VERSION,
                "canonical_base": CANONICAL_BASE,
                "provenance_mode": PROVENANCE_MODE,
                "endpoint_source_role": "KALSHI_PUBLIC_HISTORICAL_CANDLESTICKS",
                "request_path": request.path,
                "request_url": request.url,
                "request_start_ts": request.start_ts,
                "request_end_ts": request.end_ts,
                "period_interval_minutes": request.period_interval_minutes,
                "request_identity": request.request_identity,
                "market_row_hash": stable_hash(inventory_row),
                "comparator": semantics.comparator.name,
                "comparator_symbol": semantics.comparator.value,
                "threshold": str(semantics.threshold_value),
                "payout_model": semantics.payout_model.value,
                "semantic_hash": semantics.semantic_hash,
                "semantic_parser_version": ContractSpecificationParser.version,
                "selected_candle_hash": None if selected is None else stable_hash(selected),
                "raw_artifact": f"raw/{raw_name}",
                "raw_artifact_sha256": sha256(source_raw.read_bytes()).hexdigest(),
                "retrospective_retrieval": True,
                "actual_bot_ingest_at": None,
                "prospective_observation": False,
                "retrospective_full_lifecycle_volume": inventory_row.get("volume_fp", "0"),
                "point_in_time_feature_eligible": False,
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
        "market_inventory_artifact": "market_inventory.json",
        "series_ticker": "KXCPI",
        "series_membership_invariant": "INVENTORY_RESPONSE_FILTERED_BY_SERIES_TICKER_KXCPI",
        "market_inventory_request": {
            "path": KXCPI_INVENTORY_PATH,
            "cursor": KXCPI_INVENTORY_CURSOR,
            "cursor_exhausted": True,
            "response_sha256": inventory_hash,
        },
        "market_inventory_sha256": inventory_hash,
        "market_inventory_bytes": len(inventory_raw),
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
    parser.add_argument(
        "--inventory",
        type=Path,
        default=Path("evidence/cpi_p9a_historical_price/market_inventory.json"),
    )
    args = parser.parse_args()
    result = freeze(args.runtime, args.destination, args.inventory)
    print(
        json.dumps(
            {
                "events": len(cast(list[object], result["events"])),
                "siblings": len(cast(list[object], result["markets"])),
                "final_manifest_sha256": result["final_manifest_sha256"],
            }
        )
    )
