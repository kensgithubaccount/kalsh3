"""Reviewed offline BLS CPI publication-timing issuer for CPI-E1-P3.

Only exact P1-authorized archived CPI HTML artifacts may enter this module. The
publication instant is reconstructed from the artifact's own official embargo
statement; callers cannot supply timing fields or timing-evidence identity.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

import services.forecasting.cpi_pit_availability as pit
from services.forecasting.cpi_source_authority import (
    CPISourceInterface,
    resolve_cpi_source_authority,
)
from services.market_universe.domain import stable_hash

POLICY_VERSION = "cpi-e1-p3-reviewed-bls-publication-timing-v1"
TIMEZONE_NAME = "America/New_York"
NEW_YORK = ZoneInfo(TIMEZONE_NAME)
_TEXT_NORMALIZATION_SCHEMA = "cpi-e1-p3-html-visible-text-v1"
_TIMING_EVIDENCE_SCHEMA = "cpi-e1-p3-publication-timing-evidence-v1"

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
    """Exact archived release bytes do not establish one reviewed publication instant."""


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
        raise CPIPublicationTimingError("archived CPI HTML could not be normalized exactly") from exc
    text = parser.normalized_text()
    if not text:
        raise CPIPublicationTimingError("archived CPI HTML contains no visible timing evidence")
    return text


def _locator_date(locator: str) -> date:
    match = _LOCATOR_RE.fullmatch(locator)
    if match is None:
        raise CPIPublicationTimingError("CPI artifact locator is not the reviewed archived HTML shape")
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
        raise CPIPublicationTimingError("BLS embargo statement names an impossible New York local time")
    token = timezone_token.upper()
    if token == "ET":
        if len(candidates) != 1:
            raise CPIPublicationTimingError("generic ET embargo statement is locally ambiguous")
        return candidates[0]
    required_offset = {"EST": timedelta(hours=-5), "EDT": timedelta(hours=-4)}.get(token)
    if required_offset is None:
        raise CPIPublicationTimingError("BLS embargo statement timezone is unsupported")
    matching = tuple(value for value in candidates if value.utcoffset() == required_offset)
    if len(matching) != 1:
        raise CPIPublicationTimingError(
            f"{token} embargo token conflicts with America/New_York on the stated date"
        )
    return matching[0]


def _timing_evidence_identity(
    artifact: pit.CPIHistoricalReleaseArtifact,
    *,
    matched_statement: str,
    local_date: date,
    local_time: time,
    timezone_token: str,
    source_publish_at: datetime,
) -> str:
    statement_hash = sha256(matched_statement.encode("utf-8")).hexdigest()
    return stable_hash(
        (
            POLICY_VERSION,
            _TEXT_NORMALIZATION_SCHEMA,
            _TIMING_EVIDENCE_SCHEMA,
            artifact.artifact_id,
            artifact.raw_artifact_sha256,
            statement_hash,
            local_date.isoformat(),
            local_time.isoformat(timespec="minutes"),
            timezone_token,
            source_publish_at.isoformat(),
            artifact.p1_authority_identity,
            artifact.p1_policy_identity,
        )
    )


def issue_cpi_publication_evidence(
    artifact: pit.CPIHistoricalReleaseArtifact,
) -> pit.CPIActualPublicationEvidence:
    """Issue exact P2 publication evidence from the artifact's own embargo statement."""
    pit.validate_cpi_release_artifact(artifact)
    authority = resolve_cpi_source_authority(
        profile=artifact.profile,
        role=artifact.source_role,
        locator=artifact.source_locator,
    )
    if authority.source_interface is not CPISourceInterface.BLS_ARCHIVED_CPI_NEWS_RELEASE_HTML:
        raise CPIPublicationTimingError("only P1-authorized archived CPI HTML is timing authority")
    if (
        authority.authority_identity != artifact.p1_authority_identity
        or authority.policy_identity != artifact.p1_policy_identity
    ):
        raise CPIPublicationTimingError("CPI artifact P1 identities changed before timing issuance")

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
    source_publish_at = _publication_instant(local_date, local_clock, timezone_token)
    timing_identity = _timing_evidence_identity(
        artifact,
        matched_statement=matched_statement,
        local_date=local_date,
        local_time=local_clock,
        timezone_token=timezone_token,
        source_publish_at=source_publish_at,
    )
    evidence = pit._issue_actual_cpi_publication_evidence(
        artifact=artifact,
        source_publish_at=source_publish_at,
        timing_semantics=pit.CPIPublicationTimingSemantics.ACTUAL_RELEASE_OR_EMBARGO,
        timing_evidence_identity=timing_identity,
        _capability=pit._PUBLICATION_AUTHORITY_CAPABILITY,
    )
    pit.validate_cpi_publication_evidence(evidence)
    return evidence
