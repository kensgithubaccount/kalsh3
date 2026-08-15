from __future__ import annotations

import inspect
import sqlite3
from dataclasses import fields
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from services.agent_control_center.event_evidence import (
    ArchiveVerificationReceipt,
    EvaluatedMarketEventBinding,
    EventEvidenceError,
    EventEvidenceManifest,
    ExchangeEventIdentityState,
    ObservationAuthorityState,
    UniverseEventObservation,
    _binding_identity,
    _make_manifest,
    _manifest_identity,
    _receipt_identity,
    assess_manifest,
    bind_market_event,
)
from services.market_universe.archive import (
    ArchiveError,
    EntityKind,
    UniverseObservationArchive,
)
from services.market_universe.domain import Event, Market
from services.market_universe.sync import MemoryUniverseRepository, UniverseSynchronizer

JAN = datetime(2026, 1, 2, tzinfo=UTC)
MARCH = datetime(2026, 3, 2, tzinfo=UTC)


def event(ticker: str = "E", **changes: Any) -> dict[str, Any]:
    raw = {
        "event_ticker": ticker,
        "series_ticker": "S",
        "title": "Temperature event",
        "last_updated_ts": "2026-01-01T00:00:00Z",
    }
    raw.update(changes)
    return raw


def market(ticker: str = "M", **changes: Any) -> dict[str, Any]:
    raw = {
        "ticker": ticker,
        "event_ticker": "E",
        "title": "Will temperature exceed 70?",
        "market_type": "binary",
        "status": "active",
        "rules_primary": "NOAA reading",
        "rules_secondary": "final",
        "settlement_sources": [{"name": "NOAA"}],
        "price_level_structure": "linear",
        "price_ranges": [{"min": "0.01", "max": "0.99", "step": "0.01"}],
        "fractional_trading_enabled": False,
        "is_provisional": False,
        "volume_fp": "10.5",
        "open_interest_fp": "4",
        "last_updated_ts": "2026-01-01T00:00:00Z",
    }
    raw.update(changes)
    return raw


def append(
    archive: UniverseObservationArchive,
    kind: EntityKind,
    raw: dict[str, Any],
    *,
    at: datetime = JAN,
    run: str = "run",
    page: int = 1,
) -> str:
    field = {EntityKind.EVENT: "events", EntityKind.MARKET: "markets"}[kind]
    del page

    class Transport:
        def get(self, path: str, *, timeout_seconds: float) -> dict[str, Any]:
            del path, timeout_seconds
            return {field: [raw], "cursor": ""}

    result = UniverseSynchronizer(
        Transport(),
        MemoryUniverseRepository(),
        archive=archive,
        clock=lambda: at,
        run_id_factory=lambda: run,
    ).sync(field)
    assert result.completeness.value == "COMPLETE"
    ticker = str(raw["ticker" if kind is EntityKind.MARKET else "event_ticker"])
    rows = archive.at_or_before(kind, ticker, at)
    return rows.observation_id


def append_many(
    archive: UniverseObservationArchive,
    kind: EntityKind,
    rows: list[dict[str, Any]],
    *,
    at: datetime = JAN,
) -> tuple[str, ...]:
    field = {EntityKind.EVENT: "events", EntityKind.MARKET: "markets"}[kind]

    class Transport:
        def get(self, path: str, *, timeout_seconds: float) -> dict[str, Any]:
            del path, timeout_seconds
            return {field: rows, "cursor": ""}

    result = UniverseSynchronizer(
        Transport(),
        MemoryUniverseRepository(),
        archive=archive,
        clock=lambda: at,
        run_id_factory=lambda: f"many-{kind.value}",
    ).sync(field)
    assert result.completeness.value == "COMPLETE"
    ticker_field = "ticker" if kind is EntityKind.MARKET else "event_ticker"
    return tuple(
        archive.at_or_before(kind, str(row[ticker_field]), at).observation_id for row in rows
    )


def pair(archive: UniverseObservationArchive) -> tuple[str, str]:
    return (
        append(archive, EntityKind.MARKET, market(), page=1),
        append(archive, EntityKind.EVENT, event(), page=2),
    )


def mutate(path: Path, sql: str) -> None:
    with sqlite3.connect(path) as db:
        db.execute("DROP TRIGGER IF EXISTS observations_no_update")
        db.execute("DROP TRIGGER IF EXISTS pages_no_update")
        db.execute(sql)


def test_market_event_reconstruct_exactly_after_reopen_and_replay(tmp_path: Path) -> None:
    path = tmp_path / "archive.sqlite3"
    archive = UniverseObservationArchive(path)
    market_id, event_id = pair(archive)
    first_market = archive.get(market_id)
    first_event = archive.get(event_id)
    reopened = UniverseObservationArchive(path)
    assert reopened.get(market_id).entity == Market.parse(market()) == first_market.entity
    assert reopened.get(event_id).entity == Event.parse(event()) == first_event.entity
    assert pair(reopened) == (market_id, event_id)
    assert reopened.get(market_id).production_influence == Decimal("0")


def test_canonical_insertion_order_does_not_change_identity(tmp_path: Path) -> None:
    archive = UniverseObservationArchive(tmp_path / "archive.sqlite3")
    raw = market()
    reordered = dict(reversed(tuple(raw.items())))
    assert append(archive, EntityKind.MARKET, raw) == append(archive, EntityKind.MARKET, reordered)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("canonical_source", "'{}'"),
        ("metadata_hash", "'" + "a" * 64 + "'"),
        ("event_ticker", "'FORGED'"),
        ("observation_id", "'" + "b" * 64 + "'"),
        ("acquired_at", "'2025-01-01T00:00:00.000000Z'"),
        ("parser_version", "'forged-parser'"),
    ],
)
def test_sqlite_observation_tampering_fails_closed(tmp_path: Path, column: str, value: str) -> None:
    path = tmp_path / "archive.sqlite3"
    archive = UniverseObservationArchive(path)
    identity = append(archive, EntityKind.MARKET, market())
    mutate(path, f"UPDATE entity_observations SET {column}={value}")  # noqa: S608
    lookup = "b" * 64 if column == "observation_id" else identity
    with pytest.raises(ArchiveError):
        archive.get(lookup)


def test_sqlite_page_hash_tampering_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "archive.sqlite3"
    archive = UniverseObservationArchive(path)
    identity = append(archive, EntityKind.MARKET, market())
    mutate(path, "UPDATE acquisition_pages SET raw_content_hash='" + "c" * 64 + "'")
    with pytest.raises(ArchiveError):
        archive.get(identity)


def test_point_in_time_and_same_timestamp_conflict(tmp_path: Path) -> None:
    archive = UniverseObservationArchive(tmp_path / "archive.sqlite3")
    january = append(archive, EntityKind.MARKET, market(title="January"), at=JAN, run="jan")
    march = append(archive, EntityKind.MARKET, market(title="March"), at=MARCH, run="march")
    assert archive.at_or_before(EntityKind.MARKET, "M", JAN).observation_id == january
    assert archive.at_or_before(EntityKind.MARKET, "M", MARCH).observation_id == march
    with pytest.raises(ArchiveError, match="conflicting"):
        append(archive, EntityKind.MARKET, market(title="Conflict"), at=MARCH, run="other")
    with pytest.raises(ArchiveError, match="conflicting"):
        archive.at_or_before(EntityKind.MARKET, "M", MARCH)


def test_wrong_store_and_forged_receipt_fail_closed(tmp_path: Path) -> None:
    archive_a = UniverseObservationArchive(tmp_path / "a.sqlite3")
    market_id, event_id = pair(archive_a)
    observation = UniverseEventObservation.from_verified_archive(
        archive_a,
        market_observation_id=market_id,
        event_observation_id=event_id,
        as_of=JAN,
    )
    archive_b = UniverseObservationArchive(tmp_path / "b.sqlite3")
    pair(archive_b)
    with pytest.raises(EventEvidenceError, match="authority"):
        bind_market_event("M", as_of=JAN, observations=(observation,), archive=archive_b)

    forged = object.__new__(type(observation.archive_receipt))
    assert observation.archive_receipt is not None
    for item in fields(type(observation.archive_receipt)):
        object.__setattr__(forged, item.name, getattr(observation.archive_receipt, item.name))
    object.__setattr__(forged, "market_observation_id", "d" * 64)
    object.__setattr__(observation, "archive_receipt", forged)
    with pytest.raises(EventEvidenceError):
        bind_market_event("M", as_of=JAN, observations=(observation,), archive=archive_a)


def test_only_archive_factory_produces_verified_and_proven(tmp_path: Path) -> None:
    archive = UniverseObservationArchive(tmp_path / "archive.sqlite3")
    market_id, event_id = pair(archive)
    plain = UniverseEventObservation.from_entities(
        Market.parse(market()),
        Event.parse(event()),
        observation_id="a" * 64,
        observed_at=JAN,
        provenance_hash="b" * 64,
    )
    assert plain.authority_state is ObservationAuthorityState.UNVERIFIED
    assert "authority_state" not in inspect.signature(UniverseEventObservation).parameters
    verified = UniverseEventObservation.from_verified_archive(
        archive,
        market_observation_id=market_id,
        event_observation_id=event_id,
        as_of=JAN,
    )
    binding = bind_market_event("M", as_of=JAN, observations=(verified,), archive=archive)
    assert verified.authority_state is ObservationAuthorityState.ARCHIVE_VERIFIED
    assert binding.state is ExchangeEventIdentityState.PROVEN


def test_byte_for_byte_copy_is_same_logical_archive_replica(tmp_path: Path) -> None:
    original = UniverseObservationArchive(tmp_path / "original.sqlite3")
    market_id, _event_id = pair(original)
    copy_path = tmp_path / "copy.sqlite3"
    with sqlite3.connect(original.path) as source, sqlite3.connect(copy_path) as destination:
        source.backup(destination)
    replica = UniverseObservationArchive(copy_path)
    assert replica.authority_id == original.authority_id
    assert replica.get(market_id).entity == original.get(market_id).entity


def test_synchronizer_is_the_acquisition_boundary_and_records_partial(tmp_path: Path) -> None:
    class Transport:
        def get(self, path: str, *, timeout_seconds: float) -> dict[str, Any]:
            del path, timeout_seconds
            return {"markets": [market(), "malformed"], "cursor": ""}

    archive = UniverseObservationArchive(tmp_path / "archive.sqlite3")
    run = UniverseSynchronizer(
        Transport(), MemoryUniverseRepository(), archive=archive, clock=lambda: JAN
    ).sync("markets")
    assert run.completeness.value == "PARTIAL"
    with sqlite3.connect(archive.path) as db:
        assert (
            db.execute("SELECT completeness FROM acquisition_run_results").fetchone()[0]
            == "PARTIAL"
        )
    assert archive.status().total_market_observations == 1


def test_synchronizer_exposes_no_routine_archive_writer_access(tmp_path: Path) -> None:
    class Transport:
        def get(self, path: str, *, timeout_seconds: float) -> dict[str, Any]:
            del path, timeout_seconds
            return {"markets": [market("MKT-X")], "cursor": ""}

    archive = UniverseObservationArchive(tmp_path / "archive.sqlite3")
    synchronizer = UniverseSynchronizer(
        Transport(), MemoryUniverseRepository(), archive=archive, clock=lambda: JAN
    )
    with pytest.raises(AttributeError):
        assert synchronizer._archive_writer is None  # type: ignore[attr-defined]
    routine_attributes = (
        name
        for name in dir(synchronizer)
        if not name.startswith("_") and not (name.startswith("__") and name.endswith("__"))
    )
    for name in routine_attributes:
        value = getattr(synchronizer, name)
        assert not callable(getattr(value, "append_page", None))
        assert not callable(getattr(value, "record_run_result", None))

    run = synchronizer.sync("markets")
    assert run.completeness.value == "COMPLETE"
    assert archive.at_or_before(EntityKind.MARKET, "MKT-X", JAN).entity.ticker == "MKT-X"


@pytest.mark.parametrize("count", [100, 500])
def test_many_verified_markets_under_one_event_count_once(tmp_path: Path, count: int) -> None:
    archive = UniverseObservationArchive(tmp_path / "archive.sqlite3")
    event_id = append(archive, EntityKind.EVENT, event(), page=2)
    market_ids = append_many(archive, EntityKind.MARKET, [market(f"M{i}") for i in range(count)])
    observations = tuple(
        UniverseEventObservation.from_verified_archive(
            archive,
            market_observation_id=identity,
            event_observation_id=event_id,
            as_of=JAN,
        )
        for identity in market_ids
    )
    manifest = _make_manifest(
        source_universe_id="source",
        market_as_of={f"M{i}": JAN for i in range(count)},
        included_source_ids=(),
        excluded_source_items=(),
        observations=observations,
        archive=archive,
    )
    assessment = assess_manifest(manifest, archive=archive)
    assert assessment.market_count == count
    assert assessment.proven_exchange_event_count == 1
    assert assessment.proven_independent_evidence_unit_count is None
    assert assessment.review_eligibility.value == "NOT_ELIGIBLE"


def test_two_verified_events_are_two_groups_but_not_independent_units(tmp_path: Path) -> None:
    archive = UniverseObservationArchive(tmp_path / "archive.sqlite3")
    observations = []
    for index in range(2):
        event_id = append(
            archive,
            EntityKind.EVENT,
            event(f"E{index}", series_ticker=f"S{index}"),
            run=f"event-{index}",
            page=index + 1,
        )
        market_id = append(
            archive,
            EntityKind.MARKET,
            market(f"M{index}", event_ticker=f"E{index}"),
            run=f"market-{index}",
            page=index + 1,
        )
        observations.append(
            UniverseEventObservation.from_verified_archive(
                archive,
                market_observation_id=market_id,
                event_observation_id=event_id,
                as_of=JAN,
            )
        )
    manifest = _make_manifest(
        source_universe_id="source",
        market_as_of={"M0": JAN, "M1": JAN},
        included_source_ids=(),
        excluded_source_items=(),
        observations=tuple(observations),
        archive=archive,
    )
    assessment = assess_manifest(manifest, archive=archive)
    assert assessment.proven_exchange_event_count == 2
    assert assessment.proven_independent_evidence_unit_count is None
    assert assessment.review_eligibility.value == "NOT_ELIGIBLE"


def test_fifty_verified_events_still_do_not_open_independence_gate(tmp_path: Path) -> None:
    archive = UniverseObservationArchive(tmp_path / "archive.sqlite3")
    event_sources = [event(f"E{i}", series_ticker=f"S{i}") for i in range(50)]
    market_sources = [market(f"M{i}", event_ticker=f"E{i}") for i in range(50)]
    event_ids = append_many(archive, EntityKind.EVENT, event_sources)
    market_ids = append_many(archive, EntityKind.MARKET, market_sources)
    observations = tuple(
        UniverseEventObservation.from_verified_archive(
            archive,
            market_observation_id=market_id,
            event_observation_id=event_id,
            as_of=JAN,
        )
        for market_id, event_id in zip(market_ids, event_ids, strict=True)
    )
    manifest = _make_manifest(
        source_universe_id="source",
        market_as_of={f"M{i}": JAN for i in range(50)},
        included_source_ids=(),
        excluded_source_items=(),
        observations=observations,
        archive=archive,
    )
    assessment = assess_manifest(manifest, archive=archive)
    assert assessment.proven_exchange_event_count == 50
    assert assessment.proven_independent_evidence_unit_count is None
    assert assessment.review_eligibility.value == "NOT_ELIGIBLE"


def test_same_identity_different_persisted_content_is_collision(tmp_path: Path) -> None:
    path = tmp_path / "archive.sqlite3"
    archive = UniverseObservationArchive(path)
    identity = append(archive, EntityKind.MARKET, market())
    mutate(path, "UPDATE entity_observations SET ticker='FORGED'")
    with pytest.raises(ArchiveError):
        append(archive, EntityKind.MARKET, market())
    with pytest.raises(ArchiveError):
        archive.get(identity)


def test_new_zero_byte_and_valid_reopen_bootstrap(tmp_path: Path) -> None:
    nonexistent = tmp_path / "new.sqlite3"
    first = UniverseObservationArchive(nonexistent)
    assert first.status().available
    assert UniverseObservationArchive(nonexistent).authority_id == first.authority_id

    zero = tmp_path / "zero.sqlite3"
    zero.touch()
    assert UniverseObservationArchive(zero).status().available


@pytest.mark.parametrize("kind", ["partial", "foreign", "all_removed"])
def test_existing_nonarchive_or_destroyed_database_is_never_bootstrapped(
    tmp_path: Path, kind: str
) -> None:
    path = tmp_path / f"{kind}.sqlite3"
    if kind == "all_removed":
        UniverseObservationArchive(path)
        with sqlite3.connect(path) as db:
            for trigger in (
                "metadata_no_update",
                "metadata_no_delete",
                "pages_no_update",
                "pages_no_delete",
                "observations_no_update",
                "observations_no_delete",
                "results_no_update",
                "results_no_delete",
            ):
                db.execute(f"DROP TRIGGER {trigger}")
            db.execute("DROP TABLE entity_observations")
            db.execute("DROP TABLE acquisition_pages")
            db.execute("DROP TABLE acquisition_run_results")
            db.execute("DROP TABLE archive_metadata")
    else:
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TABLE partial_only(value TEXT)"
                if kind == "partial"
                else "CREATE TABLE unrelated(value INTEGER)"
            )
    with pytest.raises(ArchiveError):
        UniverseObservationArchive(path)


@pytest.mark.parametrize(
    "tamper_sql",
    [
        "DROP TRIGGER pages_no_delete",
        "DROP TRIGGER observations_no_update",
        "DROP TRIGGER pages_no_delete; CREATE TRIGGER pages_no_delete "
        "BEFORE DELETE ON acquisition_pages BEGIN SELECT 1; END",
        "DROP INDEX observations_point_in_time",
        "DROP INDEX observations_point_in_time; CREATE INDEX observations_point_in_time "
        "ON entity_observations(ticker)",
        "DROP TABLE acquisition_run_results",
        "DROP TABLE entity_observations",
        "DROP TABLE archive_metadata",
        "DROP TRIGGER metadata_no_delete; DELETE FROM archive_metadata",
        "ALTER TABLE entity_observations DROP COLUMN rules_hash",
        "ALTER TABLE entity_observations DROP COLUMN parser_version",
        "DROP INDEX observations_point_in_time; "
        "ALTER TABLE entity_observations DROP COLUMN acquired_at",
        "ALTER TABLE acquisition_pages DROP COLUMN normalized_content_hash",
        "ALTER TABLE acquisition_pages DROP COLUMN cursor_out",
    ],
)
def test_existing_structural_corruption_fails_on_reopen(tmp_path: Path, tamper_sql: str) -> None:
    path = tmp_path / "archive.sqlite3"
    UniverseObservationArchive(path)
    with sqlite3.connect(path) as db:
        db.executescript(tamper_sql)
    with pytest.raises(ArchiveError):
        UniverseObservationArchive(path)


def test_metadata_is_immutable_and_guard_removal_does_not_enable_repair(tmp_path: Path) -> None:
    path = tmp_path / "archive.sqlite3"
    archive = UniverseObservationArchive(path)
    with sqlite3.connect(path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="append only"):
            db.execute("UPDATE archive_metadata SET archive_authority_id='" + "a" * 64 + "'")
        with pytest.raises(sqlite3.IntegrityError, match="append only"):
            db.execute("DELETE FROM archive_metadata")
        db.execute("DROP TRIGGER metadata_no_update")
        db.execute("UPDATE archive_metadata SET archive_authority_id='" + "a" * 64 + "'")
    with pytest.raises(ArchiveError):
        UniverseObservationArchive(path)
    assert not archive.status().available


def test_direct_fake_acquisition_requires_synchronizer_capability(tmp_path: Path) -> None:
    archive = UniverseObservationArchive(tmp_path / "archive.sqlite3")
    fake = market("MKT-X", event_ticker="FAVORABLE-EVENT")
    with pytest.raises(ArchiveError, match="capability"):
        archive._archive_acquired_page(
            object(),
            provider="kalshi-public-api",
            endpoint="markets",
            parameters={},
            acquired_at=JAN,
            page_number=1,
            cursor_in=None,
            cursor_out=None,
            run_id="fabricated",
            kind=EntityKind.MARKET,
            payload={"markets": [fake], "cursor": ""},
        )
    assert archive.status().total_market_observations == 0

    market_id = append(archive, EntityKind.MARKET, fake)
    event_id = append(
        archive,
        EntityKind.EVENT,
        event("FAVORABLE-EVENT", series_ticker="FAKE-SERIES"),
    )
    verified = UniverseEventObservation.from_verified_archive(
        archive,
        market_observation_id=market_id,
        event_observation_id=event_id,
        as_of=JAN,
    )
    assert verified.authority_state is ObservationAuthorityState.ARCHIVE_VERIFIED


@pytest.mark.parametrize("nonfinite", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_acquisition_json_is_rejected(tmp_path: Path, nonfinite: float) -> None:
    archive = UniverseObservationArchive(tmp_path / "archive.sqlite3")

    class Transport:
        def get(self, path: str, *, timeout_seconds: float) -> dict[str, Any]:
            del path, timeout_seconds
            return {"markets": [market(extra=nonfinite)], "cursor": ""}

    run = UniverseSynchronizer(
        Transport(), MemoryUniverseRepository(), archive=archive, clock=lambda: JAN
    ).sync("markets")
    assert run.completeness.value == "FAILED"
    assert archive.status().total_pages == 0


def test_object_model_proven_graph_without_store_rows_fails_assessment(tmp_path: Path) -> None:
    archive = UniverseObservationArchive(tmp_path / "archive.sqlite3")
    receipt = object.__new__(ArchiveVerificationReceipt)
    receipt_values: dict[str, object] = {
        "archive_authority_id": archive.authority_id,
        "market_observation_id": "a" * 64,
        "event_observation_id": "b" * 64,
        "market_source_hash": "c" * 64,
        "event_source_hash": "d" * 64,
        "market_ticker": "MKT-X",
        "event_ticker": "FAVORABLE-EVENT",
        "series_ticker": "FAKE-SERIES",
        "market_acquired_at": JAN,
        "event_acquired_at": JAN,
        "verified_as_of": JAN,
        "archive_verification_policy_version": "m26f-reparse-and-rehash-v1",
        "receipt_policy_version": "m26f-archive-verification-receipt-v1",
        "production_influence": Decimal("0"),
    }
    for name, value in receipt_values.items():
        object.__setattr__(receipt, name, value)
    object.__setattr__(receipt, "receipt_id", _receipt_identity(receipt))

    observation = object.__new__(UniverseEventObservation)
    observation_values = {
        "observation_id": receipt.receipt_id,
        "market_ticker": "MKT-X",
        "market_event_ticker": "FAVORABLE-EVENT",
        "event_ticker": "FAVORABLE-EVENT",
        "series_ticker": "FAKE-SERIES",
        "market_metadata_hash": "e" * 64,
        "event_metadata_hash": "f" * 64,
        "observed_at": JAN,
        "provenance_hash": "1" * 64,
        "authority_state": ObservationAuthorityState.ARCHIVE_VERIFIED,
        "production_influence": Decimal("0"),
        "archive_receipt": receipt,
    }
    for name, value in observation_values.items():
        object.__setattr__(observation, name, value)

    binding = object.__new__(EvaluatedMarketEventBinding)
    binding_values = {
        "market_ticker": "MKT-X",
        "state": ExchangeEventIdentityState.PROVEN,
        "event_ticker": "FAVORABLE-EVENT",
        "series_ticker": "FAKE-SERIES",
        "market_metadata_hash": "e" * 64,
        "event_metadata_hash": "f" * 64,
        "source_observation_id": receipt.receipt_id,
        "provenance_hash": "1" * 64,
        "observation_authority_state": ObservationAuthorityState.ARCHIVE_VERIFIED,
        "observed_at": JAN,
        "as_of": JAN,
        "detail": "forged",
        "archive_receipt": receipt,
        "binding_policy_version": "m26e-market-event-binding-v1",
        "production_influence": Decimal("0"),
    }
    for name, value in binding_values.items():
        object.__setattr__(binding, name, value)
    object.__setattr__(binding, "binding_id", _binding_identity(binding))

    manifest = object.__new__(EventEvidenceManifest)
    manifest_values = {
        "source_universe_id": "forged-source",
        "market_tickers": ("MKT-X",),
        "bindings": (binding,),
        "included_source_ids": (),
        "excluded_source_items": (),
        "time_policy_version": "m26e-observed-not-after-evaluation-source-v1",
        "binding_policy_version": "m26e-market-event-binding-v1",
        "production_influence": Decimal("0"),
    }
    for name, value in manifest_values.items():
        object.__setattr__(manifest, name, value)
    object.__setattr__(manifest, "manifest_id", _manifest_identity(manifest))
    with pytest.raises(EventEvidenceError):
        assess_manifest(manifest, archive=archive)
