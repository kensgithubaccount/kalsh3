"""Cloud transport for the frozen five-city WN-MULTICITY-D1 development design.

Scientific rules (cities, grid points, windows, 09:00Z gate, allowlist, weather
and market validation, claim format, unit conversion) are imported from the
byte-identical frozen D1 bundle. This module only supplies the Cloud Run task
mapping, the early-wrapper wait, dedicated read-only OAuth, public Kalshi
transport, and create-only GCS evidence. No probability, scoring, outcome,
alert, or trading path exists here.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
FROZEN = HERE / "frozen" / "wn_multicity_prospective_v1"
D1_SHA256SUMS_SHA256 = "6d2c618e2ef0d99f87c12d50f41dfefa617f43cd7347aeef4c0a1d37c3ba3891"
AMENDMENT_NAME = "pre_first_event_amendment_v1.json"
AMENDMENT_SHA256 = "b6ce8154d3a3eb4a90e1eefb3cd57b879a818eabee22b8f8c8fe2dcd7614f000"
EXECUTABLE = "EXECUTABLE_MARKET_AVAILABLE"
ABSTAIN = "ABSTAIN_INSUFFICIENT_EXECUTABLE_MARKET"
PROJECT = "total-market-138523"
BUCKET = "wn-multicity-c1-354269857197-20261001"
SECRET_MOUNT = Path("/var/run/wn-multicity-oauth/credentials")
SECRET_SHA256 = "95be9f8c6636963b28adc71bb8068028c288080952315e8748bf83d486dae94e"
READONLY_SCOPE = "https://www.googleapis.com/auth/earthengine.readonly"
KALSHI_HOST = "external-api.kalshi.com"
EARLY_WRAPPER_MAX = timedelta(minutes=15)
DELAYED_OFFSET = timedelta(minutes=5)
DELAYED_GRACE = timedelta(seconds=30)
PRIMARY_BAND = "station_head_temperature_2m_p50"
DIAGNOSTIC_BANDS = (
    "station_head_temperature_2m_mean",
    "station_head_temperature_2m_p10",
    "station_head_temperature_2m_p90",
)
BANDS = (PRIMARY_BAND, *DIAGNOSTIC_BANDS)

# Frozen Cloud Run task-index mapping. Never derive a city from anything else.
TASK_INDEX_TO_CITY = ("boston", "miami", "denver", "los_angeles", "seattle")
# Second, independent statement of the D1 identities; any disagreement with the
# hash-bound protocol stops the task before the gate.
EXPECTED_IDENTITY = {
    "boston": ("KXHIGHTBOS", "CLIBOS", "KBOS", (-71.0, 42.35), (5, 28)),
    "miami": ("KXHIGHMIA", "CLIMIA", "KMIA", (-80.3, 25.8), (5, 28)),
    "denver": ("KXHIGHDEN", "CLIDEN", "KDEN", (-104.65, 39.85), (7, 30)),
    "los_angeles": ("KXHIGHLAX", "CLILAX", "KLAX", (-118.4, 33.95), (8, 31)),
    "seattle": ("KXHIGHTSEA", "CLISEA", "KSEA", (-122.3, 47.45), (8, 31)),
}
EXPECTED_ALLOWLIST = tuple(date(2026, 10, day) for day in range(3, 15))
SETTLEMENT_SOURCES = [{"name": "The Weather Company", "url": "https://weather.com/kalshi"}]
# Exactly the four read-only shapes the collector builds: event, series, market, orderbook.
PUBLIC_PATH = re.compile(
    r"^/trade-api/v2/(events/[A-Z0-9-]+|series/[A-Z0-9]+|markets/[A-Z0-9.-]+"
    r"|markets/orderbooks\?tickers=[A-Z0-9.-]+)$"
)
SAFE_CODE = re.compile(r"^[A-Z][A-Z0-9_]*(:[A-Za-z0-9_.\-]+)?$")


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def verify_frozen_bundle() -> None:
    sums_bytes = (FROZEN / "SHA256SUMS.json").read_bytes()
    if sha(sums_bytes) != D1_SHA256SUMS_SHA256:
        raise RuntimeError("D1_SHA256SUMS_HASH_MISMATCH")
    sums = json.loads(sums_bytes)["sha256"]
    present = {path.name for path in FROZEN.iterdir() if path.is_file()}
    if present != set(sums) | {
        "SHA256SUMS.json",
        "SHA256SUMS.sha256",
        AMENDMENT_NAME,
        "pre_first_event_amendment_v1.sha256",
    }:
        raise RuntimeError("D1_BUNDLE_FILE_SET_MISMATCH")
    if sha((FROZEN / AMENDMENT_NAME).read_bytes()) != AMENDMENT_SHA256:
        raise RuntimeError("PRE_FIRST_EVENT_AMENDMENT_HASH_MISMATCH")
    for relative, expected in sums.items():
        if sha((FROZEN / relative).read_bytes()) != expected:
            raise RuntimeError("D1_BUNDLE_HASH_MISMATCH:" + relative)


def load_policy() -> Any:
    """Import the frozen D1 policy and cross-check every cloud-side constant against it."""
    verify_frozen_bundle()
    if str(FROZEN) not in sys.path:
        sys.path.insert(0, str(FROZEN))
    import policy  # type: ignore[import-not-found]

    if Path(policy.__file__).resolve().parent != FROZEN:
        raise RuntimeError("POLICY_NOT_LOADED_FROM_FROZEN_BUNDLE")
    design = json.loads((FROZEN / "cloud_design.json").read_text())
    if (
        tuple(policy.CITIES) != TASK_INDEX_TO_CITY
        or design["task_index_to_city"]
        != {str(index): city for index, city in enumerate(TASK_INDEX_TO_CITY)}
        or design["task_count"] != len(TASK_INDEX_TO_CITY)
        or design["cloud_run_task_max_retries"] != 0
    ):
        raise RuntimeError("TASK_MAPPING_MISMATCH")
    for city_id, (series, cli, icao, grid, hours) in EXPECTED_IDENTITY.items():
        city = policy.CITIES[city_id]
        if (
            city["series_ticker"] != series
            or city["cli_product"] != cli
            or city["station_icao"] != icao
            or tuple(city["reviewed_grid_lonlat"]) != grid
            or tuple(city["required_forecast_hours_inclusive"]) != hours
        ):
            raise RuntimeError("CITY_IDENTITY_MISMATCH:" + city_id)
    if policy.ALLOWLIST != frozenset(EXPECTED_ALLOWLIST):
        raise RuntimeError("ALLOWLIST_MISMATCH")
    if policy.DECISION_CLOCK.isoformat() != "09:00:00" or policy.CLOSE_CLOCK.isoformat() != "09:05:00":
        raise RuntimeError("DECISION_CLOCK_MISMATCH")
    if policy.PROTOCOL["weather_variable"] != PRIMARY_BAND:
        raise RuntimeError("WEATHER_VARIABLE_MISMATCH")
    amendment = json.loads((FROZEN / AMENDMENT_NAME).read_text())
    statuses = amendment["clarifications"][
        "A_weather_development_observation_vs_market_executability"
    ]["per_market_status"]
    if (
        amendment["status"] != "FROZEN_BEFORE_FIRST_EVENT"
        or amendment["frozen_protocol_sha256"] != policy.PROTOCOL_SHA256
        or amendment["d1_sha256sums_sha256"] != D1_SHA256SUMS_SHA256
        or amendment["d1_files_modified"] != []
        or set(statuses) != {EXECUTABLE, ABSTAIN}
    ):
        raise RuntimeError("PRE_FIRST_EVENT_AMENDMENT_BINDING_MISMATCH")
    return policy


def city_for_task(environ: Mapping[str, str]) -> tuple[int, str]:
    if environ.get("CLOUD_RUN_TASK_COUNT") != str(len(TASK_INDEX_TO_CITY)):
        raise RuntimeError("TASK_COUNT_NOT_FIVE")
    raw = environ.get("CLOUD_RUN_TASK_INDEX")
    if raw not in {str(index) for index in range(len(TASK_INDEX_TO_CITY))}:
        raise RuntimeError("TASK_INDEX_INVALID")
    if environ.get("CLOUD_RUN_TASK_ATTEMPT") != "0":
        raise RuntimeError("TASK_RETRY_PROHIBITED")
    index = int(raw)
    return index, TASK_INDEX_TO_CITY[index]


def wait_until(
    due: datetime,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    while True:
        remaining = (due - clock()).total_seconds()
        if remaining <= 0:
            return
        sleep(min(remaining, 1.0))


class CityStore:
    """Create-only view that can address exactly one city/date's own keys."""

    def __init__(self, inner: Any, policy: Any, city_id: str, target: date) -> None:
        self.inner = inner
        self.claim = policy.claim_path(city_id, target)
        self.directory = f"city_days/{city_id}/{target:%Y%m%d}/"
        self.hashes: dict[str, str] = {}

    def _check(self, name: str) -> str:
        parts = name.split("/")
        if name != self.claim and not (
            name.startswith(self.directory) and all(parts) and ".." not in parts
        ):
            raise RuntimeError("CROSS_CITY_OR_INVALID_EVIDENCE_PATH")
        return name

    def exists(self, name: str) -> bool:
        return bool(self.inner.exists(self._check(name)))

    def create(self, name: str, payload: bytes) -> None:
        self.inner.create(self._check(name), payload)
        self.hashes[name] = sha(payload)

    def path(self, name: str) -> str:
        return self._check(self.directory + name)


def rows_from_features(policy: Any, city_id: str, raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Same row construction as the frozen D1 fixture builder, plus diagnostic bands."""
    city = policy.CITIES[city_id]
    rows = []
    for feature in raw["features"]:
        props = feature["properties"]
        row = {
            "city_id": city_id,
            "station_icao": city["station_icao"],
            "grid_lonlat": feature["geometry"]["coordinates"],
            "init_time_utc": props["start_time"],
            "valid_time_utc": props["end_time"],
            "forecast_hour": int(props["forecast_hour"]),
            "ingestion_time_utc": datetime.fromtimestamp(float(props["ingestion_time_utc"]), UTC)
            .isoformat()
            .replace("+00:00", "Z"),
        }
        for band in BANDS:
            row[band] = props[band]
        rows.append(row)
    return rows


def ee_weather(ee: Any, policy: Any, city_id: str, target: date) -> dict[str, Any]:
    """Image-count check, then one 00Z-init point sample at the frozen station for the city's 24 hours."""
    city = policy.CITIES[city_id]
    first, last = city["required_forecast_hours_inclusive"]
    init = datetime(target.year, target.month, target.day, tzinfo=UTC)
    images = (
        ee.ImageCollection(policy.PROTOCOL["weather_asset"])
        .filter(ee.Filter.eq("start_time", policy.stamp(init)))
        .filter(ee.Filter.gte("forecast_hour", first))
        .filter(ee.Filter.lte("forecast_hour", last))
        .sort("forecast_hour")
    )
    if images.size().getInfo() != 24:
        raise RuntimeError("DATA_NOT_READY_IMAGE_COUNT")
    station_point = ee.Geometry.Point(city["station_lonlat"])
    projection = ee.Image(images.first()).select(PRIMARY_BAND).projection()

    def sample(image: Any) -> Any:
        point = (
            image.select(list(BANDS))
            .sample(region=station_point, projection=projection, numPixels=1, geometries=True)
            .first()
        )
        return ee.Feature(
            point.geometry(),
            image.toDictionary(
                ["start_time", "end_time", "forecast_hour", "ingestion_time_utc"]
            ).combine(point.toDictionary(), overwrite=True),
        )

    result: dict[str, Any] = (
        ee.FeatureCollection(images.map(sample)).sort("forecast_hour").getInfo()
    )
    return result


def proxy(policy: Any, rows: list[dict[str, Any]], band: str) -> dict[str, Any]:
    maximum = max(Decimal(str(row[band])) for row in rows)
    return {
        "kelvin": str(maximum),
        "celsius": str(maximum - Decimal("273.15")),
        "fahrenheit": str(policy.kelvin_to_fahrenheit(maximum)),
        "argmax": [
            {"valid_time_utc": row["valid_time_utc"], "forecast_hour": row["forecast_hour"]}
            for row in rows
            if Decimal(str(row[band])) == maximum
        ],
    }


def validate_diagnostics(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        for band in DIAGNOSTIC_BANDS:
            value = Decimal(str(row[band]))
            if not value.is_finite() or not Decimal("150") < value < Decimal("350"):
                raise RuntimeError("DATA_NOT_READY_INVALID_DIAGNOSTIC_BAND")


def kalshi_get(path: str) -> dict[str, Any]:
    """Unauthenticated public GET; no account, order, or outcome endpoint is reachable."""
    import http.client
    import ssl

    if not PUBLIC_PATH.match(path):
        raise RuntimeError("INVALID_PUBLIC_KALSHI_PATH")
    connection = http.client.HTTPSConnection(
        KALSHI_HOST, timeout=10, context=ssl.create_default_context()
    )
    observed = datetime.now(UTC)
    try:
        connection.request("GET", path, headers={"Accept": "application/json"})
        response = connection.getresponse()
        body = response.read(8_000_001)
        if response.status != 200 or len(body) > 8_000_000:
            raise RuntimeError("KALSHI_PUBLIC_GET_FAILED")
    finally:
        connection.close()
    return {
        "path": path,
        "observed_at": observed.isoformat().replace("+00:00", "Z"),
        "status": response.status,
        "body_sha256": sha(body),
        "raw_body_b64": base64.b64encode(body).decode("ascii"),
        "payload": json.loads(body),
    }


def executability_status(book: Any) -> str:
    """Amendment v1 clarification A: both book sides non-empty, else abstain for that market."""
    two_sided = isinstance(book, dict) and all(
        isinstance(book.get(side), list) and book[side] for side in ("yes_dollars", "no_dollars")
    )
    return EXECUTABLE if two_sided else ABSTAIN


def depth_neutral_view(event: dict[str, Any]) -> dict[str, Any]:
    """Copy for the frozen validator in which only the superseded depth check cannot fail.

    Every other frozen validate_market condition sees the real event and market fields.
    The real books are never altered; this view is discarded after validation.
    """
    filler = [["DEPTH_CHECK_SUPERSEDED_BY_PRE_FIRST_EVENT_AMENDMENT_V1"]]
    return {
        **event,
        "markets": [
            {**row, "orderbook_fp": {"yes_dollars": filler, "no_dollars": filler}}
            for row in event["markets"]
        ],
    }


def acquire_market(
    policy: Any,
    city_id: str,
    target: date,
    get: Callable[[str], dict[str, Any]],
    clock: Callable[[], datetime],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Discover this city's exact event and six siblings, then apply frozen D1 validation.

    Per pre-first-event amendment v1, missing or one-sided depth is recorded per market
    and does not fail the city-day; all other frozen market checks still do.
    """
    city = policy.CITIES[city_id]
    series_ticker = city["series_ticker"]
    event_ticker = series_ticker + "-" + target.strftime("%y%b%d").upper()
    payload = get("/trade-api/v2/events/" + quote(event_ticker, safe=""))["payload"]
    event, listed = payload.get("event"), payload.get("markets")
    if not isinstance(event, dict) or event.get("event_ticker") != event_ticker:
        raise RuntimeError("MARKET_EVENT_IDENTITY_MISMATCH")
    if not isinstance(listed, list) or len(listed) != 6:
        raise RuntimeError("MARKET_SIBLING_COUNT_MISMATCH")
    tickers = [row.get("ticker") if isinstance(row, dict) else None for row in listed]
    if len(set(tickers)) != 6 or any(
        not isinstance(ticker, str) or not ticker.startswith(event_ticker + "-")
        for ticker in tickers
    ):
        raise RuntimeError("MARKET_SIBLING_IDENTITY_MISMATCH")
    series = get("/trade-api/v2/series/" + quote(series_ticker, safe=""))["payload"].get("series")
    if (
        not isinstance(series, dict)
        or series.get("ticker") != series_ticker
        or series.get("settlement_sources") != SETTLEMENT_SOURCES
    ):
        raise RuntimeError("MARKET_SERIES_SOURCE_MISMATCH")
    rows = []
    for ticker in sorted(tickers):
        market = get("/trade-api/v2/markets/" + quote(ticker, safe=""))["payload"].get("market")
        books = get("/trade-api/v2/markets/orderbooks?tickers=" + quote(ticker, safe=""))[
            "payload"
        ].get("orderbooks")
        retrieved = clock()
        if (
            not isinstance(market, dict)
            or market.get("ticker") != ticker
            or market.get("event_ticker") != event_ticker
        ):
            raise RuntimeError("MARKET_OBJECT_IDENTITY_MISMATCH")
        if (
            not isinstance(books, list)
            or len(books) != 1
            or not isinstance(books[0], dict)
            or books[0].get("ticker") != ticker
            or not isinstance(books[0].get("orderbook_fp"), dict)
        ):
            raise RuntimeError("MARKET_BOOK_IDENTITY_MISMATCH")
        rows.append(
            {
                **market,
                "orderbook_fp": books[0]["orderbook_fp"],
                "retrieved_at_utc": policy.stamp(retrieved),
            }
        )
    assembled = {**event, "markets": rows}
    policy.validate_market(city_id, target, depth_neutral_view(assembled))
    return assembled, {row["ticker"]: executability_status(row["orderbook_fp"]) for row in rows}


def delayed_books(
    policy: Any,
    tickers: list[str],
    decision: datetime,
    get: Callable[[str], dict[str, Any]],
    clock: Callable[[], datetime],
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    due = decision + DELAYED_OFFSET
    deadline = due + DELAYED_GRACE
    wait_until(due, clock, sleep)
    started = clock()
    rows = []
    for ticker in tickers:
        if clock() > deadline:
            raise RuntimeError("DELAYED_SNAPSHOT_MISSED")
        books = get("/trade-api/v2/markets/orderbooks?tickers=" + quote(ticker, safe=""))[
            "payload"
        ].get("orderbooks")
        if (
            not isinstance(books, list)
            or len(books) != 1
            or not isinstance(books[0], dict)
            or books[0].get("ticker") != ticker
        ):
            raise RuntimeError("DELAYED_BOOK_IDENTITY_MISMATCH")
        rows.append({"ticker": ticker, "orderbook": books[0], "receipt_at_utc": policy.stamp(clock())})
    completed = clock()
    if not due <= started <= completed <= deadline:
        raise RuntimeError("DELAYED_SNAPSHOT_MISSED")
    return {
        "decision_at_utc": policy.stamp(decision),
        "delayed_snapshot_due_at_utc": policy.stamp(due),
        "delayed_snapshot_started_at_utc": policy.stamp(started),
        "delayed_snapshot_completed_at_utc": policy.stamp(completed),
        "delayed_snapshot_status": "CAPTURED",
        "books": rows,
    }


def error_code(exc: BaseException) -> str:
    """Record only fixed upper-case codes; never free-form text that might carry a secret."""
    message = str(exc)
    return message if SAFE_CODE.match(message) else "UNCLASSIFIED:" + type(exc).__name__


def run_city(
    policy: Any,
    inner_store: Any,
    city_id: str,
    target: date,
    *,
    wrapper_started_at: datetime,
    clock: Callable[[], datetime],
    sleep: Callable[[float], None],
    open_weather: Callable[[], tuple[Callable[[], dict[str, Any]], dict[str, Any]]],
    get: Callable[[str], dict[str, Any]],
    context: dict[str, Any],
) -> tuple[str, int]:
    """One attempt for one city/date. Returns (status, process exit code)."""
    store = CityStore(inner_store, policy, city_id, target)
    city = policy.CITIES[city_id]
    common = {
        **policy.correlation(city_id, target),
        "protocol_sha256": policy.PROTOCOL_SHA256,
        "d1_sha256sums_sha256": D1_SHA256SUMS_SHA256,
        "pre_first_event_amendment_sha256": AMENDMENT_SHA256,
        "development_only": True,
    }
    due = policy.decision_at(target)
    close = due + timedelta(minutes=5)

    def seal_manifest() -> None:
        store.create(store.path("SHA256SUMS.json"), policy.encode(dict(store.hashes)))

    # Immediately before the atomic claim. gate() rejects non-allowlist dates,
    # a clock on any other UTC date, and any time before 09:00:00Z.
    scientific_started_at = clock()
    _, timing = policy.gate(city_id, target, scientific_started_at)
    claimed = policy.acquire_claim(store, city_id, target, scientific_started_at)
    claim_created = store.claim in store.hashes
    if timing == "LATE":
        if not claim_created:
            return "DUPLICATE_CLAIM", 0
        store.create(
            store.path("failure.json"),
            policy.encode(
                {
                    "record_type": "WN-MULTICITY-C1-CLOUD-FAILURE-v1",
                    **common,
                    "status": "MISSED_SCHEDULED_EXECUTION",
                    "error_code": "MISSED_SCHEDULED_EXECUTION",
                    "stage": "gate",
                    "wrapper_started_at_utc": policy.stamp(wrapper_started_at),
                    "observed_at_utc": policy.stamp(scientific_started_at),
                    "no_acquisition": True,
                    "no_retry": True,
                    "no_backfill": True,
                    "no_trade": True,
                }
            ),
        )
        seal_manifest()
        return "MISSED_SCHEDULED_EXECUTION", 0
    if not claimed or not claim_created:
        return "DUPLICATE_CLAIM", 0

    def require_open(code: str) -> None:
        if clock() >= close:
            raise RuntimeError(code)

    def raw_get(counter: list[int]) -> Callable[[str], dict[str, Any]]:
        def wrapped(path: str) -> dict[str, Any]:
            result = get(path)
            counter[0] += 1
            store.create(store.path(f"raw/kalshi/{counter[0]:03d}.json"), policy.encode(result))
            return result

        return wrapped

    stage = "start"
    counter = [0]
    try:
        store.create(
            store.path("start.json"),
            policy.encode(
                {
                    "record_type": "WN-MULTICITY-C1-CLOUD-START-v1",
                    **common,
                    "wrapper_started_at_utc": policy.stamp(wrapper_started_at),
                    "schedule_gate_at_utc": policy.stamp(due),
                    "scientific_started_at_utc": policy.stamp(scientific_started_at),
                    **context,
                }
            ),
        )
        # A slow claim or OAuth refresh may cross the closing boundary; never acquire then.
        require_open("MISSED_SCHEDULED_EXECUTION")
        stage = "credentials"
        fetch_weather, credential_facts = open_weather()
        require_open("MISSED_SCHEDULED_EXECUTION")
        stage = "weather"
        raw = fetch_weather()
        weather_retrieved_at = clock()
        store.create(store.path("raw/weather_feature_collection.json"), policy.encode(raw))
        rows = rows_from_features(policy, city_id, raw)
        maximum = policy.validate_weather(city_id, target, rows)
        validate_diagnostics(rows)
        primary = proxy(policy, rows, PRIMARY_BAND)
        if primary["kelvin"] != str(maximum):
            raise RuntimeError("FORECAST_RULE_SELF_CHECK_FAILED")
        stage = "market"
        require_open("MARKET_WINDOW_EXPIRED")
        event, executability = acquire_market(policy, city_id, target, raw_get(counter), clock)
        decision = clock()
        start, end = policy.target_window(city_id, target)
        weather_bytes = policy.encode(
            {
                "record_type": "WN-MULTICITY-C1-CLOUD-WEATHER-v1",
                **common,
                "station_icao": city["station_icao"],
                "grid_lonlat": city["reviewed_grid_lonlat"],
                "weather_asset": policy.PROTOCOL["weather_asset"],
                "init_time_utc": policy.stamp(datetime(target.year, target.month, target.day, tzinfo=UTC)),
                "target_window_utc": {
                    "start_inclusive": policy.stamp(start),
                    "end_exclusive": policy.stamp(end),
                },
                "retrieved_at_utc": policy.stamp(weather_retrieved_at),
                "rows": rows,
            }
        )
        market_bytes = policy.encode(
            {
                "record_type": "WN-MULTICITY-C1-CLOUD-MARKET-v1",
                **common,
                "event": event,
                "market_executability": executability,
                "market_executability_scope": "market/economic benchmarking only",
            }
        )
        forecast_bytes = policy.encode(
            {
                "record_type": "WN-MULTICITY-C1-CLOUD-FORECAST-RECEIPT-v1",
                **common,
                "frozen_rule_identity": policy.PROTOCOL["forecast_rule"],
                "forecast_p50_proxy_kelvin": primary["kelvin"],
                "forecast_p50_proxy_celsius": primary["celsius"],
                "forecast_p50_proxy_fahrenheit": primary["fahrenheit"],
                "primary_p50_argmax": primary["argmax"],
                "diagnostic_proxies_only": {
                    **{
                        "daily_max_" + band.rsplit("_", 1)[1] + "_proxy": proxy(policy, rows, band)
                        for band in DIAGNOSTIC_BANDS
                    },
                    "interpretation": (
                        "Maxima of hourly marginal statistics, not daily-maximum quantiles."
                    ),
                },
                "weather_sha256": sha(weather_bytes),
                "market_sha256": sha(market_bytes),
                "decision_at_utc": policy.stamp(decision),
                "forecast_status": "PRE_OUTCOME_FROZEN",
                "outcome_not_consulted": True,
                "no_bias_correction": True,
                "no_city_specific_tuning": True,
                "no_probability": True,
                "no_trade": True,
            }
        )
        stage = "seal"
        store.create(store.path("weather.json"), weather_bytes)
        store.create(store.path("market.json"), market_bytes)
        store.create(store.path("forecast.json"), forecast_bytes)
        packet = {
            "record_type": "WN-MULTICITY-C1-CLOUD-PACKET-v1",
            **common,
            "station_icao": city["station_icao"],
            "cli_product": city["cli_product"],
            "series_ticker": city["series_ticker"],
            "event_ticker": event["event_ticker"],
            "settlement_source": "The Weather Company",
            "settlement_window_version": policy.PROTOCOL["settlement_window_version"],
            "weather_development_observation": "VALID_PRE_OUTCOME_SEALED",
            "market_executability": executability,
            "all_siblings_executable": all(v == EXECUTABLE for v in executability.values()),
            "forecast_p50_proxy_kelvin": primary["kelvin"],
            "forecast_p50_proxy_fahrenheit": primary["fahrenheit"],
            "scientific_started_at_utc": policy.stamp(scientific_started_at),
            "decision_at_utc": policy.stamp(decision),
            "delayed_snapshot_due_at_utc": policy.stamp(decision + DELAYED_OFFSET),
            "component_sha256": {
                "weather.json": sha(weather_bytes),
                "market.json": sha(market_bytes),
                "forecast.json": sha(forecast_bytes),
            },
            "raw_evidence_sha256": dict(store.hashes),
            "no_trade": True,
            "no_alert": True,
            "no_probability_or_edge_calculation": True,
        }
        store.create(store.path("packet.json"), policy.encode(packet))
    except Exception as exc:  # noqa: BLE001 — every failure becomes immutable evidence, never a retry
        code = error_code(exc)
        status = (
            "DATA_NOT_READY"
            if stage == "weather" and code.startswith("DATA_NOT_READY")
            else "MISSED_SCHEDULED_EXECUTION"
            if code == "MISSED_SCHEDULED_EXECUTION"
            else "FAILED"
        )
        store.create(
            store.path("failure.json"),
            policy.encode(
                {
                    "record_type": "WN-MULTICITY-C1-CLOUD-FAILURE-v1",
                    **common,
                    "status": status,
                    "error_code": code,
                    "error_type": type(exc).__name__,
                    "stage": stage,
                    "scientific_started_at_utc": policy.stamp(scientific_started_at),
                    "failed_at_utc": policy.stamp(clock()),
                    # failure.json takes precedence over any component listed here.
                    "objects_already_created": sorted(store.hashes),
                    "no_retry": True,
                    "no_backfill": True,
                    "no_trade": True,
                }
            ),
        )
        seal_manifest()
        return status, 1

    # The forecast receipt and packet are sealed. The delayed books are ancillary
    # market evidence and can no longer change or remove the sealed forecast.
    tickers = sorted(row["ticker"] for row in event["markets"])
    try:
        delayed = delayed_books(policy, tickers, decision, raw_get(counter), clock, sleep)
        store.create(
            store.path("delayed_books.json"),
            policy.encode({"record_type": "WN-MULTICITY-C1-CLOUD-DELAYED-BOOKS-v1", **common, **delayed}),
        )
        status, code = "CAPTURED_AND_PRE_OUTCOME_FROZEN", 0
    except Exception as exc:  # noqa: BLE001
        missed = error_code(exc) == "DELAYED_SNAPSHOT_MISSED"
        status, code = ("DELAYED_SNAPSHOT_MISSED" if missed else "DELAYED_SNAPSHOT_FAILED"), 1
        store.create(
            store.path("delayed_snapshot_failure.json"),
            policy.encode(
                {
                    "record_type": "WN-MULTICITY-C1-CLOUD-DELAYED-FAILURE-v1",
                    **common,
                    "status": status,
                    "error_code": error_code(exc),
                    "error_type": type(exc).__name__,
                    "decision_at_utc": policy.stamp(decision),
                    "failed_at_utc": policy.stamp(clock()),
                    "packet_and_forecast_already_sealed": True,
                    "diagnostic_only": True,
                    "weather_development_forecast_unaffected": True,
                    "no_retry": True,
                    "no_trade": True,
                }
            ),
        )
    store.create(
        store.path("structured_log.json"),
        policy.encode(
            {
                "record_type": "WN-MULTICITY-C1-CLOUD-STRUCTURED-LOG-v1",
                **common,
                "status": status,
                "wrapper_started_at_utc": policy.stamp(wrapper_started_at),
                "scientific_started_at_utc": policy.stamp(scientific_started_at),
                "decision_at_utc": policy.stamp(decision),
                "completed_at_utc": policy.stamp(clock()),
                "kalshi_public_get_count": counter[0],
                **credential_facts,
                **context,
                "no_trade": True,
            }
        ),
    )
    seal_manifest()
    return status, code


def load_user_credentials() -> tuple[Any, Any]:
    """Dedicated Earth Engine read-only user OAuth from the mounted secret; never printed."""
    import ee
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    mount = SECRET_MOUNT
    if not mount.is_file() or not os.access(mount, os.R_OK):
        raise RuntimeError("OAUTH_SECRET_MOUNT_UNREADABLE")
    payload = mount.read_bytes()
    if sha(payload) != SECRET_SHA256:
        raise RuntimeError("OAUTH_SECRET_HASH_MISMATCH")
    saved = json.loads(payload.decode("utf-8"))
    if not isinstance(saved, dict) or set(saved.get("scopes", [])) != {READONLY_SCOPE}:
        raise RuntimeError("OAUTH_SCOPE_SHAPE_MISMATCH")
    if not saved.get("refresh_token"):
        raise RuntimeError("OAUTH_REFRESH_TOKEN_MISSING")
    home = tempfile.TemporaryDirectory(prefix="wn-multicity-c1-")
    config = Path(home.name) / ".config" / "earthengine"
    config.mkdir(parents=True, mode=0o700)
    config.chmod(0o700)
    credential_path = config / "credentials"
    fd = os.open(credential_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)
    if stat.S_IMODE(credential_path.stat().st_mode) != 0o600:
        raise RuntimeError("OAUTH_EPHEMERAL_MODE_MISMATCH")
    os.environ["HOME"] = home.name
    os.environ["XDG_CONFIG_HOME"] = str(Path(home.name) / ".config")
    os.environ["CLOUDSDK_CONFIG"] = str(Path(home.name) / "gcloud")
    os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
    if Path(ee.oauth.get_credentials_path()) != credential_path:
        raise RuntimeError("OAUTH_EE_PATH_MISMATCH")
    try:
        credentials = Credentials(None, **ee.oauth.get_credentials_arguments())
        credentials.refresh(Request())
    except Exception:
        # Drop the original exception so no token-bearing text can reach a log.
        raise RuntimeError("OAUTH_REFRESH_FAILED") from None
    if set(credentials.granted_scopes or []) != {READONLY_SCOPE}:
        raise RuntimeError("OAUTH_GRANTED_SCOPE_MISMATCH")
    return home, credentials


def emit(policy: Any, record: dict[str, Any]) -> None:
    print(policy.encode(record).decode().rstrip("\n"), flush=True)


def preflight(
    policy: Any, wrapper_started_at: datetime, environ: Mapping[str, str]
) -> tuple[str | None, int, str, date, datetime]:
    """Static pre-gate decision: task city, today's UTC target, and whether waiting is allowed."""
    if wrapper_started_at.tzinfo is None:
        raise RuntimeError("NAIVE_CLOCK")
    index, city_id = city_for_task(environ)
    # The target is today's real UTC date and nothing else; no date argument exists.
    target = wrapper_started_at.astimezone(UTC).date()
    if target not in policy.ALLOWLIST:
        return "OUTSIDE_EXACT_DATE_ALLOWLIST", index, city_id, target, wrapper_started_at
    due = policy.decision_at(target)
    if wrapper_started_at < due - EARLY_WRAPPER_MAX:
        return "WRAPPER_TOO_EARLY_NO_WAIT", index, city_id, target, due
    return None, index, city_id, target, due


def main() -> int:
    if len(sys.argv) != 1:
        raise RuntimeError("ARGUMENTS_PROHIBITED")
    wrapper_started_at = datetime.now(UTC)
    policy = load_policy()
    refusal, index, city_id, target, due = preflight(policy, wrapper_started_at, os.environ)
    base = {"city_id": city_id, "task_index": index, "target_date_utc": target.isoformat()}
    if refusal is not None:
        emit(policy, {**base, "status": refusal, "no_acquisition": True})
        return 1
    # Before the gate the wrapper has only verified static policy. It now waits by
    # UTC wall clock without creating a claim or contacting Earth Engine, Kalshi, or GCS.
    wait_until(due)

    from google.cloud import storage

    inner = policy.GcsCreateOnly(storage.Client(project=PROJECT).bucket(BUCKET))
    homes: list[Any] = []

    def open_weather() -> tuple[Callable[[], dict[str, Any]], dict[str, Any]]:
        import ee

        home, credentials = load_user_credentials()
        homes.append(home)
        ee.data.setMaxRetries(0)
        ee.Initialize(credentials=credentials, project=PROJECT)
        ee.data.setMaxRetries(0)
        facts = {
            "credential_class": type(credentials).__module__ + "." + type(credentials).__name__,
            "scope_names": [READONLY_SCOPE],
        }
        return (lambda: ee_weather(ee, policy, city_id, target)), facts

    try:
        status, code = run_city(
            policy,
            inner,
            city_id,
            target,
            wrapper_started_at=wrapper_started_at,
            clock=lambda: datetime.now(UTC),
            sleep=time.sleep,
            open_weather=open_weather,
            get=kalshi_get,
            context={
                "task_index": index,
                "cloud_run_job": os.environ.get("CLOUD_RUN_JOB", ""),
                "cloud_run_execution": os.environ.get("CLOUD_RUN_EXECUTION", ""),
            },
        )
    except Exception as exc:  # noqa: BLE001 — evidence write itself failed; report and stop
        emit(policy, {**base, "status": "FAILED_EVIDENCE_WRITE", "error_code": error_code(exc)})
        return 1
    finally:
        for home in homes:
            home.cleanup()
    emit(policy, {**base, "status": status, "no_retry": True, "no_trade": True})
    return code


if __name__ == "__main__":
    raise SystemExit(main())
