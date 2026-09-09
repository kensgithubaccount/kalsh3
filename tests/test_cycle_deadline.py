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


def test_reconciliation_deadline_before_transport_counts_no_attempt() -> None:
    clock = FakeClock()
    transport = PageTransport([{"event": {}}], clock)
    deadline = CycleDeadline(1, monotonic=clock)
    clock.advance(1)
    repository = MemoryUniverseRepository()
    with pytest.raises(CycleDeadlineExceeded):
        UniverseSynchronizer(transport, repository, deadline=deadline).reconcile_events(("EVENT",))
    assert transport.calls == 0
    assert repository.runs[0].requests == 0


def test_reconciliation_transport_failure_counts_attempt() -> None:
    clock = FakeClock()

    class FailingTransport(PageTransport):
        def get(self, path: str, *, timeout_seconds: float) -> dict[str, Any]:
            self.calls += 1
            self.timeouts.append(timeout_seconds)
            raise OSError("fixture transport failure")

    transport = FailingTransport([], clock)
    repository = MemoryUniverseRepository()
    run = UniverseSynchronizer(transport, repository).reconcile_events(("EVENT",))
    assert run.requests == 1
    assert transport.calls == 1


def test_exact_series_reconciliation_respects_deadline() -> None:
    clock = FakeClock()
    transport = PageTransport([{"series": {}}], clock, delay=2)
    deadline = CycleDeadline(1, monotonic=clock)
    with pytest.raises(CycleDeadlineExceeded) as exc_info:
        UniverseSynchronizer(
            transport, MemoryUniverseRepository(), deadline=deadline
        ).reconcile_series(("S",))
    assert exc_info.value.stage == "CYCLE_DEADLINE_SERIES_HYDRATION"


def test_series_reconciliation_deadline_before_transport_counts_no_attempt() -> None:
    clock = FakeClock()
    transport = PageTransport([{"series": {}}], clock)
    deadline = CycleDeadline(1, monotonic=clock)
    clock.advance(1)
    repository = MemoryUniverseRepository()
    with pytest.raises(CycleDeadlineExceeded):
        UniverseSynchronizer(transport, repository, deadline=deadline).reconcile_series(("S",))
    assert transport.calls == 0
    assert repository.runs[0].requests == 0


def test_series_hydration_shares_absolute_deadline_with_earlier_stages() -> None:
    """No second/reset time budget: an earlier stage's elapsed time reduces what remains
    for exact-Series hydration on the same absolute CycleDeadline."""
    clock = FakeClock()
    deadline = CycleDeadline(3, monotonic=clock)
    repo = MemoryUniverseRepository()
    markets_transport = PageTransport([{"markets": [], "cursor": ""}], clock, delay=2)
    UniverseSynchronizer(markets_transport, repo, deadline=deadline).sync("markets")
    series_transport = PageTransport([{"series": {}}], clock, delay=2)
    with pytest.raises(CycleDeadlineExceeded) as exc_info:
        UniverseSynchronizer(series_transport, repo, deadline=deadline).reconcile_series(("S",))
    assert exc_info.value.stage == "CYCLE_DEADLINE_SERIES_HYDRATION"
    assert repo.series == {}


@pytest.mark.parametrize("budget", [float("nan"), float("inf"), True, "300", 0, -1, 841])
def test_run_once_rejects_invalid_budget_before_refresh(budget: object) -> None:
    with pytest.raises(ValueError):
        runner.run_once(
            archive="unused",
            store=object(),  # type: ignore[arg-type]
            start_receipt={},
            cycle_id="invalid-budget",
            cycle_budget_seconds=budget,  # type: ignore[arg-type]
        )


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
    assert result.market_pages == 0
    assert result.market_sync_completeness is None
    assert result.markets_discovered == 0
    assert result.event_pages is None
    assert result.event_sync_completeness is None
    assert result.reconciliation_started is None
    assert result.markets_elapsed_seconds == 2.0


@pytest.mark.parametrize(
    ("pages", "budget", "stage", "field"),
    [
        (
            [{"markets": [], "cursor": ""}, {"events": [], "cursor": "next"}],
            2,
            "CYCLE_DEADLINE_EVENT_PAGINATION",
            "events_elapsed_seconds",
        ),
    ],
)
def test_refresh_timeout_timing_uses_explicit_stage_mapping(
    tmp_path: Path,
    pages: list[dict[str, Any]],
    budget: float,
    stage: str,
    field: str,
) -> None:
    clock = FakeClock()
    result = refresh_universe(
        str(tmp_path / f"{stage}.sqlite3"),
        transport=PageTransport(pages, clock, delay=1),
        deadline=CycleDeadline(budget, monotonic=clock),
        s2a_enabled=stage == "CYCLE_DEADLINE_SERIES_PAGINATION",
    )
    assert result.failure == stage
    assert getattr(result, field) == 1.0


def test_partial_sync_result_is_non_authoritative_and_skips_discovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    called = False

    def unexpected_discovery(*_: object, **__: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(
        runner,
        "refresh_universe",
        lambda *args, **kwargs: SimpleNamespace(
            complete=False,
            failure="UNIVERSE_DISCOVERY_INCOMPLETE",
            repo=SimpleNamespace(markets={"M": object()}, events={}),
            markets_discovered=1,
            events_discovered=0,
            market_pages=2,
            event_pages=1,
            market_sync_completeness="PARTIAL",
            event_sync_completeness="PARTIAL",
        ),
    )
    monkeypatch.setattr(runner, "run_discovery", unexpected_discovery)
    result = runner.run_once(
        archive=tmp_path / "archive.sqlite",
        store=ShadowObservationStore(tmp_path / "store.sqlite", RECEIPT),
        start_receipt=RECEIPT,
        cycle_id="partial-sync",
        clock=lambda: NOW,
    )
    assert result.complete is False
    assert result.diagnostics["market_sync_completeness"] == "PARTIAL"
    assert result.diagnostics["markets_discovered"] == 1
    assert called is False


def test_census_is_unknown_before_or_during_scan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        runner,
        "refresh_universe",
        lambda *args, **kwargs: SimpleNamespace(
            complete=True, repo=SimpleNamespace(markets={}, events={})
        ),
    )

    def timeout_scan(*_: object, **__: object) -> None:
        raise CycleDeadlineExceeded("CYCLE_DEADLINE_STRUCTURAL_SCAN")

    monkeypatch.setattr(runner, "run_discovery", timeout_scan)
    result = runner.run_once(
        archive=tmp_path / "archive.sqlite",
        store=ShadowObservationStore(tmp_path / "store.sqlite", RECEIPT),
        start_receipt=RECEIPT,
        cycle_id="scan-timeout",
        clock=lambda: NOW,
    )
    assert all(
        result.diagnostics[field] is None
        for field in (
            "candidate_events",
            "candidate_leads",
            "structural_cohorts",
            "structural_leads",
        )
    )


def test_empty_completed_scan_proves_zero_census(
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
    result = runner.run_once(
        archive=tmp_path / "archive.sqlite",
        store=ShadowObservationStore(tmp_path / "store.sqlite", RECEIPT),
        start_receipt=RECEIPT,
        cycle_id="empty-scan",
        clock=lambda: NOW,
    )
    assert all(
        result.diagnostics[field] == 0
        for field in (
            "candidate_events",
            "candidate_leads",
            "structural_cohorts",
            "structural_leads",
        )
    )


def test_nonempty_scan_counts_survive_later_structural_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(runner, "_event_snapshot", lambda *args, **kwargs: None)
    diagnostics = runner.CycleDiagnostics()
    lead = SimpleNamespace(
        event_ticker="E",
        broad_market_ticker="B",
        narrow_market_ticker="N",
    )
    rows, complete = runner._structural_cycle(
        repo=SimpleNamespace(markets={}, events={}),
        scan=SimpleNamespace(leads=(lead,), manifest=SimpleNamespace(structural_cohorts=3)),
        store=ShadowObservationStore(tmp_path / "store.sqlite", RECEIPT),
        start_receipt=RECEIPT,
        acquired_at=NOW,
        clock=lambda: NOW,
        diagnostics=diagnostics,
    )
    assert rows == []
    assert complete is False
    assert diagnostics.candidate_events == 1
    assert diagnostics.candidate_leads == 1
    assert diagnostics.structural_cohorts == 3
    assert diagnostics.structural_leads == 1


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


def test_unavailable_required_series_fails_structural_cycle_before_market_hydration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        runner,
        "refresh_universe",
        lambda *args, **kwargs: SimpleNamespace(
            complete=True, repo=SimpleNamespace(markets={}, events={})
        ),
    )
    lead = SimpleNamespace(event_ticker="E", broad_market_ticker="B", narrow_market_ticker="N")
    monkeypatch.setattr(
        runner,
        "run_discovery",
        lambda *args, **kwargs: SimpleNamespace(
            leads=(lead,), manifest=SimpleNamespace(structural_cohorts=1)
        ),
    )
    structural_called = False

    def unexpected_structural_cycle(*_: object, **__: object) -> None:
        nonlocal structural_called
        structural_called = True

    monkeypatch.setattr(runner, "_structural_cycle", unexpected_structural_cycle)

    def failing_hydrate(*, diagnostics: runner.CycleDiagnostics, **_: object) -> bool:
        diagnostics.exact_series_required = 1
        diagnostics.exact_series_attempted = 1
        diagnostics.exact_series_failed = 1
        runner._record_failure(diagnostics, "STRUCTURAL_EXACT_SERIES_AUTHORITY_UNAVAILABLE")
        return False

    monkeypatch.setattr(runner, "_hydrate_candidate_series", failing_hydrate)
    store = ShadowObservationStore(tmp_path / "store.sqlite3", RECEIPT)
    result = runner.run_once(
        archive=tmp_path / "archive.sqlite3",
        store=store,
        start_receipt=RECEIPT,
        cycle_id="series-unavailable",
        clock=lambda: NOW,
        s2a_enabled=True,
    )
    assert result.complete is False
    assert result.structural == ()
    assert result.weather == ()
    assert structural_called is False
    assert result.diagnostics["operational_failures"] == [
        "STRUCTURAL_EXACT_SERIES_AUTHORITY_UNAVAILABLE"
    ]
    assert result.diagnostics["exact_series_required"] == 1
    assert result.diagnostics["exact_series_failed"] == 1
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
    assert result.diagnostics["cycle_deadline_seconds"] == 300.0
    assert result.weather == ()
    assert result.structural == ()
    assert store.validate(now=NOW)["observations"] == 0
