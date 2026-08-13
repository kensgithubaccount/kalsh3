from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from services.perps_shadow_research.domain import ShadowResearchError
from services.perps_shadow_research.perps_events import PerpsBookDeltaEvent, PerpsBookSnapshotEvent
from services.perps_shadow_research.perps_metadata import parse_perps_market
from services.perps_shadow_research.perps_orderbook import PerpsBookState, PerpsSequencedBook
from services.perps_shadow_research.perps_runtime import (
    OfflinePerpsEvidenceRuntime,
    PerpsRuntimeState,
    ScriptedPerpsTransport,
)
from services.perps_shadow_research.perps_store import PerpsEvidenceStore
from services.real_time_market_data.transport import ReceivedFrame

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


def market(**changes: object):
    raw: dict[str, object] = {
        "ticker": "BTC-PERP",
        "status": "active",
        "title": "Bitcoin",
        "exchange_index": 4,
        "contract_size": "1.000000",
        "tick_size": "0.50",
        "fractional_trading_enabled": True,
        "schedule": None,
    }
    raw.update(changes)
    return parse_perps_market(raw, observed_at=NOW)


def snapshot(seq: int = 1, **changes: object) -> dict[str, object]:
    msg: dict[str, object] = {
        "market_ticker": "BTC-PERP",
        "bid": [["100.00", "2.00"]],
        "ask": [["101.00", "3.00"]],
    }
    msg.update(changes)
    return {"type": "orderbook_snapshot", "sid": 7, "seq": seq, "msg": msg}


def delta(seq: int = 2, **changes: object) -> dict[str, object]:
    msg: dict[str, object] = {
        "market_ticker": "BTC-PERP",
        "price": "100.00",
        "delta": "1.00",
        "side": "bid",
    }
    msg.update(changes)
    return {"type": "orderbook_delta", "sid": 7, "seq": seq, "msg": msg}


def ticker() -> dict[str, object]:
    return {
        "type": "ticker",
        "sid": 8,
        "msg": {
            "market_ticker": "BTC-PERP",
            "price": "100.5",
            "bid": "100",
            "ask": "101",
            "bid_size_fp": "3",
            "ask_size_fp": "4",
            "last_trade_size_fp": "1",
            "volume": "10",
            "volume_notional_value_dollars": "1000",
            "volume_24h": "5",
            "volume_24h_notional_value_dollars": "500",
            "open_interest": "7",
            "open_interest_notional_value_dollars": "700",
            "ts_ms": 1_786_622_400_000,
            "funding_rate": {"rate": "0.0001", "ts_ms": 1, "next_funding_time_ms": 2},
        },
    }


def frame(payload: object, offset: int = 0) -> ReceivedFrame:
    return ReceivedFrame(json.dumps(payload), NOW + timedelta(milliseconds=offset), 100 + offset)


def app(tmp_path: Path, *, enabled: bool = True) -> OfflinePerpsEvidenceRuntime:
    return OfflinePerpsEvidenceRuntime(
        market(),
        PerpsEvidenceStore(tmp_path / "perps.sqlite3"),
        ScriptedPerpsTransport(),
        lambda: NOW + timedelta(milliseconds=50),
        lambda: 500,
        enabled=enabled,
    )


def connect(runtime: OfflinePerpsEvidenceRuntime):
    epoch = runtime.connect()
    assert epoch
    runtime.process(
        frame({"type": "subscribed", "id": 0, "msg": {"channel": "orderbook_delta", "sid": 7}}),
        connection_epoch=epoch,
    )
    runtime.process(
        frame({"type": "subscribed", "id": 1, "msg": {"channel": "ticker", "sid": 8}}),
        connection_epoch=epoch,
    )
    return epoch


def test_canonical_book_delta_removal_negative_crossed_and_metadata_change() -> None:
    original = market()
    book = PerpsSequencedBook(original)
    book.snapshot(PerpsBookSnapshotEvent.parse(snapshot(), original), NOW)
    view = book.view(NOW)
    assert view.best_bid == Decimal("100.00") and view.best_ask == Decimal("101.00")
    assert book.delta(PerpsBookDeltaEvent.parse(delta(delta="-2.00"), original), NOW)
    assert book.view(NOW).best_bid is None
    book.snapshot(PerpsBookSnapshotEvent.parse(snapshot(), original), NOW)
    assert not book.delta(PerpsBookDeltaEvent.parse(delta(delta="-3.00"), original), NOW)
    assert book.state is PerpsBookState.GAP
    crossed = PerpsSequencedBook(original)
    crossed.snapshot(PerpsBookSnapshotEvent.parse(snapshot(bid=[["102", "1"]]), original), NOW)
    assert crossed.state is PerpsBookState.CROSSED and not crossed.view(NOW).usable
    assert crossed.invalidate_for_metadata(market(tick_size="1.00"))
    assert crossed.state is PerpsBookState.STALE


def test_offline_runtime_snapshot_delta_ticker_and_separate_tables(tmp_path: Path) -> None:
    runtime = app(tmp_path)
    epoch = connect(runtime)
    first = runtime.process(frame(snapshot()), connection_epoch=epoch)
    second = runtime.process(frame(delta(), 1), connection_epoch=epoch)
    state = runtime.process(frame(ticker(), 2), connection_epoch=epoch)
    assert first and second and state
    assert first.best_bid == Decimal("100.00") and second.best_bid_size == Decimal("3.00")
    assert second.exchange_at is None and state.funding_rate == Decimal("0.0001")
    assert runtime.store.count("perps_market_metadata") == 1
    assert runtime.store.count("perps_book_evidence") == 2
    assert runtime.store.count("perps_market_state") == 1
    assert state.production_influence == first.production_influence == Decimal("0")


def test_disabled_old_epoch_and_fresh_snapshot_after_reconnect(tmp_path: Path) -> None:
    disabled = app(tmp_path / "disabled", enabled=False)
    assert disabled.connect() is None and disabled.state is PerpsRuntimeState.STOPPED
    runtime = app(tmp_path)
    first_epoch = connect(runtime)
    assert runtime.process(frame(snapshot()), connection_epoch=first_epoch)
    runtime.disconnect()
    second_epoch = connect(runtime)
    assert second_epoch != first_epoch and runtime.book.state is PerpsBookState.STALE
    snapshot_count = runtime.health().accepted_snapshot_count
    assert runtime.process(frame(snapshot()), connection_epoch=first_epoch) is None
    assert runtime.health().accepted_snapshot_count == snapshot_count
    assert runtime.process(frame(delta()), connection_epoch=first_epoch) is None
    assert runtime.process(frame(delta()), connection_epoch=second_epoch) is None
    assert runtime.state is PerpsRuntimeState.RECONNECT_REQUIRED


def test_fresh_snapshot_counter_advances_with_identical_wall_clock_after_reconnect(
    tmp_path: Path,
) -> None:
    runtime = app(tmp_path)
    first_epoch = connect(runtime)
    assert runtime.process(frame(snapshot()), connection_epoch=first_epoch)
    assert runtime.health().accepted_snapshot_count == 1
    runtime.disconnect()
    second_epoch = connect(runtime)
    assert runtime.process(frame(snapshot()), connection_epoch=second_epoch)
    health = runtime.health()
    assert health.accepted_snapshot_count == 2
    assert health.last_snapshot_at == NOW


def test_replay_collision_and_gap_before_mutation(tmp_path: Path) -> None:
    runtime = app(tmp_path)
    epoch = connect(runtime)
    event = frame(snapshot())
    assert runtime.process(event, connection_epoch=epoch)
    before = (
        runtime.book.sequence,
        runtime.book.bids.copy(),
        runtime.store.count("perps_book_evidence"),
    )
    assert runtime.process(event, connection_epoch=epoch) is None
    assert runtime.health().replay_count == 1
    assert (
        runtime.book.sequence,
        runtime.book.bids,
        runtime.store.count("perps_book_evidence"),
    ) == before
    collision = snapshot(bid=[["99", "1"]])
    assert runtime.process(frame(collision), connection_epoch=epoch) is None
    assert runtime.state is PerpsRuntimeState.QUARANTINED
    assert runtime.health().collision_count == 1
    assert (
        runtime.book.sequence,
        runtime.book.bids,
        runtime.store.count("perps_book_evidence"),
    ) == before

    gapped = app(tmp_path / "gap")
    gap_epoch = connect(gapped)
    assert gapped.process(frame(snapshot()), connection_epoch=gap_epoch)
    assert gapped.process(frame(delta(3)), connection_epoch=gap_epoch) is None
    assert gapped.state is PerpsRuntimeState.RECONNECT_REQUIRED
    assert gapped.book.state is PerpsBookState.GAP
    assert gapped.store.count("perps_book_evidence") == 1
    assert "get_snapshot" not in str(gapped.transport.sent)


def test_ticker_source_replay_identity_and_epoch_scope(tmp_path: Path) -> None:
    runtime = app(tmp_path)
    first_epoch = connect(runtime)
    source = ticker()
    first = runtime.process(frame(source), connection_epoch=first_epoch)
    assert first
    last_ticker = runtime.health().last_ticker_at

    # Local receipt and availability times are not source identity.
    runtime.clock = lambda: NOW + timedelta(seconds=2)
    assert runtime.process(frame(source, 1000), connection_epoch=first_epoch) is None
    assert runtime.store.count("perps_market_state") == 1
    assert runtime.health().replay_count == 1
    assert runtime.health().rows_written == 1
    assert runtime.health().last_ticker_at == last_ticker

    # The exchange timestamp is coalesced state, not a sequence or unique identity.
    changed = ticker()
    assert isinstance(changed["msg"], dict)
    changed["msg"]["bid_size_fp"] = "3.01"
    second = runtime.process(frame(changed, 1000), connection_epoch=first_epoch)
    assert second
    assert runtime.store.count("perps_market_state") == 2
    assert runtime.health().collision_count == 0

    # A distinct source state at the exact same local clock instant is also distinct.
    changed_again = ticker()
    assert isinstance(changed_again["msg"], dict)
    changed_again["msg"]["ask_size_fp"] = "4.01"
    third = runtime.process(frame(changed_again, 1000), connection_epoch=first_epoch)
    assert third
    assert runtime.store.count("perps_market_state") == 3

    runtime.disconnect()
    second_epoch = connect(runtime)
    assert second_epoch != first_epoch
    assert runtime.process(frame(source, 1000), connection_epoch=second_epoch)
    assert runtime.store.count("perps_market_state") == 4


def test_crossed_book_forces_reconnect_without_repair_or_evidence(tmp_path: Path) -> None:
    runtime = app(tmp_path)
    epoch = connect(runtime)
    crossed = snapshot(bid=[["102", "1"]])
    assert runtime.process(frame(crossed), connection_epoch=epoch) is None
    assert runtime.book.bids == {Decimal("102"): Decimal("1")}
    assert runtime.book.asks == {Decimal("101.00"): Decimal("3.00")}
    assert runtime.book.state is PerpsBookState.CROSSED
    assert runtime.store.count("perps_book_evidence") == 0
    assert runtime.state is PerpsRuntimeState.RECONNECT_REQUIRED
    assert runtime.health().crossed_count == 1
    assert runtime.health().last_snapshot_at is None
    assert runtime.process(frame(delta()), connection_epoch=epoch) is None
    assert runtime.store.count("perps_book_evidence") == 0

    runtime.disconnect()
    new_epoch = connect(runtime)
    assert new_epoch != epoch and runtime.book.state is PerpsBookState.STALE
    assert runtime.process(frame(delta()), connection_epoch=new_epoch) is None
    assert runtime.state is PerpsRuntimeState.RECONNECT_REQUIRED


def test_contiguous_crossing_delta_forces_reconnect_and_preserves_source(tmp_path: Path) -> None:
    runtime = app(tmp_path)
    epoch = connect(runtime)
    assert runtime.process(frame(snapshot()), connection_epoch=epoch)
    crossing = delta(price="102", delta="1", side="bid")
    assert runtime.process(frame(crossing, 1), connection_epoch=epoch) is None
    assert runtime.book.bids[Decimal("102")] == Decimal("1")
    assert runtime.book.asks[Decimal("101.00")] == Decimal("3.00")
    health = runtime.health()
    assert health.state is PerpsRuntimeState.RECONNECT_REQUIRED
    assert health.crossed_count == 1
    assert health.last_delta_at is None
    assert runtime.store.count("perps_book_evidence") == 1


def test_stale_and_empty_books_never_claim_healthy_evidence(tmp_path: Path) -> None:
    stale = app(tmp_path / "stale")
    stale.clock = lambda: NOW + timedelta(seconds=31)
    stale_epoch = connect(stale)
    assert stale.process(frame(snapshot()), connection_epoch=stale_epoch) is None
    assert stale.state is PerpsRuntimeState.RECONNECT_REQUIRED
    assert stale.book.state is PerpsBookState.STALE
    assert stale.health().stale_count == 1
    assert stale.health().last_snapshot_at is None
    assert stale.store.count("perps_book_evidence") == 0

    stale_after_healthy = app(tmp_path / "stale-after-healthy")
    healthy_epoch = connect(stale_after_healthy)
    assert stale_after_healthy.process(frame(snapshot()), connection_epoch=healthy_epoch)
    assert stale_after_healthy.state is PerpsRuntimeState.HEALTHY
    stale_after_healthy.clock = lambda: NOW + timedelta(seconds=31)
    assert stale_after_healthy.process(frame(delta(), 1), connection_epoch=healthy_epoch) is None
    assert stale_after_healthy.state is PerpsRuntimeState.RECONNECT_REQUIRED
    assert stale_after_healthy.health().stale_count == 1
    assert stale_after_healthy.health().last_delta_at is None
    assert stale_after_healthy.store.count("perps_book_evidence") == 1

    empty = app(tmp_path / "empty")
    empty_epoch = connect(empty)
    assert empty.process(frame(snapshot(bid=[], ask=[])), connection_epoch=empty_epoch) is None
    assert empty.book.state is PerpsBookState.CURRENT
    assert empty.state is PerpsRuntimeState.SUBSCRIBING
    assert empty.health().last_snapshot_at is None
    assert empty.store.count("perps_book_evidence") == 0
    assert empty.process(frame(delta(), 1), connection_epoch=empty_epoch)
    assert empty.state is PerpsRuntimeState.HEALTHY
    assert empty.health().last_delta_at == NOW + timedelta(milliseconds=1)


def test_delta_before_snapshot_and_structural_metadata_change(tmp_path: Path) -> None:
    runtime = app(tmp_path)
    epoch = connect(runtime)
    assert runtime.process(frame(delta(1)), connection_epoch=epoch) is None
    assert runtime.state is PerpsRuntimeState.RECONNECT_REQUIRED
    changed = app(tmp_path / "metadata")
    changed_epoch = connect(changed)
    assert changed.process(frame(snapshot()), connection_epoch=changed_epoch)
    assert changed.update_metadata(market(contract_size="2.0"))
    assert changed.state is PerpsRuntimeState.RECONNECT_REQUIRED
    assert changed.book.state is PerpsBookState.STALE


def test_persistence_failure_quarantines_after_book_mutation(tmp_path: Path) -> None:
    runtime = app(tmp_path)
    epoch = connect(runtime)
    original = runtime.store.append_book

    def fail(_: object) -> bool:
        raise ShadowResearchError("Perps book evidence persistence rejected")

    runtime.store.append_book = fail  # type: ignore[method-assign]
    assert runtime.process(frame(snapshot()), connection_epoch=epoch) is None
    assert runtime.state is PerpsRuntimeState.QUARANTINED
    assert runtime.health().persistence_failure_count == 1
    runtime.store.append_book = original  # type: ignore[method-assign]


def test_store_append_only_zero_influence_no_sensitive_schema_and_concurrency(
    tmp_path: Path,
) -> None:
    runtime = app(tmp_path)
    epoch = connect(runtime)
    item = runtime.process(frame(snapshot()), connection_epoch=epoch)
    assert item
    assert runtime.process(frame(ticker()), connection_epoch=epoch)
    store = runtime.store
    with sqlite3.connect(store.path) as db:
        schemas = " ".join(
            row[0]
            for row in db.execute("SELECT sql FROM sqlite_master WHERE type='table'")
            if row[0]
        )
        assert "client_order_id" not in schemas and "subaccount" not in schemas
        for table in store.TABLES:
            with pytest.raises(sqlite3.IntegrityError, match="append only"):
                db.execute(
                    f"UPDATE {table} SET production_influence='0'"  # noqa: S608
                )
        columns = [row[1] for row in db.execute("PRAGMA table_info(perps_book_evidence)")]
        values = [None] * len(columns)
        values[columns.index("evidence_id")] = "bad"
        values[columns.index("update_kind")] = "SNAPSHOT"
        values[columns.index("ticker")] = "X"
        values[columns.index("exchange_index")] = 0
        values[columns.index("connection_epoch")] = "x"
        values[columns.index("sid")] = 1
        values[columns.index("sequence")] = 1
        values[columns.index("source_event_fingerprint")] = "0" * 64
        values[columns.index("book_state")] = "CURRENT"
        values[columns.index("received_at")] = values[columns.index("available_at")] = (
            NOW.isoformat()
        )
        values[columns.index("tick_size")] = values[columns.index("contract_size")] = "1"
        values[columns.index("fractional_trading_enabled")] = 1
        values[columns.index("perps_contract_hash")] = values[
            columns.index("market_metadata_hash")
        ] = values[columns.index("full_book_hash")] = "0" * 64
        values[columns.index("production_influence")] = "0.1"
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                f"INSERT INTO perps_book_evidence VALUES "  # noqa: S608
                f"({','.join('?' for _ in values)})",
                values,
            )
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: store.append_book(item), range(8)))
    assert results == [False] * 8


def test_m25b1_has_no_network_or_execution_capability() -> None:
    package = Path(__file__).parents[1] / "services/perps_shadow_research"
    m25b1_modules = (
        "book_evidence.py",
        "domain.py",
        "margin_protocol.py",
        "perps_events.py",
        "perps_evidence.py",
        "perps_metadata.py",
        "perps_orderbook.py",
        "perps_runtime.py",
        "perps_store.py",
        "pipeline.py",
        "runtime.py",
        "store.py",
    )
    sources = "\n".join((package / name).read_text() for name in m25b1_modules)
    forbidden = (
        "import requests",
        "import httpx",
        "import websockets",
        "urllib.request",
        "production_execution",
        "demo_execution",
        "risk_engine",
        "services.learning",
        "supervised_canary",
        "bounded_autonomy",
        "place_order",
        "create_order",
        "cancel_order",
        "amend_order",
        "transfer_funds",
        "credential_loader",
    )
    assert all(term not in sources.lower() for term in forbidden)
