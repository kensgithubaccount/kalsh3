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
from typing import Any, Final, cast
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


def _fingerprint(
    *values: object,
    _sha256: Callable[..., Any] = hashlib.sha256,
    _cast: Callable[..., str] = cast,
) -> str:
    return _cast(str, _sha256("\x00".join(map(str, values)).encode()).hexdigest())


def _strict_utc(
    value: datetime,
    field: str,
    _datetime: type[datetime] = datetime,
    _utc: Any = UTC,
    _error_cls: type[ScheduleAuthorityError] = ScheduleAuthorityError,
) -> datetime:
    if type(value) is not _datetime or value.tzinfo is None or value.utcoffset() is None:
        raise _error_cls(f"{field} must be timezone-aware")
    return value.astimezone(_utc).replace(tzinfo=_utc)


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


def _evidence_fingerprint(
    evidence: SourceEvidence, _source_evidence_cls: type[SourceEvidence] = SourceEvidence
) -> tuple[object, ...]:
    try:
        return tuple(getattr(evidence, name) for name in _source_evidence_cls.__dataclass_fields__)
    except AttributeError:
        return ("<uninitialized-evidence>",)


def _authority_fingerprint(
    values: tuple[object, ...], _fingerprint_fn: Callable[..., str] = _fingerprint
) -> str:
    return _fingerprint_fn(*(repr(value) for value in values))


def _utc_now(_datetime: type[datetime] = datetime, _utc: Any = UTC) -> datetime:
    """The real clock used by the canonical evidence issuer."""
    return _datetime.now(_utc)


def _make_evidence_issuer(
    *,
    source_evidence_cls: type[SourceEvidence] = SourceEvidence,
    fingerprint: Callable[..., str] = _fingerprint,
    strict_utc: Callable[[datetime, str], datetime] = _strict_utc,
    raw_fingerprint: Callable[[_RawResponse], tuple[object, ...]] = _raw_fingerprint,
    evidence_fingerprint: Callable[[SourceEvidence], tuple[object, ...]] = _evidence_fingerprint,
    success_status: int = SUCCESS_STATUS,
    max_response_bytes: int = MAX_RESPONSE_BYTES,
    parser_version: str = PARSER_VERSION,
    transport_policy_identity: str = TRANSPORT_POLICY_IDENTITY,
    raw_response_cls: type[_RawResponse] = _RawResponse,
    sha256: Callable[..., Any] = hashlib.sha256,
    https_connection: Callable[..., Any] = http.client.HTTPSConnection,
    create_default_context: Callable[[], Any] = ssl.create_default_context,
    utc_now: Callable[[], datetime] = _utc_now,
    error_cls: type[ScheduleAuthorityError] = ScheduleAuthorityError,
) -> tuple[
    Callable[[], SourceEvidence],
    Callable[[], SourceEvidence],
    Callable[[SourceEvidence], None],
    Callable[[SourceEvidence], None],
]:
    # Trust-critical dependencies are captured as defaults above, evaluated once
    # at module load before any caller could rebind the module globals of the
    # same name. `validate`/`acquire` below must reference only these captured
    # names, not the bare module globals, so later rebinding cannot weaken the
    # provenance/integrity checks that gate evidence acceptance.
    kalshi_contract = (
        "kalshi-kxgdp-event-v1",
        KALSHI_HOST,
        KALSHI_ORIGIN,
        KALSHI_EVENT_PATH,
        HTTP_METHOD,
        ("application/json",),
    )
    bea_contract = (
        "bea-release-schedule-v1",
        BEA_HOST,
        BEA_ORIGIN,
        BEA_SCHEDULE_PATH,
        HTTP_METHOD,
        ("text/html",),
    )
    issued: list[tuple[_RawResponse, tuple[object, ...], SourceEvidence, tuple[object, ...]]] = []

    def validate(
        contract: tuple[str, str, str, str, str, tuple[str, ...]], evidence: SourceEvidence
    ) -> None:
        match = next((item for item in issued if item[2] is evidence), None)
        if match is None or evidence_fingerprint(evidence) != match[3]:
            raise error_cls("source evidence is unissued or mutated")
        identity, host, origin, path, method, allowed_content_types = contract
        if (
            type(evidence) is not source_evidence_cls
            or evidence.source_host != host
            or evidence.source_locator != origin + path
            or evidence.source_path != path
            or evidence.method != method
            or evidence.http_status != success_status
            or evidence.content_type not in allowed_content_types
        ):
            raise error_cls("source evidence endpoint binding failed")
        if (
            sha256(evidence.raw_body).hexdigest() != evidence.raw_sha256
            or len(evidence.raw_body) != evidence.content_length
        ):
            raise error_cls("source evidence body integrity failed")
        expected = fingerprint(
            transport_policy_identity,
            identity,
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
        if evidence.source_identity != expected or evidence.parser_version != parser_version:
            raise error_cls("source evidence fingerprint failed")

    def acquire(contract: tuple[str, str, str, str, str, tuple[str, ...]]) -> SourceEvidence:
        identity, host, origin, path, method, expected = contract
        connection = https_connection(host, timeout=10.0, context=create_default_context())
        try:
            connection.request(method, path, headers={"Accept": ",".join(expected)})
            response = connection.getresponse()
            body = response.read(max_response_bytes + 1)
            content = response.getheader("Content-Type", "").split(";", 1)[0].strip().casefold()
            if (
                response.status != success_status
                or not body
                or len(body) > max_response_bytes
                or content not in expected
            ):
                raise error_cls("source response is incomplete")
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
            raw = raw_response_cls(
                origin + path,
                host,
                path,
                method,
                response.status,
                content,
                body,
                utc_now(),
                headers,
            )
            evidence = object.__new__(source_evidence_cls)
            values: dict[str, object] = {
                "source_locator": raw.locator,
                "source_host": raw.host,
                "source_path": raw.path,
                "method": raw.method,
                "http_status": raw.status,
                "content_type": raw.content_type,
                "content_length": len(raw.body),
                "acquired_at": strict_utc(raw.acquired_at, "acquired_at"),
                "parser_version": parser_version,
                "raw_body": raw.body,
                "raw_sha256": sha256(raw.body).hexdigest(),
                "headers": raw.headers,
            }
            values["source_identity"] = fingerprint(
                transport_policy_identity, identity, *(values[name] for name in values)
            )
            for name, value in values.items():
                object.__setattr__(evidence, name, value)
            issued.append((raw, raw_fingerprint(raw), evidence, evidence_fingerprint(evidence)))
            return evidence
        finally:
            connection.close()

    def acquire_kalshi_event() -> SourceEvidence:
        return acquire(kalshi_contract)

    def acquire_bea_schedule() -> SourceEvidence:
        return acquire(bea_contract)

    def validate_kalshi(evidence: SourceEvidence) -> None:
        validate(kalshi_contract, evidence)

    def validate_bea(evidence: SourceEvidence) -> None:
        validate(bea_contract, evidence)

    return acquire_kalshi_event, acquire_bea_schedule, validate_kalshi, validate_bea


(
    _acquire_kalshi_event,
    _acquire_bea_schedule,
    _validate_kalshi_evidence,
    _validate_bea_evidence,
) = _make_evidence_issuer()


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


def _bea(
    evidence: SourceEvidence, expected: Quarter, bea_origin: str = BEA_ORIGIN
) -> BEAReleaseSchedule:
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
    if not (link.startswith("/") or link.startswith(bea_origin + "/")):
        raise ScheduleAuthorityError("BEA locator is not first-party")
    locator = link if link.startswith("http") else bea_origin + link
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


# NOTE: `_json`, `_quarter`, `_quarters`, `_decimal`, `_strike`, `_rules`,
# `_ScheduleParser`, `_local`, `_bea`, `_market` above remain as standalone,
# fully-functional module-level definitions: several are exercised directly by
# tests (`_quarter`, `_quarters`, `_bea`), and the rest are kept as meaningful
# rebind targets for adversarial tests. None of them are called by the frozen
# issuance/validation path below -- `_make_authority_issuer()` below defines
# its own closure-local reimplementation of the entire semantic call graph
# (constants, types, and helpers alike, captured as parameters with defaults
# evaluated once at module load) so that ordinary rebinding of any of the
# module globals of the same name, at any depth of the call graph, cannot
# alter what the canonical issuer accepts as COMPLETE_AUTHORITY.


def _make_authority_issuer(
    validate_kalshi: Callable[[SourceEvidence], None],
    validate_bea: Callable[[SourceEvidence], None],
    *,
    event_ticker: str = EVENT_TICKER,
    series_ticker: str = SERIES_TICKER,
    metric_semantics: str = METRIC_SEMANTICS,
    settlement_edition: str = SETTLEMENT_EDITION,
    timezone_name: str = TIMEZONE_NAME,
    timezone_policy_identity: str = TIMEZONE_POLICY_IDENTITY,
    parser_version: str = PARSER_VERSION,
    bea_origin_default: str = BEA_ORIGIN,
    quarter_cls: type[Quarter] = Quarter,
    strike_type_cls: type[StrikeType] = StrikeType,
    strike_semantics_cls: type[StrikeSemantics] = StrikeSemantics,
    eligible_market_cls: type[EligibleMarket] = EligibleMarket,
    bea_release_schedule_cls: type[BEAReleaseSchedule] = BEAReleaseSchedule,
    schedule_authority_cls: type[ScheduleAuthority] = ScheduleAuthority,
    authority_status_cls: type[AuthorityStatus] = AuthorityStatus,
    result_cls: type[ScheduleAuthorityResult] = ScheduleAuthorityResult,
    error_cls: type[ScheduleAuthorityError] = ScheduleAuthorityError,
    fingerprint: Callable[..., str] = _fingerprint,
    strict_utc: Callable[[datetime, str], datetime] = _strict_utc,
    authority_fingerprint: Callable[[tuple[object, ...]], str] = _authority_fingerprint,
    json: Any = json,
    re: Any = re,
    Decimal: Any = Decimal,
    InvalidOperation: Any = InvalidOperation,
    datetime: Any = datetime,
    date: Any = date,
    UTC: Any = UTC,
    ZoneInfo: Any = ZoneInfo,
    ZoneInfoNotFoundError: Any = ZoneInfoNotFoundError,
    HTMLParser: Any = HTMLParser,
) -> tuple[
    Callable[[SourceEvidence, SourceEvidence | None, SourceEvidence], ScheduleAuthorityResult],
    Callable[[ScheduleAuthority], None],
]:
    # Every callable/class/constant this canonical issuer needs is captured
    # above as a parameter default, evaluated once when this `def` executes at
    # module load -- before any caller has had a chance to run. The nested
    # helpers below reference only these captured names (or each other, which
    # Python resolves via this function's own enclosing scope, not the module
    # globals of the same name), so later `subject.<name> = ...` rebinding of
    # any of them cannot change what this issuer accepts.

    def _string(value: object, field: str) -> str:
        if type(value) is not str or not value.strip():
            raise error_cls(f"{field} is missing or malformed")
        return value.strip()

    def _timestamp(value: object, field: str) -> datetime:
        if type(value) is not str or value.startswith("0001-"):
            raise error_cls(f"{field} timestamp is missing or sentinel")
        try:
            return strict_utc(datetime.fromisoformat(value.replace("Z", "+00:00")), field)
        except ValueError as exc:
            raise error_cls(f"{field} timestamp is malformed") from exc

    def _optional_timestamp(value: object, field: str) -> datetime | None:
        return None if value in (None, "") else _timestamp(value, field)

    def _quarters(texts: list[object]) -> Quarter:
        found: list[Quarter] = []
        for item in texts:
            if not isinstance(item, str):
                continue
            for match in re.finditer(r"\bQ([1-4])\s+(20\d{2})\b", item, re.IGNORECASE):
                found.append(quarter_cls(f"{match.group(2)}-Q{match.group(1)}", match.group(0)))
            for match in re.finditer(r"\b(20\d{2})-Q([1-4])\b", item, re.IGNORECASE):
                found.append(quarter_cls(f"{match.group(1)}-Q{match.group(2)}", match.group(0)))
            for match in re.finditer(
                r"\b([1-4])(?:st|nd|rd|th)\s+Quarter\s+(20\d{2})\b", item, re.IGNORECASE
            ):
                found.append(quarter_cls(f"{match.group(2)}-Q{match.group(1)}", match.group(0)))
        if not found or len({item.canonical for item in found}) != 1:
            raise error_cls("quarter evidence is absent or contradictory")
        return found[0]

    def _decimal(value: object, field: str) -> Decimal:
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise error_cls(f"{field} is malformed")
        try:
            result = Decimal(str(value))
        except InvalidOperation as exc:
            raise error_cls(f"{field} is malformed") from exc
        if not result.is_finite():
            raise error_cls(f"{field} is malformed")
        return result

    def _strike(
        raw: dict[str, object], ticker: str, primary: str, secondary: str
    ) -> StrikeSemantics:
        try:
            kind = strike_type_cls(_string(raw.get("strike_type"), "strike_type").casefold())
        except ValueError as exc:
            raise error_cls("unsupported strike_type") from exc
        floor_text = _string(raw.get("floor_strike"), "floor_strike")
        floor = _decimal(floor_text, "floor_strike")
        cap_value = raw.get("cap_strike")
        cap_text = None if cap_value in (None, "") else _string(cap_value, "cap_strike")
        cap = None if cap_text is None else _decimal(cap_text, "cap_strike")
        if kind is strike_type_cls.BETWEEN and cap is None:
            raise error_cls("between strike requires cap_strike")
        if kind is not strike_type_cls.BETWEEN and cap is not None:
            raise error_cls("floor/cap contradiction")
        if cap is not None and floor >= cap:
            raise error_cls("floor/cap contradiction")
        suffix = ticker.rsplit("-T", 1)[-1] if "-T" in ticker else ""
        if suffix and _decimal(suffix, "ticker strike") != floor:
            raise error_cls("ticker disagrees with explicit strike")
        lower = f"{primary}\n{secondary}".casefold()
        if kind is strike_type_cls.GREATER:
            comparator, pattern = "greater_than", r"more\s+than\s+([-+]?\d+(?:\.\d+)?)"
            if "more than" not in lower or re.search(r"less\s+than|between", lower):
                raise error_cls("comparator disagrees with strike_type")
            threshold = re.search(pattern, lower)
            if threshold is None or _decimal(threshold.group(1), "rule threshold") != floor:
                raise error_cls("rule threshold disagrees with floor_strike")
        elif kind is strike_type_cls.LESS:
            comparator, pattern = "less_than", r"less\s+than\s+([-+]?\d+(?:\.\d+)?)"
            if "less than" not in lower or re.search(r"more\s+than|between", lower):
                raise error_cls("comparator disagrees with strike_type")
            threshold = re.search(pattern, lower)
            if threshold is None or _decimal(threshold.group(1), "rule threshold") != floor:
                raise error_cls("rule threshold disagrees with floor_strike")
        else:
            comparator = "between"
            threshold = re.search(
                r"between\s+([-+]?\d+(?:\.\d+)?)[^\d]+([-+]?\d+(?:\.\d+)?)", lower
            )
            if (
                threshold is None
                or cap is None
                or _decimal(threshold.group(1), "rule floor") != floor
                or _decimal(threshold.group(2), "rule cap") != cap
            ):
                raise error_cls("rule thresholds disagree with floor/cap")
        return strike_semantics_cls(
            kind,
            floor_text,
            cap_text,
            floor,
            cap,
            comparator,
            fingerprint(lower, floor, cap, comparator),
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
            raise error_cls("GDP rule semantics are unsupported")
        editions = re.findall(r"\b(?:advance|second|third) estimate\b", low)
        if editions != ["advance estimate"] or "revised" in low or "later estimate" in low:
            raise error_cls("settlement edition is not exclusively Advance Estimate")
        if _quarters([primary, secondary]).canonical != quarter.canonical:
            raise error_cls("market rule quarter disagrees")
        return primary, secondary

    class _ScheduleParser(HTMLParser):  # type: ignore[misc]
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
            zone = ZoneInfo(timezone_name)
            local = datetime.strptime(value, "%Y-%m-%d %I:%M %p")
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise error_cls(f"{field} local time malformed") from exc
        candidates = []
        for fold in (0, 1):
            candidate = local.replace(tzinfo=zone, fold=fold)
            if candidate.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == local:
                candidates.append(candidate.astimezone(UTC))
        if len(set(candidates)) != 1:
            raise error_cls(f"{field} is ambiguous or nonexistent")
        return candidates[0].replace(tzinfo=UTC)

    def _bea(
        evidence: SourceEvidence, expected: Quarter, bea_origin: str = bea_origin_default
    ) -> BEAReleaseSchedule:
        try:
            html = evidence.raw_body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise error_cls("BEA schedule is not UTF-8") from exc
        parser = _ScheduleParser()
        parser.feed(html)
        parser.close()
        matches: list[tuple[tuple[str, ...], tuple[str, ...], int | None]] = []
        for row, links, publication_year in parser.rows:
            low = " | ".join(row).casefold()
            try:
                row_quarter = _quarters(list(row))
            except error_cls:
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
            raise error_cls("BEA schedule target is absent or duplicated")
        row, links, publication_year = matches[0]
        if len(row) < 3 or len(links) != 1:
            raise error_cls("BEA locator is not exact-row-bound")
        match = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2})", row[0])
        if not match:
            raise error_cls("BEA release date is malformed")
        try:
            month = datetime.strptime(match.group(1), "%B").month
        except ValueError as exc:
            raise error_cls("BEA release month is malformed") from exc
        if publication_year is None:
            raise error_cls("BEA publication year is not source-bound")
        release_date = date(publication_year, month, int(match.group(2)))
        if not row[1].strip():
            raise error_cls("BEA release timing precision is insufficient")
        release = _local(f"{release_date.isoformat()} {row[1]}", "BEA release")
        link = links[0]
        if not (link.startswith("/") or link.startswith(bea_origin + "/")):
            raise error_cls("BEA locator is not first-party")
        locator = link if link.startswith("http") else bea_origin + link
        if f"/news/{publication_year}/" not in locator:
            raise error_cls("BEA locator year disagrees with publication year")
        quarter_words = {"Q1": "first", "Q2": "second", "Q3": "third", "Q4": "fourth"}
        expected_suffix = expected.canonical[5:]
        expected_word = quarter_words[expected_suffix]
        if (
            "advance-estimate" not in locator.casefold()
            or expected.canonical[:4] not in locator.casefold()
            or f"{expected_word}-quarter-{expected.canonical[:4]}" not in locator.casefold()
        ):
            raise error_cls("BEA locator does not identify matched row")
        return bea_release_schedule_cls(
            release, timezone_name, locator, settlement_edition, expected
        )

    def _json(evidence: SourceEvidence) -> dict[str, object]:
        try:
            value = json.loads(evidence.raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise error_cls("Kalshi response is malformed JSON") from exc
        if not isinstance(value, dict):
            raise error_cls("Kalshi response must be an object")
        return value

    def _market(raw: dict[str, object], quarter: Quarter) -> EligibleMarket:
        ticker = _string(raw.get("ticker"), "market ticker")
        if raw.get("event_ticker") != event_ticker:
            raise error_cls("wrong-event market")
        status = _string(raw.get("status"), "market status")
        if status not in {"active", "open"}:
            raise error_cls("inactive/ineligible market")
        primary, secondary = _rules(raw, quarter)
        return eligible_market_cls(
            ticker,
            event_ticker,
            status,
            _strike(raw, ticker, primary, secondary),
            _timestamp(raw.get("open_time"), "market open"),
            _timestamp(raw.get("close_time"), "market close"),
            _optional_timestamp(raw.get("expected_expiration_time"), "expected expiration"),
            _optional_timestamp(raw.get("latest_expiration_time"), "latest expiration"),
            fingerprint(primary, secondary, quarter.canonical),
            primary,
            secondary,
        )

    def _incomplete(
        reason: str,
        event: SourceEvidence | None = None,
        market: SourceEvidence | None = None,
        bea: SourceEvidence | None = None,
    ) -> ScheduleAuthorityResult:
        return result_cls(
            authority_status_cls.EVIDENCE_INCOMPLETE, None, reason, event, market, bea
        )

    def _issue_authority_impl(
        event_evidence: SourceEvidence,
        market_evidence: SourceEvidence | None,
        bea_evidence: SourceEvidence,
    ) -> ScheduleAuthorityResult:
        try:
            validate_kalshi(event_evidence)
            validate_bea(bea_evidence)
            if market_evidence is not None:
                validate_kalshi(market_evidence)
            payload = _json(event_evidence)
            event = payload.get("event")
            if (
                not isinstance(event, dict)
                or event.get("event_ticker") != event_ticker
                or event.get("series_ticker") != series_ticker
            ):
                raise error_cls("event identity mismatch")
            nested = event.get("markets")
            if not isinstance(nested, list) or not nested:
                raise error_cls("eligible market set is absent")
            declared_body_count = event.get("market_count", payload.get("market_count"))
            if declared_body_count is not None and declared_body_count != len(nested):
                raise error_cls("market collection is internally inconsistent")
            headers = dict(event_evidence.headers)
            expected_count, terminal = (
                headers.get("x-kalshi-event-market-count"),
                headers.get("x-kalshi-event-pagination-terminal"),
            )
            if expected_count is None or terminal != "true":
                raise error_cls("market result completeness is unproven")
            if int(expected_count) != len(nested):
                raise error_cls("source market count disagrees")
            pagination = event.get("pagination", payload.get("pagination"))
            if not isinstance(pagination, dict) or pagination.get("cursor") not in (None, ""):
                raise error_cls("market result set has omitted pages")
            quarter = _quarters(
                [event.get("strike_period"), event.get("title"), event.get("sub_title")]
            )
            markets = tuple(
                sorted(
                    (_market(item, quarter) for item in nested if isinstance(item, dict)),
                    key=lambda item: (item.strike.normalized_floor, item.ticker),
                )
            )
            if len(markets) != len(nested) or len({item.ticker for item in markets}) != len(
                markets
            ):
                raise error_cls("duplicate or malformed market set")
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
                raise error_cls("duplicate equivalent strike")
            schedule = _bea(bea_evidence, quarter)
            for item in markets:
                if not item.open_at < item.close_at:
                    raise error_cls("market lifecycle ordering is invalid")
                if (
                    item.expected_expiration_at is not None
                    and item.expected_expiration_at < item.close_at
                ):
                    raise error_cls("expected expiration precedes close")
                if (
                    item.latest_expiration_at is not None
                    and item.expected_expiration_at is not None
                    and item.latest_expiration_at < item.expected_expiration_at
                ):
                    raise error_cls("latest expiration precedes expected expiration")
                if item.close_at >= schedule.release_at:
                    raise error_cls("KALSHI_CLOSE_NOT_STRICTLY_BEFORE_BEA_RELEASE")
            values = (
                event_ticker,
                series_ticker,
                quarter,
                quarter.canonical,
                metric_semantics,
                settlement_edition,
                schedule,
                schedule.release_at,
                schedule.bea_release_locator,
                markets,
                (event_evidence.source_identity, bea_evidence.source_identity),
                (event_evidence.raw_sha256, bea_evidence.raw_sha256),
                timezone_policy_identity,
                parser_version,
                "NONE",
            )
            authority = object.__new__(schedule_authority_cls)
            for name, value in zip(
                schedule_authority_cls.__dataclass_fields__, values, strict=True
            ):
                object.__setattr__(authority, name, value)
            return result_cls(
                authority_status_cls.COMPLETE_AUTHORITY,
                authority,
                None,
                event_evidence,
                market_evidence,
                bea_evidence,
            )
        except (error_cls, TypeError, ValueError) as exc:
            return _incomplete(str(exc), event_evidence, market_evidence, bea_evidence)

    issued: dict[
        int, tuple[ScheduleAuthority, tuple[object, ...], str, SourceEvidence, SourceEvidence]
    ] = {}

    def issue(
        event: SourceEvidence, market: SourceEvidence | None, bea: SourceEvidence
    ) -> ScheduleAuthorityResult:
        result = _issue_authority_impl(event, market, bea)
        if result.authority is not None:
            values = tuple(
                getattr(result.authority, name)
                for name in schedule_authority_cls.__dataclass_fields__
            )
            issued[id(result.authority)] = (
                result.authority,
                values,
                authority_fingerprint(values),
                event,
                bea,
            )
        return result

    def validate(authority: ScheduleAuthority) -> None:
        record = issued.get(id(authority))
        if (
            type(authority) is not schedule_authority_cls
            or record is None
            or record[0] is not authority
        ):
            raise error_cls("schedule authority is unissued")
        values = tuple(
            getattr(authority, name) for name in schedule_authority_cls.__dataclass_fields__
        )
        if record[1] != values or record[2] != authority_fingerprint(values):
            raise error_cls("schedule authority was mutated")
        validate_kalshi(record[3])
        validate_bea(record[4])
        if (
            authority.event_ticker != event_ticker
            or authority.series_ticker != series_ticker
            or authority.target_quarter != authority.quarter.canonical
        ):
            raise error_cls("schedule authority identity mismatch")
        bea_origin = record[4].source_locator.removesuffix(record[4].source_path)
        if (
            not authority.bea_release_locator.startswith(bea_origin + "/")
            or authority.bea_release_locator != authority.bea_schedule.bea_release_locator
        ):
            raise error_cls("release locator binding failed")
        if not authority.eligible_markets or len(
            {item.ticker for item in authority.eligible_markets}
        ) != len(authority.eligible_markets):
            raise error_cls("schedule authority market set is invalid")
        for item in authority.eligible_markets:
            if not item.open_at < item.close_at or (
                item.expected_expiration_at is not None
                and item.expected_expiration_at < item.close_at
            ):
                raise error_cls("schedule authority lifecycle is invalid")

    return issue, validate


_issue_authority, validate_schedule_authority = _make_authority_issuer(
    _validate_kalshi_evidence, _validate_bea_evidence
)


def _make_acquisition_runner(
    acquire_event: Callable[[], SourceEvidence],
    acquire_bea: Callable[[], SourceEvidence],
    issue: Callable[
        [SourceEvidence, SourceEvidence | None, SourceEvidence], ScheduleAuthorityResult
    ],
    *,
    error_cls: type[ScheduleAuthorityError] = ScheduleAuthorityError,
    http_exception: type[http.client.HTTPException] = http.client.HTTPException,
    status_cls: type[AuthorityStatus] = AuthorityStatus,
    result_cls: type[ScheduleAuthorityResult] = ScheduleAuthorityResult,
) -> Callable[[], ScheduleAuthorityResult]:
    def run() -> ScheduleAuthorityResult:
        """Acquire research-only event, market-set, and BEA schedule facts."""
        event_evidence: SourceEvidence | None = None
        bea_evidence: SourceEvidence | None = None

        try:
            event_evidence = acquire_event()
            bea_evidence = acquire_bea()
            return issue(event_evidence, None, bea_evidence)
        except (error_cls, OSError, TimeoutError, ValueError, http_exception) as exc:
            return result_cls(
                status_cls.EVIDENCE_INCOMPLETE,
                None,
                str(exc),
                event_evidence,
                None,
                bea_evidence,
            )

    return run


_run_acquisition = _make_acquisition_runner(
    _acquire_kalshi_event, _acquire_bea_schedule, _issue_authority
)


def _make_public_acquirer(
    run: Callable[[], ScheduleAuthorityResult],
) -> Callable[[], ScheduleAuthorityResult]:
    def acquire_schedule_authority() -> ScheduleAuthorityResult:
        """Acquire research-only event, market-set, and BEA schedule facts."""
        return run()

    return acquire_schedule_authority


acquire_schedule_authority = _make_public_acquirer(_run_acquisition)


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
