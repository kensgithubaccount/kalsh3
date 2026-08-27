from __future__ import annotations

import ast
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from services.market_universe.empirical_researchability import (
    EmpiricalResearchabilityError,
    EvidenceDomain,
    EvidenceProofKind,
    _validate_gate_resolutions,
    build_evidence_resolution_result,
)
from services.market_universe.research_family_coverage import (
    ResearchFamily,
    build_research_family_offline_report,
)
from services.market_universe.researchability_hard_gates import (
    A31_EMPIRICAL_ARTIFACT_STATUS,
    GateState,
    ResearchabilityGate,
    build_researchability_hard_gate_result,
)
from services.market_universe.semantic_source_coverage import SemanticSourceCoverageProjector

CAPTURED_AT = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def series(
    *,
    title: str = "Test series",
    category: str = "Economics",
    source_name: str = "Exchange-named source",
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
    response_sha256: str = "a" * 64,
):
    a2 = SemanticSourceCoverageProjector().project(
        market_rows=markets,
        event_rows=[event()] if event_rows is None else event_rows,
        series_rows=[series()] if series_rows is None else series_rows,
        source_authority="captured-public-kalshi",
        request_locator="fixture://ku-a3-2",
        response_sha256=response_sha256,
        captured_at=CAPTURED_AT,
    )
    return build_research_family_offline_report(a2)


def resolve(a22):
    return build_evidence_resolution_result(build_researchability_hard_gate_result(a22))


def gate(receipt, gate_id: ResearchabilityGate):
    return next(resolution for resolution in receipt.gates if resolution.gate is gate_id)


def test_broad_structural_family_cannot_inherit_narrow_domain_proof() -> None:
    result = resolve(project([market("KXEVENT-10"), market("KXEVENT-11")]))

    assert len(result.domain_receipts) == 2
    assert {receipt.family for receipt in result.domain_receipts} == {
        ResearchFamily.BINARY_THRESHOLD
    }
    assert all(
        receipt.evidence_domain is EvidenceDomain.UNASSIGNED for receipt in result.domain_receipts
    )
    assert all(
        gate(receipt, ResearchabilityGate.G1_SETTLEMENT_PROOF).resolved_state is GateState.UNKNOWN
        for receipt in result.domain_receipts
    )


def test_weather_evidence_signals_cannot_promote_non_weather_threshold_markets() -> None:
    result = resolve(
        project(
            [
                market("KXEVENT-10", title="Chicago maximum temperature"),
                market("KXEVENT-11", title="CPI threshold"),
            ],
            event_rows=[event(title="Daily weather")],
            series_rows=[
                series(
                    source_name="The Weather Company",
                    source_url="https://weather.com/official-looking",
                )
            ],
        )
    )

    assert len(result.domain_receipts) == 2
    assert all(
        receipt.evidence_domain is EvidenceDomain.UNASSIGNED for receipt in result.domain_receipts
    )
    for receipt in result.domain_receipts:
        assert (
            gate(receipt, ResearchabilityGate.G1_SETTLEMENT_PROOF).resolved_state
            is GateState.UNKNOWN
        )
        assert (
            gate(receipt, ResearchabilityGate.G2_PERMITTED_SOURCE).resolved_state
            is GateState.UNKNOWN
        )


def test_title_and_category_cannot_establish_evidence_domain() -> None:
    result = resolve(
        project(
            [market(title="official TWC daily temperature historical truth")],
            event_rows=[event(title="weather settlement proof", category="Weather")],
            series_rows=[series(title="permitted official weather", category="Weather")],
        )
    )

    assert result.domain_receipts[0].evidence_domain is EvidenceDomain.UNASSIGNED


def test_hostname_alone_cannot_establish_domain_or_g2() -> None:
    result = resolve(
        project(
            [market()],
            series_rows=[series(source_url="https://official.example.gov/permitted")],
        )
    )
    receipt = result.domain_receipts[0]

    assert receipt.evidence_domain is EvidenceDomain.UNASSIGNED
    assert (
        gate(receipt, ResearchabilityGate.G2_PERMITTED_SOURCE).resolved_state is GateState.UNKNOWN
    )


def test_m27b_routing_cannot_establish_domain_or_gate_pass() -> None:
    a22 = project([market(strike_type="greater", floor_strike="10")])
    assert a22.a2_result.records[0].specialist_route_state == "STRUCTURAL_DIRECTIONAL_THRESHOLD"
    receipt = resolve(a22).domain_receipts[0]

    assert receipt.evidence_domain is EvidenceDomain.UNASSIGNED
    assert all(resolution.resolved_state is GateState.UNKNOWN for resolution in receipt.gates[:6])


def test_missing_evidence_remains_unknown() -> None:
    receipt = resolve(project([market()])).domain_receipts[0]

    assert all(resolution.resolved_state is GateState.UNKNOWN for resolution in receipt.gates[:6])
    assert all(resolution.missing_evidence for resolution in receipt.gates[:6])


def test_positive_blocker_is_required_for_blocked() -> None:
    receipt = resolve(project([market()])).domain_receipts[0]
    gates = list(receipt.gates)
    gates[0] = replace(gates[0], resolved_state=GateState.BLOCKED)

    with pytest.raises(EmpiricalResearchabilityError, match="BLOCKED requires positive"):
        _validate_gate_resolutions(tuple(gates), EvidenceDomain.UNASSIGNED)


def test_fixtures_cannot_create_empirical_pass() -> None:
    receipt = resolve(project([market()])).domain_receipts[0]

    assert receipt.empirical_artifact_status == A31_EMPIRICAL_ARTIFACT_STATUS
    for gate_id in (
        ResearchabilityGate.G3_HISTORICAL_TRUTH,
        ResearchabilityGate.G4_POINT_IN_TIME_RECONSTRUCTION,
        ResearchabilityGate.G6_ECONOMICS_OBSERVABILITY,
    ):
        resolution = gate(receipt, gate_id)
        assert resolution.resolved_state is GateState.UNKNOWN
        assert resolution.proof_kind is None


def test_current_data_cannot_prove_historical_truth() -> None:
    receipt = resolve(
        project([market(volume_fp="999.00", open_interest_fp="777.00")])
    ).domain_receipts[0]

    assert (
        gate(receipt, ResearchabilityGate.G3_HISTORICAL_TRUTH).resolved_state is GateState.UNKNOWN
    )


def test_revised_or_final_data_cannot_prove_pit() -> None:
    receipt = resolve(
        project(
            [market(title="final revised historical vintage")],
            series_rows=[series(source_url="https://example.invalid/final-revised-vintage")],
        )
    ).domain_receipts[0]

    assert (
        gate(receipt, ResearchabilityGate.G4_POINT_IN_TIME_RECONSTRUCTION).resolved_state
        is GateState.UNKNOWN
    )


def test_source_presence_alone_cannot_prove_permission() -> None:
    receipt = resolve(
        project(
            [market()],
            series_rows=[
                series(source_name="The Weather Company", source_url="https://weather.com")
            ],
        )
    ).domain_receipts[0]

    assert (
        gate(receipt, ResearchabilityGate.G2_PERMITTED_SOURCE).resolved_state is GateState.UNKNOWN
    )


def test_semantic_shape_alone_cannot_prove_settlement_target() -> None:
    a22 = project([market(strike_type="greater", floor_strike="10")])
    assert a22.mappings[0].family is ResearchFamily.BINARY_THRESHOLD
    receipt = resolve(a22).domain_receipts[0]

    assert (
        gate(receipt, ResearchabilityGate.G1_SETTLEMENT_PROOF).resolved_state is GateState.UNKNOWN
    )


def test_evidence_domain_mapping_is_deterministic() -> None:
    a22 = project([market("KXEVENT-10"), market("KXEVENT-11")])
    left = resolve(a22)
    right = resolve(a22)

    assert left == right
    assert left.result_id == right.result_id
    assert tuple(receipt.receipt_id for receipt in left.domain_receipts) == tuple(
        receipt.receipt_id for receipt in right.domain_receipts
    )


def test_domain_mapping_preserves_all_inputs_without_silent_drops() -> None:
    a22 = project([market("KXEVENT-10"), market("KXEVENT-11"), market("KXEVENT-12")])
    result = resolve(a22)

    assert len(result.domain_receipts) == len(a22.mappings)
    assert tuple(receipt.a22_mapping_id for receipt in result.domain_receipts) == tuple(
        sorted(mapping.mapping_id for mapping in a22.mappings)
    )


def test_exact_a31_a22_a21_a1_binding_is_preserved() -> None:
    a22 = project([market()])
    a31 = build_researchability_hard_gate_result(a22)
    result = build_evidence_resolution_result(a31)
    receipt = result.domain_receipts[0]
    prior = a31.family_receipts[0]
    mapping = a22.mappings[0]

    assert result.a31_result is a31
    assert receipt.a31_result_id == a31.result_id
    assert receipt.a31_receipt_id == prior.receipt_id
    assert receipt.a22_result_id == a22.result_id
    assert receipt.a22_report_id == a22.report.report_id
    assert receipt.a22_mapping_id == mapping.mapping_id
    assert receipt.a2_result_id == a22.a2_result.result_id
    assert receipt.a2_manifest_id == a22.a2_result.manifest.manifest_id
    assert receipt.census_manifest_id == a22.a2_result.census.manifest.manifest_id
    assert receipt.coverage_manifest_id == a22.a2_result.census.coverage_manifest.manifest_id
    assert receipt.capture_id == a22.a2_result.census.capture.capture_id
    assert receipt.source_record_id == mapping.source_record_id


def test_foreign_or_tampered_a31_and_a22_receipts_are_rejected() -> None:
    a22 = project([market()])
    a31 = build_researchability_hard_gate_result(a22)
    object.__setattr__(a31.family_receipts[0], "content_hash", "tampered")
    with pytest.raises(EmpiricalResearchabilityError, match="foreign, tampered, or mismatched"):
        build_evidence_resolution_result(a31)

    a22 = project([market()], response_sha256="b" * 64)
    a31 = build_researchability_hard_gate_result(a22)
    object.__setattr__(a31.a22_result.mappings[0], "content_hash", "tampered")
    with pytest.raises(EmpiricalResearchabilityError, match="foreign, tampered, or mismatched"):
        build_evidence_resolution_result(a31)


def test_no_numeric_readiness_score_exists() -> None:
    result = resolve(project([market()]))
    outputs = (result, *result.domain_receipts)

    for output in outputs:
        fields = getattr(output, "__dataclass_fields__", {})
        assert "readiness_score" not in fields
        assert "score" not in fields


def test_no_ranking_exists() -> None:
    result = resolve(project([market()]))
    outputs = (result, *result.domain_receipts)

    for output in outputs:
        fields = getattr(output, "__dataclass_fields__", {})
        assert {"rank", "ranking", "best_family", "capital_recommendation"}.isdisjoint(fields)


def test_no_ev_or_profitability_exists() -> None:
    receipt = resolve(project([market()])).domain_receipts[0]
    fields = getattr(receipt, "__dataclass_fields__", {})

    assert {"ev", "expected_value", "edge", "profit", "profitability"}.isdisjoint(fields)
    assert (
        gate(receipt, ResearchabilityGate.G6_ECONOMICS_OBSERVABILITY).resolved_state
        is GateState.UNKNOWN
    )


def test_no_lifecycle_promotion_exists() -> None:
    result = resolve(project([market()]))
    outputs = (result, *result.domain_receipts)

    for output in outputs:
        fields = getattr(output, "__dataclass_fields__", {})
        assert {"promotion", "lifecycle_promotion", "trade_eligibility"}.isdisjoint(fields)
        assert output.research_only is True
        assert output.production_influence == Decimal("0")


def test_no_network_account_credential_signer_risk_or_order_dependency() -> None:
    path = Path("services/market_universe/empirical_researchability.py")
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
        "network",
        "production_execution",
        "demo_execution",
        "account",
        "credential",
        "signer",
        "risk_engine",
        "order",
        "mutation",
        "arm",
        "burn",
        "acknowledgement",
    )
    assert not any(any(token in imported for token in forbidden) for imported in imports)


def test_g7_authority_isolation_cannot_be_weakened() -> None:
    receipt = resolve(project([market()])).domain_receipts[0]
    g7 = gate(receipt, ResearchabilityGate.G7_AUTHORITY_ISOLATION)

    assert g7.prior_state is GateState.PASS
    assert g7.resolved_state is GateState.PASS
    assert g7.proof_kind is EvidenceProofKind.STRUCTURAL

    gates = list(receipt.gates)
    gates[-1] = replace(gates[-1], resolved_state=GateState.UNKNOWN, proof_kind=None)
    with pytest.raises(EmpiricalResearchabilityError, match="cannot weaken G7"):
        _validate_gate_resolutions(tuple(gates), EvidenceDomain.UNASSIGNED)
