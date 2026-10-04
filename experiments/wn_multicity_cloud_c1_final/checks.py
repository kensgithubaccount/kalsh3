"""Offline protocol-equivalence and failure-isolation checks against frozen D1 logic.

Every check drives the real cloud_collector.run_city code with fake transports and an
in-memory create-only store. The allowlist-dated runs use SYNTHETIC date-shifted copies
of the real non-current 2026-09-27 fixture, because the frozen D1 claim/evidence paths
exist only for allowlist dates. Nothing here is prospective evidence.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import cloud_collector as cc
import fixtures as fx

SYNTHETIC_TARGET = date(2026, 10, 3)
STATUS_MAP = {
    "CAPTURED_AND_PRE_OUTCOME_FROZEN": "OFFLINE_PACKET_SEALED",
    "DATA_NOT_READY": "FAILED_NO_RETRY",
    "FAILED": "FAILED_NO_RETRY",
    "MISSED_SCHEDULED_EXECUTION": "DUPLICATE_OR_LATE",
    "DUPLICATE_CLAIM": "DUPLICATE_OR_LATE",
}


def d1_market(policy: Any, city_id: str, target: date) -> dict[str, Any]:
    import test_policy  # frozen D1 synthetic ladder fixture

    event: dict[str, Any] = test_policy.market(city_id, target)
    return event


def load(store: fx.MemoryStore, name: str) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(store.objects[name])
    return value


def by_ticker(event: dict[str, Any]) -> dict[str, Any]:
    return {row["ticker"]: row for row in event["markets"]}


def replay_city(policy: Any, city_id: str) -> dict[str, Any]:
    """Cloud run_city versus frozen D1 run_offline_city on identical inputs."""
    target = SYNTHETIC_TARGET
    now = policy.decision_at(target) + timedelta(seconds=10)
    city = policy.CITIES[city_id]
    rows = fx.shifted_rows(city_id, target)
    event = d1_market(policy, city_id, target)

    d1_store, cloud_store = fx.MemoryStore(), fx.MemoryStore()
    d1_status = policy.run_offline_city(d1_store, city_id, target, now, fx.d1_rows(rows), event)
    cloud_status, exit_code = fx.run_fake_city(policy, cloud_store, city_id, target, now, rows, event)

    def d1(name: str) -> dict[str, Any]:
        return load(d1_store, policy.artifact_path(city_id, target, name))

    def cloud(name: str) -> dict[str, Any]:
        return load(cloud_store, policy.artifact_path(city_id, target, name))

    start, end = policy.target_window(city_id, target)
    expected_valid = [policy.stamp(start + timedelta(hours=i)) for i in range(24)]
    historical = fx.historical_rows(city_id)
    historical_max = policy.validate_weather(city_id, fx.HISTORICAL, historical)
    packet_keys = [k for k in d1("packet.json") if k not in ("record_type", "component_sha256")]
    forecast_keys = [k for k in d1("forecast.json") if k not in ("record_type", "weather_sha256")]
    cloud_packet, cloud_forecast = cloud("packet.json"), cloud("forecast.json")
    claim = policy.claim_path(city_id, target)
    result = {
        "city_id": city_id,
        "task_index": cc.TASK_INDEX_TO_CITY.index(city_id),
        "series_ticker": city["series_ticker"],
        "cli_product": city["cli_product"],
        "grid_equal": cloud("weather.json")["grid_lonlat"]
        == d1("weather.json")["grid_lonlat"]
        == city["reviewed_grid_lonlat"]
        and all(policy.grid_matches(row["grid_lonlat"], city["reviewed_grid_lonlat"]) for row in rows),
        "target_hour_set_equal": [r["valid_time_utc"] for r in cloud("weather.json")["rows"]]
        == [r["valid_time_utc"] for r in d1("weather.json")["rows"]]
        == expected_valid,
        "weathernext_values_equal": fx.d1_rows(cloud("weather.json")["rows"])
        == d1("weather.json")["rows"],
        "forecast_calculation_equal": cloud_forecast["forecast_p50_proxy_kelvin"]
        == d1("forecast.json")["forecast_p50_proxy_kelvin"]
        == str(historical_max)
        and cloud_forecast["forecast_p50_proxy_fahrenheit"]
        == d1("forecast.json")["forecast_p50_proxy_fahrenheit"]
        == str(policy.kelvin_to_fahrenheit(historical_max))
        and Decimal(cloud_forecast["forecast_p50_proxy_celsius"])
        == historical_max - Decimal("273.15"),
        # The cloud row also carries the API's per-market event_ticker, which the
        # collector requires; every field the D1 fixture defines must match exactly.
        "city_event_parsing_equal": {
            ticker: {key: row.get(key) for key in by_ticker(d1("market.json")["event"])[ticker]}
            for ticker, row in by_ticker(cloud("market.json")["event"]).items()
        }
        == by_ticker(d1("market.json")["event"])
        and {k: v for k, v in cloud("market.json")["event"].items() if k != "markets"}
        == {k: v for k, v in d1("market.json")["event"].items() if k != "markets"},
        "settlement_window_identity_equal": cloud("weather.json")["target_window_utc"]
        == {"start_inclusive": policy.stamp(start), "end_exclusive": policy.stamp(end)}
        and cloud_packet["settlement_window_version"] == policy.PROTOCOL["settlement_window_version"]
        and cloud_packet["cli_product"] == city["cli_product"],
        "packet_scientific_fields_equal": all(
            cloud_packet[key] == d1("packet.json")[key] for key in packet_keys
        ),
        "forecast_scientific_fields_equal": all(
            cloud_forecast[key] == d1("forecast.json")[key] for key in forecast_keys
        ),
        "claim_bytes_equal": cloud_store.objects[claim] == d1_store.objects[claim],
        "status_equal": STATUS_MAP[cloud_status] == d1_status == "OFFLINE_PACKET_SEALED"
        and exit_code == 0,
        "forecast_p50_proxy_kelvin": cloud_forecast["forecast_p50_proxy_kelvin"],
        "forecast_p50_proxy_fahrenheit": cloud_forecast["forecast_p50_proxy_fahrenheit"],
        "packet_scientific_keys_compared": packet_keys,
        "forecast_scientific_keys_compared": forecast_keys,
    }
    result["status_logic"] = status_logic(policy, city_id)
    result["status_logic_equal"] = all(row["equal"] for row in result["status_logic"])
    result["pass"] = all(value for key, value in result.items() if key.endswith("_equal"))
    return result


def status_logic(policy: Any, city_id: str) -> list[dict[str, Any]]:
    target = SYNTHETIC_TARGET
    due = policy.decision_at(target)
    good_rows = fx.shifted_rows(city_id, target)
    good_event = d1_market(policy, city_id, target)
    other = "miami" if city_id != "miami" else "boston"

    late_ingestion = [dict(row) for row in good_rows]
    late_ingestion[3]["ingestion_time_utc"] = policy.stamp(due)
    wrong_grid = [dict(row) for row in good_rows]
    wrong_grid[0]["grid_lonlat"] = policy.CITIES[other]["reviewed_grid_lonlat"]
    wrong_source = json.loads(json.dumps(good_event))
    wrong_source["settlement_sources"][0]["name"] = "Other"
    wrong_cli = json.loads(json.dumps(good_event))
    wrong_cli["markets"][0]["rules_primary"] = wrong_cli["markets"][0]["rules_primary"].replace(
        policy.CITIES[city_id]["cli_product"], policy.CITIES[other]["cli_product"]
    )
    leaked = json.loads(json.dumps(good_event))
    leaked["markets"][0]["result"] = "yes"
    scenarios = [
        ("timely", due + timedelta(seconds=10), good_rows, good_event, False),
        ("late_0905", due + timedelta(minutes=5), good_rows, good_event, False),
        ("duplicate_claim", due + timedelta(seconds=10), good_rows, good_event, True),
        ("missing_hour", due + timedelta(seconds=10), good_rows[:-1], good_event, False),
        ("late_ingestion", due + timedelta(seconds=10), late_ingestion, good_event, False),
        ("wrong_city_grid", due + timedelta(seconds=10), wrong_grid, good_event, False),
        ("wrong_settlement_source", due + timedelta(seconds=10), good_rows, wrong_source, False),
        ("wrong_city_cli_rule", due + timedelta(seconds=10), good_rows, wrong_cli, False),
        ("outcome_present", due + timedelta(seconds=10), good_rows, leaked, False),
    ]
    out = []
    for name, now, rows, event, preclaimed in scenarios:
        d1_store, cloud_store = fx.MemoryStore(), fx.MemoryStore()
        if preclaimed:
            for store in (d1_store, cloud_store):
                store.create(policy.claim_path(city_id, target), b"prior claim\n")
        d1_status = policy.run_offline_city(d1_store, city_id, target, now, fx.d1_rows(rows), event)
        cloud_status, _ = fx.run_fake_city(policy, cloud_store, city_id, target, now, rows, event)
        claim = policy.claim_path(city_id, target)
        out.append(
            {
                "scenario": name,
                "d1_status": d1_status,
                "cloud_status": cloud_status,
                "equal": STATUS_MAP[cloud_status] == d1_status
                and cloud_store.objects[claim] == d1_store.objects[claim]
                and (
                    policy.artifact_path(city_id, target, "packet.json") in cloud_store.objects
                )
                == (policy.artifact_path(city_id, target, "packet.json") in d1_store.objects),
            }
        )
    for name, target_date in (
        ("reject_oct2", date(2026, 10, 2)),
        ("reject_oct15", date(2026, 10, 15)),
        ("reject_other_year", date(2027, 10, 3)),
    ):
        now = datetime(target_date.year, target_date.month, target_date.day, 9, 0, 10, tzinfo=UTC)
        out.append(gate_refusal(policy, city_id, name, target_date, now, "OUTSIDE_ALLOWLIST"))
    out.append(
        gate_refusal(
            policy, city_id, "before_0900", target, due - timedelta(microseconds=1), "BEFORE_DECISION"
        )
    )
    return out


def gate_refusal(
    policy: Any, city_id: str, name: str, target: date, now: datetime, expected: str
) -> dict[str, Any]:
    """Both implementations must raise the same frozen gate code and touch nothing."""
    codes = []
    store = fx.MemoryStore()
    calls: list[str] = []
    weather_calls: list[str] = []
    try:
        policy.gate(city_id, target, now)
        codes.append("NO_ERROR")
    except RuntimeError as exc:
        codes.append(str(exc))
    try:
        fx.run_fake_city(
            policy, store, city_id, target, now, [], {"markets": []},
            calls=calls, weather_calls=weather_calls,
        )
        codes.append("NO_ERROR")
    except RuntimeError as exc:
        codes.append(str(exc))
    # policy.claim_path refuses non-allowlist dates, which CityStore hits first.
    accepted = {expected, "INVALID_CLAIM_IDENTITY"} if expected == "OUTSIDE_ALLOWLIST" else {expected}
    return {
        "scenario": name,
        "d1_status": codes[0],
        "cloud_status": codes[1],
        "equal": codes[0] == expected
        and codes[1] in accepted
        and not store.objects
        and not calls
        and not weather_calls,
    }


def city_objects(store: fx.MemoryStore, city_id: str) -> dict[str, bytes]:
    return {
        name: payload
        for name, payload in store.objects.items()
        if name.startswith((f"claims/{city_id}/", f"city_days/{city_id}/"))
    }


def isolation_suite(policy: Any) -> dict[str, Any]:
    """The five required failure-isolation proofs, on one shared create-only store."""
    target = SYNTHETIC_TARGET
    due = policy.decision_at(target)
    now = due + timedelta(seconds=10)
    cities = list(cc.TASK_INDEX_TO_CITY)

    def good(city_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        return fx.shifted_rows(city_id, target), d1_market(policy, city_id, target)

    def sealed(store: fx.MemoryStore, city_id: str) -> bool:
        return policy.artifact_path(city_id, target, "packet.json") in store.objects

    # 1. Boston fails; Miami/Denver/LA/Seattle still seal.
    store = fx.MemoryStore()
    rows, event = good("boston")
    rows[0]["grid_lonlat"] = policy.CITIES["miami"]["reviewed_grid_lonlat"]
    statuses = {"boston": fx.run_fake_city(policy, store, "boston", target, now, rows, event)[0]}
    for city_id in cities[1:]:
        statuses[city_id] = fx.run_fake_city(policy, store, city_id, target, now, *good(city_id))[0]
    boston_failure = load(store, policy.artifact_path("boston", target, "failure.json"))
    proof1 = {
        "statuses": statuses,
        "boston_error_code": boston_failure["error_code"],
        "pass": statuses["boston"] == "FAILED"
        and not sealed(store, "boston")
        and all(
            statuses[c] == "CAPTURED_AND_PRE_OUTCOME_FROZEN" and sealed(store, c) for c in cities[1:]
        ),
    }

    # 2. A duplicate Boston claim exits before any acquisition and changes nothing anywhere.
    before = dict(store.objects)
    calls: list[str] = []
    weather_calls: list[str] = []
    duplicate = fx.run_fake_city(
        policy, store, "boston", target, now, *good("boston"), calls=calls, weather_calls=weather_calls
    )[0]
    proof2 = {
        "status": duplicate,
        "weather_queries": len(weather_calls),
        "kalshi_queries": len(calls),
        "pass": duplicate == "DUPLICATE_CLAIM"
        and not calls
        and not weather_calls
        and store.objects == before,
    }

    # 3. One DATA_NOT_READY is a single attempt: one weather query, zero market queries, no retry.
    store3 = fx.MemoryStore()
    calls, weather_calls = [], []
    rows, event = good("denver")
    first = fx.run_fake_city(
        policy, store3, "denver", target, now, rows[:-1], event, calls=calls, weather_calls=weather_calls
    )
    second = fx.run_fake_city(
        policy, store3, "denver", target, now, rows, event, calls=calls, weather_calls=weather_calls
    )
    failure = load(store3, policy.artifact_path("denver", target, "failure.json"))
    proof3 = {
        "first_status": first[0],
        "second_invocation_status": second[0],
        "weather_queries_total": len(weather_calls),
        "kalshi_queries_total": len(calls),
        "failure_no_retry": failure["no_retry"],
        "pass": first == ("DATA_NOT_READY", 1)
        and second[0] == "DUPLICATE_CLAIM"
        and len(weather_calls) == 1
        and not calls
        and failure["no_retry"] is True
        and not sealed(store3, "denver"),
    }

    # 4. One malformed market cannot mutate another city's evidence.
    store4 = fx.MemoryStore()
    for city_id in cities:
        if city_id != "los_angeles":
            fx.run_fake_city(policy, store4, city_id, target, now, *good(city_id))
    before = dict(store4.objects)
    rows, event = good("los_angeles")
    event["markets"][2]["floor_strike"] = "not-a-number"
    malformed = fx.run_fake_city(policy, store4, "los_angeles", target, now, rows, event)[0]
    cross_city_refused = False
    try:
        cc.CityStore(store4, policy, "los_angeles", target).create(
            policy.artifact_path("seattle", target, "packet.json"), b"x"
        )
    except RuntimeError as exc:
        cross_city_refused = str(exc) == "CROSS_CITY_OR_INVALID_EVIDENCE_PATH"
    proof4 = {
        "status": malformed,
        "other_city_objects_unchanged": all(
            store4.objects[name] == payload for name, payload in before.items()
        ),
        "only_los_angeles_keys_added": all(
            name.startswith(("claims/los_angeles/", "city_days/los_angeles/"))
            for name in set(store4.objects) - set(before)
        ),
        "cross_city_write_refused": cross_city_refused,
        "pass": malformed == "FAILED"
        and all(store4.objects[name] == payload for name, payload in before.items())
        and all(
            name.startswith(("claims/los_angeles/", "city_days/los_angeles/"))
            for name in set(store4.objects) - set(before)
        )
        and cross_city_refused
        and not sealed(store4, "los_angeles"),
    }

    # 5. One late task cannot acquire; timely tasks proceed.
    store5 = fx.MemoryStore()
    calls, weather_calls = [], []
    late = fx.run_fake_city(
        policy, store5, "seattle", target, due + timedelta(minutes=5), *good("seattle"),
        calls=calls, weather_calls=weather_calls,
    )[0]
    timely = {
        c: fx.run_fake_city(policy, store5, c, target, now, *good(c))[0] for c in cities[:-1]
    }
    proof5 = {
        "late_status": late,
        "late_weather_queries": len(weather_calls),
        "late_kalshi_queries": len(calls),
        "late_claim_kind": load(store5, policy.claim_path("seattle", target))["kind"],
        "timely_statuses": timely,
        "pass": late == "MISSED_SCHEDULED_EXECUTION"
        and not calls
        and not weather_calls
        and not sealed(store5, "seattle")
        and all(v == "CAPTURED_AND_PRE_OUTCOME_FROZEN" for v in timely.values()),
    }
    proofs = {
        "boston_failure_does_not_block_others": proof1,
        "duplicate_boston_claim_does_not_affect_others": proof2,
        "data_not_ready_does_not_retry": proof3,
        "malformed_market_does_not_mutate_other_city": proof4,
        "late_task_cannot_acquire_while_timely_proceed": proof5,
    }
    return {"proofs": proofs, "pass": all(p["pass"] for p in proofs.values())}


def amendment_suite(policy: Any, city_id: str) -> dict[str, Any]:
    """Pre-first-event amendment v1: depth never erases a forecast; other authority still does."""
    target = SYNTHETIC_TARGET
    now = policy.decision_at(target) + timedelta(seconds=10)
    rows = fx.shifted_rows(city_id, target)
    other = "miami" if city_id != "miami" else "boston"

    def run(mutate: Any, **kwargs: Any) -> tuple[str, int, fx.MemoryStore, dict[str, Any]]:
        event = d1_market(policy, city_id, target)
        mutate(event)
        store = fx.MemoryStore()
        status, code = fx.run_fake_city(policy, store, city_id, target, now, rows, event, **kwargs)
        return status, code, store, event

    def path(name: str) -> str:
        return f"city_days/{city_id}/{target:%Y%m%d}/{name}"

    base_status, _, base_store, _ = run(lambda event: None)
    base_forecast = load(base_store, path("forecast.json"))

    def same_forecast(store: fx.MemoryStore) -> bool:
        if path("forecast.json") not in store.objects:
            return False
        forecast = load(store, path("forecast.json"))
        keys = ("forecast_p50_proxy_kelvin", "forecast_p50_proxy_fahrenheit", "weather_sha256", "forecast_status")
        return all(forecast[key] == base_forecast[key] for key in keys)

    def one_sided(event: dict[str, Any]) -> None:
        event["markets"][0]["orderbook_fp"] = {"yes_dollars": [], "no_dollars": [["0.99", "5"]]}

    def all_empty(event: dict[str, Any]) -> None:
        for row in event["markets"]:
            row["orderbook_fp"] = {"yes_dollars": [], "no_dollars": []}

    def missing_side(event: dict[str, Any]) -> None:
        event["markets"][5]["orderbook_fp"] = {"yes_dollars": [["0.01", "3"]]}

    cases = {}
    for name, mutate, abstain_count in (
        ("one_sided_single_sibling", one_sided, 1),
        ("all_books_empty", all_empty, 6),
        ("side_key_absent", missing_side, 1),
    ):
        status, code, store, event = run(mutate)
        market = load(store, path("market.json")) if path("market.json") in store.objects else {}
        packet = load(store, path("packet.json")) if path("packet.json") in store.objects else {}
        executability = market.get("market_executability", {})
        d1_status = policy.run_offline_city(fx.MemoryStore(), city_id, target, now, fx.d1_rows(rows), event)
        cases[name] = {
            "cloud_status": status,
            "frozen_d1_status_superseded_by_amendment": d1_status,
            "abstain_count": sum(v == cc.ABSTAIN for v in executability.values()),
            "executable_count": sum(v == cc.EXECUTABLE for v in executability.values()),
            "real_books_preserved": bool(market)
            and {r["ticker"]: r["orderbook_fp"] for r in market["event"]["markets"]}
            == {r["ticker"]: r["orderbook_fp"] for r in event["markets"]},
            "forecast_identical_to_two_sided_run": same_forecast(store),
            "pass": (status, code) == ("CAPTURED_AND_PRE_OUTCOME_FROZEN", 0)
            and d1_status == "FAILED_NO_RETRY"
            and len(executability) == 6
            and sum(v == cc.ABSTAIN for v in executability.values()) == abstain_count
            and packet.get("market_executability") == executability
            and packet.get("all_siblings_executable") is False
            and packet.get("weather_development_observation") == "VALID_PRE_OUTCOME_SEALED"
            and same_forecast(store)
            and {r["ticker"]: r["orderbook_fp"] for r in market["event"]["markets"]}
            == {r["ticker"]: r["orderbook_fp"] for r in event["markets"]},
        }

    def wrong_cli(event: dict[str, Any]) -> None:
        one_sided(event)
        event["markets"][1]["rules_primary"] = event["markets"][1]["rules_primary"].replace(
            policy.CITIES[city_id]["cli_product"], policy.CITIES[other]["cli_product"]
        )

    def inactive(event: dict[str, Any]) -> None:
        one_sided(event)
        event["markets"][2]["status"] = "closed"

    def outcome(event: dict[str, Any]) -> None:
        all_empty(event)
        event["markets"][3]["result"] = "no"

    def no_book_object(event: dict[str, Any]) -> None:
        event["markets"][4]["orderbook_fp"] = None

    def five_siblings(event: dict[str, Any]) -> None:
        all_empty(event)
        event["markets"].pop()

    for name, mutate in (
        ("one_sided_plus_wrong_cli", wrong_cli),
        ("one_sided_plus_inactive", inactive),
        ("empty_plus_outcome_present", outcome),
        ("orderbook_object_absent", no_book_object),
        ("empty_plus_five_siblings", five_siblings),
    ):
        status, code, store, _ = run(mutate)
        cases[name] = {
            "cloud_status": status,
            "error_code": load(store, path("failure.json"))["error_code"],
            "pass": (status, code) == ("FAILED", 1)
            and path("forecast.json") not in store.objects
            and path("packet.json") not in store.objects,
        }

    status, code, store, _ = run(one_sided, delayed_stall_seconds=31)
    cases["delayed_snapshot_missed_after_seal"] = {
        "cloud_status": status,
        "forecast_bytes_identical_to_run_without_miss": path("forecast.json") in store.objects
        and store.objects[path("forecast.json")]
        == run(one_sided)[2].objects[path("forecast.json")],
        "pass": (status, code) == ("DELAYED_SNAPSHOT_MISSED", 1)
        and path("packet.json") in store.objects
        and path("failure.json") not in store.objects
        and path("delayed_books.json") not in store.objects
        and load(store, path("delayed_snapshot_failure.json"))["weather_development_forecast_unaffected"] is True
        and store.objects[path("forecast.json")] == run(one_sided)[2].objects[path("forecast.json")],
    }
    two_sided = load(base_store, path("packet.json"))
    cases["two_sided_baseline"] = {
        "cloud_status": base_status,
        "pass": base_status == "CAPTURED_AND_PRE_OUTCOME_FROZEN"
        and two_sided["all_siblings_executable"] is True
        and set(two_sided["market_executability"].values()) == {cc.EXECUTABLE},
    }
    event = d1_market(policy, city_id, target)
    snapshot = json.dumps(event, sort_keys=True)
    cc.depth_neutral_view(event)
    cases["validation_view_does_not_mutate_event"] = {"pass": json.dumps(event, sort_keys=True) == snapshot}
    return {
        "amendment_sha256": cc.AMENDMENT_SHA256,
        "cases": cases,
        "pass": all(case["pass"] for case in cases.values()),
    }
