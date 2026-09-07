from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import cast

from services.market_universe.domain import stable_hash
from services.opportunity_engine.structural import RelationshipType, StructuralLead
from services.prospective_shadow.kernel import (
    HydratedMarketAuthority,
    _book,
    capture_structural_observation,
)
from services.real_time_market_data.orderbook import BookState, BookView

NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)


def _book_view(
    *,
    ticker: str = "MKT",
    sequence: int = 42,
    observed_at: datetime = NOW - timedelta(seconds=1),
    state: BookState = BookState.CURRENT,
    yes_price: str = "0.123456789012345678901234567890",
    yes_quantity: str = "7.000000000000000000001",
    no_price: str = "0.876543210987654321098765432110",
    no_quantity: str = "8.000000000000000000002",
) -> BookView:
    yes = ((Decimal(yes_price), Decimal(yes_quantity)),)
    no = ((Decimal(no_price), Decimal(no_quantity)),)
    return BookView(
        ticker,
        sequence,
        yes,
        no,
        yes[0][0],
        no[0][0],
        state,
        observed_at,
        observed_at,
        yes[0][1],
        no[0][1],
    )


def test_decimal_book_hash_is_json_safe_deterministic_and_exact() -> None:
    view = _book_view()
    first = _book(view, NOW, timedelta(seconds=30))
    second = _book(view, NOW, timedelta(seconds=30))
    expected_material = (
        view.ticker,
        view.sequence,
        view.observed_at.isoformat(),
        [[str(p), str(q)] for p, q in view.yes_bids],
        [[str(p), str(q)] for p, q in view.no_bids],
    )

    assert first == second
    assert first["snapshot_id"] == stable_hash(expected_material)
    assert first["yes_bids"] == [["0.123456789012345678901234567890", "7.000000000000000000001"]]
    assert first["no_bids"] == [["0.876543210987654321098765432110", "8.000000000000000000002"]]
    assert all(isinstance(value, str) for level in first["yes_bids"] for value in level)
    assert all(isinstance(value, str) for level in first["no_bids"] for value in level)
    assert not any(
        isinstance(value, float)
        for level in first["yes_bids"] + first["no_bids"]
        for value in level
    )


def test_every_snapshot_identity_input_changes_snapshot_id() -> None:
    base = _book(_book_view(), NOW, timedelta(seconds=30))["snapshot_id"]
    variants = (
        _book(_book_view(ticker="OTHER"), NOW, timedelta(seconds=30)),
        _book(_book_view(sequence=43), NOW, timedelta(seconds=30)),
        _book(_book_view(observed_at=NOW - timedelta(seconds=2)), NOW, timedelta(seconds=30)),
        _book(_book_view(yes_price="0.123456789012345678901234567891"), NOW, timedelta(seconds=30)),
        _book(_book_view(yes_quantity="7.000000000000000000002"), NOW, timedelta(seconds=30)),
        _book(_book_view(no_price="0.876543210987654321098765432111"), NOW, timedelta(seconds=30)),
        _book(_book_view(no_quantity="8.000000000000000000003"), NOW, timedelta(seconds=30)),
    )
    assert all(item["snapshot_id"] != base for item in variants)


def test_structural_capture_consumes_two_decimal_current_books_and_observes() -> None:
    lead = StructuralLead(
        lead_id="lead",
        relationship_type=RelationshipType.YES_HIGH_SUBSET_OF_YES_LOW,
        cohort_identity="cohort",
        event_ticker="EVENT",
        broad_market_ticker="BROAD",
        narrow_market_ticker="NARROW",
        broad_threshold=Decimal("10"),
        narrow_threshold=Decimal("20"),
        broad_quote_source_hash="a",
        narrow_quote_source_hash="b",
        broad_rules_hash="c",
        broad_metadata_hash="d",
        narrow_rules_hash="e",
        narrow_metadata_hash="f",
        source_authority="fixture",
        indicative_gross_gap=Decimal("0"),
        indicative_quantity=None,
        priority_gap=Decimal("0"),
        priority_quantity=None,
    )

    def authority(ticker: str) -> HydratedMarketAuthority:
        return cast(
            HydratedMarketAuthority,
            SimpleNamespace(
                market=SimpleNamespace(ticker=ticker),
                event=SimpleNamespace(ticker="EVENT"),
                identity={"ticker": ticker},
            ),
        )

    result = capture_structural_observation(
        lead=lead,
        broad_book=_book_view(ticker="BROAD"),
        narrow_book=_book_view(ticker="NARROW"),
        acquired_at=NOW - timedelta(seconds=2),
        decision_at=NOW,
        leg_authority={"BROAD": authority("BROAD"), "NARROW": authority("NARROW")},
    )

    assert result["decision"] == "OBSERVE"
    assert len(result["books"]) == 2
    assert result["books"][0]["yes_bids"][0][0] == "0.123456789012345678901234567890"


def test_non_current_stale_and_future_books_fail_closed() -> None:
    for view in (
        _book_view(state=BookState.STALE),
        _book_view(observed_at=NOW - timedelta(seconds=31)),
        _book_view(observed_at=NOW + timedelta(seconds=1)),
        _book_view(state=BookState.GAP),
    ):
        result = capture_structural_observation(
            lead=StructuralLead(
                "lead",
                RelationshipType.YES_HIGH_SUBSET_OF_YES_LOW,
                "cohort",
                "EVENT",
                "MKT",
                "MKT2",
                Decimal("10"),
                Decimal("20"),
                "a",
                "b",
                "c",
                "d",
                "e",
                "f",
                "fixture",
                Decimal("0"),
                None,
                Decimal("0"),
                None,
            ),
            broad_book=view,
            narrow_book=_book_view(ticker="MKT2"),
            acquired_at=NOW - timedelta(seconds=2),
            decision_at=NOW,
        )
        assert result["decision"] == "ABSTAIN"
        assert result["reason_code"] == "STALE_OR_GAPPED_BOOK"
