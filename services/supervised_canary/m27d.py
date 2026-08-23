"""M27D supervised, unvalidated-settlement-proxy canary selection.

This module is deliberately a pure boundary. It accepts already reviewed
M27C weather and M27A live-economics evidence and has no transport, signer, or
order submission capability.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from services.forecasting.weather_probability import (
    CLAIM_TYPE,
    SETTLEMENT_MAPPING_STATUS,
    CurrentWeatherForecastEvidence,
    PhysicalTemperatureProxyProbability,
)
from services.forecasting.weather_prospective import (
    FAMILY,
    FROZEN_MODEL_IDENTITIES,
    GHCND_STATION,
    SOURCE,
    STATION,
)
from services.opportunity_engine.books import OutcomeSide
from services.opportunity_engine.live_economics import MarketEconomicsEvidence, TakerCost

CANARY_STATUS = "SUPERVISED_UNVALIDATED_SETTLEMENT_PROXY_EXPERIMENT"
POLICY_VERSION = "m27d-august-taker-now-20pp-v1"
MIN_RESEARCH_DISCREPANCY = Decimal("0.20")
ONE_CONTRACT = Decimal("1.00")
AUGUST_START = date(2026, 8, 18)
AUGUST_END = date(2026, 8, 31)
MAX_BOOK_AGE = timedelta(seconds=30)
MAX_FORECAST_AGE = timedelta(minutes=30)
ACKNOWLEDGEMENT = (
    "I UNDERSTAND THE SETTLEMENT PROXY IS UNVALIDATED AND APPROVE THIS ONE "
    "REAL-MONEY WEATHER CANARY"
)


class CandidateState(StrEnum):
    QUALIFYING_EXPERIMENTAL_CANARY = "QUALIFYING_EXPERIMENTAL_CANARY"
    ABSTAIN = "ABSTAIN"


@dataclass(frozen=True, slots=True)
class ExperimentalCanaryEligibility:
    status: str
    weather_result_identity: str
    model_identity: str
    claim_type: str
    settlement_mapping_status: str
    source_family: str
    forecast_evidence_identity: str
    contract_identity: str
    target_date: date
    exact_midpoint_seconds: int
    market_evidence_identity: str
    selection_policy_identity: str
    created_at: datetime
    expires_at: datetime
    research_warning: str
    human_approval_required: bool = True

    def __post_init__(self) -> None:
        if self.status != CANARY_STATUS or not self.human_approval_required:
            raise ValueError("M27D status must remain supervised and experimental")
        if self.expires_at <= self.created_at:
            raise ValueError("eligibility expiry must be after creation")


@dataclass(frozen=True, slots=True)
class ExperimentalCandidate:
    candidate_id: str
    eligibility: ExperimentalCanaryEligibility
    market_ticker: str
    event_ticker: str
    series_ticker: str
    predicate: str
    selected_side: OutcomeSide
    executable_price: Decimal
    available_quantity: Decimal
    maximum_fee: Decimal
    maximum_commitment: Decimal
    maximum_loss: Decimal
    all_in_break_even_probability: Decimal
    research_probability_discrepancy: Decimal
    ranking: tuple[Decimal, int, str]
    economics_evidence_identity: str
    truth_warning: str


@dataclass(frozen=True, slots=True)
class ExperimentalApprovalBinding:
    candidate_id: str
    acknowledgement: str
    approval_hash: str

    @classmethod
    def create(cls, candidate_id: str, acknowledgement: str) -> ExperimentalApprovalBinding:
        validate_experimental_acknowledgement(acknowledgement)
        material = json.dumps((candidate_id, acknowledgement), separators=(",", ":")).encode()
        return cls(candidate_id, acknowledgement, hashlib.sha256(material).hexdigest())


@dataclass(frozen=True, slots=True)
class CandidateResult:
    state: CandidateState
    reason: str
    candidates: tuple[ExperimentalCandidate, ...] = ()
    selected: ExperimentalCandidate | None = None


def _abstain(reason: str) -> CandidateResult:
    return CandidateResult(CandidateState.ABSTAIN, reason)


def _validate_weather(
    probability: PhysicalTemperatureProxyProbability,
    forecast: CurrentWeatherForecastEvidence,
    now: datetime,
) -> str | None:
    if probability.claim_type != CLAIM_TYPE:
        return "claim type is not the frozen GHCN physical-temperature proxy"
    if probability.settlement_mapping_status != SETTLEMENT_MAPPING_STATUS:
        return "settlement mapping is not explicitly unvalidated GHCND proxy"
    identity = probability.model_identity
    if identity not in FROZEN_MODEL_IDENTITIES.values():
        return "model identity is not one of the three frozen identities"
    if forecast.family_identity != FAMILY or forecast.settlement_product_id != SOURCE:
        return "forecast family or source mismatch"
    if forecast.nws_station_id != STATION or forecast.ghcnd_station_id != GHCND_STATION:
        return "forecast station provenance mismatch"
    if forecast.exact_midpoint_seconds not in FROZEN_MODEL_IDENTITIES:
        return "unsupported forecast horizon"
    if forecast.exact_midpoint_seconds != probability.exact_midpoint_seconds:
        return "weather horizon mismatch"
    if forecast.local_target_date < AUGUST_START or forecast.local_target_date > AUGUST_END:
        return "target date is outside the August-only canary window"
    if forecast.research_only is not True or probability.research_only is not True:
        return "weather evidence is not research-only"
    if now - forecast.forecast_reference_time > MAX_FORECAST_AGE:
        return "forecast evidence is stale"
    return None


def _candidate_identity_material(
    eligibility: ExperimentalCanaryEligibility,
    side: OutcomeSide,
    price: Decimal,
    discrepancy: Decimal,
    economics_evidence_identity: str,
) -> tuple[object, ...]:
    """Immutable candidate identity, deliberately excluding selection/expiry timestamps.

    ``created_at`` and ``expires_at`` are freshness properties of one observation of the
    opportunity. Including them in ``candidate_id`` made the same unchanged evidence become a
    different identity every time M27D was re-evaluated. Later safety phases need to re-check
    freshness without replacing the candidate whose authenticated reads were collected.
    """

    return (
        eligibility.status,
        eligibility.weather_result_identity,
        eligibility.model_identity,
        eligibility.claim_type,
        eligibility.settlement_mapping_status,
        eligibility.source_family,
        eligibility.forecast_evidence_identity,
        eligibility.contract_identity,
        eligibility.target_date.isoformat(),
        eligibility.exact_midpoint_seconds,
        eligibility.market_evidence_identity,
        eligibility.selection_policy_identity,
        eligibility.research_warning,
        eligibility.human_approval_required,
        side.value,
        str(price),
        str(discrepancy),
        economics_evidence_identity,
    )


def _candidate(
    probability: PhysicalTemperatureProxyProbability,
    forecast: CurrentWeatherForecastEvidence,
    economics: MarketEconomicsEvidence,
    now: datetime,
) -> ExperimentalCandidate | None:
    weather_error = _validate_weather(probability, forecast, now)
    if weather_error:
        return None
    if economics.requested_quantity != ONE_CONTRACT:
        return None
    if economics.analysis_type != "TAKER_NOW" or economics.research_only is not True:
        return None
    if now - economics.orderbook_observed_at > MAX_BOOK_AGE:
        return None
    route_key = (economics.market_ticker, economics.event_ticker, economics.series_ticker)
    if route_key != (
        probability.market_ticker,
        probability.event_ticker,
        probability.series_ticker,
    ):
        return None
    if probability.diagnostic == "EMPIRICAL_BOUNDARY_MASS" or probability.probability in {
        Decimal(0),
        Decimal(1),
    }:
        return None
    sides = (
        (OutcomeSide.YES, economics.yes, probability.probability),
        (OutcomeSide.NO, economics.no, Decimal(1) - probability.probability),
    )
    eligible: list[tuple[OutcomeSide, TakerCost, Decimal, Decimal]] = []
    for side, cost, research_probability in sides:
        if cost is None:
            continue
        fee = cost.centicent_rounded_fee
        break_even = cost.depth.total_cost + fee
        discrepancy = research_probability - break_even
        if discrepancy >= MIN_RESEARCH_DISCREPANCY:
            eligible.append((side, cost, break_even, discrepancy))
    if not eligible:
        return None
    side, cost, break_even, discrepancy = max(eligible, key=lambda row: row[3])
    if cost is None:
        return None
    price = cost.depth.worst_price
    if price is None or cost.depth.filled < ONE_CONTRACT:
        return None
    contract_identity = hashlib.sha256(
        json.dumps(
            (economics.market_ticker, economics.event_ticker, economics.market_rules_hash),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    warning = (
        "THIS IS A REAL-MONEY EXPERIMENT. THE WEATHER MODEL PREDICTS A GHCN "
        "PHYSICAL-TEMPERATURE PROXY. THE WEATHER COMPANY SETTLEMENT MAPPING "
        "IS UNVALIDATED. THIS IS NOT A VALIDATED KALSHI SETTLEMENT PROBABILITY "
        "OR PROVEN EDGE."
    )
    eligibility = ExperimentalCanaryEligibility(
        CANARY_STATUS,
        probability.result_identity,
        probability.model_identity,
        CLAIM_TYPE,
        SETTLEMENT_MAPPING_STATUS,
        FAMILY,
        forecast.evidence_identity,
        contract_identity,
        forecast.local_target_date,
        probability.exact_midpoint_seconds,
        economics.evidence_id,
        POLICY_VERSION,
        now,
        min(forecast.interval_end, now + timedelta(minutes=2)),
        warning,
    )
    material = _candidate_identity_material(
        eligibility,
        side,
        price,
        discrepancy,
        economics.evidence_id,
    )
    candidate_id = hashlib.sha256(repr(material).encode()).hexdigest()
    return ExperimentalCandidate(
        candidate_id,
        eligibility,
        economics.market_ticker,
        economics.event_ticker,
        economics.series_ticker,
        contract_identity,
        side,
        price,
        cost.depth.filled,
        cost.centicent_rounded_fee,
        break_even,
        break_even,
        break_even,
        discrepancy,
        (discrepancy, probability.exact_midpoint_seconds, economics.market_ticker),
        economics.evidence_id,
        warning,
    )


def select_experimental_candidate(
    inputs: Iterable[
        tuple[
            PhysicalTemperatureProxyProbability,
            CurrentWeatherForecastEvidence,
            MarketEconomicsEvidence,
        ]
    ],
    *,
    now: datetime,
) -> CandidateResult:
    if now.tzinfo is None or now.utcoffset() is None:
        return _abstain("candidate time must be timezone-aware")
    candidates = tuple(
        candidate
        for probability, forecast, economics in inputs
        if (candidate := _candidate(probability, forecast, economics, now)) is not None
    )
    if not candidates:
        return _abstain("no supported current market satisfies the frozen policy")
    selected = sorted(
        candidates,
        key=lambda item: (
            -item.research_probability_discrepancy,
            item.eligibility.exact_midpoint_seconds,
            item.market_ticker,
        ),
    )[0]
    return CandidateResult(
        CandidateState.QUALIFYING_EXPERIMENTAL_CANARY, "policy threshold met", candidates, selected
    )


def validate_experimental_acknowledgement(value: str) -> None:
    if value != ACKNOWLEDGEMENT:
        raise PermissionError("explicit unvalidated-settlement-proxy acknowledgement required")


def final_experimental_gates(
    candidate: ExperimentalCandidate,
    *,
    now: datetime,
    forecast_evidence_identity: str,
    market_evidence_identity: str,
    exact_price: Decimal,
    rules_identity: str,
    current_rules_identity: str,
    global_submission_count: int,
    unresolved_canary: bool,
    unknown_order: bool,
    current_position: Decimal,
    acknowledgement: str,
    approval_acknowledgement_hash: str | None = None,
) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    if now.date() > AUGUST_END:
        reasons.append("canary_window_expired")
    if forecast_evidence_identity != candidate.eligibility.forecast_evidence_identity:
        reasons.append("forecast_changed")
    if market_evidence_identity != candidate.economics_evidence_identity:
        reasons.append("market_economics_changed")
    if exact_price != candidate.executable_price:
        reasons.append("price_changed")
    if rules_identity != current_rules_identity:
        reasons.append("rules_changed")
    if candidate.research_probability_discrepancy < MIN_RESEARCH_DISCREPANCY:
        reasons.append("threshold_no_longer_met")
    if global_submission_count != 0:
        reasons.append("global_one_order_limit_used")
    if unresolved_canary:
        reasons.append("unresolved_canary")
    if unknown_order:
        reasons.append("unknown_order")
    if current_position != 0:
        reasons.append("averaging_down_or_existing_position")
    try:
        validate_experimental_acknowledgement(acknowledgement)
    except PermissionError:
        reasons.append("unvalidated_proxy_acknowledgement_missing")
    expected_ack_hash = (
        ExperimentalApprovalBinding.create(candidate.candidate_id, acknowledgement).approval_hash
        if acknowledgement == ACKNOWLEDGEMENT
        else ""
    )
    if approval_acknowledgement_hash != expected_ack_hash:
        reasons.append("acknowledgement_not_bound_to_approval_hash")
    return not reasons, tuple(sorted(reasons))
