"""Authoritative orderbook snapshot -- fake-transport-only tests.

No network, no credentials, no account access. Proves: happy-path acquisition/validation
round-trips; every listed rejection reason (ticker mismatch, malformed JSON, missing sides,
invalid price/size, duplicate/ambiguous levels, body-hash mismatch, future timestamp, stale
evidence, wrong origin/path, ambiguous multi-entry response, symlink evidence path) fails
closed; and the validated output feeds the EXISTING, UNMODIFIED
``live_economics.normalize_live_orderbook`` with zero changes required there.
"""

from __future__ import annotations

import ast
import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from services.market_universe.orderbook_snapshot import (
    HOST,
    OrderbookAcquisitionError,
    _expected_path,
    acquire_orderbook_snapshot,
    read_orderbook_snapshot_evidence,
    validate_orderbook_snapshot,
)
from services.market_universe.pricing import PriceLadder
from services.market_universe.public_read import BASE, PublicReadFailure, get_orderbook_with_body
from services.opportunity_engine.authoritative_economics import build_authoritative_market_economics
from services.opportunity_engine.fees import current_event_formula_policy
from services.opportunity_engine.live_economics import normalize_live_orderbook
from services.opportunity_engine.live_fees import (
    CurrentSeriesFeeObservation,
    EventFeeOverride,
    resolve_current_fee_regime,
)
from tests.test_m27i_live_weather_preflight import _raw_market, _series_payload, _snapshot_payload

NOW = datetime(2026, 8, 20, 3, 0, 0, tzinfo=UTC)
TICKER = "M"


def test_default_production_transport_is_the_one_reviewed_public_read_helper() -> None:
    """Proves the wiring without ever calling it: no live network occurs in this assertion."""
    import inspect

    default = inspect.signature(acquire_orderbook_snapshot).parameters["transport"].default
    assert default is get_orderbook_with_body


def _orderbooks_payload(
    ticker: str = TICKER,
    *,
    yes: tuple[tuple[str, str], ...] = (("0.300", "5"),),
    no: tuple[tuple[str, str], ...] = (("0.650", "5"),),
    entries: int = 1,
) -> dict[str, object]:
    entry = {
        "ticker": ticker,
        "orderbook_fp": {
            "yes_dollars": [list(level) for level in yes],
            "no_dollars": [list(level) for level in no],
        },
    }
    return {"orderbooks": [entry] * entries}


def _fake_transport(
    *,
    ticker: str = TICKER,
    payload: dict[str, object] | None = None,
    observed_at: datetime = NOW,
    status: int = 200,
    path_override: str | None = None,
    tamper_body_sha256: str | None = None,
):
    payload = payload if payload is not None else _orderbooks_payload(ticker)
    body = json.dumps(payload, sort_keys=True).encode()

    def transport(requested_ticker: str) -> tuple[dict[str, object], bytes]:
        evidence: dict[str, object] = {
            "path": path_override if path_override is not None else _expected_path(ticker),
            "observed_at": observed_at.isoformat(),
            "status": status,
            "body_sha256": tamper_body_sha256 or hashlib.sha256(body).hexdigest(),
            "bytes": len(body),
            "classification": "SUCCESS" if status == 200 else "HTTP_OR_NETWORK_FAILURE",
        }
        if status == 200:
            evidence["payload"] = json.loads(body)
        return evidence, body

    return transport


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_acquire_happy_path() -> None:
    snapshot = acquire_orderbook_snapshot(TICKER, transport=_fake_transport(), clock=lambda: NOW)
    assert snapshot.succeeded is True
    assert snapshot.ticker == TICKER
    assert snapshot.host == HOST
    assert snapshot.path == f"{BASE}/markets/orderbooks?tickers={TICKER}"
    assert snapshot.yes_levels == (("0.300", "5"),)
    assert snapshot.no_levels == (("0.650", "5"),)
    assert snapshot.orderbook_identity


def test_validate_happy_path_round_trips() -> None:
    snapshot = acquire_orderbook_snapshot(TICKER, transport=_fake_transport(), clock=lambda: NOW)
    result = validate_orderbook_snapshot(snapshot.to_json(), expected_ticker=TICKER, now=NOW)
    assert result.succeeded is True
    assert result.yes_levels == snapshot.yes_levels
    assert result.no_levels == snapshot.no_levels
    assert result.orderbook_identity == snapshot.orderbook_identity


def test_raw_orderbook_for_economics_feeds_existing_unmodified_normalizer() -> None:
    """Proves no change to live_economics.py is required: the validated snapshot's own
    raw_orderbook shape is accepted as-is by the existing normalize_live_orderbook."""
    snapshot = acquire_orderbook_snapshot(TICKER, transport=_fake_transport(), clock=lambda: NOW)
    result = validate_orderbook_snapshot(snapshot.to_json(), expected_ticker=TICKER, now=NOW)
    ladder = PriceLadder.parse("deci_cent", [{"start": "0.0000", "end": "1.0000", "step": ".001"}])

    observation = normalize_live_orderbook(
        result.raw_orderbook_for_economics(),
        ticker=TICKER,
        ladder=ladder,
        source_id="orderbook-snapshot",
        observed_at=NOW,
        market_rules_hash="rules-hash",
    )
    assert observation.market_rules_hash == "rules-hash"
    assert observation.source_id == "orderbook-snapshot"


def test_raw_orderbook_for_economics_feeds_existing_unmodified_authoritative_economics() -> None:
    """End-to-end proof, mirroring tests/test_m27i_live_weather_preflight.py's own
    _authoritative_economics helper exactly, except the hand-built book_raw dict is replaced by
    THIS module's independently-validated snapshot output -- confirming
    build_authoritative_market_economics needs no change to accept it."""
    ticker, event_ticker = "M", "E"
    payload = _orderbooks_payload(ticker, yes=(("0.300", "5"),), no=(("0.650", "5"),))
    snapshot = acquire_orderbook_snapshot(
        ticker, transport=_fake_transport(ticker=ticker, payload=payload), clock=lambda: NOW
    )
    result = validate_orderbook_snapshot(snapshot.to_json(), expected_ticker=ticker, now=NOW)

    raw_market = _raw_market(ticker, event_ticker)
    snapshot_payload = _snapshot_payload(NOW, ticker=ticker, raw_market=raw_market)
    ladder = PriceLadder.parse("deci_cent", [{"start": "0.0000", "end": "1.0000", "step": ".001"}])
    series_observation = CurrentSeriesFeeObservation.parse(_series_payload(NOW), observed_at=NOW)
    event = EventFeeOverride.parse({})
    regime = resolve_current_fee_regime(series_observation, event)
    policy = current_event_formula_policy(
        fee_type=regime.fee_type, fee_multiplier=regime.fee_multiplier
    )

    economics, _binding = build_authoritative_market_economics(
        snapshot_payload=snapshot_payload,
        expected_market_ticker=ticker,
        expected_event_ticker=event_ticker,
        series_ticker="CLIMDW",
        market_source_id="market-source",
        raw_orderbook=result.raw_orderbook_for_economics(),
        ladder=ladder,
        orderbook_source_id=f"orderbook-snapshot-{ticker}",
        orderbook_observed_at=NOW,
        series_fee_observation_id=regime.series_observation_id,
        resolved_fee_regime_id=regime.regime_id,
        event_fee_hash=regime.event_metadata_hash,
        fee_policy=policy,
        fee_regime=regime,
        requested_quantity=Decimal("1.00"),
        economics_observed_at=NOW,
    )
    assert economics.market_ticker == ticker
    assert economics.yes is not None
    assert economics.no is not None


# ---------------------------------------------------------------------------
# Acquisition-side rejections
# ---------------------------------------------------------------------------


def test_acquire_rejects_ticker_mismatch_in_response() -> None:
    payload = _orderbooks_payload("OTHER-TICKER")
    snapshot = acquire_orderbook_snapshot(
        TICKER, transport=_fake_transport(payload=payload), clock=lambda: NOW
    )
    assert snapshot.succeeded is False
    assert "ticker" in (snapshot.reason or "").lower()


def test_acquire_rejects_malformed_json() -> None:
    def transport(ticker: str) -> tuple[dict[str, object], bytes]:
        body = b"{not valid json"
        return {
            "path": _expected_path(TICKER),
            "observed_at": NOW.isoformat(),
            "status": 200,
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "classification": "SUCCESS",
        }, body

    snapshot = acquire_orderbook_snapshot(TICKER, transport=transport, clock=lambda: NOW)
    assert snapshot.succeeded is False
    assert snapshot.classification == "MALFORMED_ENVELOPE"


def test_acquire_rejects_missing_side() -> None:
    payload = {"orderbooks": [{"ticker": TICKER, "orderbook_fp": {"yes_dollars": [["0.3", "1"]]}}]}
    snapshot = acquire_orderbook_snapshot(
        TICKER, transport=_fake_transport(payload=payload), clock=lambda: NOW
    )
    assert snapshot.succeeded is False
    assert "no_dollars" in (snapshot.reason or "")


def test_acquire_rejects_invalid_price() -> None:
    payload = _orderbooks_payload(yes=(("1.50", "5"),))  # outside open (0,1) domain
    snapshot = acquire_orderbook_snapshot(
        TICKER, transport=_fake_transport(payload=payload), clock=lambda: NOW
    )
    assert snapshot.succeeded is False
    assert "price" in (snapshot.reason or "").lower()


def test_acquire_rejects_negative_size() -> None:
    payload = _orderbooks_payload(yes=(("0.30", "-1"),))
    snapshot = acquire_orderbook_snapshot(
        TICKER, transport=_fake_transport(payload=payload), clock=lambda: NOW
    )
    assert snapshot.succeeded is False
    assert "negative" in (snapshot.reason or "").lower()


def test_acquire_rejects_duplicate_price_level() -> None:
    payload = _orderbooks_payload(yes=(("0.30", "5"), ("0.30", "9")))
    snapshot = acquire_orderbook_snapshot(
        TICKER, transport=_fake_transport(payload=payload), clock=lambda: NOW
    )
    assert snapshot.succeeded is False
    assert "duplicate" in (snapshot.reason or "").lower()


def test_acquire_rejects_ambiguous_multi_entry_response() -> None:
    payload = _orderbooks_payload(entries=2)
    snapshot = acquire_orderbook_snapshot(
        TICKER, transport=_fake_transport(payload=payload), clock=lambda: NOW
    )
    assert snapshot.succeeded is False
    assert "ambiguous" in (snapshot.reason or "").lower()


def test_acquire_rejects_body_hash_mismatch() -> None:
    snapshot = acquire_orderbook_snapshot(
        TICKER,
        transport=_fake_transport(tamper_body_sha256="0" * 64),
        clock=lambda: NOW,
    )
    assert snapshot.succeeded is False
    assert snapshot.classification == "MALFORMED_ENVELOPE"


def test_acquire_rejects_wrong_path() -> None:
    snapshot = acquire_orderbook_snapshot(
        TICKER,
        transport=_fake_transport(path_override=f"{BASE}/markets/{TICKER}"),
        clock=lambda: NOW,
    )
    assert snapshot.succeeded is False
    assert snapshot.classification == "HTTP_OR_NETWORK_FAILURE"


def test_acquire_rejects_transport_failure() -> None:
    def transport(ticker: str) -> tuple[dict[str, object], bytes]:
        raise PublicReadFailure("HTTP/network failure: simulated")

    snapshot = acquire_orderbook_snapshot(TICKER, transport=transport, clock=lambda: NOW)
    assert snapshot.succeeded is False
    assert snapshot.classification == "ACQUISITION_FAILURE"


# ---------------------------------------------------------------------------
# Validation-side rejections (tampering an already-acquired, serialized payload)
# ---------------------------------------------------------------------------


def test_validate_rejects_future_timestamp() -> None:
    future = NOW + timedelta(seconds=10)
    snapshot = acquire_orderbook_snapshot(
        TICKER, transport=_fake_transport(observed_at=future), clock=lambda: future
    )
    result = validate_orderbook_snapshot(snapshot.to_json(), expected_ticker=TICKER, now=NOW)
    assert result.succeeded is False
    assert "future" in (result.reason or "").lower()


def test_validate_rejects_stale_evidence() -> None:
    old = NOW - timedelta(minutes=5)
    snapshot = acquire_orderbook_snapshot(
        TICKER, transport=_fake_transport(observed_at=old), clock=lambda: old
    )
    result = validate_orderbook_snapshot(snapshot.to_json(), expected_ticker=TICKER, now=NOW)
    assert result.succeeded is False
    assert result.classification == "ORDERBOOK_EVIDENCE_STALE"


def test_validate_rejects_wrong_origin() -> None:
    snapshot = acquire_orderbook_snapshot(TICKER, transport=_fake_transport(), clock=lambda: NOW)
    tampered = dict(snapshot.to_json())
    tampered["host"] = "https://not-kalshi.example.com"
    result = validate_orderbook_snapshot(tampered, expected_ticker=TICKER, now=NOW)
    assert result.succeeded is False
    assert result.classification == "SOURCE_AUTHORITY_MISMATCH"


def test_validate_rejects_tampered_levels_not_matching_raw_body() -> None:
    snapshot = acquire_orderbook_snapshot(TICKER, transport=_fake_transport(), clock=lambda: NOW)
    tampered = dict(snapshot.to_json())
    tampered["yes_levels"] = [["0.999", "1"]]  # stamped field no longer matches raw_body_b64
    result = validate_orderbook_snapshot(tampered, expected_ticker=TICKER, now=NOW)
    assert result.succeeded is False
    assert "independent re-parse" in (result.reason or "")


def test_validate_rejects_extra_field() -> None:
    snapshot = acquire_orderbook_snapshot(TICKER, transport=_fake_transport(), clock=lambda: NOW)
    tampered = dict(snapshot.to_json())
    tampered["executable"] = True  # never a real field -- must never be read/trusted
    result = validate_orderbook_snapshot(tampered, expected_ticker=TICKER, now=NOW)
    assert result.succeeded is False
    assert result.classification == "MALFORMED_SNAPSHOT_EVIDENCE"


# ---------------------------------------------------------------------------
# File-backed evidence: symlink/path-trick rejection
# ---------------------------------------------------------------------------


def test_read_orderbook_snapshot_evidence_rejects_symlink(tmp_path: Path) -> None:
    real_file = tmp_path / "real.json"
    real_file.write_text(json.dumps({"a": 1}))
    link = tmp_path / "link.json"
    link.symlink_to(real_file)

    with pytest.raises(OrderbookAcquisitionError, match="symlink"):
        read_orderbook_snapshot_evidence(link)


def test_read_orderbook_snapshot_evidence_happy_path(tmp_path: Path) -> None:
    real_file = tmp_path / "real.json"
    real_file.write_text(json.dumps({"a": 1}))
    result = read_orderbook_snapshot_evidence(real_file)
    assert result == {"a": 1}


# ---------------------------------------------------------------------------
# Zero credentials/account/write capability
# ---------------------------------------------------------------------------

_FORBIDDEN_NAMES = {
    "KalshiAccountClient",
    "RequestSigner",
    "ExactReadCredential",
    "AuthorizationStore",
    "CanaryStore",
    "ProtectedWriteCredentialStore",
}


def test_module_has_no_credential_account_or_write_capability() -> None:
    source = Path("services/market_universe/orderbook_snapshot.py").read_text()
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    hits = names & _FORBIDDEN_NAMES
    assert not hits, f"orderbook_snapshot.py references forbidden credential/account names: {hits}"
    assert "kalshi_account_gateway" not in source

    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)
    # urllib.parse (pure string encoding, no network) is fine; the actual network-capable
    # modules (http.client, urllib.request, requests) must never be imported here -- this
    # module only ever receives already-fetched bytes via an injected transport callable.
    network_modules = {"http.client", "urllib.request", "requests"}
    assert not (imported_modules & network_modules), imported_modules & network_modules
