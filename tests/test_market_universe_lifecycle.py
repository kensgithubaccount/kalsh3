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


def fake_understood_fields() -> dict[str, object]:
    return {
        "capture_id": capture().capture_id,
        "market_input_hash": "b" * 64,
        "market_id": None,
        "market_ticker": "KXTEST-1",
        "event_id": None,
        "event_ticker": "KXTEST",
        "series_id": None,
        "series_ticker": "KXSERIES",
        "product_type": ProductType.BINARY_EVENT,
        "payout_model": "SIMPLE_BINARY",
        "state": LifecycleState.SEMANTICALLY_UNDERSTOOD,
        "rules_hash": "c" * 64,
        "metadata_hash": "d" * 64,
        "parent_evidence_hash": "e" * 64,
        "settlement_source_identity": "f" * 64,
        "specialist_route_id": None,
        "specialist_route_state": "ROUTE_ONLY",
        "specialist_route_reasons": (),
        "advisory_family": "other/unknown",
        "semantic_status": "VALID",
        "semantic_proof_ids": ("invented-proof",),
        "semantic_blockers": (),
        "unsupported_reasons": (),
        "semantic_material_hash": "1" * 64,
        "supersedes_record_id": None,
    }


def test_ku_a1_exposes_only_two_lifecycle_states() -> None:
    assert set(LifecycleState) == {
        LifecycleState.DISCOVERED,
        LifecycleState.SEMANTICALLY_UNDERSTOOD,
    }


def test_capture_identity_is_deterministic_and_authority_is_zero() -> None:
    left = capture()
    right = capture()
    assert left == right
    assert left.capture_id == right.capture_id
    assert left.research_only is True
    assert left.production_influence == Decimal("0")


def test_capture_hash_is_descriptive_not_authenticated_acquisition_authority() -> None:
    evidence = capture()
    assert evidence.response_sha256 == "a" * 64
    documentation = " ".join((UniverseCaptureEvidence.__doc__ or "").split())
    assert (
        "not independently authenticated Kalshi acquisition or transport authority"
        in documentation
    )


def test_ordinary_direct_lifecycle_construction_is_rejected() -> None:
    with pytest.raises(TypeError, match="canonical-router-issued only"):
        MarketLifecycleRecord()


def test_direct_construction_cannot_claim_semantically_understood() -> None:
    with pytest.raises(TypeError, match="canonical-router-issued only"):
        MarketLifecycleRecord(**fake_understood_fields())


def test_invented_semantic_proof_cannot_mint_understood_without_internal_capability() -> None:
    with pytest.raises(LifecycleError, match="issuance capability is invalid"):
        MarketLifecycleRecord._issue(capability=object(), **fake_understood_fields())


def test_caller_cannot_supply_content_or_authority_fields() -> None:
    with pytest.raises(TypeError, match="canonical-router-issued only"):
        MarketLifecycleRecord(
            **fake_understood_fields(),
            lifecycle_record_id="caller-controlled",
            research_only=False,
            production_influence=Decimal("1"),
        )
