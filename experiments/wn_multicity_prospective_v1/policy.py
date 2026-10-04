"""Offline multicity development policy. No external acquisition or scheduler entry point."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parent
MANIFEST_BYTES = (ROOT / "freeze_manifest.json").read_bytes()
MANIFEST_SHA256 = hashlib.sha256(MANIFEST_BYTES).hexdigest()
if (ROOT / "freeze_manifest.sha256").read_text().split()[0] != MANIFEST_SHA256:
    raise RuntimeError("EFFECTIVE_POLICY_MANIFEST_HASH_MISMATCH")
for _relative, _digest in json.loads(MANIFEST_BYTES)["sha256"].items():
    if hashlib.sha256((ROOT / _relative).read_bytes()).hexdigest() != _digest:
        raise RuntimeError("EFFECTIVE_POLICY_COMPONENT_HASH_MISMATCH:" + _relative)
PROTOCOL_BYTES = (ROOT / "protocol.json").read_bytes()
PROTOCOL_SHA256 = hashlib.sha256(PROTOCOL_BYTES).hexdigest()
PROTOCOL = json.loads(PROTOCOL_BYTES)
AMENDMENT = json.loads((ROOT / "decision_time_amendment.json").read_text())
UNITS = json.loads((ROOT / "forecast_units_amendment.json").read_text())
DOMAIN = json.loads((ROOT / "settlement_domain_amendment.json").read_text())
IDENTITIES = json.loads((ROOT / "city_identity_amendment.json").read_text())
GRID = json.loads((ROOT / "grid_identity_amendment.json").read_text())
if any(
    item["frozen_protocol_sha256"] != PROTOCOL_SHA256
    for item in (AMENDMENT, UNITS, DOMAIN, IDENTITIES, GRID)
):
    raise RuntimeError("PROTOCOL_AMENDMENT_HASH_MISMATCH")
CITIES = {city["city_id"]: city for city in PROTOCOL["cities"]}
if set(IDENTITIES["cities"]) != set(CITIES):
    raise RuntimeError("PER_CITY_IDENTITY_SET_MISMATCH")
for city_id, city in CITIES.items():
    identity = IDENTITIES["cities"][city_id]
    if (
        identity["settlement_source"] != "The Weather Company"
        or identity["settlement_window_version"] != PROTOCOL["settlement_window_version"]
        or city["cli_product"] not in identity["settlement_product"]
        or city["station_icao"] not in identity["settlement_product"]
    ):
        raise RuntimeError("PER_CITY_SOURCE_WINDOW_MISMATCH")
ALLOWLIST = frozenset(date.fromisoformat(value) for value in PROTOCOL["proposed_block_dates"])
DECISION_CLOCK = time.fromisoformat(AMENDMENT["revised_common_decision_time_utc"].removesuffix("Z"))
CLOSE_CLOCK = time.fromisoformat(
    AMENDMENT["scientific_start_close_exclusive_utc"].removesuffix("Z")
)
if (
    AMENDMENT["revised_common_decision_time_utc"][-1:] != "Z"
    or AMENDMENT["scientific_start_close_exclusive_utc"][-1:] != "Z"
):
    raise RuntimeError("AMENDMENT_CLOCK_NOT_UTC")
if datetime.combine(date(2026, 1, 1), CLOSE_CLOCK) - datetime.combine(
    date(2026, 1, 1), DECISION_CLOCK
) != timedelta(minutes=5):
    raise RuntimeError("AMENDMENT_GATE_WIDTH_MISMATCH")


def decision_at(target: date) -> datetime:
    return datetime.combine(target, DECISION_CLOCK, tzinfo=UTC)


class CreateOnly(Protocol):
    def create(self, name: str, payload: bytes) -> None: ...
    def exists(self, name: str) -> bool: ...


class GcsCreateOnly:
    """Separate-prefix GCS transport for a future paused job, with no implicit retry."""

    def __init__(self, bucket: Any, prefix: str = "wn_multicity_prospective_v1/v1") -> None:
        if prefix != "wn_multicity_prospective_v1/v1":
            raise RuntimeError("UNAPPROVED_GCS_PREFIX")
        self.bucket = bucket
        self.prefix = prefix + "/"

    def _name(self, relative: str) -> str:
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or not relative or "//" in relative:
            raise RuntimeError("INVALID_GCS_PATH")
        return self.prefix + relative

    def exists(self, name: str) -> bool:
        return bool(self.bucket.blob(self._name(name)).exists())

    def create(self, name: str, payload: bytes) -> None:
        try:
            self.bucket.blob(self._name(name)).upload_from_string(
                payload, content_type="application/json", if_generation_match=0, retry=None
            )
        except Exception as exc:
            if type(exc).__name__ == "PreconditionFailed":
                raise FileExistsError(name) from exc
            raise


def encode(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def stamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("NAIVE_TIME")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def correlation(city_id: str, target: date) -> dict[str, Any]:
    city = CITIES[city_id]
    return {
        "observational_unit": "CITY_DAY",
        "city_days_independent": False,
        "city_id": city_id,
        "target_date_utc": target.isoformat(),
        "same_date_cluster": target.isoformat(),
        "geographic_group": city["geographic_group"],
        "weather_regime_group": city["weather_regime_group"],
    }


def kelvin_to_fahrenheit(value: Decimal) -> Decimal:
    return (value - Decimal("273.15")) * Decimal(9) / Decimal(5) + Decimal(32)


def grid_matches(actual: Any, expected: list[float]) -> bool:
    """Allow only Earth Engine floating-point representation noise at a frozen cell."""
    return (
        isinstance(actual, list)
        and len(actual) == 2
        and all(
            abs(float(a) - float(e)) <= GRID["allowed_coordinate_difference_degrees"]
            for a, e in zip(actual, expected, strict=True)
        )
    )


def target_window(city_id: str, target: date) -> tuple[datetime, datetime]:
    city = CITIES[city_id]
    start_hour = city["required_forecast_hours_inclusive"][0]
    init = datetime(target.year, target.month, target.day, tzinfo=UTC)
    start = init + timedelta(hours=start_hour)
    end = start + timedelta(hours=24)
    if (
        stamp(start)[11:] != city["target_start_utc"]
        or stamp(end)[11:] != city["target_end_exclusive_next_date_utc"]
    ):
        raise RuntimeError("WINDOW_IDENTITY_MISMATCH")
    return start, end


def gate(city_id: str, target: date, now: datetime) -> tuple[datetime, str]:
    if city_id not in CITIES:
        raise RuntimeError("UNAPPROVED_CITY")
    if target not in ALLOWLIST:
        raise RuntimeError("OUTSIDE_ALLOWLIST")
    if now.tzinfo is None:
        raise RuntimeError("NAIVE_CLOCK")
    now = now.astimezone(UTC)
    due = decision_at(target)
    if now.date() != target:
        raise RuntimeError("WRONG_EXECUTION_DATE")
    if now < due:
        raise RuntimeError("BEFORE_DECISION")
    return due, "LATE" if now >= due + timedelta(minutes=5) else "TIMELY"


def claim_path(city_id: str, target: date) -> str:
    if city_id not in CITIES or target not in ALLOWLIST:
        raise RuntimeError("INVALID_CLAIM_IDENTITY")
    return f"claims/{city_id}/{target:%Y%m%d}.json"


def artifact_path(city_id: str, target: date, name: str) -> str:
    if (
        city_id not in CITIES
        or target not in ALLOWLIST
        or name
        not in {"weather.json", "market.json", "forecast.json", "packet.json", "failure.json"}
    ):
        raise RuntimeError("INVALID_EVIDENCE_PATH")
    return f"city_days/{city_id}/{target:%Y%m%d}/{name}"


def acquire_claim(store: CreateOnly, city_id: str, target: date, now: datetime) -> bool:
    due, timing = gate(city_id, target, now)
    if any(
        store.exists(artifact_path(city_id, target, name))
        for name in ("packet.json", "forecast.json", "weather.json", "market.json", "failure.json")
    ):
        return False
    payload = {
        "record_type": "WN-MULTICITY-D1-CLAIM-v1",
        **correlation(city_id, target),
        "kind": "MISSED_NO_ACQUISITION" if timing == "LATE" else "ONE_SCIENTIFIC_ATTEMPT",
        "observed_at_utc": stamp(now),
        "decision_at_utc": stamp(due),
        "protocol_sha256": PROTOCOL_SHA256,
        "no_retry": True,
        "no_backfill": True,
    }
    try:
        store.create(claim_path(city_id, target), encode(payload))
        return timing == "TIMELY"
    except FileExistsError:
        return False


def validate_weather(city_id: str, target: date, rows: list[dict[str, Any]]) -> Decimal:
    city = CITIES[city_id]
    start, _ = target_window(city_id, target)
    init = datetime(target.year, target.month, target.day, tzinfo=UTC)
    due = decision_at(target)
    expected_hours = range(
        *[
            city["required_forecast_hours_inclusive"][0],
            city["required_forecast_hours_inclusive"][1] + 1,
        ]
    )
    if len(rows) != 24:
        raise RuntimeError("DATA_NOT_READY_HOUR_COUNT")
    values: list[Decimal] = []
    for index, (hour, row) in enumerate(zip(expected_hours, rows, strict=True)):
        expected_valid = start + timedelta(hours=index)
        if (
            row.get("city_id") != city_id
            or row.get("station_icao") != city["station_icao"]
            or row.get("forecast_hour") != hour
        ):
            raise RuntimeError("WRONG_CITY_OR_HOUR")
        if not grid_matches(row.get("grid_lonlat"), city["reviewed_grid_lonlat"]):
            raise RuntimeError("WRONG_GRID_POINT")
        if row.get("init_time_utc") != stamp(init) or row.get("valid_time_utc") != stamp(
            expected_valid
        ):
            raise RuntimeError("INIT_OR_VALID_TIME_MISMATCH")
        ingestion = datetime.fromisoformat(
            str(row.get("ingestion_time_utc", "")).replace("Z", "+00:00")
        )
        if ingestion.tzinfo is None or ingestion >= due:
            raise RuntimeError("DATA_NOT_READY_INGESTION")
        value = Decimal(str(row["station_head_temperature_2m_p50"]))
        if (
            not value.is_finite()
            or not math.isfinite(float(value))
            or not Decimal("150") < value < Decimal("350")
        ):
            raise RuntimeError("INVALID_P50")
        values.append(value)
    return max(values)


def validate_market(city_id: str, target: date, event: dict[str, Any]) -> None:
    city = CITIES[city_id]
    ticker = city["series_ticker"] + "-" + target.strftime("%y%b%d").upper()
    siblings = event.get("markets")
    if (
        event.get("event_ticker") != ticker
        or event.get("series_ticker") != city["series_ticker"]
        or not isinstance(siblings, list)
        or len(siblings) != 6
    ):
        raise RuntimeError("MARKET_IDENTITY_OR_SIBLINGS")
    if event.get("settlement_sources") != [
        {"name": "The Weather Company", "url": "https://weather.com/kalshi"}
    ]:
        raise RuntimeError("SETTLEMENT_SOURCE_MISMATCH")
    if len({row.get("ticker") for row in siblings}) != 6:
        raise RuntimeError("DUPLICATE_SIBLING")
    if sorted(row.get("strike_type") for row in siblings) != ["between"] * 4 + ["greater", "less"]:
        raise RuntimeError("MARKET_STRIKE_SHAPE")
    lower = next(row for row in siblings if row["strike_type"] == "less")
    upper = next(row for row in siblings if row["strike_type"] == "greater")
    middle = sorted(
        (row for row in siblings if row["strike_type"] == "between"),
        key=lambda row: Decimal(str(row["floor_strike"])),
    )
    if lower.get("cap_strike") is None or upper.get("floor_strike") is None:
        raise RuntimeError("MARKET_BOUNDS_MISSING")
    base = Decimal(str(lower["cap_strike"]))
    if base != base.to_integral_value() or Decimal(str(upper["floor_strike"])) != base + 7:
        raise RuntimeError("INTEGER_LADDER_GAP_OR_OVERLAP")
    if lower["ticker"] != f"{ticker}-T{base:.0f}" or upper["ticker"] != f"{ticker}-T{base + 7:.0f}":
        raise RuntimeError("TAIL_TICKER_BOUNDS_MISMATCH")
    for index, row in enumerate(middle):
        floor = base + 2 * index
        if (
            Decimal(str(row.get("floor_strike"))) != floor
            or Decimal(str(row.get("cap_strike"))) != floor + 1
        ):
            raise RuntimeError("INTEGER_LADDER_GAP_OR_OVERLAP")
        if row["ticker"] != f"{ticker}-B{floor + Decimal('0.5'):.1f}":
            raise RuntimeError("BUCKET_TICKER_BOUNDS_MISMATCH")
    for row in siblings:
        required_rule = (
            f"at {city['city']} ({city['cli_product']}) for {target:%b} {target.day}, {target.year}"
        )
        if (
            not row["ticker"].startswith(ticker + "-")
            or required_rule not in row.get("rules_primary", "")
            or "according to The Weather Company" not in row.get("rules_primary", "")
            or row.get("status") != "active"
        ):
            raise RuntimeError("MARKET_RULE_OR_STATE")
        if row.get("result") not in (None, "") or row.get("expiration_value") not in (None, ""):
            raise RuntimeError("OUTCOME_LEAKAGE")
        if not all(
            key in row
            for key in (
                "yes_bid_dollars",
                "yes_ask_dollars",
                "yes_bid_size_fp",
                "yes_ask_size_fp",
                "orderbook_fp",
                "retrieved_at_utc",
            )
        ):
            raise RuntimeError("MARKET_QUOTE_OR_DEPTH_MISSING")
        due = decision_at(target)
        opened = datetime.fromisoformat(row["open_time"].replace("Z", "+00:00"))
        closes = datetime.fromisoformat(row["close_time"].replace("Z", "+00:00"))
        retrieved = datetime.fromisoformat(row["retrieved_at_utc"].replace("Z", "+00:00"))
        if not opened <= due < closes or not due <= retrieved < due + timedelta(minutes=5):
            raise RuntimeError("MARKET_NOT_ACTIVE_AT_DECISION")
        if not all(
            key in row["orderbook_fp"]
            and isinstance(row["orderbook_fp"][key], list)
            and row["orderbook_fp"][key]
            for key in ("yes_dollars", "no_dollars")
        ):
            raise RuntimeError("MARKET_DEPTH_MISSING")
        kind = row["strike_type"]
        if kind == "between" and (row.get("floor_strike") is None or row.get("cap_strike") is None):
            raise RuntimeError("MARKET_BOUNDS_MISSING")
        if (kind == "less" and row.get("cap_strike") is None) or (
            kind == "greater" and row.get("floor_strike") is None
        ):
            raise RuntimeError("MARKET_BOUNDS_MISSING")


def canonical_integer_fahrenheit(value: str) -> int:
    """Only exact integer source values enter the conditional discrete settlement domain."""
    exact = Decimal(value)
    if not exact.is_finite() or exact != exact.to_integral_value():
        raise RuntimeError("NON_INTEGER_SETTLEMENT_UNSUPPORTED")
    return int(exact)


def run_offline_city(
    store: CreateOnly,
    city_id: str,
    target: date,
    now: datetime,
    rows: list[dict[str, Any]],
    event: dict[str, Any],
) -> str:
    """Exercise claim and evidence isolation with fixtures; not a live collector."""
    if not acquire_claim(store, city_id, target, now):
        return "DUPLICATE_OR_LATE"
    try:
        forecast = validate_weather(city_id, target, rows)
        validate_market(city_id, target, event)
        city = CITIES[city_id]
        common = {
            **correlation(city_id, target),
            "protocol_sha256": PROTOCOL_SHA256,
            "development_only": True,
        }
        weather_bytes = encode(
            {
                "record_type": "WN-MULTICITY-D1-OFFLINE-WEATHER-v1",
                **common,
                "station_icao": city["station_icao"],
                "grid_lonlat": city["reviewed_grid_lonlat"],
                "rows": rows,
            }
        )
        market_bytes = encode(
            {"record_type": "WN-MULTICITY-D1-OFFLINE-MARKET-v1", **common, "event": event}
        )
        forecast_bytes = encode(
            {
                "record_type": "WN-MULTICITY-D1-OFFLINE-FORECAST-v1",
                **common,
                "forecast_p50_proxy_kelvin": str(forecast),
                "forecast_p50_proxy_fahrenheit": str(kelvin_to_fahrenheit(forecast)),
                "weather_sha256": hashlib.sha256(weather_bytes).hexdigest(),
            }
        )
        store.create(artifact_path(city_id, target, "weather.json"), weather_bytes)
        store.create(artifact_path(city_id, target, "market.json"), market_bytes)
        store.create(artifact_path(city_id, target, "forecast.json"), forecast_bytes)
        packet = {
            "record_type": "WN-MULTICITY-D1-OFFLINE-PACKET-v1",
            **common,
            "station_icao": city["station_icao"],
            "cli_product": city["cli_product"],
            "settlement_source": "The Weather Company",
            "forecast_p50_proxy_kelvin": str(forecast),
            "forecast_p50_proxy_fahrenheit": str(kelvin_to_fahrenheit(forecast)),
            "component_sha256": {
                "weather.json": hashlib.sha256(weather_bytes).hexdigest(),
                "market.json": hashlib.sha256(market_bytes).hexdigest(),
                "forecast.json": hashlib.sha256(forecast_bytes).hexdigest(),
            },
        }
        store.create(artifact_path(city_id, target, "packet.json"), encode(packet))
        return "OFFLINE_PACKET_SEALED"
    except Exception as exc:
        store.create(
            artifact_path(city_id, target, "failure.json"),
            encode(
                {
                    **correlation(city_id, target),
                    "error_type": type(exc).__name__,
                    "status": "FAILED_NO_RETRY",
                }
            ),
        )
        return "FAILED_NO_RETRY"
