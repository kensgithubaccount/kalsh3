"""Immutable research-only execution simulation artifacts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any


class SimulationError(ValueError):
    """Fail-closed simulation input error."""


class SimulationCase(StrEnum):
    OPTIMISTIC = "OPTIMISTIC"
    BASE = "BASE"
    ADVERSE = "ADVERSE"


class StrategyType(StrEnum):
    TAKER_NOW = "TAKER_NOW"
    MAKER_AT_BEST = "MAKER_AT_BEST"
    MAKER_IMPROVE_ONE_TICK = "MAKER_IMPROVE_ONE_TICK"


class QueueQuality(StrEnum):
    OBSERVED_OWN_ORDER_QUEUE = "OBSERVED_OWN_ORDER_QUEUE"
    RECONSTRUCTED_AGGREGATE_QUEUE = "RECONSTRUCTED_AGGREGATE_QUEUE"
    CONSERVATIVE_QUEUE_ASSUMPTION = "CONSERVATIVE_QUEUE_ASSUMPTION"
    UNKNOWN = "UNKNOWN"


class ReplayFidelity(StrEnum):
    SEQUENCE_BOOK_AND_TRADES = "SEQUENCE_BOOK_AND_TRADES"
    HIGH_RESOLUTION_BOOK = "HIGH_RESOLUTION_BOOK"
    CANDLE_ONLY = "CANDLE_ONLY"
    GAP = "GAP"


class OrderState(StrEnum):
    PROPOSED_SIMULATION = "PROPOSED_SIMULATION"
    ARRIVAL_PENDING = "ARRIVAL_PENDING"
    RESTING_SIMULATION = "RESTING_SIMULATION"
    PARTIALLY_FILLED_SIMULATION = "PARTIALLY_FILLED_SIMULATION"
    FILLED_SIMULATION = "FILLED_SIMULATION"
    CANCEL_PENDING_SIMULATION = "CANCEL_PENDING_SIMULATION"
    CANCELED_SIMULATION = "CANCELED_SIMULATION"
    EXPIRED_SIMULATION = "EXPIRED_SIMULATION"
    NO_FILL_SIMULATION = "NO_FILL_SIMULATION"
    INVALIDATED_SIMULATION = "INVALIDATED_SIMULATION"
    EXECUTION_OUTCOME_UNKNOWN = "EXECUTION_OUTCOME_UNKNOWN"


class FillState(StrEnum):
    NO_FILL = "NO_FILL"
    PARTIAL_FILL = "PARTIAL_FILL"
    FULL_FILL = "FULL_FILL"


class AdvancementState(StrEnum):
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"
    PASSES_EXECUTION_RESEARCH_GATE = "PASSES_EXECUTION_RESEARCH_GATE"


def _hash(value: object) -> str:
    def default(item: object) -> object:
        if isinstance(item, (datetime, Decimal, timedelta, StrEnum)):
            return str(item)
        if is_dataclass(item) and not isinstance(item, type):
            return asdict(item)
        raise TypeError

    encoded = json.dumps(value, default=default, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class LatencyPolicy:
    policy_id: str
    version: str
    signal: timedelta
    forecast: timedelta
    opportunity: timedelta
    scheduling: timedelta
    outbound_network: timedelta
    exchange_processing: timedelta
    cancellation: timedelta
    provenance: str
    measured: bool

    @property
    def decision_to_arrival(self) -> timedelta:
        return self.scheduling + self.outbound_network + self.exchange_processing


@dataclass(frozen=True, slots=True)
class ExecutionAssumptionPolicy:
    policy_id: str
    version: str
    scenario: SimulationCase
    effective_at: datetime
    latency: LatencyPolicy
    cancellation_credit: Decimal
    competing_fill_reserve: Decimal
    adverse_selection_reserve: Decimal
    max_rest: timedelta
    required_fidelity: ReplayFidelity
    fee_policy_ids: tuple[str, ...]
    content_hash: str

    @classmethod
    def freeze(cls, **values: Any) -> ExecutionAssumptionPolicy:
        digest = _hash(values)
        return cls(content_hash=digest, **values)

    def __post_init__(self) -> None:
        for value in (
            self.cancellation_credit,
            self.competing_fill_reserve,
            self.adverse_selection_reserve,
        ):
            if not value.is_finite() or not Decimal(0) <= value <= Decimal(1):
                raise SimulationError("scenario assumption outside [0,1]")


@dataclass(frozen=True, slots=True)
class SimulatedFill:
    simulated_fill_id: str
    simulated_order_id: str
    timestamp: datetime
    outcome_side: str
    book_side: str
    price: Decimal
    quantity: Decimal
    maker: bool
    queue_ahead_before: Decimal | None
    causing_event_id: str
    fee: Decimal
    cumulative_fill: Decimal
    remaining_quantity: Decimal
    book_lineage: str
    replay_sequence: int
    scenario: SimulationCase

    def __post_init__(self) -> None:
        monetary = (
            self.price,
            self.quantity,
            self.fee,
            self.cumulative_fill,
            self.remaining_quantity,
        )
        if any(not item.is_finite() or item < 0 for item in monetary):
            raise SimulationError("invalid fill arithmetic")


@dataclass(frozen=True, slots=True)
class SimulatedOrder:
    simulated_order_id: str
    frozen_candidate_id: str
    strategy: StrategyType
    scenario: SimulationCase
    candidate_time: datetime
    decision_time: datetime
    submit_time: datetime
    arrival_time: datetime
    initial_quantity: Decimal
    remaining_quantity: Decimal
    limit_price: Decimal | None
    state: OrderState
    queue_quality: QueueQuality
    queue_ahead: Decimal | None
    fills: tuple[SimulatedFill, ...]
    cancel_requested_at: datetime | None = None
    cancel_effective_at: datetime | None = None
    invalidation_reason: str | None = None
    production_influence: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if self.production_influence != 0:
            raise SimulationError("simulation production influence must remain zero")
        if not self.candidate_time <= self.decision_time <= self.submit_time <= self.arrival_time:
            raise SimulationError("simulation timestamps are not chronological")
        if (
            self.cancel_requested_at
            and self.cancel_effective_at
            and self.cancel_effective_at < self.cancel_requested_at
        ):
            raise SimulationError("cancel effective before request")
        total = sum((fill.quantity for fill in self.fills), Decimal(0))
        if (
            total > self.initial_quantity
            or total + self.remaining_quantity != self.initial_quantity
        ):
            raise SimulationError("fill quantity accounting invalid")
        if any(fill.timestamp < self.arrival_time for fill in self.fills):
            raise SimulationError("fill before simulated arrival")
        if self.queue_quality == QueueQuality.OBSERVED_OWN_ORDER_QUEUE:
            raise SimulationError("hypothetical orders cannot claim observed own-order queue")


@dataclass(frozen=True, slots=True)
class MarkoutObservation:
    simulated_fill_id: str
    horizon: timedelta
    reference_time: datetime
    market_reference: Decimal
    executable_unwind: Decimal | None
    normalized_markout: Decimal


@dataclass(frozen=True, slots=True)
class SimulatedOutcome:
    case: SimulationCase
    filled_quantity: Decimal
    average_price: Decimal | None
    fees: Decimal
    adverse_selection: Decimal
    information_decay: Decimal
    after_cost_value: Decimal | None
    fill_state: FillState = FillState.NO_FILL


@dataclass(frozen=True, slots=True)
class AdvancementEvidence:
    unique_settled_events: int
    effective_sample_size: Decimal
    max_drawdown: Decimal
    max_drawdown_limit: Decimal
    best_event_contribution: Decimal
    concentration_limit: Decimal
    stable_subwindows: bool
    replay_adequate: bool
    critical_failure: bool
    minimum_events: int = 50


@dataclass(frozen=True, slots=True)
class CandidateSimulation:
    candidate_id: str
    outcomes: tuple[SimulatedOutcome, ...]
    research_advancement: str
    production_influence: Decimal = Decimal(0)

    @classmethod
    def assess(
        cls,
        candidate_id: str,
        outcomes: tuple[SimulatedOutcome, ...],
        evidence: AdvancementEvidence | None = None,
    ) -> CandidateSimulation:
        by_case = {outcome.case: outcome for outcome in outcomes}
        if set(by_case) != set(SimulationCase):
            state = AdvancementState.INCONCLUSIVE
        else:
            base, adverse = by_case[SimulationCase.BASE], by_case[SimulationCase.ADVERSE]
            economics_survive = (
                base.after_cost_value is not None
                and adverse.after_cost_value is not None
                and base.after_cost_value > 0
                and adverse.after_cost_value > 0
            )
            if evidence is None:
                state = (
                    AdvancementState.FAIL
                    if not economics_survive
                    else AdvancementState.INCONCLUSIVE
                )
            else:
                evidence_passes = (
                    evidence.unique_settled_events >= evidence.minimum_events
                    and evidence.effective_sample_size >= evidence.minimum_events
                    and evidence.max_drawdown <= evidence.max_drawdown_limit
                    and evidence.best_event_contribution <= evidence.concentration_limit
                    and evidence.stable_subwindows
                    and evidence.replay_adequate
                    and not evidence.critical_failure
                )
                state = (
                    AdvancementState.PASSES_EXECUTION_RESEARCH_GATE
                    if economics_survive and evidence_passes
                    else AdvancementState.FAIL
                )
        return cls(candidate_id, outcomes, state, Decimal(0))


@dataclass(frozen=True, slots=True)
class BacktestRun:
    run_id: str
    opportunity_dataset_id: str
    candidate_policy: str
    strategy_policy: str
    execution_policy_versions: tuple[str, ...]
    replay_dataset: str
    start_at: datetime
    end_at: datetime
    markets: tuple[str, ...]
    events: tuple[str, ...]
    code_sha: str
    source_config: str
    model_config: str
    learning_config: str
    started_at: datetime
    completed_at: datetime
    content_hash: str

    @classmethod
    def freeze(cls, **values: Any) -> BacktestRun:
        digest = _hash(values)
        return cls(run_id=digest, content_hash=digest, **values)


@dataclass(frozen=True, slots=True)
class ExecutionDatasetManifest:
    replay_dataset: str
    forecast_versions: tuple[str, ...]
    learning_configuration: str
    opportunity_dataset: str
    execution_policies: tuple[str, ...]
    latency_policy: str
    fee_policies: tuple[str, ...]
    queue_model: str
    fill_model: str
    cancellation_policy: str
    code_sha: str
    start_at: datetime
    end_at: datetime
    gap_policy: str
    content_hash: str

    @classmethod
    def freeze(cls, **values: Any) -> ExecutionDatasetManifest:
        digest = _hash(values)
        return cls(content_hash=digest, **values)

    def as_manifest(self) -> dict[str, object]:
        return asdict(self)
