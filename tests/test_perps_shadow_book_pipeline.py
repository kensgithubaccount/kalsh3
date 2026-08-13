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
    source_event_fingerprint,
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
        source_event_fingerprint=source_event_fingerprint(snapshot()),
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


def test_source_fingerprint_uses_parsed_semantics_not_raw_formatting():
    first = snapshot()
    reformatted = replace(
        first,
        yes=((Decimal("0.4"), Decimal("2.5000")),),
        no=((Decimal("0.600"), Decimal("3.75")),),
        raw={"arbitrary": {"mutable": "formatting"}},
    )
    assert source_event_fingerprint(first) == source_event_fingerprint(reformatted)
    assert source_event_fingerprint(delta()) != source_event_fingerprint(
        replace(delta(), exchange_at=NOW + timedelta(microseconds=1))
    )


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


def test_delta_replay_is_preflighted_without_manager_mutation(tmp_path):
    manager, store, pipe = pipeline(tmp_path)
    pipe.snapshot(snapshot(), received_at=NOW, structure_hash="rules-v1")
    event = delta(exchange_at=NOW - timedelta(seconds=2))
    first = pipe.delta(event, received_at=NOW)
    assert first is not None
    book = manager.books[event.ticker]
    state_before = (book.state, book.sequence, book.yes.copy(), len(manager.gaps))

    replay = pipe.delta(event, received_at=NOW + timedelta(milliseconds=1))

    assert replay == first
    assert store.count() == 2
    assert (book.state, book.sequence, book.yes, len(manager.gaps)) == state_before
    following = pipe.delta(delta(3), received_at=NOW)
    assert following is not None
    assert manager.books[event.ticker].state is BookState.CURRENT
    assert manager.books[event.ticker].sequence == 3


def test_delta_source_collision_fails_before_manager_mutation(tmp_path):
    manager, store, pipe = pipeline(tmp_path)
    pipe.snapshot(snapshot(), received_at=NOW, structure_hash="rules-v1")
    pipe.delta(delta(), received_at=NOW)
    book = manager.books["TEST-PERP"]
    state_before = (book.state, book.sequence, book.yes.copy(), len(manager.gaps))
    collision = replace(delta(), delta=Decimal("9.5"))

    with pytest.raises(ShadowResearchError, match=r"source-event.*collision"):
        pipe.delta(collision, received_at=NOW)

    assert (book.state, book.sequence, book.yes, len(manager.gaps)) == state_before
    assert store.count() == 2


def test_snapshot_replay_is_preflighted_without_phantom_recovery(tmp_path):
    manager, store, pipe = pipeline(tmp_path)
    event = snapshot()
    first = pipe.snapshot(event, received_at=NOW, structure_hash="rules-v1")
    book = manager.books[event.ticker]
    state_before = (book.state, book.sequence, book.yes.copy(), book.no.copy(), len(manager.gaps))

    replay = pipe.snapshot(
        event, received_at=NOW + timedelta(milliseconds=1), structure_hash="changed-but-unused"
    )

    assert replay == first
    assert store.count() == 1
    assert (book.state, book.sequence, book.yes, book.no, len(manager.gaps)) == state_before


def test_snapshot_source_collision_fails_before_manager_mutation(tmp_path):
    manager, store, pipe = pipeline(tmp_path)
    pipe.snapshot(snapshot(), received_at=NOW, structure_hash="rules-v1")
    book = manager.books["TEST-PERP"]
    state_before = (book.state, book.sequence, book.yes.copy(), book.no.copy(), len(manager.gaps))
    collision = replace(snapshot(), yes=((Decimal("0.41"), Decimal("2.5")),))

    with pytest.raises(ShadowResearchError, match=r"source-event.*collision"):
        pipe.snapshot(collision, received_at=NOW, structure_hash="rules-v1")

    assert (book.state, book.sequence, book.yes, book.no, len(manager.gaps)) == state_before
    assert store.count() == 1


def test_store_duplicate_collision_restart_and_concurrency(tmp_path):
    path = tmp_path / "book.sqlite3"
    store = BookEvidenceStore(path)
    item = observation()
    assert store.append(item)
    assert not store.append(item)
    reopened = BookEvidenceStore(path)
    assert not reopened.append(item)
    collision = observation(source_event_fingerprint="1" * 64)
    with pytest.raises(ShadowResearchError, match="collision"):
        reopened.append(collision)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: reopened.append(item), range(16)))
    assert results == [False] * 16 and reopened.count() == 1


def test_concurrent_first_insert_into_empty_store(tmp_path):
    path = tmp_path / "book.sqlite3"
    BookEvidenceStore(path)
    item = observation()

    def append_from_fresh_connection(_: int) -> bool:
        return BookEvidenceStore(path).append(item)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(append_from_fresh_connection, range(16)))
    assert results.count(True) == 1
    assert results.count(False) == 15
    assert BookEvidenceStore(path).count() == 1


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
            "INSERT INTO book_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "x",
                "SNAPSHOT",
                "x",
                "x",
                0,
                str(uuid4()),
                0,
                0,
                "0" * 64,
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


def test_database_rejects_non_current_book_state(tmp_path):
    store = BookEvidenceStore(tmp_path / "book.sqlite3")
    item = observation()
    with sqlite3.connect(store.path) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO book_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                item.evidence_id,
                item.update_kind.value,
                item.ticker,
                item.market_id,
                item.exchange_index,
                str(item.connection_epoch),
                item.sid,
                item.sequence,
                item.source_event_fingerprint,
                BookState.GAP.value,
                str(item.best_yes_bid),
                str(item.best_yes_ask),
                str(item.best_bid_size),
                str(item.best_ask_size),
                None,
                item.received_at.isoformat(),
                item.available_at.isoformat(),
                item.price_mode.value,
                item.structure_hash,
                "0",
            ),
        )


@pytest.mark.parametrize(
    "field,value", [("sid", True), ("sid", -1), ("sequence", True), ("sequence", -1)]
)
def test_sid_and_sequence_validation(field, value):
    with pytest.raises(ShadowResearchError, match=field):
        observation(**{field: value})


@pytest.mark.parametrize("exchange_index", [True, -1])
def test_book_evidence_exchange_index_rejects_bool_and_negative(exchange_index):
    with pytest.raises(ShadowResearchError, match="exchange_index"):
        observation(exchange_index=exchange_index)


def test_timestamp_validation_and_normalization():
    offset = datetime.fromisoformat("2026-08-13T08:00:00-04:00")
    assert observation(received_at=offset).received_at == NOW
    assert (
        observation(received_at=NOW, available_at=NOW).received_at
        == observation(received_at=NOW, available_at=NOW).available_at
    )
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
