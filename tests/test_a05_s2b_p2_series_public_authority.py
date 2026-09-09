from __future__ import annotations

import json
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from services.cycle_deadline import CycleDeadline
from services.market_universe.archive import EntityKind, UniverseObservationArchive
from services.market_universe.collect import (
    OPEN_NON_MVE_V2,
    S2A_PUBLIC_SERIES_SCOPE,
    CollectionError,
    PublicUniverseTransport,
)
from services.market_universe.domain import Event
from services.market_universe.sync import (
    Completeness,
    MemoryUniverseRepository,
    UniverseSynchronizer,
)
from services.opportunity_engine import structural_measurement_runner as runner_module
from services.opportunity_engine.structural_measurement_runner import refresh_universe
from services.prospective_shadow.runner import CycleDiagnostics, _hydrate_candidate_series


class _Response:
    status = 200

    def __init__(self, payload: object) -> None:
        self.headers = Message()
        self.headers["Content-Type"] = "application/json"
        self._raw = json.dumps(payload).encode()

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def read(self, size: int = -1) -> bytes:
        return self._raw[:size]


class _Opener:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.urls: list[str] = []

    def open(self, request: Any, *, timeout: float) -> _Response:
        del timeout
        self.urls.append(request.full_url)
        path = request.full_url.removeprefix("https://external-api.kalshi.com")
        return _Response(self.responses[path])


def _series() -> dict[str, object]:
    return {
        "ticker": "S",
        "title": "Series",
        "category": "Economics",
        "frequency": "event",
        "tags": [],
        "settlement_sources": [],
    }


def _market() -> dict[str, object]:
    return {
        "ticker": "M",
        "event_ticker": "E",
        "title": "Market",
        "market_type": "binary",
        "status": "active",
        "rules_primary": "rules",
        "rules_secondary": "secondary",
        "settlement_sources": [],
        "price_level_structure": "linear",
        "price_ranges": [{"min": "0.01", "max": "0.99", "step": "0.01"}],
        "fractional_trading_enabled": False,
        "is_provisional": False,
    }


def _event() -> dict[str, object]:
    return {"event_ticker": "E", "series_ticker": "S", "title": "Event"}


def test_default_transport_is_historical_and_rejects_series(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: pytest.fail("network reached"))
    with pytest.raises(CollectionError, match="resource rejected"):
        PublicUniverseTransport().get("/trade-api/v2/series?limit=1000", timeout_seconds=1)


def test_explicit_historical_scope_rejects_series(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: pytest.fail("network reached"))
    with pytest.raises(CollectionError, match="resource rejected"):
        PublicUniverseTransport(OPEN_NON_MVE_V2).get("/trade-api/v2/series/S", timeout_seconds=1)


def test_explicit_s2a_series_scope_accepts_only_exact_series(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener = _Opener({"/trade-api/v2/series/S": {"series": _series()}})
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: opener)
    transport = PublicUniverseTransport(S2A_PUBLIC_SERIES_SCOPE)
    transport.get("/trade-api/v2/series/S", timeout_seconds=1)
    assert opener.urls == ["https://external-api.kalshi.com/trade-api/v2/series/S"]


@pytest.mark.parametrize(
    "path",
    [
        "/trade-api/v2/series",
        "/trade-api/v2/series?limit=100",
        "/trade-api/v2/series?limit=999",
        "/trade-api/v2/series?limit=1001",
        "/trade-api/v2/series?limit=1000&extra=x",
        "/trade-api/v2/series?limit=1000&limit=1000",
        "https://evil.example/trade-api/v2/series?limit=1000",
        "/trade-api/v2/series/S?limit=1000",
        "/trade-api/v2/series/S?limit=1000",
        "/trade-api/v2/series/S#fragment",
        "/trade-api/v2/series/S%2fX",
        "/trade-api/v2/series/S%41",
        "/trade-api/v2/series/S\nX",
    ],
)
def test_series_authority_rejects_every_unreviewed_shape(
    path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: pytest.fail("network reached"))
    with pytest.raises(CollectionError, match="resource rejected"):
        PublicUniverseTransport(S2A_PUBLIC_SERIES_SCOPE).get(path, timeout_seconds=1)


def test_existing_markets_events_and_exact_event_authority_remains_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener = _Opener(
        {
            "/trade-api/v2/markets?status=open&mve_filter=exclude&limit=1000": {"markets": []},
            "/trade-api/v2/events?status=open&limit=200": {"events": []},
            "/trade-api/v2/events/E": {"event": _event()},
        }
    )
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: opener)
    transport = PublicUniverseTransport(S2A_PUBLIC_SERIES_SCOPE)
    transport.get(
        "/trade-api/v2/markets?status=open&mve_filter=exclude&limit=1000", timeout_seconds=1
    )
    transport.get("/trade-api/v2/events?status=open&limit=200", timeout_seconds=1)
    transport.get("/trade-api/v2/events/E", timeout_seconds=1)


def test_exact_series_reconciliation_uses_actual_reviewed_transport_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opener = _Opener({"/trade-api/v2/series/S": {"series": _series()}})
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: opener)
    repo = MemoryUniverseRepository()
    run = UniverseSynchronizer(
        PublicUniverseTransport(S2A_PUBLIC_SERIES_SCOPE), repo
    ).reconcile_series(("S",))
    assert run.completeness is Completeness.COMPLETE
    assert set(repo.series) == {"S"}
    assert opener.urls == ["https://external-api.kalshi.com/trade-api/v2/series/S"]


@pytest.mark.parametrize(
    "series_payload",
    [
        {},
        {"series": "not-an-object"},
        {"series": {"ticker": "S"}},
        {"series": {**_series(), "ticker": "OTHER"}},
    ],
)
def test_exact_series_reconciliation_fails_closed_on_malformed_missing_or_mismatched_response(
    monkeypatch: pytest.MonkeyPatch, series_payload: dict[str, object]
) -> None:
    opener = _Opener({"/trade-api/v2/series/S": series_payload})
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: opener)
    repo = MemoryUniverseRepository()
    run = UniverseSynchronizer(
        PublicUniverseTransport(S2A_PUBLIC_SERIES_SCOPE), repo
    ).reconcile_series(("S",))
    assert run.completeness is Completeness.PARTIAL
    assert run.failure == "invalid_exact_series"
    assert repo.series == {}


def test_hydrate_candidate_series_fails_closed_without_network_when_event_authority_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: pytest.fail("network reached"))
    repo = MemoryUniverseRepository()
    scan = SimpleNamespace(leads=(SimpleNamespace(event_ticker="MISSING"),))
    diagnostics = CycleDiagnostics()
    assert not _hydrate_candidate_series(
        archive_path=tmp_path / "archive.sqlite",
        repo=repo,
        scan=scan,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
        deadline=CycleDeadline(30),
        diagnostics=diagnostics,
    )
    assert diagnostics.exact_series_required is None
    assert diagnostics.exact_series_attempted == 0
    assert diagnostics.operational_failures == ["STRUCTURAL_CANDIDATE_EVENT_AUTHORITY_UNAVAILABLE"]


def test_hydrate_candidate_series_fails_closed_without_network_when_bound_exceeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: pytest.fail("network reached"))
    tickers = [f"S{i}" for i in range(201)]
    repo = MemoryUniverseRepository(
        events={
            f"E{i}": Event.parse({"event_ticker": f"E{i}", "series_ticker": t, "title": "T"})
            for i, t in enumerate(tickers)
        }
    )
    scan = SimpleNamespace(leads=tuple(SimpleNamespace(event_ticker=f"E{i}") for i in range(201)))
    diagnostics = CycleDiagnostics()
    assert not _hydrate_candidate_series(
        archive_path=tmp_path / "archive.sqlite",
        repo=repo,
        scan=scan,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
        deadline=CycleDeadline(30),
        diagnostics=diagnostics,
    )
    assert diagnostics.exact_series_required == 201
    assert diagnostics.exact_series_attempted == 0
    assert diagnostics.operational_failures == ["STRUCTURAL_SERIES_REQUEST_BOUND_EXCEEDED"]


def test_exact_series_is_archived_and_candidate_set_is_unique_and_sorted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opener = _Opener({"/trade-api/v2/series/S": {"series": _series()}})
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: opener)
    archive_path = tmp_path / "archive.sqlite"
    repo = MemoryUniverseRepository(events={"E": Event.parse(_event())})
    scan = SimpleNamespace(
        leads=(
            SimpleNamespace(event_ticker="E"),
            SimpleNamespace(event_ticker="E"),
        )
    )
    diagnostics = CycleDiagnostics()
    assert _hydrate_candidate_series(
        archive_path=archive_path,
        repo=repo,
        scan=scan,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
        deadline=CycleDeadline(30),
        diagnostics=diagnostics,
    )
    assert diagnostics.exact_series_required == 1
    assert diagnostics.exact_series_attempted == 1
    assert opener.urls == ["https://external-api.kalshi.com/trade-api/v2/series/S"]
    archived = UniverseObservationArchive(archive_path).at_or_before(
        EntityKind.SERIES, "S", datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)
    )
    assert archived.kind is EntityKind.SERIES
    assert archived.endpoint == "series/S"


def test_s2a_refresh_does_not_require_global_series_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opener = _Opener(
        {
            "/trade-api/v2/markets?status=open&mve_filter=exclude&limit=1000": {"markets": []},
            "/trade-api/v2/events?status=open&limit=200": {"events": []},
        }
    )
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: opener)
    result = refresh_universe(str(tmp_path / "archive.sqlite"), s2a_enabled=True)
    assert result.complete


def test_refresh_selects_authority_by_s2a_mode_and_preserves_injection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected: list[object] = []

    class FixtureTransport:
        def get(self, path: str, *, timeout_seconds: float) -> dict[str, object]:
            del timeout_seconds
            if path.startswith("/trade-api/v2/markets"):
                return {"markets": []}
            if path.startswith("/trade-api/v2/events"):
                return {"events": []}
            if path.startswith("/trade-api/v2/series"):
                return {"series": [_series()]}
            raise AssertionError(path)

    def factory(scope: object) -> FixtureTransport:
        selected.append(scope)
        return FixtureTransport()

    monkeypatch.setattr(runner_module, "PublicUniverseTransport", factory)
    legacy = refresh_universe(str(tmp_path / "legacy.sqlite"), s2a_enabled=False)
    s2a = refresh_universe(str(tmp_path / "s2a.sqlite"), s2a_enabled=True)
    assert legacy.complete and s2a.complete
    assert selected == [OPEN_NON_MVE_V2, S2A_PUBLIC_SERIES_SCOPE]


@pytest.mark.parametrize(
    ("status", "content_type", "raw", "location"),
    [
        (302, "application/json", b'{"series":[]}', "https://evil.example/series"),
        (500, "application/json", b'{"series":[]}', None),
        (200, "text/plain", b'{"series":[]}', None),
        (200, "application/json", b"x" * 8_000_001, None),
        (200, "application/json", b"not-json", None),
    ],
)
def test_explicit_s2a_series_scope_retains_response_fail_closed_checks(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    content_type: str,
    raw: bytes,
    location: str | None,
) -> None:
    class Response:
        def __init__(self) -> None:
            self.status = status
            self.headers = Message()
            self.headers["Content-Type"] = content_type
            if location is not None:
                self.headers["Location"] = location

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            del args

        def read(self, size: int = -1) -> bytes:
            return raw[:size]

    class Opener:
        def open(self, request: Any, *, timeout: float) -> Response:
            del request, timeout
            return Response()

    monkeypatch.setattr("urllib.request.build_opener", lambda *args: Opener())
    with pytest.raises(CollectionError):
        PublicUniverseTransport(S2A_PUBLIC_SERIES_SCOPE).get(
            "/trade-api/v2/series/S", timeout_seconds=1
        )


def test_historical_scope_identity_is_unchanged() -> None:
    assert OPEN_NON_MVE_V2.series_endpoint is None
    assert OPEN_NON_MVE_V2.policy_version == "m26h3-reviewed-public-scope-v2"
    assert (
        OPEN_NON_MVE_V2.scope_id
        == "096ae9711882976cdbf97651d08520ac2cca123c0c861c7ce72b5b4cf4e4a56b"
    )
