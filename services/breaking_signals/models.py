"""Immutable external events and shadow research signals with four-time provenance."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum


class SourceState(StrEnum):
    CANDIDATE = "CANDIDATE"
    SHADOW = "SHADOW"
    ELIGIBLE = "ELIGIBLE"
    LIMITED_PRODUCTION = "LIMITED_PRODUCTION"
    APPROVED = "APPROVED"
    QUARANTINED = "QUARANTINED"
    DISABLED = "DISABLED"
    SETUP_REQUIRED = "SETUP_REQUIRED"


class VerificationState(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    IDENTITY_VERIFIED = "IDENTITY_VERIFIED"
    SOURCE_VERIFIED = "SOURCE_VERIFIED"
    PRIMARY_CONFIRMED = "PRIMARY_CONFIRMED"
    INDEPENDENTLY_CORROBORATED = "INDEPENDENTLY_CORROBORATED"
    CONFLICTING = "CONFLICTING"
    RETRACTED = "RETRACTED"
    DELETED = "DELETED"
    INVALID = "INVALID"


class Originality(StrEnum):
    PRIMARY_ORIGINAL = "PRIMARY_ORIGINAL"
    SECONDARY_ORIGINAL_REPORTING = "SECONDARY_ORIGINAL_REPORTING"
    REPOST = "REPOST"
    SYNDICATED = "SYNDICATED"
    COMMENTARY = "COMMENTARY"
    UNKNOWN = "UNKNOWN"


class ManipulationFlag(StrEnum):
    NEW_ACCOUNT = "NEW_ACCOUNT"
    UNKNOWN_IDENTITY = "UNKNOWN_IDENTITY"
    RAPID_REPOST_CASCADE = "RAPID_REPOST_CASCADE"
    DUPLICATE_LANGUAGE_BURST = "DUPLICATE_LANGUAGE_BURST"
    SINGLE_SOURCE_CASCADE = "SINGLE_SOURCE_CASCADE"
    DELETED_ORIGINAL = "DELETED_ORIGINAL"
    SCREENSHOT_WITHOUT_PROVENANCE = "SCREENSHOT_WITHOUT_PROVENANCE"
    UNSUPPORTED_NUMERIC_CLAIM = "UNSUPPORTED_NUMERIC_CLAIM"
    SUSPICIOUS_TIMESTAMP = "SUSPICIOUS_TIMESTAMP"
    STALE_RECIRCULATION = "STALE_RECIRCULATION"
    CONFLICTING_PRIMARY_SOURCE = "CONFLICTING_PRIMARY_SOURCE"
    VENDOR_ONLY = "VENDOR_ONLY"
    COMMENT_ONLY = "COMMENT_ONLY"
    LOW_INFORMATION_REACTION = "LOW_INFORMATION_REACTION"
    POSSIBLE_BOT_BURST = "POSSIBLE_BOT_BURST"


class SignalStatus(StrEnum):
    INFORMATIONAL_LEAD = "INFORMATIONAL_LEAD"
    CORROBORATING = "CORROBORATING"
    CORROBORATED = "CORROBORATED"
    CANDIDATE_FOR_RESEARCH = "CANDIDATE_FOR_RESEARCH"
    DUPLICATE = "DUPLICATE"
    STALE = "STALE"
    INVALID = "INVALID"
    MANIPULATION_RISK = "MANIPULATION_RISK"
    UNMATCHED = "UNMATCHED"


class Relevance(StrEnum):
    DIRECTLY_RELEVANT = "DIRECTLY_RELEVANT"
    POSSIBLY_RELEVANT = "POSSIBLY_RELEVANT"
    CONTEXT_ONLY = "CONTEXT_ONLY"
    IRRELEVANT = "IRRELEVANT"
    UNKNOWN = "UNKNOWN"


class LeadLag(StrEnum):
    BEFORE_KALSHI_MOVE = "BEFORE_KALSHI_MOVE"
    AFTER_KALSHI_MOVE = "AFTER_KALSHI_MOVE"
    BEFORE_POLYMARKET_MOVE = "BEFORE_POLYMARKET_MOVE"
    AFTER_POLYMARKET_MOVE = "AFTER_POLYMARKET_MOVE"
    SIMULTANEOUS_WITHIN_RESOLUTION = "SIMULTANEOUS_WITHIN_RESOLUTION"
    INDETERMINATE = "INDETERMINATE"


@dataclass(frozen=True, slots=True)
class SourceDefinition:
    source_id: str
    source_name: str
    provider: str
    source_type: str
    source_class: str
    access_method: str
    canonical_domain: str
    owner_organization: str
    market_families: tuple[str, ...]
    licensing_note: str
    credentials_required: bool
    credential_boundary: str | None
    expected_cadence: str
    expected_latency_ms: int | None
    monthly_cost: Decimal
    state: SourceState
    verification_method: str
    retention_days: int
    production_influence: Decimal
    last_success: datetime | None = None
    last_failure: datetime | None = None
    health: str = "NOT_STARTED"

    def __post_init__(self) -> None:
        if (
            self.state
            not in {
                SourceState.CANDIDATE,
                SourceState.SHADOW,
                SourceState.DISABLED,
                SourceState.SETUP_REQUIRED,
                SourceState.QUARANTINED,
            }
            or self.production_influence != 0
        ):
            raise ValueError("M5 sources must have zero production influence")


@dataclass(frozen=True, slots=True)
class ExternalObservation:
    event_id: str
    provider_event_id: str | None
    source_id: str
    source_type: str
    author_stable_id: str | None
    author_display_identity: str | None
    canonical_url: str | None
    original_event_id: str | None
    raw_content_hash: str
    normalized_content_hash: str
    source_event_time: datetime | None
    source_publish_time: datetime | None
    provider_receive_time: datetime | None
    bot_ingest_time: datetime
    receive_monotonic_ns: int
    raw_payload_reference: str
    deletion_state: str
    correction_of_event_id: str | None
    language: str | None
    linked_entities: tuple[str, ...]
    candidate_markets: tuple[str, ...]
    duplicate_of_event_id: str | None
    verification_state: VerificationState
    originality: Originality
    manipulation_flags: tuple[ManipulationFlag, ...]
    parser_version: str

    @property
    def publication_to_ingest_latency(self) -> timedelta | None:
        return (
            None
            if self.source_publish_time is None
            else self.bot_ingest_time - self.source_publish_time
        )

    @property
    def event_to_ingest_latency(self) -> timedelta | None:
        return (
            None
            if self.source_event_time is None
            else self.bot_ingest_time - self.source_event_time
        )


@dataclass(frozen=True, slots=True)
class ExecutableSnapshot:
    venue: str
    market_id: str
    yes_bid: Decimal | None
    yes_ask: Decimal | None
    no_bid: Decimal | None
    no_ask: Decimal | None
    yes_bid_size: Decimal | None
    yes_ask_size: Decimal | None
    observed_at: datetime
    ingested_at: datetime
    fresh: bool


@dataclass(frozen=True, slots=True)
class NumericFact:
    value: Decimal
    unit: str
    original_text: str
    preliminary: bool | None


@dataclass(frozen=True, slots=True)
class SignalObservation:
    signal_id: str
    source_event_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    detected_at: datetime
    candidate_kalshi_markets: tuple[str, ...]
    candidate_polymarket_markets: tuple[str, ...]
    category: str
    entities: tuple[str, ...]
    numeric_facts: tuple[NumericFact, ...]
    verification_state: VerificationState
    independent_source_chains: int
    originality: Originality
    duplicate_count: int
    source_class: str
    fresh: bool
    manipulation_flags: tuple[ManipulationFlag, ...]
    kalshi_at_detection: ExecutableSnapshot | None
    polymarket_at_detection: ExecutableSnapshot | None
    previous_kalshi: ExecutableSnapshot | None
    previous_polymarket: ExecutableSnapshot | None
    status: SignalStatus
    action: str
    relevance: Relevance
    lead_lag: tuple[LeadLag, ...]
    production_influence: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if self.production_influence != 0:
            raise ValueError("M5 signals terminate in shadow research data")


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"\s+", " ", value).strip()
    return value
