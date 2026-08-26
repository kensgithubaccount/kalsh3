from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from services.contract_intelligence.specification import ContractSpecificationParser
from services.market_universe.domain import stable_hash
from services.market_universe.lifecycle import LifecycleState
from services.market_universe.router import MarketUniverseRouter
from services.market_universe.semantic_source_coverage import (
    ReasonOrigin,
    SemanticSourceCoverageManifest,
    SemanticSourceCoverageProjector,
    SemanticSourceCoverageResult,
)

CAPTURED_AT = datetime(2026, 8, 25, 20, 0, tzinfo=UTC)


def series(
    *,
    source_name: str = "Official Source",
    source_url: str = "https://example.invalid/path",
) -> dict[str, object]:
    return {
        "ticker": "KXSERIES",
        "title": "Test series",
        "category": "Economics",
        "frequency": "daily",
        "settlement_sources": [{"name": source_name, "url": source_url}],
    }


def event(
    *,
    category: str = "Economics",
    settlement_sources: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "event_ticker": "KXEVENT",
        "series_ticker": "KXSERIES",
        "title": "Test event",
        "category": category,
    }
    if settlement_sources is not None:
        row["settlement_sources"] = settlement_sources
    return row


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


def project(
    markets: list[dict[str, object]],
    *,
    event_rows: list[dict[str, object]] | None = None,
    series_rows: list[dict[str, object]] | None = None,
):
    return SemanticSourceCoverageProjector().project(
        market_rows=markets,
        event_rows=[event()] if event_rows is None else event_rows,
        series_rows=[series()] if series_rows is None else series_rows,
        source_authority="captured-public-kalshi",
        request_locator="fixture://ku-a2",
        response_sha256="a" * 64,
        captured_at=CAPTURED_AT,
    )


def origins(record) -> set[ReasonOrigin]:
    return {reason.origin for reason in record.reasons}


def test_one_a2_outcome_per_input_and_exact_ku_a1_conservation() -> None:
    malformed = market("KXEVENT-BAD")
    malformed.pop("rules_primary")
    result = project([market(), market("KXEVENT-MVE", mve_collection_ticker="MVE"), malformed])
    assert result.manifest.input_market_count == 3
    assert result.manifest.accounted_market_count == 3
    assert result.manifest.parsed_market_count == 2
    assert result.manifest.quarantine_count == 1
    assert len(result.records) + len(result.quarantines) == 3
    assert result.manifest.lifecycle_record_ids == result.census.manifest.lifecycle_record_ids
    assert result.manifest.quarantine_ids == result.census.manifest.quarantine_ids
    assert {item.lifecycle_record_id for item in result.records}.isdisjoint(
        {item.quarantine_id for item in result.quarantines}
    )


def test_quarantine_projection_has_no_semantic_or_source_fields() -> None:
    malformed = market()
    malformed.pop("ticker")
    item = project([malformed]).quarantines[0]
    assert item.observed_market_ticker is None
    assert item.reason == "MARKET_PARSE_FAILURE"
    assert not hasattr(item, "semantic_status")
    assert not hasattr(item, "settlement_sources")
    assert not hasattr(item, "lifecycle_record_id")


def test_unknown_scalar_mve_and_non_event_are_visible_and_discovered() -> None:
    result = project(
        [
            market("KXEVENT-UNKNOWN", market_type="mystery"),
            market("KXEVENT-SCALAR", settlement_value_dollars="0.50"),
            market("KXEVENT-MVE", mve_collection_ticker="MVE"),
            market("KXEVENT-FUT", product_type="perpetual_future"),
        ]
    )
    assert len(result.records) == 4
    assert {item.lifecycle_state for item in result.records} == {LifecycleState.DISCOVERED.value}
    assert {item.product_type for item in result.records} == {
        "UNKNOWN",
        "SCALAR_OR_PARTIAL",
        "MULTIVARIATE_EVENT",
        "NON_EVENT",
    }


def test_unsupported_comparator_remains_discovered_with_semantic_origin() -> None:
    record = project([market(rules_primary="The official source decides the outcome.")]).records[0]
    assert record.lifecycle_state == LifecycleState.DISCOVERED.value
    assert ReasonOrigin.SEMANTIC_BLOCKER in origins(record)
    assert "RULES_COMPARATOR_UNPROVEN" in {reason.code for reason in record.reasons}


def test_parent_product_unsupported_and_descriptor_origins_are_distinct() -> None:
    parent = project([market()], event_rows=[]).records[0]
    assert ReasonOrigin.PARENT in origins(parent)

    mve = project([market(mve_collection_ticker="MVE")]).records[0]
    assert ReasonOrigin.PRODUCT in origins(mve)
    assert ReasonOrigin.UNSUPPORTED_FEATURE in origins(mve)

    bad_quote = project([market(yes_bid_dollars="not-a-number")]).records[0]
    assert ReasonOrigin.DESCRIPTOR_ISSUE in origins(bad_quote)
    assert ReasonOrigin.SEMANTIC_BLOCKER not in origins(bad_quote)


def test_m27b_is_advisory_only_and_never_promotes() -> None:
    advisory = project([market()]).records[0]
    assert ReasonOrigin.M27B_ADVISORY in origins(advisory)

    unsupported = project(
        [
            market(
                rules_primary="The official source decides the outcome.",
                strike_type="greater",
                floor_strike="10",
            )
        ]
    ).records[0]
    assert unsupported.specialist_route_state == "STRUCTURAL_DIRECTIONAL_THRESHOLD"
    assert unsupported.lifecycle_state == LifecycleState.DISCOVERED.value


def test_title_category_and_advisory_family_cannot_promote() -> None:
    result = SemanticSourceCoverageProjector().project(
        market_rows=[
            market(
                title="At least 10 with every macro keyword",
                rules_primary="The official source decides the outcome.",
            )
        ],
        event_rows=[event(category="Economics")],
        series_rows=[series()],
        source_authority="captured-public-kalshi",
        request_locator="fixture://ku-a2",
        response_sha256="a" * 64,
        captured_at=CAPTURED_AT,
    )
    assert result.records[0].lifecycle_state == LifecycleState.DISCOVERED.value


def test_llm_metadata_cannot_promote_deterministic_blocker(monkeypatch) -> None:
    router = MarketUniverseRouter()
    original = router._semantic_parser.parse

    def with_llm(*args, **kwargs):
        spec = original(*args, **kwargs)
        return replace(spec, llm_parser_version="llm-advisory-only")

    monkeypatch.setattr(router._semantic_parser, "parse", with_llm)
    result = SemanticSourceCoverageProjector(router).project(
        market_rows=[market(rules_primary="The official source decides the outcome.")],
        event_rows=[event()],
        series_rows=[series()],
        source_authority="captured-public-kalshi",
        request_locator="fixture://ku-a2",
        response_sha256="a" * 64,
        captured_at=CAPTURED_AT,
    )
    assert result.records[0].lifecycle_state == LifecycleState.DISCOVERED.value


def test_semantics_are_parsed_exactly_once_per_parsed_market(monkeypatch) -> None:
    router = MarketUniverseRouter()
    calls = 0
    original = router._semantic_parser.parse

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(router._semantic_parser, "parse", counted)
    SemanticSourceCoverageProjector(router).project(
        market_rows=[market("KXEVENT-10"), market("KXEVENT-11")],
        event_rows=[event()],
        series_rows=[series()],
        source_authority="captured-public-kalshi",
        request_locator="fixture://ku-a2",
        response_sha256="a" * 64,
        captured_at=CAPTURED_AT,
    )
    assert calls == 2


def test_semantic_projection_binds_deterministic_proof_material() -> None:
    record = project([market()]).records[0]
    assert record.deterministic_parser_version == ContractSpecificationParser.version
    assert record.source_input_hash
    assert record.semantic_hash
    assert record.semantic_status == "VALID"
    assert record.comparator == ">="
    assert record.threshold_value == Decimal("10")
    assert record.provenance_ids


def test_settlement_source_projection_preserves_distinct_same_name_origins() -> None:
    same = [{"name": "Official Source", "url": "https://example.invalid/path"}]
    record = project([market()], event_rows=[event(settlement_sources=same)]).records[0]
    assert len(record.settlement_sources) == 2
    assert {item.origin for item in record.settlement_sources} == {"EVENT", "SERIES"}
    assert len({item.source_hash for item in record.settlement_sources}) == 2
    assert {item.normalized_name for item in record.settlement_sources} == {"official source"}


def test_event_series_source_conflict_is_visible_not_collapsed() -> None:
    record = project(
        [market()],
        event_rows=[
            event(
                settlement_sources=[
                    {"name": "Event Source", "url": "https://event.invalid/path"}
                ]
            )
        ],
    ).records[0]
    assert len(record.settlement_sources) == 2
    assert record.lifecycle_state == LifecycleState.DISCOVERED.value
    assert "SETTLEMENT_SOURCE_CONFLICT" in record.blocking_issue_codes
    assert ReasonOrigin.SEMANTIC_BLOCKER in origins(record)



def test_settlement_source_identity_uses_exact_ku_a1_aggregate_tuple() -> None:
    result = project([market()])
    record = result.records[0]
    recomputed = stable_hash(
        tuple(
            sorted(
                (
                    source.source_hash,
                    source.normalized_name,
                    source.url or "",
                    source.origin,
                )
                for source in record.settlement_sources
            )
        )
    )
    assert recomputed == record.settlement_source_identity
    assert recomputed == result.census.records[0].settlement_source_identity

def test_malformed_url_has_no_permission_or_hostname_authority() -> None:
    record = project(
        [market()], series_rows=[series(source_url="not a valid network URL")]
    ).records[0]
    source = record.settlement_sources[0]
    assert source.url == "not a valid network URL"
    assert source.hostname is None
    assert not hasattr(source, "permission")
    assert not hasattr(source, "official_primary")
    assert not hasattr(source, "quality")


def test_input_order_does_not_change_a2_identity() -> None:
    markets = [market("KXEVENT-10"), market("KXEVENT-11")]
    left = project(markets)
    right = project(list(reversed(markets)))
    assert left.manifest.manifest_id == right.manifest.manifest_id
    assert left.result_id == right.result_id
    assert [item.record_id for item in left.records] == [item.record_id for item in right.records]


def test_all_a2_outputs_are_research_only_with_zero_influence() -> None:
    malformed = market("KXEVENT-BAD")
    malformed.pop("rules_primary")
    result = project([market(), malformed])
    outputs = (result, result.manifest, *result.records, *result.quarantines)
    assert all(item.research_only is True for item in outputs)
    assert all(item.production_influence == Decimal("0") for item in outputs)


def test_a2_canonical_types_reject_ordinary_construction_and_replace() -> None:
    result = project([market()])
    with pytest.raises(TypeError, match="canonical-A2-issued only"):
        SemanticSourceCoverageManifest()
    with pytest.raises(TypeError, match="canonical-A2-issued only"):
        SemanticSourceCoverageResult()
    with pytest.raises(TypeError, match="canonical-A2-issued only"):
        type(result.records[0])()
    with pytest.raises(TypeError, match="canonical-A2-issued only"):
        replace(result.records[0], semantic_status="VALID")
