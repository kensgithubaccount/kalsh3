from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from services.rt_a1.collector import (
    Classification,
    ExternalReviewEvidence,
    RtEligibility,
    first_inclusion,
    parse_external_review_html,
    parse_rt_html,
    reconstruct_transition,
)
from services.rt_a1.domain import DiscoveryError, parse_active_market
from services.rt_a1.store import RtA1Store, StoreError

NOW = datetime(2026, 9, 15, 16, 0, tzinfo=UTC)


def _html(count: int, score: int, review_id: str | None = None) -> bytes:
    review = f'<div data-review-id="{review_id}">Fresh</div>' if review_id else ""
    return f"<span>Tomatometer {score}%</span><span>Critic Reviews {count}</span>{review}".encode()


def test_rt_raw_snapshot_and_first_inclusion_interval() -> None:
    first = parse_rt_html("https://www.rottentomatoes.com/m/x", _html(1, 100), observed_at=NOW)
    body = _html(2, 50, "r-2")
    second = parse_rt_html(first.film_url, body, observed_at=NOW + timedelta(minutes=3))
    assert first.raw_body == _html(1, 100)
    assert second.raw_body_sha256 == sha256(body).hexdigest()
    events = first_inclusion(first, second)
    assert events[0]["interval"] == (NOW.isoformat(), second.observed_at.isoformat())
    assert events[0]["classification"] == Classification.FRESH_CONFIRMED


def test_transition_flags_unexplained_count_and_changed_classification() -> None:
    previous = parse_rt_html(
        "https://www.rottentomatoes.com/m/x", _html(1, 100, "r-1"), observed_at=NOW
    )
    current = parse_rt_html(
        previous.film_url,
        _html(2, 50, "r-1").replace(b"Fresh", b"Rotten"),
        observed_at=NOW + timedelta(minutes=1),
    )
    transition = reconstruct_transition(previous, current)
    assert transition.removed_or_changed == ("r-1",)
    assert transition.unexplained_count_change is True


def test_ambiguous_review_classification_is_not_eligibility() -> None:
    snapshot = parse_rt_html(
        "https://www.rottentomatoes.com/m/x",
        _html(1, 50, "r") + b"Rotten",
        observed_at=NOW,
    )
    assert snapshot.reviews[0].classification is Classification.AMBIGUOUS
    assert snapshot.reviews[0].eligibility is RtEligibility.UNKNOWN


def test_external_review_uses_observation_time_not_printed_publication_date() -> None:
    evidence = parse_external_review_html(
        "review-1",
        "https://example.test/review-1",
        b'<meta name="critic" content="A Critic"><meta name="datePublished" content="2020-01-01">',
        first_observed_at=NOW,
    )
    assert evidence.first_observed_at == NOW
    assert evidence.stated_publication_timestamp == "2020-01-01"
    assert evidence.raw_body_sha256 == sha256(evidence.raw_body).hexdigest()
    assert evidence.review_id == "review-1"
    assert isinstance(evidence, ExternalReviewEvidence)


def test_active_kxrt_parser_rejects_finalized_and_bad_timestamp() -> None:
    raw = {
        "ticker": "KXRT-TEST-T50",
        "series_ticker": "KXRT",
        "status": "finalized",
        "title": "Film",
        "rules_primary": "Tomatometer is at least floor_strike",
        "rules_secondary": "https://www.rottentomatoes.com/m/film",
        "floor_strike": "50",
        "open_time": "2026-09-15T00:00:00Z",
        "expiration_time": "2026-09-16T00:00:00Z",
        "settlement_sources": ["https://www.rottentomatoes.com/m/film"],
    }
    with pytest.raises(DiscoveryError):
        parse_active_market(
            raw, event_ticker="KXRT-TEST", body_sha256="a", raw_body_b64="b", observed_at=NOW
        )
    raw["status"] = "active"
    raw["expiration_time"] = "tomorrow"
    with pytest.raises(DiscoveryError):
        parse_active_market(
            raw, event_ticker="KXRT-TEST", body_sha256="a", raw_body_b64="b", observed_at=NOW
        )


def test_store_reopens_and_duplicate_exact_acquisition_fails_closed(tmp_path) -> None:
    store_path = tmp_path / "rt.sqlite3"
    store = RtA1Store(store_path)
    snapshot = parse_rt_html("https://www.rottentomatoes.com/m/x", _html(1, 100), observed_at=NOW)
    store.append_rt_snapshot(snapshot)
    with pytest.raises(StoreError):
        RtA1Store(store_path).append_rt_snapshot(snapshot)
    assert RtA1Store(store_path).counts("rt_snapshots") == 1
