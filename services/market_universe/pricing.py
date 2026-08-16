"""Exact price ladders and REST binary-book normalization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from itertools import pairwise
from typing import Any, cast

from .domain import UniverseValidationError, exact


@dataclass(frozen=True, slots=True)
class PriceRange:
    minimum: Decimal
    maximum: Decimal
    step: Decimal

    def __post_init__(self) -> None:
        if (
            any(not value.is_finite() for value in (self.minimum, self.maximum, self.step))
            or self.minimum < 0
            or self.maximum > 1
            or self.minimum >= self.maximum
            or self.step <= 0
        ):
            raise UniverseValidationError("malformed price range")

    def contains(self, p: Decimal) -> bool:
        return self.minimum <= p <= self.maximum

    def valid(self, p: Decimal) -> bool:
        return self.contains(p) and (p - self.minimum) % self.step == 0


@dataclass(frozen=True, slots=True)
class PriceLadder:
    structure: str
    ranges: tuple[PriceRange, ...]

    @classmethod
    def parse(cls, structure: Any, ranges: Any) -> PriceLadder:
        if (
            not isinstance(structure, str)
            or not structure
            or not isinstance(ranges, list)
            or not ranges
        ):
            raise UniverseValidationError("unsupported price structure")
        modern = all(isinstance(x, dict) and {"start", "end", "step"} <= x.keys() for x in ranges)
        legacy = all(isinstance(x, dict) and {"min", "max", "step"} <= x.keys() for x in ranges)
        if not modern and not legacy:
            raise UniverseValidationError("malformed price ranges")
        parsed = tuple(
            PriceRange(
                exact(x.get("start" if modern else "min"), "start" if modern else "min"),
                exact(x.get("end" if modern else "max"), "end" if modern else "max"),
                exact(x.get("step"), "step"),
            )
            for x in ranges
            if isinstance(x, dict)
        )
        if len(parsed) != len(ranges):
            raise UniverseValidationError("malformed price ranges")
        ordered = tuple(sorted(parsed, key=lambda x: x.minimum))
        if modern and any((item.maximum - item.minimum) % item.step != 0 for item in ordered):
            raise UniverseValidationError("price range width not divisible by step")
        if modern and any(a.maximum != b.minimum for a, b in pairwise(ordered)):
            raise UniverseValidationError("overlap or invalid gap")
        if legacy and any(
            a.maximum > b.minimum or a.maximum + a.step < b.minimum for a, b in pairwise(ordered)
        ):
            raise UniverseValidationError("overlap or invalid gap")
        if modern and (ordered[0].minimum != 0 or ordered[-1].maximum != 1):
            raise UniverseValidationError("price ranges must cover zero through one")
        return cls(structure, ordered)

    @property
    def precision(self) -> int:
        return max(-cast(int, r.step.as_tuple().exponent) for r in self.ranges)

    def price_range(self, p: Decimal) -> PriceRange | None:
        return next((r for r in self.ranges if r.contains(p)), None)

    def is_valid(self, p: Decimal) -> bool:
        return (
            p.is_finite() and Decimal(0) < p < Decimal(1) and any(r.valid(p) for r in self.ranges)
        )

    def next_above(self, p: Decimal) -> Decimal | None:
        values = [
            r.minimum + (((p - r.minimum) // r.step) + 1) * r.step
            for r in self.ranges
            if p < r.maximum
        ]
        return min((v for v in values if v > p and self.is_valid(v)), default=None)

    def next_below(self, p: Decimal) -> Decimal | None:
        values = []
        for r in self.ranges:
            if p > r.minimum:
                candidate = r.minimum + ((p - r.minimum) // r.step) * r.step
                if candidate >= p:
                    candidate -= r.step
                if r.valid(candidate):
                    values.append(candidate)
        return max(values, default=None)


@dataclass(frozen=True, slots=True)
class Level:
    price: Decimal
    size: Decimal


@dataclass(frozen=True, slots=True)
class NormalizedBook:
    yes_bids: tuple[Level, ...]
    no_bids: tuple[Level, ...]
    best_yes_bid: Decimal | None
    best_yes_ask: Decimal | None
    spread: Decimal | None
    observed_at: datetime
    ingested_at: datetime


def parse_levels(raw: Any) -> tuple[Level, ...]:
    if not isinstance(raw, list):
        raise UniverseValidationError("book levels missing")
    levels = []
    for item in raw:
        if not isinstance(item, list) or len(item) != 2:
            raise UniverseValidationError("malformed book level")
        p, s = exact(item[0], "book price"), exact(item[1], "book size")
        if p <= 0 or p >= 1 or s <= 0:
            raise UniverseValidationError("invalid book level")
        levels.append(Level(p, s))
    return tuple(levels)


def normalize_book(
    yes: Any, no: Any, observed_at: datetime, ingested_at: datetime
) -> NormalizedBook:
    y, n = parse_levels(yes), parse_levels(no)
    bid = max((x.price for x in y), default=None)
    no_bid = max((x.price for x in n), default=None)
    ask = None if no_bid is None else Decimal("1") - no_bid
    spread = None if bid is None or ask is None else ask - bid
    return NormalizedBook(y, n, bid, ask, spread, observed_at, ingested_at)


def chunk_tickers(tickers: list[str]) -> list[list[str]]:
    return [tickers[i : i + 100] for i in range(0, len(tickers), 100)]
