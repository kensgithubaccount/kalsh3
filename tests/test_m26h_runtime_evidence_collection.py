from __future__ import annotations

import http.client
import importlib
import inspect
import sqlite3
import subprocess
import sys
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from services.agent_control_center import evidence_units
from services.market_universe.archive import ArchiveError, UniverseObservationArchive
from services.market_universe.collect import (
    ALLOWED_RESOURCES,
    M26H1_SCOPE_POLICY_VERSION,
    OPEN_NON_MVE_V1,
    REVIEWED_SCOPES,
    CollectionError,
    CollectionScope,
    PublicUniverseTransport,
    collect_evidence,
    main,
)
from services.market_universe.sync import (
    Completeness,
    MemoryUniverseRepository,
    SyncProgress,
    UniverseSynchronizer,
)

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
                {"events": [event("E")], "cursor": "next"},
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
    assert (
        main(
            [
                "--archive",
                str(tmp_path / "archive.sqlite"),
                "--live-public-read",
                "--scope",
                "open-non-mve-v1",
            ]
        )
        == 1
    )
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


def test_live_cli_requires_the_only_reviewed_scope_before_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("side effect reached")

    monkeypatch.setattr("services.market_universe.collect.collect_evidence", forbidden)
    path = tmp_path / "absent.sqlite"
    assert main(["--archive", str(path), "--live-public-read"]) == 2
    assert not path.exists()
    assert "scope is required" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(
            [
                "--archive",
                str(path),
                "--live-public-read",
                "--scope",
                "arbitrary",
            ]
        )
    assert not path.exists()


def test_scope_identity_and_exact_fixed_queries(tmp_path: Path) -> None:
    transport = complete_transport()
    receipt = collect_evidence(tmp_path / "archive.sqlite", transport, clock=lambda: NOW)
    assert receipt.scope == "open-non-mve-v1"
    assert receipt.scope_policy_version == M26H1_SCOPE_POLICY_VERSION
    assert receipt.scope_id == OPEN_NON_MVE_V1.scope_id
    assert transport.calls == [
        "/trade-api/v2/markets?status=open&mve_filter=exclude&limit=1000",
        "/trade-api/v2/events?status=open&limit=200",
    ]


def test_reviewed_scope_registry_is_structurally_immutable() -> None:
    fake = CollectionScope(
        name="fake",
        markets_endpoint="/trade-api/v2/markets",
        markets_parameters=OPEN_NON_MVE_V1.markets_parameters,
        events_endpoint="/trade-api/v2/events",
        events_parameters=OPEN_NON_MVE_V1.events_parameters,
    )
    assert tuple(REVIEWED_SCOPES) == ("open-non-mve-v1",)
    assert REVIEWED_SCOPES["open-non-mve-v1"] is OPEN_NON_MVE_V1
    with pytest.raises(TypeError):
        REVIEWED_SCOPES["fake"] = fake  # type: ignore[index]
    with pytest.raises(TypeError):
        del REVIEWED_SCOPES["open-non-mve-v1"]  # type: ignore[attr-defined]
    with pytest.raises(CollectionError, match="scope rejected"):
        PublicUniverseTransport(fake)
    assert tuple(REVIEWED_SCOPES) == ("open-non-mve-v1",)


def test_cursor_is_encoded_as_one_value_and_cannot_inject(tmp_path: Path) -> None:
    cursor = "next&status=settled#fragment/path?cursor=other"
    transport = FakeTransport(
        {
            "markets": [
                {"markets": [market()], "cursor": cursor},
                {"markets": [], "cursor": ""},
            ],
            "events": [{"events": [event()], "cursor": ""}],
        }
    )
    collect_evidence(tmp_path / "archive.sqlite", transport, clock=lambda: NOW)
    second = transport.calls[1]
    assert "cursor=next%26status%3Dsettled%23fragment%2Fpath%3Fcursor%3Dother" in second
    assert second.count("status=") == 1
    assert second.count("cursor=") == 1


def test_double_encoded_control_is_only_semantically_decoded_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NetworkReached(RuntimeError):
        pass

    def reached(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise NetworkReached

    monkeypatch.setattr("urllib.request.build_opener", reached)
    with pytest.raises(NetworkReached):
        PublicUniverseTransport().get(
            "/trade-api/v2/events?status=open&limit=200&cursor=%250d",
            timeout_seconds=1,
        )


@pytest.mark.parametrize("cursor", ["bad\rvalue", "bad\nvalue", "%0d", "%0a", "%00", "x\x1fy"])
def test_control_character_cursor_stops_before_second_request(tmp_path: Path, cursor: str) -> None:
    transport = FakeTransport(
        {
            "markets": [{"markets": [market()], "cursor": cursor}],
            "events": [{"events": [event()], "cursor": ""}],
        }
    )
    receipt = collect_evidence(tmp_path / "archive.sqlite", transport, clock=lambda: NOW)
    assert receipt.market_run.failure == "invalid_cursor"
    assert len([path for path in transport.calls if "/markets?" in path]) == 1


@pytest.mark.parametrize(
    "path",
    [
        "/trade-api/v2/markets?status=settled&mve_filter=exclude&limit=1000",
        "/trade-api/v2/markets?status=open&mve_filter=exclude&limit=999",
        "/trade-api/v2/markets?status=open&mve_filter=only&limit=1000",
        "/trade-api/v2/markets?status=open&status=open&mve_filter=exclude&limit=1000",
        "/trade-api/v2/markets?status=open&mve_filter=exclude&limit=1000&extra=x",
        "/trade-api/v2/events?status=open&limit=200#fragment",
        "/trade-api/v2/events?status=open&limit=200&cursor=%0d",
        "/trade-api/v2/events?status=open&mve_filter=exclude&limit=1000",
        "/trade-api/v2/markets?status=open&limit=200",
    ],
)
def test_transport_rejects_wrong_scope_shape_before_network(
    path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "urllib.request.build_opener",
        lambda *args, **kwargs: pytest.fail("network reached"),
    )
    with pytest.raises(CollectionError, match="resource rejected"):
        PublicUniverseTransport().get(path, timeout_seconds=1)


@pytest.mark.parametrize("value", [0, -1])
def test_max_pages_nonpositive_rejected(value: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        UniverseSynchronizer(complete_transport(), MemoryUniverseRepository(), max_pages=value)


@pytest.mark.parametrize("value", [True, False, 1.0, "1"])
def test_max_pages_wrong_type_rejected(value: object) -> None:
    with pytest.raises(TypeError, match="positive int"):
        UniverseSynchronizer(complete_transport(), MemoryUniverseRepository(), max_pages=value)  # type: ignore[arg-type]


def test_market_event_coverage_is_conservative_and_extra_events_do_not_repair(
    tmp_path: Path,
) -> None:
    transport = FakeTransport(
        {
            "markets": [{"markets": [market()], "cursor": ""}],
            "events": [{"events": [event("EXTRA")], "cursor": ""}],
        }
    )
    receipt = collect_evidence(tmp_path / "archive.sqlite", transport, clock=lambda: NOW)
    assert receipt.market_run.completeness is Completeness.COMPLETE
    assert receipt.event_run.completeness is Completeness.COMPLETE
    assert not receipt.complete
    assert receipt.market_event_ticker_count == 1
    assert receipt.matched_event_ticker_count == 0
    assert receipt.missing_event_tickers == ("E",)


def test_archive_provenance_has_fixed_scope_parameters(tmp_path: Path) -> None:
    path = tmp_path / "archive.sqlite"
    collect_evidence(path, complete_transport(), clock=lambda: NOW)
    with sqlite3.connect(path) as db:
        rows = db.execute(
            "SELECT endpoint, parameters_json FROM acquisition_pages ORDER BY endpoint"
        ).fetchall()
    assert rows == [
        ("events", '{"limit":"200","status":"open"}'),
        ("markets", '{"limit":"1000","mve_filter":"exclude","status":"open"}'),
    ]


def test_old_unfiltered_rows_are_not_relabelled_as_scoped_complete(tmp_path: Path) -> None:
    path = tmp_path / "archive.sqlite"
    UniverseSynchronizer(
        FakeTransport(
            {
                "markets": [{"markets": [market()], "cursor": ""}],
                "events": [],
            }
        ),
        MemoryUniverseRepository(),
        archive=UniverseObservationArchive(path),
        clock=lambda: NOW,
    ).sync("markets")
    scoped = FakeTransport(
        {
            "markets": [{"markets": [market()], "cursor": "still-more"}],
            "events": [{"events": [event()], "cursor": ""}],
        }
    )
    receipt = collect_evidence(path, scoped, max_pages=1, clock=lambda: NOW)
    assert not receipt.complete
    assert receipt.market_run.failure == "bounded_truncation"
    with sqlite3.connect(path) as db:
        parameters = [
            row[0]
            for row in db.execute("SELECT parameters_json FROM acquisition_pages ORDER BY rowid")
        ]
    assert parameters == [
        "{}",
        '{"limit":"1000","mve_filter":"exclude","status":"open"}',
        '{"limit":"200","status":"open"}',
    ]


def test_invalid_url_is_normalized_without_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    class BadOpener:
        def open(self, *args: object, **kwargs: object) -> None:
            del args, kwargs
            raise http.client.InvalidURL("secret-cursor")

    monkeypatch.setattr("urllib.request.build_opener", lambda *args: BadOpener())
    with pytest.raises(CollectionError, match="request failed") as caught:
        PublicUniverseTransport().get(
            "/trade-api/v2/events?status=open&limit=200", timeout_seconds=1
        )
    assert "secret-cursor" not in str(caught.value)


def test_cli_progress_reports_pages_without_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret_cursor = "cursor-must-not-print"
    transport = FakeTransport(
        {
            "markets": [
                {"markets": [market()], "cursor": secret_cursor},
                {"markets": [], "cursor": ""},
            ],
            "events": [{"events": [event()], "cursor": ""}],
        }
    )
    monkeypatch.setattr(
        "services.market_universe.collect.PublicUniverseTransport", lambda scope: transport
    )
    assert (
        main(
            [
                "--archive",
                str(tmp_path / "archive.sqlite"),
                "--live-public-read",
                "--scope",
                "open-non-mve-v1",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "Starting evidence collection" in output
    assert "Collecting Markets..." in output
    assert "Markets: 1 pages / 1 observed" in output
    assert "Markets: 2 pages / 1 observed" in output
    assert "Collecting Events..." in output
    assert secret_cursor not in output


def test_progress_callback_receives_only_immutable_safe_synchronous_snapshots(
    tmp_path: Path,
) -> None:
    malicious_cursor = "private-cursor&status=settled"
    progress: list[SyncProgress] = []

    class SynchronousTransport(FakeTransport):
        def get(self, path: str, *, timeout_seconds: float) -> dict[str, Any]:
            market_calls = len([call for call in self.calls if "/markets?" in call])
            if "/markets?" in path and market_calls == 1:
                assert progress[-1] == SyncProgress("markets", 1, 1)
            return super().get(path, timeout_seconds=timeout_seconds)

    transport = SynchronousTransport(
        {
            "markets": [
                {"markets": [market()], "cursor": malicious_cursor},
                {"markets": [], "cursor": ""},
            ],
            "events": [{"events": [event()], "cursor": ""}],
        }
    )
    receipt = collect_evidence(
        tmp_path / "archive.sqlite", transport, clock=lambda: NOW, progress=progress.append
    )
    assert receipt.complete
    assert [(item.resource, item.pages, item.records_received) for item in progress] == [
        ("markets", 0, 0),
        ("markets", 1, 1),
        ("markets", 2, 1),
        ("events", 0, 0),
        ("events", 1, 1),
    ]
    assert {field.name for field in fields(SyncProgress)} == {
        "resource",
        "pages",
        "records_received",
    }
    assert all(
        not hasattr(item, "cursor") and not hasattr(item, "last_cursor") for item in progress
    )
    assert malicious_cursor not in repr(progress)
    with pytest.raises(FrozenInstanceError):
        progress[0].pages = 99  # type: ignore[misc]
