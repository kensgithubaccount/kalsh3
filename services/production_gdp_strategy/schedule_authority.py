"""Fixed, research-only KXGDP/BEA schedule authority acquisition.

This module owns the only public acquisition entrypoint.  It is deliberately
limited to the one D1-G3-S1 event and one selected market; it does not discover
series, acquire fees, evaluate a trade, start a clock, or access an account.

Positive authority is issuer-controlled.  Callers cannot provide locators,
methods, timestamps, transports, raw bodies, or parsed source objects.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import re
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from html.parser import HTMLParser
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

EVENT_TICKER: Final = "KXGDP-26OCT30"
SELECTED_MARKET_TICKER: Final = "KXGDP-26OCT30-T1.0"
TARGET_QUARTER: Final = "Q3 2026"
SERIES_TICKER: Final = "KXGDP"
TIMEZONE_NAME: Final = "America/New_York"
METRIC_SEMANTICS: Final = "real GDP quarter-over-quarter growth, seasonally adjusted annual rate"
SETTLEMENT_EDITION: Final = "BEA Advance Estimate"

KALSHI_ORIGIN: Final = "https://external-api.kalshi.com"
KALSHI_HOST: Final = "external-api.kalshi.com"
KALSHI_EVENT_PATH: Final = f"/trade-api/v2/events/{EVENT_TICKER}?with_nested_markets=true"
KALSHI_MARKET_PATH: Final = f"/trade-api/v2/markets/{SELECTED_MARKET_TICKER}"
BEA_ORIGIN: Final = "https://www.bea.gov"
BEA_HOST: Final = "www.bea.gov"
BEA_SCHEDULE_PATH: Final = "/news/schedule/"
HTTP_METHOD: Final = "GET"
SUCCESS_STATUS: Final = 200
TIMEOUT_SECONDS: Final = 10.0
MAX_RESPONSE_BYTES: Final = 4_000_000
PARSER_VERSION: Final = "d1-g3-s1-schedule-parser-v1"
TIMEZONE_POLICY_IDENTITY: Final = "iana-america-new-york-strict-local-v1"
TRANSPORT_POLICY_IDENTITY: Final = "d1-g3-s1-fixed-public-get-v1"
_UTC = UTC
_AUTHORITY_ISSUER = object()


class AuthorityStatus(StrEnum):
    COMPLETE_AUTHORITY = "COMPLETE AUTHORITY"
    EVIDENCE_INCOMPLETE = "EVIDENCE_INCOMPLETE"


class ScheduleAuthorityError(ValueError):
    """An acquisition or parsing failure that must remain incomplete."""


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


@dataclass(frozen=True, slots=True)
class SourceEvidence:
    """Exact source bytes issued by this module's fixed acquisition path."""

    source_locator: str
    source_identity: str
    http_status: int
    content_type: str
    acquired_at: datetime
    raw_body: bytes
    raw_sha256: str

    def __init__(self, *, response: _RawResponse, _capability: object) -> None:
        if _capability is not _AUTHORITY_ISSUER:
            raise ScheduleAuthorityError("source evidence requires the reviewed issuer")
        if response.status != SUCCESS_STATUS or not response.body:
            raise ScheduleAuthorityError("only successful non-empty responses become evidence")
        if response.acquired_at.tzinfo is not UTC:
            raise ScheduleAuthorityError("acquisition completion time must be canonical UTC")
        raw_hash = hashlib.sha256(response.body).hexdigest()
        identity = hashlib.sha256(
            "\x00".join(
                (
                    TRANSPORT_POLICY_IDENTITY,
                    response.locator,
                    response.method,
                    str(response.status),
                    response.content_type,
                    raw_hash,
                )
            ).encode("utf-8")
        ).hexdigest()
        object.__setattr__(self, "source_locator", response.locator)
        object.__setattr__(self, "source_identity", identity)
        object.__setattr__(self, "http_status", response.status)
        object.__setattr__(self, "content_type", response.content_type)
        object.__setattr__(self, "acquired_at", response.acquired_at)
        object.__setattr__(self, "raw_body", response.body)
        object.__setattr__(self, "raw_sha256", raw_hash)


@dataclass(frozen=True, slots=True)
class ScheduleAuthority:
    """Issuer-created positive authority; direct caller construction is blocked."""

    event_ticker: str
    series_ticker: str | None
    market_ticker: str
    target_quarter: str
    metric_semantics: str
    settlement_edition: str
    bea_release_at: datetime
    kalshi_open_at: datetime
    kalshi_close_at: datetime
    kalshi_expected_expiration_at: datetime | None
    kalshi_latest_expiration_at: datetime | None
    market_status: str
    kalshi_rule_evidence_id: str
    kalshi_rule_sha256: str
    kalshi_event_evidence_id: str
    kalshi_event_sha256: str
    kalshi_market_evidence_id: str
    kalshi_market_sha256: str
    bea_schedule_evidence_id: str
    bea_schedule_sha256: str
    event_acquired_at: datetime
    market_acquired_at: datetime
    bea_acquired_at: datetime
    timezone_policy_identity: str
    parser_version: str
    contradiction_result: str

    def __init__(self, *, values: dict[str, object], _capability: object) -> None:
        if _capability is not _AUTHORITY_ISSUER:
            raise ScheduleAuthorityError("positive authority requires the reviewed issuer")
        for key, value in values.items():
            object.__setattr__(self, key, value)


@dataclass(frozen=True, slots=True)
class ScheduleAuthorityResult:
    """Public result.  Incomplete results may expose diagnostic source evidence."""

    status: AuthorityStatus
    authority: ScheduleAuthority | None
    contradiction: str | None
    event_evidence: SourceEvidence | None
    market_evidence: SourceEvidence | None
    bea_evidence: SourceEvidence | None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _strict_utc(value: datetime, field: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ScheduleAuthorityError(f"{field} must be timezone-aware")
    result = value.astimezone(UTC)
    if result.tzinfo is not UTC:
        result = result.replace(tzinfo=UTC)
    return result


def _strict_local(value: str, field: str) -> datetime:
    try:
        zone = ZoneInfo(TIMEZONE_NAME)
        local = datetime.strptime(value, "%Y-%m-%d %I:%M %p")
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ScheduleAuthorityError(f"{field} local time is malformed") from exc
    candidates: list[datetime] = []
    for fold in (0, 1):
        candidate = local.replace(tzinfo=zone, fold=fold)
        if candidate.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == local:
            candidates.append(candidate.astimezone(UTC))
    if len(set(candidates)) != 1:
        raise ScheduleAuthorityError(f"{field} is ambiguous or nonexistent")
    return candidates[0].replace(tzinfo=UTC)


def _aware_source_timestamp(value: object, field: str) -> datetime:
    if type(value) is not str or value.startswith("0001-"):
        raise ScheduleAuthorityError(f"{field} timestamp is missing or sentinel")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ScheduleAuthorityError(f"{field} timestamp is malformed") from exc
    return _strict_utc(parsed, field)


def _content_type(value: str, expected: tuple[str, ...]) -> str:
    normalized = value.split(";", 1)[0].strip().casefold()
    if normalized not in expected:
        raise ScheduleAuthorityError("source content type is not authoritative")
    return normalized


def _fixed_get(host: str, origin: str, path: str, expected_types: tuple[str, ...]) -> _RawResponse:
    if host not in (KALSHI_HOST, BEA_HOST) or origin not in (KALSHI_ORIGIN, BEA_ORIGIN):
        raise ScheduleAuthorityError("source origin is outside the reviewed allowlist")
    if not path.startswith("/") or ".." in path.split("/") or "//" in path:
        raise ScheduleAuthorityError("source path is outside the reviewed allowlist")
    connection = http.client.HTTPSConnection(
        host, timeout=TIMEOUT_SECONDS, context=ssl.create_default_context()
    )
    try:
        connection.request(HTTP_METHOD, path, headers={"Accept": ",".join(expected_types)})
        response = connection.getresponse()
        if response.status >= 300 and response.status < 400:
            raise ScheduleAuthorityError("source redirect rejected")
        declared = response.getheader("Content-Length")
        if declared is not None and int(declared) > MAX_RESPONSE_BYTES:
            raise ScheduleAuthorityError("source response exceeded bounded size")
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ScheduleAuthorityError("source response exceeded bounded size")
        content_type = response.getheader("Content-Type", "")
        checked_type = _content_type(content_type, expected_types)
        return _RawResponse(
            locator=origin + path,
            host=host,
            path=path,
            method=HTTP_METHOD,
            status=response.status,
            content_type=checked_type,
            body=body,
            acquired_at=_utc_now(),
        )
    except ScheduleAuthorityError:
        raise
    except (OSError, TimeoutError, ValueError, http.client.HTTPException) as exc:
        raise ScheduleAuthorityError("fixed public source acquisition failed") from exc
    finally:
        connection.close()


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[str, ...]] = []
        self.text_parts: list[str] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() == "tr":
            self._row = []
        elif tag.casefold() in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(" ".join(" ".join(self._cell).split()))
            self._cell = None
        elif lowered == "tr" and self._row is not None:
            if self._row:
                self.rows.append(tuple(self._row))
            self._row = None


def _parse_bea_schedule(evidence: SourceEvidence) -> datetime:
    try:
        html = evidence.raw_body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ScheduleAuthorityError("BEA schedule is not UTF-8 HTML") from exc
    parser = _TableParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # HTMLParser can expose malformed source exceptions.
        raise ScheduleAuthorityError("BEA schedule HTML is malformed") from exc
    year_text = " ".join(parser.text_parts)
    if "year 2026" not in year_text.casefold():
        raise ScheduleAuthorityError("BEA schedule year authority is missing")
    matches: list[tuple[str, str]] = []
    for row in parser.rows:
        joined = " | ".join(row)
        lowered = joined.casefold()
        if (
            "gdp" in lowered
            and "advance estimate" in lowered
            and "second estimate" not in lowered
            and "third estimate" not in lowered
            and "annual" not in lowered
            and "3rd quarter 2026" in lowered
        ):
            if len(row) < 3:
                raise ScheduleAuthorityError("BEA schedule row layout drifted")
            matches.append((row[0], row[1]))
    if len(matches) != 1:
        raise ScheduleAuthorityError("BEA schedule has zero or duplicate target matches")
    date_text, time_text = matches[0]
    try:
        month, day = date_text.split()
        month_number = datetime.strptime(month, "%B").month
        date_value = f"2026-{month_number:02d}-{int(day):02d}"
    except (ValueError, TypeError) as exc:
        raise ScheduleAuthorityError("BEA schedule release date is malformed") from exc
    if not re.fullmatch(r"\d{1,2}:\d{2} [AP]M", time_text):
        raise ScheduleAuthorityError("BEA schedule release time is missing or malformed")
    return _strict_local(f"{date_value} {time_text}", "BEA release")


def _json_object(evidence: SourceEvidence) -> dict[str, object]:
    try:
        value = json.loads(evidence.raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ScheduleAuthorityError("Kalshi response is malformed JSON") from exc
    if not isinstance(value, dict):
        raise ScheduleAuthorityError("Kalshi response must be an object")
    return value


def _validate_source_evidence(evidence: SourceEvidence) -> None:
    if type(evidence) is not SourceEvidence or type(evidence.raw_body) is not bytes:
        raise ScheduleAuthorityError("source evidence type is not issuer-controlled")
    if hashlib.sha256(evidence.raw_body).hexdigest() != evidence.raw_sha256:
        raise ScheduleAuthorityError("source evidence raw-body hash mismatch")
    if evidence.http_status != SUCCESS_STATUS or evidence.acquired_at.tzinfo is not UTC:
        raise ScheduleAuthorityError("source evidence acquisition metadata is invalid")
    expected_identity = hashlib.sha256(
        "\x00".join(
            (
                TRANSPORT_POLICY_IDENTITY,
                evidence.source_locator,
                HTTP_METHOD,
                str(evidence.http_status),
                evidence.content_type,
                evidence.raw_sha256,
            )
        ).encode("utf-8")
    ).hexdigest()
    if evidence.source_identity != expected_identity:
        raise ScheduleAuthorityError("source evidence identity mismatch")


def _dict(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ScheduleAuthorityError(f"{field} object is missing")
    return value


def _string(value: object, field: str, *, nonempty: bool = True) -> str:
    if type(value) is not str or (nonempty and not value):
        raise ScheduleAuthorityError(f"{field} is missing or malformed")
    return value


def _optional_timestamp(value: object, field: str) -> datetime | None:
    if value is None or value == "":
        return None
    return _aware_source_timestamp(value, field)


def _market_from_event(event_payload: dict[str, object]) -> dict[str, object]:
    event = _dict(event_payload.get("event"), "event")
    nested = event.get("markets")
    if not isinstance(nested, list):
        raise ScheduleAuthorityError("event nested markets are missing")
    matches = [
        item
        for item in nested
        if isinstance(item, dict) and item.get("ticker") == SELECTED_MARKET_TICKER
    ]
    if len(matches) != 1:
        raise ScheduleAuthorityError("selected market is absent or duplicated in event")
    return matches[0]


def _validate_semantics(
    *,
    event: dict[str, object],
    event_market: dict[str, object],
    direct_market: dict[str, object],
    bea_release: datetime,
) -> tuple[dict[str, object], str | None]:
    if event.get("event_ticker") != EVENT_TICKER:
        raise ScheduleAuthorityError("event identity mismatch")
    if event.get("series_ticker") not in (SERIES_TICKER, None):
        raise ScheduleAuthorityError("series identity mismatch")
    if event.get("strike_period") != TARGET_QUARTER:
        raise ScheduleAuthorityError("target quarter mismatch")
    if event_market != direct_market:
        comparable = (
            "ticker",
            "event_ticker",
            "rules_primary",
            "rules_secondary",
            "open_time",
            "close_time",
            "expected_expiration_time",
            "latest_expiration_time",
            "status",
        )
        if any(event_market.get(key) != direct_market.get(key) for key in comparable):
            raise ScheduleAuthorityError("event and direct market authority disagree")
    ticker = _string(direct_market.get("ticker"), "market ticker")
    if ticker != SELECTED_MARKET_TICKER or direct_market.get("event_ticker") != EVENT_TICKER:
        raise ScheduleAuthorityError("market identity mismatch")
    primary = _string(direct_market.get("rules_primary"), "primary rules")
    secondary = _string(direct_market.get("rules_secondary"), "secondary rules")
    rules = f"{primary}\n{secondary}"
    lowered = rules.casefold()
    if not all(
        phrase in lowered
        for phrase in (
            "real gdp",
            "seasonally adjusted",
            "annualized",
            "advance estimate",
            "q3 2026",
        )
    ):
        raise ScheduleAuthorityError("GDP metric or Advance Estimate semantics are incomplete")
    if "second estimate" in lowered or "third estimate" in lowered or "nominal gdp" in lowered:
        raise ScheduleAuthorityError("wrong GDP edition or metric")
    open_at = _aware_source_timestamp(direct_market.get("open_time"), "Kalshi open")
    close_at = _aware_source_timestamp(direct_market.get("close_time"), "Kalshi close")
    expected = _optional_timestamp(
        direct_market.get("expected_expiration_time"), "Kalshi expected expiration"
    )
    latest = _optional_timestamp(
        direct_market.get("latest_expiration_time"), "Kalshi latest expiration"
    )
    status = _string(direct_market.get("status"), "Kalshi status")
    updated = _aware_source_timestamp(direct_market.get("updated_time"), "Kalshi updated")
    del updated
    contradiction: str | None = None
    if close_at >= bea_release:
        contradiction = "KALSHI_CLOSE_NOT_STRICTLY_BEFORE_BEA_RELEASE"
    if (
        expected is not None
        and expected.astimezone(ZoneInfo(TIMEZONE_NAME)).date()
        != bea_release.astimezone(ZoneInfo(TIMEZONE_NAME)).date()
        and contradiction is None
    ):
        contradiction = "KALSHI_EXPECTED_EXPIRATION_DATE_CONTRADICTS_BEA_RELEASE_DATE"
    if "day of the expected release" not in secondary.casefold() and contradiction is None:
        contradiction = "KALSHI_RELEASE_DAY_SEMANTICS_MISSING"
    return {
        "event_ticker": EVENT_TICKER,
        "series_ticker": event.get("series_ticker"),
        "market_ticker": ticker,
        "target_quarter": TARGET_QUARTER,
        "metric_semantics": METRIC_SEMANTICS,
        "settlement_edition": SETTLEMENT_EDITION,
        "bea_release_at": bea_release,
        "kalshi_open_at": open_at,
        "kalshi_close_at": close_at,
        "kalshi_expected_expiration_at": expected,
        "kalshi_latest_expiration_at": latest,
        "market_status": status,
        "rule_text": rules,
    }, contradiction


def _incomplete(
    reason: str,
    event_evidence: SourceEvidence | None = None,
    market_evidence: SourceEvidence | None = None,
    bea_evidence: SourceEvidence | None = None,
) -> ScheduleAuthorityResult:
    return ScheduleAuthorityResult(
        status=AuthorityStatus.EVIDENCE_INCOMPLETE,
        authority=None,
        contradiction=reason,
        event_evidence=event_evidence,
        market_evidence=market_evidence,
        bea_evidence=bea_evidence,
    )


def _issue_authority(
    event_evidence: SourceEvidence, market_evidence: SourceEvidence, bea_evidence: SourceEvidence
) -> ScheduleAuthorityResult:
    try:
        _validate_source_evidence(event_evidence)
        _validate_source_evidence(market_evidence)
        _validate_source_evidence(bea_evidence)
        event_payload = _json_object(event_evidence)
        event = _dict(event_payload.get("event"), "event")
        event_market = _market_from_event(event_payload)
        market_payload = _json_object(market_evidence)
        direct_market = _dict(market_payload.get("market"), "market")
        bea_release = _parse_bea_schedule(bea_evidence)
        values, contradiction = _validate_semantics(
            event=event,
            event_market=event_market,
            direct_market=direct_market,
            bea_release=bea_release,
        )
        if contradiction is not None:
            return _incomplete(contradiction, event_evidence, market_evidence, bea_evidence)
        rule_text = values.pop("rule_text")
        if not isinstance(rule_text, str):
            raise ScheduleAuthorityError("rule evidence is malformed")
        authority_values: dict[str, object] = {
            **values,
            "kalshi_rule_evidence_id": market_evidence.source_identity,
            "kalshi_rule_sha256": hashlib.sha256(rule_text.encode("utf-8")).hexdigest(),
            "kalshi_event_evidence_id": event_evidence.source_identity,
            "kalshi_event_sha256": event_evidence.raw_sha256,
            "kalshi_market_evidence_id": market_evidence.source_identity,
            "kalshi_market_sha256": market_evidence.raw_sha256,
            "bea_schedule_evidence_id": bea_evidence.source_identity,
            "bea_schedule_sha256": bea_evidence.raw_sha256,
            "event_acquired_at": event_evidence.acquired_at,
            "market_acquired_at": market_evidence.acquired_at,
            "bea_acquired_at": bea_evidence.acquired_at,
            "timezone_policy_identity": TIMEZONE_POLICY_IDENTITY,
            "parser_version": PARSER_VERSION,
            "contradiction_result": "NONE",
        }
        authority = ScheduleAuthority(values=authority_values, _capability=_AUTHORITY_ISSUER)
        return ScheduleAuthorityResult(
            status=AuthorityStatus.COMPLETE_AUTHORITY,
            authority=authority,
            contradiction=None,
            event_evidence=event_evidence,
            market_evidence=market_evidence,
            bea_evidence=bea_evidence,
        )
    except ScheduleAuthorityError as exc:
        return _incomplete(str(exc), event_evidence, market_evidence, bea_evidence)


def acquire_schedule_authority() -> ScheduleAuthorityResult:
    """Acquire and parse the fixed one-event KXGDP/BEA schedule authority."""
    event_evidence: SourceEvidence | None = None
    market_evidence: SourceEvidence | None = None
    bea_evidence: SourceEvidence | None = None
    try:
        event_response = _fixed_get(
            KALSHI_HOST, KALSHI_ORIGIN, KALSHI_EVENT_PATH, ("application/json",)
        )
        if event_response.status != SUCCESS_STATUS:
            return _incomplete("KALSHI_EVENT_NON_200")
        event_evidence = SourceEvidence(response=event_response, _capability=_AUTHORITY_ISSUER)
        market_response = _fixed_get(
            KALSHI_HOST, KALSHI_ORIGIN, KALSHI_MARKET_PATH, ("application/json",)
        )
        if market_response.status != SUCCESS_STATUS:
            return _incomplete("KALSHI_MARKET_NON_200", event_evidence)
        market_evidence = SourceEvidence(response=market_response, _capability=_AUTHORITY_ISSUER)
        bea_response = _fixed_get(BEA_HOST, BEA_ORIGIN, BEA_SCHEDULE_PATH, ("text/html",))
        if bea_response.status != SUCCESS_STATUS:
            return _incomplete("BEA_SCHEDULE_NON_200", event_evidence, market_evidence)
        bea_evidence = SourceEvidence(response=bea_response, _capability=_AUTHORITY_ISSUER)
    except ScheduleAuthorityError as exc:
        return _incomplete(str(exc), event_evidence, market_evidence, bea_evidence)
    return _issue_authority(event_evidence, market_evidence, bea_evidence)


__all__ = [
    "AuthorityStatus",
    "ScheduleAuthority",
    "ScheduleAuthorityError",
    "ScheduleAuthorityResult",
    "SourceEvidence",
    "acquire_schedule_authority",
]
