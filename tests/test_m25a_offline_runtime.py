from __future__ import annotations

import ast
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from services.perps_shadow_research.domain import ShadowResearchError
from services.perps_shadow_research.runtime import (
    MarketSpec,
    OfflineReadOnlyEvidenceRuntime,
    RuntimeState,
    ScriptedTransport,
)
from services.perps_shadow_research.store import BookEvidenceStore
from services.real_time_market_data.orderbook import BookState, PriceMode
from services.real_time_market_data.transport import ReceivedFrame

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


def frame(payload: object, offset: int = 0) -> ReceivedFrame:
    return ReceivedFrame(json.dumps(payload), NOW + timedelta(milliseconds=offset), 100 + offset)


def snapshot(seq: int = 1) -> dict[str, object]:
    return {
        "type": "orderbook_snapshot",
        "sid": 7,
        "seq": seq,
        "msg": {
            "market_ticker": "TEST-PERP",
            "market_id": "market-id",
            "yes_dollars_fp": [["0.40", "2"]],
            "no_dollars_fp": [["0.45", "3"]],
        },
    }


def delta(seq: int) -> dict[str, object]:
    return {
        "type": "orderbook_delta",
        "sid": 7,
        "seq": seq,
        "msg": {
            "market_ticker": "TEST-PERP",
            "market_id": "market-id",
            "price_dollars": "0.40",
            "delta_fp": "1",
            "side": "yes",
            "ts_ms": 1786622400000,
        },
    }


def runtime(tmp_path: Path, *, enabled: bool = True) -> OfflineReadOnlyEvidenceRuntime:
    return OfflineReadOnlyEvidenceRuntime(
        [MarketSpec("TEST-PERP", 4, "structure-v1")],
        BookEvidenceStore(tmp_path / "evidence.sqlite3"),
        ScriptedTransport(),
        lambda: NOW + timedelta(milliseconds=50),
        lambda: 500,
        enabled=enabled,
    )


def subscribe(app: OfflineReadOnlyEvidenceRuntime) -> None:
    epoch = app.connect()
    assert epoch is not None
    app.process(
        frame({"id": 1, "type": "subscribed", "msg": {"sid": 7}}),
        connection_epoch=epoch,
    )


def test_disabled_by_default_and_strict_market_specs(tmp_path: Path) -> None:
    disabled = OfflineReadOnlyEvidenceRuntime(
        [MarketSpec("A", 1, "h")],
        BookEvidenceStore(tmp_path / "disabled.sqlite3"),
        ScriptedTransport(),
        lambda: NOW,
        lambda: 1,
    )
    assert disabled.connect() is None and disabled.health().state is RuntimeState.STOPPED
    with pytest.raises(ShadowResearchError, match="duplicate or ambiguous"):
        OfflineReadOnlyEvidenceRuntime(
            [MarketSpec("A", 1, "h"), MarketSpec("B", 1, "h")],
            BookEvidenceStore(tmp_path / "duplicate.sqlite3"),
            ScriptedTransport(),
            lambda: NOW,
            lambda: 1,
        )
    with pytest.raises(ShadowResearchError, match="exceed"):
        OfflineReadOnlyEvidenceRuntime(
            [MarketSpec("A", 1, "h"), MarketSpec("B", 2, "h")],
            BookEvidenceStore(tmp_path / "too-many.sqlite3"),
            ScriptedTransport(),
            lambda: NOW,
            lambda: 1,
            max_depth_markets=1,
        )


def test_connection_epoch_subscription_and_old_frame_isolation(tmp_path: Path) -> None:
    app = runtime(tmp_path)
    first = app.connect()
    assert first and first.int and app.price_mode is PriceMode.UNIFIED_YES
    command = app._transport.sent[-1]
    assert command["params"]["use_yes_price"] is True
    app.process(frame({"id": 1, "type": "subscribed", "msg": {"sid": 7}}), connection_epoch=first)
    assert app.process(frame(snapshot()), connection_epoch=first) is not None
    app.disconnect()
    assert app.manager.books["TEST-PERP"].state is BookState.STALE
    second = app.connect()
    assert second and second != first
    assert app.manager.desired_depth == {"TEST-PERP"}
    assert not app.manager.subscribed_depth and not app.manager.sid_markets
    assert app.process(frame(delta(2)), connection_epoch=first) is None
    assert app.manager.books["TEST-PERP"].state is BookState.STALE


def test_gap_has_no_evidence_and_one_recovery_until_snapshot(tmp_path: Path) -> None:
    app = runtime(tmp_path)
    subscribe(app)
    epoch = app.health().epoch
    assert epoch and app.process(frame(snapshot()), connection_epoch=epoch)
    assert app.process(frame(delta(3), 1), connection_epoch=epoch) is None
    assert app.manager.books["TEST-PERP"].state is BookState.GAP
    assert app._store.count() == 1
    assert app._transport.sent[-1]["params"]["action"] == "get_snapshot"
    sent = len(app._transport.sent)
    assert app.process(frame(delta(4), 2), connection_epoch=epoch) is None
    assert len(app._transport.sent) == sent
    assert app.process(frame(snapshot(10), 3), connection_epoch=epoch)
    assert app.manager.books["TEST-PERP"].state is BookState.CURRENT
    health = app.health()
    assert health.gap_count == 2 and health.resnapshot_count == 1


def test_malformed_unknown_unexpected_and_stale_fail_closed(tmp_path: Path) -> None:
    app = runtime(tmp_path)
    subscribe(app)
    epoch = app.health().epoch
    assert epoch
    app.process(ReceivedFrame("not json", NOW, 1), connection_epoch=epoch)
    assert app.health().state is RuntimeState.QUARANTINED

    app2 = runtime(tmp_path / "two")
    subscribe(app2)
    epoch2 = app2.health().epoch
    assert epoch2
    app2.process(frame({"type": "trade"}), connection_epoch=epoch2)
    assert app2.health().state is RuntimeState.QUARANTINED

    app3 = runtime(tmp_path / "three")
    subscribe(app3)
    epoch3 = app3.health().epoch
    assert epoch3 and app3.process(frame(snapshot()), connection_epoch=epoch3)
    app3._clock = lambda: NOW + timedelta(seconds=31)
    assert not app3.check_freshness()
    assert app3.health().stale_count == 1


def test_persistence_failure_after_mutation_quarantines(tmp_path: Path, monkeypatch) -> None:
    app = runtime(tmp_path)
    subscribe(app)
    epoch = app.health().epoch
    assert epoch

    def fail(_observation) -> bool:
        raise ShadowResearchError("book evidence persistence rejected")

    monkeypatch.setattr(app._store, "append", fail)
    assert app.process(frame(snapshot()), connection_epoch=epoch) is None
    assert app.manager.books["TEST-PERP"].state is BookState.CURRENT
    health = app.health()
    assert health.state is RuntimeState.QUARANTINED
    assert health.persistence_failure_count == 1 and health.rows_written_this_run == 0


def test_store_live_pragmas_integrity_and_corruption(tmp_path: Path) -> None:
    store = BookEvidenceStore(tmp_path / "evidence.sqlite3")
    with sqlite3.connect(store.path) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert db.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 0
    # foreign_keys is connection-local; the store applies it on every owned connection.
    with store._connect() as db:
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"not a sqlite database")
    with pytest.raises((sqlite3.DatabaseError, ShadowResearchError)):
        BookEvidenceStore(corrupt)


def test_store_busy_failure_is_normalized(tmp_path: Path, monkeypatch) -> None:
    app = runtime(tmp_path)
    subscribe(app)
    epoch = app.health().epoch
    assert epoch
    observation = app.process(frame(snapshot()), connection_epoch=epoch)
    assert observation is not None

    def busy():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(app._store, "_connect", busy)
    with pytest.raises(ShadowResearchError, match="persistence rejected"):
        app._store.append(observation)


def test_official_protocol_fixture_and_safety_isolation() -> None:
    fixture = json.loads(Path("tests/fixtures/kalshi_ws_v2_official.json").read_text())
    assert fixture["handshake"] == {
        "method": "GET",
        "path": "/trade-api/ws/v2",
        "authenticated": True,
    }
    assert fixture["subscribe"]["params"]["use_yes_price"] is True
    assert fixture["subscribed"]["msg"]["sid"] == 7
    assert fixture["get_snapshot"]["cmd"] == "update_subscription"

    paths = [
        Path("services/real_time_market_data/transport.py"),
        Path("services/perps_shadow_research/runtime.py"),
    ]
    forbidden_imports = {
        "production_execution",
        "demo_execution",
        "risk_engine",
        "supervised_canary",
        "bounded_autonomy",
        "learning",
    }
    for path in paths:
        tree = ast.parse(path.read_text())
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        assert not any(part in forbidden_imports for name in imports for part in name.split("."))
        assert not any(
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.upper() in {"POST", "PUT", "PATCH", "DELETE"}
            for node in ast.walk(tree)
        )
