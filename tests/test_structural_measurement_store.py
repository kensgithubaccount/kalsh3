from __future__ import annotations

import sqlite3
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
)
from services.opportunity_engine.structural_measurement_store import StructuralMeasurementStore

NOW = datetime(2026, 8, 15, 13, tzinfo=UTC)


def _observation(
    *, observation_id: str = "obs-1", state: MeasurementState = MeasurementState.DISCOVERY_ONLY
) -> LeadObservation:
    return LeadObservation(
        observation_id,
        "relationship-1",
        "scan-1",
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


def test_append_rejects_a_content_collision(tmp_path: Path) -> None:
    store = StructuralMeasurementStore(tmp_path / "evidence.sqlite3")
    store.append(_observation())
    colliding = _observation()
    object.__setattr__(colliding, "scan_run_id", "different-scan")
    with pytest.raises(OpportunityError, match="collision"):
        store.append(colliding)


def test_append_only_rejects_direct_update_and_delete(tmp_path: Path) -> None:
    path = tmp_path / "evidence.sqlite3"
    store = StructuralMeasurementStore(path)
    store.append(_observation())
    with sqlite3.connect(path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="append only"):
            db.execute(
                "UPDATE structural_lead_observations SET blocker_reason='tampered' "
                "WHERE observation_id='obs-1'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append only"):
            db.execute("DELETE FROM structural_lead_observations WHERE observation_id='obs-1'")
    [restored] = store.for_relationship("relationship-1")
    assert restored.blocker_reason == "no evidence acquired yet"


def test_append_rejects_a_non_lead_observation_before_touching_the_database() -> None:
    unbound_store = StructuralMeasurementStore.__new__(StructuralMeasurementStore)
    with pytest.raises(OpportunityError, match="LeadObservation"):
        unbound_store.append(object())  # type: ignore[arg-type]


def test_relationship_ids_and_all_observations_list_every_row(tmp_path: Path) -> None:
    store = StructuralMeasurementStore(tmp_path / "evidence.sqlite3")
    first = _observation(observation_id="obs-1")
    second_fields = _observation(observation_id="obs-2")
    object.__setattr__(second_fields, "relationship_id", "relationship-2")
    store.append(first)
    store.append(second_fields)
    assert set(store.relationship_ids()) == {"relationship-1", "relationship-2"}
    assert len(store.all_observations()) == 2


def test_store_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "evidence.sqlite3"
    StructuralMeasurementStore(path).append(_observation())
    reopened = StructuralMeasurementStore(path)
    [restored] = reopened.for_relationship("relationship-1")
    assert restored.observation_id == "obs-1"


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
