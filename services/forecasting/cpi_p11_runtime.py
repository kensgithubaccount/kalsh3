"""Operational runtime for the frozen CPI-E1-P11 prospective replication.

This module implements the already-frozen protocol without changing its scientific
rules. It is research-only, performs no authenticated exchange access, submits no
orders, computes no fees/PnL, and fails closed on missing prospective evidence.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import re
import ssl
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlsplit

from services.contract_intelligence.specification import (
    Comparator,
    select_authoritative_comparison,
)
from services.forecasting.cpi_evidence_issuer import acquire_and_issue_cpi_evidence
from services.forecasting.cpi_initial_release_value import (
    issue_cpi_initial_release_observation,
    validate_cpi_initial_release_observation,
)
from services.forecasting.cpi_p11_prospective_protocol import (
    EXPECTED_SIBLING_COUNT,
    KALSHI_CLOSE_AT_UTC,
    TARGET_EVENT,
    TARGET_REFERENCE_MONTH,
    TARGET_SERIES,
    ProspectiveDecision,
    build_phase0_protocol,
    classify_directional_call,
    classify_event_result,
)
from services.historical_replay.cpi_price_evidence import (
    build_price_evidence,
    strict_json_loads,
    validate_candle_payload,
)
from services.market_universe import public_read
from services.market_universe.domain import (
    Event,
    Market,
    MarketStatus,
    Series,
    UniverseValidationError,
)

PROTOCOL_SHA256 = "e62374c9db5b7f3355d413687ab82e868ca2686fbdd6fd9c57a1329151a8b40d"
FROZEN_SPEC = Path("docs/reviews/artifacts/cpi-p11-phase0-prospective-protocol/spec.json")
RUN_RECORD_TYPE = "CPI-E1-P11-PROSPECTIVE-RUNTIME-v1"
ZERO = Decimal("0")

MARKET_WINDOW_START = datetime(2026, 10, 14, 12, 5, tzinfo=UTC)
PRE_RELEASE_DEADLINE = datetime(2026, 10, 14, 12, 20, tzinfo=UTC)
MARKET_CLOSE = datetime.fromisoformat(KALSHI_CLOSE_AT_UTC.replace("Z", "+00:00"))
BLS_RELEASE_AT = datetime(2026, 10, 14, 12, 30, tzinfo=UTC)

BLS_HOST = "www.bls.gov"
BLS_SCHEDULE_PATH = "/schedule/news_release/cpi.htm"
BLS_RELEASE_LOCATOR = "https://www.bls.gov/news.release/archives/cpi_10142026.htm"
MAX_BLS_BYTES = 4_000_000

KALSHI_SERIES_PATH = f"{public_read.BASE}/series/{TARGET_SERIES}"
KALSHI_EVENT_PATH = f"{public_read.BASE}/events/{TARGET_EVENT}"
KALSHI_MARKETS_PATH = (
    f"{public_read.BASE}/markets?event_ticker={TARGET_EVENT}&limit=100"
)

APPROVED_HOST_GROUPS = {
    "reuters.com": "reuters",
    "www.reuters.com": "reuters",
    "yahoo.com": "yahoo",
    "www.yahoo.com": "yahoo",
    "finance.yahoo.com": "yahoo",
    "ca.finance.yahoo.com": "yahoo",
    "kfgo.com": "kfgo",
    "www.kfgo.com": "kfgo",
    "tradingview.com": "tradingview",
    "www.tradingview.com": "tradingview",
    "nasdaq.com": "nasdaq",
    "www.nasdaq.com": "nasdaq",
    "investing.com": "investing",
    "www.investing.com": "investing",
    "wmbd.com": "wmbd",
    "www.wmbd.com": "wmbd",
    "aol.com": "aol",
    "www.aol.com": "aol",
}


class P11OperationalError(RuntimeError):
    """Base class for fail-closed P11 operational errors."""


class P11AuthorityError(P11OperationalError):
    """A protocol, source, schema, or semantic authority check failed."""


class P11TimingError(P11OperationalError):
    """A stage was invoked outside its frozen prospective timing boundary."""


class P11TransportError(P11OperationalError):
    """A bounded external read failed."""


@dataclass(frozen=True, slots=True)
class RunPaths:
    root: Path

    @property
    def protocol(self) -> Path:
        return self.root / "protocol.json"

    @property
    def preflight(self) -> Path:
        return self.root / "preflight.json"

    @property
    def market_capture(self) -> Path:
        return self.root / "kalshi" / "market_capture.json"

    @property
    def reuters_coverage(self) -> Path:
        return self.root / "reuters" / "coverage.json"

    @property
    def reuters_receipt(self) -> Path:
        return self.root / "reuters" / "receipt.json"

    @property
    def reuters_extract(self) -> Path:
        return self.root / "reuters" / "extract.json"

    @property
    def bls_initial_release(self) -> Path:
        return self.root / "bls" / "initial_release.json"

    @property
    def result(self) -> Path:
        return self.root / "result.json"

    @property
    def failure(self) -> Path:
        return self.root / "failure.json"

    @property
    def manifest(self) -> Path:
        return self.root / "manifest.sha256"


@dataclass(frozen=True, slots=True)
class MarketIdentity:
    ticker: str
    threshold: Decimal
    close_time: datetime
    raw: dict[str, Any]


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_bytes_exclusive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(fd)


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def _write_json_exclusive(path: Path, value: dict[str, Any]) -> None:
    _write_bytes_exclusive(path, _json_bytes(value))


def _read_json(path: Path) -> dict[str, Any]:
    value = strict_json_loads(path.read_bytes())
    if not isinstance(value, dict):
        raise P11AuthorityError(f"{path} is not a JSON object")
    return value


def _parse_utc(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise P11AuthorityError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise P11AuthorityError(f"{field} is not an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise P11AuthorityError(f"{field} must be timezone-aware")
    return parsed.astimezone(UTC)


def _decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, str) or not value or value != value.strip():
        raise P11AuthorityError(f"{field} must be an exact Decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise P11AuthorityError(f"{field} is not a Decimal") from exc
    if not parsed.is_finite():
        raise P11AuthorityError(f"{field} must be finite")
    return parsed


def _require_hex_digest(value: object, field: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise P11AuthorityError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _terminal_guard(paths: RunPaths) -> None:
    if paths.result.exists() or paths.failure.exists() or paths.manifest.exists():
        raise P11AuthorityError("P11 run is already terminal and immutable")


def _load_frozen_spec() -> tuple[dict[str, Any], bytes]:
    path = _repo_root() / FROZEN_SPEC
    raw = path.read_bytes()
    spec = strict_json_loads(raw)
    if not isinstance(spec, dict):
        raise P11AuthorityError("frozen P11 spec is not an object")
    if spec.get("spec_digest_sha256") != PROTOCOL_SHA256:
        raise P11AuthorityError("frozen P11 protocol digest mismatch")
    if spec != build_phase0_protocol():
        raise P11AuthorityError("frozen P11 protocol no longer regenerates exactly")
    return spec, raw


def ensure_protocol(paths: RunPaths) -> dict[str, Any]:
    spec, raw = _load_frozen_spec()
    paths.root.mkdir(parents=True, exist_ok=True)
    if paths.protocol.exists():
        if paths.protocol.read_bytes() != raw:
            raise P11AuthorityError("runtime protocol.json differs from frozen reviewed spec")
    else:
        _write_bytes_exclusive(paths.protocol, raw)
    return spec


def _raw_envelope(
    *, path: str, body: bytes, status: int, observed_at: datetime
) -> dict[str, Any]:
    return {
        "path": path,
        "observed_at_utc": _iso(observed_at),
        "status": status,
        "body_sha256": _sha256_bytes(body),
        "raw_body_b64": base64.b64encode(body).decode("ascii"),
        "bytes": len(body),
    }


def _payload_from_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    encoded = envelope.get("raw_body_b64")
    if not isinstance(encoded, str):
        raise P11AuthorityError("raw evidence envelope lacks body bytes")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise P11AuthorityError("raw evidence envelope has invalid base64") from exc
    if _sha256_bytes(raw) != envelope.get("body_sha256"):
        raise P11AuthorityError("raw evidence envelope SHA-256 mismatch")
    payload = strict_json_loads(raw)
    if not isinstance(payload, dict):
        raise P11AuthorityError("raw evidence payload is not an object")
    return payload


def _bls_schedule_get() -> tuple[bytes, datetime]:
    connection = http.client.HTTPSConnection(
        BLS_HOST, timeout=10, context=ssl.create_default_context()
    )
    try:
        connection.request(
            "GET",
            BLS_SCHEDULE_PATH,
            headers={
                "Accept": "text/html",
                "User-Agent": "kalsh3-cpi-e1-p11/1.0",
            },
        )
        response = connection.getresponse()
        if response.getheader("Location") is not None or 300 <= response.status < 400:
            raise P11TransportError("BLS schedule redirect rejected")
        declared = response.getheader("Content-Length")
        if declared is not None and int(declared) > MAX_BLS_BYTES:
            raise P11TransportError("BLS schedule response exceeds size bound")
        body = response.read(MAX_BLS_BYTES + 1)
        observed = _now()
        if response.status != 200 or not body or len(body) > MAX_BLS_BYTES:
            raise P11TransportError("BLS schedule response rejected")
        return body, observed
    except (OSError, TimeoutError, http.client.HTTPException, ValueError) as exc:
        if isinstance(exc, P11TransportError):
            raise
        raise P11TransportError(f"BLS schedule request failed: {exc}") from exc
    finally:
        connection.close()


def _validate_bls_schedule(body: bytes) -> None:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise P11AuthorityError("BLS schedule is not valid UTF-8") from exc
    parser = _VisibleText()
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:
        raise P11AuthorityError("BLS schedule HTML could not be parsed") from exc
    normalized = " ".join(" ".join(parser.parts).split())
    match = re.search(r"September\s+2026", normalized, flags=re.IGNORECASE)
    if match is None:
        raise P11AuthorityError("BLS schedule lacks September 2026 CPI row")
    window = normalized[match.start() : match.start() + 700]
    if re.search(r"Oct(?:ober|\.)?\s+14,?\s+2026", window, re.IGNORECASE) is None:
        raise P11AuthorityError("BLS schedule date for September 2026 changed")
    if re.search(r"(?:08:30|8:30)\s*(?:AM|A\.M\.)", window, re.IGNORECASE) is None:
        raise P11AuthorityError("BLS schedule time for September 2026 changed")


def _kalshi_get(
    path: str,
    *,
    deadline: datetime,
    clock: Callable[[], datetime],
    sleeper: Callable[[float], None],
) -> tuple[bytes, dict[str, Any], datetime]:
    last_status: int | None = None
    for attempt in range(3):
        if clock() >= deadline:
            raise P11TimingError("pre-release acquisition deadline passed")
        try:
            body, status, observed = public_read._get_raw(path, timeout_seconds=10)
        except public_read.PublicReadFailure as exc:
            if attempt == 2:
                raise P11TransportError(str(exc)) from exc
            sleeper(0.25 * (attempt + 1))
            continue
        last_status = status
        if status == 200:
            payload = strict_json_loads(body)
            if not isinstance(payload, dict):
                raise P11AuthorityError("Kalshi response is not a JSON object")
            return body, payload, observed
        if status != 429 or attempt == 2:
            raise P11TransportError(f"Kalshi public read returned HTTP {status}")
        sleeper(0.25 * (attempt + 1))
    raise P11TransportError(f"Kalshi public read failed with HTTP {last_status}")


def _validate_series(payload: dict[str, Any]) -> Series:
    raw = payload.get("series")
    if not isinstance(raw, dict):
        raise P11AuthorityError("Kalshi series payload is malformed")
    try:
        series = Series.parse(raw)
    except UniverseValidationError as exc:
        raise P11AuthorityError("Kalshi series failed canonical parsing") from exc
    if (
        series.ticker != TARGET_SERIES
        or series.category.casefold() != "economics"
        or "cpi" not in series.title.casefold()
    ):
        raise P11AuthorityError("Kalshi CPI series identity changed")
    sources = series.settlement_sources
    if not any(
        "bureau of labor statistics" in str(source.get("name", "")).casefold()
        and str(source.get("url", "")).startswith("https://www.bls.gov")
        for source in sources
    ):
        raise P11AuthorityError("Kalshi CPI series no longer binds BLS settlement authority")
    return series


def _validate_event(payload: dict[str, Any]) -> Event:
    raw = payload.get("event")
    if not isinstance(raw, dict):
        raise P11AuthorityError("Kalshi event payload is malformed")
    try:
        event = Event.parse(raw)
    except UniverseValidationError as exc:
        raise P11AuthorityError("Kalshi event failed canonical parsing") from exc
    if event.ticker != TARGET_EVENT or event.series_ticker != TARGET_SERIES:
        raise P11AuthorityError("Kalshi target event identity changed")
    if event.category is not None and event.category.casefold() != "economics":
        raise P11AuthorityError("Kalshi target event category changed")
    return event


def _market_close(raw: dict[str, Any]) -> datetime:
    return _parse_utc(raw.get("close_time"), "market.close_time")


def _validate_markets(payload: dict[str, Any]) -> tuple[MarketIdentity, ...]:
    rows = payload.get("markets")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise P11AuthorityError("Kalshi market inventory is malformed")
    if payload.get("cursor") not in (None, ""):
        raise P11AuthorityError("Kalshi target market inventory did not exhaust one bounded page")
    if len(rows) != EXPECTED_SIBLING_COUNT:
        raise P11AuthorityError(
            f"expected {EXPECTED_SIBLING_COUNT} CPI siblings, received {len(rows)}"
        )

    identities: list[MarketIdentity] = []
    seen_tickers: set[str] = set()
    seen_thresholds: set[Decimal] = set()
    for raw in rows:
        try:
            market = Market.parse(raw)
        except UniverseValidationError as exc:
            raise P11AuthorityError("Kalshi target market failed canonical parsing") from exc
        if (
            market.event_ticker != TARGET_EVENT
            or not market.ticker.startswith(TARGET_EVENT + "-")
            or market.status is not MarketStatus.ACTIVE
            or market.market_type != "binary"
            or market.provisional
            or market.multivariate
        ):
            raise P11AuthorityError("Kalshi target sibling identity/status is not admissible")
        if _market_close(raw) != MARKET_CLOSE:
            raise P11AuthorityError("Kalshi target sibling close time changed")
        if raw.get("strike_type") != "greater":
            raise P11AuthorityError("Kalshi CPI sibling strike type is not strict greater-than")
        rules = raw.get("rules_primary")
        if (
            not isinstance(rules, str)
            or "consumer price index" not in rules.casefold()
            or re.search(r"\bin\s+September\s+2026\b", rules, re.IGNORECASE) is None
        ):
            raise P11AuthorityError("Kalshi CPI sibling reference-month semantics changed")
        selection = select_authoritative_comparison(
            rules_primary=rules,
            title=raw.get("title") if isinstance(raw.get("title"), str) else None,
            rules_secondary=(
                raw.get("rules_secondary") if isinstance(raw.get("rules_secondary"), str) else None
            ),
        )
        if selection.comparator is not Comparator.GT or selection.threshold is None:
            raise P11AuthorityError("Kalshi CPI sibling comparator is not reviewed strict GT")
        try:
            strike = Decimal(str(raw.get("floor_strike")))
        except InvalidOperation as exc:
            raise P11AuthorityError("Kalshi CPI sibling floor strike is malformed") from exc
        if not strike.is_finite() or strike != selection.threshold:
            raise P11AuthorityError("Kalshi CPI sibling threshold conflicts with rules")
        if market.ticker in seen_tickers or strike in seen_thresholds:
            raise P11AuthorityError("Kalshi CPI sibling cohort contains duplicate identity")
        seen_tickers.add(market.ticker)
        seen_thresholds.add(strike)
        identities.append(MarketIdentity(market.ticker, strike, MARKET_CLOSE, raw))

    return tuple(sorted(identities, key=lambda item: (item.threshold, item.ticker)))


def _market_signature(markets: tuple[MarketIdentity, ...]) -> list[dict[str, str]]:
    return [
        {
            "ticker": market.ticker,
            "threshold": str(market.threshold),
            "close_time_utc": _iso(market.close_time),
        }
        for market in markets
    ]


def _live_candle_path(market: MarketIdentity) -> tuple[str, int, int]:
    raw = market.raw
    opened = _parse_utc(raw.get("open_time"), "market.open_time")
    start = max(opened, market.close_time - timedelta(days=90))
    start_ts = int(start.timestamp())
    end_ts = int(market.close_time.timestamp())
    ticker = quote(market.ticker, safe="")
    path = (
        f"{public_read.BASE}/markets/{ticker}/candlesticks"
        f"?start_ts={start_ts}&end_ts={end_ts}&period_interval=60"
    )
    return path, start_ts, end_ts


def run_preflight(
    paths: RunPaths,
    *,
    clock: Callable[[], datetime] = _now,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    ensure_protocol(paths)
    _terminal_guard(paths)
    if paths.preflight.exists():
        return _read_json(paths.preflight)
    now = clock()
    if now.date() != PRE_RELEASE_DEADLINE.date() or now >= PRE_RELEASE_DEADLINE:
        raise P11TimingError("P11 preflight must complete on Oct 14 before 12:20Z")

    schedule_raw, schedule_observed = _bls_schedule_get()
    _validate_bls_schedule(schedule_raw)

    series_raw, series_payload, series_observed = _kalshi_get(
        KALSHI_SERIES_PATH, deadline=PRE_RELEASE_DEADLINE, clock=clock, sleeper=sleeper
    )
    event_raw, event_payload, event_observed = _kalshi_get(
        KALSHI_EVENT_PATH, deadline=PRE_RELEASE_DEADLINE, clock=clock, sleeper=sleeper
    )
    markets_raw, markets_payload, markets_observed = _kalshi_get(
        KALSHI_MARKETS_PATH, deadline=PRE_RELEASE_DEADLINE, clock=clock, sleeper=sleeper
    )

    series = _validate_series(series_payload)
    event = _validate_event(event_payload)
    markets = _validate_markets(markets_payload)

    schedule_envelope = _raw_envelope(
        path=BLS_SCHEDULE_PATH,
        body=schedule_raw,
        status=200,
        observed_at=schedule_observed,
    )
    series_envelope = _raw_envelope(
        path=KALSHI_SERIES_PATH,
        body=series_raw,
        status=200,
        observed_at=series_observed,
    )
    event_envelope = _raw_envelope(
        path=KALSHI_EVENT_PATH,
        body=event_raw,
        status=200,
        observed_at=event_observed,
    )
    markets_envelope = _raw_envelope(
        path=KALSHI_MARKETS_PATH,
        body=markets_raw,
        status=200,
        observed_at=markets_observed,
    )

    _write_json_exclusive(paths.root / "bls" / "schedule.json", schedule_envelope)
    _write_json_exclusive(paths.root / "kalshi" / "series.json", series_envelope)
    _write_json_exclusive(paths.root / "kalshi" / "event.json", event_envelope)
    _write_json_exclusive(paths.root / "kalshi" / "markets.json", markets_envelope)

    receipt = {
        "record_type": RUN_RECORD_TYPE,
        "stage": "PREFLIGHT",
        "protocol_sha256": PROTOCOL_SHA256,
        "event_ticker": TARGET_EVENT,
        "reference_month": TARGET_REFERENCE_MONTH,
        "completed_at_utc": _iso(clock()),
        "first_party_only": True,
        "production_influence": "0",
        "checks": {
            "bls_schedule": True,
            "series_identity": series.ticker == TARGET_SERIES,
            "event_identity": event.ticker == TARGET_EVENT,
            "complete_sibling_count": len(markets) == EXPECTED_SIBLING_COUNT,
            "all_active_binary_nonprovisional_nonmve": True,
            "all_strict_gt_september_2026": True,
            "all_close_at_frozen_time": True,
        },
        "market_signature": _market_signature(markets),
        "evidence_sha256": {
            "bls_schedule": schedule_envelope["body_sha256"],
            "kalshi_series": series_envelope["body_sha256"],
            "kalshi_event": event_envelope["body_sha256"],
            "kalshi_markets": markets_envelope["body_sha256"],
        },
    }
    _write_json_exclusive(paths.preflight, receipt)
    return receipt


def capture_market_evidence(
    paths: RunPaths,
    *,
    clock: Callable[[], datetime] = _now,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    ensure_protocol(paths)
    _terminal_guard(paths)
    if paths.market_capture.exists():
        return _read_json(paths.market_capture)
    now = clock()
    if now < MARKET_WINDOW_START:
        raise P11TimingError("P11 market collection window has not opened")
    if now >= PRE_RELEASE_DEADLINE:
        raise P11TimingError("P11 market collection window has closed")

    preflight = run_preflight(paths, clock=clock, sleeper=sleeper)
    frozen_signature = preflight.get("market_signature")
    if not isinstance(frozen_signature, list):
        raise P11AuthorityError("preflight market signature is missing")

    markets_raw, markets_payload, markets_observed = _kalshi_get(
        KALSHI_MARKETS_PATH, deadline=PRE_RELEASE_DEADLINE, clock=clock, sleeper=sleeper
    )
    markets = _validate_markets(markets_payload)
    if _market_signature(markets) != frozen_signature:
        raise P11AuthorityError("Kalshi CPI sibling cohort changed after preflight")

    captured: list[tuple[MarketIdentity, str, bytes, datetime, dict[str, Any]]] = []
    for market in markets:
        path, start_ts, end_ts = _live_candle_path(market)
        raw, payload, observed = _kalshi_get(
            path, deadline=PRE_RELEASE_DEADLINE, clock=clock, sleeper=sleeper
        )
        candles = validate_candle_payload(
            payload,
            market_ticker=market.ticker,
            request_start_ts=start_ts,
            request_end_ts=end_ts,
            period_interval_minutes=60,
        )
        evidence = build_price_evidence(
            market.raw,
            request_path=path,
            request_start_ts=start_ts,
            request_end_ts=end_ts,
            raw_body=raw,
            retrieved_at=observed,
            candles=candles,
        )
        captured.append(
            (
                market,
                path,
                raw,
                observed,
                {
                    "threshold": str(evidence.threshold),
                    "candle_end_period_ts": evidence.candle_end_period_ts,
                    "yes_bid": None if evidence.yes_bid is None else str(evidence.yes_bid),
                    "yes_ask": None if evidence.yes_ask is None else str(evidence.yes_ask),
                    "missing_side_reason": evidence.missing_side_reason,
                    "staleness_state": evidence.staleness_state,
                    "quote_age_seconds": evidence.quote_age_seconds,
                    "evidence_id": evidence.evidence_id,
                },
            )
        )

    markets_capture_envelope = _raw_envelope(
        path=KALSHI_MARKETS_PATH,
        body=markets_raw,
        status=200,
        observed_at=markets_observed,
    )
    _write_json_exclusive(
        paths.root / "kalshi" / "markets_at_capture.json",
        markets_capture_envelope,
    )

    rows: list[dict[str, Any]] = []
    for market, request_path, raw, observed, selected in captured:
        envelope = _raw_envelope(
            path=request_path,
            body=raw,
            status=200,
            observed_at=observed,
        )
        envelope["market_ticker"] = market.ticker
        envelope["selected"] = selected
        _write_json_exclusive(
            paths.root / "kalshi" / "candles" / f"{market.ticker}.json",
            envelope,
        )
        rows.append(
            {
                "market_ticker": market.ticker,
                "threshold": str(market.threshold),
                "yes_ask": selected["yes_ask"],
                "candle_end_period_ts": selected["candle_end_period_ts"],
                "missing_side_reason": selected["missing_side_reason"],
                "evidence_id": selected["evidence_id"],
                "raw_body_sha256": envelope["body_sha256"],
            }
        )

    receipt = {
        "record_type": RUN_RECORD_TYPE,
        "stage": "MARKET_CAPTURE",
        "protocol_sha256": PROTOCOL_SHA256,
        "event_ticker": TARGET_EVENT,
        "completed_at_utc": _iso(clock()),
        "window_start_utc": _iso(MARKET_WINDOW_START),
        "window_end_utc": _iso(PRE_RELEASE_DEADLINE),
        "endpoint_mode": "LIVE_CANDLESTICK_ENDPOINT_FOR_UNARCHIVED_MARKET",
        "selection_rule": "LATEST_60M_CANDLE_END_STRICTLY_BEFORE_MARKET_CLOSE",
        "primary_price": "yes_ask",
        "production_influence": "0",
        "markets": rows,
    }
    _write_json_exclusive(paths.market_capture, receipt)
    return receipt


def _operator_group(host: str) -> str:
    normalized = host.casefold().rstrip(".")
    group = APPROVED_HOST_GROUPS.get(normalized)
    if group is None:
        raise P11AuthorityError(f"unapproved Reuters evidence host: {host}")
    return group


def record_reuters_pass(
    paths: RunPaths,
    input_record: dict[str, Any],
    *,
    clock: Callable[[], datetime] = _now,
) -> dict[str, Any]:
    ensure_protocol(paths)
    _terminal_guard(paths)
    if paths.reuters_coverage.exists():
        raise P11AuthorityError("Reuters evidence already has a terminal state")
    if not paths.preflight.is_file():
        raise P11AuthorityError("Reuters evidence cannot precede first-party preflight")
    now = clock()
    if now >= PRE_RELEASE_DEADLINE:
        raise P11TimingError("Reuters PASS evidence cannot be recorded after 12:20Z")

    if input_record.get("event_ticker") != TARGET_EVENT:
        raise P11AuthorityError("Reuters receipt event identity mismatch")
    if input_record.get("reference_month") != TARGET_REFERENCE_MONTH:
        raise P11AuthorityError("Reuters receipt reference month mismatch")
    if input_record.get("provider_organization") != "Reuters":
        raise P11AuthorityError("Reuters attribution is not exact")
    if input_record.get("measure") != (
        "headline CPI, month-over-month, seasonally adjusted, nonannualized"
    ):
        raise P11AuthorityError("Reuters receipt target measure mismatch")
    for field in (
        "reuters_attribution_verified",
        "reference_month_verified_in_body",
        "prospective_headline_mom_verified",
    ):
        if input_record.get(field) is not True:
            raise P11AuthorityError(f"Reuters evidence requirement failed: {field}")
    if input_record.get("retrospective_language_found") is not False:
        raise P11AuthorityError("retrospective Reuters language cannot enter PASS")

    value_text = input_record.get("value")
    value = _decimal(value_text, "Reuters value")
    sentence_hash = _require_hex_digest(
        input_record.get("load_bearing_sentence_sha256"),
        "load_bearing_sentence_sha256",
    )

    hosts = input_record.get("hosts")
    if not isinstance(hosts, list) or len(hosts) < 2:
        raise P11AuthorityError("Reuters PASS requires at least two host records")
    groups: set[str] = set()
    published_times: list[datetime] = []
    normalized_hosts: list[dict[str, Any]] = []
    for item in hosts:
        if not isinstance(item, dict):
            raise P11AuthorityError("Reuters host record is malformed")
        host = item.get("host")
        url = item.get("url")
        if not isinstance(host, str) or not isinstance(url, str):
            raise P11AuthorityError("Reuters host identity is malformed")
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname is None:
            raise P11AuthorityError("Reuters evidence URL must be HTTPS")
        if parsed.hostname.casefold().rstrip(".") != host.casefold().rstrip("."):
            raise P11AuthorityError("Reuters evidence URL host mismatch")
        group = _operator_group(host)
        groups.add(group)
        if item.get("http_status") != 200:
            raise P11AuthorityError("only successful host evidence can support Reuters PASS")
        retrieved = _parse_utc(item.get("retrieved_at"), "host.retrieved_at")
        published = _parse_utc(item.get("published_at"), "host.published_at")
        if retrieved > now or retrieved >= PRE_RELEASE_DEADLINE:
            raise P11AuthorityError("Reuters host retrieval was not completed by the frozen cutoff")
        if published >= MARKET_CLOSE:
            raise P11AuthorityError("Reuters publication did not precede market close")
        host_sentence_hash = _require_hex_digest(
            item.get("load_bearing_sentence_sha256"),
            "host.load_bearing_sentence_sha256",
        )
        if host_sentence_hash != sentence_hash:
            raise P11AuthorityError("cross-host load-bearing forecast sentence did not match")
        raw_hash = _require_hex_digest(item.get("raw_response_sha256"), "host.raw_response_sha256")
        published_times.append(published)
        normalized_hosts.append(
            {
                "host": host.casefold().rstrip("."),
                "operator_group": group,
                "url": url,
                "http_status": 200,
                "retrieved_at": _iso(retrieved),
                "published_at": _iso(published),
                "raw_response_sha256": raw_hash,
                "load_bearing_sentence_sha256": host_sentence_hash,
            }
        )
    if len(groups) < 2:
        raise P11AuthorityError("Reuters PASS lacks two independently operated hosts")

    conservative = max(published_times)
    if conservative >= MARKET_CLOSE:
        raise P11AuthorityError("conservative Reuters publication time misses market close")

    receipt = {
        "record_type": RUN_RECORD_TYPE,
        "stage": "REUTERS_PASS",
        "protocol_sha256": PROTOCOL_SHA256,
        "event_ticker": TARGET_EVENT,
        "reference_month": TARGET_REFERENCE_MONTH,
        "provider_organization": "Reuters",
        "predictor_family": "Reuters survey of economists",
        "measure": input_record["measure"],
        "value": str(value),
        "value_literal": value_text,
        "governing_published_at_utc": _iso(conservative),
        "recorded_at_utc": _iso(now),
        "host_operator_groups": sorted(groups),
        "hosts": normalized_hosts,
        "load_bearing_sentence_sha256": sentence_hash,
        "reuters_attribution_verified": True,
        "reference_month_verified_in_body": True,
        "prospective_headline_mom_verified": True,
        "retrospective_language_found": False,
        "production_influence": "0",
    }
    extract = {
        "record_type": "CPI-E1-P11-REUTERS-MINIMAL-EXTRACT-v1",
        "protocol_sha256": PROTOCOL_SHA256,
        "event_ticker": TARGET_EVENT,
        "reference_month": TARGET_REFERENCE_MONTH,
        "value": str(value),
        "measure": input_record["measure"],
        "governing_published_at_utc": _iso(conservative),
        "load_bearing_sentence_sha256": sentence_hash,
        "full_wire_text_stored": False,
        "production_influence": "0",
    }
    coverage = {
        "record_type": "CPI-E1-P11-REUTERS-COVERAGE-v1",
        "protocol_sha256": PROTOCOL_SHA256,
        "event_ticker": TARGET_EVENT,
        "terminal_state": "PASS",
        "completed_at_utc": _iso(now),
        "receipt_sha256": _sha256_bytes(_json_bytes(receipt)),
        "extract_sha256": _sha256_bytes(_json_bytes(extract)),
        "production_influence": "0",
    }
    _write_json_exclusive(paths.reuters_receipt, receipt)
    _write_json_exclusive(paths.reuters_extract, extract)
    _write_json_exclusive(paths.reuters_coverage, coverage)
    return coverage


def close_pre_release(
    paths: RunPaths,
    *,
    clock: Callable[[], datetime] = _now,
) -> dict[str, Any]:
    ensure_protocol(paths)
    if paths.result.exists() or paths.failure.exists():
        return _read_json(paths.result if paths.result.exists() else paths.failure)
    now = clock()
    if now < PRE_RELEASE_DEADLINE:
        raise P11TimingError("pre-release closeout cannot run before 12:20Z")
    if not paths.preflight.is_file() or not paths.market_capture.is_file():
        return write_failure(
            paths,
            stage="PRE_RELEASE_CLOSEOUT",
            reason="MISSED_OR_INCOMPLETE_PRE_RELEASE_COLLECTION",
            now=now,
        )
    if paths.reuters_coverage.exists():
        return _read_json(paths.reuters_coverage)
    coverage = {
        "record_type": "CPI-E1-P11-REUTERS-COVERAGE-v1",
        "protocol_sha256": PROTOCOL_SHA256,
        "event_ticker": TARGET_EVENT,
        "terminal_state": "UNKNOWN_SEARCHED_NO_QUALIFYING_OBSERVATION",
        "completed_at_utc": _iso(now),
        "reason": "NO_ADMISSIBLE_PASS_RECEIPT_RECORDED_BY_FROZEN_12_20Z_CUTOFF",
        "production_influence": "0",
    }
    _write_json_exclusive(paths.reuters_coverage, coverage)
    return coverage


def collect_bls_truth(
    paths: RunPaths,
    *,
    clock: Callable[[], datetime] = _now,
) -> dict[str, Any]:
    ensure_protocol(paths)
    if paths.failure.exists():
        raise P11AuthorityError("terminal failure prevents later truth mutation")
    if paths.bls_initial_release.exists():
        return _read_json(paths.bls_initial_release)
    if clock() < BLS_RELEASE_AT:
        raise P11TimingError("BLS initial-release truth cannot be acquired before 12:30Z")

    issuance = acquire_and_issue_cpi_evidence(BLS_RELEASE_LOCATOR)
    observation = issue_cpi_initial_release_observation(issuance)
    validate_cpi_initial_release_observation(observation)
    if observation.reference_year != 2026 or observation.reference_month != 9:
        raise P11AuthorityError("BLS initial-release reference month mismatch")

    acquisition = issuance.acquisition_evidence
    receipt = {
        "record_type": RUN_RECORD_TYPE,
        "stage": "BLS_INITIAL_RELEASE",
        "protocol_sha256": PROTOCOL_SHA256,
        "event_ticker": TARGET_EVENT,
        "reference_month": TARGET_REFERENCE_MONTH,
        "source_locator": acquisition.source_locator,
        "acquired_at_utc": _iso(acquisition.acquired_at),
        "raw_body_sha256": acquisition.raw_body_sha256,
        "raw_body_b64": base64.b64encode(acquisition.raw_body).decode("ascii"),
        "acquisition_evidence_id": acquisition.evidence_id,
        "release_artifact_id": observation.release_artifact_id,
        "publication_evidence_id": observation.publication_evidence_id,
        "publication_timing_evidence_id": observation.publication_timing_evidence_id,
        "observation_id": observation.observation_id,
        "value": str(observation.value),
        "precision": observation.precision,
        "truth_authority": "BLS_INITIAL_RELEASE",
        "production_influence": "0",
    }
    _write_json_exclusive(paths.bls_initial_release, receipt)
    return receipt


def _load_market_rows(paths: RunPaths) -> list[dict[str, Any]]:
    capture = _read_json(paths.market_capture)
    rows = capture.get("markets")
    if not isinstance(rows, list) or len(rows) != EXPECTED_SIBLING_COUNT:
        raise P11AuthorityError("market capture row count is invalid")
    output: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise P11AuthorityError("market capture row is malformed")
        ticker = row.get("market_ticker")
        if not isinstance(ticker, str):
            raise P11AuthorityError("market capture ticker is malformed")
        candle = _read_json(paths.root / "kalshi" / "candles" / f"{ticker}.json")
        selected = candle.get("selected")
        if not isinstance(selected, dict):
            raise P11AuthorityError("candle evidence lacks selected observation")
        if selected.get("evidence_id") != row.get("evidence_id"):
            raise P11AuthorityError("market capture evidence identity mismatch")
        output.append(row)
    return output


def score_and_seal(paths: RunPaths, *, clock: Callable[[], datetime] = _now) -> dict[str, Any]:
    ensure_protocol(paths)
    if paths.failure.exists():
        return _read_json(paths.failure)
    if paths.result.exists():
        return _read_json(paths.result)
    if clock() < BLS_RELEASE_AT:
        raise P11TimingError("P11 score cannot run before BLS initial-release time")
    if not paths.preflight.is_file() or not paths.market_capture.is_file():
        return write_failure(
            paths,
            stage="SCORING",
            reason="MISSING_FROZEN_PRE_RELEASE_EVIDENCE",
            now=clock(),
        )
    if not paths.reuters_coverage.exists():
        close_pre_release(paths, clock=clock)
    if paths.failure.exists():
        return _read_json(paths.failure)
    if not paths.bls_initial_release.is_file():
        raise P11AuthorityError("BLS initial-release truth has not been acquired")

    coverage = _read_json(paths.reuters_coverage)
    truth = _read_json(paths.bls_initial_release)
    truth_value = _decimal(truth.get("value"), "BLS initial-release value")
    market_rows = _load_market_rows(paths)

    terminal = coverage.get("terminal_state")
    if terminal != "PASS":
        result = {
            "record_type": "CPI-E1-P11-PROSPECTIVE-RESULT-v1",
            "protocol_sha256": PROTOCOL_SHA256,
            "event_ticker": TARGET_EVENT,
            "reference_month": TARGET_REFERENCE_MONTH,
            "decision": ProspectiveDecision.INSUFFICIENT.value,
            "reason": "REUTERS_EVIDENCE_NOT_PASS",
            "reuters_terminal_state": terminal,
            "eligible_siblings": 0,
            "promotion_authority": "NONE",
            "phase1_economic_test_authorized": False,
            "production_influence": "0",
            "scored_at_utc": _iso(clock()),
        }
        _write_json_exclusive(paths.result, result)
        seal_manifest(paths)
        return result

    receipt = _read_json(paths.reuters_receipt)
    reuters_value = _decimal(receipt.get("value"), "Reuters value")

    rows: list[dict[str, Any]] = []
    reuters_correct = 0
    kalshi_correct = 0
    eligible = 0
    for row in market_rows:
        threshold = _decimal(row.get("threshold"), "market threshold")
        ask_raw = row.get("yes_ask")
        exclusion: str | None = None
        if ask_raw is None:
            exclusion = "MISSING_YES_ASK"
        else:
            ask = _decimal(ask_raw, "market yes_ask")
            if ask in {Decimal("0"), Decimal("1")}:
                exclusion = "BOUNDARY_YES_ASK"
            elif ask == Decimal("0.5"):
                exclusion = "KALSHI_TIE_0_5"
        truth_call = 1 if truth_value > threshold else 0
        if exclusion is not None:
            rows.append(
                {
                    "market_ticker": row["market_ticker"],
                    "threshold": str(threshold),
                    "yes_ask": ask_raw,
                    "truth_call": truth_call,
                    "eligible": False,
                    "exclusion": exclusion,
                }
            )
            continue
        assert ask_raw is not None
        ask = _decimal(ask_raw, "market yes_ask")
        reuters_call, kalshi_call = classify_directional_call(
            reuters_value=reuters_value,
            kalshi_yes_ask=ask,
            threshold=threshold,
        )
        if kalshi_call is None:
            raise P11AuthorityError("unexpected Kalshi tie reached eligible scorer")
        reuters_hit = int(reuters_call == truth_call)
        kalshi_hit = int(kalshi_call == truth_call)
        eligible += 1
        reuters_correct += reuters_hit
        kalshi_correct += kalshi_hit
        rows.append(
            {
                "market_ticker": row["market_ticker"],
                "threshold": str(threshold),
                "yes_ask": str(ask),
                "truth_call": truth_call,
                "reuters_call": reuters_call,
                "kalshi_call": kalshi_call,
                "reuters_correct": reuters_hit,
                "kalshi_correct": kalshi_hit,
                "eligible": True,
                "exclusion": None,
            }
        )

    decision = classify_event_result(
        reuters_correct=reuters_correct,
        kalshi_correct=kalshi_correct,
        eligible_siblings=eligible,
    )
    result = {
        "record_type": "CPI-E1-P11-PROSPECTIVE-RESULT-v1",
        "protocol_sha256": PROTOCOL_SHA256,
        "event_ticker": TARGET_EVENT,
        "reference_month": TARGET_REFERENCE_MONTH,
        "decision": decision.value,
        "eligible_siblings": eligible,
        "reuters_correct": reuters_correct,
        "kalshi_correct": kalshi_correct,
        "reuters_accuracy": (
            None if eligible == 0 else str(Decimal(reuters_correct) / Decimal(eligible))
        ),
        "kalshi_accuracy": (
            None if eligible == 0 else str(Decimal(kalshi_correct) / Decimal(eligible))
        ),
        "reuters_value": str(reuters_value),
        "bls_initial_release_value": str(truth_value),
        "rows": rows,
        "promotion_authority": "NONE",
        "phase1_economic_test_authorized": False,
        "production_influence": "0",
        "scored_at_utc": _iso(clock()),
    }
    _write_json_exclusive(paths.result, result)
    seal_manifest(paths)
    return result


def write_failure(
    paths: RunPaths,
    *,
    stage: str,
    reason: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    ensure_protocol(paths)
    if paths.result.exists():
        raise P11AuthorityError("result already exists; failure cannot be added")
    if paths.failure.exists():
        return _read_json(paths.failure)
    timestamp = now or _now()
    receipt = {
        "record_type": "CPI-E1-P11-PROSPECTIVE-FAILURE-v1",
        "protocol_sha256": PROTOCOL_SHA256,
        "event_ticker": TARGET_EVENT,
        "reference_month": TARGET_REFERENCE_MONTH,
        "stage": stage,
        "reason": reason,
        "failed_at_utc": _iso(timestamp),
        "decision": ProspectiveDecision.INSUFFICIENT.value,
        "no_backfill": True,
        "promotion_authority": "NONE",
        "phase1_economic_test_authorized": False,
        "production_influence": "0",
    }
    _write_json_exclusive(paths.failure, receipt)
    seal_manifest(paths)
    return receipt


def seal_manifest(paths: RunPaths) -> None:
    if paths.manifest.exists():
        raise P11AuthorityError("terminal manifest already exists")
    files = sorted(
        path
        for path in paths.root.rglob("*")
        if path.is_file() and path != paths.manifest
    )
    lines = [
        f"{_sha256_file(path)}  {path.relative_to(paths.root).as_posix()}"
        for path in files
    ]
    _write_bytes_exclusive(paths.manifest, ("\n".join(lines) + "\n").encode())


def verify_terminal_manifest(paths: RunPaths) -> None:
    if not paths.manifest.is_file():
        raise P11AuthorityError("terminal manifest is missing")
    lines = paths.manifest.read_text().splitlines()
    expected: dict[str, str] = {}
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if match is None:
            raise P11AuthorityError("terminal manifest line is malformed")
        digest, relative = match.groups()
        if relative in expected:
            raise P11AuthorityError("terminal manifest contains duplicate path")
        expected[relative] = digest
    actual_paths = {
        path.relative_to(paths.root).as_posix(): path
        for path in paths.root.rglob("*")
        if path.is_file() and path != paths.manifest
    }
    if set(expected) != set(actual_paths):
        raise P11AuthorityError("terminal manifest file set mismatch")
    for relative, path in actual_paths.items():
        if _sha256_file(path) != expected[relative]:
            raise P11AuthorityError(f"terminal artifact hash mismatch: {relative}")
