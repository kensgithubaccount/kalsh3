"""Reviewed offline BLS CPI publication-timing parser for CPI-E1-P3.

Only exact P1-authorized archived CPI HTML artifacts may enter this module. The
publication instant is parsed deterministically from the artifact's own official
embargo statement. Parsed timing is structural research data only: byte identity
and a reviewed locator do not establish acquisition provenance or publication
authority.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from hashlib import sha256
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

from services.forecasting.cpi_pit_availability import (
    CPIHistoricalReleaseArtifact,
    validate_cpi_release_artifact,
)
from services.forecasting.cpi_source_authority import (
    CPISourceInterface,
    CPISourceProfile,
    CPISourceRole,
    resolve_cpi_source_authority,
)
from services.market_universe.domain import stable_hash

PARSER_POLICY_VERSION = "cpi-e1-p3-reviewed-bls-publication-timing-parser-v1"
TIMEZONE_NAME = "America/New_York"
NEW_YORK = ZoneInfo(TIMEZONE_NAME)
ZERO = Decimal("0")
_TEXT_NORMALIZATION_SCHEMA = "cpi-e1-p3-html-visible-text-v1"
_PARSED_TIMING_SCHEMA = "cpi-e1-p3-parsed-publication-timing-v1"

_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_LOCATOR_RE = re.compile(
    r"\Ahttps://www\.bls\.gov/news\.release/archives/cpi_"
    r"(?P<month>[0-9]{2})(?P<day>[0-9]{2})(?P<year>[0-9]{4})\.htm\Z"
)
_EMBARGO_RE = re.compile(
    r"Transmission\s+of\s+material\s+in\s+this\s+release\s+is\s+embargoed\s+until\s+"
    r"(?P<hour>1[0-2]|[1-9]):(?P<minute>[0-5][0-9])\s+"
    r"(?P<meridiem>a\.m\.|p\.m\.)\s+"
    r"\((?P<timezone>EST|EDT|ET)\)\s*,?\s*"
    r"(?:(?P<weekday>Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s*,?\s*)?"
    r"(?P<month>January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+"
    r"(?P<day>[0-9]{1,2})\s*,\s*(?P<year>[0-9]{4})",
    re.IGNORECASE,
)


class CPIPublicationTimingError(ValueError):
    """Exact archived release bytes do not parse to one reviewed publication instant."""


@dataclass(frozen=True, slots=True)
class ParsedCPIPublicationTiming:
    """Non-authoritative parsed timing observation bound to one structural artifact."""

    profile: CPISourceProfile
    source_role: CPISourceRole
    source_locator: str
    source_artifact_id: str
    raw_artifact_sha256: str
    p1_authority_identity: str
    p1_policy_identity: str
    matched_statement: str
    local_release_date: date
    local_release_time: time
    source_timezone_token: str
    publication_instant: datetime
    observation_identity: str
    parser_policy_version: str = field(init=False, default=PARSER_POLICY_VERSION)
    parser_schema_version: str = field(init=False, default=_PARSED_TIMING_SCHEMA)
    text_normalization_schema: str = field(init=False, default=_TEXT_NORMALIZATION_SCHEMA)
    research_only: bool = field(init=False, default=True)
    production_influence: Decimal = field(init=False, default=ZERO)


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._ignored_depth = 0
        self._seen_html = False
        self._seen_body = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        normalized = tag.casefold()
        if normalized == "html":
            self._seen_html = True
        elif normalized == "body":
            self._seen_body = True
        if normalized in {"script", "style"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"script", "style"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self._parts.append(data)

    def normalized_text(self) -> str:
        if not self._seen_html or not self._seen_body:
            raise CPIPublicationTimingError("archived CPI bytes are not the reviewed HTML shape")
        return " ".join(" ".join(self._parts).split())


def _normalized_visible_text(raw_artifact: bytes) -> str:
    if type(raw_artifact) is not bytes or not raw_artifact:
        raise CPIPublicationTimingError("exact non-empty archived HTML bytes are required")
    parser = _VisibleTextParser()
    try:
        parser.feed(raw_artifact.decode("latin-1"))
        parser.close()
    except Exception as exc:
        raise CPIPublicationTimingError(
            "archived CPI HTML could not be normalized exactly"
        ) from exc
    text = parser.normalized_text()
    if not text:
        raise CPIPublicationTimingError("archived CPI HTML contains no visible timing evidence")
    return text


def _locator_date(locator: str) -> date:
    match = _LOCATOR_RE.fullmatch(locator)
    if match is None:
        raise CPIPublicationTimingError(
            "CPI artifact locator is not the reviewed archived HTML shape"
        )
    try:
        return date(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
    except ValueError as exc:
        raise CPIPublicationTimingError("CPI archive locator date is invalid") from exc


def _local_date(match: re.Match[str]) -> date:
    month = _MONTHS[match.group("month").casefold()]
    try:
        parsed = date(int(match.group("year")), month, int(match.group("day")))
    except ValueError as exc:
        raise CPIPublicationTimingError("BLS embargo statement date is malformed") from exc
    weekday = match.group("weekday")
    if weekday is not None and parsed.strftime("%A").casefold() != weekday.casefold():
        raise CPIPublicationTimingError("BLS embargo statement weekday conflicts with its date")
    return parsed


def _local_time(match: re.Match[str]) -> time:
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    meridiem = match.group("meridiem").casefold()
    if meridiem == "a.m.":
        hour = 0 if hour == 12 else hour
    elif meridiem == "p.m.":
        hour = hour if hour == 12 else hour + 12
    else:  # pragma: no cover - regex makes this unreachable
        raise CPIPublicationTimingError("BLS embargo statement time is malformed")
    return time(hour, minute)


def _valid_new_york_candidates(local_date: date, local_time: time) -> tuple[datetime, ...]:
    naive = datetime.combine(local_date, local_time)
    by_utc: dict[datetime, datetime] = {}
    for fold in (0, 1):
        candidate = naive.replace(tzinfo=NEW_YORK, fold=fold)
        utc_value = candidate.astimezone(UTC)
        roundtrip = utc_value.astimezone(NEW_YORK)
        if roundtrip.replace(tzinfo=None) == naive:
            by_utc[utc_value] = candidate
    return tuple(by_utc[key] for key in sorted(by_utc))


def _publication_instant(local_date: date, local_time: time, timezone_token: str) -> datetime:
    candidates = _valid_new_york_candidates(local_date, local_time)
    if not candidates:
        raise CPIPublicationTimingError(
            "BLS embargo statement names an impossible New York local time"
        )
    timezone_abbreviation = timezone_token.upper()
    if timezone_abbreviation == "ET":
        if len(candidates) != 1:
            raise CPIPublicationTimingError("generic ET embargo statement is locally ambiguous")
        return candidates[0]
    required_offset = {
        "EST": timedelta(hours=-5),
        "EDT": timedelta(hours=-4),
    }.get(timezone_abbreviation)
    if required_offset is None:
        raise CPIPublicationTimingError("BLS embargo statement timezone is unsupported")
    matching = tuple(value for value in candidates if value.utcoffset() == required_offset)
    if len(matching) != 1:
        raise CPIPublicationTimingError(
            f"{timezone_abbreviation} embargo token conflicts with America/New_York "
            "on the stated date"
        )
    return matching[0]


def _observation_identity(
    artifact: CPIHistoricalReleaseArtifact,
    *,
    matched_statement: str,
    local_date: date,
    local_time: time,
    timezone_token: str,
    publication_instant: datetime,
) -> str:
    statement_hash = sha256(matched_statement.encode("utf-8")).hexdigest()
    return stable_hash(
        (
            PARSER_POLICY_VERSION,
            _TEXT_NORMALIZATION_SCHEMA,
            _PARSED_TIMING_SCHEMA,
            artifact.artifact_id,
            artifact.raw_artifact_sha256,
            statement_hash,
            local_date.isoformat(),
            local_time.isoformat(timespec="minutes"),
            timezone_token,
            publication_instant.isoformat(),
            artifact.p1_authority_identity,
            artifact.p1_policy_identity,
        )
    )


def parse_cpi_publication_timing(
    artifact: CPIHistoricalReleaseArtifact,
) -> ParsedCPIPublicationTiming:
    """Parse one non-authoritative timing observation from reviewed-shape CPI HTML."""
    validate_cpi_release_artifact(artifact)
    authority = resolve_cpi_source_authority(
        profile=artifact.profile,
        role=artifact.source_role,
        locator=artifact.source_locator,
    )
    if authority.source_interface is not CPISourceInterface.BLS_ARCHIVED_CPI_NEWS_RELEASE_HTML:
        raise CPIPublicationTimingError("only P1-authorized archived CPI HTML may be parsed")
    if (
        authority.authority_identity != artifact.p1_authority_identity
        or authority.policy_identity != artifact.p1_policy_identity
    ):
        raise CPIPublicationTimingError("CPI artifact P1 identities changed before timing parsing")

    text = _normalized_visible_text(artifact.raw_artifact)
    matches = tuple(_EMBARGO_RE.finditer(text))
    if not matches:
        raise CPIPublicationTimingError(
            "archived CPI release does not contain one complete reviewed embargo statement"
        )
    statements = {match.group(0) for match in matches}
    if len(statements) != 1:
        raise CPIPublicationTimingError("archived CPI release contains ambiguous timing statements")
    match = matches[0]
    matched_statement = match.group(0)
    local_date = _local_date(match)
    local_clock = _local_time(match)
    timezone_token = match.group("timezone").upper()
    if local_date != _locator_date(artifact.source_locator):
        raise CPIPublicationTimingError(
            "BLS embargo statement date conflicts with the authorized archive locator date"
        )
    publication_instant = _publication_instant(local_date, local_clock, timezone_token)
    observation_identity = _observation_identity(
        artifact,
        matched_statement=matched_statement,
        local_date=local_date,
        local_time=local_clock,
        timezone_token=timezone_token,
        publication_instant=publication_instant,
    )
    return ParsedCPIPublicationTiming(
        profile=artifact.profile,
        source_role=artifact.source_role,
        source_locator=artifact.source_locator,
        source_artifact_id=artifact.artifact_id,
        raw_artifact_sha256=artifact.raw_artifact_sha256,
        p1_authority_identity=artifact.p1_authority_identity,
        p1_policy_identity=artifact.p1_policy_identity,
        matched_statement=matched_statement,
        local_release_date=local_date,
        local_release_time=local_clock,
        source_timezone_token=timezone_token,
        publication_instant=publication_instant,
        observation_identity=observation_identity,
    )
