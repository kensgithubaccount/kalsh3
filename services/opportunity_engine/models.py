"""Immutable opportunity inputs, economics, diagnostics, candidates, and manifests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from services.historical_replay.archive import stable_hash

from .books import OutcomeSide
from .domain import OpportunityError


class AnalysisType(StrEnum):
    TAKER_NOW = "TAKER_NOW"
    MAKER_AT_BEST = "MAKER_AT_BEST"
    MAKER_IMPROVE_ONE_VALID_TICK = "MAKER_IMPROVE_ONE_VALID_TICK"


class DecisionState(StrEnum):
    REJECTED = "REJECTED"
    INCOMPLETE = "INCOMPLETE"
    WATCH = "WATCH"
    RESEARCH_CANDIDATE = "RESEARCH_CANDIDATE"
    HIGH_PRIORITY_RESEARCH_CANDIDATE = "HIGH_PRIORITY_RESEARCH_CANDIDATE"


class RejectionReason(StrEnum):
    MARKET_INACTIVE = "MARKET_INACTIVE"
    MARKET_PROVISIONAL = "MARKET_PROVISIONAL"
    MARKET_UNSUPPORTED = "MARKET_UNSUPPORTED"
    MVE_UNSUPPORTED = "MVE_UNSUPPORTED"
    PAYOUT_UNSUPPORTED = "PAYOUT_UNSUPPORTED"
    RULES_INVALID = "RULES_INVALID"
    RULES_CHANGED = "RULES_CHANGED"
    CONTRACT_AMBIGUOUS = "CONTRACT_AMBIGUOUS"
    SETTLEMENT_SOURCE_UNKNOWN = "SETTLEMENT_SOURCE_UNKNOWN"
    FORECAST_MISSING = "FORECAST_MISSING"
    FORECAST_STALE = "FORECAST_STALE"
    FORECAST_RULE_VERSION_MISMATCH = "FORECAST_RULE_VERSION_MISMATCH"
    FORECAST_ABSTAINED = "FORECAST_ABSTAINED"
    BOOK_MISSING = "BOOK_MISSING"
    BOOK_STALE = "BOOK_STALE"
    BOOK_CROSSED = "BOOK_CROSSED"
    BOOK_SEQUENCE_INVALID = "BOOK_SEQUENCE_INVALID"
    DEPTH_INSUFFICIENT = "DEPTH_INSUFFICIENT"
    PRICE_STRUCTURE_UNSUPPORTED = "PRICE_STRUCTURE_UNSUPPORTED"
    QUANTITY_STRUCTURE_UNSUPPORTED = "QUANTITY_STRUCTURE_UNSUPPORTED"
    FEE_MODEL_UNAVAILABLE = "FEE_MODEL_UNAVAILABLE"
    FEE_MODEL_UNVERIFIED = "FEE_MODEL_UNVERIFIED"
    FAIR_INTERVAL_TOO_WIDE = "FAIR_INTERVAL_TOO_WIDE"
    INSUFFICIENT_SEPARATION = "INSUFFICIENT_SEPARATION"
    NET_VALUE_BELOW_THRESHOLD = "NET_VALUE_BELOW_THRESHOLD"
    SOURCE_HEALTH_FAILURE = "SOURCE_HEALTH_FAILURE"
    DATA_TEMPORAL_SKEW = "DATA_TEMPORAL_SKEW"
    FILL_MODEL_UNAVAILABLE = "FILL_MODEL_UNAVAILABLE"
    SLIPPAGE_UNCERTAIN = "SLIPPAGE_UNCERTAIN"
    CORRELATION_DUPLICATE = "CORRELATION_DUPLICATE"
    SEMANTIC_MATCH_INSUFFICIENT = "SEMANTIC_MATCH_INSUFFICIENT"
    REFERENCE_OVERLAP = "REFERENCE_OVERLAP"
    TOO_CLOSE_TO_CLOSE = "TOO_CLOSE_TO_CLOSE"
    INFORMATION_TOO_STALE = "INFORMATION_TOO_STALE"
    RESEARCH_EVIDENCE_INSUFFICIENT = "RESEARCH_EVIDENCE_INSUFFICIENT"
    DATA_QUALITY_FAILURE = "DATA_QUALITY_FAILURE"
    AFTER_COST_REPLAY_UNSUPPORTED = "AFTER_COST_REPLAY_UNSUPPORTED"


class FillQuality(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    CONSERVATIVE_HEURISTIC = "CONSERVATIVE_HEURISTIC"
    EMPIRICAL_RESEARCH_MODEL = "EMPIRICAL_RESEARCH_MODEL"
    DISPLAYED_TAKER_DEPTH = "DISPLAYED_TAKER_DEPTH"


class SlippageMethod(StrEnum):
    EXACT_CURRENT_BOOK_WALK = "EXACT_CURRENT_BOOK_WALK"
    EMPIRICAL_MODEL = "EMPIRICAL_MODEL"
    CONSERVATIVE_HEURISTIC = "CONSERVATIVE_HEURISTIC"
    UNKNOWN = "UNKNOWN"


class InformationDecay(StrEnum):
    SLOW = "SLOW"
    MODERATE = "MODERATE"
    FAST = "FAST"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class AsOfOpportunitySnapshot:
    forecast_time: datetime
    kalshi_time: datetime
    external_market_time: datetime | None
    evidence_time: datetime
    replay_time: datetime

    @property
    def maximum_skew(self) -> timedelta:
        times = [self.forecast_time, self.kalshi_time, self.evidence_time]
        if self.external_market_time is not None:
            times.append(self.external_market_time)
        return max(times) - min(times)

    def validate(self, maximum_skew: timedelta) -> None:
        if any(
            time > self.replay_time
            for time in (self.forecast_time, self.kalshi_time, self.evidence_time)
        ):
            raise OpportunityError("future opportunity input")
        if self.maximum_skew > maximum_skew:
            raise OpportunityError("opportunity temporal skew")


@dataclass(frozen=True, slots=True)
class LiquidityDiagnostic:
    absolute_spread: Decimal
    relative_spread: Decimal | None
    top_quantity: Decimal
    depth_within_ticks: Decimal
    recent_trade_count: int
    recent_quantity: Decimal
    open_interest: Decimal
    quote_age_ms: int
    book_stability: str


@dataclass(frozen=True, slots=True)
class OutcomeEconomics:
    outcome_side: OutcomeSide
    fair_probability: Decimal
    conservative_probability: Decimal
    executable_price: Decimal
    gross_probability_difference: Decimal
    gross_expected_value: Decimal
    break_even_probability: Decimal
    expected_fee: Decimal
    expected_slippage: Decimal
    conservative_expected_value: Decimal


@dataclass(frozen=True, slots=True)
class TradeCandidate:
    candidate_id: str
    created_at: datetime
    market_ticker: str
    event_id: str
    series_id: str
    market_family: str
    rules_version: str
    rules_hash: str
    contract_interpretation_version: str
    forecast_id: str
    forecast_kind: str
    forecast_time: datetime
    fair_yes_probability: Decimal
    fair_no_probability: Decimal
    fair_yes_lower: Decimal
    fair_yes_upper: Decimal
    fair_no_lower: Decimal
    fair_no_upper: Decimal
    market_snapshot_id: str
    book_snapshot_time: datetime
    book_age_ms: int
    outcome_side: OutcomeSide
    analysis_type: AnalysisType
    best_bid: Decimal
    best_ask: Decimal
    executable_price: Decimal
    available_size_at_best: Decimal
    available_depth: Decimal
    economics: OutcomeEconomics
    fee_schedule_id: str
    fee_type: str
    fee_multiplier: Decimal
    expected_slippage: Decimal
    slippage_method: SlippageMethod
    fill_probability: Decimal | None
    fill_probability_quality: FillQuality
    uncertainty_adjustment: Decimal
    information_age: timedelta
    information_decay: InformationDecay
    information_decay_estimate: Decimal
    liquidity: LiquidityDiagnostic
    correlation_cluster_id: str | None
    correlation_context: str
    time_to_close: timedelta
    time_to_expected_resolution: timedelta
    capital_turnover_measure: Decimal
    opportunity_score: Decimal
    decision_state: DecisionState
    rejection_reasons: tuple[RejectionReason, ...]
    code_git_sha: str
    content_hash: str
    research_only: bool = True
    production_influence: Decimal = Decimal(0)

    @classmethod
    def freeze(cls, **values: object) -> TradeCandidate:
        if (
            values.get("production_influence", Decimal(0)) != 0
            or values.get("research_only", True) is not True
        ):
            raise OpportunityError("candidate must remain research-only with zero influence")
        digest = stable_hash(tuple(sorted(values.items())))
        return cls(candidate_id=digest, content_hash=digest, **values)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class OpportunityDataset:
    dataset_id: str
    start_at: datetime
    end_at: datetime
    policy_version: str
    forecast_versions: tuple[str, ...]
    model_versions: tuple[str, ...]
    calibrators: tuple[str, ...]
    fee_schedules: tuple[str, ...]
    slippage_model: str
    fill_model: str
    market_data_fidelity: str
    learning_configuration: str
    source_configuration: str
    gap_state: str
    code_sha: str
    content_hash: str

    @classmethod
    def build(cls, **values: object) -> OpportunityDataset:
        digest = stable_hash(tuple(sorted(values.items())))
        return cls(dataset_id=digest, content_hash=digest, **values)  # type: ignore[arg-type]
