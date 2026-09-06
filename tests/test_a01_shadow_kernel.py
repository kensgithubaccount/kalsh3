from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from services.prospective_shadow.kernel import (
    SCHEMA_ID,
    ShadowKernelError,
    ShadowObservationStore,
    _base,
    build_start_receipt,
    validate_observation,
)

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def start_receipt() -> dict[str, object]:
    return build_start_receipt(
        canonical_base_sha="9169160c4583c4a6e353c5eb6c01eb59b816d31c",
        canonical_base_tree="114569cd2ab1c86f892a529d1c1b6eb24457bff7",
        start_at=NOW,
        source_identities={"kalshi_public_read": "public-read-v1"},
        fee_policy_id="research-only-fees-v1",
    )


def observation(acquired: datetime = NOW + timedelta(seconds=1)) -> dict[str, object]:
    return _base(
        "structural_threshold",
        acquired,
        acquired,
        "event-1",
        "independent-event-1",
        ("A", "B"),
        {"state": "active"},
        "rules-hash",
        "settlement-authority",
        {"source": "fixture"},
        [],
        "OBSERVE",
        None,
        {"fixture": True},
    )


def test_start_authority_is_required_and_replayable(tmp_path: Path) -> None:
    store = ShadowObservationStore(tmp_path / "shadow.sqlite", start_receipt())
    assert store.validate(now=NOW + timedelta(minutes=1))["observations"] == 0
    assert store.append(observation(), now=NOW + timedelta(minutes=1)) == 1
    assert store.append(observation(), now=NOW + timedelta(minutes=1)) == 1
    assert store.validate(now=NOW + timedelta(minutes=1)) == {
        "receipt_digest": start_receipt()["receipt_digest"],
        "observations": 1,
        "abstentions": 0,
    }


def test_mutated_duplicate_and_future_observations_fail(tmp_path: Path) -> None:
    store = ShadowObservationStore(tmp_path / "shadow.sqlite", start_receipt())
    first = observation()
    store.append(first, now=NOW + timedelta(minutes=1))
    mutated = dict(first, reason_code="MUTATED")
    with pytest.raises(ShadowKernelError, match="identity/hash"):
        validate_observation(mutated, now=NOW + timedelta(minutes=1))
    with pytest.raises(ShadowKernelError, match="future"):
        store.append(observation(NOW + timedelta(days=1)), now=NOW + timedelta(minutes=1))


def test_missing_independence_and_nonzero_influence_fail() -> None:
    missing = observation()
    missing["independent_event_identity"] = ""
    with pytest.raises(ShadowKernelError, match="independent"):
        validate_observation(missing, now=NOW + timedelta(minutes=1))
    unsafe = observation()
    unsafe["production_influence"] = "1"
    with pytest.raises(ShadowKernelError, match="safety"):
        validate_observation(unsafe, now=NOW + timedelta(minutes=1))


def test_abstention_requires_reason_and_is_persisted(tmp_path: Path) -> None:
    store = ShadowObservationStore(tmp_path / "shadow.sqlite", start_receipt())
    abstention = _base(
        "daily_weather",
        NOW + timedelta(seconds=1),
        NOW + timedelta(seconds=1),
        "event-2",
        "day-location-2",
        ("WX",),
        {"state": "active"},
        "rules",
        "authority",
        {"source": "fixture"},
        [],
        "ABSTAIN",
        "STALE_OR_GAPPED_BOOK",
        {},
    )
    assert store.append(abstention, now=NOW + timedelta(minutes=1)) == 1
    assert store.validate(now=NOW + timedelta(minutes=1))["abstentions"] == 1
    no_reason = dict(abstention, reason_code=None)
    no_reason.pop("observation_id")
    no_reason["observation_id"] = "bad"
    with pytest.raises(ShadowKernelError, match="reason"):
        validate_observation(no_reason, now=NOW + timedelta(minutes=1))


def test_schema_identity_is_canonical() -> None:
    assert SCHEMA_ID == "kalshi.a01.prospective-observation.v1"
