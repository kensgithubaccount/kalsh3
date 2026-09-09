import hashlib
import inspect
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from services.prospective_shadow import kernel
from services.prospective_shadow.kernel import (
    SCHEMA_ID,
    STRUCTURAL_POLICY_ID,
    ShadowKernelError,
    ShadowObservationStore,
    _base,
    build_start_receipt,
    content_hash,
    hydrate_market_authority,
    validate_observation,
)
from tests.test_m27r_public_adapter_positive import (
    EVENT_TICKER,
    MARKET_TICKER,
    _event_acquirer,
    _market_acquirer,
)

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def authority(seed: str) -> dict[str, str]:
    digest = seed * 64
    return {
        "market_body_sha256": digest,
        "market_rules_hash": digest,
        "market_metadata_hash": digest,
        "event_body_sha256": digest,
        "event_metadata_hash": digest,
        "market_parser_version": kernel.MARKET_PARSER_VERSION,
        "event_parser_version": kernel.EVENT_PARSER_VERSION,
        "market_snapshot_schema": kernel.MARKET_SNAPSHOT_SCHEMA,
        "event_snapshot_schema": kernel.EVENT_SNAPSHOT_SCHEMA,
    }


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
        {
            "fixture": True,
            "structural_policy_identity": STRUCTURAL_POLICY_ID,
            "market_authority": {"A": authority("a"), "B": authority("b")},
        },
        start_receipt_digest=start_receipt()["receipt_digest"],
    )


def weather_observation() -> dict[str, object]:
    return _base(
        "daily_weather",
        NOW + timedelta(seconds=1),
        NOW + timedelta(seconds=1),
        "weather-event",
        "weather-day-location",
        ("WX",),
        {"state": "active"},
        "rules-hash",
        "settlement-authority",
        {"source": "fixture"},
        [],
        "OBSERVE",
        None,
        {
            "weather_protocol_identity": kernel.WEATHER_PROTOCOL_ID,
            "market_authority": authority("c"),
        },
        start_receipt_digest=start_receipt()["receipt_digest"],
    )


def rehash(observation_value: dict[str, object]) -> dict[str, object]:
    observation_value["evidence_hash"] = content_hash(observation_value["evidence"])
    body = dict(observation_value)
    body.pop("observation_id", None)
    body.pop("evidence_hash", None)
    observation_value["observation_id"] = content_hash(body)
    return observation_value


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


def _distinct_observation(event: str) -> dict[str, object]:
    value = observation()
    value["event_identity"] = event
    value["independent_event_identity"] = f"independent-{event}"
    return rehash(value)


def test_append_batch_commits_order_and_hashes_atomically(tmp_path: Path) -> None:
    store = ShadowObservationStore(tmp_path / "shadow.sqlite", start_receipt())
    first = observation()
    second = _distinct_observation("event-2")
    sequences = store.append_batch((first, second), now=NOW + timedelta(minutes=1))
    assert sequences == (1, 2)
    with sqlite3.connect(store.path) as db:
        rows = db.execute(
            "SELECT sequence,observation_id,canonical_json,content_hash FROM observations "
            "ORDER BY sequence"
        ).fetchall()
    assert [row[0] for row in rows] == [1, 2]
    assert [row[1] for row in rows] == [first["observation_id"], second["observation_id"]]
    assert [row[3] for row in rows] == [hashlib.sha256(row[2].encode()).hexdigest() for row in rows]


def test_append_batch_invalid_row_rolls_back_prior_valid_row(tmp_path: Path) -> None:
    store = ShadowObservationStore(tmp_path / "shadow.sqlite", start_receipt())
    invalid = _distinct_observation("event-2")
    invalid["production_influence"] = "1"
    with pytest.raises(ShadowKernelError, match="safety"):
        store.append_batch((observation(), invalid), now=NOW + timedelta(minutes=1))
    assert store.validate(now=NOW + timedelta(minutes=1))["observations"] == 0


def test_append_batch_acceptance_failure_rolls_back(tmp_path: Path) -> None:
    store = ShadowObservationStore(tmp_path / "shadow.sqlite", start_receipt())
    checks = 0

    def fail_before_commit() -> None:
        nonlocal checks
        checks += 1
        if checks == 2:
            raise TimeoutError("fixture deadline")

    with pytest.raises(TimeoutError):
        store.append_batch(
            (observation(), _distinct_observation("event-2")),
            now=NOW + timedelta(minutes=1),
            acceptance_check=fail_before_commit,
        )
    assert checks == 2
    assert store.validate(now=NOW + timedelta(minutes=1))["observations"] == 0


def test_append_batch_write_failure_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ShadowObservationStore(tmp_path / "shadow.sqlite", start_receipt())
    original_connect = store._connect

    class FailingConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection
            self.inserts = 0

        def __enter__(self) -> "FailingConnection":
            self.connection.__enter__()
            return self

        def __exit__(self, *args: object) -> bool:
            return bool(self.connection.__exit__(*args))

        def execute(self, sql: str, parameters: object = ()) -> sqlite3.Cursor:
            if sql.startswith("INSERT INTO observations"):
                self.inserts += 1
                if self.inserts == 2:
                    raise sqlite3.IntegrityError("fixture write failure")
            return self.connection.execute(sql, parameters)

    monkeypatch.setattr(store, "_connect", lambda: FailingConnection(original_connect()))
    with pytest.raises(sqlite3.IntegrityError):
        store.append_batch(
            (observation(), _distinct_observation("event-2")),
            now=NOW + timedelta(minutes=1),
        )
    assert store.validate(now=NOW + timedelta(minutes=1))["observations"] == 0


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


@pytest.mark.parametrize(
    "field",
    [
        "market_body_sha256",
        "event_body_sha256",
        "market_rules_hash",
        "market_metadata_hash",
        "event_metadata_hash",
        "market_parser_version",
        "event_parser_version",
        "market_snapshot_schema",
        "event_snapshot_schema",
    ],
)
def test_weather_observe_requires_complete_canonical_authority(field: str) -> None:
    weather = weather_observation()
    weather["evidence"] = dict(weather["evidence"])
    weather["evidence"]["market_authority"] = dict(weather["evidence"]["market_authority"])
    weather["evidence"]["market_authority"].pop(field)
    rehash(weather)
    with pytest.raises(ShadowKernelError, match="authority"):
        validate_observation(weather, now=NOW + timedelta(minutes=1), start_receipt=start_receipt())


def test_weather_complete_hydrated_authority_passes() -> None:
    validate_observation(
        weather_observation(), now=NOW + timedelta(minutes=1), start_receipt=start_receipt()
    )


def test_structural_authority_requires_both_exact_legs() -> None:
    missing = observation()
    missing["evidence"] = dict(missing["evidence"])
    missing["evidence"]["market_authority"] = {"A": authority("a")}
    rehash(missing)
    with pytest.raises(ShadowKernelError, match="leg"):
        validate_observation(missing, now=NOW + timedelta(minutes=1), start_receipt=start_receipt())

    wrong = observation()
    wrong["evidence"] = dict(wrong["evidence"])
    wrong["evidence"]["market_authority"] = {"A": authority("a"), "C": authority("c")}
    rehash(wrong)
    with pytest.raises(ShadowKernelError, match="leg"):
        validate_observation(wrong, now=NOW + timedelta(minutes=1), start_receipt=start_receipt())


def test_structural_complete_hydrated_authority_passes() -> None:
    validate_observation(
        observation(), now=NOW + timedelta(minutes=1), start_receipt=start_receipt()
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("kernel_version", "drifted"),
        ("adapter_versions", {"daily_weather": "drifted"}),
        ("schema_identity", "drifted"),
    ],
)
def test_start_receipt_canonical_constants_are_frozen(field: str, value: object) -> None:
    receipt = start_receipt()
    receipt[field] = value
    receipt["receipt_digest"] = content_hash(
        {k: v for k, v in receipt.items() if k != "receipt_digest"}
    )
    with pytest.raises(ShadowKernelError):
        kernel.validate_start_receipt(receipt)


def test_observation_before_start_and_convention_drift_fail_at_append_or_replay(
    tmp_path: Path,
) -> None:
    store = ShadowObservationStore(tmp_path / "shadow.sqlite", start_receipt())
    before = observation(NOW - timedelta(seconds=1))
    with pytest.raises(ShadowKernelError, match="predates"):
        store.append(before, now=NOW + timedelta(minutes=1))

    drifted = observation()
    drifted["execution_price_convention"] = "altered"
    rehash(drifted)
    with pytest.raises(ShadowKernelError, match="drifted"):
        store.append(drifted, now=NOW + timedelta(minutes=1))

    for field in ("fee_policy_identity", "slippage_depth_model"):
        convention_drift = observation()
        convention_drift[field] = "altered"
        rehash(convention_drift)
        with pytest.raises(ShadowKernelError, match="drifted"):
            store.append(convention_drift, now=NOW + timedelta(minutes=1))


def test_receipt_binding_and_production_influence_are_fail_closed() -> None:
    altered = observation()
    altered["start_receipt_digest"] = "b" * 64
    rehash(altered)
    with pytest.raises(ShadowKernelError, match="binding"):
        validate_observation(altered, now=NOW + timedelta(minutes=1), start_receipt=start_receipt())

    unsafe = observation()
    unsafe["production_influence"] = "1"
    rehash(unsafe)
    with pytest.raises(ShadowKernelError, match="safety"):
        validate_observation(unsafe, now=NOW + timedelta(minutes=1), start_receipt=start_receipt())


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
        start_receipt_digest=start_receipt()["receipt_digest"],
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


def test_exact_detail_hydration_binds_rules_and_event_authority() -> None:
    market_snapshot = _market_acquirer(MARKET_TICKER, clock=lambda: NOW)
    event_snapshot = _event_acquirer(EVENT_TICKER, clock=lambda: NOW)
    authority = hydrate_market_authority(
        {"ticker": MARKET_TICKER, "event_ticker": EVENT_TICKER},
        market_snapshot=market_snapshot,
        event_snapshot=event_snapshot,
    )
    assert authority.market.rules_hash == market_snapshot.rules_hash
    assert authority.identity["market_body_sha256"] == market_snapshot.body_sha256
    assert authority.identity["event_body_sha256"] == event_snapshot.body_sha256


def test_summary_only_data_cannot_be_accepted_without_hydration() -> None:
    market_snapshot = _market_acquirer(MARKET_TICKER, clock=lambda: NOW)
    event_snapshot = _event_acquirer(EVENT_TICKER, clock=lambda: NOW)
    authority = hydrate_market_authority(
        {"ticker": MARKET_TICKER, "event_ticker": EVENT_TICKER},
        market_snapshot=market_snapshot,
        event_snapshot=event_snapshot,
    )
    assert authority is not None
    assert authority.market.rules_hash


@pytest.mark.parametrize(
    "summary,market_change,event_change,match",
    [
        ({"ticker": "WRONG", "event_ticker": EVENT_TICKER}, {}, {}, "ticker"),
        ({"ticker": MARKET_TICKER, "event_ticker": "WRONG"}, {}, {}, "ticker"),
        (
            {"ticker": MARKET_TICKER, "event_ticker": EVENT_TICKER},
            {"classification": "MISSING"},
            {},
            "unavailable",
        ),
        (
            {"ticker": MARKET_TICKER, "event_ticker": EVENT_TICKER},
            {},
            {"classification": "MISSING"},
            "unavailable",
        ),
    ],
)
def test_authority_hydration_fails_closed(
    summary: dict[str, str],
    market_change: dict[str, str],
    event_change: dict[str, str],
    match: str,
) -> None:
    market_snapshot = replace(_market_acquirer(MARKET_TICKER, clock=lambda: NOW), **market_change)
    event_snapshot = replace(_event_acquirer(EVENT_TICKER, clock=lambda: NOW), **event_change)
    with pytest.raises(ShadowKernelError, match=match):
        hydrate_market_authority(
            summary, market_snapshot=market_snapshot, event_snapshot=event_snapshot
        )


def test_unhydrated_weather_is_explicit_abstention() -> None:
    assert "production_execution" not in inspect.getsource(kernel)
    assert "security_boundary" not in inspect.getsource(kernel)
