"""Exact mode-separated double-entry demo/paper/mock portfolio ledger."""

from dataclasses import dataclass
from decimal import Decimal

from .domain import ExecutionEnvironment
from .store import FillEvent


@dataclass(frozen=True, slots=True)
class Posting:
    posting_id: str
    mode: ExecutionEnvironment
    account: str
    amount: Decimal
    trade_id: str


def postings_for_fill(fill: FillEvent, mode: ExecutionEnvironment) -> tuple[Posting, ...]:
    cost = fill.price * fill.quantity
    prefix = f"{mode}:{fill.trade_id}"
    return (
        Posting(f"{prefix}:cash", mode, "CASH", -(cost + fill.fee), fill.trade_id),
        Posting(
            f"{prefix}:contract",
            mode,
            f"CONTRACT:{fill.ticker}:{fill.outcome_side}",
            cost,
            fill.trade_id,
        ),
        Posting(f"{prefix}:fee", mode, "FEE_EXPENSE", fill.fee, fill.trade_id),
    )


def net_postings(postings: tuple[Posting, ...]) -> Decimal:
    return sum((posting.amount for posting in postings), Decimal(0))
