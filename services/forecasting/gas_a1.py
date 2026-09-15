"""GAS-A1: append-only, research-only AAA/Kalshi prospective evidence capture.

This module deliberately produces observations and diagnostics only.  It has no account client,
write transport, order model, alerting, or execution dependency.
"""

from __future__ import annotations

import hashlib
import html.parser
import http.client
import json
import re
import sqlite3
import ssl
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit

from services.market_universe.orderbook_snapshot import acquire_orderbook_snapshot
from services.market_universe.public_read import get, get_market_with_body
from services.opportunity_engine.fees import FeeType, calculate_fee, current_event_formula_policy

AAA_HOST = "gasprices.aaa.com"
AAA_URLS = ("https://gasprices.aaa.com/", "https://gasprices.aaa.com/state-gas-price-averages/")
SERIES = "KXAAAGASD"
ZERO = Decimal(0)
MAX_AAA_BYTES = 2_000_000
PREDICTOR_VERSION = "gas-a1-predictors-v1"


class GasA1Error(RuntimeError):
    """A required authority, source, schema, or persistence invariant failed."""


class DiscoveryError(GasA1Error):
    """A series-scoped market discovery attempt did not complete safely."""

    def __init__(self, classification: str, detail: str) -> None:
        super().__init__(detail)
        self.classification = classification


class AaaTransport(Protocol):
    def get(self, url: str) -> tuple[bytes, datetime]: ...


class AaaHttpTransport:
    """Fixed-origin HTTPS GET transport for the two reviewed AAA pages."""

    def get(self, url: str) -> tuple[bytes, datetime]:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.netloc != AAA_HOST or parts.fragment:
            raise GasA1Error("AAA URL is outside the reviewed fixed origin")
        path = parts.path or "/"
        if path not in {"/", "/state-gas-price-averages/"} or parts.query:
            raise GasA1Error("AAA URL is outside the reviewed page allowlist")
        observed = datetime.now(UTC)
        connection = http.client.HTTPSConnection(
            AAA_HOST, timeout=15, context=ssl.create_default_context()
        )
        try:
            connection.request("GET", path, headers={"Accept": "text/html"})
            response = connection.getresponse()
            body = response.read(MAX_AAA_BYTES + 1)
            if response.status != 200 or len(body) > MAX_AAA_BYTES:
                raise GasA1Error("AAA page unavailable or exceeded bounded size")
            return body, observed
        except (OSError, TimeoutError, http.client.HTTPException) as exc:
            raise GasA1Error(f"AAA page request failed: {exc}") from exc
        finally:
            connection.close()


class _TableParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def _money(value: str) -> Decimal:
    try:
        return Decimal(value.replace("$", "").replace(",", "").strip())
    except InvalidOperation as exc:
        raise GasA1Error(f"malformed AAA dollar value: {value!r}") from exc


def parse_aaa_html(
    body: bytes, *, url: str, observed_at: datetime, require_national: bool = True
) -> tuple[dict[str, Any], ...]:
    """Parse only explicit AAA regular-gas table cells; preserve source identity."""
    parser = _TableParser()
    try:
        parser.feed(body.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise GasA1Error("AAA HTML is not UTF-8") from exc
    rows: list[dict[str, Any]] = []
    digest = hashlib.sha256(body).hexdigest()
    for cells in parser.rows:
        if len(cells) >= 2 and cells[0] in {
            "Current Avg.",
            "Yesterday Avg.",
            "Week Ago Avg.",
            "Month Ago Avg.",
        }:
            rows.append(
                {
                    "value": str(_money(cells[1])),
                    "unit": "USD/gallon",
                    "geography": "US",
                    "date_label": cells[0],
                    "source_identity": "AAA Fuel Prices",
                    "url": url,
                    "observed_at": observed_at.isoformat(),
                    "raw_sha256": digest,
                }
            )
        elif (
            len(cells) >= 2
            and cells[0] not in {"State", "Regular", "Mid-Grade", "Premium", "Diesel"}
            and cells[1].startswith("$")
        ):
            rows.append(
                {
                    "value": str(_money(cells[1])),
                    "unit": "USD/gallon",
                    "geography": cells[0],
                    "date_label": "current",
                    "source_identity": "AAA Fuel Prices",
                    "url": url,
                    "observed_at": observed_at.isoformat(),
                    "raw_sha256": digest,
                }
            )
    if require_national and not any(
        row["geography"] == "US" and row["date_label"] == "Current Avg." for row in rows
    ):
        raise GasA1Error("AAA national regular-gas table was absent or ambiguous")
    if not require_national and not any(row["geography"] != "US" for row in rows):
        raise GasA1Error("AAA state regular-gas table was absent or ambiguous")
    return tuple(rows)


_DATE_RE = re.compile(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b")
_TEXT_DATE_RE = re.compile(
    r"\b([A-Za-z]{3,9})"
    r"\s+(\d{1,2}),\s+(20\d{2})\b",
    re.I,
)
_MONTHS = {
    name: index
    for index, name in enumerate(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
        1,
    )
}
_MONTHS.update({name[:3]: value for name, value in _MONTHS.items()})
_STRIKE_RE = re.compile(r"(?:\$|USD\s*)(\d+(?:\.\d+)?)", re.I)
_COMPARE_RE = re.compile(r"\b(above|below|greater than|less than|at least|under|over)\b", re.I)


@dataclass(frozen=True, slots=True)
class MarketAuthority:
    ticker: str
    event_ticker: str
    target_date: date
    strike: Decimal
    comparator: str
    status: str
    rules_primary: str
    rules_secondary: str
    settlement_source: str
    settlement_url: str
    close_time: str | None
    settlement_time: str | None
    fee_type: str
    fee_multiplier: Decimal
    raw_body_sha256: str
    observed_at: str
    close_target_relation: str
    latency_eligible: bool


def parse_active_market(
    raw: dict[str, Any],
    *,
    raw_sha256: str,
    observed_at: datetime,
    series_fee_type: str | None = None,
    series_fee_multiplier: Decimal | None = None,
) -> MarketAuthority:
    if raw.get("status") != "active":
        raise GasA1Error("market is not ACTIVE")
    ticker = raw.get("ticker")
    event = raw.get("event_ticker")
    if not isinstance(ticker, str) or not ticker.startswith(SERIES) or not isinstance(event, str):
        raise GasA1Error("market identity is not KXAAAGASD")
    primary = raw.get("rules_primary")
    secondary = raw.get("rules_secondary", "")
    if (
        not isinstance(primary, str)
        or not isinstance(secondary, str)
        or "AAA" not in (primary + secondary).upper()
    ):
        raise GasA1Error("exact AAA settlement authority is absent")
    text = " ".join(
        str(raw.get(key, "")) for key in ("title", "subtitle", "yes_sub_title", "rules_primary")
    )
    match = _STRIKE_RE.search(text)
    comparator = _COMPARE_RE.search(text)
    dates = _DATE_RE.findall(text)
    text_dates = _TEXT_DATE_RE.findall(text)
    if not match or not comparator or len(dates) + len(text_dates) != 1:
        raise GasA1Error("unsupported rule shape or malformed strike/date")
    try:
        if dates:
            target = date(int(dates[0][0]), int(dates[0][1]), int(dates[0][2]))
        else:
            target = date(
                int(text_dates[0][2]),
                _MONTHS[text_dates[0][0].capitalize()],
                int(text_dates[0][1]),
            )
        strike = Decimal(match.group(1))
        multiplier = series_fee_multiplier or Decimal(str(raw.get("fee_multiplier")))
    except (KeyError, ValueError, InvalidOperation) as exc:
        raise GasA1Error("malformed market date, strike, or fee multiplier") from exc
    if strike <= 0 or multiplier < 0:
        raise GasA1Error("malformed strike or fee multiplier")
    source_url = "https://gasprices.aaa.com/"
    close_time = raw.get("close_time")
    settlement_time = raw.get("settlement_ts", raw.get("expiration_time"))
    if not isinstance(close_time, str) or not isinstance(settlement_time, str):
        raise GasA1Error("close or settlement timing is missing")
    try:
        close_at = datetime.fromisoformat(close_time.replace("Z", "+00:00")).astimezone(UTC)
        settlement_at = datetime.fromisoformat(settlement_time.replace("Z", "+00:00")).astimezone(
            UTC
        )
    except ValueError as exc:
        raise GasA1Error("close or settlement timing is malformed") from exc
    if close_at >= settlement_at:
        raise GasA1Error("market close is not before settlement")
    relation = (
        "CLOSE_BEFORE_TARGET_DATE" if close_at.date() < target else "CLOSE_ON_OR_AFTER_TARGET_DATE"
    )
    return MarketAuthority(
        ticker,
        event,
        target,
        strike,
        comparator.group(1).lower(),
        raw["status"],
        primary,
        secondary,
        "AAA Fuel Prices",
        source_url,
        close_time,
        settlement_time,
        series_fee_type or str(raw.get("fee_type")),
        multiplier,
        raw_sha256,
        observed_at.isoformat(),
        relation,
        relation == "CLOSE_BEFORE_TARGET_DATE",
    )


def measure_update_ordering(
    national: tuple[tuple[datetime, str], ...],
    states: tuple[tuple[datetime, str], ...],
    *,
    tolerance_seconds: int = 120,
) -> dict[str, Any]:
    """Compare first observed hash changes, without treating sequential GETs as publication time."""
    if not national or not states:
        return {"classification": "INDETERMINATE", "reason": "insufficient point-in-time snapshots"}
    national_changes = tuple(
        national[index]
        for index in range(1, len(national))
        if national[index][1] != national[index - 1][1]
    )
    state_changes = tuple(
        states[index] for index in range(1, len(states)) if states[index][1] != states[index - 1][1]
    )
    if not national_changes or not state_changes:
        return {"classification": "INDETERMINATE", "reason": "no paired page change observed"}
    n_time, _ = national_changes[0]
    s_time, _ = state_changes[0]
    delta = (n_time - s_time).total_seconds()
    if abs(delta) <= tolerance_seconds:
        classification = "SYNCHRONIZED"
    elif s_time < n_time:
        classification = "STATE_BEFORE_NATIONAL"
    else:
        classification = "NATIONAL_BEFORE_STATE"
    return {
        "classification": classification,
        "national_change_at": n_time.isoformat(),
        "state_change_at": s_time.isoformat(),
        "delta_seconds": delta,
        "latency_claim_allowed": classification == "STATE_BEFORE_NATIONAL",
    }


def conservative_taker_debits(
    *,
    yes_bids: list[list[str]],
    no_bids: list[list[str]],
    fee_type: str,
    fee_multiplier: Decimal,
    quantity: Decimal = Decimal("1"),
) -> dict[str, Any]:
    """Calculate research-only all-in debits from displayed opposite-side bids."""
    if quantity <= 0:
        raise GasA1Error("quantity must be positive")
    try:
        policy = current_event_formula_policy(
            fee_type=FeeType(fee_type),
            fee_multiplier=fee_multiplier,
        )
    except Exception as exc:
        raise GasA1Error(f"fee metadata is unsupported: {exc}") from exc

    def walk(rows: list[list[str]], side: str) -> tuple[Decimal, Decimal, Decimal]:
        asks = sorted(
            ((Decimal("1") - Decimal(row[0]), Decimal(row[1])) for row in rows),
            key=lambda item: item[0],
        )
        remaining = quantity
        debit = Decimal(0)
        displayed = Decimal(0)
        for price, size in asks:
            fill = min(remaining, size)
            if fill > 0:
                debit += price * fill + calculate_fee(policy, price, fill).total_fee
                displayed += fill
                remaining -= fill
            if remaining == 0:
                break
        if remaining > 0:
            raise GasA1Error(f"insufficient displayed {side} depth")
        return debit, displayed, asks[0][0] if asks else Decimal(0)

    yes_debit, yes_depth, yes_best = walk(no_bids, "YES")
    no_debit, no_depth, no_best = walk(yes_bids, "NO")
    return {
        "quantity": str(quantity),
        "yes_all_in_debit": str(yes_debit),
        "no_all_in_debit": str(no_debit),
        "yes_displayed_depth": str(yes_depth),
        "no_displayed_depth": str(no_depth),
        "yes_best_ask": str(yes_best),
        "no_best_ask": str(no_best),
        "fee_policy_id": policy.policy_id,
    }


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
 run_id TEXT PRIMARY KEY, registered_at TEXT NOT NULL,
 research_only INTEGER NOT NULL CHECK(research_only=1),
 production_influence TEXT NOT NULL CHECK(production_influence='0'));
CREATE TABLE IF NOT EXISTS market_observations(
 id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(run_id, payload));
CREATE TABLE IF NOT EXISTS source_observations(
 id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS book_observations(
 id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, ticker TEXT NOT NULL,
 observed_at TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS failures(
 id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL,
 observed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS diagnostics(
 id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, ticker TEXT, observed_at TEXT NOT NULL,
 kind TEXT NOT NULL, payload TEXT NOT NULL);
"""


def _json_value(value: Any) -> Any:
    if isinstance(value, (Decimal, date, datetime)):
        return str(value)
    return value


class GasA1Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.executescript(SCHEMA)

    def register_run(self, run_id: str, at: datetime) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO runs VALUES (?, ?, 1, '0')", (run_id, at.isoformat()))

    def _insert(self, table: str, columns: tuple[str, ...], values: tuple[Any, ...]) -> None:
        marks = ",".join("?" for _ in columns)
        with sqlite3.connect(self.path) as db:
            db.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({marks})", values)  # noqa: S608

    def source(self, run_id: str, payload: dict[str, Any]) -> None:
        self._insert(
            "source_observations",
            ("run_id", "payload"),
            (run_id, json.dumps(payload, sort_keys=True)),
        )

    def market(self, run_id: str, payload: dict[str, Any]) -> None:
        self._insert(
            "market_observations",
            ("run_id", "payload"),
            (run_id, json.dumps(payload, sort_keys=True)),
        )

    def book(self, run_id: str, ticker: str, payload: dict[str, Any], at: datetime) -> None:
        self._insert(
            "book_observations",
            ("run_id", "ticker", "observed_at", "payload"),
            (run_id, ticker, at.isoformat(), json.dumps(payload, sort_keys=True)),
        )

    def failure(self, run_id: str, kind: str, detail: str, at: datetime) -> None:
        self._insert(
            "failures",
            ("run_id", "kind", "detail", "observed_at"),
            (run_id, kind, detail, at.isoformat()),
        )

    def diagnostic(
        self, run_id: str, ticker: str | None, kind: str, payload: dict[str, Any], at: datetime
    ) -> None:
        self._insert(
            "diagnostics",
            ("run_id", "ticker", "observed_at", "kind", "payload"),
            (run_id, ticker, at.isoformat(), kind, json.dumps(payload, sort_keys=True)),
        )


def _market_rows(payload: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    rows = payload.get("markets")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise GasA1Error("Kalshi market response shape is unsupported")
    if any(not str(row.get("ticker", "")).startswith(SERIES + "-") for row in rows):
        raise DiscoveryError(
            "UNSUPPORTED_RESPONSE", "series-scoped response contained a foreign ticker"
        )
    return tuple(row for row in rows if isinstance(row, dict))


def discover_series_markets(
    fetch: Any = get, *, max_pages: int = 250
) -> tuple[dict[str, Any], ...]:
    """Discover only KXAAAGASD markets, consuming every deterministic cursor page."""
    if max_pages <= 0:
        raise DiscoveryError("PAGINATION_INCOMPLETE", "max_pages must be positive")
    cursor: str | None = None
    seen_cursors: set[str] = set()
    discovered: list[dict[str, Any]] = []
    for _page in range(max_pages):
        parameters = {"series_ticker": SERIES, "status": "open", "limit": "1000"}
        if cursor is not None:
            parameters["cursor"] = cursor
        path = "/trade-api/v2/markets?" + urlencode(parameters)
        try:
            evidence = fetch(path)
        except Exception as exc:
            raise DiscoveryError("DISCOVERY_FAILED", str(exc)) from exc
        if not isinstance(evidence, dict) or evidence.get("classification") != "SUCCESS":
            raise DiscoveryError("DISCOVERY_FAILED", "series-scoped public response failed")
        payload = evidence.get("payload")
        if not isinstance(payload, dict):
            raise DiscoveryError("UNSUPPORTED_RESPONSE", "series-scoped payload is not an object")
        try:
            discovered.extend(_market_rows(payload))
        except DiscoveryError:
            raise
        next_cursor = payload.get("cursor")
        if next_cursor in (None, ""):
            return tuple(discovered)
        if not isinstance(next_cursor, str) or next_cursor in seen_cursors:
            raise DiscoveryError("PAGINATION_INCOMPLETE", "cursor was malformed or repeated")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    raise DiscoveryError("PAGINATION_INCOMPLETE", "series-scoped pagination exceeded max_pages")


def parse_series_metadata(payload: dict[str, Any]) -> tuple[str, Decimal]:
    series = payload.get("series")
    if not isinstance(series, dict) or series.get("ticker") != SERIES:
        raise DiscoveryError("UNSUPPORTED_RESPONSE", "series metadata identity is invalid")
    sources = series.get("settlement_sources")
    if not isinstance(sources, list) or len(sources) != 1 or not isinstance(sources[0], dict):
        raise DiscoveryError("UNSUPPORTED_RESPONSE", "series settlement source is ambiguous")
    if sources[0].get("name") != "AAA" or sources[0].get("url") != AAA_URLS[0]:
        raise DiscoveryError("UNSUPPORTED_RESPONSE", "series settlement source is not exact AAA")
    try:
        fee_type = FeeType(str(series["fee_type"]))
        multiplier = Decimal(str(series["fee_multiplier"]))
    except (KeyError, ValueError, InvalidOperation) as exc:
        raise DiscoveryError("UNSUPPORTED_RESPONSE", "series fee metadata is malformed") from exc
    return str(fee_type), multiplier


def collect_once(
    store: GasA1Store,
    *,
    run_id: str | None = None,
    aaa: AaaTransport | None = None,
    discovery_fetch: Any = get,
) -> str:
    now = datetime.now(UTC)
    run_id = run_id or hashlib.sha256(f"gas-a1:{now.isoformat()}".encode()).hexdigest()
    store.register_run(run_id, now)
    aaa_transport = aaa or AaaHttpTransport()
    try:
        rows = discover_series_markets(discovery_fetch)
    except DiscoveryError as exc:
        store.failure(run_id, exc.classification, str(exc), datetime.now(UTC))
        return run_id
    if not rows:
        store.failure(
            run_id,
            "NO_MARKET",
            "no KXAAAGASD candidates in completed series-scoped query",
            datetime.now(UTC),
        )
        return run_id
    try:
        series_evidence = get("/trade-api/v2/series/" + SERIES)
        series_payload = series_evidence.get("payload")
        if series_evidence.get("classification") != "SUCCESS" or not isinstance(
            series_payload, dict
        ):
            raise DiscoveryError("DISCOVERY_FAILED", "series metadata request failed")
        fee_type, fee_multiplier = parse_series_metadata(series_payload)
    except DiscoveryError as exc:
        store.failure(run_id, exc.classification, str(exc), datetime.now(UTC))
        return run_id
    except Exception as exc:
        store.failure(run_id, "DISCOVERY_FAILED", str(exc), datetime.now(UTC))
        return run_id
    markets: list[MarketAuthority] = []
    for candidate in rows:
        try:
            ticker = candidate["ticker"]
            evidence, _ = get_market_with_body(ticker)
            if evidence.get("classification") != "SUCCESS" or not isinstance(
                evidence.get("payload"), dict
            ):
                raise GasA1Error("single-market evidence failed")
            payload = evidence.get("payload")
            if not isinstance(payload, dict) or not isinstance(payload.get("market"), dict):
                raise GasA1Error("single-market payload shape is unsupported")
            market = parse_active_market(
                payload["market"],
                raw_sha256=str(evidence["body_sha256"]),
                observed_at=now,
                series_fee_type=fee_type,
                series_fee_multiplier=fee_multiplier,
            )
            markets.append(market)
            store.market(
                run_id,
                {
                    field: _json_value(value)
                    for field in market.__dataclass_fields__
                    for value in (getattr(market, field),)
                },
            )
        except Exception as exc:
            store.failure(run_id, "MARKET_REJECTED", str(exc), datetime.now(UTC))
    for url in AAA_URLS:
        try:
            body, observed = aaa_transport.get(url)
            for row in parse_aaa_html(
                body, url=url, observed_at=observed, require_national=url == AAA_URLS[0]
            ):
                store.source(run_id, row)
            store.diagnostic(
                run_id,
                None,
                "AAA UPDATE OBSERVED",
                {
                    "url": url,
                    "raw_sha256": hashlib.sha256(body).hexdigest(),
                    "predictor_version": PREDICTOR_VERSION,
                },
                observed,
            )
        except Exception as exc:
            store.failure(run_id, "AAA_SOURCE_FAILURE", str(exc), datetime.now(UTC))
    for market in markets:
        try:
            snapshot = acquire_orderbook_snapshot(market.ticker)
            payload = snapshot.to_json()
            at = snapshot.observed_at
            store.book(run_id, market.ticker, payload, at)
            economics: dict[str, Any] = {
                "fee_type": market.fee_type,
                "fee_multiplier": str(market.fee_multiplier),
            }
            if snapshot.succeeded:
                try:
                    economics["conservative_taker_debits"] = conservative_taker_debits(
                        yes_bids=payload["yes_levels"],
                        no_bids=payload["no_levels"],
                        fee_type=market.fee_type,
                        fee_multiplier=market.fee_multiplier,
                    )
                except GasA1Error as exc:
                    store.failure(run_id, "COST_DIAGNOSTIC_FAILURE", f"{market.ticker}: {exc}", at)
            store.diagnostic(
                run_id,
                market.ticker,
                "KALSHI BOOK SNAPSHOT",
                {
                    "classification": snapshot.classification,
                    "yes_levels": payload["yes_levels"],
                    "no_levels": payload["no_levels"],
                    "fee_type": market.fee_type,
                    "fee_multiplier": str(market.fee_multiplier),
                },
                at,
            )
            store.diagnostic(run_id, market.ticker, "EXECUTABLE COST DIAGNOSTIC", economics, at)
        except Exception as exc:
            store.failure(run_id, "ORDERBOOK_FAILURE", f"{market.ticker}: {exc}", datetime.now(UTC))
    return run_id
