from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from services.market_universe.archive import EntityKind
from services.market_universe.domain import Series, material_hashes
from services.opportunity_engine.structural import RelationshipType, StructuralLead
from services.prospective_shadow.kernel import (
    STRUCTURAL_POLICY_ID,
    STRUCTURAL_SIGNAL_ENVELOPE_SCHEMA,
    HydratedMarketAuthority,
    HydratedSeriesAuthority,
    RuntimeIdentity,
    ShadowKernelError,
    _book,
    build_structural_signal_envelope,
    canonical_json,
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
    threshold = "10" if ticker == "BROAD" else "20"
    raw = {
        "ticker": ticker,
        "event_ticker": "EVENT",
        "title": "Measured value threshold",
        "yes_sub_title": "Measured value is at least threshold units",
        "no_sub_title": "Measured value is below threshold units",
        "rules_primary": f"YES if the measured value is at least {threshold} units.",
        "rules_secondary": "Use the final published report.",
        "market_type": "binary",
        "status": "active",
        "price_level_structure": "linear_cent",
        "price_ranges": [{"start": "0", "end": "1", "step": ".01"}],
        "strike_type": "greater_or_equal",
        "custom_strike": None,
        "floor_strike": threshold,
        "timezone": "UTC",
        "expiration_time": "2026-09-08T20:00:00Z",
        "expected_expiration_time": "2026-09-08T20:00:00Z",
        "occurrence_datetime": "2026-09-08T12:00:00Z",
        "settlement_sources": [{"name": "Official Source", "url": "https://example.test"}],
        "settlement_value_dollars": "1.00",
        "measured_event_or_value": "final measured value",
        "subject_entities": ["subject-a"],
        "geographic_scope": "scope-a",
        "threshold_unit": "units",
        "rounding_rules": "nearest whole unit",
        "revision_rules": "final report controls",
        "correction_rules": "published corrections control",
        "recount_rules": "none",
        "cancellation_rules": "void only under exchange rules",
        "postponement_rules": "deadline unchanged",
        "early_close_condition": "none",
        "exception_rules": ["none"],
    }
    rules_hash, metadata_hash = material_hashes(raw)
    market = SimpleNamespace(
        ticker=ticker,
        event_ticker="EVENT",
        rules_hash=rules_hash,
        metadata_hash=metadata_hash,
        raw=raw,
    )
    event = SimpleNamespace(
        ticker="EVENT",
        series_ticker="SERIES",
        metadata_hash="4" * 64,
        raw={
            "event_ticker": "EVENT",
            "series_ticker": "SERIES",
            "title": "Measured event",
            "category": "Economics",
            "settlement_sources": [{"name": "Official Source", "url": "https://example.test"}],
        },
    )
    market_snapshot = SimpleNamespace(
        body_sha256=("5" if ticker == "BROAD" else "6") * 64,
        observed_at=NOW - timedelta(seconds=1),
    )
    event_snapshot = SimpleNamespace(body_sha256=event_body)
    return HydratedMarketAuthority(market, event, market_snapshot, event_snapshot)


def _series_authority() -> HydratedSeriesAuthority:
    raw = {
        "ticker": "SERIES",
        "title": "Measured series",
        "category": "Economics",
        "frequency": "event",
        "settlement_sources": [{"name": "Official Source", "url": "https://example.test"}],
    }
    series = Series.parse(raw)
    observation = SimpleNamespace(
        kind=EntityKind.SERIES,
        ticker="SERIES",
        canonical_source_hash=hashlib.sha256(canonical_json(raw).encode()).hexdigest(),
        metadata_hash=series.metadata_hash,
        observation_id="8" * 64,
        acquired_at=NOW - timedelta(seconds=3),
        parser_version="m2-market-universe-parser-v1",
        archive_schema_version="m26f-universe-archive-schema-v1",
        archive_policy_version="m26f-canonical-json-sha256-v1",
        entity=series,
    )
    return HydratedSeriesAuthority(series, observation)


def _lead() -> StructuralLead:
    broad = _authority("BROAD").market
    narrow = _authority("NARROW").market
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
        broad.rules_hash,
        broad.metadata_hash,
        narrow.rules_hash,
        narrow.metadata_hash,
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


SERIES_AUTHORITY = _series_authority()


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
        broad_series_authority=SERIES_AUTHORITY,
        narrow_series_authority=SERIES_AUTHORITY,
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
        broad_series_authority=SERIES_AUTHORITY,
        narrow_series_authority=SERIES_AUTHORITY,
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
        "discovery_priority_gap": "0.01",
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
            broad_series_authority=SERIES_AUTHORITY,
            narrow_series_authority=SERIES_AUTHORITY,
        )


def test_discovery_rules_or_metadata_mismatch_fails_closed_before_semantics() -> None:
    broad, narrow = _books()
    lead = _lead()
    lead = StructuralLead(
        **{
            name: getattr(lead, name)
            for name in lead.__dataclass_fields__
            if name not in {"broad_rules_hash", "narrow_metadata_hash"}
        },
        broad_rules_hash="f" * 64,
        narrow_metadata_hash="e" * 64,
    )
    with pytest.raises(ShadowKernelError, match="discovery lead market authority"):
        build_structural_signal_envelope(
            lead=lead,
            broad_book=broad,
            narrow_book=narrow,
            broad_authority=_authority("BROAD"),
            narrow_authority=_authority("NARROW"),
            acquired_at=NOW - timedelta(seconds=2),
            decision_at=NOW,
            start_receipt_digest=RECEIPT,
            runtime=RUNTIME,
            broad_series_authority=SERIES_AUTHORITY,
            narrow_series_authority=SERIES_AUTHORITY,
        )
    row = capture_structural_observation(
        lead=lead,
        broad_book=_book_view("BROAD", yes_bid="0.20", no_bid="0.30"),
        narrow_book=_book_view("NARROW", yes_bid="0.50", no_bid="0.40"),
        acquired_at=NOW - timedelta(seconds=2),
        decision_at=NOW,
        leg_authority={"BROAD": _authority("BROAD"), "NARROW": _authority("NARROW")},
        start_receipt_digest=RECEIPT,
        runtime=RUNTIME,
        leg_series_authority={"BROAD": SERIES_AUTHORITY, "NARROW": SERIES_AUTHORITY},
        s2a_enabled=True,
    )
    assert row["decision"] == "ABSTAIN"
    assert row["reason_code"] == "STRUCTURAL_SIGNAL_ENVELOPE_UNAVAILABLE"


def test_load_bearing_semantic_context_mismatch_fails_closed() -> None:
    broad, narrow = _books()
    altered_narrow = _authority("NARROW")
    altered_narrow.market.raw["geographic_scope"] = "different-scope"
    with pytest.raises(ShadowKernelError, match="semantic context mismatch"):
        build_structural_signal_envelope(
            lead=_lead(),
            broad_book=broad,
            narrow_book=narrow,
            broad_authority=_authority("BROAD"),
            narrow_authority=altered_narrow,
            acquired_at=NOW - timedelta(seconds=2),
            decision_at=NOW,
            start_receipt_digest=RECEIPT,
            runtime=RUNTIME,
            broad_series_authority=SERIES_AUTHORITY,
            narrow_series_authority=SERIES_AUTHORITY,
        )


def test_normalization_refuses_ticker_or_title_inference() -> None:
    broad, narrow = _books()
    incomplete = _authority("NARROW")
    del incomplete.market.raw["rules_primary"]
    with pytest.raises(ShadowKernelError, match="semantic normalization"):
        build_structural_signal_envelope(
            lead=_lead(),
            broad_book=broad,
            narrow_book=narrow,
            broad_authority=_authority("BROAD"),
            narrow_authority=incomplete,
            acquired_at=NOW - timedelta(seconds=2),
            decision_at=NOW,
            start_receipt_digest=RECEIPT,
            runtime=RUNTIME,
            broad_series_authority=SERIES_AUTHORITY,
            narrow_series_authority=SERIES_AUTHORITY,
        )


def test_stored_semantics_replay_without_current_metadata() -> None:
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
        broad_series_authority=SERIES_AUTHORITY,
        narrow_series_authority=SERIES_AUTHORITY,
    )
    validate_structural_signal_envelope(envelope)
    assert envelope["relationship"]["semantic_context"]["identity"]
    assert envelope["lead"]["discovery_priority_gap"] == "0.01"
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
        leg_series_authority={"BROAD": SERIES_AUTHORITY, "NARROW": SERIES_AUTHORITY},
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
        leg_series_authority={"BROAD": SERIES_AUTHORITY, "NARROW": SERIES_AUTHORITY},
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
        broad_series_authority=SERIES_AUTHORITY,
        narrow_series_authority=SERIES_AUTHORITY,
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
        leg_series_authority={"BROAD": SERIES_AUTHORITY, "NARROW": SERIES_AUTHORITY},
        s2a_enabled=True,
    )
    row["structural_signal_envelope"]["start_receipt_digest"] = "e" * 64
    with pytest.raises(ShadowKernelError):
        validate_observation(row, now=NOW)


def test_r2_rejects_direction_and_book_content_mutations() -> None:
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
        broad_series_authority=SERIES_AUTHORITY,
        narrow_series_authority=SERIES_AUTHORITY,
    )
    bad_comparator = dict(envelope)
    bad_relationship = dict(envelope["relationship"])
    bad_broad = dict(bad_relationship["broad"])
    bad_broad["proposition"] = dict(bad_broad["proposition"], comparator="<=")
    bad_relationship["broad"] = bad_broad
    bad_comparator["relationship"] = bad_relationship
    with pytest.raises(ShadowKernelError, match="comparator missing"):
        validate_structural_signal_envelope(bad_comparator)

    bad_books = dict(envelope)
    bad_evidence = dict(envelope["evidence"])
    bad_contents = dict(bad_evidence["book_contents"])
    bad_contents["broad"] = dict(bad_contents["broad"], yes_bids=[["0.21", "2"]])
    bad_evidence["book_contents"] = bad_contents
    bad_books["evidence"] = bad_evidence
    with pytest.raises(ShadowKernelError, match="snapshot identity"):
        validate_structural_signal_envelope(bad_books)
