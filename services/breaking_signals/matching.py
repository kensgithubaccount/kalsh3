"""Conservative deterministic cross-venue semantic matching and price observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from services.contract_intelligence.specification import Comparator, PayoutModel

from .models import ExecutableSnapshot


class MatchClass(StrEnum):
    EXACT_DETERMINISTIC_MATCH = "EXACT_DETERMINISTIC_MATCH"
    STRONG_CANDIDATE = "STRONG_CANDIDATE"
    RELATED_ONLY = "RELATED_ONLY"
    SEMANTIC_CONFLICT = "SEMANTIC_CONFLICT"
    INCOMPATIBLE = "INCOMPATIBLE"
    UNMATCHED = "UNMATCHED"


@dataclass(frozen=True, slots=True)
class MatchSemantics:
    market_id: str
    entities: tuple[str, ...]
    event_type: str
    outcome_meaning: str
    comparator: Comparator
    threshold: Decimal | None
    date: datetime | None
    timezone: str | None
    geography: str | None
    authority: str | None
    cancellation_rules: str | None
    revision_rules: str | None
    recount_rules: str | None
    payout_model: PayoutModel
    date_relation: str = "on"


@dataclass(frozen=True, slots=True)
class MatchResult:
    classification: MatchClass
    matched_fields: tuple[str, ...]
    conflicts: tuple[str, ...]


def match(left: MatchSemantics, right: MatchSemantics) -> MatchResult:
    fields = (
        "entities",
        "event_type",
        "outcome_meaning",
        "comparator",
        "threshold",
        "date",
        "timezone",
        "geography",
        "authority",
        "cancellation_rules",
        "revision_rules",
        "recount_rules",
        "payout_model",
        "date_relation",
    )
    matched = []
    conflicts = []
    for field in fields:
        a, b = getattr(left, field), getattr(right, field)
        if a is None or b is None:
            continue
        if a == b:
            matched.append(field)
        else:
            conflicts.append(field)
    if conflicts:
        return MatchResult(
            MatchClass.INCOMPATIBLE
            if "payout_model" in conflicts or "entities" in conflicts
            else MatchClass.SEMANTIC_CONFLICT,
            tuple(matched),
            tuple(conflicts),
        )
    required = {
        "entities",
        "event_type",
        "outcome_meaning",
        "comparator",
        "date",
        "timezone",
        "authority",
        "payout_model",
    }
    if required.issubset(matched):
        return MatchResult(MatchClass.EXACT_DETERMINISTIC_MATCH, tuple(matched), ())
    return MatchResult(
        MatchClass.STRONG_CANDIDATE
        if len(matched) >= 6
        else MatchClass.RELATED_ONLY
        if matched
        else MatchClass.UNMATCHED,
        tuple(matched),
        (),
    )


@dataclass(frozen=True, slots=True)
class CrossVenuePriceObservation:
    match_class: MatchClass
    kalshi: ExecutableSnapshot
    polymarket: ExecutableSnapshot
    yes_ask_difference: Decimal | None
    freshness_difference_ms: int
    source_lag_ms: int
    observation_type: str = "CROSS_VENUE_DISCREPANCY_OBSERVATION"

    @classmethod
    def create(
        cls, match_class: MatchClass, kalshi: ExecutableSnapshot, polymarket: ExecutableSnapshot
    ) -> CrossVenuePriceObservation:
        difference = (
            None
            if kalshi.yes_ask is None or polymarket.yes_ask is None
            else polymarket.yes_ask - kalshi.yes_ask
        )
        freshness = int((polymarket.observed_at - kalshi.observed_at).total_seconds() * 1000)
        lag = int((polymarket.ingested_at - kalshi.ingested_at).total_seconds() * 1000)
        return cls(match_class, kalshi, polymarket, difference, freshness, lag)
