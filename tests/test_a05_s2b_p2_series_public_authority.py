from __future__ import annotations

import json
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from services.market_universe.collect import (
    OPEN_NON_MVE_V2,
    S2A_PUBLIC_SERIES_SCOPE,
    CollectionError,
    PublicUniverseTransport,
)
from services.market_universe.sync import (
    Completeness,
    MemoryUniverseRepository,
    UniverseSynchronizer,
)
from services.opportunity_engine import structural_measurement_runner as runner_module
from services.opportunity_engine.structural_measurement_runner import refresh_universe


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
        PublicUniverseTransport(OPEN_NON_MVE_V2).get(
            "/trade-api/v2/series?limit=1000", timeout_seconds=1
        )


def test_explicit_s2a_series_scope_accepts_exact_and_canonical_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener = _Opener(
        {
            "/trade-api/v2/series?limit=1000": {"series": [], "cursor": ""},
            "/trade-api/v2/series?cursor=next&limit=1000": {"series": [], "cursor": ""},
            "/trade-api/v2/series?limit=1000&cursor=next": {"series": [], "cursor": ""},
        }
    )
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: opener)
    transport = PublicUniverseTransport(S2A_PUBLIC_SERIES_SCOPE)
    transport.get("/trade-api/v2/series?limit=1000", timeout_seconds=1)
    transport.get("/trade-api/v2/series?cursor=next&limit=1000", timeout_seconds=1)
    assert opener.urls == [
        "https://external-api.kalshi.com/trade-api/v2/series?limit=1000",
        "https://external-api.kalshi.com/trade-api/v2/series?cursor=next&limit=1000",
    ]


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
        "/trade-api/v2/series?limit=1000&cursor=bad%0Avalue",
        "/trade-api/v2/series?limit=1000&cursor=",
        "/trade-api/v2/series?limit=1000#fragment",
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


def test_series_sync_uses_actual_reviewed_transport_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opener = _Opener({"/trade-api/v2/series?limit=1000": {"series": [_series()]}})
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: opener)
    repo = MemoryUniverseRepository()
    run = UniverseSynchronizer(PublicUniverseTransport(S2A_PUBLIC_SERIES_SCOPE), repo).sync(
        "series", parameters={"limit": "1000"}
    )
    assert run.completeness is Completeness.COMPLETE
    assert set(repo.series) == {"S"}
    assert opener.urls == ["https://external-api.kalshi.com/trade-api/v2/series?limit=1000"]


@pytest.mark.parametrize("series_payload", [{"series": [_series()]}, {"series": "malformed"}])
def test_s2a_refresh_requires_complete_series_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, series_payload: dict[str, object]
) -> None:
    opener = _Opener(
        {
            "/trade-api/v2/markets?status=open&mve_filter=exclude&limit=1000": {"markets": []},
            "/trade-api/v2/events?status=open&limit=200": {"events": []},
            "/trade-api/v2/series?limit=1000": series_payload,
        }
    )
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: opener)
    result = refresh_universe(str(tmp_path / "archive.sqlite"), s2a_enabled=True)
    assert result.complete is (series_payload["series"] != "malformed")


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
            "/trade-api/v2/series?limit=1000", timeout_seconds=1
        )


def test_historical_scope_identity_is_unchanged() -> None:
    assert OPEN_NON_MVE_V2.series_endpoint is None
    assert OPEN_NON_MVE_V2.policy_version == "m26h3-reviewed-public-scope-v2"
    assert (
        OPEN_NON_MVE_V2.scope_id
        == "096ae9711882976cdbf97651d08520ac2cca123c0c861c7ce72b5b4cf4e4a56b"
    )
