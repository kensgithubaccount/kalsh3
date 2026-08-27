from __future__ import annotations

import ast
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from services.market_universe.research_family_coverage import (
    ResearchFamily,
    build_research_family_offline_report,
)
from services.market_universe.researchability_hard_gates import (
    A31_EMPIRICAL_ARTIFACT_STATUS,
    HARD_GATE_ORDER,
    GateReason,
    GateState,
    ResearchabilityGate,
    ResearchabilityHardGateError,
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
        request_locator="fixture://ku-a3-1",
        response_sha256=response_sha256,
        captured_at=CAPTURED_AT,
    )
    return build_research_family_offline_report(a2)


def receipt_for(result, family: ResearchFamily | None = None):
    a31 = build_researchability_hard_gate_result(result)
    if family is None:
        assert len(a31.family_receipts) == 1
        return a31.family_receipts[0]
    return next(receipt for receipt in a31.family_receipts if receipt.family is family)


def gate(receipt, gate_id: ResearchabilityGate):
    return next(decision for decision in receipt.gates if decision.gate is gate_id)


def test_deterministic_gate_results() -> None:
    a22 = project([market()])
    left = build_researchability_hard_gate_result(a22)
    right = build_researchability_hard_gate_result(a22)

    assert left == right
    assert left.result_id == right.result_id
    assert left.family_receipts[0].receipt_id == right.family_receipts[0].receipt_id


def test_input_order_invariance() -> None:
    rows = [market("KXEVENT-10"), market("KXEVENT-11")]
    left = build_researchability_hard_gate_result(project(rows))
    right = build_researchability_hard_gate_result(project(list(reversed(rows))))

    assert left.result_id == right.result_id
    assert tuple(receipt.receipt_id for receipt in left.family_receipts) == tuple(
        receipt.receipt_id for receipt in right.family_receipts
    )


def test_exact_seven_gate_conservation() -> None:
    receipt = receipt_for(project([market()]))

    assert len(receipt.gates) == 7
    assert tuple(decision.gate for decision in receipt.gates) == HARD_GATE_ORDER
    assert len({decision.gate for decision in receipt.gates}) == 7


def test_only_pass_blocked_unknown_states_exist() -> None:
    assert set(GateState) == {GateState.PASS, GateState.BLOCKED, GateState.UNKNOWN}
    receipt = receipt_for(project([market()]))
    assert {decision.state for decision in receipt.gates} <= set(GateState)


def test_missing_evidence_cannot_become_pass() -> None:
    receipt = receipt_for(project([market()]))

    for gate_id in HARD_GATE_ORDER[:6]:
        assert gate(receipt, gate_id).state is GateState.UNKNOWN
    assert gate(receipt, ResearchabilityGate.G7_AUTHORITY_ISOLATION).state is GateState.PASS


def test_title_and_category_cannot_prove_a_gate() -> None:
    a22 = project(
        [market(title="official historical profitable PIT economics source")],
        event_rows=[event(title="official historical truth", category="Researchable")],
        series_rows=[series(title="permitted source proof", category="Ready")],
    )
    receipt = receipt_for(a22)

    assert all(decision.state is GateState.UNKNOWN for decision in receipt.gates[:6])


def test_hostname_cannot_prove_g2() -> None:
    a22 = project(
        [market()],
        series_rows=[series(source_url="https://official.example.gov/permitted")],
    )
    decision = gate(receipt_for(a22), ResearchabilityGate.G2_PERMITTED_SOURCE)

    assert decision.state is GateState.UNKNOWN
    assert GateReason.SOURCE_POLICY_UNPROVEN in decision.reason_codes


def test_m27b_route_cannot_prove_a_gate() -> None:
    a22 = project(
        [market(strike_type="greater", floor_strike="10")],
    )
    assert a22.a2_result.records[0].specialist_route_state == "STRUCTURAL_DIRECTIONAL_THRESHOLD"
    receipt = receipt_for(a22)

    assert all(decision.state is GateState.UNKNOWN for decision in receipt.gates[:6])


def test_a2_2_family_classification_alone_cannot_prove_researchability() -> None:
    a22 = project([market()])
    assert a22.mappings[0].family is ResearchFamily.BINARY_THRESHOLD
    receipt = receipt_for(a22)

    assert not hasattr(receipt, "researchable")
    assert not hasattr(receipt, "readiness")
    assert [decision.state for decision in receipt.gates[:6]] == [GateState.UNKNOWN] * 6


def test_fixtures_cannot_masquerade_as_empirical_repository_evidence() -> None:
    receipt = receipt_for(project([market()]))

    assert receipt.empirical_artifact_status == A31_EMPIRICAL_ARTIFACT_STATUS
    for gate_id in (
        ResearchabilityGate.G3_HISTORICAL_TRUTH,
        ResearchabilityGate.G4_POINT_IN_TIME_RECONSTRUCTION,
        ResearchabilityGate.G6_ECONOMICS_OBSERVABILITY,
    ):
        decision = gate(receipt, gate_id)
        assert decision.state is GateState.UNKNOWN
        assert GateReason.EMPIRICAL_ARTIFACT_UNAVAILABLE in decision.reason_codes


def test_current_data_cannot_prove_historical_truth() -> None:
    receipt = receipt_for(project([market(volume_fp="999.00", open_interest_fp="777.00")]))
    decision = gate(receipt, ResearchabilityGate.G3_HISTORICAL_TRUTH)

    assert decision.state is GateState.UNKNOWN
    assert GateReason.HISTORICAL_TRUTH_UNPROVEN in decision.reason_codes


def test_final_or_revised_label_cannot_prove_point_in_time_reconstruction() -> None:
    a22 = project(
        [market(title="final revised historical vintage")],
        series_rows=[series(source_url="https://example.invalid/final-revised-vintage")],
    )
    decision = gate(receipt_for(a22), ResearchabilityGate.G4_POINT_IN_TIME_RECONSTRUCTION)

    assert decision.state is GateState.UNKNOWN
    assert GateReason.PIT_UNPROVEN in decision.reason_codes


def test_economics_observability_does_not_calculate_ev() -> None:
    receipt = receipt_for(project([market(volume_fp="1000000.00", open_interest_fp="500000.00")]))
    decision = gate(receipt, ResearchabilityGate.G6_ECONOMICS_OBSERVABILITY)

    assert decision.state is GateState.UNKNOWN
    assert GateReason.ECONOMICS_OBSERVABILITY_UNPROVEN in decision.reason_codes
    assert not hasattr(receipt, "ev")
    assert not hasattr(receipt, "expected_value")


def test_no_numeric_readiness_score_exists() -> None:
    result = build_researchability_hard_gate_result(project([market()]))
    outputs = (result, *result.family_receipts)

    for output in outputs:
        fields = getattr(output, "__dataclass_fields__", {})
        assert "readiness_score" not in fields
        assert "score" not in fields


def test_no_ranking_exists() -> None:
    result = build_researchability_hard_gate_result(project([market()]))
    outputs = (result, *result.family_receipts)

    for output in outputs:
        fields = getattr(output, "__dataclass_fields__", {})
        assert {"rank", "ranking", "best_family", "capital_recommendation"}.isdisjoint(fields)


def test_no_lifecycle_promotion_exists() -> None:
    result = build_researchability_hard_gate_result(project([market()]))
    outputs = (result, *result.family_receipts)

    for output in outputs:
        fields = getattr(output, "__dataclass_fields__", {})
        assert {"promotion", "lifecycle_promotion", "trade_eligibility"}.isdisjoint(fields)
        assert output.research_only is True
        assert output.production_influence == Decimal("0")


def test_no_production_execution_account_credential_signer_risk_or_order_dependency() -> None:
    path = Path("services/market_universe/researchability_hard_gates.py")
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


def test_foreign_or_tampered_a2_2_receipt_is_rejected() -> None:
    a22 = project([market()])
    object.__setattr__(a22.mappings[0], "content_hash", "tampered")

    with pytest.raises(ResearchabilityHardGateError, match="foreign, tampered, or mismatched"):
        build_researchability_hard_gate_result(a22)


def test_exact_a2_2_a2_1_a1_binding_is_preserved() -> None:
    a22 = project([market()])
    before = (
        a22.result_id,
        a22.report.report_id,
        a22.a2_result.result_id,
        a22.a2_result.manifest.manifest_id,
        a22.a2_result.census.manifest.manifest_id,
        a22.a2_result.census.coverage_manifest.manifest_id,
        a22.a2_result.census.capture.capture_id,
    )
    result = build_researchability_hard_gate_result(a22)
    receipt = result.family_receipts[0]
    after = (
        a22.result_id,
        a22.report.report_id,
        a22.a2_result.result_id,
        a22.a2_result.manifest.manifest_id,
        a22.a2_result.census.manifest.manifest_id,
        a22.a2_result.census.coverage_manifest.manifest_id,
        a22.a2_result.census.capture.capture_id,
    )

    assert before == after
    assert result.a22_result is a22
    assert receipt.a22_result_id == a22.result_id
    assert receipt.a22_report_id == a22.report.report_id
    assert receipt.a22_mapping_ids == tuple(sorted(mapping.mapping_id for mapping in a22.mappings))
    assert receipt.a2_result_id == a22.a2_result.result_id
    assert receipt.a2_manifest_id == a22.a2_result.manifest.manifest_id
    assert receipt.census_manifest_id == a22.a2_result.census.manifest.manifest_id
    assert receipt.coverage_manifest_id == a22.a2_result.census.coverage_manifest.manifest_id
    assert receipt.capture_id == a22.a2_result.census.capture.capture_id


def test_replaced_exact_a2_2_object_binding_is_rejected() -> None:
    original = project([market()], response_sha256="b" * 64)
    replacement = project([market()], response_sha256="c" * 64)
    result = build_researchability_hard_gate_result(original)
    object.__setattr__(result, "a22_result", replacement)

    with pytest.raises(ResearchabilityHardGateError, match=r"exact A2.2 object binding"):
        result._validate_canonical_identity()


def test_unknown_unmapped_remains_fail_closed() -> None:
    a22 = project([market(rules_primary="The official source decides the outcome.")])
    assert a22.mappings[0].family is ResearchFamily.UNKNOWN_UNMAPPED
    receipt = receipt_for(a22, ResearchFamily.UNKNOWN_UNMAPPED)

    assert receipt.family is ResearchFamily.UNKNOWN_UNMAPPED
    assert all(decision.state is GateState.UNKNOWN for decision in receipt.gates[:6])
    assert all(
        GateReason.UNKNOWN_UNMAPPED in decision.reason_codes for decision in receipt.gates[:6]
    )
    assert gate(receipt, ResearchabilityGate.G7_AUTHORITY_ISOLATION).state is GateState.PASS
    assert not hasattr(receipt, "researchable")
