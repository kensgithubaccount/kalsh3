from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from services.perps_shadow_research.domain import Direction, ShadowResearchError
from services.perps_shadow_research.perps_events import (
    PerpsBookSnapshotEvent,
    PerpsTickerEvent,
)
from services.perps_shadow_research.perps_evidence import (
    PerpsBookEvidenceObservation,
    PerpsMarketStateObservation,
)
from services.perps_shadow_research.perps_metadata import parse_perps_market
from services.perps_shadow_research.perps_orderbook import PerpsBookState, PerpsBookView
from services.perps_shadow_research.reference_lag import (
    MAX_BOOK_AGE_MS,
    PHASE0_HORIZONS_MS,
    HorizonStatus,
    build_reference_impulse,
    measure_reference_lag_grid,
    measure_reference_lag_horizon,
)

NOW = datetime(2026, 10, 4, 12, tzinfo=UTC)
NOW_MS = int(NOW.timestamp() * 1000)
EPOCH = UUID("12345678-1234-5678-1234-567812345678")
OTHER_EPOCH = UUID("87654321-4321-8765-4321-876543218765")


def market(**changes: object):
    raw: dict[str, object] = {
        "ticker": "BTC-PERP",
        "status": "active",
        "title": "Bitcoin",
        "exchange_index": 4,
        "market_version": 7,
        "contract_size": "0.001000",
        "underlying_multiplier": "1.000000",
        "tick_size": "0.5000",
        "fractional_trading_enabled": True,
        "schedule": None,
        "asset_class": "Crypto",
    }
    raw.update(changes)
    return parse_perps_market(raw, observed_at=NOW)


def state(
    *,
    reference: str,
    source_offset_ms: int,
    available_offset_ms: int,
    market_obj=None,
    epoch: UUID = EPOCH,
    sid: int = 8,
) -> PerpsMarketStateObservation:
    market_obj = market() if market_obj is None else market_obj
    event = PerpsTickerEvent.parse(
        {
            "type": "ticker",
            "sid": sid,
            "sending_ts_ms": NOW_MS + available_offset_ms - 1,
            "msg": {
                "market_ticker": "BTC-PERP",
                "price": "100.5000",
                "bid": "100.0000",
                "ask": "101.0000",
                "bid_size_fp": "2.00",
                "ask_size_fp": "3.00",
                "last_trade_size_fp": "1.00",
                "volume": "10.00",
                "volume_notional_value_dollars": "1000.00",
                "volume_24h": "5.00",
                "volume_24h_notional_value_dollars": "500.00",
                "open_interest": "7.00",
                "open_interest_notional_value_dollars": "700.00",
                "ts_ms": NOW_MS + available_offset_ms - 2,
                "reference_price": {
                    "price": reference,
                    "ts_ms": NOW_MS + source_offset_ms,
                },
            },
        },
        market_obj,
    )
    available = NOW + timedelta(milliseconds=available_offset_ms)
    return PerpsMarketStateObservation.create(
        event,
        market_obj,
        epoch,
        available - timedelta(milliseconds=1),
        available,
    )


def book(
    *,
    sequence: int,
    bid: str,
    ask: str,
    available_offset_ms: int,
    epoch: UUID = EPOCH,
    sid: int = 7,
    market_obj=None,
) -> PerpsBookEvidenceObservation:
    market_obj = market() if market_obj is None else market_obj
    event = PerpsBookSnapshotEvent(
        sid=sid,
        sequence=sequence,
        ticker=market_obj.ticker,
        bids=((Decimal(bid), Decimal("2.00")),),
        asks=((Decimal(ask), Decimal("3.00")),),
    )
    available = NOW + timedelta(milliseconds=available_offset_ms)
    view = PerpsBookView(
        ticker=market_obj.ticker,
        sequence=sequence,
        bids=event.bids,
        asks=event.asks,
        best_bid=Decimal(bid),
        best_ask=Decimal(ask),
        best_bid_size=Decimal("2.00"),
        best_ask_size=Decimal("3.00"),
        state=PerpsBookState.CURRENT,
        observed_at=available,
        ingested_at=available,
        full_book_hash="1" * 64,
    )
    return PerpsBookEvidenceObservation.create(
        event=event,
        market=market_obj,
        epoch=epoch,
        view=view,
        received_at=available,
        available_at=available,
    )


def test_phase0_horizons_and_book_freshness_are_frozen() -> None:
    assert PHASE0_HORIZONS_MS == (1_000, 2_000, 5_000, 10_000)
    assert MAX_BOOK_AGE_MS == 30_000


def test_every_observed_nonzero_market_bound_reference_change_is_an_impulse() -> None:
    previous = state(reference="100.0000", source_offset_ms=100, available_offset_ms=120)
    current = state(reference="100.2500", source_offset_ms=1_100, available_offset_ms=1_120)
    impulse = build_reference_impulse(previous, current)
    assert impulse is not None
    assert impulse.reference_change == Decimal("0.2500")
    assert impulse.reference_change_bps == Decimal("25.0000")
    assert impulse.direction is Direction.LONG
    assert impulse.previous_market_state_evidence_id == previous.evidence_id
    assert impulse.current_market_state_evidence_id == current.evidence_id
    assert impulse.connection_epoch == EPOCH
    assert impulse.ticker_sid == 8
    assert impulse.underlying_multiplier == Decimal("1.000000")
    assert impulse.production_influence == 0

    unchanged = state(reference="100.2500", source_offset_ms=2_100, available_offset_ms=2_120)
    assert build_reference_impulse(current, unchanged) is None


def test_reference_impulse_fails_closed_across_contract_or_time_boundary() -> None:
    previous = state(reference="100", source_offset_ms=100, available_offset_ms=120)
    changed_market = market(market_version=8)
    current = state(
        reference="101",
        source_offset_ms=1_100,
        available_offset_ms=1_120,
        market_obj=changed_market,
    )
    with pytest.raises(ShadowResearchError, match="contract boundary"):
        build_reference_impulse(previous, current)

    nonmonotonic = state(reference="101", source_offset_ms=50, available_offset_ms=1_120)
    with pytest.raises(ShadowResearchError, match="strictly increasing"):
        build_reference_impulse(previous, nonmonotonic)

    impossible_previous = state(
        reference="100",
        source_offset_ms=200,
        available_offset_ms=120,
    )
    with pytest.raises(ShadowResearchError, match="after local availability"):
        build_reference_impulse(impossible_previous, current)


def test_reference_impulse_cannot_span_reconnect_or_subscription_change() -> None:
    previous = state(reference="100", source_offset_ms=100, available_offset_ms=120)
    current = state(reference="101", source_offset_ms=1_100, available_offset_ms=1_120)

    reconnected = PerpsMarketStateObservation.create(
        PerpsTickerEvent.parse(
            {
                "type": "ticker",
                "sid": 8,
                "msg": {
                    "market_ticker": "BTC-PERP",
                    "price": "100.5",
                    "bid": "100",
                    "ask": "101",
                    "bid_size_fp": "2",
                    "ask_size_fp": "3",
                    "last_trade_size_fp": "1",
                    "volume": "10",
                    "volume_notional_value_dollars": "1000",
                    "volume_24h": "5",
                    "volume_24h_notional_value_dollars": "500",
                    "open_interest": "7",
                    "open_interest_notional_value_dollars": "700",
                    "ts_ms": NOW_MS + 1_118,
                    "reference_price": {
                        "price": "101",
                        "ts_ms": NOW_MS + 1_100,
                    },
                },
            },
            market(),
        ),
        market(),
        OTHER_EPOCH,
        NOW + timedelta(milliseconds=1_119),
        NOW + timedelta(milliseconds=1_120),
    )
    with pytest.raises(ShadowResearchError, match="connection/subscription"):
        build_reference_impulse(previous, reconnected)

    with pytest.raises(ShadowResearchError, match="evidence_id"):
        PerpsMarketStateObservation(
            evidence_id=current.evidence_id,
            ticker=current.ticker,
            exchange_index=current.exchange_index,
            market_version=current.market_version,
            underlying_multiplier=current.underlying_multiplier,
            asset_class=current.asset_class,
            connection_epoch=current.connection_epoch,
            sid=9,
            source_fingerprint=current.source_fingerprint,
            ticker_ts_ms=current.ticker_ts_ms,
            sending_ts_ms=current.sending_ts_ms,
            received_at=current.received_at,
            available_at=current.available_at,
            price=current.price,
            bid=current.bid,
            ask=current.ask,
            bid_size=current.bid_size,
            ask_size=current.ask_size,
            last_trade_size=current.last_trade_size,
            volume=current.volume,
            volume_notional_value_dollars=current.volume_notional_value_dollars,
            volume_24h=current.volume_24h,
            volume_24h_notional_value_dollars=current.volume_24h_notional_value_dollars,
            open_interest=current.open_interest,
            open_interest_notional_value_dollars=current.open_interest_notional_value_dollars,
            reference_price=current.reference_price,
            settlement_mark_price=current.settlement_mark_price,
            liquidation_mark_price=current.liquidation_mark_price,
            funding_rate=current.funding_rate,
            funding_observed_ts_ms=current.funding_observed_ts_ms,
            next_funding_time_ms=current.next_funding_time_ms,
            market_metadata_hash=current.market_metadata_hash,
        )


def test_phase0_grid_measures_quote_repricing_without_pnl() -> None:
    previous = state(reference="100", source_offset_ms=100, available_offset_ms=120)
    current = state(reference="101", source_offset_ms=1_100, available_offset_ms=1_120)
    impulse = build_reference_impulse(previous, current)
    assert impulse is not None

    books = (
        book(sequence=1, bid="100.0", ask="101.0", available_offset_ms=1_000),
        book(sequence=2, bid="100.5", ask="101.5", available_offset_ms=2_000),
        book(sequence=3, bid="101.0", ask="102.0", available_offset_ms=7_000),
    )
    rows = measure_reference_lag_grid(impulse, books)
    assert [row.status for row in rows] == [HorizonStatus.MEASURED] * 4
    assert rows[0].midpoint_change == Decimal("0.5")
    assert rows[1].midpoint_change == Decimal("0.5")
    assert rows[2].midpoint_change == Decimal("0.5")
    assert rows[3].midpoint_change == Decimal("1.0")
    assert rows[0].bid_change == rows[0].ask_change == Decimal("0.5")
    assert not hasattr(rows[0], "pnl")
    assert not hasattr(rows[0], "fee")
    assert not hasattr(rows[0], "order")


def test_no_baseline_and_stream_boundaries_are_explicit_abstentions() -> None:
    previous = state(reference="100", source_offset_ms=100, available_offset_ms=120)
    current = state(reference="99", source_offset_ms=1_100, available_offset_ms=1_120)
    impulse = build_reference_impulse(previous, current)
    assert impulse is not None

    no_baseline = measure_reference_lag_horizon(
        impulse,
        [book(sequence=1, bid="99", ask="100", available_offset_ms=1_500)],
        horizon_ms=1_000,
    )
    assert no_baseline.status is HorizonStatus.NO_BASELINE_BOOK
    assert no_baseline.midpoint_change is None

    reconnect = measure_reference_lag_horizon(
        impulse,
        [
            book(sequence=1, bid="100", ask="101", available_offset_ms=1_000),
            book(
                sequence=1,
                bid="99",
                ask="100",
                available_offset_ms=1_500,
                epoch=OTHER_EPOCH,
            ),
        ],
        horizon_ms=1_000,
    )
    assert reconnect.status is HorizonStatus.STREAM_BOUNDARY_WITHIN_HORIZON
    assert reconnect.midpoint_change is None

    resubscribed = measure_reference_lag_horizon(
        impulse,
        [
            book(sequence=1, bid="100", ask="101", available_offset_ms=1_000),
            book(
                sequence=1,
                bid="99",
                ask="100",
                available_offset_ms=1_500,
                sid=9,
            ),
        ],
        horizon_ms=1_000,
    )
    assert resubscribed.status is HorizonStatus.STREAM_BOUNDARY_WITHIN_HORIZON
    assert resubscribed.midpoint_change is None


def test_stale_baseline_and_horizon_are_explicit_abstentions() -> None:
    previous = state(reference="100", source_offset_ms=100, available_offset_ms=40_120)
    current = state(reference="101", source_offset_ms=41_100, available_offset_ms=41_120)
    impulse = build_reference_impulse(previous, current)
    assert impulse is not None

    stale_baseline = measure_reference_lag_horizon(
        impulse,
        [book(sequence=1, bid="100", ask="101", available_offset_ms=10_000)],
        horizon_ms=1_000,
    )
    assert stale_baseline.status is HorizonStatus.STALE_BASELINE_BOOK
    assert stale_baseline.midpoint_change is None

    fresh_previous = state(reference="100", source_offset_ms=100, available_offset_ms=120)
    fresh_current = state(reference="101", source_offset_ms=1_100, available_offset_ms=1_120)
    fresh_impulse = build_reference_impulse(fresh_previous, fresh_current)
    assert fresh_impulse is not None
    stale_horizon = measure_reference_lag_horizon(
        fresh_impulse,
        [book(sequence=1, bid="100", ask="101", available_offset_ms=1_000)],
        horizon_ms=10_000,
    )
    assert stale_horizon.status is HorizonStatus.MEASURED

    old_but_valid_impulse = build_reference_impulse(
        state(reference="101", source_offset_ms=30_100, available_offset_ms=30_120),
        state(reference="102", source_offset_ms=31_100, available_offset_ms=31_120),
    )
    assert old_but_valid_impulse is not None
    horizon_row = measure_reference_lag_horizon(
        old_but_valid_impulse,
        [book(sequence=1, bid="100", ask="101", available_offset_ms=7_000)],
        horizon_ms=10_000,
    )
    assert horizon_row.status is HorizonStatus.STALE_HORIZON_BOOK


def test_book_contract_mismatch_and_unfrozen_horizon_fail_closed() -> None:
    previous = state(reference="100", source_offset_ms=100, available_offset_ms=120)
    current = state(reference="101", source_offset_ms=1_100, available_offset_ms=1_120)
    impulse = build_reference_impulse(previous, current)
    assert impulse is not None

    with pytest.raises(ShadowResearchError, match="contract identity"):
        measure_reference_lag_horizon(
            impulse,
            [
                book(
                    sequence=1,
                    bid="100",
                    ask="101",
                    available_offset_ms=1_000,
                    market_obj=market(market_version=8),
                )
            ],
            horizon_ms=1_000,
        )
    with pytest.raises(ShadowResearchError, match="outside frozen"):
        measure_reference_lag_horizon(
            impulse,
            [book(sequence=1, bid="100", ask="101", available_offset_ms=1_000)],
            horizon_ms=3_000,
        )


def test_book_sequence_regression_within_same_stream_fails_closed() -> None:
    previous = state(reference="100", source_offset_ms=100, available_offset_ms=120)
    current = state(reference="101", source_offset_ms=1_100, available_offset_ms=1_120)
    impulse = build_reference_impulse(previous, current)
    assert impulse is not None

    with pytest.raises(ShadowResearchError, match="sequence regressed"):
        measure_reference_lag_horizon(
            impulse,
            [
                book(sequence=5, bid="100", ask="101", available_offset_ms=1_000),
                book(sequence=4, bid="100.5", ask="101.5", available_offset_ms=1_500),
            ],
            horizon_ms=1_000,
        )
