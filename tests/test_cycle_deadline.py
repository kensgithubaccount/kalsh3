from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from services.cycle_deadline import CycleDeadline, CycleDeadlineExceeded
from services.market_universe.sync import MemoryUniverseRepository, SyncRun, UniverseSynchronizer
from services.opportunity_engine.structural_measurement_runner import refresh_universe
from services.prospective_shadow import runner
from services.prospective_shadow.kernel import ShadowObservationStore

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = json.loads((ROOT / "artifacts/a01/prospective_start.json").read_text())
NOW = datetime(2026, 9, 6, 17, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class PageTransport:
    def __init__(self, pages: list[dict[str, Any]], clock: FakeClock, delay: float = 0.0) -> None:
        self.pages = pages
        self.clock = clock
        self.delay = delay
        self.timeouts: list[float] = []
        self.calls = 0

    def get(self, path: str, *, timeout_seconds: float) -> dict[str, Any]:
        del path
        self.calls += 1
        self.timeouts.append(timeout_seconds)
        self.clock.advance(self.delay)
        return self.pages.pop(0)


def test_deadline_is_absolute_and_request_timeout_is_capped() -> None:
    clock = FakeClock()
    deadline = CycleDeadline(5, monotonic=clock)
    transport = PageTransport([{"markets": [], "cursor": ""}], clock)
    run = UniverseSynchronizer(
        transport, MemoryUniverseRepository(), timeout=30, deadline=deadline
    ).sync("markets")
    assert run.completeness.value == "COMPLETE"
    assert transport.timeouts == [5]
    clock.advance(5)
    with pytest.raises(CycleDeadlineExceeded) as exc_info:
        deadline.check("CYCLE_DEADLINE_MARKET_PAGINATION")
    assert exc_info.value.stage == "CYCLE_DEADLINE_MARKET_PAGINATION"


def test_slow_market_pagination_fails_before_next_page() -> None:
    clock = FakeClock()
    transport = PageTransport(
        [{"markets": [], "cursor": "next"}, {"markets": [], "cursor": ""}], clock, delay=2
    )
    deadline = CycleDeadline(3, monotonic=clock)
    with pytest.raises(CycleDeadlineExceeded) as exc_info:
        UniverseSynchronizer(transport, MemoryUniverseRepository(), deadline=deadline)._pages(
            "markets", "markets", SyncRun("run", "markets", "baseline", NOW)
        )
    assert exc_info.value.stage == "CYCLE_DEADLINE_MARKET_PAGINATION"
    assert transport.calls == 2


def test_slow_event_pagination_fails_at_event_stage() -> None:
    clock = FakeClock()
    transport = PageTransport(
        [{"events": [], "cursor": "next"}, {"events": [], "cursor": ""}], clock, delay=2
    )
    deadline = CycleDeadline(1, monotonic=clock)
    with pytest.raises(CycleDeadlineExceeded) as exc_info:
        UniverseSynchronizer(transport, MemoryUniverseRepository(), deadline=deadline)._pages(
            "events", "events", SyncRun("run", "events", "baseline", NOW)
        )
    assert exc_info.value.stage == "CYCLE_DEADLINE_EVENT_PAGINATION"


def test_repeated_pagination_cannot_escape_deadline() -> None:
    clock = FakeClock()
    transport = PageTransport(
        [{"markets": [], "cursor": f"page-{index}"} for index in range(10)], clock, delay=1
    )
    deadline = CycleDeadline(2, monotonic=clock)
    with pytest.raises(CycleDeadlineExceeded):
        UniverseSynchronizer(transport, MemoryUniverseRepository(), deadline=deadline)._pages(
            "markets", "markets", SyncRun("run", "markets", "baseline", NOW)
        )
    assert transport.calls == 2


def test_exact_event_reconciliation_respects_deadline() -> None:
    clock = FakeClock()
    transport = PageTransport([{"event": {}}], clock, delay=2)
    deadline = CycleDeadline(1, monotonic=clock)
    with pytest.raises(CycleDeadlineExceeded) as exc_info:
        UniverseSynchronizer(
            transport, MemoryUniverseRepository(), deadline=deadline
        ).reconcile_events(("EVENT",))
    assert exc_info.value.stage == "CYCLE_DEADLINE_EVENT_RECONCILIATION"


def test_incomplete_refresh_is_not_authoritative(tmp_path: Path) -> None:
    clock = FakeClock()
    transport = PageTransport([{"markets": [], "cursor": "next"}], clock, delay=2)
    result = refresh_universe(
        str(tmp_path / "archive.sqlite3"),
        transport=transport,
        deadline=CycleDeadline(1, monotonic=clock),
    )
    assert result.complete is False
    assert result.failure == "CYCLE_DEADLINE_MARKET_PAGINATION"


def test_downstream_deadline_emits_operational_failure_without_observation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    clock = FakeClock()
    monkeypatch.setattr(
        runner,
        "refresh_universe",
        lambda *args, **kwargs: SimpleNamespace(
            complete=True, repo=SimpleNamespace(markets={}, events={})
        ),
    )
    monkeypatch.setattr(
        runner,
        "run_discovery",
        lambda *args, **kwargs: SimpleNamespace(
            leads=(), manifest=SimpleNamespace(structural_cohorts=0)
        ),
    )

    def timeout_cycle(**kwargs: Any) -> tuple[list[Any], bool]:
        clock.advance(301)
        kwargs["deadline"].check("CYCLE_DEADLINE_BOOK_ACQUISITION")
        return [], True

    monkeypatch.setattr(runner, "_structural_cycle", timeout_cycle)
    store = ShadowObservationStore(tmp_path / "store.sqlite3", RECEIPT)
    result = runner.run_once(
        archive=tmp_path / "archive.sqlite3",
        store=store,
        start_receipt=RECEIPT,
        cycle_id="deadline",
        clock=lambda: NOW,
        monotonic=clock,
    )
    assert result.complete is False
    assert result.diagnostics["timeout_stage"] == "CYCLE_DEADLINE_BOOK_ACQUISITION"
    assert result.diagnostics["operational_failures"] == ["CYCLE_DEADLINE_BOOK_ACQUISITION"]
    assert store.validate(now=NOW)["observations"] == 0


def test_successful_empty_cycle_remains_complete_without_weather_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        runner,
        "refresh_universe",
        lambda *args, **kwargs: SimpleNamespace(
            complete=True, repo=SimpleNamespace(markets={}, events={})
        ),
    )
    monkeypatch.setattr(
        runner,
        "run_discovery",
        lambda *args, **kwargs: SimpleNamespace(
            leads=(), manifest=SimpleNamespace(structural_cohorts=0)
        ),
    )
    store = ShadowObservationStore(tmp_path / "store.sqlite3", RECEIPT)
    result = runner.run_once(
        archive=tmp_path / "archive.sqlite3",
        store=store,
        start_receipt=RECEIPT,
        cycle_id="success",
        clock=lambda: NOW,
    )
    assert result.complete is True
    assert result.weather == ()
    assert result.structural == ()
    assert store.validate(now=NOW)["observations"] == 0
