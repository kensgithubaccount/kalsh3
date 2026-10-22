#!/usr/bin/env python3
"""Research-only raw Kalshi ladder/AAA capture. No authenticated trading endpoints."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.parse import urlencode

API = "https://api.elections.kalshi.com/trade-api/v2"
AAA = "https://gasprices.aaa.com/"
MAX_BYTES = 8_000_000

def utc():
    return datetime.now(timezone.utc).isoformat()

def get(url):
    start = utc()
    request = Request(url, headers={"User-Agent": "kalsh3-research-aaa-capture/1.0", "Accept": "application/json,text/html"})
    with urlopen(request, timeout=20) as response:
        body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError("oversize HTTP body")
        headers = dict(response.headers.items())
        status = response.status
        final_url = response.geturl()
    end = utc()
    if status != 200 or final_url != url:
        raise ValueError("unexpected HTTP status or redirect")
    return {"requested_at_utc": start, "received_at_utc": end, "url": url,
            "status": status, "headers": headers, "sha256": hashlib.sha256(body).hexdigest(),
            "byte_count": len(body)}, body

def fetch_json(url):
    meta, raw = get(url)
    return meta, raw, json.loads(raw)

def all_markets(series):
    cursor = None
    seen_cursors = set()
    markets = {}
    pages = []
    while True:
        params = {"series_ticker": series, "limit": "1000"}
        if cursor:
            params["cursor"] = cursor
        url = API + "/markets?" + urlencode(params)
        meta, raw, data = fetch_json(url)
        pages.append((meta, raw))
        for market in data.get("markets", []):
            ticker = market["ticker"]
            if ticker in markets:
                raise ValueError("duplicate ticker in paginated discovery")
            markets[ticker] = market
        nxt = data.get("cursor")
        if not nxt:
            break
        if nxt in seen_cursors:
            raise ValueError("repeating cursor")
        seen_cursors.add(nxt)
        cursor = nxt
        if len(pages) > 100:
            raise ValueError("too many pages")
    return markets, pages

def write_snapshot(root, entries, manifest):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    # Exclusive directory avoids silently overwriting a prior capture.
    directory = root / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + os.urandom(4).hex())
    directory.mkdir()
    for name, meta, body in entries:
        (directory / name).write_bytes(body)
        manifest["artifacts"].append({"path": name, **meta})
    path = directory / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return directory

def capture(root, series, event_prefix):
    manifest = {"record_type": "AAA_GAS_RESEARCH_SNAPSHOT_V1", "research_only": True,
                "production_influence": False, "trading_authority": False,
                "started_at_utc": utc(), "series": series, "event_prefix": event_prefix,
                "artifacts": [], "errors": [], "complete": False,
                "fee_schedule": {"status": "UNVERIFIED", "source_url": "https://kalshi.com/fee-schedule",
                                 "fee_multiplier": None, "schedule_version": None}}
    entries = []
    try:
        aaa_meta, aaa_body = get(AAA)
        entries.append(("aaa.html", aaa_meta, aaa_body))
        markets, pages = all_markets(series)
        for i, (meta, raw) in enumerate(pages):
            entries.append((f"discovery_{i:03d}.json", meta, raw))
        selected = {t: m for t, m in markets.items() if t.startswith(event_prefix)}
        if not selected:
            raise ValueError("no event tickers discovered")
        for i, ticker in enumerate(sorted(selected)):
            meta, raw, _ = fetch_json(API + "/markets/" + ticker)
            entries.append((f"market_{i:03d}.json", meta, raw))
            bookmeta, bookraw, _ = fetch_json(API + "/markets/" + ticker + "/orderbook")
            entries.append((f"book_{i:03d}.json", bookmeta, bookraw))
            manifest.setdefault("tickers", []).append({"ticker": ticker, "market_file": f"market_{i:03d}.json",
                                                         "book_file": f"book_{i:03d}.json"})
        manifest["complete"] = True
    except Exception as exc:
        manifest["errors"].append(type(exc).__name__ + ": " + str(exc))
    manifest["finished_at_utc"] = utc()
    directory = write_snapshot(root, entries, manifest)
    print(json.dumps({"snapshot": str(directory), "complete": manifest["complete"],
                      "tickers": len(manifest.get("tickers", [])), "errors": manifest["errors"]}))
    return 0 if manifest["complete"] else 4

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--series", default="KXAAAGASD")
    parser.add_argument("--event-prefix", required=True, help="Exact event ticker prefix, e.g. KXAAAGASD-26OCT23-")
    args = parser.parse_args()
    raise SystemExit(capture(args.output, args.series, args.event_prefix))
