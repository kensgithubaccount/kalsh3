"""Pure RT parsing and point-in-time transition reconstruction."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from html.parser import HTMLParser
from typing import Any


class RtEligibility(StrEnum):
    CONFIRMED = "CONFIRMED"
    PLAUSIBLE = "PLAUSIBLE"
    UNKNOWN = "UNKNOWN"
    REJECTED = "REJECTED"


class Classification(StrEnum):
    FRESH_CONFIRMED = "FRESH_CONFIRMED"
    ROTTEN_CONFIRMED = "ROTTEN_CONFIRMED"
    FRESH_INFERRED = "FRESH_INFERRED"
    ROTTEN_INFERRED = "ROTTEN_INFERRED"
    AMBIGUOUS = "AMBIGUOUS"


class RtInclusion(StrEnum):
    NOT_YET_OBSERVED = "NOT_YET_OBSERVED"
    INCLUDED = "INCLUDED"
    REMOVED_OR_CHANGED = "REMOVED_OR_CHANGED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ReviewRecord:
    review_id: str
    critic: str | None
    outlet: str | None
    source_url: str | None
    classification: Classification
    eligibility: RtEligibility
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ExternalReviewEvidence:
    """One public source acquisition; first availability means our observation time only."""

    review_id: str
    source_url: str
    first_observed_at: datetime
    raw_body: bytes
    raw_body_sha256: str
    critic: str | None
    outlet: str | None
    title: str | None
    rating_or_grade: str | None
    stated_publication_timestamp: str | None


@dataclass(frozen=True, slots=True)
class RtSnapshot:
    film_url: str
    observed_at: datetime
    raw_body_sha256: str
    raw_body: bytes
    score: int | None
    review_count: int | None
    reviews: tuple[ReviewRecord, ...]
    parse_status: str = "SUCCESS"


@dataclass(frozen=True, slots=True)
class Transition:
    film_url: str
    previous_observed_at: datetime
    observed_at: datetime
    previous_score: int | None
    previous_count: int | None
    new_score: int | None
    new_count: int | None
    newly_included: tuple[str, ...]
    removed_or_changed: tuple[str, ...]
    unexplained_count_change: bool


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _score(text: str) -> int | None:
    matches = re.findall(
        r"(?:tomatometer|tomatometer score|tomatometer-score)"
        r"[^%\d]{0,50}(\d{1,3})\s*%",
        text,
        re.I,
    )
    if not matches:
        matches = re.findall(r"\b(\d{1,3})\s*%\s*(?:tomatometer|fresh)", text, re.I)
    values = {int(item) for item in matches if int(item) <= 100}
    return next(iter(values)) if len(values) == 1 else None


def _count(text: str) -> int | None:
    matches = re.findall(r"(?:total reviews|critic reviews|reviews)\D{0,20}(\d{1,5})", text, re.I)
    values = {int(item) for item in matches}
    return next(iter(values)) if len(values) == 1 else None


def _records(body: bytes) -> tuple[ReviewRecord, ...]:
    text = body.decode("utf-8", errors="replace")
    result: dict[str, ReviewRecord] = {}
    # RT has changed markup repeatedly. These conservative anchors only accept
    # identifiers and URLs explicitly exposed by the page; unknown records stay absent.
    patterns = re.findall(
        r'(?:reviewId|review-id|review_id)["\'=:\s]+([A-Za-z0-9_.:-]{1,200}).{0,1800}?(https?://[^"\'<>\s]+)?',
        text,
        re.I | re.S,
    )
    for identifier, url in patterns:
        window = text[max(0, text.find(identifier) - 200) : text.find(identifier) + 1000]
        fresh = re.search(r"\bFresh\b", window, re.I)
        rotten = re.search(r"\bRotten\b", window, re.I)
        if fresh and not rotten:
            classification = Classification.FRESH_CONFIRMED
        elif rotten and not fresh:
            classification = Classification.ROTTEN_CONFIRMED
        else:
            classification = Classification.AMBIGUOUS
        result[identifier] = ReviewRecord(
            identifier,
            None,
            None,
            url or None,
            classification,
            RtEligibility.UNKNOWN,
            {"window": window},
        )
    return tuple(result.values())


def parse_rt_html(film_url: str, body: bytes, *, observed_at: datetime) -> RtSnapshot:
    """Parse only explicitly displayed score/count/review evidence from one HTML snapshot."""
    if observed_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    timestamp = observed_at.astimezone(UTC)
    digest = hashlib.sha256(body).hexdigest()
    parser = _Text()
    parser.feed(body.decode("utf-8", errors="replace"))
    text = " ".join(parser.parts)
    return RtSnapshot(film_url, timestamp, digest, body, _score(text), _count(text), _records(body))


def parse_external_review_html(
    review_id: str, source_url: str, body: bytes, *, first_observed_at: datetime
) -> ExternalReviewEvidence:
    """Capture source evidence without treating its printed date as availability proof."""
    if not review_id or not source_url.startswith("https://"):
        raise ValueError("external review identity and HTTPS source URL are required")
    if first_observed_at.tzinfo is None:
        raise ValueError("first_observed_at must be timezone-aware")
    text = body.decode("utf-8", errors="replace")
    return ExternalReviewEvidence(
        review_id=review_id,
        source_url=source_url,
        first_observed_at=first_observed_at.astimezone(UTC),
        raw_body=body,
        raw_body_sha256=hashlib.sha256(body).hexdigest(),
        critic=_metadata(text, "critic"),
        outlet=_metadata(text, "outlet|publication|publisher"),
        title=_metadata(text, "title|headline"),
        rating_or_grade=_metadata(text, "rating|grade|score"),
        stated_publication_timestamp=_metadata(text, "datePublished|publicationDate|published"),
    )


def _metadata(text: str, keys: str) -> str | None:
    meta = re.search(
        rf"name\s*=\s*[\"'](?:{keys})[\"'][^>]*"
        rf"content\s*=\s*[\"']([^\"']{{1,300}})",
        text,
        re.I,
    )
    if meta:
        return meta.group(1).strip()
    match = re.search(
        rf"[\"'](?:{keys})[\"']\s*:\s*[\"']?([^\"'<>;,}}\s]{{1,300}})",
        text,
        re.I,
    )
    return match.group(1).strip() if match else None


def reconstruct_transition(previous: RtSnapshot, current: RtSnapshot) -> Transition:
    if current.observed_at <= previous.observed_at:
        raise ValueError("RT snapshots must be strictly chronological")
    before = {record.review_id: record for record in previous.reviews}
    after = {record.review_id: record for record in current.reviews}
    new = tuple(identifier for identifier in after if identifier not in before)
    changed = tuple(
        identifier
        for identifier in before.keys() & after.keys()
        if before[identifier].classification != after[identifier].classification
    ) + tuple(identifier for identifier in before if identifier not in after)
    membership_delta = len(new) - sum(1 for identifier in before if identifier not in after)
    count_delta = (
        None
        if previous.review_count is None or current.review_count is None
        else current.review_count - previous.review_count
    )
    unexplained = count_delta is not None and count_delta != membership_delta
    return Transition(
        current.film_url,
        previous.observed_at,
        current.observed_at,
        previous.score,
        previous.review_count,
        current.score,
        current.review_count,
        new,
        changed,
        unexplained,
    )


def first_inclusion(previous: RtSnapshot | None, current: RtSnapshot) -> tuple[dict[str, Any], ...]:
    """Return bounded first-inclusion events; exact inclusion time is never inferred."""
    prior = {record.review_id for record in previous.reviews} if previous else set()
    absent_at = previous.observed_at if previous else None
    return tuple(
        {
            "film_url": current.film_url,
            "review_id": record.review_id,
            "last_snapshot_where_absent": previous.raw_body_sha256 if previous else None,
            "first_snapshot_where_present": current.raw_body_sha256,
            "last_absent_observed_at": absent_at.isoformat() if absent_at else None,
            "first_present_observed_at": current.observed_at.isoformat(),
            "interval": (
                absent_at.isoformat() if absent_at else None,
                current.observed_at.isoformat(),
            ),
            "score_before": previous.score if previous else None,
            "count_before": previous.review_count if previous else None,
            "score_after": current.score,
            "count_after": current.review_count,
            "classification": record.classification.value,
            "raw_snapshot_identities": [
                x
                for x in (previous.raw_body_sha256 if previous else None, current.raw_body_sha256)
                if x
            ],
        }
        for record in current.reviews
        if record.review_id not in prior
    )
