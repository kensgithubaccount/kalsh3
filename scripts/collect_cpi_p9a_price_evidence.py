#!/usr/bin/env python3
"""Collect all public KXCPI sibling candle evidence; no auth, writes only research state."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from services.historical_replay.archive import stable_hash
from services.historical_replay.cpi_price_evidence import SCHEMA_VERSION, build_price_evidence
from services.market_universe import public_read

SERIES = "KXCPI"
INTERVAL = 60


def _request(path: str) -> tuple[bytes, datetime]:
    for attempt in range(8):
        body, status, observed = public_read._get_raw(path)  # reviewed fixed-origin GET
        if status == 200:
            return body, observed
        if status == 429:
            time.sleep(min(60, 2**attempt))
            continue
        raise RuntimeError(f"public Kalshi request failed: HTTP {status}")
    raise RuntimeError("public Kalshi request remained rate-limited")


def collect(output: Path) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=True)
    market_path = f"{public_read.BASE}/historical/markets?limit=1000&series_ticker={SERIES}"
    market_raw, market_observed = _request(market_path)
    market_payload = json.loads(market_raw)
    markets = market_payload.get("markets")
    if not isinstance(markets, list) or any(not isinstance(row, dict) for row in markets):
        raise RuntimeError("KXCPI market inventory response malformed")
    if len(markets) != 474:
        raise RuntimeError(f"expected 474 KXCPI siblings, received {len(markets)}")
    rows: list[dict[str, object]] = []
    events: defaultdict[str, list[str]] = defaultdict(list)
    for market in markets:
        event = str(market["event_ticker"])
        ticker = str(market["ticker"])
        close = int(
            datetime.fromisoformat(str(market["close_time"]).replace("Z", "+00:00")).timestamp()
        )
        opened = int(
            datetime.fromisoformat(str(market["open_time"]).replace("Z", "+00:00")).timestamp()
        )
        start = max(opened, close - int(timedelta(days=90).total_seconds()))
        path = (
            f"{public_read.BASE}/historical/markets/{quote(ticker, safe='')}/candlesticks"
            f"?start_ts={start}&end_ts={close}&period_interval={INTERVAL}"
        )
        raw, observed = _request(path)
        payload = json.loads(raw)
        candles = payload.get("candlesticks")
        if not isinstance(candles, list) or any(not isinstance(candle, dict) for candle in candles):
            raise RuntimeError(f"malformed candles for {ticker}")
        evidence = build_price_evidence(
            market,
            request_path=path,
            request_start_ts=start,
            request_end_ts=close,
            raw_body=raw,
            retrieved_at=observed,
            candles=candles,
        )
        raw_file = output / "raw" / f"{ticker}.json"
        raw_file.parent.mkdir(exist_ok=True)
        raw_file.write_bytes(raw)
        row = {
            **{field: getattr(evidence, field) for field in evidence.__dataclass_fields__},
            "threshold": str(evidence.threshold),
            "market_open": evidence.market_open.isoformat(),
            "market_close": evidence.market_close.isoformat(),
            "retrieved_at": evidence.retrieved_at.isoformat(),
            "candle_volume": (
                None if evidence.candle_volume is None else str(evidence.candle_volume)
            ),
            "historical_total_volume": str(evidence.historical_total_volume),
            "raw_artifact": str(raw_file.relative_to(output)),
            "evidence_id": evidence.evidence_id,
        }
        row["production_influence"] = str(evidence.production_influence)
        for field in ("yes_bid", "yes_ask", "no_bid", "no_ask"):
            row[field] = None if getattr(evidence, field) is None else str(getattr(evidence, field))
        rows.append(row)
        events[event].append(ticker)
        time.sleep(0.15)
    event_rows = [
        {
            "event_ticker": event,
            "underlying_event_id": f"kalshi:{event}",
            "intended_siblings": len(tickers),
            "persisted_siblings": len(tickers),
            "complete": len(tickers) > 0
            and all(
                row["event_ticker"] == event for row in rows if row["market_ticker"] in tickers
            ),
        }
        for event, tickers in sorted(events.items())
    ]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "series_ticker": SERIES,
        "research_only": True,
        "production_influence": "0",
        "market_target": 474,
        "event_target": 60,
        "persisted_market_count": len(rows),
        "persisted_event_count": len(event_rows),
        "market_inventory": {
            "path": market_path,
            "sha256": hashlib.sha256(market_raw).hexdigest(),
            "observed_at": market_observed.isoformat(),
        },
        "markets": sorted(rows, key=lambda row: str(row["market_ticker"])),
        "events": event_rows,
    }
    manifest["manifest_sha256"] = stable_hash(manifest)
    (output / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    summary = Counter(
        "BOTH_USABLE_SIDES"
        if row["yes_ask"] not in (None, "1.0000") and row["yes_bid"] not in (None, "0.0000")
        else "MISSING_USABLE_SIDE"
        for row in rows
    )
    print(
        json.dumps(
            {"events": len(event_rows), "siblings": len(rows), "side_distribution": summary},
            sort_keys=True,
        )
    )
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("state/cpi_p9a_price_evidence"))
    collect(parser.parse_args().output)
