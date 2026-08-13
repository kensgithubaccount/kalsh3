import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from services.perps_shadow_research.book_evidence import (
    BookEvidenceObservation,
    BookUpdateKind,
)
from services.perps_shadow_research.domain import ShadowResearchError
from services.perps_shadow_research.pipeline import ReadOnlyBookEvidencePipeline
from services.perps_shadow_research.store import BookEvidenceStore
from services.real_time_market_data.events import BookDeltaEvent, BookSnapshotEvent
from services.real_time_market_data.manager import SubscriptionManager
from services.real_time_market_data.orderbook import BookState, PriceMode

NOW = datetime(2026, 8, 13, 12, tzinfo=UTC)


def snapshot(seq: int = 1, sid: int = 7) -> BookSnapshotEvent:
    return BookSnapshotEvent(
        sid,
        seq,
        "TEST-PERP",
        "market-1",
        ((Decimal("0.4000"), Decimal("2.500")),),
        ((Decimal("0.6000"), Decimal("3.750")),),
        PriceMode.UNIFIED_YES,
        {},
    )


def delta(seq: int = 2, sid: int = 7, exchange_at: datetime = NOW) -> BookDeltaEvent:
    return BookDeltaEvent(
        sid,
        seq,
        "TEST-PERP",
        "market-1",
        Decimal("0.4000"),
        Decimal("1.250"),
        "yes",
        exchange_at,
        {},
    )


def pipeline(tmp_path: Path, *, clock=lambda: NOW + timedelta(milliseconds=1), indexes=None):
    manager = SubscriptionManager()
    manager.epoch = uuid4()
    store = BookEvidenceStore(tmp_path / "book.sqlite3")
    pipe = ReadOnlyBookEvidencePipeline(
        manager, store, {"TEST-PERP": 4} if indexes is None else indexes, clock
    )
    return manager, store, pipe


def observation(**changes) -> BookEvidenceObservation:
    values = dict(
        update_kind=BookUpdateKind.SNAPSHOT,
        ticker="TEST-PERP",
        market_id="market-1",
        exchange_index=4,
        connection_epoch=UUID("12345678-1234-5678-1234-567812345678"),
        sid=7,
        sequence=1,
        book_state=BookState.CURRENT,
        best_yes_bid=Decimal("0.4000"),
        best_yes_ask=Decimal("0.6000"),
        best_bid_size=Decimal("2.500"),
        best_ask_size=Decimal("3.750"),
        exchange_at=None,
        received_at=NOW,
        available_at=NOW + timedelta(milliseconds=1),
        price_mode=PriceMode.UNIFIED_YES,
        structure_hash="rules-v1",
        production_influence=Decimal("0"),
    )
    values.update(changes)
    return BookEvidenceObservation.create(**values)


def test_accepted_snapshot_and_delta_persist_exactly_once(tmp_path):
    _, store, pipe = pipeline(tmp_path)
    first = pipe.snapshot(snapshot(), received_at=NOW, structure_hash="rules-v1")
    second = pipe.delta(delta(exchange_at=NOW - timedelta(seconds=2)), received_at=NOW)
    assert first is not None and first.exchange_at is None
    assert second is not None and second.exchange_at == NOW - timedelta(seconds=2)
    assert second.best_bid_size == Decimal("3.750")
    assert store.count() == 2


def test_rejected_gapped_and_stale_delta_store_nothing(tmp_path):
    manager, store, pipe = pipeline(tmp_path)
    pipe.snapshot(snapshot(), received_at=NOW, structure_hash="rules-v1")
    assert pipe.delta(delta(3), received_at=NOW) is None
    assert store.count() == 1

    manager.new_epoch()
    pipe.snapshot(snapshot(10), received_at=NOW, structure_hash="rules-v1")
    stale = delta(11, exchange_at=NOW - timedelta(minutes=1))
    assert pipe.delta(stale, received_at=NOW) is None
    assert store.count() == 2


def test_exchange_index_and_epoch_fail_closed_before_application(tmp_path):
    manager, store, pipe = pipeline(tmp_path, indexes={})
    with pytest.raises(ShadowResearchError, match="unknown exchange"):
        pipe.snapshot(snapshot(), received_at=NOW, structure_hash="rules-v1")
    assert not manager.books and store.count() == 0
    for bad in (True, -1):
        _, _, bad_pipe = pipeline(tmp_path / str(bad), indexes={"TEST-PERP": bad})
        with pytest.raises(ShadowResearchError, match="exchange_index"):
            bad_pipe.snapshot(snapshot(), received_at=NOW, structure_hash="rules-v1")
    manager.epoch = UUID(int=0)
    with pytest.raises(ShadowResearchError, match="non-zero"):
        pipe.snapshot(snapshot(), received_at=NOW, structure_hash="rules-v1")


def test_reconnect_epoch_provenance(tmp_path):
    manager, store, pipe = pipeline(tmp_path)
    first = pipe.snapshot(snapshot(), received_at=NOW, structure_hash="rules-v1")
    old_epoch = first.connection_epoch
    new_epoch = manager.new_epoch()
    second = pipe.snapshot(snapshot(), received_at=NOW, structure_hash="rules-v1")
    assert new_epoch != old_epoch and second.connection_epoch == new_epoch and store.count() == 2


def test_store_duplicate_collision_restart_and_concurrency(tmp_path):
    path = tmp_path / "book.sqlite3"
    store = BookEvidenceStore(path)
    item = observation()
    assert store.append(item)
    assert not store.append(item)
    reopened = BookEvidenceStore(path)
    assert not reopened.append(item)
    collision = observation(best_bid_size=Decimal("9.00"))
    with pytest.raises(ShadowResearchError, match="collision"):
        reopened.append(collision)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: reopened.append(item), range(16)))
    assert results == [False] * 16 and reopened.count() == 1


def test_exact_decimal_round_trip_and_zero_influence_enforcement(tmp_path):
    store = BookEvidenceStore(tmp_path / "book.sqlite3")
    item = observation()
    store.append(item)
    loaded = store.get(item.evidence_id)
    assert loaded == item
    assert loaded.best_yes_bid.as_tuple() == Decimal("0.4000").as_tuple()
    with pytest.raises(ShadowResearchError, match="production influence"):
        observation(production_influence=Decimal("0.01"))
    with sqlite3.connect(store.path) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO book_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "x",
                "SNAPSHOT",
                "x",
                "x",
                0,
                str(uuid4()),
                0,
                0,
                "CURRENT",
                "0.4",
                None,
                "1",
                None,
                None,
                NOW.isoformat(),
                NOW.isoformat(),
                PriceMode.UNIFIED_YES.value,
                "h",
                "0.1",
            ),
        )


def test_database_update_and_delete_are_prohibited(tmp_path):
    store = BookEvidenceStore(tmp_path / "book.sqlite3")
    store.append(observation())
    for statement in ("UPDATE book_evidence SET ticker='x'", "DELETE FROM book_evidence"):
        with (
            sqlite3.connect(store.path) as db,
            pytest.raises(sqlite3.IntegrityError, match="append only"),
        ):
            db.execute(statement)


@pytest.mark.parametrize(
    "field,value", [("sid", True), ("sid", -1), ("sequence", True), ("sequence", -1)]
)
def test_sid_and_sequence_validation(field, value):
    with pytest.raises(ShadowResearchError, match=field):
        observation(**{field: value})


def test_timestamp_validation_and_normalization():
    offset = datetime.fromisoformat("2026-08-13T08:00:00-04:00")
    assert observation(received_at=offset).received_at == NOW
    with pytest.raises(ShadowResearchError, match="timezone-aware"):
        observation(received_at=datetime(2026, 8, 13))
    with pytest.raises(ShadowResearchError, match="must not exceed"):
        observation(received_at=NOW + timedelta(seconds=1))


@pytest.mark.parametrize(
    "changes",
    [
        {"best_yes_bid": 0.4},
        {"best_bid_size": Decimal("0")},
        {"best_yes_bid": None, "best_bid_size": Decimal("1")},
        {"best_yes_bid": None, "best_bid_size": None, "best_yes_ask": None, "best_ask_size": None},
        {"book_state": BookState.STALE},
    ],
)
def test_price_size_and_current_state_validation(changes):
    with pytest.raises(ShadowResearchError):
        observation(**changes)


def test_canonical_identity_rejects_tampering():
    item = observation()
    assert item == observation()
    with pytest.raises(ShadowResearchError, match="evidence_id"):
        replace(item, evidence_id="not-canonical")


def test_architecture_remains_read_only_and_isolated():
    root = Path(__file__).parents[1]
    package = root / "services/perps_shadow_research"
    assert not (package / "adapter.py").exists()
    assert not (package / "canonical.py").exists()
    sources = "\n".join(path.read_text() for path in package.glob("*.py"))
    forbidden = (
        "production_execution",
        "risk_engine",
        "services.learning",
        "import requests",
        "import httpx",
        "place_order",
        "cancel_order",
    )
    assert all(term not in sources.lower() for term in forbidden)
