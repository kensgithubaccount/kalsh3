from __future__ import annotations

import json
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from services.market_universe.collect import (
    OPEN_NON_MVE_V2,
    CollectionError,
    PublicUniverseTransport,
)
from services.market_universe.sync import (
    Completeness,
    MemoryUniverseRepository,
    UniverseSynchronizer,
)
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


def test_exact_series_and_canonical_pagination_are_accepted(
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
    transport = PublicUniverseTransport()
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
        PublicUniverseTransport().get(path, timeout_seconds=1)


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
    transport = PublicUniverseTransport()
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
    run = UniverseSynchronizer(PublicUniverseTransport(), repo).sync(
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


def test_historical_scope_identity_is_unchanged() -> None:
    assert OPEN_NON_MVE_V2.series_endpoint is None
    assert OPEN_NON_MVE_V2.policy_version == "m26h3-reviewed-public-scope-v2"
