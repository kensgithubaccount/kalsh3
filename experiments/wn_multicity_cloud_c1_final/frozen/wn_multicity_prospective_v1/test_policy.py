"""Offline policy tests using synthetic rows and a real non-current historical fixture."""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import policy


class FakeStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def exists(self, name: str) -> bool:
        return name in self.objects

    def create(self, name: str, payload: bytes) -> None:
        if name in self.objects:
            raise FileExistsError(name)
        self.objects[name] = payload


def weather(city_id: str, target: date) -> list[dict[str, Any]]:
    city = policy.CITIES[city_id]
    init = datetime(target.year, target.month, target.day, tzinfo=UTC)
    start, _ = policy.target_window(city_id, target)
    first = city["required_forecast_hours_inclusive"][0]
    return [
        {
            "city_id": city_id,
            "station_icao": city["station_icao"],
            "grid_lonlat": city["reviewed_grid_lonlat"],
            "init_time_utc": policy.stamp(init),
            "valid_time_utc": policy.stamp(start + timedelta(hours=i)),
            "forecast_hour": first + i,
            "ingestion_time_utc": policy.stamp(init + timedelta(hours=8)),
            "station_head_temperature_2m_p50": str(
                Decimal("280") + Decimal(i) / 10 if i != 17 else Decimal("305.25")
            ),
        }
        for i in range(24)
    ]


def market(city_id: str, target: date) -> dict[str, Any]:
    city = policy.CITIES[city_id]
    event = city["series_ticker"] + "-" + target.strftime("%y%b%d").upper()
    due = datetime(target.year, target.month, target.day, 9, tzinfo=UTC)
    kinds = ["less", "between", "between", "between", "between", "greater"]
    base = 70
    rows = []
    for i, kind in enumerate(kinds):
        floor = base + 2 * (i - 1)
        suffix = (
            f"B{floor}.5"
            if kind == "between"
            else (f"T{base}" if kind == "less" else f"T{base + 7}")
        )
        row = {
            "ticker": event + "-" + suffix,
            "strike_type": kind,
            "rules_primary": (
                f"If the maximum temperature recorded at {city['city']} "
                f"({city['cli_product']}) for {target:%b} {target.day}, {target.year} "
                "is in range according to The Weather Company"
            ),
            "status": "active",
            "open_time": policy.stamp(due - timedelta(hours=1)),
            "close_time": policy.stamp(due + timedelta(hours=1)),
            "retrieved_at_utc": policy.stamp(due + timedelta(seconds=10)),
            "yes_bid_dollars": "0.10",
            "yes_ask_dollars": "0.12",
            "yes_bid_size_fp": "1",
            "yes_ask_size_fp": "1",
            "orderbook_fp": {"yes_dollars": [["0.10", "1"]], "no_dollars": [["0.88", "1"]]},
        }
        if kind == "less":
            row["cap_strike"] = base
        elif kind == "greater":
            row["floor_strike"] = base + 7
        else:
            row["floor_strike"] = floor
            row["cap_strike"] = floor + 1
        rows.append(row)
    return {
        "event_ticker": event,
        "series_ticker": city["series_ticker"],
        "settlement_sources": [
            {"name": "The Weather Company", "url": "https://weather.com/kalshi"}
        ],
        "markets": rows,
    }


class PolicyTests(unittest.TestCase):
    def test_city_windows_and_exact_historical_00z_coverage(self) -> None:
        historical = date(2026, 9, 27)
        source = json.loads(
            (
                Path(__file__).resolve().parent.parent
                / "wn_multicity_a0"
                / "weathernext_feasibility.json"
            ).read_text()
        )
        actual_fixture = json.loads(
            (Path(__file__).resolve().parent / "historical_point_fixture_20260927.json").read_text()
        )
        observed = set(source["hour_axis_observed"])
        expected_starts = {"boston": 5, "miami": 5, "denver": 7, "los_angeles": 8, "seattle": 8}
        historical_max_kelvin = {
            "boston": "289.0198974609375",
            "miami": "304.6248779296875",
            "denver": "301.12823486328125",
            "los_angeles": "300.14788818359375",
            "seattle": "291.26678466796875",
        }
        for city_id, first in expected_starts.items():
            with self.subTest(city=city_id):
                start, end = policy.target_window(city_id, historical)
                self.assertEqual(start.hour, first)
                self.assertEqual(end - start, timedelta(hours=24))
                self.assertEqual(set(range(first, first + 24)) - observed, set())
                rows = weather(city_id, historical)
                self.assertEqual(
                    [row["valid_time_utc"] for row in rows],
                    [policy.stamp(start + timedelta(hours=i)) for i in range(24)],
                )
                self.assertEqual(
                    policy.validate_weather(city_id, historical, rows), Decimal("305.25")
                )
                self.assertEqual(
                    policy.kelvin_to_fahrenheit(policy.validate_weather(city_id, historical, rows)),
                    Decimal("89.78"),
                )
                self.assertEqual(
                    policy.validate_weather(city_id, historical, actual_fixture["cities"][city_id]),
                    Decimal(historical_max_kelvin[city_id]),
                )

    def test_missing_duplicate_wrong_city_grid_and_late_ingestion_fail(self) -> None:
        target = date(2026, 9, 27)
        good = weather("boston", target)
        variants = []
        variants.append(good[:-1])
        duplicate = [dict(row) for row in good]
        duplicate[5] = dict(duplicate[4])
        variants.append(duplicate)
        wrong_city = [dict(row) for row in good]
        wrong_city[0]["station_icao"] = "KMIA"
        variants.append(wrong_city)
        wrong_grid = [dict(row) for row in good]
        wrong_grid[0]["grid_lonlat"] = policy.CITIES["miami"]["reviewed_grid_lonlat"]
        variants.append(wrong_grid)
        late = [dict(row) for row in good]
        late[0]["ingestion_time_utc"] = "2026-09-27T09:00:00Z"
        variants.append(late)
        for rows in variants:
            with self.subTest(variant=len(rows)), self.assertRaises(RuntimeError):
                policy.validate_weather("boston", target, rows)

    def test_allowlist_and_execution_clock(self) -> None:
        target = date(2026, 10, 3)
        due = datetime(2026, 10, 3, 9, tzinfo=UTC)
        for outside in (date(2026, 10, 2), date(2026, 10, 15)):
            with self.assertRaisesRegex(RuntimeError, "OUTSIDE_ALLOWLIST"):
                policy.gate("boston", outside, due)
        with self.assertRaisesRegex(RuntimeError, "BEFORE_DECISION"):
            policy.gate("boston", target, due - timedelta(microseconds=1))
        self.assertEqual(policy.gate("boston", target, due)[1], "TIMELY")
        self.assertEqual(policy.gate("boston", target, due + timedelta(minutes=5))[1], "LATE")
        store = FakeStore()
        self.assertFalse(policy.acquire_claim(store, "boston", target, due + timedelta(minutes=5)))
        self.assertEqual(
            json.loads(store.objects[policy.claim_path("boston", target)])["kind"],
            "MISSED_NO_ACQUISITION",
        )
        self.assertFalse(policy.acquire_claim(store, "boston", target, due))

    def test_duplicate_claim_and_existing_artifact_protection(self) -> None:
        target = date(2026, 10, 3)
        due = datetime(2026, 10, 3, 9, tzinfo=UTC)
        store = FakeStore()
        self.assertTrue(policy.acquire_claim(store, "seattle", target, due))
        self.assertFalse(policy.acquire_claim(store, "seattle", target, due))
        other = FakeStore()
        packet = policy.artifact_path("seattle", target, "packet.json")
        other.create(packet, b"original")
        self.assertFalse(policy.acquire_claim(other, "seattle", target, due))
        with self.assertRaises(FileExistsError):
            other.create(packet, b"replacement")
        self.assertEqual(other.objects[packet], b"original")

    def test_one_city_failure_isolation_and_correlation_labels(self) -> None:
        target = date(2026, 10, 3)
        due = datetime(2026, 10, 3, 9, tzinfo=UTC)
        store = FakeStore()
        bad = weather("boston", target)
        bad[0]["grid_lonlat"] = policy.CITIES["miami"]["reviewed_grid_lonlat"]
        self.assertEqual(
            policy.run_offline_city(store, "boston", target, due, bad, market("boston", target)),
            "FAILED_NO_RETRY",
        )
        self.assertEqual(
            policy.run_offline_city(
                store, "miami", target, due, weather("miami", target), market("miami", target)
            ),
            "OFFLINE_PACKET_SEALED",
        )
        packet = json.loads(store.objects[policy.artifact_path("miami", target, "packet.json")])
        self.assertEqual(packet["observational_unit"], "CITY_DAY")
        self.assertFalse(packet["city_days_independent"])
        self.assertEqual(packet["same_date_cluster"], target.isoformat())
        self.assertEqual(packet["forecast_p50_proxy_kelvin"], "305.25")
        self.assertEqual(packet["forecast_p50_proxy_fahrenheit"], "89.78")
        for name in ("weather.json", "market.json", "forecast.json", "packet.json"):
            self.assertEqual(
                json.loads(store.objects[policy.artifact_path("miami", target, name)])[
                    "observational_unit"
                ],
                "CITY_DAY",
            )
        for name, digest in packet["component_sha256"].items():
            self.assertEqual(
                hashlib.sha256(
                    store.objects[policy.artifact_path("miami", target, name)]
                ).hexdigest(),
                digest,
            )
        self.assertEqual(
            json.loads(store.objects[policy.claim_path("boston", target)])["observational_unit"],
            "CITY_DAY",
        )
        self.assertEqual(
            json.loads(store.objects[policy.artifact_path("boston", target, "failure.json")])[
                "observational_unit"
            ],
            "CITY_DAY",
        )
        self.assertNotIn(policy.artifact_path("boston", target, "packet.json"), store.objects)

    def test_storage_failure_in_one_city_does_not_block_other_city(self) -> None:
        target = date(2026, 10, 3)
        due = policy.decision_at(target)

        class FailingStore(FakeStore):
            def create(self, name: str, payload: bytes) -> None:
                if name.startswith("city_days/boston/"):
                    raise OSError("injected city-local storage failure")
                super().create(name, payload)

        store = FailingStore()
        with self.assertRaises(OSError):
            policy.run_offline_city(
                store, "boston", target, due, weather("boston", target), market("boston", target)
            )
        self.assertEqual(
            policy.run_offline_city(
                store, "miami", target, due, weather("miami", target), market("miami", target)
            ),
            "OFFLINE_PACKET_SEALED",
        )
        self.assertIn(policy.claim_path("boston", target), store.objects)
        self.assertIn(policy.artifact_path("miami", target, "packet.json"), store.objects)
        self.assertNotIn(policy.artifact_path("boston", target, "packet.json"), store.objects)

    def test_market_source_and_ladder_cross_city_rejection(self) -> None:
        target = date(2026, 10, 3)
        good = market("boston", target)
        policy.validate_market("boston", target, good)
        wrong_source = json.loads(json.dumps(good))
        wrong_source["settlement_sources"][0]["name"] = "Other"
        wrong_city = json.loads(json.dumps(good))
        wrong_city["markets"][0]["rules_primary"] = wrong_city["markets"][0][
            "rules_primary"
        ].replace("Boston (CLIBOS)", "Miami (CLIMIA)")
        gap = json.loads(json.dumps(good))
        gap["markets"][2]["floor_strike"] = 73
        wrong_ticker = json.loads(json.dumps(good))
        wrong_ticker["markets"][2]["ticker"] = wrong_ticker["markets"][2]["ticker"].replace(
            "B72.5", "B72.0"
        )
        leaked = json.loads(json.dumps(good))
        leaked["markets"][0]["expiration_value"] = "60.00"
        for variant in (wrong_source, wrong_city, gap, wrong_ticker, leaked):
            with self.assertRaises(RuntimeError):
                policy.validate_market("boston", target, variant)
        self.assertEqual(policy.canonical_integer_fahrenheit("60.00"), 60)
        with self.assertRaisesRegex(RuntimeError, "NON_INTEGER"):
            policy.canonical_integer_fahrenheit("60.25")

    def test_immutable_gcs_paths(self) -> None:
        target = date(2026, 10, 3)
        self.assertEqual(
            policy.claim_path("los_angeles", target), "claims/los_angeles/20261003.json"
        )
        self.assertNotEqual(
            policy.claim_path("los_angeles", target), policy.claim_path("seattle", target)
        )
        self.assertEqual(
            policy.artifact_path("los_angeles", target, "packet.json"),
            "city_days/los_angeles/20261003/packet.json",
        )
        with self.assertRaisesRegex(RuntimeError, "INVALID_EVIDENCE_PATH"):
            policy.artifact_path("seattle", target, "../packet.json")

        class Blob:
            def __init__(self, objects: dict[str, bytes], name: str) -> None:
                self.objects, self.name = objects, name

            def exists(self) -> bool:
                return self.name in self.objects

            def upload_from_string(
                self, payload: bytes, *, if_generation_match: int, retry: object, **_: object
            ) -> None:
                if if_generation_match != 0 or retry is not None:
                    raise AssertionError("GCS_IMMUTABILITY_OR_RETRY_POLICY")
                if self.name in self.objects:
                    raise FileExistsError(self.name)
                self.objects[self.name] = payload

        class Bucket:
            def __init__(self) -> None:
                self.objects: dict[str, bytes] = {}

            def blob(self, name: str) -> Blob:
                return Blob(self.objects, name)

        bucket = Bucket()
        gcs = policy.GcsCreateOnly(bucket)
        path = policy.claim_path("seattle", target)
        gcs.create(path, b"claim")
        self.assertEqual(bucket.objects["wn_multicity_prospective_v1/v1/" + path], b"claim")
        with self.assertRaises(FileExistsError):
            gcs.create(path, b"different")
        with self.assertRaisesRegex(RuntimeError, "UNAPPROVED_GCS_PREFIX"):
            policy.GcsCreateOnly(bucket, "chicago")


if __name__ == "__main__":
    unittest.main()
