"""Offline tests for the five-city cloud collector. No network, no GCS, no Earth Engine."""

from __future__ import annotations

import json
import sys
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import checks
import cloud_collector as cc
import fixtures as fx

POLICY = cc.load_policy()
TARGET = date(2026, 10, 3)
DUE = datetime(2026, 10, 3, 9, tzinfo=UTC)
NOW = DUE + timedelta(seconds=10)


def env(index: Any, count: Any = "5", attempt: Any = "0") -> dict[str, str]:
    return {
        key: value
        for key, value in (
            ("CLOUD_RUN_TASK_INDEX", index),
            ("CLOUD_RUN_TASK_COUNT", count),
            ("CLOUD_RUN_TASK_ATTEMPT", attempt),
        )
        if value is not None
    }


def good(city_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return fx.shifted_rows(city_id, TARGET), checks.d1_market(POLICY, city_id, TARGET)


class PreconditionFailed(Exception):
    pass


class Blob:
    def __init__(self, objects: dict[str, bytes], name: str) -> None:
        self.objects, self.name = objects, name

    def exists(self) -> bool:
        return self.name in self.objects

    def upload_from_string(self, payload: bytes, *, if_generation_match: int, retry: object, **_: object) -> None:
        if if_generation_match != 0 or retry is not None:
            raise AssertionError("GCS_IMMUTABILITY_OR_RETRY_POLICY")
        if self.name in self.objects:
            raise PreconditionFailed(self.name)
        self.objects[self.name] = payload


class Bucket:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def blob(self, name: str) -> Blob:
        return Blob(self.objects, name)


class FrozenAuthorityTests(unittest.TestCase):
    def test_bundle_is_byte_identical_to_d1_and_tamper_is_detected(self) -> None:
        source = Path(__file__).resolve().parent.parent / "wn_multicity_prospective_v1"
        for path in cc.FROZEN.iterdir():
            if path.is_file():
                self.assertEqual(path.read_bytes(), (source / path.name).read_bytes(), path.name)
        cc.verify_frozen_bundle()
        original = cc.D1_SHA256SUMS_SHA256
        try:
            cc.D1_SHA256SUMS_SHA256 = "0" * 64
            with self.assertRaisesRegex(RuntimeError, "D1_SHA256SUMS_HASH_MISMATCH"):
                cc.verify_frozen_bundle()
        finally:
            cc.D1_SHA256SUMS_SHA256 = original

    def test_task_mapping_is_frozen(self) -> None:
        expected = ["boston", "miami", "denver", "los_angeles", "seattle"]
        self.assertEqual(list(cc.TASK_INDEX_TO_CITY), expected)
        for index, city_id in enumerate(expected):
            self.assertEqual(cc.city_for_task(env(str(index))), (index, city_id))
        for bad in (None, "", "5", "-1", "00", "1.0", " 1", "boston"):
            with self.assertRaisesRegex(RuntimeError, "TASK_INDEX_INVALID"):
                cc.city_for_task(env(bad))
        for count in (None, "1", "4", "6"):
            with self.assertRaisesRegex(RuntimeError, "TASK_COUNT_NOT_FIVE"):
                cc.city_for_task(env("0", count=count))
        for attempt in (None, "1", "2"):
            with self.assertRaisesRegex(RuntimeError, "TASK_RETRY_PROHIBITED"):
                cc.city_for_task(env("0", attempt=attempt))

    def test_city_identity_table_matches_task_statement(self) -> None:
        stated = {
            "boston": ("KXHIGHTBOS", "CLIBOS", [-71.0, 42.35], "05:00:00Z"),
            "miami": ("KXHIGHMIA", "CLIMIA", [-80.3, 25.8], "05:00:00Z"),
            "denver": ("KXHIGHDEN", "CLIDEN", [-104.65, 39.85], "07:00:00Z"),
            "los_angeles": ("KXHIGHLAX", "CLILAX", [-118.4, 33.95], "08:00:00Z"),
            "seattle": ("KXHIGHTSEA", "CLISEA", [-122.3, 47.45], "08:00:00Z"),
        }
        for city_id, (series, cli, grid, start_clock) in stated.items():
            city = POLICY.CITIES[city_id]
            start, end = POLICY.target_window(city_id, TARGET)
            self.assertEqual(
                (city["series_ticker"], city["cli_product"], city["reviewed_grid_lonlat"]),
                (series, cli, grid),
            )
            self.assertEqual(POLICY.stamp(start), "2026-10-03T" + start_clock)
            self.assertEqual(POLICY.stamp(end), "2026-10-04T" + start_clock)
            self.assertEqual(POLICY.decision_at(TARGET), DUE)


class GateTests(unittest.TestCase):
    def test_exact_date_allowlist(self) -> None:
        self.assertEqual(
            sorted(d.isoformat() for d in POLICY.ALLOWLIST),
            [f"2026-10-{day:02d}" for day in range(3, 15)],
        )
        for day in range(3, 15):
            refusal, _, _, target, due = cc.preflight(
                POLICY, datetime(2026, 10, day, 8, 50, tzinfo=UTC), env("0")
            )
            self.assertIsNone(refusal)
            self.assertEqual((target, due), (date(2026, 10, day), datetime(2026, 10, day, 9, tzinfo=UTC)))
        for outside in (
            datetime(2026, 10, 2, 8, 50, tzinfo=UTC),
            datetime(2026, 10, 15, 8, 50, tzinfo=UTC),
            datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
            datetime(2025, 10, 3, 9, 0, tzinfo=UTC),
            datetime(2027, 10, 3, 9, 0, tzinfo=UTC),
            datetime(2026, 9, 3, 9, 0, tzinfo=UTC),
            datetime(2026, 11, 3, 9, 0, tzinfo=UTC),
            datetime(2026, 10, 2, 23, 59, 59, 999999, tzinfo=UTC),
        ):
            self.assertEqual(cc.preflight(POLICY, outside, env("0"))[0], "OUTSIDE_EXACT_DATE_ALLOWLIST")

    def test_wrapper_window(self) -> None:
        early = cc.preflight(POLICY, DUE - timedelta(minutes=15, microseconds=1), env("0"))
        self.assertEqual(early[0], "WRAPPER_TOO_EARLY_NO_WAIT")
        self.assertIsNone(cc.preflight(POLICY, DUE - timedelta(minutes=10), env("0"))[0])
        with self.assertRaisesRegex(RuntimeError, "NAIVE_CLOCK"):
            cc.preflight(POLICY, datetime(2026, 10, 3, 8, 50), env("0"))

    def test_nothing_happens_before_0900(self) -> None:
        store = fx.MemoryStore()
        calls: list[str] = []
        weather: list[str] = []
        rows, event = good("boston")
        with self.assertRaisesRegex(RuntimeError, "BEFORE_DECISION"):
            fx.run_fake_city(
                POLICY, store, "boston", TARGET, DUE - timedelta(microseconds=1), rows, event,
                calls=calls, weather_calls=weather,
            )
        self.assertEqual((store.objects, calls, weather), ({}, [], []))

    def test_scientific_start_window_bounds(self) -> None:
        for now, expected in (
            (DUE, "CAPTURED_AND_PRE_OUTCOME_FROZEN"),
            (DUE + timedelta(minutes=5) - timedelta(microseconds=1), "CAPTURED_AND_PRE_OUTCOME_FROZEN"),
            (DUE + timedelta(minutes=5), "MISSED_SCHEDULED_EXECUTION"),
            (DUE + timedelta(hours=6), "MISSED_SCHEDULED_EXECUTION"),
        ):
            store = fx.MemoryStore()
            calls: list[str] = []
            weather: list[str] = []
            status, _ = fx.run_fake_city(
                POLICY, store, "miami", TARGET, now, *good("miami"), calls=calls, weather_calls=weather
            )
            self.assertEqual(status, expected, now)
            if expected == "MISSED_SCHEDULED_EXECUTION":
                self.assertEqual((calls, weather), ([], []))
                self.assertEqual(
                    json.loads(store.objects[POLICY.claim_path("miami", TARGET)])["kind"],
                    "MISSED_NO_ACQUISITION",
                )
                failure = json.loads(store.objects[POLICY.artifact_path("miami", TARGET, "failure.json")])
                self.assertEqual(failure["status"], "MISSED_SCHEDULED_EXECUTION")
                self.assertTrue(failure["no_acquisition"] and failure["no_backfill"])

    def test_slow_setup_crossing_close_never_acquires(self) -> None:
        store = fx.MemoryStore()
        clock = fx.FakeClock(DUE + timedelta(minutes=4, seconds=59))
        weather: list[str] = []
        calls: list[str] = []

        def open_weather() -> Any:
            clock.sleep(2)  # slow OAuth refresh crosses 09:05:00Z
            return (lambda: weather.append("x") or {}), {}

        status, code = cc.run_city(
            POLICY, store, "denver", TARGET, wrapper_started_at=clock.now, clock=clock,
            sleep=clock.sleep, open_weather=open_weather,
            get=fx.fake_kalshi(good("denver")[1], calls), context={},
        )
        self.assertEqual((status, code, weather, calls), ("MISSED_SCHEDULED_EXECUTION", 1, [], []))


class EvidenceTests(unittest.TestCase):
    def test_sealed_layout_hashes_and_claim(self) -> None:
        store = fx.MemoryStore()
        status, code = fx.run_fake_city(POLICY, store, "seattle", TARGET, NOW, *good("seattle"))
        self.assertEqual((status, code), ("CAPTURED_AND_PRE_OUTCOME_FROZEN", 0))
        base = "city_days/seattle/20261003/"
        expected = {
            "claims/seattle/20261003.json",
            *(base + name for name in (
                "start.json", "raw/weather_feature_collection.json", "weather.json", "market.json",
                "forecast.json", "packet.json", "delayed_books.json", "structured_log.json",
                "SHA256SUMS.json",
            )),
            *(base + f"raw/kalshi/{n:03d}.json" for n in range(1, 21)),
        }
        self.assertEqual(set(store.objects), expected)
        sums = json.loads(store.objects[base + "SHA256SUMS.json"])
        self.assertEqual(set(sums), expected - {base + "SHA256SUMS.json"})
        for name, digest in sums.items():
            self.assertEqual(cc.sha(store.objects[name]), digest)
        packet = json.loads(store.objects[base + "packet.json"])
        for name, digest in packet["component_sha256"].items():
            self.assertEqual(cc.sha(store.objects[base + name]), digest)
        claim = json.loads(store.objects["claims/seattle/20261003.json"])
        start = json.loads(store.objects[base + "start.json"])
        self.assertEqual(claim["kind"], "ONE_SCIENTIFIC_ATTEMPT")
        self.assertEqual(claim["observed_at_utc"], start["scientific_started_at_utc"])
        self.assertEqual(start["scientific_started_at_utc"], "2026-10-03T09:00:10Z")
        delayed = json.loads(store.objects[base + "delayed_books.json"])
        self.assertEqual(delayed["delayed_snapshot_due_at_utc"], "2026-10-03T09:05:10Z")
        self.assertEqual(len(delayed["books"]), 6)

    def test_forecast_rule_and_diagnostics(self) -> None:
        for city_id in cc.TASK_INDEX_TO_CITY:
            store = fx.MemoryStore()
            rows, event = good(city_id)
            fx.run_fake_city(POLICY, store, city_id, TARGET, NOW, rows, event)
            forecast = json.loads(store.objects[POLICY.artifact_path(city_id, TARGET, "forecast.json")])
            kelvin = max(row[cc.PRIMARY_BAND] for row in rows)
            self.assertEqual(float(forecast["forecast_p50_proxy_kelvin"]), kelvin)
            self.assertAlmostEqual(float(forecast["forecast_p50_proxy_celsius"]), kelvin - 273.15, places=9)
            self.assertAlmostEqual(
                float(forecast["forecast_p50_proxy_fahrenheit"]), (kelvin - 273.15) * 9 / 5 + 32, places=9
            )
            diag = forecast["diagnostic_proxies_only"]
            self.assertEqual(
                set(diag), {"daily_max_mean_proxy", "daily_max_p10_proxy", "daily_max_p90_proxy", "interpretation"}
            )
            self.assertIn("not daily-maximum quantiles", diag["interpretation"])
            self.assertEqual(forecast["forecast_status"], "PRE_OUTCOME_FROZEN")
            keys = set(forecast) - {"no_probability", "no_bias_correction", "no_city_specific_tuning"}
            for forbidden in ("probab", "edge", "bias", "tuning", "quantile", "outcome_value", "settle"):
                self.assertFalse([key for key in keys if forbidden in key], forbidden)

    def test_delayed_snapshot_missed_keeps_sealed_forecast(self) -> None:
        store = fx.MemoryStore()
        status, code = fx.run_fake_city(
            POLICY, store, "boston", TARGET, NOW, *good("boston"), delayed_stall_seconds=31
        )
        self.assertEqual((status, code), ("DELAYED_SNAPSHOT_MISSED", 1))
        base = "city_days/boston/20261003/"
        self.assertIn(base + "packet.json", store.objects)
        self.assertIn(base + "forecast.json", store.objects)
        self.assertNotIn(base + "delayed_books.json", store.objects)
        failure = json.loads(store.objects[base + "delayed_snapshot_failure.json"])
        self.assertEqual(failure["status"], "DELAYED_SNAPSHOT_MISSED")
        self.assertTrue(failure["no_retry"])

    def test_delayed_snapshot_timing_edges(self) -> None:
        tickers = ["A", "B"]

        def get(path: str) -> dict[str, Any]:
            ticker = path.rsplit("=", 1)[1]
            return {"payload": {"orderbooks": [{"ticker": ticker, "orderbook_fp": {}}]}}

        clock = fx.FakeClock(NOW)
        out = cc.delayed_books(POLICY, tickers, NOW, get, clock, clock.sleep)
        self.assertEqual(out["delayed_snapshot_started_at_utc"], "2026-10-03T09:05:10Z")
        late = fx.FakeClock(NOW + timedelta(minutes=5, seconds=30, microseconds=1))
        with self.assertRaisesRegex(RuntimeError, "DELAYED_SNAPSHOT_MISSED"):
            cc.delayed_books(POLICY, tickers, NOW, get, late, late.sleep)

    def test_gcs_create_only_through_city_store(self) -> None:
        bucket = Bucket()
        inner = POLICY.GcsCreateOnly(bucket)
        store = cc.CityStore(inner, POLICY, "boston", TARGET)
        claim = POLICY.claim_path("boston", TARGET)
        store.create(claim, b"first")
        with self.assertRaises(FileExistsError):
            store.create(claim, b"second")
        self.assertEqual(bucket.objects["wn_multicity_prospective_v1/v1/" + claim], b"first")
        for bad in (
            POLICY.claim_path("miami", TARGET),
            "city_days/miami/20261003/packet.json",
            "city_days/boston/20261004/packet.json",
            "city_days/boston/20261003/../../miami/20261003/packet.json",
            "city_days/boston/20261003//packet.json",
            "claims/boston/20261003.json.bak",
        ):
            with self.assertRaisesRegex(RuntimeError, "CROSS_CITY_OR_INVALID_EVIDENCE_PATH"):
                store.create(bad, b"x")
        self.assertFalse(hasattr(inner, "delete") or hasattr(store, "delete"))
        self.assertFalse(hasattr(inner, "overwrite") or hasattr(store, "overwrite"))

    def test_real_run_through_gcs_transport_and_duplicate(self) -> None:
        bucket = Bucket()
        inner = POLICY.GcsCreateOnly(bucket)
        self.assertEqual(
            fx.run_fake_city(POLICY, inner, "denver", TARGET, NOW, *good("denver"))[0],
            "CAPTURED_AND_PRE_OUTCOME_FROZEN",
        )
        snapshot = dict(bucket.objects)
        calls: list[str] = []
        weather: list[str] = []
        self.assertEqual(
            fx.run_fake_city(
                POLICY, inner, "denver", TARGET, NOW, *good("denver"), calls=calls, weather_calls=weather
            )[0],
            "DUPLICATE_CLAIM",
        )
        self.assertEqual((bucket.objects, calls, weather), (snapshot, [], []))
        self.assertTrue(all(name.startswith("wn_multicity_prospective_v1/v1/") for name in bucket.objects))


class MarketTests(unittest.TestCase):
    def rejects(self, city_id: str, mutate: Any) -> str:
        rows, event = good(city_id)
        mutate(event)
        store = fx.MemoryStore()
        status, _ = fx.run_fake_city(POLICY, store, city_id, TARGET, NOW, rows, event)
        self.assertNotIn(POLICY.artifact_path(city_id, TARGET, "packet.json"), store.objects)
        self.assertNotIn(POLICY.artifact_path(city_id, TARGET, "forecast.json"), store.objects)
        failure = json.loads(store.objects[POLICY.artifact_path(city_id, TARGET, "failure.json")])
        self.assertEqual(status, "FAILED")
        return str(failure["error_code"])

    def test_runtime_market_authority(self) -> None:
        def drop(event: dict[str, Any]) -> None:
            event["markets"].pop()

        def inactive(event: dict[str, Any]) -> None:
            event["markets"][1]["status"] = "closed"

        def wrong_series(event: dict[str, Any]) -> None:
            event["series_ticker"] = "KXHIGHMIA"

        def wrong_event(event: dict[str, Any]) -> None:
            event["event_ticker"] = "KXHIGHTBOS-26OCT04"

        def wrong_cli(event: dict[str, Any]) -> None:
            for row in event["markets"]:
                row["rules_primary"] = row["rules_primary"].replace("Boston (CLIBOS)", "Miami (CLIMIA)")

        def no_bound(event: dict[str, Any]) -> None:
            del event["markets"][0]["cap_strike"]

        def no_depth(event: dict[str, Any]) -> None:
            event["markets"][3]["orderbook_fp"] = {"yes_dollars": [], "no_dollars": []}

        def outcome(event: dict[str, Any]) -> None:
            event["markets"][0]["expiration_value"] = "61"

        expectations = {
            drop: "MARKET_SIBLING_COUNT_MISMATCH",
            inactive: "MARKET_RULE_OR_STATE",
            wrong_series: "MARKET_SERIES_SOURCE_MISMATCH",
            wrong_event: "MARKET_EVENT_IDENTITY_MISMATCH",
            wrong_cli: "MARKET_RULE_OR_STATE",
            outcome: "OUTCOME_LEAKAGE",
        }
        for mutate, code in expectations.items():
            with self.subTest(mutate=mutate.__name__):
                self.assertEqual(self.rejects("boston", mutate), code)
        self.assertNotEqual(self.rejects("boston", no_bound), "")
        # Amendment v1: depth alone no longer fails the city-day.
        rows, event = good("boston")
        no_depth(event)
        store = fx.MemoryStore()
        self.assertEqual(
            fx.run_fake_city(POLICY, store, "boston", TARGET, NOW, rows, event)[0],
            "CAPTURED_AND_PRE_OUTCOME_FROZEN",
        )

    def test_only_public_read_paths(self) -> None:
        calls: list[str] = []
        fx.run_fake_city(POLICY, fx.MemoryStore(), "los_angeles", TARGET, NOW, *good("los_angeles"), calls=calls)
        self.assertEqual(len(calls), 20)
        self.assertEqual(calls[0], "/trade-api/v2/events/KXHIGHLAX-26OCT03")
        self.assertEqual(calls[1], "/trade-api/v2/series/KXHIGHLAX")
        self.assertTrue(all("KXHIGHLAX" in path for path in calls))
        for path in calls:
            self.assertRegex(path, cc.PUBLIC_PATH)
        for bad in ("/trade-api/v2/portfolio/orders", "/trade-api/v2/markets/../portfolio", "/other",
                    "/trade-api/v2/markets/trades", "/trade-api/v2/series/KXHIGHLAX/markets/X/candlesticks",
                    "/trade-api/v2/events/KXHIGHLAX-26OCT03?with_nested_markets=true",
                    "/trade-api/v2/markets/orderbooks?tickers=A,B"):
            with self.assertRaisesRegex(RuntimeError, "INVALID_PUBLIC_KALSHI_PATH"):
                cc.kalshi_get(bad)


class EquivalenceAndIsolationTests(unittest.TestCase):
    def test_protocol_equivalence_all_five_tasks(self) -> None:
        for city_id in cc.TASK_INDEX_TO_CITY:
            result = checks.replay_city(POLICY, city_id)
            failed = [k for k, v in result.items() if k.endswith("_equal") and not v]
            self.assertTrue(result["pass"], (city_id, failed))

    def test_failure_isolation_suite(self) -> None:
        suite = checks.isolation_suite(POLICY)
        self.assertTrue(suite["pass"], suite)

    def test_wrong_city_weather_is_rejected(self) -> None:
        for city_id in cc.TASK_INDEX_TO_CITY:
            for other in cc.TASK_INDEX_TO_CITY:
                if other == city_id:
                    continue
                store = fx.MemoryStore()
                status, _ = fx.run_fake_city(
                    POLICY, store, city_id, TARGET, NOW,
                    fx.shifted_rows(other, TARGET), checks.d1_market(POLICY, city_id, TARGET),
                )
                self.assertEqual(status, "FAILED", (city_id, other))
                self.assertNotIn(POLICY.artifact_path(city_id, TARGET, "forecast.json"), store.objects)


class AmendmentTests(unittest.TestCase):
    def test_amendment_is_hash_bound_and_append_only(self) -> None:
        source = Path(__file__).resolve().parent.parent / "wn_multicity_prospective_v1"
        body = (source / cc.AMENDMENT_NAME).read_bytes()
        self.assertEqual(cc.sha(body), cc.AMENDMENT_SHA256)
        self.assertEqual(
            (source / "pre_first_event_amendment_v1.sha256").read_text().split()[0], cc.AMENDMENT_SHA256
        )
        amendment = json.loads(body)
        self.assertEqual(amendment["frozen_protocol_sha256"], POLICY.PROTOCOL_SHA256)
        self.assertEqual(amendment["d1_files_modified"], [])
        self.assertEqual(cc.sha((source / "SHA256SUMS.json").read_bytes()), cc.D1_SHA256SUMS_SHA256)
        original = cc.AMENDMENT_SHA256
        try:
            cc.AMENDMENT_SHA256 = "0" * 64
            with self.assertRaisesRegex(RuntimeError, "PRE_FIRST_EVENT_AMENDMENT_HASH_MISMATCH"):
                cc.verify_frozen_bundle()
        finally:
            cc.AMENDMENT_SHA256 = original

    def test_amendment_semantics_all_five_cities(self) -> None:
        for city_id in cc.TASK_INDEX_TO_CITY:
            suite = checks.amendment_suite(POLICY, city_id)
            failed = [name for name, case in suite["cases"].items() if not case["pass"]]
            self.assertEqual(failed, [], city_id)

    def test_executability_status(self) -> None:
        full = {"yes_dollars": [["0.1", "1"]], "no_dollars": [["0.8", "1"]]}
        self.assertEqual(cc.executability_status(full), cc.EXECUTABLE)
        for book in ({"yes_dollars": [], "no_dollars": [["0.8", "1"]]}, {"yes_dollars": [["0.1", "1"]]},
                     {"yes_dollars": None, "no_dollars": None}, {}, None, "x"):
            self.assertEqual(cc.executability_status(book), cc.ABSTAIN)


class SecrecyTests(unittest.TestCase):
    def test_error_codes_never_carry_free_text(self) -> None:
        self.assertEqual(cc.error_code(RuntimeError("DATA_NOT_READY_HOUR_COUNT")), "DATA_NOT_READY_HOUR_COUNT")
        leaked = cc.error_code(ValueError('refresh_token "1//0abc" rejected'))
        self.assertEqual(leaked, "UNCLASSIFIED:ValueError")

    def test_credential_loader_fails_closed_without_reading_a_secret(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "OAUTH_SECRET_MOUNT_UNREADABLE"):
            cc.load_user_credentials()
        self.assertEqual(cc.READONLY_SCOPE, "https://www.googleapis.com/auth/earthengine.readonly")

    def test_source_has_no_outcome_trading_or_broad_scope_path(self) -> None:
        here = Path(__file__).resolve().parent
        for name in ("cloud_collector.py", "canary.py"):
            text = (here / name).read_text()
            for forbidden in ("/portfolio", "orders", "KALSHI-ACCESS", "drive", "gmail", "cloud-platform",
                              "settlements", "candlesticks", "wn_ee_", "wn-ee-", "KXHIGHCHI"):
                self.assertNotIn(forbidden, text, (name, forbidden))


if __name__ == "__main__":
    unittest.main()
