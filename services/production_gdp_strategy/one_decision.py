"""The smallest research-only GDPNow -> KXGDP decision path.

The exported one-shot entrypoint owns its acquisition composition and accepts no
caller-supplied source, response, evidence, or clock.  The lower-level evaluator in
this module is deliberately private and fixture-only: its self-consistent values prove
policy logic and integrity, not real-source provenance.  Provenance belongs to fixed,
reviewed acquisition functions, which are not yet configured for schedule or fee data.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from services.forecasting.gdpnow_parsing import (
    ParsedGDPNowVintage,
    validate_parsed_gdpnow_vintage,
)
from services.forecasting.gdpnow_source_acquisition import (
    GDPNowAcquisitionEvidence,
    validate_gdpnow_acquisition_evidence,
)
from services.market_universe.domain import (
    Market,
    MarketStatus,
    UniverseValidationError,
    stable_hash,
)
from services.market_universe.public_read import (
    get_market_with_body,
    get_orderbook_with_body,
)
from services.opportunity_engine.fees import FeePolicy, FeeType, calculate_fee

POLICY_VERSION = "d1-g2-p1-one-decision-v1"
ENTRY_RULE_VERSION = "d1-g2-fixed-low-debit-v1"
TRADABILITY_PROTOCOL_VERSION = "d1-g2-status-book-status-v1"
MAX_STATUS_BOOK_STATUS_WINDOW_NS = 2_000_000_000
MAX_STATUS_BOOK_STATUS_WINDOW_MS = 2000
DECISION_MARGIN = timedelta(minutes=3)
ZERO = Decimal("0")
ONE = Decimal("1.00")
GATE = Decimal("0.50")
KXGDP = "KXGDP"
METRIC = "US real GDP quarter-over-quarter growth, seasonally adjusted annual rate"
SETTLEMENT_EDITION = "BEA Advance Estimate"
TIMEZONE_NAME = "America/New_York"
BOOK_PATH_PREFIX = "/trade-api/v2/markets/orderbooks?tickers="


class DecisionError(ValueError):
    """A decision input or immutable research receipt failed closed."""


class DecisionClass(StrEnum):
    EVIDENCE_INCOMPLETE = "EVIDENCE_INCOMPLETE"
    EXECUTION_NOT_IDENTIFIABLE = "EXECUTION_NOT_IDENTIFIABLE"
    ABSTAIN = "ABSTAIN"
    TRADE_YES = "TRADE_YES"
    TRADE_NO = "TRADE_NO"


class SignalSide(StrEnum):
    YES = "YES"
    NO = "NO"


@dataclass(frozen=True, slots=True)
class _ClockSample:
    wall_utc: datetime
    monotonic_ns: int


_Clock = Callable[[], _ClockSample]


def _system_clock() -> _ClockSample:
    return _ClockSample(datetime.now(UTC), time.monotonic_ns())


@dataclass(frozen=True, slots=True)
class _TransportResponse:
    """Disposable transport-shaped fixture output, never source authority."""

    path: str
    status: int
    body: bytes


class _ResearchDecisionSource(Protocol):
    """Private fixture seam; not a canonical acquisition authority."""

    def schedule(self) -> _TransportResponse: ...

    def market(self, ticker: str) -> _TransportResponse: ...

    def orderbook(self, ticker: str) -> _TransportResponse: ...

    def fee(self) -> _TransportResponse: ...


class _FixedPublicKXGDPSource:
    """Public Kalshi transport for market/orderbook reads.

    Schedule and fee authority are intentionally supplied by a separately reviewed adapter;
    this class does not infer either from current metadata.
    """

    def market(self, ticker: str) -> _TransportResponse:
        evidence, body = get_market_with_body(ticker)
        status = evidence.get("status", 0)
        if type(status) is not int:
            raise DecisionError("public market transport returned malformed status")
        return _TransportResponse(str(evidence.get("path", "")), status, body)

    def orderbook(self, ticker: str) -> _TransportResponse:
        evidence, body = get_orderbook_with_body(ticker)
        status = evidence.get("status", 0)
        if type(status) is not int:
            raise DecisionError("public orderbook transport returned malformed status")
        return _TransportResponse(str(evidence.get("path", "")), status, body)

    def schedule(self) -> _TransportResponse:
        raise DecisionError("reviewed BEA/Kalshi schedule transport is not configured")

    def fee(self) -> _TransportResponse:
        raise DecisionError("reviewed fee-authority transport is not configured")


def _utc(value: datetime, field: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise DecisionError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _response_hash(response: _TransportResponse) -> str:
    if type(response) is not _TransportResponse or type(response.path) is not str:
        raise DecisionError("transport response type is invalid")
    if type(response.status) is not int or isinstance(response.status, bool):
        raise DecisionError("transport status is invalid")
    if type(response.body) is not bytes or not response.body:
        raise DecisionError("transport response body is invalid")
    return hashlib.sha256(response.body).hexdigest()


def _json(response: _TransportResponse, expected_path: str) -> dict[str, object]:
    if response.path != expected_path or response.status != 200:
        raise DecisionError("source response path or status is not authoritative")
    _response_hash(response)
    try:
        value = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DecisionError("source response is not valid JSON") from exc
    if not isinstance(value, dict):
        raise DecisionError("source response must be an object")
    return value


def _parse_local_instant(value: object, *, zone_name: object, field: str) -> datetime | date:
    if type(value) is not str or type(zone_name) is not str or zone_name != TIMEZONE_NAME:
        raise DecisionError(f"{field} timezone authority is invalid")
    if len(value) == 10:
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise DecisionError(f"{field} local date is malformed") from exc
    try:
        zone = ZoneInfo(zone_name)
        local = datetime.fromisoformat(value)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise DecisionError(f"{field} local time is malformed") from exc
    if local.tzinfo is not None:
        raise DecisionError(f"{field} must be a local civil time without an embedded offset")
    candidates: dict[datetime, datetime] = {}
    for fold in (0, 1):
        candidate = local.replace(tzinfo=zone, fold=fold)
        instant = candidate.astimezone(UTC)
        if instant.astimezone(zone).replace(tzinfo=None) == local:
            candidates[instant] = candidate
    if len(candidates) != 1:
        raise DecisionError(f"{field} is ambiguous or nonexistent under IANA timezone")
    return next(iter(candidates)).astimezone(UTC)


def _require_exact_instant(value: datetime | date, field: str) -> datetime:
    """Reject lower-precision schedule values where chronology needs an instant."""
    if type(value) is not datetime:
        raise DecisionError(f"{field} must provide an exact local time")
    return value


def _parse_aware(value: object, field: str) -> datetime:
    if type(value) is not str:
        raise DecisionError(f"{field} timestamp is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DecisionError(f"{field} timestamp is malformed") from exc
    return _utc(parsed, field)


def _decimal(value: object, field: str) -> Decimal:
    if type(value) is not str:
        raise DecisionError(f"{field} must be an exact Decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise DecisionError(f"{field} is malformed") from exc
    if not result.is_finite():
        raise DecisionError(f"{field} is nonfinite")
    return result


def _has_sub_cent_precision(value: Decimal) -> bool:
    exponent = value.as_tuple().exponent
    return isinstance(exponent, int) and -exponent > 2


@dataclass(frozen=True, slots=True, init=False)
class _ScheduleEvidence:
    evidence_id: str
    body: bytes
    body_sha256: str
    event_ticker: str
    market_ticker: str
    target_quarter: str
    release_at: datetime
    open_at: datetime
    close_at: datetime
    thresholds: tuple[Decimal, ...]
    raw: dict[str, object]

    def __init__(
        self, *, response: _TransportResponse, completed_at: datetime, _capability: object
    ) -> None:
        if _capability is not _ISSUER:
            raise DecisionError("schedule evidence requires reviewed issuer")
        payload = _json(response, "/reviewed/d1-g2/schedule")
        raw = payload.get("schedule")
        if not isinstance(raw, dict):
            raise DecisionError("schedule authority object is missing")
        if raw.get("series_ticker") != KXGDP or raw.get("metric") != METRIC:
            raise DecisionError("KXGDP metric authority is unsupported")
        if raw.get("settlement_edition") != SETTLEMENT_EDITION:
            raise DecisionError("settlement edition is not BEA Advance Estimate")
        event_ticker = raw.get("event_ticker")
        market_ticker = raw.get("market_ticker")
        quarter = raw.get("target_quarter")
        if not all(isinstance(v, str) and v for v in (event_ticker, market_ticker, quarter)):
            raise DecisionError("schedule identities are incomplete")
        zone = raw.get("timezone")
        release = _require_exact_instant(
            _parse_local_instant(raw.get("bea_release_local"), zone_name=zone, field="BEA release"),
            "BEA release",
        )
        opened = _require_exact_instant(
            _parse_local_instant(raw.get("market_open_local"), zone_name=zone, field="market open"),
            "market open",
        )
        closed = _require_exact_instant(
            _parse_local_instant(
                raw.get("market_close_local"), zone_name=zone, field="market close"
            ),
            "market close",
        )
        threshold_raw = raw.get("listed_thresholds")
        if not isinstance(threshold_raw, list) or not threshold_raw:
            raise DecisionError("listed threshold authority is missing")
        thresholds = tuple(_decimal(item, "threshold") for item in threshold_raw)
        if len(set(thresholds)) != len(thresholds):
            raise DecisionError("duplicate threshold authority")
        completed = _utc(completed_at, "schedule completion")
        digest = stable_hash(
            (
                response.path,
                response.status,
                hashlib.sha256(response.body).hexdigest(),
                completed.isoformat(),
            )
        )
        object.__setattr__(self, "evidence_id", digest)
        object.__setattr__(self, "body", response.body)
        object.__setattr__(self, "body_sha256", hashlib.sha256(response.body).hexdigest())
        object.__setattr__(self, "event_ticker", event_ticker)
        object.__setattr__(self, "market_ticker", market_ticker)
        object.__setattr__(self, "target_quarter", quarter)
        object.__setattr__(self, "release_at", release)
        object.__setattr__(self, "open_at", opened)
        object.__setattr__(self, "close_at", closed)
        object.__setattr__(self, "thresholds", thresholds)
        object.__setattr__(self, "raw", raw)
        _register_issued(self, digest)


@dataclass(frozen=True, slots=True, init=False)
class _MarketEvidence:
    evidence_id: str
    body: bytes
    body_sha256: str
    ticker: str
    event_ticker: str
    status: MarketStatus
    open_at: datetime
    close_at: datetime
    rules_hash: str
    metadata_hash: str
    threshold: Decimal
    raw: dict[str, object]
    request_start: datetime
    response_complete: datetime
    request_start_mono_ns: int
    response_complete_mono_ns: int

    def __init__(
        self,
        *,
        response: _TransportResponse,
        start: _ClockSample,
        end: _ClockSample,
        _capability: object,
    ) -> None:
        if _capability is not _ISSUER:
            raise DecisionError("market evidence requires reviewed issuer")
        payload = _json(response, "") if response.path == "" else None
        if payload is None:
            try:
                payload = _json(response, response.path)
            except DecisionError:
                payload = _decode_market(response)
        raw = payload.get("market")
        if not isinstance(raw, dict):
            raise DecisionError("market authority object is missing")
        try:
            market = Market.parse(raw)
        except UniverseValidationError as exc:
            raise DecisionError("market authority failed canonical parse") from exc
        expected_path = f"/trade-api/v2/markets/{market.ticker}"
        if response.path != expected_path:
            raise DecisionError("market endpoint does not bind the returned market ticker")
        if market.ticker != raw.get("ticker") or not market.ticker.startswith("KXGDP"):
            raise DecisionError("market is not positively bound to KXGDP")
        rules = raw.get("rules_primary")
        if (
            not isinstance(rules, str)
            or "real GDP" not in rules
            or "seasonally adjusted" not in rules
            or "BEA Advance Estimate" not in rules
            or "more than" not in rules
        ):
            raise DecisionError("KXGDP settlement rule semantics are unsupported")
        try:
            opened = _parse_aware(raw.get("open_time"), "market open")
            closed = _parse_aware(raw.get("close_time"), "market close")
            threshold = _decimal(raw.get("threshold"), "market threshold")
        except DecisionError:
            raise
        if closed <= opened:
            raise DecisionError("market open/close interval is invalid")
        end_wall = _utc(end.wall_utc, "market response completion")
        start_wall = _utc(start.wall_utc, "market request start")
        if end.monotonic_ns < start.monotonic_ns:
            raise DecisionError("market monotonic clock regressed")
        digest = stable_hash(
            (
                response.path,
                response.status,
                hashlib.sha256(response.body).hexdigest(),
                start_wall.isoformat(),
                end_wall.isoformat(),
                start.monotonic_ns,
                end.monotonic_ns,
            )
        )
        for name, value in {
            "evidence_id": digest,
            "body": response.body,
            "body_sha256": hashlib.sha256(response.body).hexdigest(),
            "ticker": market.ticker,
            "event_ticker": market.event_ticker,
            "status": market.status,
            "open_at": opened,
            "close_at": closed,
            "rules_hash": market.rules_hash,
            "metadata_hash": market.metadata_hash,
            "threshold": threshold,
            "raw": raw,
            "request_start": start_wall,
            "response_complete": end_wall,
            "request_start_mono_ns": start.monotonic_ns,
            "response_complete_mono_ns": end.monotonic_ns,
        }.items():
            object.__setattr__(self, name, value)
        _register_issued(self, digest)


def _decode_market(response: _TransportResponse) -> dict[str, object]:
    if response.status != 200:
        raise DecisionError("market response status is not 200")
    _response_hash(response)
    try:
        payload = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DecisionError("market response is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise DecisionError("market response is not an object")
    return payload


@dataclass(frozen=True, slots=True, init=False)
class _OrderbookEvidence:
    evidence_id: str
    body: bytes
    body_sha256: str
    ticker: str
    yes_levels: tuple[tuple[Decimal, Decimal], ...]
    no_levels: tuple[tuple[Decimal, Decimal], ...]
    request_start: datetime
    response_complete: datetime
    request_start_mono_ns: int
    response_complete_mono_ns: int

    def __init__(
        self,
        *,
        response: _TransportResponse,
        ticker: str,
        start: _ClockSample,
        end: _ClockSample,
        _capability: object,
    ) -> None:
        if _capability is not _ISSUER:
            raise DecisionError("orderbook evidence requires reviewed issuer")
        expected_path = BOOK_PATH_PREFIX + ticker
        if response.path != expected_path or response.status != 200:
            raise DecisionError("orderbook endpoint or status is not authoritative")
        _response_hash(response)
        try:
            payload = json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DecisionError("orderbook response is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise DecisionError("orderbook response is not an object")
        entries = payload.get("orderbooks")
        if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
            raise DecisionError("orderbook response is ambiguous")
        entry = entries[0]
        if entry.get("ticker") != ticker or not isinstance(entry.get("orderbook_fp"), dict):
            raise DecisionError("orderbook ticker identity mismatch")
        book = entry["orderbook_fp"]
        yes = _levels(book.get("yes_dollars"), "YES")
        no = _levels(book.get("no_dollars"), "NO")
        start_wall, end_wall = (
            _utc(start.wall_utc, "book request start"),
            _utc(end.wall_utc, "book response complete"),
        )
        if end.monotonic_ns < start.monotonic_ns:
            raise DecisionError("book monotonic clock regressed")
        digest = stable_hash(
            (
                response.path,
                response.status,
                hashlib.sha256(response.body).hexdigest(),
                ticker,
                start_wall.isoformat(),
                end_wall.isoformat(),
                start.monotonic_ns,
                end.monotonic_ns,
            )
        )
        for name, value in {
            "evidence_id": digest,
            "body": response.body,
            "body_sha256": hashlib.sha256(response.body).hexdigest(),
            "ticker": ticker,
            "yes_levels": yes,
            "no_levels": no,
            "request_start": start_wall,
            "response_complete": end_wall,
            "request_start_mono_ns": start.monotonic_ns,
            "response_complete_mono_ns": end.monotonic_ns,
        }.items():
            object.__setattr__(self, name, value)
        _register_issued(self, digest)


def _levels(value: object, side: str) -> tuple[tuple[Decimal, Decimal], ...]:
    if not isinstance(value, list):
        raise DecisionError(f"{side} orderbook ladder is missing")
    result: list[tuple[Decimal, Decimal]] = []
    seen: set[Decimal] = set()
    for row in value:
        if not isinstance(row, list) or len(row) != 2:
            raise DecisionError(f"{side} orderbook level is malformed")
        price, quantity = _decimal(row[0], f"{side} price"), _decimal(row[1], f"{side} quantity")
        if not ZERO < price < ONE or quantity <= ZERO or price in seen:
            raise DecisionError(f"{side} orderbook level is invalid")
        seen.add(price)
        result.append((price, quantity))
    return tuple(sorted(result))


@dataclass(frozen=True, slots=True, init=False)
class _FeeEvidence:
    evidence_id: str
    body: bytes
    body_sha256: str
    policy: FeePolicy
    resolver_id: str

    def __init__(
        self, *, response: _TransportResponse, completed_at: datetime, _capability: object
    ) -> None:
        if _capability is not _ISSUER:
            raise DecisionError("fee evidence requires reviewed issuer")
        payload = _json(response, "/reviewed/d1-g2/fee")
        raw = payload.get("fee")
        if not isinstance(raw, dict):
            raise DecisionError("fee authority object is missing")
        if raw.get("series_ticker") != KXGDP or raw.get("regime") != "MARKETABLE_TAKER":
            raise DecisionError("fee regime is not exact marketable KXGDP authority")
        if raw.get("formula") != "0.07 * price * (1-price) * quantity * multiplier":
            raise DecisionError("fee formula authority is unsupported")
        if raw.get("rounding") != "CEILING_0.0001" or raw.get("price_units") != "USD_PER_CONTRACT":
            raise DecisionError("fee rounding or units authority is unsupported")
        if raw.get("decimal_precision") != "EXACT_DECIMAL":
            raise DecisionError("fee Decimal precision authority is incomplete")
        fee_type = raw.get("fee_type")
        if not isinstance(fee_type, str):
            raise DecisionError("fee type is unsupported")
        try:
            parsed_type = FeeType(fee_type)
        except ValueError as exc:
            raise DecisionError("fee type is unsupported") from exc
        if parsed_type not in {FeeType.QUADRATIC, FeeType.QUADRATIC_WITH_MAKER_FEES}:
            raise DecisionError("fee type is unsupported")
        multiplier = _decimal(raw.get("fee_multiplier"), "fee multiplier")
        effective = _parse_aware(raw.get("effective_at"), "fee effective")
        completed = _utc(completed_at, "fee completion")
        if effective > completed:
            raise DecisionError("fee authority is not effective at decision time")
        policy = FeePolicy(
            policy_id=str(raw.get("schedule_id")),
            fee_type=parsed_type,
            fee_multiplier=multiplier,
            effective_at=effective,
            retired_at=None,
            formula_version="price-times-complement-times-quantity-v1",
            source_reference=str(raw.get("schedule_id")),
            verified=True,
            quadratic_coefficient=Decimal("0.07"),
            maker_quadratic_coefficient=Decimal("0.0175")
            if parsed_type is FeeType.QUADRATIC_WITH_MAKER_FEES
            else None,
        )
        if not raw.get("override_precedence") or raw.get("quantity_convention") != "CONTRACTS":
            raise DecisionError("fee override or quantity authority is incomplete")
        digest = stable_hash(
            (
                response.path,
                response.status,
                hashlib.sha256(response.body).hexdigest(),
                completed.isoformat(),
            )
        )
        for name, value in {
            "evidence_id": digest,
            "body": response.body,
            "body_sha256": hashlib.sha256(response.body).hexdigest(),
            "policy": policy,
            "resolver_id": "d1-g2-fee-resolver-v1",
        }.items():
            object.__setattr__(self, name, value)
        _register_issued(self, digest)


_ISSUER = object()
_ISSUED: dict[int, str] = {}
_ISSUED_FINGERPRINTS: dict[int, str] = {}


def _register_issued(value: object, identity: str) -> None:
    _ISSUED[id(value)] = identity
    _ISSUED_FINGERPRINTS[id(value)] = stable_hash(repr(value))


def _check_issued(value: object, name: str) -> None:
    identity = getattr(value, "evidence_id", None)
    if (
        type(identity) is not str
        or _ISSUED.get(id(value)) != identity
        or _ISSUED_FINGERPRINTS.get(id(value)) != stable_hash(repr(value))
    ):
        raise DecisionError(f"{name} is reconstructed, replaced, or not issuer-issued")


@dataclass(frozen=True, slots=True)
class _Bundle:
    schedule: _ScheduleEvidence
    before: _MarketEvidence
    book: _OrderbookEvidence
    after: _MarketEvidence
    fee: _FeeEvidence
    gdpnow: GDPNowAcquisitionEvidence
    vintage: ParsedGDPNowVintage


@dataclass(frozen=True, slots=True, init=False)
class DecisionReceipt:
    decision_id: str
    policy_version: str
    policy_hash: str
    entry_rule_version: str
    entry_rule_hash: str
    tradability_protocol_version: str
    tradability_protocol_hash: str
    decision_timestamp: datetime
    pipeline_completion_timestamp: datetime
    schedule_id: str
    schedule_gate_passed: bool
    gdpnow_acquisition_id: str
    gdpnow_vintage_id: str
    market_before_id: str
    orderbook_id: str
    market_after_id: str
    fee_id: str
    selected_market_ticker: str
    selected_threshold: Decimal
    signal_side: SignalSide | None
    entry_price: Decimal | None
    quantity: Decimal
    depth_at_entry: Decimal
    entry_fee: Decimal | None
    all_in_debit: Decimal | None
    entry_gate_value: Decimal
    entry_gate_passed: bool
    classification: DecisionClass
    observation_window_elapsed_ms: int
    whole_window_inside_open_close: bool
    whole_window_before_cutoff: bool
    active_before: bool
    active_after: bool
    research_only: bool
    production_influence: Decimal
    payload_hash: str
    bundle: _Bundle | None

    def __init__(
        self, *, values: Mapping[str, object], bundle: _Bundle | None, _capability: object
    ) -> None:
        if _capability is not _ISSUER:
            raise DecisionError("decision receipt requires reviewed issuer")
        fields = dict(values)
        payload = stable_hash(tuple(sorted((k, str(v)) for k, v in fields.items())))
        fields["payload_hash"] = payload
        for name, value in {**fields, "bundle": bundle}.items():
            object.__setattr__(self, name, value)
        _register_issued(self, payload)


def _policy_hash() -> str:
    return stable_hash(
        (
            POLICY_VERSION,
            ENTRY_RULE_VERSION,
            TRADABILITY_PROTOCOL_VERSION,
            str(DECISION_MARGIN),
            str(GATE),
        )
    )


def _validate_bundle(bundle: _Bundle, cutoff: datetime) -> None:
    for value, name in (
        (bundle.schedule, "schedule"),
        (bundle.before, "status-before"),
        (bundle.book, "orderbook"),
        (bundle.after, "status-after"),
        (bundle.fee, "fee"),
    ):
        _check_issued(value, name)
    validate_gdpnow_acquisition_evidence(bundle.gdpnow)
    validate_parsed_gdpnow_vintage(bundle.vintage)
    if bundle.vintage.acquisition_evidence_id != bundle.gdpnow.content_hash:
        raise DecisionError("GDPNow vintage/acquisition identity mismatch")
    if bundle.vintage.target_quarter != bundle.schedule.target_quarter:
        raise DecisionError("GDPNow target quarter does not match schedule")
    if bundle.gdpnow.acquired_at >= cutoff:
        raise DecisionError("GDPNow acquisition was not strictly before cutoff")
    if (
        bundle.before.ticker != bundle.schedule.market_ticker
        or bundle.after.ticker != bundle.schedule.market_ticker
        or bundle.book.ticker != bundle.schedule.market_ticker
    ):
        raise DecisionError("market identity mismatch")
    if (
        bundle.before.event_ticker != bundle.schedule.event_ticker
        or bundle.after.event_ticker != bundle.schedule.event_ticker
    ):
        raise DecisionError("event identity mismatch")


def _classify(
    *, bundle: _Bundle, cutoff: datetime, decision_at: datetime, completion_at: datetime
) -> tuple[
    DecisionClass,
    Decimal | None,
    Decimal | None,
    Decimal | None,
    SignalSide | None,
    int,
    bool,
    bool,
]:
    elapsed_ns = bundle.after.response_complete_mono_ns - bundle.before.request_start_mono_ns
    elapsed_ms = elapsed_ns // 1_000_000
    active = (
        bundle.before.status is MarketStatus.ACTIVE and bundle.after.status is MarketStatus.ACTIVE
    )
    window_start = bundle.before.request_start
    window_end = bundle.after.response_complete
    bounds_match = (
        bundle.before.open_at == bundle.schedule.open_at
        and bundle.after.open_at == bundle.schedule.open_at
        and bundle.before.close_at == bundle.schedule.close_at
        and bundle.after.close_at == bundle.schedule.close_at
    )
    schedule_ok = bundle.schedule.close_at < bundle.schedule.release_at
    cutoff_inside = bundle.schedule.open_at < cutoff < bundle.schedule.close_at
    inside = window_start > bundle.schedule.open_at and window_end < bundle.schedule.close_at
    before_cutoff = window_end <= cutoff and completion_at <= cutoff
    valid_timing = elapsed_ns <= MAX_STATUS_BOOK_STATUS_WINDOW_NS and elapsed_ns >= 0
    if (
        not active
        or not bounds_match
        or not schedule_ok
        or not cutoff_inside
        or not inside
        or not before_cutoff
        or not valid_timing
    ):
        return (
            DecisionClass.EVIDENCE_INCOMPLETE,
            None,
            None,
            None,
            None,
            elapsed_ms,
            inside,
            before_cutoff,
        )
    thresholds = bundle.schedule.thresholds
    distances = [
        (abs(bundle.vintage.gdpnow_value - threshold), threshold) for threshold in thresholds
    ]
    selected = min(distances, key=lambda item: (item[0], item[1]))[1]
    if bundle.before.threshold != selected or bundle.after.threshold != selected:
        return (
            DecisionClass.EVIDENCE_INCOMPLETE,
            selected,
            None,
            None,
            None,
            elapsed_ms,
            inside,
            before_cutoff,
        )
    side = SignalSide.YES if bundle.vintage.gdpnow_value > selected else SignalSide.NO
    opposing = bundle.book.no_levels if side is SignalSide.YES else bundle.book.yes_levels
    if not opposing:
        return (
            DecisionClass.EVIDENCE_INCOMPLETE,
            selected,
            None,
            None,
            side,
            elapsed_ms,
            inside,
            before_cutoff,
        )
    source_price, quantity = max(opposing, key=lambda item: item[0])
    price = ONE - source_price
    structure = bundle.before.raw.get("price_level_structure")
    if structure != "deci_cent" or any(
        _has_sub_cent_precision(value) for value in (source_price, price)
    ):
        return (
            DecisionClass.EVIDENCE_INCOMPLETE,
            selected,
            None,
            None,
            side,
            elapsed_ms,
            inside,
            before_cutoff,
        )
    if not price.is_finite() or price <= ZERO or price >= ONE:
        return (
            DecisionClass.EVIDENCE_INCOMPLETE,
            selected,
            None,
            None,
            side,
            elapsed_ms,
            inside,
            before_cutoff,
        )
    if quantity < ONE:
        return (
            DecisionClass.EXECUTION_NOT_IDENTIFIABLE,
            selected,
            price,
            None,
            side,
            elapsed_ms,
            inside,
            before_cutoff,
        )
    fee = calculate_fee(bundle.fee.policy, price, ONE).total_fee
    debit = price + fee
    classification = (
        DecisionClass.ABSTAIN
        if debit >= GATE
        else (DecisionClass.TRADE_YES if side is SignalSide.YES else DecisionClass.TRADE_NO)
    )
    return classification, selected, price, fee, side, elapsed_ms, inside, before_cutoff


def _evaluate_fixture_decision(
    source: _ResearchDecisionSource,
    gdpnow: GDPNowAcquisitionEvidence,
    vintage: ParsedGDPNowVintage,
    clock: _Clock,
) -> DecisionReceipt:
    decision_sample = clock()
    schedule_start = clock()
    schedule_response = source.schedule()
    schedule_end = clock()
    schedule = _ScheduleEvidence(
        response=schedule_response, completed_at=schedule_end.wall_utc, _capability=_ISSUER
    )
    cutoff = schedule.release_at - DECISION_MARGIN
    if schedule_start.wall_utc > cutoff:
        raise DecisionError("schedule acquisition started after cutoff")
    selected_ticker = schedule.market_ticker
    before_start = clock()
    before_response = source.market(selected_ticker)
    before_end = clock()
    before = _MarketEvidence(
        response=before_response, start=before_start, end=before_end, _capability=_ISSUER
    )
    book_start = clock()
    book_response = source.orderbook(selected_ticker)
    book_end = clock()
    book = _OrderbookEvidence(
        response=book_response,
        ticker=selected_ticker,
        start=book_start,
        end=book_end,
        _capability=_ISSUER,
    )
    after_start = clock()
    after_response = source.market(selected_ticker)
    after_end = clock()
    after = _MarketEvidence(
        response=after_response, start=after_start, end=after_end, _capability=_ISSUER
    )
    fee_response = source.fee()
    fee_end = clock()
    try:
        fee = _FeeEvidence(
            response=fee_response, completed_at=fee_end.wall_utc, _capability=_ISSUER
        )
    except DecisionError:
        return _incomplete_receipt(fee_end)
    bundle = _Bundle(schedule, before, book, after, fee, gdpnow, vintage)
    pipeline = clock()
    try:
        _validate_bundle(bundle, cutoff)
        classification, threshold, price, entry_fee, side, elapsed_ms, inside, before_cutoff = (
            _classify(
                bundle=bundle,
                cutoff=cutoff,
                decision_at=decision_sample.wall_utc,
                completion_at=pipeline.wall_utc,
            )
        )
    except DecisionError:
        classification, threshold, price, entry_fee, side, elapsed_ms, inside, before_cutoff = (
            DecisionClass.EVIDENCE_INCOMPLETE,
            None,
            None,
            None,
            None,
            max(0, (after.response_complete_mono_ns - before.request_start_mono_ns) // 1_000_000),
            False,
            False,
        )
    values: dict[str, object] = {
        "decision_id": stable_hash(
            (
                POLICY_VERSION,
                gdpnow.content_hash,
                schedule.evidence_id,
                selected_ticker,
                pipeline.wall_utc.isoformat(),
            )
        ),
        "policy_version": POLICY_VERSION,
        "policy_hash": _policy_hash(),
        "entry_rule_version": ENTRY_RULE_VERSION,
        "entry_rule_hash": stable_hash(
            (
                ENTRY_RULE_VERSION,
                str(GATE),
                "closest-absolute-decimal",
                "lower-tie",
                "strict-greater",
            )
        ),
        "tradability_protocol_version": TRADABILITY_PROTOCOL_VERSION,
        "tradability_protocol_hash": stable_hash(
            (TRADABILITY_PROTOCOL_VERSION, MAX_STATUS_BOOK_STATUS_WINDOW_MS, "active-book-active")
        ),
        "decision_timestamp": _utc(decision_sample.wall_utc, "decision timestamp"),
        "pipeline_completion_timestamp": _utc(pipeline.wall_utc, "pipeline completion"),
        "schedule_id": schedule.evidence_id,
        "schedule_gate_passed": classification is not DecisionClass.EVIDENCE_INCOMPLETE,
        "gdpnow_acquisition_id": gdpnow.content_hash,
        "gdpnow_vintage_id": vintage.vintage_id,
        "market_before_id": before.evidence_id,
        "orderbook_id": book.evidence_id,
        "market_after_id": after.evidence_id,
        "fee_id": fee.evidence_id,
        "selected_market_ticker": selected_ticker,
        "selected_threshold": threshold if threshold is not None else Decimal("0"),
        "signal_side": side,
        "entry_price": price,
        "quantity": ONE,
        "depth_at_entry": (
            max(
                (
                    q
                    for p, q in (book.no_levels if side is SignalSide.YES else book.yes_levels)
                    if price is not None and p == ONE - price
                ),
                default=ZERO,
            )
            if side is not None
            else ZERO
        ),
        "entry_fee": entry_fee,
        "all_in_debit": None if price is None or entry_fee is None else price + entry_fee,
        "entry_gate_value": GATE,
        "entry_gate_passed": classification in {DecisionClass.TRADE_YES, DecisionClass.TRADE_NO},
        "classification": classification,
        "observation_window_elapsed_ms": elapsed_ms,
        "whole_window_inside_open_close": inside,
        "whole_window_before_cutoff": before_cutoff,
        "active_before": before.status is MarketStatus.ACTIVE,
        "active_after": after.status is MarketStatus.ACTIVE,
        "research_only": True,
        "production_influence": ZERO,
    }
    receipt = DecisionReceipt(values=values, bundle=bundle, _capability=_ISSUER)
    validate_decision_receipt(receipt)
    return receipt


def run_one_research_decision() -> DecisionReceipt:
    """Run one fixed production composition, failing closed while adapters are absent.

    This public boundary intentionally has no source, response, evidence, or clock
    parameter.  A later activation review may enable fixed reviewed adapters.  Until
    then, schedule and fee authority are unavailable, so this function records only
    ``EVIDENCE_INCOMPLETE`` and performs no live acquisition.
    """
    return _incomplete_receipt(_system_clock())


def _incomplete_receipt(sample: _ClockSample) -> DecisionReceipt:
    values: dict[str, object] = {
        "decision_id": stable_hash(
            (POLICY_VERSION, "EVIDENCE_INCOMPLETE", sample.wall_utc.isoformat())
        ),
        "policy_version": POLICY_VERSION,
        "policy_hash": _policy_hash(),
        "entry_rule_version": ENTRY_RULE_VERSION,
        "entry_rule_hash": stable_hash((ENTRY_RULE_VERSION, str(GATE))),
        "tradability_protocol_version": TRADABILITY_PROTOCOL_VERSION,
        "tradability_protocol_hash": stable_hash(
            (TRADABILITY_PROTOCOL_VERSION, MAX_STATUS_BOOK_STATUS_WINDOW_MS)
        ),
        "decision_timestamp": _utc(sample.wall_utc, "decision timestamp"),
        "pipeline_completion_timestamp": _utc(sample.wall_utc, "pipeline completion"),
        "schedule_id": "UNAVAILABLE",
        "schedule_gate_passed": False,
        "gdpnow_acquisition_id": "UNAVAILABLE",
        "gdpnow_vintage_id": "UNAVAILABLE",
        "market_before_id": "UNAVAILABLE",
        "orderbook_id": "UNAVAILABLE",
        "market_after_id": "UNAVAILABLE",
        "fee_id": "UNAVAILABLE",
        "selected_market_ticker": "UNAVAILABLE",
        "selected_threshold": ZERO,
        "signal_side": None,
        "entry_price": None,
        "quantity": ONE,
        "depth_at_entry": ZERO,
        "entry_fee": None,
        "all_in_debit": None,
        "entry_gate_value": GATE,
        "entry_gate_passed": False,
        "classification": DecisionClass.EVIDENCE_INCOMPLETE,
        "observation_window_elapsed_ms": 0,
        "whole_window_inside_open_close": False,
        "whole_window_before_cutoff": False,
        "active_before": False,
        "active_after": False,
        "research_only": True,
        "production_influence": ZERO,
    }
    return DecisionReceipt(values=values, bundle=None, _capability=_ISSUER)


def validate_decision_receipt(receipt: DecisionReceipt) -> None:
    if type(receipt) is not DecisionReceipt or _ISSUED.get(id(receipt)) != receipt.payload_hash:
        raise DecisionError("decision receipt is reconstructed, replaced, or tampered")
    if receipt.research_only is not True or receipt.production_influence != ZERO:
        raise DecisionError("decision receipt is not research-only")
    if receipt.quantity != ONE or receipt.entry_gate_value != GATE:
        raise DecisionError("decision receipt policy constants changed")
    payload_values = {
        field.name: getattr(receipt, field.name)
        for field in fields(DecisionReceipt)
        if field.name not in {"payload_hash", "bundle"}
    }
    expected_payload_hash = stable_hash(
        tuple(sorted((name, str(value)) for name, value in payload_values.items()))
    )
    if receipt.payload_hash != expected_payload_hash:
        raise DecisionError("decision payload hash does not match immutable fields")
    if receipt.bundle is None:
        if receipt.classification is not DecisionClass.EVIDENCE_INCOMPLETE:
            raise DecisionError("non-incomplete receipt is missing evidence bundle")
        return
    try:
        _validate_bundle(receipt.bundle, receipt.bundle.schedule.release_at - DECISION_MARGIN)
    except DecisionError:
        if receipt.classification is not DecisionClass.EVIDENCE_INCOMPLETE:
            raise


def replay_decision(receipt: DecisionReceipt) -> DecisionReceipt:
    """Recompute the decision from its preserved evidence and require exact persisted equality."""
    validate_decision_receipt(receipt)
    if receipt.bundle is None:
        return receipt
    bundle = receipt.bundle
    completion = receipt.pipeline_completion_timestamp
    try:
        _validate_bundle(bundle, bundle.schedule.release_at - DECISION_MARGIN)
        classification, threshold, price, fee, _side, elapsed, inside, before_cutoff = _classify(
            bundle=bundle,
            cutoff=bundle.schedule.release_at - DECISION_MARGIN,
            decision_at=receipt.decision_timestamp,
            completion_at=completion,
        )
    except DecisionError:
        classification, threshold, price, fee, _side, elapsed, inside, before_cutoff = (
            DecisionClass.EVIDENCE_INCOMPLETE,
            Decimal("0"),
            None,
            None,
            None,
            receipt.observation_window_elapsed_ms,
            receipt.whole_window_inside_open_close,
            receipt.whole_window_before_cutoff,
        )
    checks = {
        "classification": classification,
        "selected_threshold": threshold if threshold is not None else Decimal("0"),
        "entry_price": price,
        "entry_fee": fee,
        "observation_window_elapsed_ms": elapsed,
        "whole_window_inside_open_close": inside,
        "whole_window_before_cutoff": before_cutoff,
    }
    expected_decision_id = stable_hash(
        (
            POLICY_VERSION,
            bundle.gdpnow.content_hash,
            bundle.schedule.evidence_id,
            receipt.selected_market_ticker,
            receipt.pipeline_completion_timestamp.isoformat(),
        )
    )
    if receipt.decision_id != expected_decision_id:
        raise DecisionError("decision replay mismatch: decision_id")
    for name, expected in checks.items():
        if getattr(receipt, name) != expected:
            raise DecisionError(f"decision replay mismatch: {name}")
    return receipt


__all__ = ["DecisionClass", "DecisionError", "replay_decision", "run_one_research_decision"]
