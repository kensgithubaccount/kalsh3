from __future__ import annotations

import ast
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from services.market_universe.research_family_coverage import (
    FamilyMappingStatus,
    ResearchFamily,
    ResearchFamilyCoverageError,
    build_research_family_offline_report,
)
from services.market_universe.semantic_source_coverage import SemanticSourceCoverageProjector

CAPTURED_AT = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def series(
    *,
    title: str = "Test series",
    category: str = "Economics",
    source_name: str = "Official Source",
    source_url: str = "https://example.invalid/path",
) -> dict[str, object]:
    return {
        "ticker": "KXSERIES",
        "title": title,
        "category": category,
        "frequency": "daily",
        "settlement_sources": [{"name": source_name, "url": source_url}],
    }


def event(*, title: str = "Test event", category: str = "Economics") -> dict[str, object]:
    return {
        "event_ticker": "KXEVENT",
        "series_ticker": "KXSERIES",
        "title": title,
        "category": category,
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
        "expiration_time": "2026-08-28T20:00:00Z",
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
    response_sha256: str = "b" * 64,
):
    return SemanticSourceCoverageProjector().project(
        market_rows=markets,
        event_rows=[event()] if event_rows is None else event_rows,
        series_rows=[series()] if series_rows is None else series_rows,
        source_authority="captured-public-kalshi",
        request_locator="fixture://ku-a2-2",
        response_sha256=response_sha256,
        captured_at=CAPTURED_AT,
    )


def test_deterministic_mapping_and_order_invariant_result() -> None:
    markets = [market("KXEVENT-10"), market("KXEVENT-11")]
    left = build_research_family_offline_report(project(markets))
    right = build_research_family_offline_report(project(list(reversed(markets))))

    assert left.result_id == right.result_id
    assert left.report.report_id == right.report.report_id
    assert [item.mapping_id for item in left.mappings] == [
        item.mapping_id for item in right.mappings
    ]
    assert {item.family for item in left.mappings} == {ResearchFamily.BINARY_THRESHOLD}


def test_every_a2_input_has_exactly_one_mapping_and_quarantine_is_explicit_unknown() -> None:
    malformed = market("KXEVENT-BAD")
    malformed.pop("rules_primary")
    a2 = project([market(), malformed])
    result = build_research_family_offline_report(a2)

    assert len(result.mappings) == a2.manifest.input_market_count == 2
    assert result.report.total_input_count == 2
    assert result.report.mapped_count + result.report.unmapped_count == 2
    quarantine = next(item for item in result.mappings if item.quarantine_id is not None)
    assert quarantine.family is ResearchFamily.UNKNOWN_UNMAPPED
    assert quarantine.mapping_status is FamilyMappingStatus.UNMAPPED


def test_unproven_semantics_remain_explicit_unknown_unmapped() -> None:
    a2 = project([market(rules_primary="The official source decides the outcome.")])
    result = build_research_family_offline_report(a2)
    mapping = result.mappings[0]

    assert mapping.family is ResearchFamily.UNKNOWN_UNMAPPED
    assert mapping.mapping_status is FamilyMappingStatus.UNMAPPED
    assert mapping.rule_code == "NO_REVIEWED_RULE_PROVEN"


def test_title_and_category_alone_cannot_grant_family() -> None:
    a2 = project(
        [
            market(
                title="weather economics election threshold research family",
                rules_primary="The official source decides the outcome.",
            )
        ],
        event_rows=[event(title="Weather macro threshold", category="Economics")],
        series_rows=[series(title="Researchable weather series", category="Weather")],
    )
    mapping = build_research_family_offline_report(a2).mappings[0]
    assert mapping.family is ResearchFamily.UNKNOWN_UNMAPPED


def test_hostname_alone_cannot_grant_family_or_authority() -> None:
    a2 = project(
        [market(rules_primary="The official source decides the outcome.")],
        series_rows=[series(source_url="https://official.example.gov/data")],
    )
    result = build_research_family_offline_report(a2)
    mapping = result.mappings[0]

    assert mapping.family is ResearchFamily.UNKNOWN_UNMAPPED
    assert not hasattr(mapping, "researchable")
    assert not hasattr(mapping, "official")
    assert not hasattr(result.report, "source_authority_score")


def test_m27b_advisory_route_cannot_grant_family() -> None:
    a2 = project(
        [
            market(
                rules_primary="The official source decides the outcome.",
                strike_type="greater",
                floor_strike="10",
            )
        ]
    )
    record = a2.records[0]
    assert record.specialist_route_state == "STRUCTURAL_DIRECTIONAL_THRESHOLD"
    mapping = build_research_family_offline_report(a2).mappings[0]
    assert mapping.family is ResearchFamily.UNKNOWN_UNMAPPED


def test_foreign_or_tampered_a2_receipt_is_rejected() -> None:
    left = project([market()], response_sha256="c" * 64)
    right = project([market()], response_sha256="d" * 64)
    object.__setattr__(left, "records", right.records)

    with pytest.raises(ResearchFamilyCoverageError, match="foreign or tampered"):
        build_research_family_offline_report(left)


def test_exact_capture_and_census_binding_is_required() -> None:
    a2 = project([market()])
    object.__setattr__(a2.records[0], "capture_id", "foreign-capture")

    with pytest.raises(ResearchFamilyCoverageError, match="foreign or tampered"):
        build_research_family_offline_report(a2)


def test_mapping_does_not_mutate_a1_or_a2_identities() -> None:
    a2 = project([market()])
    before = (
        a2.census.capture.capture_id,
        a2.census.manifest.manifest_id,
        a2.census.coverage_manifest.manifest_id,
        a2.manifest.manifest_id,
        a2.result_id,
        tuple(item.lifecycle_record_id for item in a2.census.records),
        tuple(item.record_id for item in a2.records),
    )
    result = build_research_family_offline_report(a2)
    after = (
        a2.census.capture.capture_id,
        a2.census.manifest.manifest_id,
        a2.census.coverage_manifest.manifest_id,
        a2.manifest.manifest_id,
        a2.result_id,
        tuple(item.lifecycle_record_id for item in a2.census.records),
        tuple(item.record_id for item in a2.records),
    )

    assert before == after
    assert result.a2_result is a2


def test_report_reuses_canonical_a2_aggregates_without_semantic_redefinition() -> None:
    a2 = project([market()])
    report = build_research_family_offline_report(a2).report

    assert report.a2_lifecycle_state_counts is a2.manifest.lifecycle_state_counts
    assert report.a2_semantic_status_counts is a2.manifest.semantic_status_counts
    assert report.a2_reason_origin_counts is a2.manifest.reason_origin_counts
    assert report.a2_product_counts is a2.manifest.product_counts
    assert report.a2_payout_counts is a2.manifest.payout_counts
    assert report.a2_strike_type_counts is a2.manifest.strike_type_counts
    assert report.a2_category_counts is a2.manifest.category_counts
    assert report.a2_series_counts is a2.manifest.series_counts
    assert report.a2_recurrence_counts is a2.manifest.recurrence_counts
    assert (
        report.a2_settlement_source_origin_counts
        is a2.manifest.settlement_source_origin_counts
    )
    assert report.a2_unknown_unavailable_counts is a2.manifest.unknown_unavailable_counts


def test_report_describes_settlement_source_presence_and_unknown_fields() -> None:
    malformed = market("KXEVENT-BAD")
    malformed.pop("ticker")
    a2 = project([market(), malformed])
    report = build_research_family_offline_report(a2).report

    assert ("PRESENT", 1) in report.settlement_source_presence_counts
    assert ("UNAVAILABLE", 1) in report.settlement_source_presence_counts
    assert report.a2_settlement_source_origin_counts == a2.manifest.settlement_source_origin_counts
    assert report.a2_unknown_unavailable_counts == a2.manifest.unknown_unavailable_counts


def test_report_and_mapping_have_no_readiness_ev_rank_or_production_authority() -> None:
    result = build_research_family_offline_report(project([market()]))
    outputs = (result, result.report, *result.mappings)
    forbidden_fields = {
        "readiness",
        "readiness_score",
        "ev",
        "expected_value",
        "rank",
        "ranking",
        "gates",
        "promotion",
        "capital_allocation",
        "execution_authority",
    }

    for output in outputs:
        assert output.research_only is True
        assert output.production_influence == Decimal("0")
        assert forbidden_fields.isdisjoint(getattr(output, "__dataclass_fields__", {}))


def test_a2_2_result_rejects_replaced_exact_a2_object_binding() -> None:
    original = project([market()], response_sha256="e" * 64)
    replacement = project([market()], response_sha256="f" * 64)
    result = build_research_family_offline_report(original)
    object.__setattr__(result, "a2_result", replacement)

    with pytest.raises(ResearchFamilyCoverageError, match="exact A2.1 object binding"):
        result._validate_canonical_identity()


def test_module_has_no_network_execution_account_or_credential_dependencies() -> None:
    path = Path("services/market_universe/research_family_coverage.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)

    forbidden = (
        "requests",
        "httpx",
        "aiohttp",
        "socket",
        "urllib.request",
        "production_execution",
        "demo_execution",
        "account",
        "credential",
        "signer",
        "risk_engine",
        "order",
    )
    assert not any(any(token in imported for token in forbidden) for imported in imports)
