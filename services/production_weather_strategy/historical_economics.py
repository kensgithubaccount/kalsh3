"""Fee-aware historical checkpoint economics for M28 model evaluation.

This module is pure and deliberately stops short of execution. It reconstructs one-contract
TAKER economics from pre-cutoff public quote evidence plus an explicitly reviewed fee regime.
It does not claim that a historical order would have filled; fill truth remains a separate
execution-learning problem.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from services.historical_replay.archive import stable_hash
from services.opportunity_engine.fees import FeeType, calculate_fee, current_event_formula_policy


class HistoricalEconomicsError(ValueError):
    """Historical market economics violate an M28 reconstruction invariant."""


class ReconstructionFidelity(StrEnum):
    TOP_OF_BOOK_CANDLE = "TOP_OF_BOOK_CANDLE"


class TradeSide(StrEnum):
    YES = "YES"
    NO = "NO"


@dataclass(frozen=True, slots=True)
class HistoricalQuoteCheckpoint:
    """One pre-cutoff binary-market top-of-book reconstruction."""

    checkpoint_id: str
    market_ticker: str
    checkpoint_at: datetime
    yes_bid: Decimal
    yes_ask: Decimal
    no_bid: Decimal
    no_ask: Decimal
    quote_evidence_id: str
    fidelity: ReconstructionFidelity
    content_hash: str

    @classmethod
    def build(
        cls,
        *,
        market_ticker: str,
        checkpoint_at: datetime,
        yes_bid: Decimal,
        yes_ask: Decimal,
        quote_evidence_id: str,
        fidelity: ReconstructionFidelity = ReconstructionFidelity.TOP_OF_BOOK_CANDLE,
    ) -> HistoricalQuoteCheckpoint:
        if not market_ticker.strip() or not quote_evidence_id.strip():
            raise HistoricalEconomicsError("quote checkpoint identity is required")
        checkpoint = _utc(checkpoint_at, "checkpoint")
        for value, name in ((yes_bid, "yes bid"), (yes_ask, "yes ask")):
            if not value.is_finite() or not Decimal("0") <= value <= Decimal("1"):
                raise HistoricalEconomicsError(f"{name} is outside [0,1]")
        if yes_bid > yes_ask:
            raise HistoricalEconomicsError("historical yes bid exceeds yes ask")
        no_bid = Decimal("1") - yes_ask
        no_ask = Decimal("1") - yes_bid
        material = (
            "m28d-historical-quote-v1",
            market_ticker,
            checkpoint.isoformat(),
            str(yes_bid),
            str(yes_ask),
            str(no_bid),
            str(no_ask),
            quote_evidence_id,
            fidelity.value,
        )
        digest = stable_hash(material)
        return cls(
            checkpoint_id=digest,
            market_ticker=market_ticker,
            checkpoint_at=checkpoint,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            no_bid=no_bid,
            no_ask=no_ask,
            quote_evidence_id=quote_evidence_id,
            fidelity=fidelity,
            content_hash=digest,
        )


@dataclass(frozen=True, slots=True)
class HistoricalSideEconomics:
    side: TradeSide
    taker_price: Decimal
    taker_fee: Decimal
    all_in_cost: Decimal
    maximum_loss: Decimal


@dataclass(frozen=True, slots=True)
class HistoricalCheckpointEconomics:
    """One-contract conservative TAKER economics at one historical checkpoint."""

    economics_id: str
    market_ticker: str
    checkpoint_at: datetime
    quote_checkpoint_id: str
    fee_policy_id: str
    fee_type: FeeType
    fee_multiplier: Decimal
    yes: HistoricalSideEconomics
    no: HistoricalSideEconomics
    exact_fill_truth: bool
    reconstruction_note: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class HistoricalOpportunity:
    """Model-vs-market opportunity at the exact same historical checkpoint."""

    opportunity_id: str
    market_ticker: str
    side: TradeSide
    model_side_probability: Decimal
    all_in_cost: Decimal
    after_cost_edge: Decimal
    resolved_yes: int
    hypothetical_pnl: Decimal
    economics_id: str
    model_evidence_id: str
    content_hash: str


def reconstruct_checkpoint_economics(
    quote: HistoricalQuoteCheckpoint,
    *,
    fee_type: FeeType,
    fee_multiplier: Decimal,
) -> HistoricalCheckpointEconomics:
    """Apply the reviewed 2026 event-fee formula to one historical quote checkpoint."""

    if fee_multiplier < 0 or not fee_multiplier.is_finite():
        raise HistoricalEconomicsError("fee multiplier is invalid")
    try:
        policy = current_event_formula_policy(fee_type=fee_type, fee_multiplier=fee_multiplier)
        if not policy.applies_at(quote.checkpoint_at):
            raise HistoricalEconomicsError(
                "reviewed fee policy does not apply at historical checkpoint"
            )
        yes_fee = calculate_fee(policy, quote.yes_ask, Decimal("1"), maker=False).total_fee
        no_fee = calculate_fee(policy, quote.no_ask, Decimal("1"), maker=False).total_fee
    except HistoricalEconomicsError:
        raise
    except Exception as exc:
        raise HistoricalEconomicsError("historical fee reconstruction failed") from exc
    yes = _side_economics(TradeSide.YES, quote.yes_ask, yes_fee)
    no = _side_economics(TradeSide.NO, quote.no_ask, no_fee)
    material = (
        "m28d-historical-economics-v1",
        quote.checkpoint_id,
        policy.policy_id,
        fee_type.value,
        str(fee_multiplier),
        _side_material(yes),
        _side_material(no),
        False,
    )
    digest = stable_hash(material)
    return HistoricalCheckpointEconomics(
        economics_id=digest,
        market_ticker=quote.market_ticker,
        checkpoint_at=quote.checkpoint_at,
        quote_checkpoint_id=quote.checkpoint_id,
        fee_policy_id=policy.policy_id,
        fee_type=fee_type,
        fee_multiplier=fee_multiplier,
        yes=yes,
        no=no,
        exact_fill_truth=False,
        reconstruction_note=(
            "Top-of-book public quote plus reviewed fee formula; does not prove historical fill, "
            "queue position, depth, slippage, or final charged exchange fee."
        ),
        content_hash=digest,
    )


def evaluate_historical_opportunity(
    economics: HistoricalCheckpointEconomics,
    *,
    model_yes_probability: Decimal,
    resolved_yes: int,
    model_evidence_id: str,
) -> HistoricalOpportunity:
    """Choose the stronger side and compute one-contract hypothetical settlement PnL."""

    if not model_evidence_id.strip():
        raise HistoricalEconomicsError("model evidence id is required")
    if (
        not model_yes_probability.is_finite()
        or not Decimal("0") <= model_yes_probability <= Decimal("1")
    ):
        raise HistoricalEconomicsError("model probability is outside [0,1]")
    if resolved_yes not in {0, 1}:
        raise HistoricalEconomicsError("resolved_yes must be binary")
    yes_edge = model_yes_probability - economics.yes.all_in_cost
    model_no_probability = Decimal("1") - model_yes_probability
    no_edge = model_no_probability - economics.no.all_in_cost
    if yes_edge >= no_edge:
        side = TradeSide.YES
        probability = model_yes_probability
        selected = economics.yes
        won = resolved_yes == 1
        edge = yes_edge
    else:
        side = TradeSide.NO
        probability = model_no_probability
        selected = economics.no
        won = resolved_yes == 0
        edge = no_edge
    pnl = (Decimal("1") - selected.all_in_cost) if won else -selected.all_in_cost
    material = (
        "m28d-historical-opportunity-v1",
        economics.economics_id,
        model_evidence_id,
        side.value,
        str(probability),
        str(selected.all_in_cost),
        str(edge),
        resolved_yes,
        str(pnl),
    )
    digest = stable_hash(material)
    return HistoricalOpportunity(
        opportunity_id=digest,
        market_ticker=economics.market_ticker,
        side=side,
        model_side_probability=probability,
        all_in_cost=selected.all_in_cost,
        after_cost_edge=edge,
        resolved_yes=resolved_yes,
        hypothetical_pnl=pnl,
        economics_id=economics.economics_id,
        model_evidence_id=model_evidence_id,
        content_hash=digest,
    )


def _side_economics(side: TradeSide, price: Decimal, fee: Decimal) -> HistoricalSideEconomics:
    if not Decimal("0") < price < Decimal("1"):
        raise HistoricalEconomicsError("taker price must be strictly inside (0,1)")
    if fee < 0 or not fee.is_finite():
        raise HistoricalEconomicsError("taker fee is invalid")
    all_in = price + fee
    if all_in > Decimal("1"):
        raise HistoricalEconomicsError("all-in historical cost exceeds maximum payout")
    return HistoricalSideEconomics(
        side=side,
        taker_price=price,
        taker_fee=fee,
        all_in_cost=all_in,
        maximum_loss=all_in,
    )


def _side_material(value: HistoricalSideEconomics) -> tuple[str, str, str, str, str]:
    return (
        value.side.value,
        str(value.taker_price),
        str(value.taker_fee),
        str(value.all_in_cost),
        str(value.maximum_loss),
    )


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HistoricalEconomicsError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)
