"""M27N.2 -- Event/Market reconstruction tests.

Fake-transport-only, no network. Proves: an already-PASSing event/market snapshot payload
reconstructs the exact typed ``Event``/``Market`` object the frozen validator itself independently
parsed and discarded; any snapshot that does not itself validate, or any tampering of the raw body
that would change what a fresh reparse yields, fails closed with
:class:`EvidenceReconstructionError` -- this module never falls back to trusting a stamped field.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime

import pytest

from services.market_universe.domain import Event, Market
from services.market_universe.event_snapshot import acquire_event_snapshot
from services.supervised_canary.m27n2_evidence_reconstruction import (
    EvidenceReconstructionError,
    reconstruct_event,
    reconstruct_market,
)
from tests.test_event_snapshot import _event_payload
from tests.test_event_snapshot import _fake_transport as _event_fake_transport
from tests.test_m27i_live_weather_preflight import _raw_market, _snapshot_payload

NOW = datetime(2026, 8, 20, 3, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Event
# ---------------------------------------------------------------------------


def test_reconstruct_event_happy_path_returns_the_exact_typed_object() -> None:
    payload = acquire_event_snapshot(
        "E", transport=_event_fake_transport(ticker="E"), clock=lambda: NOW
    ).to_json()

    event = reconstruct_event(payload, expected_ticker="E")

    assert isinstance(event, Event)
    assert event.ticker == "E"
    assert event.series_ticker == "CLIMDW"
    assert event.raw == _event_payload("E")["event"]


def test_reconstruct_event_rejects_evidence_that_does_not_itself_validate() -> None:
    payload = acquire_event_snapshot(
        "E", transport=_event_fake_transport(ticker="E"), clock=lambda: NOW
    ).to_json()
    payload["ticker"] = "WRONG-TICKER"

    with pytest.raises(EvidenceReconstructionError, match="did not validate"):
        reconstruct_event(payload, expected_ticker="E")


def test_reconstruct_event_rejects_tampered_raw_body_bytes() -> None:
    """A body whose bytes were swapped for a different-but-still-hash-consistent envelope must
    still fail: the validator's own body_sha256 re-check catches this before reparse is reached."""
    payload = acquire_event_snapshot(
        "E", transport=_event_fake_transport(ticker="E"), clock=lambda: NOW
    ).to_json()
    tampered_body = json.dumps(_event_payload("E", title="Tampered Title")).encode()
    payload["raw_body_b64"] = base64.b64encode(tampered_body).decode("ascii")

    with pytest.raises(EvidenceReconstructionError, match="did not validate"):
        reconstruct_event(payload, expected_ticker="E")


def test_reconstruct_event_rejects_non_object_payload() -> None:
    with pytest.raises(EvidenceReconstructionError, match="did not validate"):
        reconstruct_event("not-an-object", expected_ticker="E")


# ---------------------------------------------------------------------------
# Market
# ---------------------------------------------------------------------------


def test_reconstruct_market_happy_path_returns_the_exact_typed_object() -> None:
    raw_market = _raw_market("M", "E")
    payload = _snapshot_payload(NOW, ticker="M", raw_market=raw_market, observed_at=NOW)

    market = reconstruct_market(payload, expected_ticker="M", expected_event_ticker="E")

    assert isinstance(market, Market)
    assert market.ticker == "M"
    assert market.event_ticker == "E"
    assert market.raw == raw_market


def test_reconstruct_market_rejects_evidence_that_does_not_itself_validate() -> None:
    raw_market = _raw_market("M", "E")
    payload = _snapshot_payload(NOW, ticker="M", raw_market=raw_market, observed_at=NOW)

    with pytest.raises(EvidenceReconstructionError, match="did not validate"):
        reconstruct_market(payload, expected_ticker="OTHER", expected_event_ticker="E")


def test_reconstruct_market_rejects_tampered_body_hash_binding() -> None:
    raw_market = _raw_market("M", "E")
    payload = dict(_snapshot_payload(NOW, ticker="M", raw_market=raw_market, observed_at=NOW))
    payload["body_sha256"] = hashlib.sha256(b"different-bytes-entirely").hexdigest()

    with pytest.raises(EvidenceReconstructionError, match="did not validate"):
        reconstruct_market(payload, expected_ticker="M", expected_event_ticker="E")


def test_reconstruct_market_rejects_non_object_payload() -> None:
    with pytest.raises(EvidenceReconstructionError, match="did not validate"):
        reconstruct_market(None, expected_ticker="M", expected_event_ticker="E")
