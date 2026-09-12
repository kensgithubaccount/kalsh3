"""Issuer-bound, research-only authority for the reviewed GDP schedule."""

from __future__ import annotations

import hashlib
import http.client
import json
import re
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from html.parser import HTMLParser
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

EVENT_TICKER: Final = "KXGDP-26OCT30"
SERIES_TICKER: Final = "KXGDP"
TIMEZONE_NAME: Final = "America/New_York"
METRIC_SEMANTICS: Final = "real GDP quarter-over-quarter growth, seasonally adjusted annual rate"
SETTLEMENT_EDITION: Final = "BEA Advance Estimate"
KALSHI_ORIGIN: Final = "https://external-api.kalshi.com"
KALSHI_HOST: Final = "external-api.kalshi.com"
KALSHI_EVENT_PATH: Final = f"/trade-api/v2/events/{EVENT_TICKER}?with_nested_markets=true"
BEA_ORIGIN: Final = "https://www.bea.gov"
BEA_HOST: Final = "www.bea.gov"
BEA_SCHEDULE_PATH: Final = "/news/schedule/"
HTTP_METHOD: Final = "GET"
SUCCESS_STATUS: Final = 200
MAX_RESPONSE_BYTES: Final = 4_000_000
PARSER_VERSION: Final = "d1-g3-schedule-parser-v3"
TIMEZONE_POLICY_IDENTITY: Final = "iana-america-new-york-strict-local-v3"
TRANSPORT_POLICY_IDENTITY: Final = "d1-g3-fixed-source-contract-v3"


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
    headers: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class _ReviewedEndpoint:
    identity: str
    host: str
    origin: str
    path: str
    method: str
    content_types: tuple[str, ...]


@dataclass(frozen=True, slots=True, init=False)
class SourceEvidence:
    source_locator: str
    source_host: str
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
    headers: tuple[tuple[str, str], ...]

    def __init__(self, **_: object) -> None:
        raise ScheduleAuthorityError("source evidence is issuer-issued only")


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
    normalized_floor: Decimal
    normalized_cap: Decimal | None
    comparator: str
    rule_identity: str


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
    release_at: datetime
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
    bea_release_at: datetime
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


_KALSHI_ENDPOINT = _ReviewedEndpoint(
    "kalshi-kxgdp-event-v1",
    KALSHI_HOST,
    KALSHI_ORIGIN,
    KALSHI_EVENT_PATH,
    HTTP_METHOD,
    ("application/json",),
)
_BEA_ENDPOINT = _ReviewedEndpoint(
    "bea-release-schedule-v1",
    BEA_HOST,
    BEA_ORIGIN,
    BEA_SCHEDULE_PATH,
    HTTP_METHOD,
    ("text/html",),
)


def _fingerprint(*values: object) -> str:
    return hashlib.sha256("\x00".join(map(str, values)).encode()).hexdigest()


def _strict_utc(value: datetime, field: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ScheduleAuthorityError(f"{field} must be timezone-aware")
    return value.astimezone(UTC).replace(tzinfo=UTC)


def _raw_fingerprint(response: _RawResponse) -> tuple[object, ...]:
    return (
        response.locator,
        response.host,
        response.path,
        response.method,
        response.status,
        response.content_type,
        response.body,
        response.acquired_at,
        response.headers,
    )


def _evidence_fingerprint(evidence: SourceEvidence) -> tuple[object, ...]:
    try:
        return tuple(getattr(evidence, name) for name in SourceEvidence.__dataclass_fields__)
    except AttributeError:
        return ("<uninitialized-evidence>",)


def _authority_fingerprint(values: tuple[object, ...]) -> str:
    return _fingerprint(*(repr(value) for value in values))


def _make_evidence_issuer() -> tuple[
    Callable[[_RawResponse, _ReviewedEndpoint | None], SourceEvidence],
    Callable[[_RawResponse, _ReviewedEndpoint], SourceEvidence],
    Callable[[SourceEvidence, _ReviewedEndpoint | None], None],
]:
    raw_registry: dict[int, tuple[_RawResponse, tuple[object, ...]]] = {}
    evidence_registry: dict[int, tuple[SourceEvidence, tuple[object, ...]]] = {}

    def new_evidence(
        response: _RawResponse, endpoint: _ReviewedEndpoint | None = None
    ) -> SourceEvidence:
        record = raw_registry.get(id(response))
        if (
            endpoint is None
            or (endpoint is not _KALSHI_ENDPOINT and endpoint is not _BEA_ENDPOINT)
            or type(response) is not _RawResponse
            or record is None
            or record[0] is not response
            or record[1] != _raw_fingerprint(response)
            or response.host != endpoint.host
            or response.locator != endpoint.origin + endpoint.path
            or response.path != endpoint.path
            or response.method != endpoint.method
        ):
            raise ScheduleAuthorityError("raw response is not issuer-registered")
        if (
            response.status != SUCCESS_STATUS
            or not response.body
            or response.content_type not in endpoint.content_types
        ):
            raise ScheduleAuthorityError("only successful non-empty responses become evidence")
        acquired = _strict_utc(response.acquired_at, "acquired_at")
        digest = hashlib.sha256(response.body).hexdigest()
        evidence = object.__new__(SourceEvidence)
        values: dict[str, object] = {
            "source_locator": response.locator,
            "source_host": response.host,
            "source_path": response.path,
            "method": response.method,
            "http_status": response.status,
            "content_type": response.content_type,
            "content_length": len(response.body),
            "acquired_at": acquired,
            "parser_version": PARSER_VERSION,
            "raw_body": response.body,
            "raw_sha256": digest,
            "headers": response.headers,
        }
        values["source_identity"] = _fingerprint(
            TRANSPORT_POLICY_IDENTITY, endpoint.identity, *(values[name] for name in values)
        )
        for name, value in values.items():
            object.__setattr__(evidence, name, value)
        evidence_registry[id(evidence)] = (evidence, _evidence_fingerprint(evidence))
        return evidence

    def acquire_evidence(response: _RawResponse, endpoint: _ReviewedEndpoint) -> SourceEvidence:
        if endpoint is not _KALSHI_ENDPOINT and endpoint is not _BEA_ENDPOINT:
            raise ScheduleAuthorityError("endpoint is not reviewed")
        raw_registry[id(response)] = (response, _raw_fingerprint(response))
        return new_evidence(response, endpoint)

    def validate_evidence(
        evidence: SourceEvidence, endpoint: _ReviewedEndpoint | None = None
    ) -> None:
        record = evidence_registry.get(id(evidence))
        if (
            type(evidence) is not SourceEvidence
            or record is None
            or record[0] is not evidence
            or record[1] != _evidence_fingerprint(evidence)
        ):
            raise ScheduleAuthorityError("source evidence is unissued or mutated")
        if (
            hashlib.sha256(evidence.raw_body).hexdigest() != evidence.raw_sha256
            or len(evidence.raw_body) != evidence.content_length
        ):
            raise ScheduleAuthorityError("source evidence body integrity failed")
        expected = _fingerprint(
            TRANSPORT_POLICY_IDENTITY,
            endpoint.identity if endpoint is not None else _endpoint_identity(evidence),
            evidence.source_locator,
            evidence.source_host,
            evidence.source_path,
            evidence.method,
            evidence.http_status,
            evidence.content_type,
            evidence.content_length,
            evidence.acquired_at,
            evidence.parser_version,
            evidence.raw_body,
            evidence.raw_sha256,
            evidence.headers,
        )
        if evidence.source_identity != expected or evidence.parser_version != PARSER_VERSION:
            raise ScheduleAuthorityError("source evidence fingerprint failed")
        if endpoint is not None and (
            evidence.source_host != endpoint.host
            or evidence.source_locator != endpoint.origin + endpoint.path
            or evidence.source_path != endpoint.path
            or evidence.method != endpoint.method
            or evidence.http_status != SUCCESS_STATUS
            or evidence.content_type not in endpoint.content_types
        ):
            raise ScheduleAuthorityError("source evidence endpoint binding failed")

    return new_evidence, acquire_evidence, validate_evidence


def _endpoint_identity(evidence: SourceEvidence) -> str:
    for endpoint in (_KALSHI_ENDPOINT, _BEA_ENDPOINT):
        if (
            evidence.source_host == endpoint.host
            and evidence.source_locator == endpoint.origin + endpoint.path
            and evidence.source_path == endpoint.path
            and evidence.method == endpoint.method
        ):
            return endpoint.identity
    raise ScheduleAuthorityError("source evidence endpoint is not reviewed")


_new_evidence, _acquire_evidence, _validate_evidence = _make_evidence_issuer()


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
    match = re.fullmatch(r"(?:Q([1-4])\s+(20\d{2})|(20\d{2})-Q([1-4]))", value, re.IGNORECASE)
    if not match:
        raise ScheduleAuthorityError(f"{field} quarter is not canonicalizable")
    quarter, year = (
        (match.group(1), match.group(2)) if match.group(1) else (match.group(4), match.group(3))
    )
    return Quarter(f"{year}-Q{quarter}", value)


def _quarters(texts: list[object]) -> Quarter:
    found: list[Quarter] = []
    for item in texts:
        if not isinstance(item, str):
            continue
        for match in re.finditer(r"\bQ([1-4])\s+(20\d{2})\b", item, re.IGNORECASE):
            found.append(Quarter(f"{match.group(2)}-Q{match.group(1)}", match.group(0)))
        for match in re.finditer(r"\b(20\d{2})-Q([1-4])\b", item, re.IGNORECASE):
            found.append(Quarter(f"{match.group(1)}-Q{match.group(2)}", match.group(0)))
        for match in re.finditer(
            r"\b([1-4])(?:st|nd|rd|th)\s+Quarter\s+(20\d{2})\b", item, re.IGNORECASE
        ):
            found.append(Quarter(f"{match.group(2)}-Q{match.group(1)}", match.group(0)))
    if not found or len({item.canonical for item in found}) != 1:
        raise ScheduleAuthorityError("quarter evidence is absent or contradictory")
    return found[0]


def _decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ScheduleAuthorityError(f"{field} is malformed")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ScheduleAuthorityError(f"{field} is malformed") from exc
    if not result.is_finite():
        raise ScheduleAuthorityError(f"{field} is malformed")
    return result


def _strike(raw: dict[str, object], ticker: str, primary: str, secondary: str) -> StrikeSemantics:
    try:
        kind = StrikeType(_string(raw.get("strike_type"), "strike_type").casefold())
    except ValueError as exc:
        raise ScheduleAuthorityError("unsupported strike_type") from exc
    floor_text = _string(raw.get("floor_strike"), "floor_strike")
    floor = _decimal(floor_text, "floor_strike")
    cap_value = raw.get("cap_strike")
    cap_text = None if cap_value in (None, "") else _string(cap_value, "cap_strike")
    cap = None if cap_text is None else _decimal(cap_text, "cap_strike")
    if kind is StrikeType.BETWEEN and cap is None:
        raise ScheduleAuthorityError("between strike requires cap_strike")
    if kind is not StrikeType.BETWEEN and cap is not None:
        raise ScheduleAuthorityError("floor/cap contradiction")
    if cap is not None and floor >= cap:
        raise ScheduleAuthorityError("floor/cap contradiction")
    suffix = ticker.rsplit("-T", 1)[-1] if "-T" in ticker else ""
    if suffix and _decimal(suffix, "ticker strike") != floor:
        raise ScheduleAuthorityError("ticker disagrees with explicit strike")
    lower = f"{primary}\n{secondary}".casefold()
    if kind is StrikeType.GREATER:
        comparator, pattern = "greater_than", r"more\s+than\s+([-+]?\d+(?:\.\d+)?)"
        if "more than" not in lower or re.search(r"less\s+than|between", lower):
            raise ScheduleAuthorityError("comparator disagrees with strike_type")
        threshold = re.search(pattern, lower)
        if threshold is None or _decimal(threshold.group(1), "rule threshold") != floor:
            raise ScheduleAuthorityError("rule threshold disagrees with floor_strike")
    elif kind is StrikeType.LESS:
        comparator, pattern = "less_than", r"less\s+than\s+([-+]?\d+(?:\.\d+)?)"
        if "less than" not in lower or re.search(r"more\s+than|between", lower):
            raise ScheduleAuthorityError("comparator disagrees with strike_type")
        threshold = re.search(pattern, lower)
        if threshold is None or _decimal(threshold.group(1), "rule threshold") != floor:
            raise ScheduleAuthorityError("rule threshold disagrees with floor_strike")
    else:
        comparator = "between"
        threshold = re.search(r"between\s+([-+]?\d+(?:\.\d+)?)[^\d]+([-+]?\d+(?:\.\d+)?)", lower)
        if (
            threshold is None
            or cap is None
            or _decimal(threshold.group(1), "rule floor") != floor
            or _decimal(threshold.group(2), "rule cap") != cap
        ):
            raise ScheduleAuthorityError("rule thresholds disagree with floor/cap")
    return StrikeSemantics(
        kind,
        floor_text,
        cap_text,
        floor,
        cap,
        comparator,
        _fingerprint(lower, floor, cap, comparator),
    )


def _rules(market: dict[str, object], quarter: Quarter) -> tuple[str, str]:
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
    if _quarters([primary, secondary]).canonical != quarter.canonical:
        raise ScheduleAuthorityError("market rule quarter disagrees")
    return primary, secondary


class _ScheduleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[tuple[str, ...], tuple[str, ...], int | None]] = []
        self.text_parts: list[str] = []
        self._publication_year: int | None = None
        self._row_year: int | None = None
        self._row: list[str] | None = None
        self._links: list[str] | None = None
        self._cell: list[str] | None = None
        self._href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lower = tag.casefold()
        if lower == "tr":
            self._row, self._links = [], []
            self._row_year = self._publication_year
        elif lower in ("td", "th") and self._row is not None:
            self._cell = []
        elif lower == "a" and self._row is not None:
            self._href = dict(attrs).get("href")

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)
        if self._cell is not None:
            self._cell.append(data)
        else:
            year = re.fullmatch(r"\s*Year\s+(20\d{2})\s*", data)
            if year:
                self._publication_year = int(year.group(1))

    def handle_endtag(self, tag: str) -> None:
        lower = tag.casefold()
        if lower in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(" ".join(" ".join(self._cell).split()))
            self._cell = None
        elif lower == "a":
            if self._href and self._links is not None:
                self._links.append(self._href)
            self._href = None
        elif lower == "tr" and self._row is not None and self._links is not None:
            if self._row:
                self.rows.append((tuple(self._row), tuple(self._links), self._row_year))
            self._row, self._links = None, None


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
    matches: list[tuple[tuple[str, ...], tuple[str, ...], int | None]] = []
    for row, links, publication_year in parser.rows:
        low = " | ".join(row).casefold()
        try:
            row_quarter = _quarters(list(row))
        except ScheduleAuthorityError:
            continue
        if (
            "gdp" in low
            and "advance estimate" in low
            and "second estimate" not in low
            and "third estimate" not in low
            and row_quarter.canonical == expected.canonical
        ):
            matches.append((row, links, publication_year))
    if len(matches) != 1:
        raise ScheduleAuthorityError("BEA schedule target is absent or duplicated")
    row, links, publication_year = matches[0]
    if len(row) < 3 or len(links) != 1:
        raise ScheduleAuthorityError("BEA locator is not exact-row-bound")
    match = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2})", row[0])
    if not match:
        raise ScheduleAuthorityError("BEA release date is malformed")
    try:
        month = datetime.strptime(match.group(1), "%B").month
    except ValueError as exc:
        raise ScheduleAuthorityError("BEA release month is malformed") from exc
    # Prefer the publication-year heading on the source page.  The quarter
    # year in the release description is the target quarter, not necessarily
    # the publication year (Q4 releases commonly publish in the next year).
    if publication_year is None:
        raise ScheduleAuthorityError("BEA publication year is not source-bound")
    release_date = date(publication_year, month, int(match.group(2)))
    if not row[1].strip():
        raise ScheduleAuthorityError("BEA release timing precision is insufficient")
    release = _local(f"{release_date.isoformat()} {row[1]}", "BEA release")
    link = links[0]
    if not (link.startswith("/") or link.startswith(BEA_ORIGIN + "/")):
        raise ScheduleAuthorityError("BEA locator is not first-party")
    locator = link if link.startswith("http") else BEA_ORIGIN + link
    if f"/news/{publication_year}/" not in locator:
        raise ScheduleAuthorityError("BEA locator year disagrees with publication year")
    quarter_words = {"Q1": "first", "Q2": "second", "Q3": "third", "Q4": "fourth"}
    expected_suffix = expected.canonical[5:]
    expected_word = quarter_words[expected_suffix]
    if (
        "advance-estimate" not in locator.casefold()
        or expected.canonical[:4] not in locator.casefold()
        or f"{expected_word}-quarter-{expected.canonical[:4]}" not in locator.casefold()
    ):
        raise ScheduleAuthorityError("BEA locator does not identify matched row")
    return BEAReleaseSchedule(release, TIMEZONE_NAME, locator, SETTLEMENT_EDITION, expected)


def _market(raw: dict[str, object], quarter: Quarter) -> EligibleMarket:
    ticker = _string(raw.get("ticker"), "market ticker")
    if raw.get("event_ticker") != EVENT_TICKER:
        raise ScheduleAuthorityError("wrong-event market")
    status = _string(raw.get("status"), "market status")
    if status not in {"active", "open"}:
        raise ScheduleAuthorityError("inactive/ineligible market")
    primary, secondary = _rules(raw, quarter)
    return EligibleMarket(
        ticker,
        EVENT_TICKER,
        status,
        _strike(raw, ticker, primary, secondary),
        _timestamp(raw.get("open_time"), "market open"),
        _timestamp(raw.get("close_time"), "market close"),
        _optional_timestamp(raw.get("expected_expiration_time"), "expected expiration"),
        _optional_timestamp(raw.get("latest_expiration_time"), "latest expiration"),
        _fingerprint(primary, secondary, quarter.canonical),
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


def _issue_authority_impl(
    event_evidence: SourceEvidence,
    market_evidence: SourceEvidence | None,
    bea_evidence: SourceEvidence,
) -> ScheduleAuthorityResult:
    try:
        _validate_evidence(event_evidence, _KALSHI_ENDPOINT)
        _validate_evidence(bea_evidence, _BEA_ENDPOINT)
        if market_evidence is not None:
            _validate_evidence(market_evidence, _KALSHI_ENDPOINT)
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
        declared_body_count = event.get("market_count", payload.get("market_count"))
        if declared_body_count is not None and declared_body_count != len(nested):
            raise ScheduleAuthorityError("market collection is internally inconsistent")
        headers = dict(event_evidence.headers)
        expected_count, terminal = (
            headers.get("x-kalshi-event-market-count"),
            headers.get("x-kalshi-event-pagination-terminal"),
        )
        if expected_count is None or terminal != "true":
            raise ScheduleAuthorityError("market result completeness is unproven")
        if int(expected_count) != len(nested):
            raise ScheduleAuthorityError("source market count disagrees")
        pagination = event.get("pagination", payload.get("pagination"))
        if not isinstance(pagination, dict) or pagination.get("cursor") not in (None, ""):
            raise ScheduleAuthorityError("market result set has omitted pages")
        quarter = _quarters(
            [event.get("strike_period"), event.get("title"), event.get("sub_title")]
        )
        markets = tuple(
            sorted(
                (_market(item, quarter) for item in nested if isinstance(item, dict)),
                key=lambda item: (item.strike.normalized_floor, item.ticker),
            )
        )
        if len(markets) != len(nested) or len({item.ticker for item in markets}) != len(markets):
            raise ScheduleAuthorityError("duplicate or malformed market set")
        identities = {
            (
                item.strike.strike_type,
                item.strike.normalized_floor,
                item.strike.normalized_cap,
                item.strike.comparator,
            )
            for item in markets
        }
        if len(identities) != len(markets):
            raise ScheduleAuthorityError("duplicate equivalent strike")
        schedule = _bea(bea_evidence, quarter)
        for item in markets:
            if not item.open_at < item.close_at:
                raise ScheduleAuthorityError("market lifecycle ordering is invalid")
            if (
                item.expected_expiration_at is not None
                and item.expected_expiration_at < item.close_at
            ):
                raise ScheduleAuthorityError("expected expiration precedes close")
            if (
                item.latest_expiration_at is not None
                and item.expected_expiration_at is not None
                and item.latest_expiration_at < item.expected_expiration_at
            ):
                raise ScheduleAuthorityError("latest expiration precedes expected expiration")
            if item.close_at >= schedule.release_at:
                raise ScheduleAuthorityError("KALSHI_CLOSE_NOT_STRICTLY_BEFORE_BEA_RELEASE")
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
        authority = object.__new__(ScheduleAuthority)
        for name, value in zip(ScheduleAuthority.__dataclass_fields__, values, strict=True):
            object.__setattr__(authority, name, value)
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


def _acquire_fixed(endpoint: _ReviewedEndpoint) -> SourceEvidence:
    if endpoint is not _KALSHI_ENDPOINT and endpoint is not _BEA_ENDPOINT:
        raise ScheduleAuthorityError("endpoint is not reviewed")
    connection = http.client.HTTPSConnection(
        endpoint.host, timeout=10.0, context=ssl.create_default_context()
    )
    try:
        connection.request(
            endpoint.method, endpoint.path, headers={"Accept": ",".join(endpoint.content_types)}
        )
        response = connection.getresponse()
        body = response.read(MAX_RESPONSE_BYTES + 1)
        content = response.getheader("Content-Type", "").split(";", 1)[0].strip().casefold()
        if (
            response.status != SUCCESS_STATUS
            or not body
            or len(body) > MAX_RESPONSE_BYTES
            or content not in endpoint.content_types
        ):
            raise ScheduleAuthorityError("source response is incomplete")
        headers = tuple(
            (name, value)
            for name, value in (
                (
                    "x-kalshi-event-market-count",
                    response.getheader("X-Kalshi-Event-Market-Count", ""),
                ),
                (
                    "x-kalshi-event-pagination-terminal",
                    response.getheader("X-Kalshi-Event-Pagination-Terminal", ""),
                ),
            )
            if value
        )
        raw = _RawResponse(
            endpoint.origin + endpoint.path,
            endpoint.host,
            endpoint.path,
            endpoint.method,
            response.status,
            content,
            body,
            _utc_now(),
            headers,
        )
        return _acquire_evidence(raw, endpoint)
    finally:
        connection.close()


def _make_authority_issuer() -> tuple[
    Callable[[SourceEvidence, SourceEvidence | None, SourceEvidence], ScheduleAuthorityResult],
    Callable[[ScheduleAuthority], None],
]:
    issued: dict[
        int, tuple[ScheduleAuthority, tuple[object, ...], str, SourceEvidence, SourceEvidence]
    ] = {}

    def issue(
        event: SourceEvidence, market: SourceEvidence | None, bea: SourceEvidence
    ) -> ScheduleAuthorityResult:
        result = _issue_authority_impl(event, market, bea)
        if result.authority is not None:
            values = tuple(
                getattr(result.authority, name) for name in ScheduleAuthority.__dataclass_fields__
            )
            issued[id(result.authority)] = (
                result.authority,
                values,
                _authority_fingerprint(values),
                event,
                bea,
            )
        return result

    def validate(authority: ScheduleAuthority) -> None:
        record = issued.get(id(authority))
        if type(authority) is not ScheduleAuthority or record is None or record[0] is not authority:
            raise ScheduleAuthorityError("schedule authority is unissued")
        values = tuple(getattr(authority, name) for name in ScheduleAuthority.__dataclass_fields__)
        if record[1] != values or record[2] != _authority_fingerprint(values):
            raise ScheduleAuthorityError("schedule authority was mutated")
        _validate_evidence(record[3], _KALSHI_ENDPOINT)
        _validate_evidence(record[4], _BEA_ENDPOINT)
        if (
            authority.event_ticker != EVENT_TICKER
            or authority.series_ticker != SERIES_TICKER
            or authority.target_quarter != authority.quarter.canonical
        ):
            raise ScheduleAuthorityError("schedule authority identity mismatch")
        if (
            not authority.bea_release_locator.startswith(BEA_ORIGIN + "/")
            or authority.bea_release_locator != authority.bea_schedule.bea_release_locator
        ):
            raise ScheduleAuthorityError("release locator binding failed")
        if not authority.eligible_markets or len(
            {item.ticker for item in authority.eligible_markets}
        ) != len(authority.eligible_markets):
            raise ScheduleAuthorityError("schedule authority market set is invalid")
        for item in authority.eligible_markets:
            if not item.open_at < item.close_at or (
                item.expected_expiration_at is not None
                and item.expected_expiration_at < item.close_at
            ):
                raise ScheduleAuthorityError("schedule authority lifecycle is invalid")

    return issue, validate


_issue_authority, validate_schedule_authority = _make_authority_issuer()


def acquire_schedule_authority() -> ScheduleAuthorityResult:
    """Acquire research-only event, market-set, and BEA schedule facts."""
    event_evidence: SourceEvidence | None = None
    bea_evidence: SourceEvidence | None = None

    try:
        event_evidence = _acquire_fixed(_KALSHI_ENDPOINT)
        bea_evidence = _acquire_fixed(_BEA_ENDPOINT)
        return _issue_authority(event_evidence, None, bea_evidence)
    except (
        ScheduleAuthorityError,
        OSError,
        TimeoutError,
        ValueError,
        http.client.HTTPException,
    ) as exc:
        return _incomplete(str(exc), event_evidence, None, bea_evidence)


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
