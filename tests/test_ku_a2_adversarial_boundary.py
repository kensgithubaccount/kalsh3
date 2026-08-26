from __future__ import annotations

import ast
from copy import copy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from services.market_universe.lifecycle import MarketLifecycleRecord
from services.market_universe.router import CensusQuarantineRecord, MarketUniverseRouter
from services.market_universe.semantic_source_coverage import (
    _A2_ISSUANCE_CAPABILITY,
    SemanticSourceCoverageError,
    SemanticSourceCoverageManifest,
    SemanticSourceCoverageProjector,
    SemanticSourceCoverageResult,
    _validate_a1_context,
)

CAPTURED_AT = datetime(2026, 8, 25, 20, 0, tzinfo=UTC)


def series() -> dict[str, object]:
    return {
        "ticker": "KXSERIES",
        "title": "Test series",
        "category": "Economics",
        "frequency": "daily",
        "settlement_sources": [
            {"name": "Official Source", "url": "https://example.invalid/path"}
        ],
    }


def event() -> dict[str, object]:
    return {
        "event_ticker": "KXEVENT",
        "series_ticker": "KXSERIES",
        "title": "Test event",
        "category": "Economics",
    }


def market(ticker: str = "KXEVENT-10", **changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "ticker": ticker,
        "event_ticker": "KXEVENT",
        "title": "Test threshold",
        "market_type": "binary",
        "status": "active",
        "rules_primary": "The market resolves Yes if the official value is at least 10.",
        "price_level_structure": "standard",
        "timezone": "UTC",
        "expiration_time": "2026-08-26T20:00:00Z",
        "volume_fp": "12.00",
        "open_interest_fp": "3.00",
    }
    row.update(changes)
    return row


def context(markets: list[dict[str, object]]):
    return MarketUniverseRouter()._census_with_context(
        market_rows=markets,
        event_rows=[event()],
        series_rows=[series()],
        source_authority="captured-public-kalshi",
        request_locator="fixture://ku-a2-boundary",
        response_sha256="a" * 64,
        captured_at=CAPTURED_AT,
    )


def project(markets: list[dict[str, object]]):
    return SemanticSourceCoverageProjector().project(
        market_rows=markets,
        event_rows=[event()],
        series_rows=[series()],
        source_authority="captured-public-kalshi",
        request_locator="fixture://ku-a2-boundary",
        response_sha256="a" * 64,
        captured_at=CAPTURED_AT,
    )


def tampered_census(ctx, **changes):
    result = copy(ctx.result)
    for name, value in changes.items():
        object.__setattr__(result, name, value)
    clone = copy(ctx)
    object.__setattr__(clone, "result", result)
    return clone


def test_forged_lifecycle_subclass_is_rejected() -> None:
    ctx = context([market()])
    original = ctx.result.records[0]

    class ForgedLifecycleRecord(MarketLifecycleRecord):
        pass

    forged = object.__new__(ForgedLifecycleRecord)
    for name in MarketLifecycleRecord.__dataclass_fields__:
        object.__setattr__(forged, name, getattr(original, name))
    bad = tampered_census(ctx, records=(forged,))
    with pytest.raises(SemanticSourceCoverageError, match="identity"):
        _validate_a1_context(bad)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("lifecycle_record_id", "caller-invented"),
        ("content_hash", "caller-invented"),
        ("semantic_material_hash", "caller-invented"),
    ],
)
def test_mutated_lifecycle_identity_or_semantics_are_rejected(field: str, value: str) -> None:
    ctx = context([market()])
    altered = copy(ctx.result.records[0])
    object.__setattr__(altered, field, value)
    bad = tampered_census(ctx, records=(altered,))
    with pytest.raises(SemanticSourceCoverageError, match="identity"):
        _validate_a1_context(bad)


def test_forged_or_tampered_quarantine_is_rejected() -> None:
    malformed = market()
    malformed.pop("ticker")
    ctx = context([malformed])
    altered = copy(ctx.result.quarantines[0])
    object.__setattr__(altered, "quarantine_id", "caller-invented")
    bad = tampered_census(ctx, quarantines=(altered,))
    with pytest.raises(SemanticSourceCoverageError, match="identity"):
        _validate_a1_context(bad)


def test_mismatched_capture_and_census_are_rejected() -> None:
    left = context([market()])
    right = MarketUniverseRouter()._census_with_context(
        market_rows=[market()],
        event_rows=[event()],
        series_rows=[series()],
        source_authority="captured-public-kalshi",
        request_locator="fixture://different",
        response_sha256="b" * 64,
        captured_at=CAPTURED_AT,
    )
    bad = tampered_census(left, capture=right.result.capture)
    with pytest.raises(SemanticSourceCoverageError, match="identity|receipt"):
        _validate_a1_context(bad)


def test_omitted_a2_record_fails_manifest_issuance() -> None:
    markets = [market("KXEVENT-10"), market("KXEVENT-11")]
    result = project(markets)
    ctx = context(markets)
    with pytest.raises(SemanticSourceCoverageError, match="lifecycle identities"):
        SemanticSourceCoverageManifest._issue(
            capability=_A2_ISSUANCE_CAPABILITY,
            context=ctx,
            records=result.records[:-1],
            quarantines=result.quarantines,
        )


def test_extra_or_duplicate_a2_record_fails_manifest_issuance() -> None:
    result = project([market()])
    ctx = context([market()])
    with pytest.raises(SemanticSourceCoverageError):
        SemanticSourceCoverageManifest._issue(
            capability=_A2_ISSUANCE_CAPABILITY,
            context=ctx,
            records=(*result.records, result.records[0]),
            quarantines=result.quarantines,
        )


def test_omitted_record_fails_result_issuance() -> None:
    markets = [market("KXEVENT-10"), market("KXEVENT-11")]
    result = project(markets)
    ctx = context(markets)
    with pytest.raises(SemanticSourceCoverageError):
        SemanticSourceCoverageResult._issue(
            capability=_A2_ISSUANCE_CAPABILITY,
            context=ctx,
            records=result.records[:-1],
            quarantines=result.quarantines,
            manifest=result.manifest,
        )


def test_a2_manifest_is_descriptive_only_without_readiness_or_economics() -> None:
    manifest = project([market()]).manifest
    forbidden = (
        "readiness",
        "rank",
        "winner",
        "historical_depth",
        "capacity",
        "slippage",
        "fee",
        "economics",
        "edge",
    )
    assert all(not hasattr(manifest, name) for name in forbidden)


def test_a2_dependency_graph_cannot_reach_execution_or_network_clients() -> None:
    root = Path(__file__).resolve().parents[1]
    start = root / "services/market_universe/semantic_source_coverage.py"
    source = start.read_text()
    forbidden_tokens = (
        "requests",
        "httpx",
        "websocket",
        "websockets",
        "urllib.request",
        "risk_engine",
        "credential",
        "account_gateway",
        "signer",
        "order_submission",
        "production_execution",
        "demo_execution",
    )
    assert all(token not in source for token in forbidden_tokens)
    tree = ast.parse(source)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not any(name.startswith(("requests", "httpx", "websocket")) for name in imported)


def test_quarantine_reason_is_counted_under_quarantine_origin() -> None:
    malformed = market()
    malformed.pop("ticker")
    manifest = project([malformed]).manifest
    assert manifest.reason_origin_counts == (("QUARANTINE", 1),)


def test_manifest_exposes_required_descriptive_aggregates() -> None:
    manifest = project([market()]).manifest
    assert manifest.lifecycle_state_counts
    assert manifest.semantic_status_counts
    assert manifest.reason_origin_counts
    assert manifest.product_counts
    assert manifest.payout_counts
    assert manifest.category_counts
    assert manifest.series_counts
    assert manifest.recurrence_counts
    assert manifest.settlement_source_name_counts
    assert manifest.settlement_source_host_counts
    assert manifest.settlement_source_origin_counts
    assert manifest.specialist_route_state_counts
    assert manifest.unknown_unavailable_counts


def test_unmodified_copied_lifecycle_evidence_is_rejected() -> None:
    ctx = context([market()])
    copied = copy(ctx.result.records[0])
    bad = tampered_census(ctx, records=(copied,))
    with pytest.raises(SemanticSourceCoverageError, match="exact-object identity"):
        _validate_a1_context(bad)


def test_unmodified_copied_quarantine_evidence_is_rejected() -> None:
    malformed = market()
    malformed.pop("ticker")
    ctx = context([malformed])
    copied = copy(ctx.result.quarantines[0])
    bad = tampered_census(ctx, quarantines=(copied,))
    with pytest.raises(SemanticSourceCoverageError, match="exact-object identity"):
        _validate_a1_context(bad)


def test_manually_reconstructed_equal_quarantine_is_rejected() -> None:
    malformed = market()
    malformed.pop("ticker")
    ctx = context([malformed])
    original = ctx.result.quarantines[0]
    rebuilt = CensusQuarantineRecord(
        original.capture_id,
        original.market_input_hash,
        original.observed_market_ticker,
        original.occurrence_ordinal,
        original.reason,
        original.detail,
    )
    assert rebuilt == original
    assert rebuilt is not original
    bad = tampered_census(ctx, quarantines=(rebuilt,))
    with pytest.raises(SemanticSourceCoverageError, match="exact-object identity"):
        _validate_a1_context(bad)


def test_unmodified_copied_descriptor_evidence_is_rejected() -> None:
    ctx = context([market()])
    copied = copy(ctx.result.coverage_descriptors[0])
    bad = tampered_census(ctx, coverage_descriptors=(copied,))
    with pytest.raises(SemanticSourceCoverageError, match="exact-object identity"):
        _validate_a1_context(bad)


def test_copied_census_result_is_rejected_even_when_content_equal() -> None:
    ctx = context([market()])
    copied_result = copy(ctx.result)
    assert copied_result == ctx.result
    clone = copy(ctx)
    object.__setattr__(clone, "result", copied_result)
    with pytest.raises(SemanticSourceCoverageError, match="exact-object identity"):
        _validate_a1_context(clone)


def test_copied_contract_specification_is_not_exact_routing_proof() -> None:
    ctx = context([market()])
    routed = ctx.routed_markets[0]
    assert routed.outcome.specification is not None
    copied_spec = copy(routed.outcome.specification)
    assert copied_spec == routed.outcome.specification
    object.__setattr__(routed.outcome, "specification", copied_spec)
    with pytest.raises(SemanticSourceCoverageError, match="exact-object identity"):
        _validate_a1_context(ctx)


def test_foreign_lifecycle_projection_cannot_be_inserted_into_manifest() -> None:
    left_market = market("KXEVENT-10")
    right_market = market("KXEVENT-11")
    ctx = context([left_market])
    foreign = project([right_market])
    with pytest.raises(SemanticSourceCoverageError):
        SemanticSourceCoverageManifest._issue(
            capability=_A2_ISSUANCE_CAPABILITY,
            context=ctx,
            records=foreign.records,
            quarantines=foreign.quarantines,
        )


def test_foreign_quarantine_projection_cannot_be_inserted_into_manifest() -> None:
    left = market("KXEVENT-BAD-1")
    left.pop("ticker")
    right = market("KXEVENT-BAD-2", title="different malformed input")
    right.pop("ticker")
    ctx = context([left])
    foreign = project([right])
    with pytest.raises(SemanticSourceCoverageError):
        SemanticSourceCoverageManifest._issue(
            capability=_A2_ISSUANCE_CAPABILITY,
            context=ctx,
            records=foreign.records,
            quarantines=foreign.quarantines,
        )


def test_tampered_a2_record_cannot_be_used_to_assemble_manifest() -> None:
    result = project([market()])
    ctx = context([market()])
    altered = copy(result.records[0])
    object.__setattr__(altered, "market_ticker", "FORGED")
    with pytest.raises(SemanticSourceCoverageError, match="content-addressed identity"):
        SemanticSourceCoverageManifest._issue(
            capability=_A2_ISSUANCE_CAPABILITY,
            context=ctx,
            records=(altered,),
            quarantines=result.quarantines,
        )


def test_tampered_a2_manifest_cannot_be_used_to_assemble_result() -> None:
    result = project([market()])
    ctx = context([market()])
    altered = copy(result.manifest)
    object.__setattr__(altered, "census_manifest_id", "FORGED")
    with pytest.raises(SemanticSourceCoverageError, match="content-addressed identity"):
        SemanticSourceCoverageResult._issue(
            capability=_A2_ISSUANCE_CAPABILITY,
            context=ctx,
            records=result.records,
            quarantines=result.quarantines,
            manifest=altered,
        )


def test_a2_internal_issuance_capability_is_required() -> None:
    result = project([market()])
    ctx = context([market()])
    with pytest.raises(SemanticSourceCoverageError, match="issuance capability"):
        SemanticSourceCoverageManifest._issue(
            capability=object(),
            context=ctx,
            records=result.records,
            quarantines=result.quarantines,
        )
    with pytest.raises(SemanticSourceCoverageError, match="issuance capability"):
        SemanticSourceCoverageResult._issue(
            capability=object(),
            context=ctx,
            records=result.records,
            quarantines=result.quarantines,
            manifest=result.manifest,
        )


def test_a2_result_binds_exact_a1_census_and_coverage_manifests() -> None:
    result = project([market()])
    assert result.manifest.census_manifest_id == result.census.manifest.manifest_id
    assert result.manifest.coverage_manifest_id == result.census.coverage_manifest.manifest_id
    result._validate_canonical_identity()
