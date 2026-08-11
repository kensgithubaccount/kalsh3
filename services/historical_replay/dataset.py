"""Replay fidelity, gaps, labels, vintages, and content-addressed datasets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from .archive import stable_hash
from .domain import Availability, ReplayError


class ReplayCapability(StrEnum):
    FULL_ORDERBOOK_EVENTS = "FULL_ORDERBOOK_EVENTS"
    ORDERBOOK_SNAPSHOTS = "ORDERBOOK_SNAPSHOTS"
    TRADE_EVENTS = "TRADE_EVENTS"
    TOP_OF_BOOK = "TOP_OF_BOOK"
    ONE_MINUTE_CANDLES = "ONE_MINUTE_CANDLES"
    HOURLY_CANDLES = "HOURLY_CANDLES"
    DAILY_CANDLES = "DAILY_CANDLES"
    SETTLEMENT_ONLY = "SETTLEMENT_ONLY"


class RulesQuality(StrEnum):
    OBSERVED_VERSIONED = "OBSERVED_VERSIONED"
    AUTHORITATIVE_ARCHIVE = "AUTHORITATIVE_ARCHIVE"
    FINAL_RULES_ONLY = "FINAL_RULES_ONLY"
    UNKNOWN = "UNKNOWN"


class FeeQuality(StrEnum):
    OBSERVED_VERSIONED = "OBSERVED_VERSIONED"
    AUTHORITATIVE_EFFECTIVE_DATE = "AUTHORITATIVE_EFFECTIVE_DATE"
    CURRENT_ONLY = "CURRENT_ONLY"
    UNKNOWN = "UNKNOWN"


class GapSeverity(StrEnum):
    CRITICAL = "CRITICAL"
    NONCRITICAL = "NONCRITICAL"


@dataclass(frozen=True, slots=True)
class ReplayGap:
    gap_id: str
    gap_type: str
    start_at: datetime
    end_at: datetime | None
    severity: GapSeverity
    detail: str


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    dataset_id: str
    created_at: datetime
    start_at: datetime
    end_at: datetime
    markets: tuple[str, ...]
    providers: tuple[str, ...]
    archive_manifests: tuple[str, ...]
    cutoff_observations: tuple[str, ...]
    rules_quality: tuple[tuple[str, int], ...]
    fee_quality: tuple[tuple[str, int], ...]
    availability_basis: tuple[tuple[str, int], ...]
    capabilities: frozenset[ReplayCapability]
    gaps: tuple[ReplayGap, ...]
    excluded_records: int
    parser_versions: tuple[str, ...]
    code_git_sha: str
    content_hash: str

    @classmethod
    def build(
        cls,
        *,
        created_at: datetime,
        start_at: datetime,
        end_at: datetime,
        markets: tuple[str, ...],
        providers: tuple[str, ...],
        archive_manifests: tuple[str, ...],
        cutoff_observations: tuple[str, ...],
        rules_quality: dict[str, int],
        fee_quality: dict[str, int],
        availability_basis: dict[str, int],
        capabilities: set[ReplayCapability],
        gaps: tuple[ReplayGap, ...],
        excluded_records: int,
        parser_versions: tuple[str, ...],
        code_git_sha: str,
    ) -> DatasetManifest:
        material = (
            start_at,
            end_at,
            sorted(markets),
            sorted(providers),
            sorted(archive_manifests),
            sorted(cutoff_observations),
            sorted(rules_quality.items()),
            sorted(fee_quality.items()),
            sorted(availability_basis.items()),
            sorted(capabilities),
            gaps,
            excluded_records,
            sorted(parser_versions),
            code_git_sha,
        )
        digest = stable_hash(material)
        return cls(
            digest,
            created_at,
            start_at,
            end_at,
            tuple(sorted(markets)),
            tuple(sorted(providers)),
            tuple(sorted(archive_manifests)),
            tuple(sorted(cutoff_observations)),
            tuple(sorted(rules_quality.items())),
            tuple(sorted(fee_quality.items())),
            tuple(sorted(availability_basis.items())),
            frozenset(capabilities),
            gaps,
            excluded_records,
            tuple(sorted(parser_versions)),
            code_git_sha,
            digest,
        )

    def validate(self, required: ReplayCapability, tolerate_noncritical: bool = True) -> None:
        if required not in self.capabilities:
            raise ReplayError(f"required replay fidelity unavailable: {required}")
        if any(g.severity == GapSeverity.CRITICAL for g in self.gaps):
            raise ReplayError("critical replay gap")
        if not tolerate_noncritical and self.gaps:
            raise ReplayError("strategy does not tolerate replay gaps")


class SettlementState(StrEnum):
    DETERMINED = "DETERMINED"
    DISPUTED = "DISPUTED"
    AMENDED = "AMENDED"
    FINALIZED = "FINALIZED"


@dataclass(frozen=True, slots=True)
class SettlementLabel:
    label_id: str
    market_ticker: str
    rules_version: str
    result: str
    settlement_value: Decimal
    settlement_at: datetime
    finalized_at: datetime | None
    state: SettlementState
    raw_manifest_id: str
    retrieved_at: datetime
    supersedes_label_id: str | None = None

    @property
    def training_eligible(self) -> bool:
        return self.state == SettlementState.FINALIZED and self.finalized_at is not None


@dataclass(frozen=True, slots=True)
class VintageValue:
    series_id: str
    value: Decimal
    vintage_at: datetime
    availability: Availability
    revision_of: str | None = None

    def visible(self, replay_at: datetime) -> bool:
        available = self.availability.replay_available_at
        return available is not None and available <= replay_at
