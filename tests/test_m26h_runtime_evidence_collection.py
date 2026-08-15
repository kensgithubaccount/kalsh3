from __future__ import annotations

import importlib
import inspect
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from services.agent_control_center import evidence_units
from services.market_universe.archive import ArchiveError, UniverseObservationArchive
from services.market_universe.collect import (
    ALLOWED_RESOURCES,
    CollectionError,
    PublicUniverseTransport,
    collect_evidence,
    main,
)
from services.market_universe.sync import Completeness

NOW = datetime(2026, 8, 15, tzinfo=UTC)


def market(ticker: str = "M") -> dict[str, Any]:
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
        "last_updated_ts": "2026-08-14T00:00:00Z",
    }


def event(ticker: str = "E") -> dict[str, Any]:
    return {
        "event_ticker": ticker,
        "series_ticker": "S",
        "title": "Temperature event",
        "last_updated_ts": "2026-08-14T00:00:00Z",
    }


class FakeTransport:
    def __init__(self, pages: dict[str, list[object]]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def get(self, path: str, *, timeout_seconds: float) -> dict[str, Any]:
        del timeout_seconds
        self.calls.append(path)
        resource = "markets" if "/markets?" in path else "events"
        response = self.pages[resource].pop(0)
        if isinstance(response, Exception):
            raise response
        assert isinstance(response, dict)
        return response


def complete_transport() -> FakeTransport:
    return FakeTransport(
        {
            "markets": [{"markets": [market()], "cursor": ""}],
            "events": [{"events": [event()], "cursor": ""}],
        }
    )


def counts(path: Path) -> tuple[int, int, int]:
    with sqlite3.connect(path) as db:
        pages = db.execute("SELECT count(*) FROM acquisition_pages").fetchone()
        observations = db.execute("SELECT count(*) FROM entity_observations").fetchone()
        results = db.execute("SELECT count(*) FROM acquisition_run_results").fetchone()
    assert pages is not None and observations is not None and results is not None
    return int(pages[0]), int(observations[0]), int(results[0])


def test_import_has_no_network_archive_or_background_side_effects(
    tmp_path: Path,
) -> None:
    script = """
import sqlite3
import urllib.request
real_connect = sqlite3.connect
def forbidden(*args, **kwargs):
    raise AssertionError('network or archive open on import')
def guarded_connect(path, *args, **kwargs):
    if path != ':memory:':
        forbidden()
    return real_connect(path, *args, **kwargs)
sqlite3.connect = guarded_connect
urllib.request.build_opener = forbidden
import services.market_universe.collect
import services.web_dashboard.app
"""
    subprocess.run([sys.executable, "-c", script], check=True, cwd=Path(__file__).parents[1])
    assert list(tmp_path.iterdir()) == []


def test_cli_without_live_flag_never_opens_network_or_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("gate bypassed")

    monkeypatch.setattr("urllib.request.build_opener", forbidden)
    monkeypatch.setattr("services.market_universe.collect.collect_evidence", forbidden)
    archive = tmp_path / "must-not-exist.sqlite"
    assert main(["--archive", str(archive)]) == 2
    assert not archive.exists()
    assert "NOT STARTED" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["--archive", str(archive), "--live-public-read=false"])
    assert not archive.exists()


def test_complete_collection_uses_one_authority_and_zero_influence(tmp_path: Path) -> None:
    path = tmp_path / "archive.sqlite"
    receipt = collect_evidence(path, complete_transport(), clock=lambda: NOW)
    assert receipt.complete
    assert receipt.market_run.completeness is Completeness.COMPLETE
    assert receipt.event_run.completeness is Completeness.COMPLETE
    assert receipt.production_influence == Decimal("0")
    assert receipt.archive_authority_id == UniverseObservationArchive(path).authority_id
    assert counts(path) == (2, 2, 2)


@pytest.mark.parametrize(
    ("resource", "pages"),
    [
        ("markets", [TimeoutError("secret-token")]),
        ("events", [TimeoutError("secret-token")]),
    ],
)
def test_one_resource_failure_makes_collection_incomplete(
    tmp_path: Path, resource: str, pages: list[object]
) -> None:
    transport = complete_transport()
    transport.pages[resource] = pages
    receipt = collect_evidence(tmp_path / "archive.sqlite", transport, clock=lambda: NOW)
    assert not receipt.complete
    failed = receipt.market_run if resource == "markets" else receipt.event_run
    assert failed.completeness is Completeness.FAILED
    assert failed.failure == "TimeoutError"
    assert len(transport.calls) == 2


def test_pagination_completes_only_at_natural_end(tmp_path: Path) -> None:
    transport = FakeTransport(
        {
            "markets": [
                {"markets": [market("M1")], "cursor": "next"},
                {"markets": [market("M2")], "cursor": ""},
            ],
            "events": [
                {"events": [event("E1")], "cursor": "next"},
                {"events": [event("E2")], "cursor": ""},
            ],
        }
    )
    receipt = collect_evidence(
        tmp_path / "archive.sqlite", transport, max_pages=2, clock=lambda: NOW
    )
    assert receipt.complete
    assert receipt.market_run.pages == receipt.event_run.pages == 2


def test_page_bound_is_truthful_and_archives_prior_pages(tmp_path: Path) -> None:
    transport = FakeTransport(
        {
            "markets": [{"markets": [market()], "cursor": "still-more"}],
            "events": [{"events": [event()], "cursor": ""}],
        }
    )
    path = tmp_path / "archive.sqlite"
    receipt = collect_evidence(path, transport, max_pages=1, clock=lambda: NOW)
    assert not receipt.complete
    assert receipt.market_run.completeness is Completeness.PARTIAL
    assert receipt.market_run.failure == "bounded_truncation"
    assert counts(path) == (2, 2, 2)


def test_repeated_runs_append_without_rewriting_history(tmp_path: Path) -> None:
    path = tmp_path / "archive.sqlite"
    moments = iter(NOW + timedelta(microseconds=i) for i in range(20))
    collect_evidence(path, complete_transport(), clock=lambda: next(moments))
    first = counts(path)
    with sqlite3.connect(path) as db:
        old_rows = tuple(db.execute("SELECT * FROM acquisition_pages ORDER BY page_id"))
    collect_evidence(path, complete_transport(), clock=lambda: next(moments))
    with sqlite3.connect(path) as db:
        preserved = tuple(
            db.execute(
                "SELECT * FROM acquisition_pages WHERE page_id IN (?,?) ORDER BY page_id",
                (old_rows[0][0], old_rows[1][0]),
            )
        )
    assert first == (2, 2, 2)
    assert counts(path) == (4, 4, 4)
    assert preserved == old_rows


@pytest.mark.parametrize(
    "path",
    [
        "/trade-api/v2/orders?",
        "/trade-api/v2/portfolio?",
        "/trade-api/v2/positions?",
        "/trade-api/v2/cancel?",
        "/trade-api/v2/../../orders?",
        "/v2/portfolio/orders?",
        "https://evil.example/trade-api/v2/events?",
        "/trade-api/v2/events?foo=x",
    ],
)
def test_public_transport_rejects_resource_injection_before_network(
    path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("network reached")

    monkeypatch.setattr("urllib.request.build_opener", forbidden)
    with pytest.raises(CollectionError, match="resource rejected"):
        PublicUniverseTransport().get(path, timeout_seconds=1)
    assert frozenset({"markets", "events"}) == ALLOWED_RESOURCES


def test_malformed_payload_fails_closed(tmp_path: Path) -> None:
    transport = complete_transport()
    transport.pages["events"] = [{"events": "not-a-list", "cursor": ""}]
    receipt = collect_evidence(tmp_path / "archive.sqlite", transport, clock=lambda: NOW)
    assert not receipt.complete
    assert receipt.event_run.failure == "malformed_page"


def test_corrupt_archive_fails_before_transport_and_is_not_repaired(tmp_path: Path) -> None:
    path = tmp_path / "archive.sqlite"
    UniverseObservationArchive(path)
    with sqlite3.connect(path) as db:
        db.execute("DROP TRIGGER observations_no_delete")
    transport = complete_transport()
    with pytest.raises(ArchiveError):
        collect_evidence(path, transport, clock=lambda: NOW)
    assert transport.calls == []
    with sqlite3.connect(path) as db:
        trigger = db.execute(
            "SELECT count(*) FROM sqlite_master WHERE name='observations_no_delete'"
        ).fetchone()
    assert trigger == (0,)


def test_archive_append_failure_cannot_report_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reject_append(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise ArchiveError("local-secret-detail")

    monkeypatch.setattr(UniverseObservationArchive, "_archive_acquired_page", reject_append)
    receipt = collect_evidence(tmp_path / "archive.sqlite", complete_transport(), clock=lambda: NOW)
    assert not receipt.complete
    assert receipt.market_run.completeness is Completeness.FAILED
    assert receipt.event_run.completeness is Completeness.FAILED
    assert receipt.market_run.failure == receipt.event_run.failure == "ArchiveError"


def test_failure_output_is_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "private-secret-value"

    def fail(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError(secret)

    monkeypatch.setattr("services.market_universe.collect.collect_evidence", fail)
    assert main(["--archive", str(tmp_path / "archive.sqlite"), "--live-public-read"]) == 1
    output = capsys.readouterr().out
    assert secret not in output
    assert "RuntimeError" in output


def test_m26f_writer_boundary_and_m26g_m9_disconnection_remain_intact() -> None:
    archive_source = inspect.getsource(UniverseObservationArchive)
    collector_source = inspect.getsource(
        importlib.import_module("services.market_universe.collect")
    )
    assert not hasattr(UniverseObservationArchive, "append_page")
    assert "_UniverseSynchronizer__archive_writer" not in collector_source
    assert "_acquisition_writer_for_synchronizer" not in collector_source
    assert "EvidenceUnitAssignment" not in collector_source
    assert "paired_event_interval" not in collector_source
    assert "production_execution" not in collector_source
    assert evidence_units._REPOSITORY_REVIEWED_AUTHORITIES == ()
    assert "authoritative archive writes require acquisition capability" in archive_source
