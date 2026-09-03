from __future__ import annotations

import json
import sqlite3
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from services.market_universe.archive import EntityKind, UniverseObservationArchive
from services.market_universe.collect import (
    MAX_EVENT_RECONCILIATION_REQUESTS,
    CollectionError,
    PublicUniverseTransport,
    ReconciliationStatus,
    collect_evidence,
)
from services.market_universe.sync import MemoryUniverseRepository, UniverseSynchronizer

NOW = datetime(2026, 8, 15, tzinfo=UTC)


def market(ticker: str, event_ticker: str, *, status: str = "active") -> dict[str, Any]:
    return {
        "ticker": ticker,
        "event_ticker": event_ticker,
        "title": "Market",
        "market_type": "binary",
        "status": status,
        "rules_primary": "Rule",
        "rules_secondary": "Final",
        "settlement_sources": [{"name": "Source"}],
        "price_level_structure": "linear",
        "price_ranges": [{"min": "0.01", "max": "0.99", "step": "0.01"}],
        "fractional_trading_enabled": False,
        "is_provisional": False,
        "volume_fp": "0",
        "open_interest_fp": "0",
        "last_updated_ts": "2026-08-14T00:00:00Z",
    }


def event(ticker: str) -> dict[str, Any]:
    return {
        "event_ticker": ticker,
        "series_ticker": "S",
        "title": "Event",
        "last_updated_ts": "2026-08-14T00:00:00Z",
    }


class ReconciliationTransport:
    def __init__(
        self,
        markets: list[dict[str, Any]],
        broad_events: list[dict[str, Any]],
        exact: dict[str, object],
    ) -> None:
        self.markets = markets
        self.broad_events = broad_events
        self.exact = exact
        self.calls: list[str] = []

    def get(self, path: str, *, timeout_seconds: float) -> dict[str, Any]:
        del timeout_seconds
        self.calls.append(path)
        if "/markets?" in path:
            return {"markets": self.markets, "cursor": ""}
        if "/events?" in path:
            return {"events": self.broad_events, "cursor": ""}
        ticker = path.rsplit("/", 1)[-1]
        response = self.exact[ticker]
        if isinstance(response, Exception):
            raise response
        assert isinstance(response, dict)
        return response


def test_no_missing_makes_zero_point_reads(tmp_path: Path) -> None:
    transport = ReconciliationTransport([market("M", "E")], [event("E")], {})
    receipt = collect_evidence(tmp_path / "archive.sqlite", transport, clock=lambda: NOW)
    assert receipt.complete
    assert receipt.reconciliation_status is ReconciliationStatus.NOT_NEEDED
    assert receipt.reconciliation_requests == 0
    assert len(transport.calls) == 2


def test_real_drift_and_active_omission_are_both_reconciled(tmp_path: Path) -> None:
    finalized = market("M-FINAL", "E-FINAL", status="finalized")
    active = market("M-ACTIVE", "E-ACTIVE")
    transport = ReconciliationTransport(
        [market("M1", "E-FINAL"), market("M2", "E-ACTIVE")],
        [],
        {
            "E-FINAL": {"event": event("E-FINAL"), "markets": [finalized]},
            "E-ACTIVE": {"event": event("E-ACTIVE"), "markets": [active]},
        },
    )
    path = tmp_path / "archive.sqlite"
    receipt = collect_evidence(path, transport, clock=lambda: NOW)
    assert receipt.complete
    assert receipt.initial_missing_event_tickers == ("E-ACTIVE", "E-FINAL")
    assert receipt.reconciled_event_tickers == ("E-ACTIVE", "E-FINAL")
    assert transport.calls[2:] == [
        "/trade-api/v2/events/E-ACTIVE",
        "/trade-api/v2/events/E-FINAL",
    ]
    assert UniverseObservationArchive(path).status().total_market_observations == 2


@pytest.mark.parametrize(
    "response",
    [
        {"event": event("WRONG")},
        {"event": {"event_ticker": "E"}},
        {"markets": []},
        TimeoutError("unavailable"),
    ],
)
def test_failed_exact_read_remains_unresolved_and_incomplete(
    tmp_path: Path, response: object
) -> None:
    transport = ReconciliationTransport([market("M", "E")], [], {"E": response})
    receipt = collect_evidence(tmp_path / "archive.sqlite", transport, clock=lambda: NOW)
    assert not receipt.complete
    assert receipt.reconciliation_status is ReconciliationStatus.PARTIAL
    assert receipt.reconciliation_requests == 1
    assert receipt.missing_event_tickers == ("E",)


def test_partial_recovery_never_reports_complete(tmp_path: Path) -> None:
    transport = ReconciliationTransport(
        [market("M1", "E1"), market("M2", "E2")],
        [],
        {"E1": {"event": event("E1")}, "E2": TimeoutError()},
    )
    receipt = collect_evidence(tmp_path / "archive.sqlite", transport, clock=lambda: NOW)
    assert not receipt.complete
    assert receipt.reconciled_event_tickers == ("E1",)
    assert receipt.missing_event_tickers == ("E2",)


def test_exactly_the_bound_completes_reconciliation_with_all_requests_made(
    tmp_path: Path,
) -> None:
    """M27B.3 event-reconciliation capacity repair: exactly MAX_EVENT_RECONCILIATION_REQUESTS
    missing parents must still enter reconciliation (only *exceeding* the bound skips it), and
    with every exact read valid, reconciliation must complete fully -- proving the repaired
    bound (200) works end to end, not just that the over-bound rejection still fires."""
    count = MAX_EVENT_RECONCILIATION_REQUESTS
    tickers = [f"E{i:04d}" for i in range(count)]
    markets = [market(f"M{i:04d}", ticker) for i, ticker in enumerate(tickers)]
    exact = {ticker: {"event": event(ticker)} for ticker in tickers}
    transport = ReconciliationTransport(markets, [], exact)
    path = tmp_path / "archive.sqlite"
    receipt = collect_evidence(path, transport, clock=lambda: NOW)
    assert receipt.complete
    assert receipt.reconciliation_status is ReconciliationStatus.COMPLETE
    assert receipt.reconciliation_requests == count
    assert receipt.missing_event_tickers == ()
    # deterministic sorted order, unchanged by the repair: point-read calls occur in ascending
    # ticker order, exactly matching the sorted initial_missing_event_tickers set.
    assert receipt.initial_missing_event_tickers == tuple(sorted(tickers))
    assert transport.calls[2:] == [f"/trade-api/v2/events/{ticker}" for ticker in sorted(tickers)]
    # production_influence remains exactly '0' for every row this reconciliation path writes.
    with sqlite3.connect(path) as db:
        page_influence = {
            row[0]
            for row in db.execute(
                "SELECT DISTINCT production_influence FROM acquisition_pages "
                "WHERE endpoint LIKE 'events/%'"
            )
        }
        observation_influence = {
            row[0]
            for row in db.execute(
                "SELECT DISTINCT production_influence FROM entity_observations "
                "WHERE entity_kind='event'"
            )
        }
    assert page_influence == {"0"}
    assert observation_influence == {"0"}


def test_reconciliation_bound_makes_no_point_requests(tmp_path: Path) -> None:
    count = MAX_EVENT_RECONCILIATION_REQUESTS + 1
    transport = ReconciliationTransport([market(f"M{i}", f"E{i}") for i in range(count)], [], {})
    receipt = collect_evidence(tmp_path / "archive.sqlite", transport, clock=lambda: NOW)
    assert not receipt.complete
    assert receipt.reconciliation_failure == "bounded_reconciliation_exceeded"
    assert receipt.reconciliation_requests == 0
    assert len(transport.calls) == 2


def test_prior_archive_and_prior_collection_cannot_union_coverage(tmp_path: Path) -> None:
    path = tmp_path / "archive.sqlite"
    archive = UniverseObservationArchive(path)

    class PriorTransport:
        def get(self, path: str, *, timeout_seconds: float) -> dict[str, Any]:
            del path, timeout_seconds
            return {"events": [event("E")], "cursor": ""}

    UniverseSynchronizer(
        PriorTransport(), MemoryUniverseRepository(), archive=archive, clock=lambda: NOW
    ).sync("events")
    failed = ReconciliationTransport([market("M", "E")], [], {"E": TimeoutError()})
    first = collect_evidence(path, failed, clock=lambda: NOW + timedelta(seconds=1))
    second = collect_evidence(path, failed, clock=lambda: NOW + timedelta(seconds=2))
    assert not first.complete and not second.complete
    assert first.missing_event_tickers == second.missing_event_tickers == ("E",)


def test_singleton_raw_payload_restores_with_broad_archive_authority(tmp_path: Path) -> None:
    nested = market("NESTED", "E", status="finalized")
    payload = {"event": event("E"), "markets": [nested], "extra": {"preserved": True}}
    path = tmp_path / "archive.sqlite"
    receipt = collect_evidence(
        path,
        ReconciliationTransport([market("M", "E")], [], {"E": payload}),
        clock=lambda: NOW,
    )
    archive = UniverseObservationArchive(path)
    restored = archive.at_or_before(EntityKind.EVENT, "E", NOW)
    assert receipt.complete
    assert restored.entity.ticker == "E"
    assert restored.archive_authority_id == receipt.archive_authority_id
    with sqlite3.connect(path) as db:
        singleton = db.execute(
            "SELECT canonical_payload FROM acquisition_pages WHERE endpoint='events/E'"
        ).fetchone()
        market_count = db.execute(
            "SELECT COUNT(*) FROM entity_observations WHERE entity_kind='market'"
        ).fetchone()
    assert singleton is not None
    assert json.loads(singleton[0]) == payload
    assert market_count == (1,)


@pytest.mark.parametrize(
    "path",
    [
        "/trade-api/v2/events/",
        "/trade-api/v2/events/E/INJECT",
        "/trade-api/v2/events/E%2FINJECT",
        "/trade-api/v2/events/E?status=open",
        "/trade-api/v2/events/E#fragment",
        "/trade-api/v2/events/E%0AINJECT",
        "https://evil.example/trade-api/v2/events/E",
    ],
)
def test_exact_event_target_injection_is_rejected_before_network(
    path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: pytest.fail("network reached"))
    with pytest.raises(CollectionError, match="resource rejected"):
        PublicUniverseTransport().get(path, timeout_seconds=1)


def test_point_read_redirect_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    class RedirectingOpener:
        def open(self, request: object, *, timeout: float) -> None:
            del request, timeout
            raise urllib.error.HTTPError(
                "https://external-api.kalshi.com/trade-api/v2/events/E",
                302,
                "redirect",
                {},
                None,
            )

    monkeypatch.setattr("urllib.request.build_opener", lambda *args: RedirectingOpener())
    with pytest.raises(CollectionError, match="error status"):
        PublicUniverseTransport().get("/trade-api/v2/events/E", timeout_seconds=1)
