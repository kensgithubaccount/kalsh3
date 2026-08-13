from __future__ import annotations

import base64
import subprocess
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from services.kalshi_account_gateway.auth import RequestSigner
from services.market_universe.domain import UniverseValidationError
from services.real_time_market_data.archive import ArchiveBuffer, RawRecord, replay
from services.real_time_market_data.events import (
    BookDeltaEvent,
    BookSnapshotEvent,
    LifecycleEvent,
    TickerEvent,
    TradeEvent,
)
from services.real_time_market_data.integration import CanonicalAction, LifecycleIntegrator
from services.real_time_market_data.manager import RealtimeState, SubscriptionManager
from services.real_time_market_data.orderbook import BookState, PriceMode, SequencedBook
from services.real_time_market_data.protocol import (
    Channel,
    ProtocolError,
    ProtocolState,
    websocket_headers,
)

NOW = datetime(2026, 4, 17, tzinfo=UTC)
EPOCH = UUID("00000000-0000-0000-0000-000000000001")


def test_exact_websocket_auth_path_and_read_signer_boundary(tmp_path: Path) -> None:
    key = tmp_path / "key"
    subprocess.run(
        [
            "/usr/bin/openssl",
            "genpkey",
            "-algorithm",
            "RSA",
            "-pkeyopt",
            "rsa_keygen_bits:2048",
            "-out",
            str(key),
        ],
        check=True,
        capture_output=True,
    )
    signer = RequestSigner("read-only-id", key.read_bytes())
    headers = websocket_headers(signer, 123)
    assert (
        set(headers) == {"KALSHI-ACCESS-KEY", "KALSHI-ACCESS-TIMESTAMP", "KALSHI-ACCESS-SIGNATURE"}
        and headers["KALSHI-ACCESS-KEY"] == "read-only-id"
        and base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    )
    assert "write" not in repr(signer).lower()


def test_subscription_commands_responses_and_sid_command_separation() -> None:
    state = ProtocolState(EPOCH)
    command = state.subscribe(Channel.ORDERBOOK, ("A", "B"))
    assert command["params"]["use_yes_price"] is True and command["params"]["market_tickers"] == [
        "A",
        "B",
    ]
    subscription = state.response({"id": command["id"], "type": "subscribed", "msg": {"sid": 91}})
    assert subscription and subscription.sid == 91 and subscription.command_id != 91
    add = state.update(91, "add_markets", ("C",))
    assert add["params"]["action"] == "add_markets"
    state.response({"id": add["id"], "type": "ok"})
    delete = state.update(91, "delete_markets", ("B",))
    state.response({"id": delete["id"], "type": "ok"})
    snapshot = state.update(91, "get_snapshot", ("A",))
    assert snapshot["params"]["action"] == "get_snapshot"
    assert (
        state.unsubscribe(91)["cmd"] == "unsubscribe"
        and state.list_subscriptions()["cmd"] == "list_subscriptions"
    )
    error = state.subscribe(Channel.TICKER)
    state.response({"id": error["id"], "type": "error", "msg": {"msg": "duplicate subscription"}})
    assert state.errors == ["duplicate subscription"]
    with pytest.raises(ProtocolError):
        state.response({"id": 999, "type": "ok"})


def snapshot(seq: int = 1, sid: int = 7) -> dict[str, object]:
    return {
        "type": "orderbook_snapshot",
        "sid": sid,
        "seq": seq,
        "msg": {
            "market_ticker": "M",
            "market_id": "mid",
            "yes_dollars_fp": [["0.400", "2.25"]],
            "no_dollars_fp": [["0.450", "3.50"]],
        },
    }


def delta(seq: int, delta_fp: str = "-2.25", side: str = "yes") -> dict[str, object]:
    return {
        "type": "orderbook_delta",
        "sid": 7,
        "seq": seq,
        "msg": {
            "market_ticker": "M",
            "market_id": "mid",
            "price_dollars": "0.400",
            "delta_fp": delta_fp,
            "side": side,
            "ts_ms": 1776384000123,
        },
    }


def test_unified_yes_mode_no_double_complement_and_fractional_depth() -> None:
    event = BookSnapshotEvent.parse(snapshot(), PriceMode.UNIFIED_YES)
    manager = SubscriptionManager()
    manager.new_epoch()
    manager.install_snapshot(event, NOW, "rules")
    view = manager.books["M"].view(NOW)
    assert (
        view.best_yes_ask == Decimal("0.450")
        and view.best_ask_size == Decimal("3.50")
        and view.best_yes_bid == Decimal("0.400")
    )
    assert manager.apply_delta(BookDeltaEvent.parse(delta(2)), NOW)
    assert not manager.books["M"].yes
    bad = BookDeltaEvent.parse(delta(3, "-1"))
    assert not manager.apply_delta(bad, NOW) and manager.state == RealtimeState.GAP_DETECTED


def test_sid_sequence_gap_duplicate_recovery_and_new_epoch() -> None:
    manager = SubscriptionManager()
    first = manager.new_epoch()
    manager.install_snapshot(BookSnapshotEvent.parse(snapshot(10), PriceMode.UNIFIED_YES), NOW, "v")
    assert not manager.apply_delta(BookDeltaEvent.parse(delta(12, "1")), NOW)
    assert manager.gaps[-1].expected == 11 and manager.books["M"].state == BookState.GAP
    command = manager.recovery_command(7)
    assert command["params"]["action"] == "get_snapshot"
    manager.install_snapshot(BookSnapshotEvent.parse(snapshot(20), PriceMode.UNIFIED_YES), NOW, "v")
    assert manager.books["M"].state == BookState.CURRENT and manager.gaps[-1].recovered_at == NOW
    manager.disconnected()
    second = manager.new_epoch()
    assert second != first and manager.books["M"].state == BookState.STALE and not manager.last_seq


def test_backpressure_and_liveness_never_silently_healthy() -> None:
    manager = SubscriptionManager(queue_limit=1)
    manager.new_epoch()
    assert manager.enqueue({"sid": 7, "seq": 1}, NOW)
    assert not manager.enqueue({"sid": 7, "seq": 2}, NOW)
    assert manager.state == RealtimeState.BACKPRESSURED and manager.gaps
    assert not manager.liveness.stalled(NOW + timedelta(seconds=29)) and manager.liveness.stalled(
        NOW + timedelta(seconds=31)
    )


def test_ticker_trade_fixed_point_direction_and_timestamp() -> None:
    ticker = {
        "type": "ticker",
        "sid": 1,
        "seq": 2,
        "msg": {
            "market_ticker": "M",
            "price_dollars": "0.50",
            "yes_bid_dollars": "0.49",
            "yes_ask_dollars": "0.51",
            "volume_fp": "100.25",
            "open_interest_fp": "50.00",
            "yes_bid_size_fp": "2.25",
            "yes_ask_size_fp": "3.50",
            "last_trade_size_fp": "0.10",
            "ts_ms": 1776384000123,
        },
    }
    parsed = TickerEvent.parse(ticker)
    assert parsed.yes_bid_size == Decimal("2.25") and parsed.exchange_at.microsecond == 123000
    ticker["msg"]["yes_bid_size_fp"] = "9.00"  # type: ignore[index]
    assert TickerEvent.parse(ticker).yes_bid_size == Decimal("9.00")
    trade = {
        "type": "trade",
        "sid": 2,
        "seq": 3,
        "msg": {
            "trade_id": "T",
            "market_ticker": "M",
            "yes_price_dollars": "0.620",
            "no_price_dollars": "0.380",
            "count_fp": "1.25",
            "taker_outcome_side": "yes",
            "taker_book_side": "ask",
            "taker_side": "legacy-ignored",
            "ts_ms": 1776384000123,
        },
    }
    parsed_trade = TradeEvent.parse(trade)
    assert parsed_trade.count == Decimal("1.25") and parsed_trade.taker_outcome_side.value == "yes"


def lifecycle(
    kind: str, channel: str = "market_lifecycle_v2", **extra: object
) -> dict[str, object]:
    msg = {"market_ticker": "M", "event_type": kind, "ts_ms": 1776384000123} | extra
    return {"type": channel, "sid": 3, "seq": 4, "msg": msg}


def test_all_lifecycle_actions_mve_and_no_obsolete_fractional_event() -> None:
    integrator = LifecycleIntegrator()
    books = {"M": SequencedBook("M")}
    books["M"].snapshot(1, [], [], NOW, "v")
    expected = {
        "created": CanonicalAction.DISCOVER,
        "activated": CanonicalAction.REFRESH_METADATA,
        "deactivated": CanonicalAction.MARK_INACTIVE,
        "close_date_updated": CanonicalAction.REFRESH_METADATA,
        "determined": CanonicalAction.RECORD_DETERMINATION,
        "settled": CanonicalAction.RECONCILE_FINALIZED,
        "metadata_updated": CanonicalAction.REFRESH_METADATA,
        "price_level_structure_updated": CanonicalAction.INVALIDATE_BOOK,
    }
    for kind, action in expected.items():
        extra = {"settlement_value": "0.75", "result": "yes"} if kind == "determined" else {}
        event = LifecycleEvent.parse(lifecycle(kind, **extra))
        assert action in integrator.accept(event, NOW, books)
    settled = LifecycleEvent.parse(lifecycle("settled"))
    assert settled.rest_status_hint == "finalized"
    mve = LifecycleEvent.parse(lifecycle("created", "multivariate_market_lifecycle"))
    assert mve.is_mve
    with pytest.raises(UniverseValidationError):
        LifecycleEvent.parse(lifecycle("fractional_trading_updated"))
    job = integrator.jobs[0]
    assert integrator.retry_created_404(job, NOW) is not None


def test_raw_archive_integrity_timestamps_gap_and_deterministic_replay() -> None:
    buffer = ArchiveBuffer(max_events=2)
    one = RawRecord.create(EPOCH, "ticker", 1, 1, NOW, 100, {"type": "ticker"})
    assert buffer.append(one) is None
    two = RawRecord.create(
        EPOCH, "gap", 1, 3, NOW, 200, {"expected": 2, "actual": 3}, gap_marker=True
    )
    batch = buffer.append(two)
    assert batch and batch.gap_markers == 1
    records = replay(batch)
    assert (
        records[0]["epoch"] == str(EPOCH)
        and records[1]["gap_marker"] is True
        and records[0]["receive_monotonic_ns"] == 100
    )
    with pytest.raises((OSError, ValueError)):
        replay(type(batch)(batch.compressed + b"x", batch.sha256, 2, 1))


def test_selective_depth_for_3000_markets_is_bounded() -> None:
    manager = SubscriptionManager(max_depth_markets=250)
    manager.new_epoch()
    manager.set_depth_watch({f"M{i}" for i in range(3000)})
    commands = manager.depth_commands(None)
    assert len(manager.desired_depth) == 250 and len(commands[0]["params"]["market_tickers"]) == 250
