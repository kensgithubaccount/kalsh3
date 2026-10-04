"""NON-SCIENTIFIC canaries for the five-task multicity collector image.

Run only as separate canary Cloud Run jobs with an explicit mode argument. No mode
here can create a scientific claim, query an allowlist-date WeatherNext forecast,
or query a current Kalshi event: the kalshi mode reads only finalized 2026-09-27
events for connectivity and shape; the only Earth Engine read is the fixed non-current 2026-09-27
initialization, and all GCS writes go under the c1_canary/ prefix.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cloud_collector as cc

CANARY_PREFIX = "c1_canary"


def canary_store(policy: Any, mode: str) -> Any:
    """The frozen D1 create-only transport, re-pointed away from the scientific prefix."""
    from google.cloud import storage

    store = policy.GcsCreateOnly(storage.Client(project=cc.PROJECT).bucket(cc.BUCKET))
    execution = os.environ.get("CLOUD_RUN_EXECUTION", "local")
    store.prefix = f"{CANARY_PREFIX}/{mode}/{execution}/"
    return store


def replay(policy: Any, index: int, city_id: str) -> dict[str, Any]:
    import ee

    import checks
    import fixtures as fx

    if fx.HISTORICAL in policy.ALLOWLIST or fx.HISTORICAL >= datetime.now(UTC).date():
        raise RuntimeError("CANARY_DATE_NOT_HISTORICAL")
    home, credentials = cc.load_user_credentials()
    try:
        ee.data.setMaxRetries(0)
        ee.Initialize(credentials=credentials, project=cc.PROJECT)
        ee.data.setMaxRetries(0)
        raw = cc.ee_weather(ee, policy, city_id, fx.HISTORICAL)
    finally:
        home.cleanup()
    rows = cc.rows_from_features(policy, city_id, raw)
    frozen = fx.historical_rows(city_id)
    live_d1 = fx.d1_rows(rows)
    maximum = policy.validate_weather(city_id, fx.HISTORICAL, rows)
    cc.validate_diagnostics(rows)
    frozen_max = policy.validate_weather(city_id, fx.HISTORICAL, frozen)
    start, end = policy.target_window(city_id, fx.HISTORICAL)
    live = {
        "historical_target_date": fx.HISTORICAL.isoformat(),
        "rows": len(rows),
        "live_rows_equal_frozen_d1_fixture": live_d1 == frozen,
        "grid_equal": all(
            policy.grid_matches(row["grid_lonlat"], policy.CITIES[city_id]["reviewed_grid_lonlat"])
            for row in rows
        ),
        "target_hour_set_equal": [row["valid_time_utc"] for row in rows]
        == [row["valid_time_utc"] for row in frozen],
        "weathernext_values_equal": [row[cc.PRIMARY_BAND] for row in rows]
        == [row[cc.PRIMARY_BAND] for row in frozen],
        "forecast_calculation_equal": maximum == frozen_max,
        "target_window_utc": [policy.stamp(start), policy.stamp(end)],
        "daily_max_p50_proxy": cc.proxy(policy, rows, cc.PRIMARY_BAND),
        "diagnostic_maxima": {band: cc.proxy(policy, rows, band)["kelvin"] for band in cc.DIAGNOSTIC_BANDS},
        "credential_class": type(credentials).__module__ + "." + type(credentials).__name__,
        "granted_scope_names": sorted(credentials.granted_scopes or []),
    }
    live["pass"] = all(v for k, v in live.items() if k.endswith("_equal")) and live["rows"] == 24
    offline = checks.replay_city(policy, city_id)
    amendment = checks.amendment_suite(policy, city_id)
    return {
        "live_historical_earth_engine": live,
        "offline_protocol_equivalence": offline,
        "pre_first_event_amendment": amendment,
        "pass": live["pass"] and offline["pass"] and amendment["pass"],
    }


def kalshi(policy: Any, index: int, city_id: str) -> dict[str, Any]:
    """Connectivity/shape only, against this city's FINALIZED 2026-09-27 event.

    Uses the scientific transport and path shapes. Records no prices, results, or bodies.
    """
    import fixtures as fx
    from urllib.parse import quote

    if fx.HISTORICAL in policy.ALLOWLIST or fx.HISTORICAL >= datetime.now(UTC).date():
        raise RuntimeError("CANARY_DATE_NOT_HISTORICAL")
    city = policy.CITIES[city_id]
    series = city["series_ticker"]
    event_ticker = series + "-" + fx.HISTORICAL.strftime("%y%b%d").upper()
    http: list[dict[str, Any]] = []

    def get(path: str) -> dict[str, Any]:
        result = cc.kalshi_get(path)
        http.append({"path": path, "status": result["status"], "body_bytes": len(base64.b64decode(result["raw_body_b64"]))})
        payload: dict[str, Any] = result["payload"]
        return payload

    payload = get("/trade-api/v2/events/" + quote(event_ticker, safe=""))
    event, listed = payload.get("event") or {}, payload.get("markets") or []
    tickers = sorted(row.get("ticker", "") for row in listed)
    series_payload = get("/trade-api/v2/series/" + quote(series, safe="")).get("series") or {}
    required = ("ticker", "event_ticker", "strike_type", "rules_primary", "status", "result",
                "expiration_value", "open_time", "close_time", "yes_bid_dollars",
                "yes_ask_dollars", "yes_bid_size_fp", "yes_ask_size_fp")
    siblings = []
    for ticker in tickers:
        market = get("/trade-api/v2/markets/" + quote(ticker, safe="")).get("market") or {}
        books = get("/trade-api/v2/markets/orderbooks?tickers=" + quote(ticker, safe="")).get("orderbooks")
        book = books[0] if isinstance(books, list) and len(books) == 1 and isinstance(books[0], dict) else {}
        fp = book.get("orderbook_fp")
        siblings.append(
            {
                "ticker": ticker,
                "market_identity_ok": market.get("ticker") == ticker and market.get("event_ticker") == event_ticker,
                "market_required_fields_present": all(key in market for key in required),
                "bounds_present": market.get("cap_strike") is not None if market.get("strike_type") == "less"
                else market.get("floor_strike") is not None if market.get("strike_type") == "greater"
                else market.get("floor_strike") is not None and market.get("cap_strike") is not None,
                "strike_type": market.get("strike_type"),
                "status": market.get("status"),
                "cli_rule_text_ok": f"at {city['city']} ({city['cli_product']}) for " in str(market.get("rules_primary"))
                and "according to The Weather Company" in str(market.get("rules_primary")),
                "orderbook_identity_ok": book.get("ticker") == ticker,
                "orderbook_fp_is_object": isinstance(fp, dict),
                "orderbook_side_keys": sorted(fp) if isinstance(fp, dict) else None,
                "executability_status_if_evaluated": cc.executability_status(fp),
            }
        )
    out = {
        "historical_event_ticker": event_ticker,
        "kalshi_host": cc.KALSHI_HOST,
        "http": http,
        "all_http_200": all(row["status"] == 200 for row in http),
        "request_count": len(http),
        "event_identity_ok": event.get("event_ticker") == event_ticker and event.get("series_ticker") == series,
        "event_settlement_source_ok": event.get("settlement_sources") == cc.SETTLEMENT_SOURCES,
        "series_identity_and_source_ok": series_payload.get("ticker") == series
        and series_payload.get("settlement_sources") == cc.SETTLEMENT_SOURCES,
        "sibling_count": len(tickers),
        "sibling_prefix_ok": len(set(tickers)) == 6 and all(t.startswith(event_ticker + "-") for t in tickers),
        "strike_shape_ok": sorted(str(row["strike_type"]) for row in siblings) == ["between"] * 4 + ["greater", "less"],
        "market_statuses": sorted({str(row["status"]) for row in siblings}),
        "siblings": siblings,
        "scientific_claim_created": False,
        "packet_created": False,
        "outcome_values_recorded": False,
    }
    out["pass"] = bool(
        out["all_http_200"] and out["request_count"] == 14 and out["event_identity_ok"]
        and out["event_settlement_source_ok"] and out["series_identity_and_source_ok"]
        and out["sibling_prefix_ok"] and out["strike_shape_ok"]
        and all(
            row["market_identity_ok"] and row["market_required_fields_present"] and row["bounds_present"]
            and row["cli_rule_text_ok"] and row["orderbook_identity_ok"] and row["orderbook_fp_is_object"]
            for row in siblings
        )
    )
    return out


def isolation(policy: Any, index: int, city_id: str) -> dict[str, Any]:
    """Task 0 fails on purpose; tasks 1-4 must still finish and write their own marker."""
    import checks

    suite = checks.isolation_suite(policy)
    if index == 0:
        return {"deliberate_task_failure": True, "offline_suite": suite, "pass": suite["pass"], "exit": 1}
    time.sleep(20)  # still running well after task 0 has already failed
    return {"deliberate_task_failure": False, "offline_suite": suite, "pass": suite["pass"]}


def immutability(policy: Any, index: int, city_id: str, store: Any) -> dict[str, Any]:
    name = f"probe/{city_id}.json"
    first = policy.encode({"probe": "first", "city_id": city_id})
    store.create(name, first)
    second_rejected = False
    try:
        store.create(name, policy.encode({"probe": "second", "city_id": city_id}))
    except FileExistsError:
        second_rejected = True
    blob = store.bucket.blob(store.prefix + name)

    def denied(action: Any) -> str:
        try:
            action()
        except Exception as exc:  # noqa: BLE001
            return type(exc).__name__
        return "ALLOWED"

    overwrite = denied(lambda: blob.upload_from_string(b"overwrite", retry=None))
    delete = denied(lambda: blob.delete(retry=None))
    readback = store.bucket.blob(store.prefix + name).download_as_bytes()
    blob.reload()
    return {
        "object": f"gs://{cc.BUCKET}/{store.prefix}{name}",
        "first_create_succeeded": True,
        "second_create_rejected": second_rejected,
        "unconditional_overwrite_result": overwrite,
        "delete_result": delete,
        "readback_sha256": cc.sha(readback),
        "original_sha256": cc.sha(first),
        "generation": str(blob.generation),
        "metageneration": str(blob.metageneration),
        "pass": second_rejected
        and overwrite == "Forbidden"
        and delete == "Forbidden"
        and readback == first
        and str(blob.metageneration) == "1",
    }


def timing(policy: Any, index: int, city_id: str, started: datetime) -> dict[str, Any]:
    gate = datetime.fromisoformat(os.environ["WN_MC_TIMING_GATE_UTC"].replace("Z", "+00:00"))
    if gate.tzinfo is None:
        raise RuntimeError("TIMING_GATE_NAIVE")
    cc.wait_until(gate)
    crossed = datetime.now(UTC)
    simulated = datetime.now(UTC)
    delay = (simulated - gate).total_seconds()
    return {
        "wrapper_started_at_utc": policy.stamp(started),
        "gate_at_utc": policy.stamp(gate),
        "gate_crossed_at_utc": policy.stamp(crossed),
        "simulated_scientific_started_at_utc": policy.stamp(simulated),
        "start_delay_seconds": delay,
        "wrapper_started_before_gate": started < gate,
        "within_five_minute_scientific_window": 0 <= delay < 300,
        "scientific_acquisition": False,
        "execution_claim_created": False,
        "weather_queried": False,
        "kalshi_queried": False,
        "pass": started < gate and 0 <= delay < 5,
    }


def main() -> int:
    started = datetime.now(UTC)
    if len(sys.argv) != 2 or sys.argv[1] not in ("replay", "isolation", "immutability", "timing", "kalshi"):
        raise RuntimeError("CANARY_MODE_REQUIRED")
    mode = sys.argv[1]
    policy = cc.load_policy()
    index, city_id = cc.city_for_task(os.environ)
    record: dict[str, Any] = {
        "record_type": "WN-MULTICITY-C1-CANARY-v1",
        "mode": mode,
        "non_scientific": True,
        "task_index": index,
        "city_id": city_id,
        "series_ticker": policy.CITIES[city_id]["series_ticker"],
        "cloud_run_job": os.environ.get("CLOUD_RUN_JOB", ""),
        "cloud_run_execution": os.environ.get("CLOUD_RUN_EXECUTION", ""),
        "task_attempt": os.environ.get("CLOUD_RUN_TASK_ATTEMPT", ""),
        "started_at_utc": policy.stamp(started),
        # Hashes of the exact files executing in this image, for binding to reviewed source.
        "image_source_sha256": {
            path.relative_to(cc.HERE).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(cc.HERE.rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts
        },
    }
    store = None if mode == "timing" else canary_store(policy, mode)
    try:
        if mode == "replay":
            record["result"] = replay(policy, index, city_id)
        elif mode == "isolation":
            record["result"] = isolation(policy, index, city_id)
        elif mode == "kalshi":
            record["result"] = kalshi(policy, index, city_id)
        elif mode == "immutability":
            record["result"] = immutability(policy, index, city_id, store)
        else:
            record["result"] = timing(policy, index, city_id, started)
        record["pass"] = bool(record["result"]["pass"])
    except Exception as exc:  # noqa: BLE001
        record["pass"] = False
        record["error_code"] = cc.error_code(exc)
    record["completed_at_utc"] = policy.stamp(datetime.now(UTC))
    if store is not None:
        store.create(f"result/{city_id}.json", policy.encode(record))
    print(json.dumps(record, sort_keys=True), flush=True)
    if record.get("result", {}).get("exit") == 1:
        return 1
    return 0 if record["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
