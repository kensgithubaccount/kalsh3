from __future__ import annotations

import hashlib
import json
import multiprocessing
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest

from services.market_universe import public_read
from services.market_universe.collect import DEFAULT_MAX_PAGES
from services.market_universe.market_snapshot import FRESHNESS
from services.market_universe.orderbook_snapshot import acquire_orderbook_snapshot
from services.opportunity_engine import structural_measurement_runner as runner_module
from services.opportunity_engine.domain import OpportunityError
from services.opportunity_engine.structural_measurement import (
    MeasurementState,
    compute_lifetime,
    relationship_id,
    summarize_run,
)
from services.opportunity_engine.structural_measurement_runner import (
    attempt_exact_confirmation,
    refresh_universe,
    run_discovery,
    run_forever,
    run_scan_cycle,
)
from services.opportunity_engine.structural_measurement_store import StructuralMeasurementStore

NOW = datetime(2026, 8, 15, 13, tzinfo=UTC)
SERIES_TICKER = "S"
EVENT_TICKER = "E"


def _hold_cycle_lock(path: str, ready: Any, release: Any) -> None:
    store = StructuralMeasurementStore(path)
    with store.cycle_lock():
        ready.send(True)
        release.recv()


def semantic_market_fields(
    ticker: str, threshold: object, *, quote: dict[str, str] | None = None
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "ticker": ticker,
        "event_ticker": EVENT_TICKER,
        "title": f"Will the measured value be at least {threshold} units?",
        "yes_sub_title": f"Measured value is at least {threshold} units",
        "no_sub_title": f"Measured value is below {threshold} units",
        "rules_primary": f"YES if the measured value is at least {threshold} units.",
        "rules_secondary": "Use the final published report.",
        "market_type": "binary",
        "status": "active",
        "price_level_structure": "linear_cent",
        "price_ranges": [{"start": "0", "end": "1", "step": ".01"}],
        "floor_strike": threshold,
        "strike_type": "greater",
        "custom_strike": None,
        "timezone": "UTC",
        "expiration_time": "2026-08-16T20:00:00Z",
        "expected_expiration_time": "2026-08-16T20:00:00Z",
        "occurrence_datetime": "2026-08-16T12:00:00Z",
        "rounding_rules": "nearest whole unit",
        "revision_rules": "final report controls",
        "correction_rules": "published corrections control",
        "recount_rules": "none",
        "cancellation_rules": "void only under exchange rules",
        "postponement_rules": "deadline unchanged",
        "early_close_condition": "none",
        "exception_rules": ["none"],
        "measured_event_or_value": "final measured value",
        "subject_entities": ["subject-a"],
        "geographic_scope": "scope-a",
        "threshold_unit": "units",
        "is_provisional": False,
    }
    if quote is not None:
        fields.update(quote)
    return fields


def quote_fields(bid: str, ask: str, *, size: str = "5") -> dict[str, str]:
    return {
        "yes_bid_dollars": bid,
        "yes_ask_dollars": ask,
        "yes_bid_size_fp": size,
        "yes_ask_size_fp": size,
        "no_bid_dollars": str(Decimal(1) - Decimal(ask)),
        "no_ask_dollars": str(Decimal(1) - Decimal(bid)),
        "volume_fp": "0",
        "volume_24h_fp": "0",
        "open_interest_fp": "0",
        "liquidity_dollars": "0",
    }


def raw_event() -> dict[str, Any]:
    return {
        "event_ticker": EVENT_TICKER,
        "series_ticker": SERIES_TICKER,
        "title": "Measured event",
        "category": "Economics",
        "settlement_sources": [{"name": "Official Source", "url": "https://example.test"}],
    }


def raw_series() -> dict[str, Any]:
    return {
        "ticker": SERIES_TICKER,
        "title": "Measured series",
        "category": "Economics",
        "frequency": "event",
        "tags": [],
        "settlement_sources": [{"name": "Official Source", "url": "https://example.test"}],
        "fee_type": "quadratic",
        "fee_multiplier": "1",
        "last_updated_ts": "2026-08-15T12:00:00Z",
    }


class FakeUniverseTransport:
    """Implements the `PublicTransport` protocol used by `UniverseSynchronizer`."""

    def __init__(self, markets: list[dict[str, Any]], events: list[dict[str, Any]]) -> None:
        self.markets = markets
        self.events = events

    def get(self, path: str, *, timeout_seconds: float) -> dict[str, Any]:
        del timeout_seconds
        if path.startswith("/trade-api/v2/markets"):
            return {"markets": self.markets, "cursor": ""}
        if path.startswith("/trade-api/v2/events"):
            return {"events": self.events, "cursor": ""}
        raise AssertionError(f"unexpected universe path: {path}")


def test_refresh_universe_populates_markets_and_events(tmp_path: Path) -> None:
    transport = FakeUniverseTransport(
        markets=[semantic_market_fields("LOW", "1"), semantic_market_fields("HIGH", "2")],
        events=[raw_event()],
    )
    result = refresh_universe(
        str(tmp_path / "archive.sqlite3"), transport=transport, clock=lambda: NOW
    )
    assert result.complete
    assert set(result.repo.markets) == {"LOW", "HIGH"}
    assert set(result.repo.events) == {EVENT_TICKER}


def test_refresh_universe_enforces_reviewed_max_page_bound(tmp_path: Path) -> None:
    class NeverEndingTransport(FakeUniverseTransport):
        def __init__(self) -> None:
            super().__init__([], [])
            self.market_calls = 0

        def get(self, path: str, *, timeout_seconds: float) -> dict[str, Any]:
            del timeout_seconds
            if path.startswith("/trade-api/v2/markets"):
                self.market_calls += 1
                return {"markets": [], "cursor": f"cursor-{self.market_calls}"}
            return {"events": [], "cursor": ""}

    transport = NeverEndingTransport()
    result = refresh_universe(str(tmp_path / "archive.sqlite3"), transport=transport)
    assert not result.complete
    assert transport.market_calls == DEFAULT_MAX_PAGES


def test_refresh_universe_rejects_repeated_cursor(tmp_path: Path) -> None:
    class RepeatingTransport(FakeUniverseTransport):
        def get(self, path: str, *, timeout_seconds: float) -> dict[str, Any]:
            del timeout_seconds
            if path.startswith("/trade-api/v2/markets"):
                return {"markets": [], "cursor": "same-cursor"}
            raise AssertionError("events must not be reached after cursor failure")

    result = refresh_universe(
        str(tmp_path / "archive.sqlite3"), transport=RepeatingTransport([], [])
    )
    assert not result.complete


def test_refresh_progress_is_cursor_free_and_flushable(tmp_path: Path) -> None:
    progress: list[tuple[str, int, int]] = []
    result = refresh_universe(
        str(tmp_path / "archive.sqlite3"),
        transport=FakeUniverseTransport([], []),
        progress=lambda item: progress.append((item.resource, item.pages, item.records_received)),
    )
    assert result.complete
    assert progress == [("markets", 1, 0), ("events", 1, 0)]
    assert "cursor" not in repr(progress)


def test_finite_cli_returns_nonzero_and_reports_incomplete_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        runner_module,
        "run_forever",
        lambda **kwargs: iter([runner_module.ScanCycleResult("scan", 0, 0, (), False)]),
    )
    result = runner_module.main(
        [
            "--archive",
            str(tmp_path / "archive.sqlite3"),
            "--evidence-db",
            str(tmp_path / "evidence.sqlite3"),
            "--live-public-read",
            "--max-iterations",
            "1",
        ]
    )
    assert result == 1
    output = capsys.readouterr().out
    assert "refresh_complete=False" in output
    assert "measurement complete: refresh_complete=False" in output
    assert "cursor" not in output


def test_run_discovery_finds_no_lead_on_a_monotonic_ladder_and_one_lead_when_inverted(
    tmp_path: Path,
) -> None:
    monotonic_transport = FakeUniverseTransport(
        markets=[
            semantic_market_fields("LOW", "1", quote=quote_fields(".7", ".8")),
            semantic_market_fields("HIGH", "2", quote=quote_fields(".5", ".6")),
        ],
        events=[raw_event()],
    )
    monotonic = refresh_universe(
        str(tmp_path / "a.sqlite3"), transport=monotonic_transport, clock=lambda: NOW
    )
    assert not run_discovery(monotonic.repo, source_authority="test").leads

    inverted_transport = FakeUniverseTransport(
        markets=[
            semantic_market_fields("LOW", "1", quote=quote_fields(".20", ".45")),
            semantic_market_fields("HIGH", "2", quote=quote_fields(".55", ".60")),
        ],
        events=[raw_event()],
    )
    inverted = refresh_universe(
        str(tmp_path / "b.sqlite3"), transport=inverted_transport, clock=lambda: NOW
    )
    scan = run_discovery(inverted.repo, source_authority="test")
    assert len(scan.leads) == 1
    assert (scan.leads[0].broad_market_ticker, scan.leads[0].narrow_market_ticker) == (
        "LOW",
        "HIGH",
    )

    duplicate_transport = FakeUniverseTransport(
        markets=[
            semantic_market_fields("D1", "1", quote=quote_fields(".2", ".3")),
            semantic_market_fields("D2", "1", quote=quote_fields(".2", ".3")),
        ],
        events=[raw_event()],
    )
    duplicate = refresh_universe(
        str(tmp_path / "c.sqlite3"), transport=duplicate_transport, clock=lambda: NOW
    )
    duplicate_scan = run_discovery(duplicate.repo, source_authority="test")
    assert not duplicate_scan.leads
    assert duplicate_scan.manifest.cohorts_rejected_or_ambiguous == 1


def _market_transport(
    raw_by_ticker: dict[str, dict[str, Any]],
) -> Callable[[str], tuple[dict[str, object], bytes]]:
    def transport(ticker: str) -> tuple[dict[str, object], bytes]:
        body = json.dumps({"market": raw_by_ticker[ticker]}).encode()
        evidence = {
            "path": f"{public_read.BASE}/markets/{ticker}",
            "observed_at": NOW.isoformat(),
            "status": 200,
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "classification": "SUCCESS",
            "payload": {"market": raw_by_ticker[ticker]},
        }
        return evidence, body

    return transport


def _series_transport(series_raw: dict[str, Any]) -> Callable[[str], dict[str, Any]]:
    def transport(ticker: str) -> dict[str, Any]:
        assert ticker == series_raw["ticker"]
        return {"series": series_raw}

    return transport


def _orderbook_transport(
    order_books: dict[str, dict[str, Any]],
) -> Callable[[str], tuple[dict[str, object], bytes]]:
    def transport(ticker: str) -> tuple[dict[str, object], bytes]:
        raw = order_books[ticker]
        body = json.dumps({"orderbooks": [{"ticker": ticker, "orderbook_fp": raw}]}).encode()
        expected_path = f"{public_read.BASE}/markets/orderbooks?" + urlencode({"tickers": ticker})
        evidence = {
            "path": expected_path,
            "observed_at": NOW.isoformat(),
            "status": 200,
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "classification": "SUCCESS",
        }
        return evidence, body

    return transport


def _orderbook_acquirer(order_books: dict[str, dict[str, Any]]) -> Callable[[str], Any]:
    transport = _orderbook_transport(order_books)

    def acquirer(ticker: str) -> Any:
        return acquire_orderbook_snapshot(ticker, transport=transport, clock=lambda: NOW)

    return acquirer


def _universe_for_confirmation(
    *, low_book: dict[str, Any], high_book: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], FakeUniverseTransport]:
    del low_book, high_book
    low_raw = semantic_market_fields("LOW", "1", quote=quote_fields(".20", ".45"))
    high_raw = semantic_market_fields("HIGH", "2", quote=quote_fields(".55", ".60"))
    transport = FakeUniverseTransport(markets=[low_raw, high_raw], events=[raw_event()])
    return low_raw, high_raw, transport


def test_attempt_exact_confirmation_end_to_end_happy_path(tmp_path: Path) -> None:
    low_raw, high_raw, transport = _universe_for_confirmation(low_book={}, high_book={})
    refresh = refresh_universe(str(tmp_path / "u.sqlite3"), transport=transport, clock=lambda: NOW)
    scan = run_discovery(refresh.repo, source_authority="test")
    lead = scan.leads[0]

    # Cheap asks on both legs so the deterministic $1 payout beats cost + fees.
    order_books = {
        "LOW": {"yes_dollars": [[".05", "5"]], "no_dollars": [[".85", "5"]]},
        "HIGH": {"yes_dollars": [[".85", "5"]], "no_dollars": [[".05", "5"]]},
    }
    observation = attempt_exact_confirmation(
        lead,
        relationship_id_value=relationship_id(lead),
        scan_run_id="scan-1",
        repo=refresh.repo,
        market_read=_market_transport({"LOW": low_raw, "HIGH": high_raw}),
        series_read=_series_transport(raw_series()),
        orderbook_acquirer=_orderbook_acquirer(order_books),
        clock=lambda: NOW,
    )
    assert observation.state is MeasurementState.AFTER_COST_POSITIVE_RESEARCH
    assert observation.formula_adjusted_gap is not None and observation.formula_adjusted_gap > 0
    assert observation.confirmation_id is not None


def test_attempt_exact_confirmation_blocks_on_a_semantically_invalid_specification(
    tmp_path: Path,
) -> None:
    low_raw, high_raw, _transport = _universe_for_confirmation(low_book={}, high_book={})
    # No settlement source at all: the generic parser cannot reach SemanticStatus.VALID.
    bare_event = {
        "event_ticker": EVENT_TICKER,
        "series_ticker": SERIES_TICKER,
        "title": "E",
        "category": "x",
    }
    refresh = refresh_universe(
        str(tmp_path / "u.sqlite3"),
        transport=FakeUniverseTransport(markets=[low_raw, high_raw], events=[bare_event]),
        clock=lambda: NOW,
    )
    scan = run_discovery(refresh.repo, source_authority="test")
    lead = scan.leads[0]
    bare_series = {
        "ticker": SERIES_TICKER,
        "title": "S",
        "category": "x",
        "frequency": "event",
        "fee_type": "quadratic",
        "fee_multiplier": "1",
        "last_updated_ts": "2026-08-15T12:00:00Z",
    }
    observation = attempt_exact_confirmation(
        lead,
        relationship_id_value=relationship_id(lead),
        scan_run_id="scan-1",
        repo=refresh.repo,
        market_read=_market_transport({"LOW": low_raw, "HIGH": high_raw}),
        series_read=_series_transport(bare_series),
        orderbook_acquirer=_orderbook_acquirer({}),
        clock=lambda: NOW,
    )
    assert observation.state is MeasurementState.DISCOVERY_ONLY
    assert observation.blocker_reason is not None
    assert "strategy-supported" in observation.blocker_reason


def test_attempt_exact_confirmation_blocks_on_a_stale_orderbook_snapshot(tmp_path: Path) -> None:
    low_raw, high_raw, transport = _universe_for_confirmation(low_book={}, high_book={})
    refresh = refresh_universe(str(tmp_path / "u.sqlite3"), transport=transport, clock=lambda: NOW)
    scan = run_discovery(refresh.repo, source_authority="test")
    lead = scan.leads[0]
    order_books = {
        "LOW": {"yes_dollars": [[".05", "5"]], "no_dollars": [[".85", "5"]]},
        "HIGH": {"yes_dollars": [[".85", "5"]], "no_dollars": [[".05", "5"]]},
    }
    far_future = NOW + FRESHNESS + timedelta(hours=1)
    observation = attempt_exact_confirmation(
        lead,
        relationship_id_value=relationship_id(lead),
        scan_run_id="scan-1",
        repo=refresh.repo,
        market_read=_market_transport({"LOW": low_raw, "HIGH": high_raw}),
        series_read=_series_transport(raw_series()),
        orderbook_acquirer=_orderbook_acquirer(order_books),
        clock=lambda: far_future,
    )
    assert observation.state is MeasurementState.DISCOVERY_ONLY
    assert observation.blocker_reason is not None
    assert "stale" in observation.blocker_reason.lower() or "STALE" in observation.blocker_reason


def test_run_scan_cycle_closes_a_lead_that_disappears_on_a_later_scan(tmp_path: Path) -> None:
    archive = str(tmp_path / "archive.sqlite3")
    store = StructuralMeasurementStore(tmp_path / "evidence.sqlite3")
    low_raw = semantic_market_fields("LOW", "1", quote=quote_fields(".20", ".45"))
    high_raw = semantic_market_fields("HIGH", "2", quote=quote_fields(".55", ".60"))
    inverted_transport = FakeUniverseTransport(markets=[low_raw, high_raw], events=[raw_event()])

    first = run_scan_cycle(
        archive_path=archive,
        store=store,
        source_authority="test",
        universe_transport=inverted_transport,
        series_read=_series_transport(raw_series()),
        orderbook_acquirer=_orderbook_acquirer({}),
        clock=lambda: NOW,
    )
    assert first.discovery_leads == 1
    recorded_relationships = set(store.relationship_ids())
    assert len(recorded_relationships) == 1
    [rel_id] = recorded_relationships
    assert store.for_relationship(rel_id)[-1].state is MeasurementState.DISCOVERY_ONLY

    monotonic_low = semantic_market_fields("LOW", "1", quote=quote_fields(".7", ".8"))
    monotonic_high = semantic_market_fields("HIGH", "2", quote=quote_fields(".5", ".6"))
    monotonic_transport = FakeUniverseTransport(
        markets=[monotonic_low, monotonic_high], events=[raw_event()]
    )
    second = run_scan_cycle(
        archive_path=archive,
        store=store,
        source_authority="test",
        universe_transport=monotonic_transport,
        series_read=_series_transport(raw_series()),
        orderbook_acquirer=_orderbook_acquirer({}),
        clock=lambda: NOW + timedelta(minutes=15),
    )
    assert second.discovery_leads == 0
    history = store.for_relationship(rel_id)
    assert history[-1].state is MeasurementState.DISAPPEARED
    assert history[-1].observed_at == NOW + timedelta(minutes=15)


def test_overlapping_run_scan_cycle_fails_before_refresh_and_releases_cross_process(
    tmp_path: Path,
) -> None:
    path = str(tmp_path / "evidence.sqlite3")
    context = multiprocessing.get_context("spawn")
    ready_parent, ready_child = context.Pipe(duplex=False)
    release_parent, release_child = context.Pipe(duplex=False)
    process = context.Process(target=_hold_cycle_lock, args=(path, ready_child, release_parent))
    process.start()
    assert ready_parent.recv() is True
    store = StructuralMeasurementStore(path)
    with pytest.raises(OpportunityError, match="already running"):
        run_scan_cycle(
            archive_path=str(tmp_path / "archive.sqlite3"),
            store=store,
            source_authority="test",
        )
    release_child.send(True)
    process.join(timeout=10)
    assert process.exitcode == 0
    with store.cycle_lock():
        pass


def test_incomplete_refresh_fails_closed_without_observation_or_disappearance_writes(
    tmp_path: Path,
) -> None:
    class IncompleteTransport(FakeUniverseTransport):
        def get(self, path: str, *, timeout_seconds: float) -> dict[str, Any]:
            if path.startswith("/trade-api/v2/markets") and "cursor=" in path:
                raise RuntimeError("refresh interrupted")
            if path.startswith("/trade-api/v2/markets"):
                return {"markets": [semantic_market_fields("LOW", "1")], "cursor": "next"}
            return super().get(path, timeout_seconds=timeout_seconds)

    store = StructuralMeasurementStore(tmp_path / "evidence.sqlite3")
    result = run_scan_cycle(
        archive_path=str(tmp_path / "archive.sqlite3"),
        store=store,
        source_authority="test",
        universe_transport=IncompleteTransport([], [raw_event()]),
        clock=lambda: NOW,
    )
    assert not result.refresh_complete
    assert result.observations == ()
    assert store.all_observations() == []


def test_ambiguous_cohort_is_not_recorded_as_disappeared(tmp_path: Path) -> None:
    store = StructuralMeasurementStore(tmp_path / "evidence.sqlite3")
    first_transport = FakeUniverseTransport(
        [
            semantic_market_fields("LOW", "1", quote=quote_fields(".20", ".45")),
            semantic_market_fields("HIGH", "2", quote=quote_fields(".55", ".60")),
        ],
        [raw_event()],
    )
    run_scan_cycle(
        archive_path=str(tmp_path / "archive.sqlite3"),
        store=store,
        source_authority="test",
        universe_transport=first_transport,
        series_read=_series_transport(raw_series()),
        orderbook_acquirer=_orderbook_acquirer({}),
        clock=lambda: NOW,
    )
    ambiguous_transport = FakeUniverseTransport(
        [
            semantic_market_fields("LOW", "1", quote=quote_fields(".20", ".45")),
            semantic_market_fields("HIGH", "2", quote=quote_fields(".55", ".60")),
            semantic_market_fields("DUP", "2", quote=quote_fields(".55", ".60")),
        ],
        [raw_event()],
    )
    run_scan_cycle(
        archive_path=str(tmp_path / "archive.sqlite3"),
        store=store,
        source_authority="test",
        universe_transport=ambiguous_transport,
        series_read=_series_transport(raw_series()),
        orderbook_acquirer=_orderbook_acquirer({}),
        clock=lambda: NOW + timedelta(minutes=15),
    )
    states = [observation.state for observation in store.all_observations()]
    assert MeasurementState.AMBIGUOUS in states
    assert MeasurementState.DISAPPEARED not in states


def test_ambiguous_episode_is_censored_and_recurrence_starts_a_new_episode(
    tmp_path: Path,
) -> None:
    store = StructuralMeasurementStore(tmp_path / "evidence.sqlite3")
    inverted = FakeUniverseTransport(
        [
            semantic_market_fields("LOW", "1", quote=quote_fields(".20", ".45")),
            semantic_market_fields("HIGH", "2", quote=quote_fields(".55", ".60")),
        ],
        [raw_event()],
    )
    ambiguous = FakeUniverseTransport(
        [
            semantic_market_fields("LOW", "1", quote=quote_fields(".20", ".45")),
            semantic_market_fields("HIGH", "2", quote=quote_fields(".55", ".60")),
            semantic_market_fields("DUP", "2", quote=quote_fields(".55", ".60")),
        ],
        [raw_event()],
    )
    kwargs = {
        "archive_path": str(tmp_path / "archive.sqlite3"),
        "store": store,
        "source_authority": "test",
        "series_read": _series_transport(raw_series()),
        "orderbook_acquirer": _orderbook_acquirer({}),
    }
    first_result = run_scan_cycle(**kwargs, universe_transport=inverted, clock=lambda: NOW)
    run_scan_cycle(
        **kwargs, universe_transport=ambiguous, clock=lambda: NOW + timedelta(minutes=15)
    )
    recurrence_result = run_scan_cycle(
        **kwargs, universe_transport=inverted, clock=lambda: NOW + timedelta(minutes=30)
    )
    histories = [store.for_relationship(rel) for rel in store.relationship_ids()]
    assert len(histories) == 2
    censored, active = sorted(histories, key=lambda history: history[0].observed_at)
    censored_lifetime = compute_lifetime(censored)
    active_lifetime = compute_lifetime(active)
    assert censored[-1].state is MeasurementState.AMBIGUOUS
    assert not censored_lifetime.still_active
    assert censored_lifetime.ambiguity_censored_at == NOW + timedelta(minutes=15)
    assert censored_lifetime.disappeared_at is None
    assert active_lifetime.still_active
    assert active_lifetime.ambiguity_censored_at is None
    summary = summarize_run(
        list(first_result.observations)
        + list(recurrence_result.observations)
        + store.all_observations()[1:-1],
        [censored_lifetime, active_lifetime],
        scans_completed=3,
        independent_cohorts_observed=1,
    )
    assert summary.still_active_count == 1
    assert summary.disappeared_count == 0
    assert summary.ambiguity_censored_count == 1


def test_recurrence_after_disappearance_starts_a_new_persistence_episode(tmp_path: Path) -> None:
    store = StructuralMeasurementStore(tmp_path / "evidence.sqlite3")
    inverted = FakeUniverseTransport(
        [
            semantic_market_fields("LOW", "1", quote=quote_fields(".20", ".45")),
            semantic_market_fields("HIGH", "2", quote=quote_fields(".55", ".60")),
        ],
        [raw_event()],
    )
    monotonic = FakeUniverseTransport(
        [
            semantic_market_fields("LOW", "1", quote=quote_fields(".70", ".80")),
            semantic_market_fields("HIGH", "2", quote=quote_fields(".50", ".60")),
        ],
        [raw_event()],
    )
    kwargs = {
        "archive_path": str(tmp_path / "a.sqlite3"),
        "store": store,
        "source_authority": "test",
        "series_read": _series_transport(raw_series()),
        "orderbook_acquirer": _orderbook_acquirer({}),
    }
    run_scan_cycle(**kwargs, universe_transport=inverted, clock=lambda: NOW)
    run_scan_cycle(
        **kwargs, universe_transport=monotonic, clock=lambda: NOW + timedelta(minutes=15)
    )
    run_scan_cycle(**kwargs, universe_transport=inverted, clock=lambda: NOW + timedelta(minutes=30))
    run_scan_cycle(**kwargs, universe_transport=inverted, clock=lambda: NOW + timedelta(minutes=45))
    run_scan_cycle(**kwargs, universe_transport=inverted, clock=lambda: NOW + timedelta(minutes=60))
    histories = [store.for_relationship(rel) for rel in store.relationship_ids()]
    assert len(histories) == 2
    closed, active = sorted(histories, key=lambda history: history[0].observed_at)
    assert closed[-1].state is MeasurementState.DISAPPEARED
    assert [row.observed_at for row in active] == [
        NOW + timedelta(minutes=30),
        NOW + timedelta(minutes=45),
        NOW + timedelta(minutes=60),
    ]
    assert all(row.state is not MeasurementState.DISAPPEARED for row in active)


def test_run_forever_respects_max_iterations_and_never_sleeps_after_the_last_scan(
    tmp_path: Path,
) -> None:
    archive = str(tmp_path / "archive.sqlite3")
    store = StructuralMeasurementStore(tmp_path / "evidence.sqlite3")
    transport = FakeUniverseTransport(markets=[], events=[])
    sleeps: list[float] = []
    results = list(
        run_forever(
            archive_path=archive,
            store=store,
            source_authority="test",
            cadence_seconds=42,
            max_iterations=2,
            sleeper=sleeps.append,
            universe_transport=transport,
            clock=lambda: NOW,
        )
    )
    assert len(results) == 2
    assert sleeps == [42]
