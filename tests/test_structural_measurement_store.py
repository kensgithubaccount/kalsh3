from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from services.opportunity_engine.domain import OpportunityError
from services.opportunity_engine.structural import RelationshipType
from services.opportunity_engine.structural_measurement import (
    FeeTreatment,
    LeadObservation,
    MeasurementState,
    observation_content_identity,
)
from services.opportunity_engine.structural_measurement_store import StructuralMeasurementStore

NOW = datetime(2026, 8, 15, 13, tzinfo=UTC)


def _observation(
    *,
    relationship_id: str = "relationship-1",
    scan_run_id: str = "scan-1",
    state: MeasurementState = MeasurementState.DISCOVERY_ONLY,
) -> LeadObservation:
    observation = LeadObservation(
        "placeholder",
        relationship_id,
        scan_run_id,
        NOW,
        "E",
        "LOW",
        "HIGH",
        Decimal("1"),
        Decimal("2"),
        RelationshipType.YES_HIGH_SUBSET_OF_YES_LOW,
        None,
        None,
        None,
        None,
        None,
        None,
        FeeTreatment.NOT_ATTEMPTED,
        None,
        None,
        state,
        "no evidence acquired yet" if state is MeasurementState.DISCOVERY_ONLY else None,
        "test-authority",
    )
    object.__setattr__(observation, "observation_id", observation_content_identity(observation))
    return observation


def test_append_persists_and_round_trips_exactly(tmp_path: Path) -> None:
    store = StructuralMeasurementStore(tmp_path / "evidence.sqlite3")
    observation = _observation()
    assert store.append(observation) is True
    [restored] = store.for_relationship("relationship-1")
    assert restored == observation
    assert restored.production_influence == 0
    assert restored.research_only is True


def test_append_is_idempotent_for_identical_content(tmp_path: Path) -> None:
    store = StructuralMeasurementStore(tmp_path / "evidence.sqlite3")
    observation = _observation()
    assert store.append(observation) is True
    assert store.append(observation) is False
    assert len(store.for_relationship("relationship-1")) == 1


def test_append_rejects_different_content_for_same_relationship_and_scan(tmp_path: Path) -> None:
    store = StructuralMeasurementStore(tmp_path / "evidence.sqlite3")
    first = _observation()
    second = _observation()
    object.__setattr__(second, "blocker_reason", "different content")
    object.__setattr__(second, "observation_id", observation_content_identity(second))
    store.append(first)
    with pytest.raises(OpportunityError, match="relationship and scan"):
        store.append(second)


def test_concurrent_append_attempts_preserve_one_row(tmp_path: Path) -> None:
    store = StructuralMeasurementStore(tmp_path / "evidence.sqlite3")
    store.register_episode("relationship-1", "relationship-1", 1)
    first = _observation()
    second = _observation()
    object.__setattr__(second, "blocker_reason", "different content")
    object.__setattr__(second, "observation_id", observation_content_identity(second))
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(store.append, item) for item in (first, second)]
        results = [future.result() for future in futures if not future.exception()]
        errors = [future.exception() for future in futures if future.exception()]
    assert results == [True]
    assert len(errors) == 1
    assert isinstance(errors[0], OpportunityError)
    assert len(store.for_relationship("relationship-1")) == 1


def test_sqlite_enforces_relationship_and_scan_uniqueness(tmp_path: Path) -> None:
    store = StructuralMeasurementStore(tmp_path / "evidence.sqlite3")
    observation = _observation()
    store.append(observation)
    with sqlite3.connect(store.path) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO structural_lead_observations "
            "(observation_id,relationship_id,scan_run_id,observed_at,event_ticker,"
            "broad_market_ticker,narrow_market_ticker,broad_threshold,narrow_threshold,"
            "relationship_type,fee_treatment,state,source_authority,production_influence) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "other",
                observation.relationship_id,
                observation.scan_run_id,
                observation.observed_at.isoformat(),
                observation.event_ticker,
                observation.broad_market_ticker,
                observation.narrow_market_ticker,
                "1",
                "2",
                observation.relationship_type.value,
                observation.fee_treatment.value,
                observation.state.value,
                observation.source_authority,
                "0",
            ),
        )


def test_initialization_rejects_legacy_duplicate_rows(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE structural_lead_observations (
                observation_id TEXT PRIMARY KEY, relationship_id TEXT NOT NULL,
                scan_run_id TEXT NOT NULL, observed_at TEXT NOT NULL,
                event_ticker TEXT NOT NULL, broad_market_ticker TEXT NOT NULL,
                narrow_market_ticker TEXT NOT NULL, broad_threshold TEXT NOT NULL,
                narrow_threshold TEXT NOT NULL, relationship_type TEXT NOT NULL,
                lead_id TEXT, broad_quote_source_hash TEXT, narrow_quote_source_hash TEXT,
                gross_apparent_gap TEXT, indicative_quantity TEXT, confirmed_depth TEXT,
                fee_treatment TEXT NOT NULL, formula_adjusted_gap TEXT, confirmation_id TEXT,
                state TEXT NOT NULL, blocker_reason TEXT, source_authority TEXT NOT NULL,
                production_influence TEXT NOT NULL DEFAULT '0'
            );
            INSERT INTO structural_lead_observations VALUES
                ('one','r','s','2026-01-01T00:00:00+00:00','e','b','n','1','2','YES_HIGH_SUBSET_OF_YES_LOW',NULL,NULL,NULL,NULL,NULL,NULL,'NOT_ATTEMPTED',NULL,NULL,'DISCOVERY_ONLY','x','a','0'),
                ('two','r','s','2026-01-01T00:00:01+00:00','e','b','n','1','2','YES_HIGH_SUBSET_OF_YES_LOW',NULL,NULL,NULL,NULL,NULL,NULL,'NOT_ATTEMPTED',NULL,NULL,'DISCOVERY_ONLY','y','a','0');
            """
        )
    with pytest.raises(OpportunityError, match="conflicting observations"):
        StructuralMeasurementStore(path)


def test_append_rejects_a_content_collision(tmp_path: Path) -> None:
    store = StructuralMeasurementStore(tmp_path / "evidence.sqlite3")
    original = _observation()
    store.append(original)
    colliding = _observation(scan_run_id="different-scan")
    object.__setattr__(colliding, "observation_id", original.observation_id)
    with pytest.raises(OpportunityError, match="identity formula"):
        store.append(colliding)


def test_append_only_rejects_direct_update_and_delete(tmp_path: Path) -> None:
    path = tmp_path / "evidence.sqlite3"
    store = StructuralMeasurementStore(path)
    observation = _observation()
    store.append(observation)
    with sqlite3.connect(path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="append only"):
            db.execute(
                "UPDATE structural_lead_observations SET blocker_reason='tampered' "
                "WHERE observation_id=?",
                (observation.observation_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append only"):
            db.execute(
                "DELETE FROM structural_lead_observations WHERE observation_id=?",
                (observation.observation_id,),
            )
    [restored] = store.for_relationship("relationship-1")
    assert restored.blocker_reason == "no evidence acquired yet"


def test_append_rejects_a_non_lead_observation_before_touching_the_database() -> None:
    unbound_store = StructuralMeasurementStore.__new__(StructuralMeasurementStore)
    with pytest.raises(OpportunityError, match="LeadObservation"):
        unbound_store.append(object())  # type: ignore[arg-type]


def test_relationship_ids_and_all_observations_list_every_row(tmp_path: Path) -> None:
    store = StructuralMeasurementStore(tmp_path / "evidence.sqlite3")
    first = _observation()
    second_fields = _observation(relationship_id="relationship-2")
    store.append(first)
    store.append(second_fields)
    assert set(store.relationship_ids()) == {"relationship-1", "relationship-2"}
    assert len(store.all_observations()) == 2


def test_store_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "evidence.sqlite3"
    observation = _observation()
    StructuralMeasurementStore(path).append(observation)
    reopened = StructuralMeasurementStore(path)
    [restored] = reopened.for_relationship("relationship-1")
    assert restored.observation_id == observation.observation_id


def test_episode_associations_are_durable_and_append_only(tmp_path: Path) -> None:
    path = tmp_path / "evidence.sqlite3"
    store = StructuralMeasurementStore(path)
    store.register_episode("base", "episode-1", 1)
    store.register_episode("base", "episode-2", 2)
    assert StructuralMeasurementStore(path).episodes_for_relationship("base") == [
        "episode-1",
        "episode-2",
    ]
    with sqlite3.connect(path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="append only"):
            db.execute(
                "UPDATE structural_measurement_episodes SET episode_id='tampered' "
                "WHERE episode_id='episode-1'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append only"):
            db.execute("DELETE FROM structural_measurement_episodes WHERE episode_id='episode-1'")
