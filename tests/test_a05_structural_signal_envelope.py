from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from services.opportunity_engine.structural import RelationshipType, StructuralLead
from services.prospective_shadow.kernel import (
    STRUCTURAL_POLICY_ID,
    STRUCTURAL_SIGNAL_ENVELOPE_SCHEMA,
    HydratedMarketAuthority,
    RuntimeIdentity,
    ShadowKernelError,
    _book,
    build_structural_signal_envelope,
    capture_structural_observation,
    validate_observation,
    validate_structural_signal_envelope,
)
from services.real_time_market_data.orderbook import BookState, BookView

NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)
RECEIPT = "a" * 64
RUNTIME = RuntimeIdentity("b" * 40, "c" * 40)


def _book_view(ticker: str, *, yes_bid: str, no_bid: str) -> BookView:
    yes = ((Decimal(yes_bid), Decimal("2")),)
    no = ((Decimal(no_bid), Decimal("2")),)
    return BookView(
        ticker,
        42,
        yes,
        no,
        yes[0][0],
        no[0][0],
        BookState.CURRENT,
        NOW - timedelta(seconds=1),
        NOW - timedelta(seconds=1),
        yes[0][1],
        no[0][1],
    )


def _authority(ticker: str, *, event_body: str = "d" * 64) -> HydratedMarketAuthority:
    raw = {
        "ticker": ticker,
        "event_ticker": "EVENT",
        "strike_type": "greater_or_equal",
        "custom_strike": None,
    }
    market = SimpleNamespace(
        ticker=ticker,
        event_ticker="EVENT",
        rules_hash="1" * 64,
        metadata_hash=("2" if ticker == "BROAD" else "3") * 64,
        raw=raw,
    )
    event = SimpleNamespace(ticker="EVENT", metadata_hash="4" * 64)
    market_snapshot = SimpleNamespace(body_sha256=("5" if ticker == "BROAD" else "6") * 64)
    event_snapshot = SimpleNamespace(body_sha256=event_body)
    return HydratedMarketAuthority(market, event, market_snapshot, event_snapshot)


def _lead() -> StructuralLead:
    return StructuralLead(
        "lead",
        RelationshipType.YES_HIGH_SUBSET_OF_YES_LOW,
        "cohort",
        "EVENT",
        "BROAD",
        "NARROW",
        Decimal("10"),
        Decimal("20"),
        "a" * 64,
        "b" * 64,
        "1" * 64,
        "2" * 64,
        "1" * 64,
        "3" * 64,
        "fixture",
        Decimal("0.01"),
        None,
        Decimal("0.01"),
        None,
    )


def _books() -> tuple[dict, dict]:
    return (
        _book(_book_view("BROAD", yes_bid="0.20", no_bid="0.30"), NOW, timedelta(seconds=30)),
        _book(_book_view("NARROW", yes_bid="0.50", no_bid="0.40"), NOW, timedelta(seconds=30)),
    )


def test_complete_envelope_is_canonical_and_deterministic() -> None:
    broad, narrow = _books()
    first = build_structural_signal_envelope(
        lead=_lead(),
        broad_book=broad,
        narrow_book=narrow,
        broad_authority=_authority("BROAD"),
        narrow_authority=_authority("NARROW"),
        acquired_at=NOW - timedelta(seconds=2),
        decision_at=NOW,
        start_receipt_digest=RECEIPT,
        runtime=RUNTIME,
    )
    second = build_structural_signal_envelope(
        lead=_lead(),
        broad_book=broad,
        narrow_book=narrow,
        broad_authority=_authority("BROAD"),
        narrow_authority=_authority("NARROW"),
        acquired_at=NOW - timedelta(seconds=2),
        decision_at=NOW,
        start_receipt_digest=RECEIPT,
        runtime=RUNTIME,
    )
    assert first == second
    assert first["schema"] == STRUCTURAL_SIGNAL_ENVELOPE_SCHEMA
    assert first["signal_id"] == second["signal_id"]
    assert first["relationship"]["broad"]["role"] == "BROAD_LOWER_THRESHOLD"
    assert first["relationship"]["narrow"]["role"] == "NARROW_HIGHER_THRESHOLD"
    assert first["lead"] == {
        "broad_yes_ask": "0.30",
        "narrow_yes_bid": "0.50",
        "gap": "0.20",
        "policy_identity": STRUCTURAL_POLICY_ID,
        "threshold": "0.01",
        "inequality": "narrow_yes_bid > broad_yes_ask",
        "price_convention": "displayed-price-only",
    }
    assert first["probability"] is None and first["settlement"] is None
    assert first["safety"] == {"research_only": True, "production_influence": "0"}
    assert first["runtime"]["runtime_git_sha"] == "b" * 40


def test_disagreeing_event_authority_and_invalid_runtime_fail_closed() -> None:
    broad, narrow = _books()
    with pytest.raises(ShadowKernelError, match="event authorities"):
        build_structural_signal_envelope(
            lead=_lead(),
            broad_book=broad,
            narrow_book=narrow,
            broad_authority=_authority("BROAD"),
            narrow_authority=_authority("NARROW", event_body="e" * 64),
            acquired_at=NOW - timedelta(seconds=2),
            decision_at=NOW,
            start_receipt_digest=RECEIPT,
            runtime=RUNTIME,
        )
    with pytest.raises(ShadowKernelError, match="runtime git_sha"):
        RuntimeIdentity("not-a-sha", "c" * 40)
        build_structural_signal_envelope(
            lead=_lead(),
            broad_book=broad,
            narrow_book=narrow,
            broad_authority=_authority("BROAD"),
            narrow_authority=_authority("NARROW"),
            acquired_at=NOW - timedelta(seconds=2),
            decision_at=NOW,
            start_receipt_digest=RECEIPT,
            runtime=RuntimeIdentity("x", "c" * 40),
        )


def test_capture_binds_envelope_to_parent_and_legacy_mode_remains_valid() -> None:
    broad_view = _book_view("BROAD", yes_bid="0.20", no_bid="0.30")
    narrow_view = _book_view("NARROW", yes_bid="0.50", no_bid="0.40")
    authorities = {"BROAD": _authority("BROAD"), "NARROW": _authority("NARROW")}
    row = capture_structural_observation(
        lead=_lead(),
        broad_book=broad_view,
        narrow_book=narrow_view,
        acquired_at=NOW - timedelta(seconds=2),
        decision_at=NOW,
        leg_authority=authorities,
        start_receipt_digest=RECEIPT,
        runtime=RUNTIME,
        s2a_enabled=True,
    )
    assert row["decision"] == "OBSERVE"
    assert "structural_signal_envelope" in row
    validate_observation(row, now=NOW)
    legacy = capture_structural_observation(
        lead=_lead(),
        broad_book=broad_view,
        narrow_book=narrow_view,
        acquired_at=NOW - timedelta(seconds=2),
        decision_at=NOW,
        leg_authority=authorities,
        start_receipt_digest=RECEIPT,
    )
    assert legacy["decision"] == "OBSERVE"
    assert "structural_signal_envelope" not in legacy
    validate_observation(legacy, now=NOW)


def test_s2a_mode_abstains_without_envelope_or_when_lead_is_not_positive() -> None:
    authorities = {"BROAD": _authority("BROAD"), "NARROW": _authority("NARROW")}
    row = capture_structural_observation(
        lead=_lead(),
        broad_book=_book_view("BROAD", yes_bid="0.20", no_bid="0.30"),
        narrow_book=_book_view("NARROW", yes_bid="0.50", no_bid="0.40"),
        acquired_at=NOW - timedelta(seconds=2),
        decision_at=NOW,
        leg_authority=authorities,
        start_receipt_digest=RECEIPT,
        s2a_enabled=True,
    )
    assert row["decision"] == "ABSTAIN"
    no_lead = capture_structural_observation(
        lead=_lead(),
        broad_book=_book_view("BROAD", yes_bid="0.20", no_bid="0.60"),
        narrow_book=_book_view("NARROW", yes_bid="0.50", no_bid="0.40"),
        acquired_at=NOW - timedelta(seconds=2),
        decision_at=NOW,
        leg_authority=authorities,
        start_receipt_digest=RECEIPT,
        runtime=RUNTIME,
        s2a_enabled=True,
    )
    assert no_lead["decision"] == "ABSTAIN"
    assert no_lead["reason_code"] == "STRUCTURAL_SIGNAL_ENVELOPE_UNAVAILABLE"


def test_envelope_rejects_mutated_signal_id_and_parent_mismatch() -> None:
    broad, narrow = _books()
    envelope = build_structural_signal_envelope(
        lead=_lead(),
        broad_book=broad,
        narrow_book=narrow,
        broad_authority=_authority("BROAD"),
        narrow_authority=_authority("NARROW"),
        acquired_at=NOW - timedelta(seconds=2),
        decision_at=NOW,
        start_receipt_digest=RECEIPT,
        runtime=RUNTIME,
    )
    mutated = dict(envelope)
    mutated["lead"] = dict(envelope["lead"], gap="0.21")
    with pytest.raises(ShadowKernelError, match="lead reconciliation"):
        validate_structural_signal_envelope(mutated)
    row = capture_structural_observation(
        lead=_lead(),
        broad_book=_book_view("BROAD", yes_bid="0.20", no_bid="0.30"),
        narrow_book=_book_view("NARROW", yes_bid="0.50", no_bid="0.40"),
        acquired_at=NOW - timedelta(seconds=2),
        decision_at=NOW,
        leg_authority={"BROAD": _authority("BROAD"), "NARROW": _authority("NARROW")},
        start_receipt_digest=RECEIPT,
        runtime=RUNTIME,
        s2a_enabled=True,
    )
    row["structural_signal_envelope"]["start_receipt_digest"] = "e" * 64
    with pytest.raises(ShadowKernelError):
        validate_observation(row, now=NOW)
