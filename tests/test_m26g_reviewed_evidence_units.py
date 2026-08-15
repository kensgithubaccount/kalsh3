from __future__ import annotations

from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

import services.agent_control_center.evidence_units as units
from services.agent_control_center.event_evidence import (
    IndependenceState,
    ReviewEligibility,
    UniverseEventObservation,
    _make_manifest,
    assess_manifest,
)
from services.market_universe.archive import EntityKind, UniverseObservationArchive
from services.market_universe.sync import MemoryUniverseRepository, UniverseSynchronizer

NOW = datetime(2026, 1, 2, tzinfo=UTC)


def _event(index: int, **changes: Any) -> dict[str, Any]:
    value = {
        "event_ticker": f"SYNTHETIC-E{index}",
        "series_ticker": f"SYNTHETIC-S{index}",
        "title": f"Synthetic event {index}",
        "category": f"synthetic-{index}",
        "last_updated_ts": "2026-01-01T00:00:00Z",
    }
    value.update(changes)
    return value


def _market(index: int, event_index: int | None = None) -> dict[str, Any]:
    event_index = index if event_index is None else event_index
    return {
        "ticker": f"SYNTHETIC-M{index}",
        "event_ticker": f"SYNTHETIC-E{event_index}",
        "title": f"Synthetic market {index}",
        "market_type": "binary",
        "status": "active",
        "rules_primary": "Synthetic fixture only",
        "rules_secondary": "Synthetic fixture only",
        "settlement_sources": [{"name": "Synthetic"}],
        "price_level_structure": "linear",
        "price_ranges": [{"min": "0.01", "max": "0.99", "step": "0.01"}],
        "fractional_trading_enabled": False,
        "is_provisional": False,
        "volume_fp": "1",
        "open_interest_fp": "1",
        "last_updated_ts": "2026-01-01T00:00:00Z",
    }


def _append_many(
    archive: UniverseObservationArchive,
    kind: EntityKind,
    rows: list[dict[str, Any]],
    *,
    run: str,
    at: datetime = NOW,
) -> tuple[str, ...]:
    field = "events" if kind is EntityKind.EVENT else "markets"

    class Transport:
        def get(self, path: str, *, timeout_seconds: float) -> dict[str, Any]:
            del path, timeout_seconds
            return {field: rows, "cursor": ""}

    result = UniverseSynchronizer(
        Transport(),
        MemoryUniverseRepository(),
        archive=archive,
        clock=lambda: at,
        run_id_factory=lambda: run,
    ).sync(field)
    assert result.completeness.value == "COMPLETE"
    key = "event_ticker" if kind is EntityKind.EVENT else "ticker"
    return tuple(archive.at_or_before(kind, str(row[key]), at).observation_id for row in rows)


def _source(
    tmp_path: Path, event_count: int, *, market_count: int | None = None
) -> tuple[
    UniverseObservationArchive,
    Any,
    Any,
    tuple[units.VerifiedEventAuthority, ...],
]:
    archive = UniverseObservationArchive(tmp_path / "synthetic-m26g.sqlite3")
    market_count = event_count if market_count is None else market_count
    event_rows = [_event(index) for index in range(event_count)]
    market_rows = [_market(index, index % event_count) for index in range(market_count)]
    event_ids = _append_many(archive, EntityKind.EVENT, event_rows, run="synthetic-events")
    market_ids = _append_many(archive, EntityKind.MARKET, market_rows, run="synthetic-markets")
    observations = tuple(
        UniverseEventObservation.from_verified_archive(
            archive,
            market_observation_id=market_id,
            event_observation_id=event_ids[index % event_count],
            as_of=NOW,
        )
        for index, market_id in enumerate(market_ids)
    )
    manifest = _make_manifest(
        source_universe_id="synthetic-complete-source",
        market_as_of={f"SYNTHETIC-M{index}": NOW for index in range(market_count)},
        included_source_ids=(),
        excluded_source_items=(),
        observations=observations,
        archive=archive,
    )
    assessment = assess_manifest(manifest, archive=archive)
    authorities = units._verified_event_authorities(manifest)
    return archive, manifest, assessment, authorities


def _authority(
    authorities: tuple[units.VerifiedEventAuthority, ...],
    unit_for: Any = None,
    *,
    version: str = "synthetic-reviewed-fixture-v1",
) -> units.EvidenceUnitAuthorityManifest:
    unit_for = unit_for or (lambda index: f"SYNTHETIC-UNIT-{index}")
    assignments = tuple(
        units._make_assignment(authority, unit_for(index))
        for index, authority in enumerate(authorities)
    )
    return units._make_reviewed_authority_manifest(
        tuple(reversed(assignments)), reviewed_manifest_version=version
    )


def _install(monkeypatch: pytest.MonkeyPatch, authority: Any) -> None:
    monkeypatch.setattr(units, "_REPOSITORY_REVIEWED_AUTHORITIES", (authority,))


@pytest.mark.parametrize("count", [49, 50, 51])
def test_review_gate_is_exactly_human_review_at_fifty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, count: int
) -> None:
    archive, manifest, source, authorities = _source(tmp_path, count)
    _install(monkeypatch, _authority(authorities))
    result = units.assess_reviewed_evidence_units(manifest, source, archive=archive)
    assert result.proven_independent_evidence_unit_count == count
    assert result.review_eligibility is (
        ReviewEligibility.ELIGIBLE if count >= 50 else ReviewEligibility.NOT_ELIGIBLE
    )
    assert result.independence_state is IndependenceState.PROVEN_DISTINCT_UNDER_POLICY
    assert result.interval is None and result.production_influence == Decimal("0")


def test_different_metadata_never_proves_units_without_reviewed_authority(tmp_path: Path) -> None:
    archive, manifest, source, _authorities = _source(tmp_path, 50)
    result = units.assess_reviewed_evidence_units(manifest, source, archive=archive)
    assert source.proven_exchange_event_count == 50
    assert result.proven_independent_evidence_unit_count is None
    assert result.independence_state is IndependenceState.NOT_PROVEN
    assert result.review_eligibility is ReviewEligibility.NOT_ELIGIBLE
    assert result.authority_manifest_id is None


@pytest.mark.parametrize("market_count", [100, 500])
def test_many_markets_one_event_cannot_pseudoreplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    market_count: int,
) -> None:
    archive, manifest, source, authorities = _source(tmp_path, 1, market_count=market_count)
    _install(monkeypatch, _authority(authorities))
    result = units.assess_reviewed_evidence_units(manifest, source, archive=archive)
    assert source.market_count == market_count
    assert source.proven_exchange_event_count == 1
    assert result.proven_independent_evidence_unit_count == 1


def test_one_hundred_events_may_be_reviewed_into_ten_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, manifest, source, authorities = _source(tmp_path, 100)
    _install(monkeypatch, _authority(authorities, lambda index: f"SYNTHETIC-UNIT-{index % 10}"))
    result = units.assess_reviewed_evidence_units(manifest, source, archive=archive)
    assert result.proven_exchange_event_count == 100
    assert result.proven_independent_evidence_unit_count == 10
    assert result.dependence_cluster_count == 10
    assert result.independence_state is IndependenceState.DEPENDENT
    assert result.review_eligibility is ReviewEligibility.NOT_ELIGIBLE


def test_two_events_same_or_distinct_reviewed_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, manifest, source, authorities = _source(tmp_path, 2)
    _install(monkeypatch, _authority(authorities, lambda _index: "SYNTHETIC-UNIT-ONE"))
    collapsed = units.assess_reviewed_evidence_units(manifest, source, archive=archive)
    assert collapsed.proven_independent_evidence_unit_count == 1
    assert collapsed.independence_state is IndependenceState.DEPENDENT
    _install(monkeypatch, _authority(authorities, version="synthetic-reviewed-fixture-v2"))
    distinct = units.assess_reviewed_evidence_units(manifest, source, archive=archive)
    assert distinct.proven_independent_evidence_unit_count == 2
    assert distinct.independence_state is IndependenceState.PROVEN_DISTINCT_UNDER_POLICY


def test_partial_partition_never_reports_survivor_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, manifest, source, authorities = _source(tmp_path, 50)
    _install(monkeypatch, _authority(authorities[:-1]))
    result = units.assess_reviewed_evidence_units(manifest, source, archive=archive)
    assert len(result.assignment_ids) == 49
    assert len(result.unresolved_event_authorities) == 1
    assert result.proven_independent_evidence_unit_count is None
    assert result.review_eligibility is ReviewEligibility.NOT_ELIGIBLE


def test_wrong_archive_observation_and_source_hash_do_not_transfer_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, manifest, source, authorities = _source(tmp_path / "a", 2)
    other_archive, _other_manifest, _other_source, other = _source(tmp_path / "b", 2)
    del other_archive
    _install(monkeypatch, _authority(other))
    wrong_archive = units.assess_reviewed_evidence_units(manifest, source, archive=archive)
    assert wrong_archive.proven_independent_evidence_unit_count is None

    changed = replace(authorities[0], event_source_hash="f" * 64)
    _install(monkeypatch, _authority((changed, authorities[1])))
    changed_source = units.assess_reviewed_evidence_units(manifest, source, archive=archive)
    assert changed_source.proven_independent_evidence_unit_count is None

    changed = replace(authorities[0], event_observation_id="e" * 64)
    _install(monkeypatch, _authority((changed, authorities[1])))
    changed_observation = units.assess_reviewed_evidence_units(manifest, source, archive=archive)
    assert changed_observation.proven_independent_evidence_unit_count is None


def test_later_event_body_does_not_inherit_earlier_reviewed_assignment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, _old_manifest, _old_source, old_authorities = _source(tmp_path, 1)
    later = NOW + timedelta(days=1)
    new_event_id = _append_many(
        archive,
        EntityKind.EVENT,
        [_event(0, title="Changed synthetic event body")],
        run="synthetic-event-revision",
        at=later,
    )[0]
    market_id = archive.at_or_before(EntityKind.MARKET, "SYNTHETIC-M0", later).observation_id
    observation = UniverseEventObservation.from_verified_archive(
        archive,
        market_observation_id=market_id,
        event_observation_id=new_event_id,
        as_of=later,
    )
    manifest = _make_manifest(
        source_universe_id="synthetic-later-source",
        market_as_of={"SYNTHETIC-M0": later},
        included_source_ids=(),
        excluded_source_items=(),
        observations=(observation,),
        archive=archive,
    )
    source = assess_manifest(manifest, archive=archive)
    _install(monkeypatch, _authority(old_authorities))
    result = units.assess_reviewed_evidence_units(manifest, source, archive=archive)
    assert result.proven_independent_evidence_unit_count is None
    assert len(result.unresolved_event_authorities) == 1


def test_conflicts_malformed_units_and_corruption_fail_closed(tmp_path: Path) -> None:
    _archive, _manifest, _source_assessment, authorities = _source(tmp_path, 2)
    first = units._make_assignment(authorities[0], "SYNTHETIC-UNIT-1")
    conflict = units._make_assignment(authorities[0], "SYNTHETIC-UNIT-2")
    with pytest.raises(units.EvidenceUnitError, match="multiple assignments"):
        units._make_reviewed_authority_manifest(
            (first, conflict), reviewed_manifest_version="synthetic-conflict"
        )
    with pytest.raises(units.EvidenceUnitError, match="malformed"):
        units._make_assignment(authorities[0], "")
    with pytest.raises(units.EvidenceUnitError, match="nonzero"):
        replace(first, production_influence=Decimal("1"))
    with pytest.raises(units.EvidenceUnitError, match="identity mismatch"):
        replace(first, evidence_unit_id="SYNTHETIC-UNIT-9")


def test_caller_objects_and_forged_graph_cannot_confer_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, manifest, source, authorities = _source(tmp_path, 2)
    caller_assignment = units._make_assignment(authorities[0], "CALLER-UNIT")
    caller_manifest = units._make_reviewed_authority_manifest(
        (caller_assignment,), reviewed_manifest_version="caller-created"
    )
    # The normal assessment API accepts neither assignments nor manifests.
    assert "authority" not in units.assess_reviewed_evidence_units.__annotations__
    assert (
        units.assess_reviewed_evidence_units(
            manifest, source, archive=archive
        ).proven_independent_evidence_unit_count
        is None
    )

    forged = object.__new__(units.EvidenceUnitAuthorityManifest)
    for item in fields(units.EvidenceUnitAuthorityManifest):
        object.__setattr__(forged, item.name, getattr(caller_manifest, item.name))
    object.__setattr__(forged, "assignments", _authority(authorities).assignments)
    _install(monkeypatch, forged)
    with pytest.raises(units.EvidenceUnitError):
        units.assess_reviewed_evidence_units(manifest, source, archive=archive)


def test_order_is_canonical_and_authority_version_changes_assessment_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, manifest, source, authorities = _source(tmp_path, 3)
    assignments = tuple(
        units._make_assignment(authority, f"SYNTHETIC-UNIT-{index}")
        for index, authority in enumerate(authorities)
    )
    first = units._make_reviewed_authority_manifest(
        tuple(reversed(assignments)), reviewed_manifest_version="synthetic-review-v1"
    )
    second = units._make_reviewed_authority_manifest(
        assignments, reviewed_manifest_version="synthetic-review-v1"
    )
    assert first == second
    _install(monkeypatch, first)
    assessment_v1 = units.assess_reviewed_evidence_units(manifest, source, archive=archive)
    _install(monkeypatch, _authority(authorities, version="synthetic-review-v2"))
    assessment_v2 = units.assess_reviewed_evidence_units(manifest, source, archive=archive)
    assert assessment_v1.assessment_id != assessment_v2.assessment_id
    assert assessment_v1.source_event_assessment_id == source.assessment_id


def test_m9_significance_and_consequential_paths_are_disconnected() -> None:
    code = Path("services/agent_control_center/evidence_units.py").read_text()
    for forbidden in (
        "paired_event_interval",
        "production_execution",
        "allocate_budget",
        "GovernanceProposal",
        "compare_challenger",
        "RESEARCH_CHAMPION",
        "position sizing",
        "scheduler",
        "autostart",
    ):
        assert forbidden not in code
