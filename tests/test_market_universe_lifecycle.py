from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from services.market_universe.lifecycle import (
    LifecycleError,
    LifecycleState,
    MarketLifecycleRecord,
    ProductType,
    UniverseCaptureEvidence,
)


def capture() -> UniverseCaptureEvidence:
    return UniverseCaptureEvidence(
        source_authority="captured-public-kalshi",
        request_locator="fixture://whole-exchange",
        response_sha256="a" * 64,
        captured_at=datetime(2026, 8, 25, 20, 0, tzinfo=UTC),
    )


def record(**changes: object) -> MarketLifecycleRecord:
    values = dict(
        capture_id=capture().capture_id,
        market_input_hash="b" * 64,
        market_id=None,
        market_ticker="KXTEST-1",
        event_id=None,
        event_ticker="KXTEST",
        series_id=None,
        series_ticker="KXSERIES",
        product_type=ProductType.BINARY_EVENT,
        payout_model="SIMPLE_BINARY",
        state=LifecycleState.DISCOVERED,
        rules_hash="c" * 64,
        metadata_hash="d" * 64,
        parent_evidence_hash="e" * 64,
        settlement_source_identity="f" * 64,
        specialist_route_id=None,
        specialist_route_state="ROUTE_ONLY",
        specialist_route_reasons=("UNSUPPORTED_STRIKE_TYPE",),
        advisory_family="other/unknown",
        semantic_status="AMBIGUOUS",
        semantic_proof_ids=(),
        semantic_blockers=("UNKNOWN_LANGUAGE",),
        unsupported_reasons=(),
        semantic_material_hash="1" * 64,
        supersedes_record_id=None,
    )
    values.update(changes)
    return MarketLifecycleRecord(**values)


def test_ku_a1_exposes_only_two_lifecycle_states() -> None:
    assert set(LifecycleState) == {
        LifecycleState.DISCOVERED,
        LifecycleState.SEMANTICALLY_UNDERSTOOD,
    }


def test_record_identity_is_deterministic_and_authority_is_zero() -> None:
    left = record()
    right = record()
    assert left == right
    assert left.lifecycle_record_id == right.lifecycle_record_id
    assert left.research_only is True
    assert left.production_influence == Decimal("0")


def test_semantically_understood_requires_valid_binary_deterministic_proof() -> None:
    understood = record(
        state=LifecycleState.SEMANTICALLY_UNDERSTOOD,
        semantic_status="VALID",
        semantic_proof_ids=("proof-1", "proof-2"),
        semantic_blockers=(),
        specialist_route_reasons=(),
    )
    assert understood.state is LifecycleState.SEMANTICALLY_UNDERSTOOD
    with pytest.raises(LifecycleError):
        replace(understood, product_type=ProductType.MULTIVARIATE_EVENT)
    with pytest.raises(LifecycleError):
        replace(understood, semantic_status="AMBIGUOUS")
    with pytest.raises(LifecycleError):
        replace(understood, semantic_proof_ids=())


def test_discovered_state_cannot_be_reasonless() -> None:
    with pytest.raises(LifecycleError):
        record(semantic_blockers=(), unsupported_reasons=(), specialist_route_reasons=())


def test_caller_cannot_supply_content_or_authority_fields() -> None:
    base = record()
    with pytest.raises(TypeError):
        MarketLifecycleRecord(  # type: ignore[call-arg]
            **{name: getattr(base, name) for name in base.__dataclass_fields__ if name not in {
                "schema_version",
                "lifecycle_record_id",
                "content_hash",
                "research_only",
                "production_influence",
            }},
            lifecycle_record_id="caller-controlled",
        )


def test_supersession_changes_identity_without_mutating_prior_record() -> None:
    prior = record()
    current = record(supersedes_record_id=prior.lifecycle_record_id)
    assert current.lifecycle_record_id != prior.lifecycle_record_id
    assert prior.supersedes_record_id is None
