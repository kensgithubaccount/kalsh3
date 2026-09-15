"""WN-A1 raw 64-member WeatherNext daily-high probability -- no smoothing, no calibration.

Each of the 64 WeatherNext members votes independently: reconstruct its hourly valid
times, take its maximum temperature over the authoritative settlement-day window, convert
Kelvin to Fahrenheit deterministically, and evaluate the exact Kalshi range/threshold
predicate. ``probability = matching_members / 64``. Nothing here fits, calibrates, mixes
members spatially, or substitutes the ensemble mean for a member vote.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from .wn_a1_current_daily_high_authority import CurrentDailyHighContract
from .wn_a1_domain import PRODUCTION_INFLUENCE, RESEARCH_ONLY, WnA1Error
from .wn_a1_weathernext_evidence import MEMBER_COUNT, WeatherNextEnsembleEvidence

_KELVIN_ZERO_CELSIUS = Decimal("273.15")


def kelvin_to_fahrenheit(kelvin: Decimal) -> Decimal:
    return (kelvin - _KELVIN_ZERO_CELSIUS) * Decimal(9) / Decimal(5) + Decimal(32)


@dataclass(frozen=True, slots=True)
class MemberDailyHigh:
    sample: int
    daily_high_f: Decimal
    hours_used: int


def compute_member_daily_highs(
    evidence: WeatherNextEnsembleEvidence, window_start: datetime, window_end: datetime
) -> tuple[MemberDailyHigh, ...]:
    """Member-by-member maximum over exactly ``[window_start, window_end)``."""
    if window_end <= window_start:
        raise WnA1Error("settlement-day window must be non-empty and forward")
    by_sample: dict[int, list[Decimal]] = {i: [] for i in range(MEMBER_COUNT)}
    for row in evidence.members:
        if window_start <= row.valid_time < window_end:
            by_sample[row.sample].append(kelvin_to_fahrenheit(row.value_kelvin))
    missing = [sample for sample, values in by_sample.items() if not values]
    if missing:
        raise WnA1Error(
            f"members {missing} have zero retained hourly values inside the settlement window"
        )
    return tuple(
        MemberDailyHigh(sample=i, daily_high_f=max(by_sample[i]), hours_used=len(by_sample[i]))
        for i in range(MEMBER_COUNT)
    )


def evaluate_predicate(value: Decimal, contract: CurrentDailyHighContract) -> bool:
    if contract.comparator == "RANGE":
        if contract.upper is None:
            raise WnA1Error("range predicate requires an upper bound")
        return contract.lower <= value <= contract.upper
    if contract.comparator == "GT":
        return value > contract.lower
    if contract.comparator == "LT":
        return value < contract.lower
    raise WnA1Error(f"unsupported comparator: {contract.comparator!r}")


@dataclass(frozen=True, slots=True)
class RawProbabilityResult:
    numerator: int
    denominator: int
    probability: Decimal
    members: tuple[MemberDailyHigh, ...]
    min_f: Decimal
    median_f: Decimal
    max_f: Decimal
    research_only: bool = RESEARCH_ONLY
    production_influence: Decimal = PRODUCTION_INFLUENCE


def compute_raw_probability(
    members: tuple[MemberDailyHigh, ...], contract: CurrentDailyHighContract
) -> RawProbabilityResult:
    if len(members) != MEMBER_COUNT or {m.sample for m in members} != set(range(MEMBER_COUNT)):
        raise WnA1Error("raw probability requires exactly the 64 reviewed member samples")
    numerator = sum(1 for m in members if evaluate_predicate(m.daily_high_f, contract))
    values = sorted(m.daily_high_f for m in members)
    mid = MEMBER_COUNT // 2
    median = (values[mid - 1] + values[mid]) / 2
    return RawProbabilityResult(
        numerator=numerator,
        denominator=MEMBER_COUNT,
        probability=Decimal(numerator) / Decimal(MEMBER_COUNT),
        members=members,
        min_f=values[0],
        median_f=median,
        max_f=values[-1],
    )


class StressDirection(StrEnum):
    PLUS_ONE_F = "PLUS_ONE_F"
    MINUS_ONE_F = "MINUS_ONE_F"


@dataclass(frozen=True, slots=True)
class BoundaryRiskDiagnostic:
    members_within_one_f_of_boundary: int
    baseline_preferred_ticker: str
    plus_one_preferred_ticker: str
    minus_one_preferred_ticker: str

    @property
    def plus_one_reverses(self) -> bool:
        return self.plus_one_preferred_ticker != self.baseline_preferred_ticker

    @property
    def minus_one_reverses(self) -> bool:
        return self.minus_one_preferred_ticker != self.baseline_preferred_ticker

    @property
    def reverses(self) -> bool:
        return self.plus_one_reverses or self.minus_one_reverses


def _sibling_probability(
    members: tuple[MemberDailyHigh, ...], contract: CurrentDailyHighContract, shift: Decimal
) -> int:
    return sum(1 for m in members if evaluate_predicate(m.daily_high_f + shift, contract))


def _preferred_ticker(
    members: tuple[MemberDailyHigh, ...],
    sibling_contracts: Mapping[str, CurrentDailyHighContract],
    shift: Decimal,
) -> str:
    return max(
        sibling_contracts,
        key=lambda ticker: (
            _sibling_probability(members, sibling_contracts[ticker], shift),
            ticker,
        ),
    )


def boundary_risk(
    members: tuple[MemberDailyHigh, ...],
    sibling_contracts: Mapping[str, CurrentDailyHighContract],
    candidate_ticker: str,
) -> BoundaryRiskDiagnostic:
    """Diagnose ±1°F stress: near-boundary member count and preferred-contract reversal.

    ``sibling_contracts`` must contain every mutually-exclusive strike market in the same
    event as ``candidate_ticker``, so "preferred contract" reflects the real argmax across
    the whole event, not just whether this one contract crosses 50%.
    """
    if candidate_ticker not in sibling_contracts:
        raise WnA1Error("candidate ticker missing from sibling contract set")
    candidate = sibling_contracts[candidate_ticker]
    boundaries = [b for b in (candidate.lower, candidate.upper) if b is not None]
    within = sum(
        1 for m in members if any(abs(m.daily_high_f - b) <= Decimal(1) for b in boundaries)
    )
    return BoundaryRiskDiagnostic(
        members_within_one_f_of_boundary=within,
        baseline_preferred_ticker=_preferred_ticker(members, sibling_contracts, Decimal(0)),
        plus_one_preferred_ticker=_preferred_ticker(members, sibling_contracts, Decimal(1)),
        minus_one_preferred_ticker=_preferred_ticker(members, sibling_contracts, Decimal(-1)),
    )
