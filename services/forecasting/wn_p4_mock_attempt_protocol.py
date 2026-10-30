"""WN-P4a: offline, forkable, MOCK-ONLY all-attempt reservation protocol.

No I/O, filesystem claims, live observations, retries, source timestamps,
settlement, forecasting, model, pricing, paper, or trading. Five synthetic city
reservations and six untrusted market identities per city are never evidence.

Immutable values support testing a create-only *transition shape*, NOT actual
create-only custody: callers can fork an earlier value, fabricate rosters or
clock readings, or instantiate dataclasses directly. No source attestation or
paper/production authority follows from any success here. Issue #245 still
requires a separate, independently reviewed durable issuer and settlement path.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Mapping

from .wn_p3_sibling_preflight import CITY_SERIES, StructuralRoster, event_ticker_for

POLICY = "wn-p4a-untrusted-mock-all-attempt-protocol-v1"


class MockLedgerError(ValueError):
    """Invalid synthetic reservation, date cluster, or blocked transition."""


class AttemptState(StrEnum):
    ATTEMPT_RESERVED = "ATTEMPT_RESERVED"
    FORECAST_MISSING = "FORECAST_MISSING"
    FORECAST_LATE = "FORECAST_LATE"
    MARKET_MISSING = "MARKET_MISSING"
    STALE_BOOK = "STALE_BOOK"
    INSUFFICIENT_DEPTH = "INSUFFICIENT_DEPTH"
    FEE_UNKNOWN = "FEE_UNKNOWN"
    NO_MODEL = "NO_MODEL"
    NO_EDGE = "NO_EDGE"
    COLLECTOR_FAILURE = "COLLECTOR_FAILURE"
    SETTLEMENT_NOT_FINAL = "SETTLEMENT_NOT_FINAL"
    PAPER_DIAGNOSTIC_ONLY = "PAPER_DIAGNOSTIC_ONLY"
    FINALIZED = "FINALIZED"
    CONFLICT = "CONFLICT"


# Settlement, paper and outcome-related states are NOT issuable in this checkpoint.
_MOCK_ABSTENTIONS = frozenset(
    {
        AttemptState.FORECAST_MISSING,
        AttemptState.FORECAST_LATE,
        AttemptState.MARKET_MISSING,
        AttemptState.STALE_BOOK,
        AttemptState.INSUFFICIENT_DEPTH,
        AttemptState.FEE_UNKNOWN,
        AttemptState.NO_MODEL,
        AttemptState.NO_EDGE,
        AttemptState.COLLECTOR_FAILURE,
    }
)


@dataclass(frozen=True, slots=True)
class MockAttempt:
    target_local_date: date
    series_ticker: str
    event_ticker: str
    untrusted_sibling_tickers: tuple[str, ...]
    reserved_at_utc: datetime
    state: AttemptState = AttemptState.ATTEMPT_RESERVED
    classified_at_utc: datetime | None = None


@dataclass(frozen=True, slots=True)
class MockDateCluster:
    target_local_date: date
    attempts: tuple[MockAttempt, ...]

    @property
    def denominator_city_attempts(self) -> int:
        return len(self.attempts)

    @property
    def denominator_sibling_slots(self) -> int:
        return sum(len(a.untrusted_sibling_tickers) for a in self.attempts)


@dataclass(frozen=True, slots=True)
class MockLedger:
    clusters: tuple[MockDateCluster, ...] = ()
    policy: str = POLICY

    @property
    def denominator_date_clusters(self) -> int:
        return len(self.clusters)

    @property
    def denominator_city_attempts(self) -> int:
        return sum(c.denominator_city_attempts for c in self.clusters)

    @property
    def denominator_sibling_slots(self) -> int:
        return sum(c.denominator_sibling_slots for c in self.clusters)

    @property
    def input_source_attested(self) -> bool:
        return False

    @property
    def model_probability_authorized(self) -> bool:
        return False

    @property
    def paper_authorized(self) -> bool:
        return False

    @property
    def trading_authority(self) -> str:
        return "NONE"

    @property
    def research_only(self) -> bool:
        return True


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise MockLedgerError("synthetic clock must be timezone-aware datetime")
    return value.astimezone(UTC)


def _validate_cluster(cluster: MockDateCluster) -> None:
    if type(cluster) is not MockDateCluster or len(cluster.attempts) != len(CITY_SERIES):
        raise MockLedgerError("mock cluster must retain five city attempts")
    if tuple(a.series_ticker for a in cluster.attempts) != tuple(sorted(CITY_SERIES)):
        raise MockLedgerError("mock cluster roster must be complete and canonical")
    for attempt in cluster.attempts:
        if type(attempt) is not MockAttempt:
            raise MockLedgerError("mock attempt malformed")
        expected = event_ticker_for(attempt.series_ticker, cluster.target_local_date)
        if attempt.target_local_date != cluster.target_local_date or attempt.event_ticker != expected:
            raise MockLedgerError("mock event/date mismatch")
        siblings = attempt.untrusted_sibling_tickers
        if (
            len(siblings) != 6
            or len(set(siblings)) != 6
            or any(not isinstance(t, str) or not t.startswith(expected + "-") for t in siblings)
        ):
            raise MockLedgerError("missing or duplicate synthetic sibling identity")
        _utc(attempt.reserved_at_utc)
        if type(attempt.state) is not AttemptState:
            raise MockLedgerError("invalid synthetic state")
        if attempt.state is AttemptState.ATTEMPT_RESERVED:
            if attempt.classified_at_utc is not None:
                raise MockLedgerError("unclassified mock attempt has completion time")
        elif attempt.state not in _MOCK_ABSTENTIONS:
            raise MockLedgerError("paper, outcome, and settlement state not issuable")
        elif (
            attempt.classified_at_utc is None
            or _utc(attempt.classified_at_utc) < _utc(attempt.reserved_at_utc)
        ):
            raise MockLedgerError("abstention lacks a valid post-reservation timestamp")


def _validate_ledger(ledger: MockLedger) -> None:
    if type(ledger) is not MockLedger or ledger.policy != POLICY:
        raise MockLedgerError("mock ledger identity mismatch")
    previous: date | None = None
    for cluster in ledger.clusters:
        _validate_cluster(cluster)
        if previous is not None and cluster.target_local_date <= previous:
            raise MockLedgerError("duplicate or out-of-order mock date reservation")
        previous = cluster.target_local_date


def reserve_mock_date(
    ledger: MockLedger,
    *,
    target_local_date: date,
    reserved_at: datetime,
    untrusted_rosters: Mapping[str, StructuralRoster],
) -> MockLedger:
    """Append all FIVE synthetic city reservations before any mock classification.

    No actual reservation write occurs. Reusing the same immutable snapshot
    can create a fork, so this cannot satisfy real no-retry / create-only custody.
    """
    _validate_ledger(ledger)
    when = _utc(reserved_at)
    if type(target_local_date) is not date:
        raise MockLedgerError("target date must be an exact date")
    if ledger.clusters and target_local_date <= ledger.clusters[-1].target_local_date:
        raise MockLedgerError("mock target dates must be strictly increasing")
    if set(untrusted_rosters) != CITY_SERIES or len(untrusted_rosters) != 5:
        raise MockLedgerError("all five frozen city rosters required")
    attempts: list[MockAttempt] = []
    for series in sorted(CITY_SERIES):
        proposed = untrusted_rosters[series]
        expected_event = event_ticker_for(series, target_local_date)
        if (
            type(proposed) is not StructuralRoster
            or proposed.series_ticker != series
            or proposed.target_local_date != target_local_date
            or proposed.event_ticker != expected_event
        ):
            raise MockLedgerError("untrusted preflight identity mismatch")
        attempts.append(
            MockAttempt(
                target_local_date=target_local_date,
                series_ticker=series,
                event_ticker=expected_event,
                untrusted_sibling_tickers=proposed.market_tickers,
                reserved_at_utc=when,
            )
        )
    cluster = MockDateCluster(target_local_date, tuple(attempts))
    _validate_cluster(cluster)
    return replace(ledger, clusters=(*ledger.clusters, cluster))


def classify_mock_abstention(
    ledger: MockLedger,
    *,
    target_local_date: date,
    series_ticker: str,
    reason: AttemptState,
    classified_at: datetime,
) -> MockLedger:
    """Only RESERVED -> one negative reason; never select/issue a positive signal."""
    _validate_ledger(ledger)
    if type(reason) is not AttemptState or reason not in _MOCK_ABSTENTIONS:
        raise MockLedgerError("mock transition must be a permitted negative abstention")
    when = _utc(classified_at)
    cluster_index = next(
        (i for i, c in enumerate(ledger.clusters) if c.target_local_date == target_local_date),
        None,
    )
    if cluster_index is None:
        raise MockLedgerError("cannot classify without prior date reservation")
    cluster = ledger.clusters[cluster_index]
    attempt_index = next(
        (i for i, a in enumerate(cluster.attempts) if a.series_ticker == series_ticker),
        None,
    )
    if attempt_index is None:
        raise MockLedgerError("city outside complete date cluster")
    current = cluster.attempts[attempt_index]
    if current.state is not AttemptState.ATTEMPT_RESERVED:
        raise MockLedgerError("no overwriting, reclassifying or retrying mock attempts")
    if when < current.reserved_at_utc:
        raise MockLedgerError("classification predates mock reservation")
    attempts = list(cluster.attempts)
    attempts[attempt_index] = replace(
        current, state=reason, classified_at_utc=when
    )
    clusters = list(ledger.clusters)
    clusters[cluster_index] = replace(cluster, attempts=tuple(attempts))
    return replace(ledger, clusters=tuple(clusters))
