"""Pure Phase-0 market-bound reference-lag measurement for Perps research.

This module consumes already-validated immutable Perps market-state and book
evidence. It has no network, credential, order, fee, funding, sizing, or
production capability.

The first checkpoint deliberately uses the market-bound Margin ticker
reference_price. The official feed is coalesced to at most one ticker update per
market per second, so the frozen diagnostic horizons are whole-second multiples.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from .domain import Direction, ShadowResearchError
from .perps_evidence import PerpsBookEvidenceObservation, PerpsMarketStateObservation
from .perps_metadata import canonical_hash

PHASE0_HORIZONS_MS = (1_000, 2_000, 5_000, 10_000)
MAX_BOOK_AGE_MS = 30_000
PRODUCTION_INFLUENCE = Decimal("0")


class HorizonStatus(StrEnum):
    MEASURED = "MEASURED"
    NO_BASELINE_BOOK = "NO_BASELINE_BOOK"
    STALE_BASELINE_BOOK = "STALE_BASELINE_BOOK"
    STALE_HORIZON_BOOK = "STALE_HORIZON_BOOK"
    STREAM_BOUNDARY_WITHIN_HORIZON = "STREAM_BOUNDARY_WITHIN_HORIZON"


@dataclass(frozen=True, slots=True)
class ReferenceImpulse:
    impulse_id: str
    ticker: str
    exchange_index: int
    market_version: int
    underlying_multiplier: Decimal
    market_metadata_hash: str
    previous_market_state_evidence_id: str
    current_market_state_evidence_id: str
    connection_epoch: UUID
    ticker_sid: int
    previous_reference_price: Decimal
    reference_price: Decimal
    previous_source_ts_ms: int
    source_ts_ms: int
    available_at: datetime
    reference_change: Decimal
    reference_change_bps: Decimal
    direction: Direction
    production_influence: Decimal = PRODUCTION_INFLUENCE

    def __post_init__(self) -> None:
        if self.production_influence != 0:
            raise ShadowResearchError("reference-lag research cannot have production influence")
        if self.reference_price <= 0 or self.previous_reference_price <= 0:
            raise ShadowResearchError("reference prices must be positive")
        if (
            not isinstance(self.underlying_multiplier, Decimal)
            or not self.underlying_multiplier.is_finite()
            or self.underlying_multiplier <= 0
        ):
            raise ShadowResearchError("reference impulse underlying_multiplier must be positive")
        if self.source_ts_ms <= self.previous_source_ts_ms:
            raise ShadowResearchError("reference source timestamps must increase")
        if self.available_at.tzinfo is None or self.available_at.utcoffset() is None:
            raise ShadowResearchError("impulse available_at must be timezone-aware")
        object.__setattr__(self, "available_at", self.available_at.astimezone(UTC))
        if not isinstance(self.connection_epoch, UUID) or self.connection_epoch.int == 0:
            raise ShadowResearchError("reference impulse requires a non-zero connection epoch")
        if type(self.ticker_sid) is not int or self.ticker_sid < 1:
            raise ShadowResearchError("reference impulse ticker sid must be a positive integer")
        for evidence_id in (
            self.previous_market_state_evidence_id,
            self.current_market_state_evidence_id,
        ):
            if len(evidence_id) != 64 or any(
                char not in "0123456789abcdef" for char in evidence_id
            ):
                raise ShadowResearchError("reference impulse evidence IDs must be SHA-256")
        expected_change = self.reference_price - self.previous_reference_price
        if self.reference_change != expected_change or self.reference_change == 0:
            raise ShadowResearchError("reference impulse must preserve a nonzero exact change")
        expected_bps = expected_change / self.previous_reference_price * Decimal("10000")
        if self.reference_change_bps != expected_bps:
            raise ShadowResearchError("reference change bps contradicts exact prices")
        expected_direction = Direction.LONG if expected_change > 0 else Direction.SHORT
        if self.direction is not expected_direction:
            raise ShadowResearchError("reference impulse direction contradicts exact change")
        expected_id = canonical_hash(
            {
                "ticker": self.ticker,
                "exchange_index": self.exchange_index,
                "market_version": self.market_version,
                "underlying_multiplier": self.underlying_multiplier,
                "market_metadata_hash": self.market_metadata_hash,
                "previous_market_state_evidence_id": self.previous_market_state_evidence_id,
                "current_market_state_evidence_id": self.current_market_state_evidence_id,
                "connection_epoch": self.connection_epoch,
                "ticker_sid": self.ticker_sid,
                "previous_reference_price": self.previous_reference_price,
                "reference_price": self.reference_price,
                "previous_source_ts_ms": self.previous_source_ts_ms,
                "source_ts_ms": self.source_ts_ms,
                "available_at": self.available_at,
                "reference_change": self.reference_change,
                "reference_change_bps": self.reference_change_bps,
                "direction": self.direction,
                "production_influence": self.production_influence,
            }
        )
        if self.impulse_id != expected_id:
            raise ShadowResearchError("reference impulse id does not match canonical payload")


@dataclass(frozen=True, slots=True)
class ReferenceLagMeasurement:
    impulse_id: str
    ticker: str
    horizon_ms: int
    status: HorizonStatus
    baseline_book_evidence_id: str | None
    horizon_book_evidence_id: str | None
    baseline_available_at: datetime | None
    horizon_cutoff_at: datetime
    horizon_book_available_at: datetime | None
    bid_change: Decimal | None
    ask_change: Decimal | None
    midpoint_change: Decimal | None
    midpoint_change_bps: Decimal | None
    production_influence: Decimal = PRODUCTION_INFLUENCE

    def __post_init__(self) -> None:
        if self.horizon_ms not in PHASE0_HORIZONS_MS:
            raise ShadowResearchError("horizon is outside frozen Phase-0 grid")
        if self.production_influence != 0:
            raise ShadowResearchError("reference-lag measurement cannot have production influence")
        if self.status is HorizonStatus.MEASURED:
            if (
                self.baseline_book_evidence_id is None
                or self.horizon_book_evidence_id is None
                or self.baseline_available_at is None
                or self.horizon_book_available_at is None
            ):
                raise ShadowResearchError("measured lag row requires both book identities")
        elif any(
            value is not None
            for value in (
                self.bid_change,
                self.ask_change,
                self.midpoint_change,
                self.midpoint_change_bps,
            )
        ):
            raise ShadowResearchError("abstained lag row cannot contain repricing values")


def build_reference_impulse(
    previous: PerpsMarketStateObservation,
    current: PerpsMarketStateObservation,
) -> ReferenceImpulse | None:
    """Build one observed nonzero reference change, or None for no change."""
    if previous.connection_epoch != current.connection_epoch or previous.sid != current.sid:
        raise ShadowResearchError("reference observations cross a connection/subscription boundary")
    if (
        previous.ticker != current.ticker
        or previous.exchange_index != current.exchange_index
        or previous.market_version != current.market_version
        or previous.market_metadata_hash != current.market_metadata_hash
        or previous.underlying_multiplier != current.underlying_multiplier
    ):
        raise ShadowResearchError("reference observations cross a market-contract boundary")
    if previous.reference_price is None or current.reference_price is None:
        raise ShadowResearchError("Phase-0 reference lag requires market-bound reference_price")
    if previous.available_at > current.available_at:
        raise ShadowResearchError("reference evidence availability must be monotonic")
    if previous.reference_price.ts_ms >= current.reference_price.ts_ms:
        raise ShadowResearchError("reference source timestamps must be strictly increasing")

    previous_source_at = datetime.fromtimestamp(previous.reference_price.ts_ms / 1000, UTC)
    current_source_at = datetime.fromtimestamp(current.reference_price.ts_ms / 1000, UTC)
    if previous_source_at > previous.available_at or current_source_at > current.available_at:
        raise ShadowResearchError("reference source timestamp is after local availability")

    change = current.reference_price.price - previous.reference_price.price
    if change == 0:
        return None
    bps = change / previous.reference_price.price * Decimal("10000")
    direction = Direction.LONG if change > 0 else Direction.SHORT
    identity_payload = {
        "ticker": current.ticker,
        "exchange_index": current.exchange_index,
        "market_version": current.market_version,
        "underlying_multiplier": current.underlying_multiplier,
        "market_metadata_hash": current.market_metadata_hash,
        "previous_market_state_evidence_id": previous.evidence_id,
        "current_market_state_evidence_id": current.evidence_id,
        "connection_epoch": current.connection_epoch,
        "ticker_sid": current.sid,
        "previous_reference_price": previous.reference_price.price,
        "reference_price": current.reference_price.price,
        "previous_source_ts_ms": previous.reference_price.ts_ms,
        "source_ts_ms": current.reference_price.ts_ms,
        "available_at": current.available_at,
        "reference_change": change,
        "reference_change_bps": bps,
        "direction": direction,
        "production_influence": PRODUCTION_INFLUENCE,
    }
    return ReferenceImpulse(
        impulse_id=canonical_hash(identity_payload),
        ticker=current.ticker,
        exchange_index=current.exchange_index,
        market_version=current.market_version,
        underlying_multiplier=current.underlying_multiplier,
        market_metadata_hash=current.market_metadata_hash,
        previous_market_state_evidence_id=previous.evidence_id,
        current_market_state_evidence_id=current.evidence_id,
        connection_epoch=current.connection_epoch,
        ticker_sid=current.sid,
        previous_reference_price=previous.reference_price.price,
        reference_price=current.reference_price.price,
        previous_source_ts_ms=previous.reference_price.ts_ms,
        source_ts_ms=current.reference_price.ts_ms,
        available_at=current.available_at,
        reference_change=change,
        reference_change_bps=bps,
        direction=direction,
    )


def _midpoint(book: PerpsBookEvidenceObservation) -> Decimal | None:
    if book.best_bid is None or book.best_ask is None:
        return None
    return (book.best_bid + book.best_ask) / Decimal("2")


def _candidate_books(
    impulse: ReferenceImpulse,
    books: Iterable[PerpsBookEvidenceObservation],
) -> tuple[PerpsBookEvidenceObservation, ...]:
    material = tuple(books)
    for book in material:
        if (
            book.ticker != impulse.ticker
            or book.exchange_index != impulse.exchange_index
            or book.market_version != impulse.market_version
            or book.underlying_multiplier != impulse.underlying_multiplier
            or book.market_metadata_hash != impulse.market_metadata_hash
        ):
            raise ShadowResearchError("book evidence crosses reference-impulse contract identity")
    return tuple(
        sorted(material, key=lambda item: (item.available_at, item.sequence, item.evidence_id))
    )


def _age_ms(earlier: datetime, later: datetime) -> int:
    delta = later - earlier
    microseconds = (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds
    if microseconds < 0:
        raise ShadowResearchError("book availability cannot be after evaluation cutoff")
    milliseconds, remainder = divmod(microseconds, 1_000)
    if remainder:
        return milliseconds + 1
    return milliseconds


def _abstention(
    impulse: ReferenceImpulse,
    horizon_ms: int,
    status: HorizonStatus,
    cutoff: datetime,
    *,
    baseline: PerpsBookEvidenceObservation | None = None,
    horizon: PerpsBookEvidenceObservation | None = None,
) -> ReferenceLagMeasurement:
    return ReferenceLagMeasurement(
        impulse.impulse_id,
        impulse.ticker,
        horizon_ms,
        status,
        None if baseline is None else baseline.evidence_id,
        None if horizon is None else horizon.evidence_id,
        None if baseline is None else baseline.available_at,
        cutoff,
        None if horizon is None else horizon.available_at,
        None,
        None,
        None,
        None,
    )


def measure_reference_lag_horizon(
    impulse: ReferenceImpulse,
    books: Iterable[PerpsBookEvidenceObservation],
    *,
    horizon_ms: int,
) -> ReferenceLagMeasurement:
    """Measure quote repricing at one frozen horizon without constructing a trade."""
    if horizon_ms not in PHASE0_HORIZONS_MS:
        raise ShadowResearchError("horizon is outside frozen Phase-0 grid")
    material = _candidate_books(impulse, books)
    cutoff = impulse.available_at + timedelta(milliseconds=horizon_ms)

    baseline_candidates = [
        item
        for item in material
        if item.connection_epoch == impulse.connection_epoch
        and item.available_at <= impulse.available_at
    ]
    if not baseline_candidates:
        return _abstention(
            impulse,
            horizon_ms,
            HorizonStatus.NO_BASELINE_BOOK,
            cutoff,
        )
    baseline = baseline_candidates[-1]
    if _age_ms(baseline.available_at, impulse.available_at) > MAX_BOOK_AGE_MS:
        return _abstention(
            impulse,
            horizon_ms,
            HorizonStatus.STALE_BASELINE_BOOK,
            cutoff,
            baseline=baseline,
        )

    boundary_books = [
        item
        for item in material
        if impulse.available_at < item.available_at <= cutoff
        and (
            item.connection_epoch != impulse.connection_epoch
            or (
                item.connection_epoch == impulse.connection_epoch
                and item.sid != baseline.sid
            )
        )
    ]
    if boundary_books:
        return _abstention(
            impulse,
            horizon_ms,
            HorizonStatus.STREAM_BOUNDARY_WITHIN_HORIZON,
            cutoff,
            baseline=baseline,
            horizon=boundary_books[0],
        )

    horizon_candidates = [
        item
        for item in material
        if item.connection_epoch == impulse.connection_epoch
        and item.sid == baseline.sid
        and item.available_at <= cutoff
    ]
    horizon = horizon_candidates[-1]
    if _age_ms(horizon.available_at, cutoff) > MAX_BOOK_AGE_MS:
        return _abstention(
            impulse,
            horizon_ms,
            HorizonStatus.STALE_HORIZON_BOOK,
            cutoff,
            baseline=baseline,
            horizon=horizon,
        )
    if horizon.sequence < baseline.sequence:
        raise ShadowResearchError("book sequence regressed within one stream")

    bid_change = (
        None
        if baseline.best_bid is None or horizon.best_bid is None
        else horizon.best_bid - baseline.best_bid
    )
    ask_change = (
        None
        if baseline.best_ask is None or horizon.best_ask is None
        else horizon.best_ask - baseline.best_ask
    )
    baseline_mid = _midpoint(baseline)
    horizon_mid = _midpoint(horizon)
    midpoint_change = (
        None if baseline_mid is None or horizon_mid is None else horizon_mid - baseline_mid
    )
    midpoint_change_bps = (
        None
        if midpoint_change is None or baseline_mid is None or baseline_mid <= 0
        else midpoint_change / baseline_mid * Decimal("10000")
    )
    return ReferenceLagMeasurement(
        impulse.impulse_id,
        impulse.ticker,
        horizon_ms,
        HorizonStatus.MEASURED,
        baseline.evidence_id,
        horizon.evidence_id,
        baseline.available_at,
        cutoff,
        horizon.available_at,
        bid_change,
        ask_change,
        midpoint_change,
        midpoint_change_bps,
    )


def measure_reference_lag_grid(
    impulse: ReferenceImpulse,
    books: Iterable[PerpsBookEvidenceObservation],
) -> tuple[ReferenceLagMeasurement, ...]:
    material = tuple(books)
    return tuple(
        measure_reference_lag_horizon(impulse, material, horizon_ms=horizon)
        for horizon in PHASE0_HORIZONS_MS
    )
