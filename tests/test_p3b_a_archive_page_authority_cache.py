"""P3B-A regression: `_archive_acquired_page` must resolve `authority_id` once per
page, not once per entity row.  Covers the structural fix only; all existing
archive/provenance suites remain the semantic-equivalence proof.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from services.market_universe.archive import (
    EntityKind,
    UniverseObservationArchive,
    _acquisition_writer_for_synchronizer,
)

JAN = datetime(2026, 1, 2, tzinfo=UTC)


def market(ticker: str) -> dict[str, Any]:
    return {
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


def append_page(
    archive: UniverseObservationArchive, entity_count: int, *, run_id: str
) -> tuple[str, tuple[str, ...]]:
    writer = _acquisition_writer_for_synchronizer(archive)
    markets = [market(f"M{i}") for i in range(entity_count)]
    return writer.append_page(
        provider="kalshi-public-api",
        endpoint="markets",
        parameters={},
        acquired_at=JAN,
        page_number=1,
        cursor_in=None,
        cursor_out=None,
        run_id=run_id,
        kind=EntityKind.MARKET,
        payload={"markets": markets, "cursor": ""},
    )


def _count_authority_id_resolutions(
    archive: UniverseObservationArchive, monkeypatch: pytest.MonkeyPatch
) -> dict[str, int]:
    calls = {"count": 0}
    original = type(archive).authority_id.fget
    assert original is not None

    def counting_authority_id(self: UniverseObservationArchive) -> str:
        calls["count"] += 1
        return original(self)

    monkeypatch.setattr(type(archive), "authority_id", property(counting_authority_id))
    return calls


@pytest.mark.parametrize("entity_count", [1, 5, 50])
def test_archive_acquired_page_resolves_authority_id_exactly_once_per_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entity_count: int
) -> None:
    """The authority/schema lookup must not scale with the number of entity rows
    in the page: exactly one resolution regardless of `entity_count`."""
    archive = UniverseObservationArchive(tmp_path / "archive.sqlite3")
    calls = _count_authority_id_resolutions(archive, monkeypatch)

    _page_id, observation_ids = append_page(archive, entity_count, run_id="run")

    assert len(observation_ids) == entity_count
    assert calls["count"] == 1


def test_archive_acquired_page_authority_id_is_semantically_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every entity row and the page row must carry the exact same
    `archive_authority_id` value that the archive would independently report,
    and that content must survive a reopen/replay byte-for-byte."""
    path = tmp_path / "archive.sqlite3"
    archive = UniverseObservationArchive(path)
    expected_authority = archive.authority_id

    calls = _count_authority_id_resolutions(archive, monkeypatch)
    _page_id, observation_ids = append_page(archive, 10, run_id="run")
    assert calls["count"] == 1

    for observation_id in observation_ids:
        observed = archive.get(observation_id)
        assert observed.archive_authority_id == expected_authority

    reopened = UniverseObservationArchive(path)
    assert reopened.authority_id == expected_authority
    for observation_id in observation_ids:
        assert reopened.get(observation_id) == archive.get(observation_id)

    status = reopened.status()
    assert status.archive_authority_id == expected_authority
    assert status.total_market_observations == 10
