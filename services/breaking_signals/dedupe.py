"""Conservative multi-level deduplication, lineage, recirculation and corroboration."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import ExternalObservation, ManipulationFlag, VerificationState, normalize_text


@dataclass(frozen=True, slots=True)
class DuplicateDecision:
    duplicate_of: str | None
    reason: str | None
    normalized_hash: str
    near_duplicate: bool


class DedupeIndex:
    def __init__(self) -> None:
        self.events: dict[str, ExternalObservation] = {}
        self.provider_ids: dict[tuple[str, str], str] = {}
        self.urls: dict[str, str] = {}
        self.raw_hashes: dict[str, str] = {}
        self.normalized_hashes: dict[str, str] = {}

    def classify(self, event: ExternalObservation, text: str) -> DuplicateDecision:
        normalized = normalize_text(text)
        digest = hashlib.sha256(normalized.encode()).hexdigest()
        checks = []
        if event.provider_event_id:
            checks.append(
                (
                    self.provider_ids.get((event.source_id, event.provider_event_id)),
                    "provider_event_id",
                )
            )
        if event.canonical_url:
            checks.append((self.urls.get(event.canonical_url), "canonical_url"))
        checks.extend(
            (
                (self.raw_hashes.get(event.raw_content_hash), "raw_hash"),
                (self.normalized_hashes.get(digest), "normalized_hash"),
            )
        )
        for duplicate, reason in checks:
            if duplicate:
                return DuplicateDecision(duplicate, reason, digest, False)
        # Numeric tokens must match exactly before conservative near-duplicate classification.
        numbers = tuple(re.findall(r"[-+]?\d+(?:\.\d+)?%?", normalized))
        tokens = set(normalized.split())
        for prior_id, prior in self.events.items():
            prior_text = prior.raw_payload_reference
            prior_norm = normalize_text(prior_text)
            prior_numbers = tuple(re.findall(r"[-+]?\d+(?:\.\d+)?%?", prior_norm))
            if numbers != prior_numbers:
                continue
            other = set(prior_norm.split())
            similarity = len(tokens & other) / max(1, len(tokens | other))
            if similarity >= 0.9 and event.source_id == prior.source_id:
                return DuplicateDecision(prior_id, "conservative_near_duplicate", digest, True)
        return DuplicateDecision(None, None, digest, False)

    def add(self, event: ExternalObservation, text: str) -> DuplicateDecision:
        decision = self.classify(event, text)
        self.events[event.event_id] = event
        if event.provider_event_id:
            self.provider_ids.setdefault((event.source_id, event.provider_event_id), event.event_id)
        if event.canonical_url:
            self.urls.setdefault(event.canonical_url, event.event_id)
        self.raw_hashes.setdefault(event.raw_content_hash, event.event_id)
        self.normalized_hashes.setdefault(decision.normalized_hash, event.event_id)
        return decision

    def recirculation_flags(
        self, event: ExternalObservation, now: datetime, max_age: timedelta = timedelta(hours=24)
    ) -> tuple[ManipulationFlag, ...]:
        published = event.source_publish_time
        return (
            (ManipulationFlag.STALE_RECIRCULATION,)
            if published is not None and now - published > max_age
            else ()
        )


def independent_chains(events: list[ExternalObservation]) -> int:
    roots = {
        event.original_event_id or event.event_id
        for event in events
        if event.verification_state
        not in {VerificationState.DELETED, VerificationState.RETRACTED, VerificationState.INVALID}
    }
    return len(roots)
