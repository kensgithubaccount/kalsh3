from __future__ import annotations

import hashlib
import inspect
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from services.agent_control_center.event_evidence import (
    EvaluatedMarketEventBinding,
    EventEvidenceError,
    EvidenceSufficiencyState,
    ExchangeEventIdentityState,
    IndependenceState,
    ObservationAuthorityState,
    UniverseEventObservation,
    _binding_identity,
    _make_manifest,
    _manifest_identity,
    aggregate_market_differences_by_exchange_event,
    assess_manifest,
    bind_market_event,
)
from services.market_universe.domain import Event, Market

NOW = datetime(2026, 1, 2, tzinfo=UTC)


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def market(ticker: str, event_ticker: str) -> Market:
    return Market.parse(
        {
            "ticker": ticker,
            "event_ticker": event_ticker,
            "title": ticker,
            "market_type": "binary",
            "status": "finalized",
            "rules_primary": "rules",
            "price_level_structure": "linear_cent",
            "is_provisional": False,
        }
    )


def event(ticker: str, series: str = "SERIES-A") -> Event:
    return Event.parse({"event_ticker": ticker, "series_ticker": series, "title": ticker})


def observation(
    market_ticker: str,
    event_ticker: str,
    *,
    series: str = "SERIES-A",
    observed_at: datetime = NOW,
    observation_id: str | None = None,
) -> UniverseEventObservation:
    return UniverseEventObservation.from_entities(
        market(market_ticker, event_ticker),
        event(event_ticker, series),
        observation_id=(
            observation_id or sha(f"snapshot:{market_ticker}:{event_ticker}:{observed_at}")
        ),
        observed_at=observed_at,
        provenance_hash=sha("offline immutable universe fixture"),
    )


def manifest(rows: tuple[UniverseEventObservation, ...]):
    return _make_manifest(
        source_universe_id=sha("complete M26C universe"),
        market_as_of={row.market_ticker: NOW + timedelta(seconds=1) for row in rows},
        included_source_ids=tuple(sha(row.market_ticker) for row in rows),
        excluded_source_items=(),
        observations=rows,
    )


def test_candidate_binding_preserves_event_series_but_cannot_claim_authority() -> None:
    row = observation("NOT-A-PREFIX", "EVENT-A", series="RECURRING")
    first = bind_market_event("NOT-A-PREFIX", as_of=NOW, observations=(row,))
    assert first == bind_market_event("NOT-A-PREFIX", as_of=NOW, observations=(row,))
    assert first.state is ExchangeEventIdentityState.UNPROVEN
    assert (first.event_ticker, first.series_ticker) == ("EVENT-A", "RECURRING")
    assert first.observation_authority_state is ObservationAuthorityState.UNVERIFIED
    assert first.production_influence == Decimal(0)


def test_caller_forgery_cannot_become_authoritative_proven() -> None:
    forged = UniverseEventObservation.from_entities(
        market("CALLER-MARKET", "CALLER-EVENT"),
        event("CALLER-EVENT"),
        observation_id=sha("caller chose this snapshot id"),
        observed_at=NOW,
        provenance_hash=sha("caller chose this provenance"),
    )
    binding = bind_market_event("CALLER-MARKET", as_of=NOW, observations=(forged,))
    result = assess_manifest(manifest((forged,)))
    assert forged.authority_state is ObservationAuthorityState.UNVERIFIED
    assert binding.state is ExchangeEventIdentityState.UNPROVEN
    assert result.candidate_exchange_event_count == 1
    assert result.proven_exchange_event_count is None
    assert result.exchange_event_identity_state is ExchangeEventIdentityState.UNPROVEN


def test_no_caller_supplied_trust_parameter_or_enum_can_escalate() -> None:
    assert (
        "authority_state"
        not in inspect.signature(UniverseEventObservation.from_entities).parameters
    )
    assert "authority_state" not in inspect.signature(UniverseEventObservation).parameters
    authority_field = next(
        field for field in fields(UniverseEventObservation) if field.name == "authority_state"
    )
    assert authority_field.init is False
    row = observation("M", "E")
    with pytest.raises((TypeError, ValueError), match="init=False"):
        replace(row, authority_state=ObservationAuthorityState.ARCHIVE_VERIFIED)


def test_no_current_path_can_construct_a_proven_binding() -> None:
    with pytest.raises(EventEvidenceError, match="unavailable without an archive verifier"):
        EvaluatedMarketEventBinding(
            binding_id=sha("caller binding"),
            market_ticker="M",
            state=ExchangeEventIdentityState.PROVEN,
            event_ticker="E",
            series_ticker="S",
            market_metadata_hash=sha("market"),
            event_metadata_hash=sha("event"),
            source_observation_id=sha("observation"),
            provenance_hash=sha("provenance"),
            observation_authority_state=ObservationAuthorityState.ARCHIVE_VERIFIED,
            observed_at=NOW,
            as_of=NOW,
            detail="caller fabricated",
        )


def test_assessment_rejects_forged_proven_binding_after_object_model_bypass() -> None:
    row = observation("M", "E")
    normal_manifest = manifest((row,))
    normal_binding = normal_manifest.bindings[0]
    assert normal_binding.state is ExchangeEventIdentityState.UNPROVEN

    forged_binding = object.__new__(EvaluatedMarketEventBinding)
    for item in fields(EvaluatedMarketEventBinding):
        object.__setattr__(forged_binding, item.name, getattr(normal_binding, item.name))
    object.__setattr__(forged_binding, "state", ExchangeEventIdentityState.PROVEN)
    object.__setattr__(forged_binding, "binding_id", _binding_identity(forged_binding))

    forged_manifest = object.__new__(type(normal_manifest))
    for item in fields(type(normal_manifest)):
        object.__setattr__(forged_manifest, item.name, getattr(normal_manifest, item.name))
    object.__setattr__(forged_manifest, "bindings", (forged_binding,))
    object.__setattr__(forged_manifest, "manifest_id", _manifest_identity(forged_manifest))

    with pytest.raises(EventEvidenceError, match="without an archive verifier"):
        assess_manifest(forged_manifest)


def test_market_event_disagreement_is_explicit_conflict() -> None:
    row = UniverseEventObservation.from_entities(
        market("M", "EVENT-A"),
        event("EVENT-B"),
        observation_id=sha("snapshot"),
        observed_at=NOW,
        provenance_hash=sha("provenance"),
    )
    assert (
        bind_market_event("M", as_of=NOW, observations=(row,)).state
        is ExchangeEventIdentityState.CONFLICTED
    )


def test_missing_historical_observation_does_not_use_current_metadata() -> None:
    current = observation("M", "CURRENT-EVENT", observed_at=NOW + timedelta(days=1))
    binding = bind_market_event("M", as_of=NOW, observations=(current,))
    assert binding.state is ExchangeEventIdentityState.MISSING and binding.event_ticker is None


def test_historical_identity_is_not_rewritten_by_later_current_metadata() -> None:
    historical = observation("M", "HISTORICAL-EVENT", observed_at=NOW - timedelta(days=1))
    current = observation("M", "CURRENT-EVENT", observed_at=NOW + timedelta(days=1))
    binding = bind_market_event("M", as_of=NOW, observations=(current, historical))
    assert binding.state is ExchangeEventIdentityState.UNPROVEN
    assert binding.event_ticker == "HISTORICAL-EVENT"


def test_same_observation_id_with_different_content_and_time_is_conflicted() -> None:
    reused = sha("reused observation identity")
    first = observation("M", "EVENT-A", observed_at=NOW - timedelta(days=1), observation_id=reused)
    second = observation("M", "EVENT-B", observed_at=NOW, observation_id=reused)
    binding = bind_market_event("M", as_of=NOW, observations=(first, second))
    result = assess_manifest(manifest((first, second)))
    assert binding.state is ExchangeEventIdentityState.CONFLICTED
    assert result.evidence_state is EvidenceSufficiencyState.EVIDENCE_UNAVAILABLE
    assert result.proven_exchange_event_count is None


def test_conflicting_mappings_fail_complete_assessment_closed() -> None:
    result = assess_manifest(manifest((observation("M", "A"), observation("M", "B"))))
    assert result.evidence_state is EvidenceSufficiencyState.EVIDENCE_UNAVAILABLE
    assert result.proven_exchange_event_count is result.unresolved_market_event_count is None


@pytest.mark.parametrize("count", [100, 500])
def test_many_markets_under_one_event_are_one_exchange_event(count: int) -> None:
    rows = tuple(observation(f"LADDER-{index}", "EVENT-A") for index in range(count))
    result = assess_manifest(manifest(rows))
    assert (result.market_count, result.candidate_exchange_event_count) == (count, 1)
    assert result.proven_exchange_event_count is None
    assert result.proven_independent_evidence_unit_count is None
    assert result.independence_state is IndependenceState.NOT_PROVEN


@pytest.mark.parametrize("series", [("DAILY", "DAILY"), ("ONE", "TWO")])
def test_series_neither_collapses_events_nor_proves_independence(series: tuple[str, str]) -> None:
    rows = (
        observation("M-A", "EVENT-A", series=series[0]),
        observation("M-B", "EVENT-B", series=series[1]),
    )
    result = assess_manifest(manifest(rows))
    assert result.candidate_exchange_event_count == 2
    assert result.proven_exchange_event_count is None
    assert result.proven_independent_evidence_unit_count is None
    assert result.review_eligibility.value == "NOT_ELIGIBLE"


def test_manifest_identity_is_insertion_order_independent() -> None:
    rows = (observation("M-B", "EVENT-B"), observation("M-A", "EVENT-A"))
    assert manifest(rows).manifest_id == manifest(tuple(reversed(rows))).manifest_id


def test_all_unresolved_is_truthful_not_fake_zero() -> None:
    value = _make_manifest(
        source_universe_id=sha("complete"),
        market_as_of={"M": NOW},
        included_source_ids=(sha("evaluation"),),
        excluded_source_items=(),
        observations=(),
    )
    result = assess_manifest(value)
    assert result.market_count == result.unresolved_market_event_count == 1
    assert result.proven_exchange_event_count is None
    assert result.evidence_state is EvidenceSufficiencyState.EVENT_IDENTITY_INCOMPLETE


def test_exact_within_event_mean_emits_one_equal_weight_row_per_event() -> None:
    rows = (
        *(observation(f"A-{index}", "EVENT-A") for index in range(100)),
        observation("B-ONLY", "EVENT-B"),
    )
    value = manifest(rows)
    differences = (
        *((f"A-{index}", Decimal("0.01")) for index in range(100)),
        ("B-ONLY", Decimal("0.99")),
    )
    aggregated = aggregate_market_differences_by_exchange_event(differences, value)
    assert [(row.event_ticker, row.shared_market_count) for row in aggregated] == [
        ("EVENT-A", 100),
        ("EVENT-B", 1),
    ]
    assert [row.mean_a_minus_b_brier for row in aggregated] == [
        Decimal("0.01"),
        Decimal("0.99"),
    ]
    assert sum((row.mean_a_minus_b_brier for row in aggregated), Decimal(0)) / Decimal(
        2
    ) == Decimal("0.50")


@pytest.mark.parametrize("bad", [None, "2026-01-02T00:00:00Z", object()])
def test_bad_observation_timestamp_is_clean_domain_error(bad: object) -> None:
    with pytest.raises(EventEvidenceError, match="observed_at must be a UTC datetime"):
        UniverseEventObservation.from_entities(
            market("M", "E"),
            event("E"),
            observation_id=sha("snapshot"),
            observed_at=bad,  # type: ignore[arg-type]
            provenance_hash=sha("provenance"),
        )


@pytest.mark.parametrize("bad", [None, "2026-01-02T00:00:00Z", object()])
def test_bad_as_of_timestamp_is_clean_domain_error(bad: object) -> None:
    with pytest.raises(EventEvidenceError, match="as_of must be a UTC datetime"):
        bind_market_event("M", as_of=bad, observations=())  # type: ignore[arg-type]
