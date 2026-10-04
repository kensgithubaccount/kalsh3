"""Public structural market preflight; never request outcomes or forecasts."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parent
PROTOCOL = json.loads((ROOT / "protocol.json").read_text())
BASE = "https://api.elections.kalshi.com/trade-api/v2"
TARGET = "2026-10-02"
EVENT_SUFFIX = "26OCT02"


def get(path: str) -> dict[str, Any]:
    if not path.startswith(("/events/", "/markets/")) or ".." in path:
        raise RuntimeError("INVALID_PUBLIC_MARKET_PATH")
    request = urllib.request.Request(  # noqa: S310
        BASE + path, headers={"Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
        return cast(dict[str, Any], json.load(response))


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def main() -> None:
    observed = datetime.now(UTC)
    candidate = datetime(2026, 10, 2, 9, tzinfo=UTC)
    if observed >= candidate:
        raise RuntimeError("STRUCTURAL_PREFLIGHT_EXPIRED_NO_QUERY")
    results = {}
    for city in PROTOCOL["cities"]:
        ticker = city["series_ticker"] + "-" + EVENT_SUFFIX
        event = get("/events/" + ticker + "?with_nested_markets=true")["event"]
        markets = event["markets"]
        if any(
            market.get("result") not in (None, "")
            or market.get("expiration_value") not in (None, "")
            for market in markets
        ):
            raise RuntimeError("OUTCOME_PRESENT_STOP_PREFLIGHT")
        tickers = [market["ticker"] for market in markets]
        exact_identity = (
            event["event_ticker"] == ticker and event["series_ticker"] == city["series_ticker"]
        )
        source_identity = event.get("settlement_sources") == [
            {"name": "The Weather Company", "url": "https://weather.com/kalshi"}
        ]
        sibling_complete = (
            len(markets) == 6
            and len(set(tickers)) == 6
            and all(value.startswith(ticker + "-") for value in tickers)
        )
        shape = sorted(market.get("strike_type") for market in markets) == ["between"] * 4 + [
            "greater",
            "less",
        ]
        rules = all(
            f"at {city['city']} ({city['cli_product']}) for Oct 2, 2026"
            in market.get("rules_primary", "")
            and "according to The Weather Company" in market.get("rules_primary", "")
            for market in markets
        )
        bounds = all(
            (market.get("floor_strike") is not None and market.get("cap_strike") is not None)
            if market.get("strike_type") == "between"
            else (
                market.get("cap_strike") is not None
                if market.get("strike_type") == "less"
                else market.get("floor_strike") is not None
            )
            for market in markets
        )
        quotes = all(
            all(
                key in market
                for key in (
                    "yes_bid_dollars",
                    "yes_ask_dollars",
                    "yes_bid_size_fp",
                    "yes_ask_size_fp",
                )
            )
            for market in markets
        )
        open_times = sorted({market["open_time"] for market in markets})
        close_times = sorted({market["close_time"] for market in markets})
        scheduled_active = all(
            parse(market["open_time"]) <= candidate < parse(market["close_time"])
            for market in markets
        )
        active_now = all(market["status"] == "active" for market in markets)
        depth = []
        for market in markets:
            book = get(
                "/markets/" + urllib.parse.quote(market["ticker"], safe="") + "/orderbook?depth=5"
            )["orderbook_fp"]
            depth.append(
                {
                    "ticker": market["ticker"],
                    "yes_levels": len(book.get("yes_dollars", [])),
                    "no_levels": len(book.get("no_dollars", [])),
                    "present": bool(book.get("yes_dollars")) and bool(book.get("no_dollars")),
                }
            )
        results[city["city_id"]] = {
            "event_ticker": ticker,
            "exact_identity": exact_identity,
            "source_identity": source_identity,
            "sibling_complete": sibling_complete,
            "strike_shape_valid": shape,
            "rule_cli_identity_valid": rules,
            "structured_bounds_available": bounds,
            "all_sibling_bid_ask_fields_available": quotes,
            "all_sibling_public_depth_endpoint_available": all(row["present"] for row in depth),
            "depth_level_counts": depth,
            "all_siblings_active_at_observation": active_now,
            "candidate_0900_within_scheduled_open_close": scheduled_active,
            "open_times_utc": open_times,
            "close_times_utc": close_times,
        }
    result = {
        "record_type": "WN-MULTICITY-D1-MARKET-STRUCTURE-PREFLIGHT-v1",
        "observed_at_utc": observed.isoformat().replace("+00:00", "Z"),
        "sample_target_date": TARGET,
        "observational_unit": "CITY_DAY",
        "city_days_independent": False,
        "same_date_cluster": TARGET,
        "correlation_groups_by_city": {
            city["city_id"]: {
                "geographic_group": city["geographic_group"],
                "weather_regime_group": city["weather_regime_group"],
            }
            for city in PROTOCOL["cities"]
        },
        "candidate_decision_at_utc": candidate.isoformat().replace("+00:00", "Z"),
        "limitation": (
            "Public structural snapshot and posted open/close times; future 09:00 "
            "quote liquidity and active state remain runtime gates. "
            "No outcome or forecast requested."
        ),
        "cities": results,
    }
    (ROOT / "market_timing_preflight.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                city: {
                    key: value
                    for key, value in row.items()
                    if key
                    in (
                        "source_identity",
                        "sibling_complete",
                        "strike_shape_valid",
                        "rule_cli_identity_valid",
                        "structured_bounds_available",
                        "all_sibling_bid_ask_fields_available",
                        "all_sibling_public_depth_endpoint_available",
                        "all_siblings_active_at_observation",
                        "candidate_0900_within_scheduled_open_close",
                    )
                }
                for city, row in results.items()
            }
        )
    )


if __name__ == "__main__":
    main()
