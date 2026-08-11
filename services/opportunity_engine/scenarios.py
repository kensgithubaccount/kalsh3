"""Hypothetical maker/taker research scenarios without order plans."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from .models import AnalysisType, FillQuality, InformationDecay


class AdverseSelectionQuality(StrEnum):
    UNKNOWN = "UNKNOWN"
    CONSERVATIVE_HEURISTIC = "CONSERVATIVE_HEURISTIC"
    EMPIRICAL = "EMPIRICAL"


@dataclass(frozen=True, slots=True)
class HypotheticalScenario:
    analysis_type: AnalysisType
    hypothetical_price: Decimal
    fee_regime: str
    fill_probability: Decimal | None
    fill_quality: FillQuality
    queue_ahead: Decimal | None
    expected_time_to_fill_seconds: Decimal | None
    adverse_selection_reserve: Decimal | None
    adverse_selection_quality: AdverseSelectionQuality
    information_decay: InformationDecay
    ev_conditional_on_fill: Decimal | None
    ev_expected_over_attempt: Decimal | None

    @classmethod
    def maker(
        cls,
        *,
        price: Decimal,
        fee_regime: str,
        conservative_fill_probability: Decimal | None,
        adverse_selection_reserve: Decimal | None,
        information_decay_factor: Decimal,
    ) -> HypotheticalScenario:
        # Missing empirical inputs stay unavailable; never default fill to one.
        conditional = None if adverse_selection_reserve is None else -adverse_selection_reserve
        attempt = (
            None
            if conditional is None or conservative_fill_probability is None
            else conditional * conservative_fill_probability * information_decay_factor
        )
        return cls(
            AnalysisType.MAKER_AT_BEST,
            price,
            fee_regime,
            conservative_fill_probability,
            FillQuality.UNAVAILABLE
            if conservative_fill_probability is None
            else FillQuality.CONSERVATIVE_HEURISTIC,
            None,
            None,
            adverse_selection_reserve,
            AdverseSelectionQuality.UNKNOWN
            if adverse_selection_reserve is None
            else AdverseSelectionQuality.CONSERVATIVE_HEURISTIC,
            InformationDecay.UNKNOWN,
            conditional,
            attempt,
        )
