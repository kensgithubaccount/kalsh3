"""P4a mock protocol tests: no real ledger, custody, source, orders, or paper."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from services.forecasting.wn_p3_sibling_preflight import (
    CITY_SERIES,
    StructuralRoster,
    event_ticker_for,
    validate_untrusted_roster,
)
from services.forecasting.wn_p4_mock_attempt_protocol import (
    AttemptState,
    MockAttempt,
    MockDateCluster,
    MockLedger,
    MockLedgerError,
    classify_mock_abstention,
    reserve_mock_date,
)

DATE = date(2026, 11, 1)
WHEN = datetime(2026, 10, 31, 9, 0, tzinfo=UTC)
SPECS = (
    ("LT", "less", None, 70),
    ("B70", "between", 70, 71),
    ("B72", "between", 72, 73),
    ("B74", "between", 74, 75),
    ("B76", "between", 76, 77),
    ("GT", "greater", 77, None),
)


def synthetic_rosters(target: date = DATE) -> dict[str, StructuralRoster]:
    rosters: dict[str, StructuralRoster] = {}
    for series in sorted(CITY_SERIES):
        event = event_ticker_for(series, target)
        markets = [
            {
                "ticker": f"{event}-{suffix}",
                "event_ticker": event,
                "status": "active",
                "market_type": "binary",
                "rules_primary": "synthetic only, never source authority",
                "strike_type": kind,
                "floor_strike": floor,
                "cap_strike": cap,
            }
            for suffix, kind, floor, cap in SPECS
        ]
        rosters[series] = validate_untrusted_roster(
            series_ticker=series,
            target_local_date=target,
            event_payload={
                "event": {"event_ticker": event, "series_ticker": series},
                "markets": markets,
            },
        )
    return rosters


def one() -> MockLedger:
    return reserve_mock_date(
        MockLedger(),
        target_local_date=DATE,
        reserved_at=WHEN,
        untrusted_rosters=synthetic_rosters(),
    )


def test_mock_full_denominator_and_unissued_authority() -> None:
    ledger = one()
    assert ledger.denominator_date_clusters == 1
    assert ledger.denominator_city_attempts == 5
    assert ledger.denominator_sibling_slots == 30
    cluster = ledger.clusters[0]
    assert cluster.target_local_date == DATE
    assert tuple(a.series_ticker for a in cluster.attempts) == tuple(sorted(CITY_SERIES))
    assert all(a.state is AttemptState.ATTEMPT_RESERVED for a in cluster.attempts)
    assert all(len(a.untrusted_sibling_tickers) == 6 for a in cluster.attempts)
    assert not ledger.input_source_attested
    assert not ledger.model_probability_authorized
    assert not ledger.paper_authorized
    assert ledger.trading_authority == "NONE"
    assert ledger.research_only


def test_two_date_clusters_are_independent_units_not_thirty_trials() -> None:
    original = one()
    second_date = DATE + timedelta(days=1)
    subsequent = reserve_mock_date(
        original,
        target_local_date=second_date,
        reserved_at=WHEN + timedelta(days=1),
        untrusted_rosters=synthetic_rosters(second_date),
    )
    assert original.denominator_date_clusters == 1
    assert subsequent.denominator_date_clusters == 2
    assert subsequent.denominator_city_attempts == 10
    assert subsequent.denominator_sibling_slots == 60


@pytest.mark.parametrize("reason", sorted(
    (s for s in AttemptState if s in {
        AttemptState.FORECAST_MISSING,
        AttemptState.FORECAST_LATE,
        AttemptState.MARKET_MISSING,
        AttemptState.STALE_BOOK,
        AttemptState.INSUFFICIENT_DEPTH,
        AttemptState.FEE_UNKNOWN,
        AttemptState.NO_MODEL,
        AttemptState.NO_EDGE,
        AttemptState.COLLECTOR_FAILURE,
    }), key=str
))
def test_each_negative_reason_preserves_denominator(reason: AttemptState) -> None:
    ledger = one()
    updated = classify_mock_abstention(
        ledger,
        target_local_date=DATE,
        series_ticker="KXHIGHMIA",
        reason=reason,
        classified_at=WHEN + timedelta(seconds=1),
    )
    assert updated.clusters[0].attempts[2].state is reason
    assert updated.denominator_date_clusters == 1
    assert updated.denominator_city_attempts == 5
    assert updated.denominator_sibling_slots == 30
    assert all(a.state is AttemptState.ATTEMPT_RESERVED for a in ledger.clusters[0].attempts)


@pytest.mark.parametrize("blocked", [
    AttemptState.ATTEMPT_RESERVED,
    AttemptState.SETTLEMENT_NOT_FINAL,
    AttemptState.PAPER_DIAGNOSTIC_ONLY,
    AttemptState.FINALIZED,
    AttemptState.CONFLICT,
])
def test_no_positive_or_settlement_state_is_issuable(blocked: AttemptState) -> None:
    with pytest.raises(MockLedgerError):
        classify_mock_abstention(
            one(),
            target_local_date=DATE,
            series_ticker="KXHIGHMIA",
            reason=blocked,
            classified_at=WHEN,
        )


def test_no_reclassifying_or_retries_on_same_value() -> None:
    updated = classify_mock_abstention(
        one(),
        target_local_date=DATE,
        series_ticker="KXHIGHBOS",
        reason=AttemptState.NO_MODEL,
        classified_at=WHEN,
    )
    with pytest.raises(MockLedgerError):
        classify_mock_abstention(
            updated,
            target_local_date=DATE,
            series_ticker="KXHIGHBOS",
            reason=AttemptState.NO_EDGE,
            classified_at=WHEN + timedelta(seconds=2),
        )


def test_no_classification_before_reservation() -> None:
    with pytest.raises(MockLedgerError):
        classify_mock_abstention(
            MockLedger(),
            target_local_date=DATE,
            series_ticker="KXHIGHMIA",
            reason=AttemptState.NO_MODEL,
            classified_at=WHEN,
        )


@pytest.mark.parametrize("bad_time", [
    WHEN.replace(tzinfo=None),
    WHEN - timedelta(minutes=1),
])
def test_bad_classification_clock_rejected(bad_time: datetime) -> None:
    with pytest.raises(MockLedgerError):
        classify_mock_abstention(
            one(),
            target_local_date=DATE,
            series_ticker="KXHIGHMIA",
            reason=AttemptState.NO_MODEL,
            classified_at=bad_time,
        )


def test_naive_reservation_clock_fails() -> None:
    with pytest.raises(MockLedgerError):
        reserve_mock_date(
            MockLedger(),
            target_local_date=DATE,
            reserved_at=WHEN.replace(tzinfo=None),
            untrusted_rosters=synthetic_rosters(),
        )


@pytest.mark.parametrize("change", ["missing", "extra", "wrong_date", "wrong_city"])
def test_incomplete_or_wrong_rosters_rejected(change: str) -> None:
    rosters = synthetic_rosters()
    if change == "missing":
        rosters.pop("KXHIGHMIA")
    elif change == "extra":
        rosters["KXHIGHCHI"] = rosters["KXHIGHMIA"]
    elif change == "wrong_date":
        rosters["KXHIGHMIA"] = synthetic_rosters(DATE + timedelta(days=1))["KXHIGHMIA"]
    else:
        rosters["KXHIGHMIA"] = rosters["KXHIGHTBOS"]
    with pytest.raises(MockLedgerError):
        reserve_mock_date(
            MockLedger(),
            target_local_date=DATE,
            reserved_at=WHEN,
            untrusted_rosters=rosters,
        )


def test_duplicate_or_out_of_order_date_rejected() -> None:
    ledger = one()
    with pytest.raises(MockLedgerError):
        reserve_mock_date(
            ledger,
            target_local_date=DATE,
            reserved_at=WHEN + timedelta(days=1),
            untrusted_rosters=synthetic_rosters(),
        )
    with pytest.raises(MockLedgerError):
        reserve_mock_date(
            ledger,
            target_local_date=DATE - timedelta(days=1),
            reserved_at=WHEN,
            untrusted_rosters=synthetic_rosters(),
        )


def test_missing_city_or_unreserved_date_cannot_be_classified() -> None:
    for city, target in (
        ("KXHIGHCHI", DATE),
        ("KXHIGHMIA", DATE + timedelta(days=1)),
    ):
        with pytest.raises(MockLedgerError):
            classify_mock_abstention(
                one(),
                target_local_date=target,
                series_ticker=city,
                reason=AttemptState.NO_MODEL,
                classified_at=WHEN,
            )


def test_caller_roster_mutation_does_not_alter_frozen_reservation() -> None:
    rosters = synthetic_rosters()
    frozen = reserve_mock_date(
        MockLedger(), target_local_date=DATE, reserved_at=WHEN, untrusted_rosters=rosters
    )
    rosters.clear()
    assert frozen.denominator_city_attempts == 5
    assert frozen.denominator_sibling_slots == 30


def test_forged_positive_state_cannot_pass_transition_validation() -> None:
    ledger = one()
    cluster = ledger.clusters[0]
    forged = replace(cluster.attempts[0], state=AttemptState.PAPER_DIAGNOSTIC_ONLY)
    altered = replace(cluster, attempts=(forged, *cluster.attempts[1:]))
    mock = replace(ledger, clusters=(altered,))
    with pytest.raises(MockLedgerError):
        classify_mock_abstention(
            mock,
            target_local_date=DATE,
            series_ticker="KXHIGHMIA",
            reason=AttemptState.NO_MODEL,
            classified_at=WHEN,
        )


def test_forged_five_city_denominator_fails_before_transition() -> None:
    ledger = one()
    cluster = ledger.clusters[0]
    fake = replace(ledger, clusters=(replace(cluster, attempts=cluster.attempts[:-1]),))
    with pytest.raises(MockLedgerError):
        classify_mock_abstention(
            fake,
            target_local_date=DATE,
            series_ticker="KXHIGHMIA",
            reason=AttemptState.NO_MODEL,
            classified_at=WHEN,
        )


def test_negative_properties_cannot_be_overridden() -> None:
    result = one()
    for key in ("paper_authorized", "model_probability_authorized", "input_source_attested"):
        with pytest.raises(AttributeError):
            object.__setattr__(result, key, True)


def test_immutable_in_memory_value_is_not_a_durable_ledger() -> None:
    source = one()
    # Forks are possible: actual create-only/exactly-once custody remains unimplemented.
    left = classify_mock_abstention(
        source,
        target_local_date=DATE,
        series_ticker="KXHIGHMIA",
        reason=AttemptState.NO_MODEL,
        classified_at=WHEN,
    )
    right = classify_mock_abstention(
        source,
        target_local_date=DATE,
        series_ticker="KXHIGHMIA",
        reason=AttemptState.COLLECTOR_FAILURE,
        classified_at=WHEN,
    )
    assert left != right
    assert source.clusters[0].attempts[2].state is AttemptState.ATTEMPT_RESERVED


def test_constructor_does_not_grant_real_source_or_custody_authority() -> None:
    attempted: dict[str, Any] = {"paper_authorized": True}
    with pytest.raises(TypeError):
        MockLedger(**attempted)
    manually_constructed = MockLedger(
        clusters=(MockDateCluster(DATE, (MockAttempt(
            DATE, "KXHIGHMIA", "synthetic", (), WHEN
        ),)),)
    )
    assert not manually_constructed.paper_authorized
    assert manually_constructed.trading_authority == "NONE"
