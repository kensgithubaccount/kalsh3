"""Deterministic shadow-only signal stages, provenance, deduplication, and health."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol


class SignalStage(StrEnum):
    LEAD = "lead"
    CORROBORATING = "corroborating"
    CORROBORATED = "corroborated"
    CANDIDATE_OPPORTUNITY = "candidate_opportunity"
    INVALID = "invalid"
    DUPLICATE = "duplicate"
    MANIPULATION_RISK = "manipulation_risk"


class SourceClass(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    SOCIAL = "social"
    MARKET = "market"


@dataclass(frozen=True, slots=True)
class ExternalSignal:
    signal_id: str
    source_id: str
    source_class: SourceClass
    source_event_at: datetime
    source_published_at: datetime
    provider_received_at: datetime | None
    bot_ingested_at: datetime
    content_hash: str
    canonical_locator: str
    summary: str
    stage: SignalStage
    matched_market_tickers: tuple[str, ...]
    production_influence: bool = False

    def __post_init__(self) -> None:
        if self.production_influence:
            raise ValueError("M5 signals are shadow-only")


@dataclass(frozen=True, slots=True)
class SourceHealth:
    source_id: str
    last_success_at: datetime | None
    last_failure_at: datetime | None
    consecutive_failures: int
    latency_ms: int | None
    healthy: bool


class SignalAdapter(Protocol):
    source_id: str

    def fetch(self, since: datetime) -> list[ExternalSignal]: ...


class AdapterKind(StrEnum):
    POLYMARKET = "polymarket"
    PREDICTBUDDY = "predictbuddy"
    RSS = "rss"
    X = "x"
    BLUESKY = "bluesky"
    REDDIT = "reddit_official"
    TELEGRAM = "authorized_telegram"
    DISCORD = "authorized_discord"


class SignalRegistry:
    def __init__(self) -> None:
        self._hashes: set[str] = set()
        self.signals: list[ExternalSignal] = []

    def ingest(self, signal: ExternalSignal) -> ExternalSignal:
        if signal.content_hash in self._hashes:
            duplicate = replace(signal, stage=SignalStage.DUPLICATE)
            self.signals.append(duplicate)
            return duplicate
        self._hashes.add(signal.content_hash)
        self.signals.append(signal)
        return signal

    def health(
        self, source_id: str, now: datetime, stale_after: timedelta = timedelta(minutes=10)
    ) -> SourceHealth:
        matches = [x for x in self.signals if x.source_id == source_id]
        last = max((x.bot_ingested_at for x in matches), default=None)
        latency = (
            None
            if not matches
            else int(
                (matches[-1].bot_ingested_at - matches[-1].source_published_at).total_seconds()
                * 1000
            )
        )
        return SourceHealth(
            source_id, last, None, 0, latency, last is not None and now - last <= stale_after
        )


def content_hash(source_id: str, locator: str, summary: str) -> str:
    return hashlib.sha256(f"{source_id}\0{locator}\0{summary}".encode()).hexdigest()
