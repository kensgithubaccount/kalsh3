"""Deterministic M27C archive coverage replay using the production parser.

This is replay/coverage evidence, not independent semantic validation, and it cannot
independently establish a false-negative exclusion rate.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

from services.forecasting.daily_temperature import (
    DailyTemperatureRouteState,
    route_daily_temperature,
)
from services.market_universe.domain import Event, Market


def replay(path: Path) -> dict[str, object]:
    """Measure parser coverage over the latest archived Markets and parent Events."""
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        rows = db.execute(
            "SELECT entity_kind,ticker,canonical_source FROM ("
            "SELECT entity_kind,ticker,canonical_source,ROW_NUMBER() OVER ("
            "PARTITION BY entity_kind,ticker ORDER BY acquired_at DESC,observation_id) AS rank "
            "FROM entity_observations WHERE entity_kind IN ('market','event')) WHERE rank=1"
        ).fetchall()
    events = {
        ticker: Event.parse(json.loads(source)) for kind, ticker, source in rows if kind == "event"
    }
    markets = [Market.parse(json.loads(source)) for kind, _, source in rows if kind == "market"]
    counts: Counter[str] = Counter()
    identifiers: set[str] = set()
    sources: set[str] = set()
    malformed_candidates = 0
    for market in markets:
        event = events.get(market.event_ticker)
        if event is None:
            counts["ABSTAIN"] += 1
            continue
        route = route_daily_temperature(market, event)
        counts[route.state.value] += 1
        if route.state is not DailyTemperatureRouteState.SUPPORTED:
            if route.reason is not None and route.reason.value != "NOT_DAILY_TEMPERATURE":
                malformed_candidates += 1
            continue
        if route.contract is None or route.production_influence != 0:
            raise RuntimeError("supported route violated M27C safety invariants")
        counts[route.contract.measurement] += 1
        counts[market.raw["strike_type"]] += 1
        counts[route.contract.unit] += 1
        identifiers.add(route.contract.station_id)
        sources.add(route.contract.settlement_source)
    return {
        "total_markets_evaluated": len(markets),
        "supported": counts["SUPPORTED"],
        "daily_max": counts["DAILY_MAX"],
        "daily_min": counts["DAILY_MIN"],
        "between": counts["between"],
        "greater": counts["greater"],
        "less": counts["less"],
        "unique_cli_identifiers": len(identifiers),
        "settlement_sources": sorted(sources),
        "degF": counts["degF"],
        "malformed_recognized_candidates": malformed_candidates,
        "production_influence": "0",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay M27C daily-temperature authority offline")
    parser.add_argument("archive", type=Path, help="operator-supplied M26F SQLite archive")
    print(json.dumps(replay(parser.parse_args().archive), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
