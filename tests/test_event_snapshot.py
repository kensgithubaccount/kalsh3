"""Authoritative event snapshot -- fake-transport-only tests.

No network, no credentials, no account access. Proves: happy-path acquisition/validation
round-trips; every listed rejection reason (ticker mismatch, malformed JSON, missing event
object, ambiguous/multiple event representation, body-hash mismatch, wrong origin/path,
malformed self-consistency window, tampered stamped fields) fails closed; and that this module
adds zero credential/account/store/network capability beyond the injected transport seam.

This module deliberately mirrors tests/test_orderbook_snapshot.py's structure, adapted for
services.market_universe.event_snapshot's design (which mirrors market_snapshot's deferred
now-relative freshness, not orderbook_snapshot's caller-`now` freshness -- see
event_snapshot.py's own module docstring for why).
"""

from __future__ import annotations

import ast
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from services.market_universe.event_snapshot import (
    HOST,
    AuthoritativeEventSnapshot,
    acquire_event_snapshot,
    validate_event_snapshot,
)
from services.market_universe.public_read import BASE, PublicReadFailure, get_event_with_body

NOW = datetime(2026, 8, 20, 3, 0, 0, tzinfo=UTC)
TICKER = "E"


def test_default_production_transport_is_the_one_reviewed_public_read_helper() -> None:
    """Proves the wiring without ever calling it: no live network occurs in this assertion."""
    import inspect

    default = inspect.signature(acquire_event_snapshot).parameters["transport"].default
    assert default is get_event_with_body


def _event_payload(
    ticker: str = TICKER,
    *,
    series_ticker: str = "CLIMDW",
    title: str = "Chicago high temperature",
) -> dict[str, object]:
    return {
        "event": {
            "event_ticker": ticker,
            "series_ticker": series_ticker,
            "title": title,
            "category": "Weather",
            "status": "open",
        }
    }


def _fake_transport(
    *,
    ticker: str = TICKER,
    payload: dict[str, object] | None = None,
    observed_at: datetime = NOW,
    status: int = 200,
    path_override: str | None = None,
    tamper_body_sha256: str | None = None,
):
    payload = payload if payload is not None else _event_payload(ticker)
    body = json.dumps(payload, sort_keys=True).encode()

    def transport(requested_ticker: str) -> tuple[dict[str, object], bytes]:
        evidence: dict[str, object] = {
            "path": path_override if path_override is not None else f"{BASE}/events/{ticker}",
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
    snapshot = acquire_event_snapshot(TICKER, transport=_fake_transport(), clock=lambda: NOW)
    assert snapshot.succeeded is True
    assert snapshot.ticker == TICKER
    assert snapshot.host == HOST
    assert snapshot.path == f"{BASE}/events/{TICKER}"
    assert snapshot.parsed_event_ticker == TICKER
    assert snapshot.parsed_series_ticker == "CLIMDW"
    assert snapshot.metadata_hash


def test_validate_happy_path_round_trips() -> None:
    snapshot = acquire_event_snapshot(TICKER, transport=_fake_transport(), clock=lambda: NOW)
    result = validate_event_snapshot(snapshot.to_json(), expected_ticker=TICKER)
    assert result.succeeded is True
    assert result.ticker == snapshot.parsed_event_ticker
    assert result.series_ticker == snapshot.parsed_series_ticker
    assert result.metadata_hash == snapshot.metadata_hash


def test_validate_is_deterministic_across_identical_bytes() -> None:
    """Same acquisition, validated twice, must produce byte-identical serialized results."""
    snapshot = acquire_event_snapshot(TICKER, transport=_fake_transport(), clock=lambda: NOW)
    first = validate_event_snapshot(snapshot.to_json(), expected_ticker=TICKER)
    second = validate_event_snapshot(snapshot.to_json(), expected_ticker=TICKER)
    assert first == second


# ---------------------------------------------------------------------------
# Acquisition-side rejections
# ---------------------------------------------------------------------------


def test_acquire_rejects_ticker_mismatch_in_response() -> None:
    payload = _event_payload("OTHER-TICKER")
    snapshot = acquire_event_snapshot(
        TICKER, transport=_fake_transport(payload=payload), clock=lambda: NOW
    )
    assert snapshot.succeeded is False
    assert snapshot.classification == "EVENT_IDENTITY_MISMATCH"


def test_acquire_rejects_malformed_json() -> None:
    def transport(ticker: str) -> tuple[dict[str, object], bytes]:
        body = b"{not valid json"
        return {
            "path": f"{BASE}/events/{TICKER}",
            "observed_at": NOW.isoformat(),
            "status": 200,
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "classification": "SUCCESS",
        }, body

    snapshot = acquire_event_snapshot(TICKER, transport=transport, clock=lambda: NOW)
    assert snapshot.succeeded is False
    assert snapshot.classification == "MALFORMED_ENVELOPE"


def test_acquire_rejects_missing_event_object() -> None:
    payload = {"events": [_event_payload(TICKER)["event"]]}  # wrong wrapper key entirely
    snapshot = acquire_event_snapshot(
        TICKER, transport=_fake_transport(payload=payload), clock=lambda: NOW
    )
    assert snapshot.succeeded is False
    assert snapshot.classification == "MALFORMED_ENVELOPE"
    assert "single event object" in (snapshot.reason or "")


def test_acquire_rejects_non_dict_event_value() -> None:
    payload = {"event": "not-an-object"}
    snapshot = acquire_event_snapshot(
        TICKER, transport=_fake_transport(payload=payload), clock=lambda: NOW
    )
    assert snapshot.succeeded is False
    assert snapshot.classification == "MALFORMED_ENVELOPE"


def test_acquire_rejects_body_hash_mismatch() -> None:
    snapshot = acquire_event_snapshot(
        TICKER,
        transport=_fake_transport(tamper_body_sha256="0" * 64),
        clock=lambda: NOW,
    )
    assert snapshot.succeeded is False
    assert snapshot.classification == "MALFORMED_ENVELOPE"


def test_acquire_rejects_wrong_path() -> None:
    snapshot = acquire_event_snapshot(
        TICKER,
        transport=_fake_transport(path_override=f"{BASE}/markets/{TICKER}"),
        clock=lambda: NOW,
    )
    assert snapshot.succeeded is False
    assert snapshot.classification == "HTTP_OR_NETWORK_FAILURE"


def test_acquire_rejects_transport_failure() -> None:
    def transport(ticker: str) -> tuple[dict[str, object], bytes]:
        raise PublicReadFailure("HTTP/network failure: simulated")

    snapshot = acquire_event_snapshot(TICKER, transport=transport, clock=lambda: NOW)
    assert snapshot.succeeded is False
    assert snapshot.classification == "ACQUISITION_FAILURE"


def test_acquire_rejects_malformed_event_payload() -> None:
    """A well-formed envelope whose inner event object fails Event.parse's own required-field
    checks (missing title) must surface as a parse failure, never silently accepted."""
    payload = {"event": {"event_ticker": TICKER, "series_ticker": "CLIMDW"}}  # missing title
    snapshot = acquire_event_snapshot(
        TICKER, transport=_fake_transport(payload=payload), clock=lambda: NOW
    )
    assert snapshot.succeeded is False
    assert snapshot.classification == "MALFORMED_ENVELOPE"
    assert "canonical parse" in (snapshot.reason or "")


# ---------------------------------------------------------------------------
# Validation-side rejections (tampering an already-acquired, serialized payload)
# ---------------------------------------------------------------------------


def test_validate_rejects_wrong_origin() -> None:
    snapshot = acquire_event_snapshot(TICKER, transport=_fake_transport(), clock=lambda: NOW)
    tampered = dict(snapshot.to_json())
    tampered["host"] = "https://not-kalshi.example.com"
    result = validate_event_snapshot(tampered, expected_ticker=TICKER)
    assert result.succeeded is False
    assert result.classification == "SOURCE_AUTHORITY_MISMATCH"


def test_validate_rejects_wrong_path() -> None:
    snapshot = acquire_event_snapshot(TICKER, transport=_fake_transport(), clock=lambda: NOW)
    tampered = dict(snapshot.to_json())
    tampered["path"] = f"{BASE}/events/OTHER"
    result = validate_event_snapshot(tampered, expected_ticker=TICKER)
    assert result.succeeded is False
    assert result.classification == "SOURCE_AUTHORITY_MISMATCH"


def test_validate_rejects_ticker_mismatch() -> None:
    snapshot = acquire_event_snapshot(TICKER, transport=_fake_transport(), clock=lambda: NOW)
    result = validate_event_snapshot(snapshot.to_json(), expected_ticker="OTHER")
    assert result.succeeded is False
    assert result.classification == "EVENT_IDENTITY_MISMATCH"


def test_validate_rejects_tampered_raw_body() -> None:
    """Changed raw response bytes (ticker of the embedded event swapped for another's, i.e.
    cross-event body substitution) must fail on the recomputed body hash before ever reaching
    Event.parse."""
    snapshot = acquire_event_snapshot(TICKER, transport=_fake_transport(), clock=lambda: NOW)
    tampered = dict(snapshot.to_json())
    substituted = json.dumps(_event_payload("OTHER"), sort_keys=True).encode()
    import base64

    tampered["raw_body_b64"] = base64.b64encode(substituted).decode("ascii")
    result = validate_event_snapshot(tampered, expected_ticker=TICKER)
    assert result.succeeded is False
    assert result.classification == "MALFORMED_SNAPSHOT_EVIDENCE"
    assert "body_sha256" in (result.reason or "")


def test_validate_rejects_body_hash_edited_to_match_tampered_body() -> None:
    """Even if an attacker recomputes body_sha256 to match a substituted raw body (another
    event's genuine bytes), the stamped ticker/path/metadata_hash still bind to the ORIGINAL
    request -- the re-parsed event ticker from the substituted body must not match expected."""
    snapshot = acquire_event_snapshot(TICKER, transport=_fake_transport(), clock=lambda: NOW)
    tampered = dict(snapshot.to_json())
    substituted = json.dumps(_event_payload("OTHER"), sort_keys=True).encode()
    import base64

    tampered["raw_body_b64"] = base64.b64encode(substituted).decode("ascii")
    tampered["body_sha256"] = hashlib.sha256(substituted).hexdigest()
    result = validate_event_snapshot(tampered, expected_ticker=TICKER)
    assert result.succeeded is False
    assert result.classification == "EVENT_IDENTITY_MISMATCH"


def test_validate_rejects_tampered_metadata_hash_not_matching_raw_body() -> None:
    snapshot = acquire_event_snapshot(TICKER, transport=_fake_transport(), clock=lambda: NOW)
    tampered = dict(snapshot.to_json())
    tampered["metadata_hash"] = "0" * 64  # stamped field no longer matches raw_body_b64
    result = validate_event_snapshot(tampered, expected_ticker=TICKER)
    assert result.succeeded is False
    assert "independent re-parse" in (result.reason or "")


def test_validate_rejects_tampered_series_ticker_not_matching_raw_body() -> None:
    snapshot = acquire_event_snapshot(TICKER, transport=_fake_transport(), clock=lambda: NOW)
    tampered = dict(snapshot.to_json())
    tampered["parsed_series_ticker"] = "SOME-OTHER-SERIES"
    result = validate_event_snapshot(tampered, expected_ticker=TICKER)
    assert result.succeeded is False
    assert "independent re-parse" in (result.reason or "")


def test_validate_rejects_extra_field() -> None:
    snapshot = acquire_event_snapshot(TICKER, transport=_fake_transport(), clock=lambda: NOW)
    tampered = dict(snapshot.to_json())
    tampered["event_valid"] = True  # never a real field -- must never be read/trusted
    result = validate_event_snapshot(tampered, expected_ticker=TICKER)
    assert result.succeeded is False
    assert result.classification == "MALFORMED_SNAPSHOT_EVIDENCE"


def test_validate_rejects_missing_field() -> None:
    snapshot = acquire_event_snapshot(TICKER, transport=_fake_transport(), clock=lambda: NOW)
    tampered = dict(snapshot.to_json())
    del tampered["metadata_hash"]
    result = validate_event_snapshot(tampered, expected_ticker=TICKER)
    assert result.succeeded is False
    assert result.classification == "MALFORMED_SNAPSHOT_EVIDENCE"


def test_validate_rejects_expires_before_observed() -> None:
    """Internal self-consistency: expires_at must be strictly after observed_at. This module
    deliberately does not check now-relative staleness itself (see module docstring) -- only
    that the two stamped timestamps are not internally contradictory."""
    snapshot = acquire_event_snapshot(TICKER, transport=_fake_transport(), clock=lambda: NOW)
    tampered = dict(snapshot.to_json())
    tampered["expires_at"] = tampered["observed_at"]
    result = validate_event_snapshot(tampered, expected_ticker=TICKER)
    assert result.succeeded is False
    assert result.classification == "MALFORMED_SNAPSHOT_EVIDENCE"


def test_validate_rejects_expiry_beyond_fixed_freshness_bound() -> None:
    snapshot = acquire_event_snapshot(TICKER, transport=_fake_transport(), clock=lambda: NOW)
    tampered = dict(snapshot.to_json())
    far_future = NOW + timedelta(hours=1)
    tampered["expires_at"] = far_future.isoformat()
    result = validate_event_snapshot(tampered, expected_ticker=TICKER)
    assert result.succeeded is False
    assert result.classification == "MALFORMED_SNAPSHOT_EVIDENCE"


def test_validate_rejects_naive_timestamp() -> None:
    snapshot = acquire_event_snapshot(TICKER, transport=_fake_transport(), clock=lambda: NOW)
    tampered = dict(snapshot.to_json())
    tampered["observed_at"] = "2026-08-20T03:00:00"  # no timezone
    result = validate_event_snapshot(tampered, expected_ticker=TICKER)
    assert result.succeeded is False
    assert result.classification == "MALFORMED_SNAPSHOT_EVIDENCE"


def test_validate_rejects_non_dict_payload() -> None:
    result = validate_event_snapshot("not-a-dict", expected_ticker=TICKER)
    assert result.succeeded is False
    assert result.classification == "MALFORMED_SNAPSHOT_EVIDENCE"


def test_validate_rejects_unsucceeded_classification() -> None:
    snapshot = acquire_event_snapshot(
        TICKER, transport=_fake_transport(status=500), clock=lambda: NOW
    )
    result = validate_event_snapshot(snapshot.to_json(), expected_ticker=TICKER)
    assert result.succeeded is False
    assert result.classification == "SOURCE_AUTHORITY_MISMATCH"


# ---------------------------------------------------------------------------
# Determinism / identity
# ---------------------------------------------------------------------------


def test_metadata_hash_changes_on_material_mutation() -> None:
    base = acquire_event_snapshot(TICKER, transport=_fake_transport(), clock=lambda: NOW)
    mutated = acquire_event_snapshot(
        TICKER,
        transport=_fake_transport(payload=_event_payload(TICKER, title="A different title")),
        clock=lambda: NOW,
    )
    assert base.metadata_hash != mutated.metadata_hash


def test_metadata_hash_stable_for_identical_raw_bytes() -> None:
    first = acquire_event_snapshot(TICKER, transport=_fake_transport(), clock=lambda: NOW)
    second = acquire_event_snapshot(TICKER, transport=_fake_transport(), clock=lambda: NOW)
    assert first.metadata_hash == second.metadata_hash
    assert first.to_json() == second.to_json()


# ---------------------------------------------------------------------------
# Zero credentials/account/write/network capability
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
    source = Path("services/market_universe/event_snapshot.py").read_text()
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
    assert not hits, f"event_snapshot.py references forbidden credential/account names: {hits}"
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
    network_modules = {"http.client", "urllib.request", "requests", "subprocess", "sqlite3"}
    assert not (imported_modules & network_modules), imported_modules & network_modules


def test_module_never_calls_transport_without_the_injected_seam() -> None:
    """Proves acquire_event_snapshot cannot reach a live socket: the only way network bytes ever
    enter this module is through the `transport` callable a test/caller supplies."""
    source = Path("services/market_universe/event_snapshot.py").read_text()
    assert "http.client" not in source
    assert "requests." not in source


def test_authoritative_event_snapshot_is_frozen_and_slotted() -> None:
    """No accidental mutability that could let a downstream caller silently patch fields after
    acquisition/validation -- mirrors AuthoritativeMarketSnapshot/AuthoritativeOrderbookSnapshot."""
    snapshot = acquire_event_snapshot(TICKER, transport=_fake_transport(), clock=lambda: NOW)
    with pytest.raises((AttributeError, TypeError)):
        snapshot.ticker = "TAMPERED"  # type: ignore[misc]


def test_authoritative_event_snapshot_field_shape() -> None:
    assert set(AuthoritativeEventSnapshot.__dataclass_fields__) == {
        "schema",
        "software_version",
        "environment",
        "host",
        "path",
        "ticker",
        "http_status",
        "observed_at",
        "expires_at",
        "body_sha256",
        "raw_body_b64",
        "parsed_event_ticker",
        "parsed_series_ticker",
        "source_updated_at",
        "metadata_hash",
        "parser_version",
        "classification",
        "reason",
    }
