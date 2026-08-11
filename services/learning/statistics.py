"""Conservative multiple-comparison and transparent source-quality diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


def benjamini_hochberg(p_values: tuple[tuple[str, Decimal], ...], alpha: Decimal) -> frozenset[str]:
    ordered = sorted(p_values, key=lambda item: item[1])
    accepted: set[str] = set()
    threshold_index = 0
    count = Decimal(len(ordered) or 1)
    for index, (_identifier, value) in enumerate(ordered, 1):
        if value <= alpha * Decimal(index) / count:
            threshold_index = index
    for identifier, _ in ordered[:threshold_index]:
        accepted.add(identifier)
    return frozenset(accepted)


@dataclass(frozen=True, slots=True)
class SourceQuality:
    stable_source_id: str
    uptime: Decimal
    missingness: Decimal
    parse_failure: Decimal
    revision_rate: Decimal
    correction_rate: Decimal
    retraction_rate: Decimal
    median_latency_ms: Decimal
    duplication_rate: Decimal
    originality_rate: Decimal
    independent_source_fraction: Decimal
    monthly_cost: Decimal
    required_for_contract_truth: bool
    target_type: str
    coverage_denominator: int
    eligible_observations: int
    used_observations: int
    missing_observations: int
    inclusion_reason: str

    def incremental_brier_per_dollar(self, contribution: Decimal) -> Decimal | None:
        return None if self.monthly_cost == 0 else contribution / self.monthly_cost
