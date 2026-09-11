"""Issuer-bound, research-only authority for one reviewed GDP event."""

from __future__ import annotations

import hashlib
import http.client
import json
import re
import ssl
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from html.parser import HTMLParser
from typing import Final, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

EVENT_TICKER: Final = "KXGDP-26OCT30"
SERIES_TICKER: Final = "KXGDP"
TIMEZONE_NAME: Final = "America/New_York"
METRIC_SEMANTICS: Final = "real GDP quarter-over-quarter growth, seasonally adjusted annual rate"
SETTLEMENT_EDITION: Final = "BEA Advance Estimate"
KALSHI_ORIGIN: Final = "https://external-api.kalshi.com"
KALSHI_HOST: Final = "external-api.kalshi.com"
KALSHI_EVENT_PATH: Final = f"/trade-api/v2/events/{EVENT_TICKER}?with_nested_markets=true"
KALSHI_MARKET_PATH: Final = "/trade-api/v2/markets/"
BEA_ORIGIN: Final = "https://www.bea.gov"
BEA_HOST: Final = "www.bea.gov"
BEA_SCHEDULE_PATH: Final = "/news/schedule/"
HTTP_METHOD: Final = "GET"
SUCCESS_STATUS: Final = 200
MAX_RESPONSE_BYTES: Final = 4_000_000
PARSER_VERSION: Final = "d1-g3-schedule-parser-v2"
TIMEZONE_POLICY_IDENTITY: Final = "iana-america-new-york-strict-local-v2"
TRANSPORT_POLICY_IDENTITY: Final = "d1-g3-public-event-and-bea-get-v2"

_EVIDENCE_REGISTRY: dict[int, tuple[object, ...]] = {}
_AUTHORITY_REGISTRY: dict[int, tuple[object, ...]] = {}


class AuthorityStatus(StrEnum):
    COMPLETE_AUTHORITY = "COMPLETE AUTHORITY"
    EVIDENCE_INCOMPLETE = "EVIDENCE_INCOMPLETE"


class ScheduleAuthorityError(ValueError):
    """Acquisition, provenance, or semantic validation failed closed."""


@dataclass(frozen=True, slots=True)
class _RawResponse:
    locator: str
    host: str
    path: str
    method: str
    status: int
    content_type: str
    body: bytes
    acquired_at: datetime


@dataclass(frozen=True, slots=True, init=False)
class SourceEvidence:
    source_locator: str
    source_path: str
    method: str
    http_status: int
    content_type: str
    content_length: int
    acquired_at: datetime
    parser_version: str
    raw_body: bytes
    raw_sha256: str
    source_identity: str

    def __init__(self, **_: object) -> None:
        raise ScheduleAuthorityError("source evidence is issuer-issued only")


def _new_evidence(response: _RawResponse) -> SourceEvidence:
    if response.status != SUCCESS_STATUS or not response.body:
        raise ScheduleAuthorityError("only successful non-empty responses become evidence")
    evidence = object.__new__(SourceEvidence)
    acquired = _strict_utc(response.acquired_at, "acquired_at")
    digest = hashlib.sha256(response.body).hexdigest()
    identity = _fingerprint(
        response.locator,
        response.path,
        response.method,
        response.status,
        response.content_type,
        len(response.body),
        acquired.isoformat(),
        digest,
        PARSER_VERSION,
    )
    for name, value in {
        "source_locator": response.locator,
        "source_path": response.path,
        "method": response.method,
        "http_status": response.status,
        "content_type": response.content_type,
        "content_length": len(response.body),
        "acquired_at": acquired,
        "parser_version": PARSER_VERSION,
        "raw_body": response.body,
        "raw_sha256": digest,
        "source_identity": identity,
    }.items():
        object.__setattr__(evidence, name, value)
    _EVIDENCE_REGISTRY[id(evidence)] = _evidence_fingerprint(evidence)
    return evidence


@dataclass(frozen=True, slots=True)
class Quarter:
    canonical: str
    raw: str


class StrikeType(StrEnum):
    GREATER = "greater"
    LESS = "less"
    BETWEEN = "between"


@dataclass(frozen=True, slots=True)
class StrikeSemantics:
    strike_type: StrikeType
    floor_strike: str
    cap_strike: str | None
    comparator: str


@dataclass(frozen=True, slots=True)
class EligibleMarket:
    ticker: str
    event_ticker: str
    status: str
    strike: StrikeSemantics
    open_at: datetime
    close_at: datetime
    expected_expiration_at: datetime | None
    latest_expiration_at: datetime | None
    rule_identity: str
    rules_primary: str
    rules_secondary: str


@dataclass(frozen=True, slots=True)
class BEAReleaseSchedule:
    release_at: datetime | date
    timezone: str
    bea_release_locator: str
    edition: str
    quarter: Quarter


@dataclass(frozen=True, slots=True, init=False)
class ScheduleAuthority:
    event_ticker: str
    series_ticker: str
    quarter: Quarter
    target_quarter: str
    metric_semantics: str
    settlement_edition: str
    bea_schedule: BEAReleaseSchedule
    bea_release_at: datetime | date
    bea_release_locator: str
    eligible_markets: tuple[EligibleMarket, ...]
    evidence_ids: tuple[str, ...]
    evidence_sha256: tuple[str, ...]
    timezone_policy_identity: str
    parser_version: str
    contradiction_result: str

    def __init__(self, **_: object) -> None:
        raise ScheduleAuthorityError("positive authority is issuer-issued only")


@dataclass(frozen=True, slots=True)
class ScheduleAuthorityResult:
    status: AuthorityStatus
    authority: ScheduleAuthority | None
    contradiction: str | None
    event_evidence: SourceEvidence | None
    market_evidence: SourceEvidence | None
    bea_evidence: SourceEvidence | None


def _fingerprint(*values: object) -> str:
    return hashlib.sha256("\x00".join(map(str, values)).encode()).hexdigest()


def _strict_utc(value: datetime, field: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ScheduleAuthorityError(f"{field} must be timezone-aware")
    return value.astimezone(UTC).replace(tzinfo=UTC)


def _evidence_fingerprint(evidence: SourceEvidence) -> tuple[object, ...]:
    return (
        evidence.source_locator,
        evidence.source_path,
        evidence.method,
        evidence.http_status,
        evidence.content_type,
        evidence.content_length,
        evidence.acquired_at,
        evidence.parser_version,
        evidence.raw_body,
        evidence.raw_sha256,
        evidence.source_identity,
    )


def _validate_evidence(evidence: SourceEvidence) -> None:
    if type(evidence) is not SourceEvidence or _EVIDENCE_REGISTRY.get(
        id(evidence)
    ) != _evidence_fingerprint(evidence):
        raise ScheduleAuthorityError("source evidence is unissued or mutated")
    if (
        hashlib.sha256(evidence.raw_body).hexdigest() != evidence.raw_sha256
        or len(evidence.raw_body) != evidence.content_length
    ):
        raise ScheduleAuthorityError("source evidence body integrity failed")
    expected = _fingerprint(
        evidence.source_locator,
        evidence.source_path,
        evidence.method,
        evidence.http_status,
        evidence.content_type,
        evidence.content_length,
        evidence.acquired_at.isoformat(),
        evidence.raw_sha256,
        evidence.parser_version,
    )
    if evidence.source_identity != expected or evidence.parser_version != PARSER_VERSION:
        raise ScheduleAuthorityError("source evidence fingerprint failed")


def _json(evidence: SourceEvidence) -> dict[str, object]:
    try:
        value = json.loads(evidence.raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ScheduleAuthorityError("Kalshi response is malformed JSON") from exc
    if not isinstance(value, dict):
        raise ScheduleAuthorityError("Kalshi response must be an object")
    return value


def _string(value: object, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise ScheduleAuthorityError(f"{field} is missing or malformed")
    return value.strip()


def _timestamp(value: object, field: str) -> datetime:
    if type(value) is not str or value.startswith("0001-"):
        raise ScheduleAuthorityError(f"{field} timestamp is missing or sentinel")
    try:
        return _strict_utc(datetime.fromisoformat(value.replace("Z", "+00:00")), field)
    except ValueError as exc:
        raise ScheduleAuthorityError(f"{field} timestamp is malformed") from exc


def _optional_timestamp(value: object, field: str) -> datetime | None:
    return None if value in (None, "") else _timestamp(value, field)


def _quarter(raw: object, field: str) -> Quarter:
    value = _string(raw, field)
    match = re.fullmatch(r"Q([1-4])\s+(20\d{2})", value, re.IGNORECASE)
    if not match:
        raise ScheduleAuthorityError(f"{field} quarter is not canonicalizable")
    return Quarter(f"{match.group(2)}-Q{match.group(1)}", value)


def _quarters(texts: list[object]) -> Quarter:
    found: list[Quarter] = []
    for item in texts:
        if isinstance(item, str):
            for match in re.finditer(r"\bQ([1-4])\s+(20\d{2})\b", item, re.IGNORECASE):
                found.append(Quarter(f"{match.group(2)}-Q{match.group(1)}", match.group(0)))
            for match in re.finditer(
                r"\b([1-4])(?:st|nd|rd|th)\s+Quarter\s+(20\d{2})\b", item, re.IGNORECASE
            ):
                found.append(Quarter(f"{match.group(2)}-Q{match.group(1)}", match.group(0)))
    if not found or len({item.canonical for item in found}) != 1:
        raise ScheduleAuthorityError("quarter evidence is absent or contradictory")
    return found[0]


def _decimal_text(value: object, field: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ScheduleAuthorityError(f"{field} is malformed")
    text = str(value)
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text):
        raise ScheduleAuthorityError(f"{field} is malformed")
    return text


def _strike(raw: dict[str, object], ticker: str) -> StrikeSemantics:
    try:
        kind = StrikeType(_string(raw.get("strike_type"), "strike_type").casefold())
    except ValueError as exc:
        raise ScheduleAuthorityError("unsupported strike_type") from exc
    floor = _decimal_text(raw.get("floor_strike"), "floor_strike")
    cap_value = raw.get("cap_strike")
    cap = None if cap_value in (None, "") else _decimal_text(cap_value, "cap_strike")
    if kind is StrikeType.BETWEEN and cap is None:
        raise ScheduleAuthorityError("between strike requires cap_strike")
    if kind is not StrikeType.BETWEEN and cap is not None:
        raise ScheduleAuthorityError("floor/cap contradiction")
    if cap is not None and float(floor) >= float(cap):
        raise ScheduleAuthorityError("floor/cap contradiction")
    suffix = ticker.rsplit("-T", 1)[-1] if "-T" in ticker else ""
    if suffix and suffix != floor:
        raise ScheduleAuthorityError("ticker disagrees with explicit strike")
    comparator = {
        StrikeType.GREATER: "greater_than",
        StrikeType.LESS: "less_than",
        StrikeType.BETWEEN: "between",
    }[kind]
    return StrikeSemantics(kind, floor, cap, comparator)


def _rules(market: dict[str, object], quarter: Quarter) -> tuple[str, str, str]:
    primary = _string(market.get("rules_primary"), "rules_primary")
    secondary = _string(market.get("rules_secondary"), "rules_secondary")
    low = f"{primary}\n{secondary}".casefold()
    if (
        any(
            item not in low
            for item in ("real gdp", "seasonally adjusted", "annualized", "advance estimate")
        )
        or "nominal gdp" in low
    ):
        raise ScheduleAuthorityError("GDP rule semantics are unsupported")
    editions = re.findall(r"\b(?:advance|second|third) estimate\b", low)
    if editions != ["advance estimate"] or "revised" in low or "later estimate" in low:
        raise ScheduleAuthorityError("settlement edition is not exclusively Advance Estimate")
    if not any(item in low for item in ("more than", "less than", "between")):
        raise ScheduleAuthorityError("comparator semantics are ambiguous")
    if _quarters([primary, secondary]).canonical != quarter.canonical:
        raise ScheduleAuthorityError("market rule quarter disagrees")
    return primary, secondary, _fingerprint(primary, secondary, quarter.canonical)


class _ScheduleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[str, ...]] = []
        self.links: list[str] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() == "tr":
            self._row = []
        elif tag.casefold() in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag.casefold() == "a":
            self._href = dict(attrs).get("href")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(" ".join(" ".join(self._cell).split()))
            self._cell = None
        elif lowered == "a":
            if self._href:
                self.links.append(self._href)
            self._href = None
        elif lowered == "tr" and self._row is not None:
            if self._row:
                self.rows.append(tuple(self._row))
            self._row = None


def _local(value: str, field: str) -> datetime:
    try:
        zone = ZoneInfo(TIMEZONE_NAME)
        local = datetime.strptime(value, "%Y-%m-%d %I:%M %p")
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ScheduleAuthorityError(f"{field} local time malformed") from exc
    candidates = []
    for fold in (0, 1):
        candidate = local.replace(tzinfo=zone, fold=fold)
        if candidate.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == local:
            candidates.append(candidate.astimezone(UTC))
    if len(set(candidates)) != 1:
        raise ScheduleAuthorityError(f"{field} is ambiguous or nonexistent")
    return candidates[0].replace(tzinfo=UTC)


def _bea(evidence: SourceEvidence, expected: Quarter) -> BEAReleaseSchedule:
    try:
        html = evidence.raw_body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ScheduleAuthorityError("BEA schedule is not UTF-8") from exc
    parser = _ScheduleParser()
    parser.feed(html)
    parser.close()
    matches = []
    for row in parser.rows:
        low = " | ".join(row).casefold()
        if (
            "gdp" in low
            and "advance estimate" in low
            and "second estimate" not in low
            and "third estimate" not in low
            and _quarters(list(row)).canonical == expected.canonical
        ):
            matches.append(row)
    if len(matches) != 1:
        raise ScheduleAuthorityError("BEA schedule target is absent or duplicated")
    row = matches[0]
    if len(row) < 3:
        raise ScheduleAuthorityError("BEA schedule row layout drifted")
    match = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2})", row[0])
    if not match:
        raise ScheduleAuthorityError("BEA release date is malformed")
    release_date = date(2026, datetime.strptime(match.group(1), "%B").month, int(match.group(2)))
    release: datetime | date = (
        release_date
        if not row[1].strip()
        else _local(f"{release_date.isoformat()} {row[1]}", "BEA release")
    )
    links = [link for link in parser.links if link.startswith("/") or link.startswith(BEA_ORIGIN)]
    if len(links) != 1:
        raise ScheduleAuthorityError("bea_release_locator is absent or ambiguous")
    locator = links[0] if links[0].startswith("http") else BEA_ORIGIN + links[0]
    return BEAReleaseSchedule(release, TIMEZONE_NAME, locator, SETTLEMENT_EDITION, expected)


def _market(raw: dict[str, object], quarter: Quarter) -> EligibleMarket:
    ticker = _string(raw.get("ticker"), "market ticker")
    if raw.get("event_ticker") != EVENT_TICKER:
        raise ScheduleAuthorityError("wrong-event market")
    status = _string(raw.get("status"), "market status")
    if status not in {"active", "open"}:
        raise ScheduleAuthorityError("inactive/ineligible market")
    primary, secondary, identity = _rules(raw, quarter)
    return EligibleMarket(
        ticker,
        EVENT_TICKER,
        status,
        _strike(raw, ticker),
        _timestamp(raw.get("open_time"), "market open"),
        _timestamp(raw.get("close_time"), "market close"),
        _optional_timestamp(raw.get("expected_expiration_time"), "expected expiration"),
        _optional_timestamp(raw.get("latest_expiration_time"), "latest expiration"),
        identity,
        primary,
        secondary,
    )


def _incomplete(
    reason: str,
    event: SourceEvidence | None = None,
    market: SourceEvidence | None = None,
    bea: SourceEvidence | None = None,
) -> ScheduleAuthorityResult:
    return ScheduleAuthorityResult(
        AuthorityStatus.EVIDENCE_INCOMPLETE, None, reason, event, market, bea
    )


def _issue_authority(
    event_evidence: SourceEvidence,
    market_evidence: SourceEvidence | None,
    bea_evidence: SourceEvidence,
) -> ScheduleAuthorityResult:
    try:
        _validate_evidence(event_evidence)
        _validate_evidence(bea_evidence)
        if market_evidence is not None:
            _validate_evidence(market_evidence)
        payload = _json(event_evidence)
        event = payload.get("event")
        if (
            not isinstance(event, dict)
            or event.get("event_ticker") != EVENT_TICKER
            or event.get("series_ticker") != SERIES_TICKER
        ):
            raise ScheduleAuthorityError("event identity mismatch")
        nested = event.get("markets")
        if not isinstance(nested, list) or not nested:
            raise ScheduleAuthorityError("eligible market set is absent")
        container = event
        pagination = payload.get("pagination", container.get("pagination"))
        if not isinstance(pagination, dict) or "cursor" not in pagination:
            raise ScheduleAuthorityError("market result completeness is unproven")
        if isinstance(pagination, dict) and pagination.get("cursor") not in (None, ""):
            raise ScheduleAuthorityError("market result set has omitted pages")
        market_count = payload.get("market_count", container.get("market_count"))
        if market_count != len(nested):
            raise ScheduleAuthorityError("market count does not prove completeness")
        quarter = _quarters(
            [event.get("strike_period"), event.get("title"), event.get("sub_title")]
        )
        markets = tuple(
            sorted(
                (_market(item, quarter) for item in nested if isinstance(item, dict)),
                key=lambda m: (m.strike.floor_strike, m.ticker),
            )
        )
        if len(markets) != len(nested) or len({m.ticker for m in markets}) != len(markets):
            raise ScheduleAuthorityError("duplicate or malformed market set")
        equivalents = {
            (m.strike.strike_type, m.strike.floor_strike, m.strike.cap_strike, m.strike.comparator)
            for m in markets
        }
        if len(equivalents) != len(markets):
            raise ScheduleAuthorityError("duplicate equivalent strike")
        schedule = _bea(bea_evidence, quarter)
        if isinstance(schedule.release_at, datetime) and any(
            item.close_at >= schedule.release_at for item in markets
        ):
            raise ScheduleAuthorityError("KALSHI_CLOSE_NOT_STRICTLY_BEFORE_BEA_RELEASE")
        authority = object.__new__(ScheduleAuthority)
        values = (
            EVENT_TICKER,
            SERIES_TICKER,
            quarter,
            quarter.canonical,
            METRIC_SEMANTICS,
            SETTLEMENT_EDITION,
            schedule,
            schedule.release_at,
            schedule.bea_release_locator,
            markets,
            (event_evidence.source_identity, bea_evidence.source_identity),
            (event_evidence.raw_sha256, bea_evidence.raw_sha256),
            TIMEZONE_POLICY_IDENTITY,
            PARSER_VERSION,
            "NONE",
        )
        for name, value in zip(ScheduleAuthority.__dataclass_fields__, values, strict=True):
            object.__setattr__(authority, name, value)
        _AUTHORITY_REGISTRY[id(authority)] = (*values, event_evidence, bea_evidence)
        return ScheduleAuthorityResult(
            AuthorityStatus.COMPLETE_AUTHORITY,
            authority,
            None,
            event_evidence,
            market_evidence,
            bea_evidence,
        )
    except (ScheduleAuthorityError, TypeError, ValueError) as exc:
        return _incomplete(str(exc), event_evidence, market_evidence, bea_evidence)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _fixed_get(host: str, origin: str, path: str, expected: tuple[str, ...]) -> _RawResponse:
    if (host, origin) not in ((KALSHI_HOST, KALSHI_ORIGIN), (BEA_HOST, BEA_ORIGIN)):
        raise ScheduleAuthorityError("source outside allowlist")
    connection = http.client.HTTPSConnection(
        host, timeout=10.0, context=ssl.create_default_context()
    )
    try:
        connection.request(HTTP_METHOD, path, headers={"Accept": ",".join(expected)})
        response = connection.getresponse()
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES or response.status != SUCCESS_STATUS:
            raise ScheduleAuthorityError("source response is incomplete")
        content = response.getheader("Content-Type", "").split(";", 1)[0].strip().casefold()
        if content not in expected:
            raise ScheduleAuthorityError("source content type is not authoritative")
        return _RawResponse(
            origin + path, host, path, HTTP_METHOD, response.status, content, body, _utc_now()
        )
    except ScheduleAuthorityError:
        raise
    except (OSError, TimeoutError, ValueError, http.client.HTTPException) as exc:
        raise ScheduleAuthorityError("fixed public source acquisition failed") from exc
    finally:
        connection.close()


def acquire_schedule_authority() -> ScheduleAuthorityResult:
    """Acquire research-only event, market-set, and BEA schedule facts."""
    event = bea = None
    try:
        event = _new_evidence(
            _fixed_get(KALSHI_HOST, KALSHI_ORIGIN, KALSHI_EVENT_PATH, ("application/json",))
        )
        bea = _new_evidence(_fixed_get(BEA_HOST, BEA_ORIGIN, BEA_SCHEDULE_PATH, ("text/html",)))
        return _issue_authority(event, None, bea)
    except ScheduleAuthorityError as exc:
        return _incomplete(str(exc), event, None, bea)


def validate_schedule_authority(authority: ScheduleAuthority) -> None:
    """Validate issuer registration and every bound field before authoritative use."""
    if type(authority) is not ScheduleAuthority or _AUTHORITY_REGISTRY.get(id(authority)) is None:
        raise ScheduleAuthorityError("schedule authority is unissued")
    try:
        values = tuple(getattr(authority, name) for name in ScheduleAuthority.__dataclass_fields__)
    except AttributeError as exc:
        raise ScheduleAuthorityError("schedule authority is uninitialized") from exc
    record = _AUTHORITY_REGISTRY[id(authority)]
    if record[: len(values)] != values:
        raise ScheduleAuthorityError("schedule authority was mutated")
    _validate_evidence(cast(SourceEvidence, record[-2]))
    _validate_evidence(cast(SourceEvidence, record[-1]))
    if authority.event_ticker != EVENT_TICKER or authority.series_ticker != SERIES_TICKER:
        raise ScheduleAuthorityError("schedule authority identity mismatch")
    if not authority.bea_release_locator.startswith(BEA_ORIGIN + "/"):
        raise ScheduleAuthorityError("release locator is not first-party")
    if not authority.eligible_markets or len({m.ticker for m in authority.eligible_markets}) != len(
        authority.eligible_markets
    ):
        raise ScheduleAuthorityError("schedule authority market set is invalid")


__all__ = [
    "AuthorityStatus",
    "BEAReleaseSchedule",
    "EligibleMarket",
    "Quarter",
    "ScheduleAuthority",
    "ScheduleAuthorityError",
    "ScheduleAuthorityResult",
    "SourceEvidence",
    "StrikeSemantics",
    "StrikeType",
    "acquire_schedule_authority",
    "validate_schedule_authority",
]
