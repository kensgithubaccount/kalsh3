from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from services.perps_shadow_research.domain import ShadowResearchError
from services.perps_shadow_research.margin_protocol import MarginChannel, MarginProtocolState
from services.perps_shadow_research.perps_events import (
    LastUpdateReason,
    PerpsBookDeltaEvent,
    PerpsBookSnapshotEvent,
    PerpsTickerEvent,
)
from services.perps_shadow_research.perps_evidence import (
    PerpsMarketStateObservation,
    perps_book_fingerprint,
    perps_ticker_fingerprint,
)
from services.perps_shadow_research.perps_metadata import parse_perps_market

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


def market_raw(**changes: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "ticker": "BTC-PERP",
        "status": "active",
        "title": "Bitcoin Perpetual",
        "exchange_index": 4,
        "contract_size": "0.001000",
        "tick_size": "0.500000",
        "fractional_trading_enabled": True,
        "schedule": None,
        "leverage_estimate": Decimal("9.125"),
        "leverage_estimates": {"1000": Decimal("9.1")},
        "long_leverage_estimates": {"1000": Decimal("8.9")},
        "short_leverage_estimates": {"1000": Decimal("8.7")},
        "bid": "60000.500000",
        "ask": "60001.000000",
        "price": "60000.750000",
        "settlement_mark_price": {"price": "60000.25", "ts_ms": 1},
        "liquidation_mark_price": {"price": "60000.50", "ts_ms": 2},
        "reference_price": {"price": "60000.75", "ts_ms": 3},
        "volume": "123.45",
        "open_interest": "67.89",
    }
    raw.update(changes)
    return raw


def market(**changes: object):
    return parse_perps_market(market_raw(**changes), observed_at=NOW)


def snapshot(**msg_changes: object) -> dict[str, object]:
    msg: dict[str, object] = {
        "market_ticker": "BTC-PERP",
        "bid": [["60000.500000", "2.50"]],
        "ask": [["60001.000000", "3.25"]],
    }
    msg.update(msg_changes)
    return {"type": "orderbook_snapshot", "sid": 7, "seq": 1, "msg": msg}


def delta(**msg_changes: object) -> dict[str, object]:
    msg: dict[str, object] = {
        "market_ticker": "BTC-PERP",
        "price": "60000.500000",
        "delta": "1.25",
        "side": "bid",
    }
    msg.update(msg_changes)
    return {"type": "orderbook_delta", "sid": 7, "seq": 2, "msg": msg}


def test_market_metadata_exactness_hashes_and_perps_boundary() -> None:
    item = market()
    assert item.exchange_index == 4
    assert item.contract_size == Decimal("0.001000")
    assert item.tick_size == Decimal("0.500000")
    assert item.long_leverage_estimates != item.short_leverage_estimates
    assert item.reference_price and item.reference_price.ts_ms == 3
    assert item.schedule is None and item.is_open()
    assert not hasattr(item, "market_id")
    assert not hasattr(item, "price_level_structure")
    assert not hasattr(item, "price_ranges")
    assert item.source_provenance.source_url.endswith("perps_openapi.yaml")
    assert len(item.source_provenance.sha256) == 64

    volatile = market(bid="61000", ask="61001", volume="999", open_interest="888")
    assert volatile.perps_contract_hash == item.perps_contract_hash
    assert volatile.market_metadata_hash != item.market_metadata_hash
    reordered = parse_perps_market(dict(reversed(list(market_raw().items()))), observed_at=NOW)
    assert reordered.perps_contract_hash == item.perps_contract_hash
    assert reordered.market_metadata_hash == item.market_metadata_hash
    assert market(tick_size="1.0").perps_contract_hash != item.perps_contract_hash


@pytest.mark.parametrize("fractional", [False, True])
def test_book_quantity_uses_fixed_point_granularity_not_fractional_flag(
    fractional: bool,
) -> None:
    item = market(fractional_trading_enabled=fractional)
    assert item.quantity_valid(Decimal("1.55"))
    assert item.quantity_valid(Decimal("0.01"))
    assert item.quantity_valid(Decimal("0"))
    assert not item.quantity_valid(Decimal("0.001"))
    assert not item.quantity_valid(Decimal("1.551"))  # exact rejection; never rounded
    assert not item.quantity_valid(Decimal("-0.01"))
    assert not item.quantity_valid(Decimal("NaN"))


@pytest.mark.parametrize(
    "field",
    [
        "ticker",
        "status",
        "title",
        "exchange_index",
        "contract_size",
        "tick_size",
        "fractional_trading_enabled",
        "schedule",
    ],
)
def test_market_required_fields(field: str) -> None:
    raw = market_raw()
    del raw[field]
    with pytest.raises(ShadowResearchError, match="missing"):
        parse_perps_market(raw, observed_at=NOW)


@pytest.mark.parametrize("value", [True, -1, "0", None])
def test_exchange_index_is_exact_and_never_inferred(value: object) -> None:
    with pytest.raises(ShadowResearchError, match="exchange_index"):
        market(exchange_index=value)


def test_schedule_absent_null_and_nested_semantics() -> None:
    with pytest.raises(ShadowResearchError, match="schedule"):
        parse_perps_market(
            {key: value for key, value in market_raw().items() if key != "schedule"},
            observed_at=NOW,
        )
    closed = market(schedule={"is_open": False, "next_open_ts": 10, "next_close_ts": None})
    assert closed.schedule and not closed.is_open()
    with pytest.raises(ShadowResearchError, match="next_open_ts"):
        market(schedule={"is_open": False, "next_close_ts": None})


def test_json_boundary_can_preserve_decimal_numbers() -> None:
    payload = json.loads(json.dumps(market_raw(), default=str), parse_float=Decimal)
    payload["leverage_estimate"] = json.loads("9.1234567890123456789", parse_float=Decimal)
    assert parse_perps_market(payload, observed_at=NOW).leverage_estimate == Decimal(
        "9.1234567890123456789"
    )


def test_snapshot_sides_and_strict_levels() -> None:
    parsed = PerpsBookSnapshotEvent.parse(snapshot(), market())
    assert parsed.bids[0] == (Decimal("60000.500000"), Decimal("2.50"))
    assert PerpsBookSnapshotEvent.parse(snapshot(bid=None), market()).bids == ()
    assert PerpsBookSnapshotEvent.parse(snapshot(bid=None, ask=None), market()).asks == ()
    assert PerpsBookSnapshotEvent.parse(snapshot(bid=[]), market()).bids == ()
    for bad in ([["1"]], [["1", "2", "3"]], [[1, "2"]]):
        with pytest.raises(ShadowResearchError, match="two strings"):
            PerpsBookSnapshotEvent.parse(snapshot(bid=bad), market())
    with pytest.raises(ShadowResearchError, match="duplicate"):
        PerpsBookSnapshotEvent.parse(snapshot(bid=[["1.0", "1"], ["1.00", "2"]]), market())
    with pytest.raises(ShadowResearchError, match="contract"):
        PerpsBookSnapshotEvent.parse(snapshot(bid=[["1.1", "1"]]), market())
    with pytest.raises(ShadowResearchError, match="finite"):
        PerpsBookSnapshotEvent.parse(snapshot(bid=[["NaN", "1"]]), market())
    with pytest.raises(ShadowResearchError, match="contract"):
        PerpsBookSnapshotEvent.parse(snapshot(bid=[["1.0", "0.001"]]), market())


@pytest.mark.parametrize("field,value", [("sid", 0), ("sid", True), ("seq", 0), ("seq", True)])
def test_sid_sequence_contract(field: str, value: object) -> None:
    raw = snapshot()
    raw[field] = value
    with pytest.raises(ShadowResearchError):
        PerpsBookSnapshotEvent.parse(raw, market())


def test_delta_optional_fields_and_account_identifiers_are_nonsemantic() -> None:
    raw = delta(
        last_update_reason="Trade", ts_ms=1_786_622_400_123, client_order_id="secret", subaccount=63
    )
    event = PerpsBookDeltaEvent.parse(raw, market())
    assert event.last_update_reason is LastUpdateReason.TRADE
    assert event.exchange_at and event.exchange_at.microsecond == 123000
    assert event.has_client_order_id and event.has_subaccount
    cleaned = PerpsBookDeltaEvent.parse(
        delta(last_update_reason="Trade", ts_ms=1_786_622_400_123), market()
    )
    assert perps_book_fingerprint(event) == perps_book_fingerprint(cleaned)
    assert "secret" not in perps_book_fingerprint(event)
    assert PerpsBookDeltaEvent.parse(delta(), market()).exchange_at is None
    with pytest.raises(ShadowResearchError, match="side"):
        PerpsBookDeltaEvent.parse(delta(side="yes"), market())
    with pytest.raises(ShadowResearchError, match="last_update_reason"):
        PerpsBookDeltaEvent.parse(delta(last_update_reason="Other"), market())


def ticker() -> dict[str, object]:
    return {
        "type": "ticker",
        "sid": 8,
        "msg": {
            "market_ticker": "BTC-PERP",
            "price": "1.1",
            "bid": "1.0",
            "ask": "1.5",
            "bid_size_fp": "2.25",
            "ask_size_fp": "3.5",
            "last_trade_size_fp": "0.25",
            "volume": "10.5",
            "volume_notional_value_dollars": "20.5",
            "volume_24h": "4.5",
            "volume_24h_notional_value_dollars": "8.5",
            "open_interest": "7.5",
            "open_interest_notional_value_dollars": "14.5",
            "ts_ms": 100,
            "reference_price": {"price": "1.2", "ts_ms": 90},
            "settlement_mark_price": {"price": "1.3", "ts_ms": 91},
            "liquidation_mark_price": {"price": "1.4", "ts_ms": 92},
            "funding_rate": {
                "rate": Decimal("0.000000000123"),
                "next_funding_time_ms": 200,
                "ts_ms": 93,
            },
        },
    }


def test_ticker_exact_fields_independent_timestamps_and_no_sequence() -> None:
    event = PerpsTickerEvent.parse(ticker(), market())
    assert event.funding_rate and event.funding_rate.rate == Decimal("0.000000000123")
    assert event.reference_price and event.reference_price.ts_ms == 90
    assert event.settlement_mark_price and event.settlement_mark_price.ts_ms == 91
    assert event.liquidation_mark_price and event.liquidation_mark_price.ts_ms == 92
    assert not hasattr(event, "sequence")


def test_ticker_source_fingerprint_excludes_local_clocks() -> None:
    event = PerpsTickerEvent.parse(ticker(), market())
    epoch = uuid4()
    first = PerpsMarketStateObservation.create(event, market(), epoch, NOW, NOW)
    later = PerpsMarketStateObservation.create(
        event, market(), epoch, NOW + timedelta(seconds=1), NOW + timedelta(seconds=2)
    )
    assert first.source_fingerprint == later.source_fingerprint == perps_ticker_fingerprint(event)
    assert first.evidence_id != later.evidence_id


def test_margin_protocol_is_minimal_and_ambiguity_fails_closed() -> None:
    protocol = MarginProtocolState(uuid4())
    book = protocol.subscribe(MarginChannel.ORDERBOOK, ("BTC-PERP",))
    ticker_command = protocol.subscribe(MarginChannel.TICKER, ("BTC-PERP",))
    assert book["id"] == 0 and ticker_command["id"] == 1
    assert "use_yes_price" not in str((book, ticker_command))
    assert not hasattr(protocol, "update") and not hasattr(protocol, "get_snapshot")
    assert (
        protocol.subscribed(
            {"type": "subscribed", "id": 0, "msg": {"channel": "orderbook_delta", "sid": 7}}
        ).sid
        == 7
    )
    with pytest.raises(ShadowResearchError):
        protocol.subscribe("fill", ("BTC-PERP",))  # type: ignore[arg-type]
    ambiguous = MarginProtocolState(uuid4())
    ambiguous.subscribe(MarginChannel.TICKER, ("A",))
    ambiguous.subscribe(MarginChannel.TICKER, ("B",))
    with pytest.raises(ShadowResearchError, match="ambiguous"):
        ambiguous.subscribed({"type": "subscribed", "msg": {"channel": "ticker", "sid": 9}})
    with pytest.raises(ShadowResearchError, match="SID"):
        MarginProtocolState(uuid4()).unsubscribe(True)
