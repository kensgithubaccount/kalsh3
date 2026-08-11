"""Immutable contract versions, invalidation, and sibling-bin structural validation."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise

from .specification import Comparator, ContractSpecification, SemanticStatus


class RelationshipStatus(StrEnum):
    COMPLETE = "COMPLETE"
    GAP = "GAP"
    OVERLAP = "OVERLAP"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True, slots=True)
class StructuralRelationship:
    event_ticker: str
    market_tickers: tuple[str, ...]
    status: RelationshipStatus
    detail: str


@dataclass(slots=True)
class ContractRegistry:
    versions: dict[str, list[ContractSpecification]] = field(default_factory=dict)
    invalidations: list[tuple[str, str, datetime]] = field(default_factory=list)

    def add(self, spec: ContractSpecification) -> ContractSpecification:
        history = self.versions.setdefault(spec.market_ticker, [])
        if history and history[-1].semantic_hash == spec.semantic_hash:
            return history[-1]
        if history:
            old = history[-1]
            if old.semantic_status not in {SemanticStatus.STALE, SemanticStatus.INVALIDATED}:
                history[-1] = replace(old, semantic_status=SemanticStatus.STALE)
            spec = replace(spec, supersedes_spec_id=old.contract_spec_id)
        history.append(spec)
        return spec

    def invalidate(self, ticker: str, cause: str, now: datetime) -> None:
        history = self.versions.get(ticker)
        if not history:
            return
        history[-1] = history[-1].invalidate(cause, now)
        self.invalidations.append((ticker, cause, now))

    def current(self, ticker: str) -> ContractSpecification | None:
        history = self.versions.get(ticker, [])
        return history[-1] if history else None


def validate_bins(event_ticker: str, specs: list[ContractSpecification]) -> StructuralRelationship:
    if not specs:
        return StructuralRelationship(
            event_ticker, (), RelationshipStatus.UNSUPPORTED, "No sibling specifications"
        )
    intervals = []
    for spec in specs:
        if (
            spec.comparator == Comparator.BETWEEN
            and spec.lower_bound is not None
            and spec.upper_bound is not None
        ):
            intervals.append((spec.lower_bound, spec.upper_bound, spec.market_ticker))
        elif spec.comparator == Comparator.LT and spec.threshold_value is not None:
            intervals.append((Decimal("-Infinity"), spec.threshold_value, spec.market_ticker))
        elif spec.comparator == Comparator.GTE and spec.threshold_value is not None:
            intervals.append((spec.threshold_value, Decimal("Infinity"), spec.market_ticker))
        else:
            return StructuralRelationship(
                event_ticker,
                tuple(x.market_ticker for x in specs),
                RelationshipStatus.UNSUPPORTED,
                "Comparator set cannot be proven exhaustive",
            )
    intervals.sort()
    status = RelationshipStatus.COMPLETE
    detail = "Sibling bins are contiguous"
    for left, right in pairwise(intervals):
        if left[1] < right[0]:
            status, detail = RelationshipStatus.GAP, f"Gap between {left[2]} and {right[2]}"
            break
        if left[1] > right[0]:
            status, detail = RelationshipStatus.OVERLAP, f"Overlap between {left[2]} and {right[2]}"
            break
    return StructuralRelationship(event_ticker, tuple(x[2] for x in intervals), status, detail)
