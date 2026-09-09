"""A0.2 bounded-runner safety and failure-taxonomy tests."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.run_a02_prospective_collection as a02
from services.prospective_shadow import runner
from services.prospective_shadow.kernel import ShadowObservationStore
from services.real_time_market_data.orderbook import BookState

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = json.loads((ROOT / "artifacts/a01/prospective_start.json").read_text())
NOW = datetime(2026, 9, 6, 17, 0, tzinfo=UTC)


def _empty_refresh() -> SimpleNamespace:
    return SimpleNamespace(
        complete=True,
        repo=SimpleNamespace(markets={}, events={}),
    )


def _empty_scan() -> SimpleNamespace:
    return SimpleNamespace(
        leads=(),
        manifest=SimpleNamespace(structural_cohorts=0),
    )


def test_weather_operational_failure_is_diagnostic_only(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(runner, "refresh_universe", lambda *args, **kwargs: _empty_refresh())
    monkeypatch.setattr(runner, "run_discovery", lambda *args, **kwargs: _empty_scan())
    store = ShadowObservationStore(tmp_path / "shadow.sqlite", RECEIPT)
    result = runner.run_once(
        archive=tmp_path / "archive.sqlite",
        store=store,
        start_receipt=RECEIPT,
        cycle_id="cycle-1",
        weather_acquirer=lambda _day: runner.WeatherAcquisitionResult(
            None, True, "WEATHER_ACQUISITION_EVALUATION_BLOCKED:wgrib2 missing"
        ),
        clock=lambda: NOW,
    )
    assert result.complete is True
    assert result.weather == ()
    assert result.structural == ()
    assert result.diagnostics["weather_operational_failures"] == 0
    assert result.diagnostics["operational_failures"] == []
    assert store.validate(now=NOW)["observations"] == 0


def test_missing_weather_runtime_dependency_is_recorded_without_rows(tmp_path) -> None:
    diagnostics = runner.CycleDiagnostics()
    rows = runner._weather_cycle(
        repo=SimpleNamespace(markets={}, events={}),
        store=ShadowObservationStore(tmp_path / "shadow.sqlite", RECEIPT),
        start_receipt=RECEIPT,
        acquired_at=NOW,
        weather_acquirer=None,
        clock=lambda: NOW,
        diagnostics=diagnostics,
    )
    assert rows == []
    assert diagnostics.weather_operational_failures == 1
    assert diagnostics.operational_failures == ["WEATHER_ACQUISITION_RUNTIME_DEPENDENCY_MISSING"]


def test_explicit_wgrib2_is_resolved_once_and_reaches_weather_composer(monkeypatch) -> None:
    resolved_calls: list[str | None] = []
    compose_calls: list[dict[str, object]] = []

    def resolve(requested: str | None) -> tuple[str, str]:
        resolved_calls.append(requested)
        return "/isolated/reviewed/wgrib2", "sha256"

    def compose(day, *, transport, wgrib2_bin):
        del transport
        compose_calls.append({"day": day, "wgrib2_bin": wgrib2_bin})
        return {"classification": "SUCCESS", "records": [{"target_date": day.isoformat()}]}

    monkeypatch.setattr(a02, "_resolve_wgrib2", resolve)
    monkeypatch.setattr(a02, "compose_weather", compose)

    acquirer = a02._weather_acquirer_factory("/configured/wgrib2")
    result = acquirer(date(2026, 9, 8))

    assert result.evidence == {"target_date": "2026-09-08"}
    assert resolved_calls == ["/configured/wgrib2"]
    assert compose_calls == [{"day": date(2026, 9, 8), "wgrib2_bin": "/isolated/reviewed/wgrib2"}]


def test_explicit_wgrib2_does_not_use_path_lookup(monkeypatch) -> None:
    def fail_if_path_lookup(name: str) -> None:
        raise AssertionError(f"unexpected PATH lookup for {name}")

    monkeypatch.setattr(a02, "_resolve_wgrib2", lambda requested: (requested or "", "sha256"))
    monkeypatch.setattr(a02.shutil, "which", fail_if_path_lookup)
    monkeypatch.setattr(
        a02,
        "compose_weather",
        lambda day, *, transport, wgrib2_bin: {
            "classification": "EVALUATION_BLOCKED",
            "reason": "ZERO_VALID_CANDIDATES",
        },
    )

    result = a02._weather_acquirer_factory("/configured/wgrib2")(date(2026, 9, 8))

    assert result.operational_failure is True
    assert result.reason == "WEATHER_ACQUISITION_EVALUATION_BLOCKED:ZERO_VALID_CANDIDATES"


def test_invalid_explicit_wgrib2_fails_closed_without_path_fallback(monkeypatch) -> None:
    resolve_calls: list[str | None] = []

    def resolve(requested: str | None) -> tuple[str, str]:
        resolve_calls.append(requested)
        raise RuntimeError("invalid explicit executable")

    monkeypatch.setattr(a02, "_resolve_wgrib2", resolve)
    monkeypatch.setattr(
        a02.shutil,
        "which",
        lambda _name: (_ for _ in ()).throw(AssertionError("PATH fallback")),
    )

    result = a02._weather_acquirer_factory("/invalid/wgrib2")(date(2026, 9, 8))

    assert resolve_calls == ["/invalid/wgrib2"]
    assert result.evidence is None
    assert result.operational_failure is True
    assert result.reason == "WEATHER_ACQUISITION_RUNTIME_DEPENDENCY_MISSING:wgrib2 3.8.0 required"


def test_event_snapshot_cache_is_once_per_event(monkeypatch) -> None:
    calls: list[str] = []

    class Snapshot:
        succeeded = True

    monkeypatch.setattr(
        runner,
        "acquire_event_snapshot",
        lambda ticker, clock: calls.append(ticker) or Snapshot(),
    )
    cache = {}
    diagnostics = runner.CycleDiagnostics()
    assert runner._event_snapshot("EVT", NOW, cache, diagnostics) is not None
    assert runner._event_snapshot("EVT", NOW, cache, diagnostics) is not None
    assert calls == ["EVT"]
    assert diagnostics.exact_event_attempted == 1


def test_book_uses_live_post_request_clock_and_preserves_transport_observed_at(monkeypatch) -> None:
    t0 = NOW
    transport_observed = t0 + timedelta(seconds=1)
    clock_values = iter((t0, t0 + timedelta(seconds=2)))
    clock_calls: list[datetime] = []

    def clock() -> datetime:
        value = next(clock_values)
        clock_calls.append(value)
        return value

    def acquire(ticker: str, *, clock, **kwargs):
        clock()
        return SimpleNamespace(
            succeeded=True,
            ticker=ticker,
            yes_levels=(("0.30", "1"),),
            no_levels=(("0.65", "1"),),
            observed_at=transport_observed,
            orderbook_identity="transport-identity",
        )

    monkeypatch.setattr(runner, "acquire_orderbook_snapshot", acquire)
    view = runner._book("BOOK", t0, runner.CycleDiagnostics(), clock=clock)

    assert view.state is BookState.CURRENT
    assert view.observed_at == transport_observed
    assert clock_calls == [t0, t0 + timedelta(seconds=2)]


def test_structural_decision_is_read_after_both_books_and_authority(monkeypatch, tmp_path) -> None:
    timestamps = iter(
        (
            NOW,
            NOW + timedelta(seconds=1),
            NOW + timedelta(seconds=2),
            NOW + timedelta(seconds=3),
            NOW + timedelta(seconds=4),
            NOW + timedelta(seconds=5),
        )
    )
    decisions: list[datetime] = []
    broad_observed = NOW + timedelta(seconds=3)
    narrow_observed = NOW + timedelta(seconds=4)

    def clock() -> datetime:
        return next(timestamps)

    lead = SimpleNamespace(
        event_ticker="EVENT",
        broad_market_ticker="BROAD",
        narrow_market_ticker="NARROW",
    )
    scan = SimpleNamespace(leads=(lead,), manifest=SimpleNamespace(structural_cohorts=1))
    repo = SimpleNamespace(
        markets={
            "BROAD": SimpleNamespace(ticker="BROAD"),
            "NARROW": SimpleNamespace(ticker="NARROW"),
        }
    )
    authority = SimpleNamespace(
        market_snapshot=SimpleNamespace(observed_at=NOW + timedelta(seconds=2)),
        event_snapshot=SimpleNamespace(observed_at=NOW + timedelta(seconds=1)),
    )

    monkeypatch.setattr(runner, "_event_snapshot", lambda *args, **kwargs: authority.event_snapshot)
    monkeypatch.setattr(runner, "_authority", lambda *args, **kwargs: authority)

    def book(ticker, now, diagnostics, deadline=None, *, clock):
        del now, diagnostics, deadline
        clock()
        clock()
        observed = broad_observed if ticker == "BROAD" else narrow_observed
        return SimpleNamespace(ticker=ticker, state=BookState.CURRENT, observed_at=observed)

    monkeypatch.setattr(runner, "_book", book)
    monkeypatch.setattr(
        runner,
        "capture_structural_observation",
        lambda *, decision_at, broad_book, narrow_book, **kwargs: (
            decisions.append(decision_at)
            or {"decision": "OBSERVE", "broad": broad_book, "narrow": narrow_book}
        ),
    )

    rows, complete = runner._structural_cycle(
        repo=repo,
        scan=scan,
        store=ShadowObservationStore(tmp_path / "shadow.sqlite", RECEIPT),
        start_receipt=RECEIPT,
        acquired_at=NOW,
        clock=clock,
        diagnostics=runner.CycleDiagnostics(),
    )

    assert complete is True
    assert rows[0]["decision"] == "OBSERVE"
    assert decisions == [NOW + timedelta(seconds=5)]
    assert decisions[0] >= broad_observed
    assert decisions[0] >= narrow_observed
    assert decisions[0] >= authority.market_snapshot.observed_at
    assert decisions[0] >= authority.event_snapshot.observed_at


def test_structural_hydration_bound_cannot_be_complete_zero_lead(tmp_path) -> None:
    scan = SimpleNamespace(
        leads=tuple(SimpleNamespace(event_ticker=f"EVT-{index}") for index in range(501)),
        manifest=SimpleNamespace(structural_cohorts=501),
    )
    diagnostics = runner.CycleDiagnostics()
    rows, complete = runner._structural_cycle(
        repo=SimpleNamespace(markets={}, events={}),
        scan=scan,
        store=ShadowObservationStore(tmp_path / "shadow.sqlite", RECEIPT),
        start_receipt=RECEIPT,
        acquired_at=NOW,
        clock=lambda: NOW,
        diagnostics=diagnostics,
    )
    assert rows == []
    assert complete is False
    assert "STRUCTURAL_CANDIDATE_BOUND_EXCEEDED" in diagnostics.operational_failures


def test_three_restart_replay_cycles_are_complete_and_deterministic(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(runner, "refresh_universe", lambda *args, **kwargs: _empty_refresh())
    monkeypatch.setattr(runner, "run_discovery", lambda *args, **kwargs: _empty_scan())
    store_path = tmp_path / "shadow.sqlite"
    results = []
    for index in range(3):
        store = ShadowObservationStore(store_path, RECEIPT)
        results.append(
            runner.run_once(
                archive=tmp_path / "archive.sqlite",
                store=store,
                start_receipt=RECEIPT,
                cycle_id=f"cycle-{index}",
                weather_acquirer=lambda _day: runner.WeatherAcquisitionResult(
                    None, True, "missing"
                ),
                clock=lambda: NOW,
            )
        )
        assert store.validate(now=NOW)["observations"] == 0
    assert [result.complete for result in results] == [True, True, True]
    stable = [
        {key: value for key, value in result.diagnostics.items() if key != "cycle_duration_seconds"}
        for result in results
    ]
    assert stable[0] == stable[1] == stable[2]


@pytest.mark.parametrize("budget", [301, 840, float("nan"), float("inf"), True, "300", 0, -1])
def test_run_forever_rejects_nonreviewed_budget_before_run(
    monkeypatch: pytest.MonkeyPatch, budget: float
) -> None:
    called = False

    def unexpected_run_once(**_: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(runner, "run_once", unexpected_run_once)
    with pytest.raises(ValueError):
        runner.run_forever(cycle_budget_seconds=budget)
    assert called is False


def test_run_forever_passes_exact_reviewed_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def stop_after_one(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(complete=False)

    monkeypatch.setattr(runner, "run_once", stop_after_one)
    with pytest.raises(RuntimeError, match="incomplete"):
        runner.run_forever(cycle_budget_seconds=300)
    assert captured["cycle_budget_seconds"] == 300.0
