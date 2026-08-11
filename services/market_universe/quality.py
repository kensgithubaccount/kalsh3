"""Deterministic data quality and research-routing taxonomy; never eligibility or alpha."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum

from .domain import Market
from .pricing import NormalizedBook


class QualityReason(StrEnum):
    MISSING_QUOTE = "MISSING_QUOTE"
    CROSSED_BOOK = "CROSSED_BOOK"
    EMPTY_BOOK = "EMPTY_BOOK"
    STALE_BOOK = "STALE_BOOK"
    WIDE_SPREAD = "WIDE_SPREAD"
    MISSING_TOP_SIZE = "MISSING_TOP_SIZE"
    UNSUPPORTED_PRICE_STRUCTURE = "UNSUPPORTED_PRICE_STRUCTURE"
    PROVISIONAL = "PROVISIONAL"
    MVE_UNSUPPORTED = "MVE_UNSUPPORTED"
    MISSING_RULES = "MISSING_RULES"
    MISSING_SETTLEMENT_SOURCE = "MISSING_SETTLEMENT_SOURCE"
    MALFORMED_TIMESTAMP = "MALFORMED_TIMESTAMP"


def diagnose(
    m: Market,
    book: NormalizedBook | None,
    *,
    structure_supported: bool,
    has_source: bool,
    now: datetime | None = None,
) -> tuple[QualityReason, ...]:
    now = now or datetime.now(UTC)
    reasons: list[QualityReason] = []
    if book is None:
        reasons.extend((QualityReason.MISSING_QUOTE, QualityReason.EMPTY_BOOK))
    else:
        if book.best_yes_bid is None or book.best_yes_ask is None:
            reasons.append(QualityReason.MISSING_QUOTE)
        if not book.yes_bids and not book.no_bids:
            reasons.append(QualityReason.EMPTY_BOOK)
        if book.spread is not None and book.spread < 0:
            reasons.append(QualityReason.CROSSED_BOOK)
        if book.spread is not None and book.spread > 0.20:
            reasons.append(QualityReason.WIDE_SPREAD)
        if now - book.observed_at > timedelta(minutes=10):
            reasons.append(QualityReason.STALE_BOOK)
    if not structure_supported:
        reasons.append(QualityReason.UNSUPPORTED_PRICE_STRUCTURE)
    if m.provisional:
        reasons.append(QualityReason.PROVISIONAL)
    if m.multivariate:
        reasons.append(QualityReason.MVE_UNSUPPORTED)
    if not m.raw.get("rules_primary"):
        reasons.append(QualityReason.MISSING_RULES)
    if not has_source:
        reasons.append(QualityReason.MISSING_SETTLEMENT_SOURCE)
    return tuple(dict.fromkeys(reasons))


class Family(StrEnum):
    WEATHER = "weather"
    MACRO = "macro"
    ENERGY = "energy"
    SPORTS = "sports"
    POLITICS = "politics"
    LEGAL = "legal/regulatory"
    ENTERTAINMENT = "entertainment"
    TECHNOLOGY = "technology"
    UNKNOWN = "other/unknown"


def classify(category: str, title: str) -> Family:
    text = (category + " " + title).lower()
    rules = (
        (Family.WEATHER, ("weather", "temperature", "rain", "snow")),
        (Family.MACRO, ("economics", "cpi", "gdp", "fed", "jobs")),
        (Family.ENERGY, ("energy", "oil", "gas", "eia")),
        (Family.SPORTS, ("sports", "nba", "nfl", "mlb", "nhl")),
        (Family.POLITICS, ("politics", "election", "president")),
        (Family.LEGAL, ("court", "legal", "regulation")),
        (Family.ENTERTAINMENT, ("entertainment", "award", "movie")),
        (Family.TECHNOLOGY, ("technology", "tech", "ai")),
    )
    return next(
        (family for family, words in rules if any(word in text for word in words)), Family.UNKNOWN
    )
