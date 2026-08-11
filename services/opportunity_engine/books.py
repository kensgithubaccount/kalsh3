"""Exact binary-book normalization and fractional depth walks."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from .domain import OpportunityError


class OutcomeSide(StrEnum):
    YES = "YES"
    NO = "NO"


@dataclass(frozen=True, slots=True)
class RawBidLevel:
    raw_level_id: str
    side: OutcomeSide
    price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        if not self.price.is_finite() or not Decimal(0) < self.price < Decimal(1):
            raise OpportunityError("raw bid price invalid")
        if not self.quantity.is_finite() or self.quantity <= 0:
            raise OpportunityError("fractional depth quantity invalid")


@dataclass(frozen=True, slots=True)
class ExecutableLevel:
    outcome: OutcomeSide
    book_side: str
    price: Decimal
    quantity: Decimal
    raw_level_id: str
    raw_side: OutcomeSide
    raw_price: Decimal


@dataclass(frozen=True, slots=True)
class NormalizedBook:
    yes_bids: tuple[ExecutableLevel, ...]
    yes_asks: tuple[ExecutableLevel, ...]
    no_bids: tuple[ExecutableLevel, ...]
    no_asks: tuple[ExecutableLevel, ...]

    @property
    def yes_best_ask(self) -> Decimal | None:
        return self.yes_asks[0].price if self.yes_asks else None

    @property
    def no_best_ask(self) -> Decimal | None:
        return self.no_asks[0].price if self.no_asks else None


def normalize_binary_book(levels: tuple[RawBidLevel, ...]) -> NormalizedBook:
    yes = sorted(
        (level for level in levels if level.side == OutcomeSide.YES),
        key=lambda level: level.price,
        reverse=True,
    )
    no = sorted(
        (level for level in levels if level.side == OutcomeSide.NO),
        key=lambda level: level.price,
        reverse=True,
    )

    def bids(rows: list[RawBidLevel], side: OutcomeSide) -> tuple[ExecutableLevel, ...]:
        return tuple(
            ExecutableLevel(
                side, "BID", row.price, row.quantity, row.raw_level_id, row.side, row.price
            )
            for row in rows
        )

    def asks(rows: list[RawBidLevel], side: OutcomeSide) -> tuple[ExecutableLevel, ...]:
        return tuple(
            sorted(
                (
                    ExecutableLevel(
                        side,
                        "ASK",
                        Decimal(1) - row.price,
                        row.quantity,
                        row.raw_level_id,
                        row.side,
                        row.price,
                    )
                    for row in rows
                ),
                key=lambda level: level.price,
            )
        )

    book = NormalizedBook(
        bids(yes, OutcomeSide.YES),
        asks(no, OutcomeSide.YES),
        bids(no, OutcomeSide.NO),
        asks(yes, OutcomeSide.NO),
    )
    if book.yes_bids and book.yes_asks and book.yes_bids[0].price >= book.yes_asks[0].price:
        raise OpportunityError("binary book crossed")
    if book.no_bids and book.no_asks and book.no_bids[0].price >= book.no_asks[0].price:
        raise OpportunityError("binary book crossed")
    return book


@dataclass(frozen=True, slots=True)
class DepthWalk:
    requested: Decimal
    filled: Decimal
    average_price: Decimal | None
    worst_price: Decimal | None
    unfilled: Decimal
    total_cost: Decimal
    levels_consumed: int

    @property
    def complete(self) -> bool:
        return self.unfilled == 0


def walk_depth(levels: tuple[ExecutableLevel, ...], quantity: Decimal) -> DepthWalk:
    if quantity <= 0 or not quantity.is_finite():
        raise OpportunityError("hypothetical depth quantity invalid")
    remaining, total, worst, count = quantity, Decimal(0), None, 0
    for level in levels:
        take = min(remaining, level.quantity)
        if take <= 0:
            continue
        total += take * level.price
        remaining -= take
        worst, count = level.price, count + 1
        if remaining == 0:
            break
    filled = quantity - remaining
    return DepthWalk(
        quantity, filled, None if filled == 0 else total / filled, worst, remaining, total, count
    )
