from __future__ import annotations

import ast
import inspect
from dataclasses import fields, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

import pytest

import services.market_universe.modelability as modelability_module
from services.forecasting.calibration import CalibrationMethod
from services.forecasting.macro import ReleaseTarget, ReleaseVintage
from services.market_universe.domain import stable_hash
from services.market_universe.empirical_researchability import (
    EvidenceDomain,
    EvidenceResolutionResult,
    build_evidence_resolution_result,
)
from services.market_universe.modelability import (
    CpiReleaseRecipe,
    ModelabilityError,
    ModelabilityProofKind,
    ModelabilityRequirement,
    ModelabilityResult,
    ModelabilityState,
    RequirementAssessment,
    _A4_ISSUANCE_CAPABILITY,
    _canonical_recipe_values,
    _recipe_identity_material,
    _result_identity_material,
    _validate_requirement_assessments,
    build_modelability_result,
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
from services.production_weather_strategy.architecture import (
    MarketFamilySpec,
    ModelRecipe,
    SourceCapability,
    SourceRole,
    StrategyRegistry,
)
from services.production_weather_strategy.contracts import SettlementLabel

CAPTURED_AT = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def series(
    *,
    title: str = "CPI research candidate",
    category: str = "Economics",
    source_name: str = "Exchange-named source",
    source_url: str = "https://example.invalid/source",
) -> dict[str, object]:
    return {
        "ticker": "KXSERIES",
        "title": title,
        "category": category,
        "frequency": "monthly",
        "settlement_sources": [{"name": source_name, "url": source_url}],
    }


def event(
    *, title: str = "CPI release event", category: str = "Economics"
) -> dict[str, object]:
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
        "title": "CPI threshold",
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


def make_a32(
    markets: list[dict[str, object]] | None = None,
    *,
    event_row: dict[str, object] | None = None,
    series_row: dict[str, object] | None = None,
    request_locator: str = "fixture://ku-a4-cpi",
) -> EvidenceResolutionResult:
    a2 = SemanticSourceCoverageProjector().project(
        market_rows=[market()] if markets is None else markets,
        event_rows=[event()] if event_row is None else [event_row],
        series_rows=[series()] if series_row is None else [series_row],
        source_authority="captured-public-kalshi",
        request_locator=request_locator,
        response_sha256="a" * 64,
        captured_at=CAPTURED_AT,
    )
    a22 = build_research_family_offline_report(a2)
    a31 = build_researchability_hard_gate_result(a22)
    return build_evidence_resolution_result(a31)


def requirement(
    result: ModelabilityResult, item: ModelabilityRequirement
) -> RequirementAssessment:
    return next(value for value in result.requirements if value.requirement is item)


def a32_gate(
    result: EvidenceResolutionResult, gate: ResearchabilityGate
) -> tuple[GateState, ...]:
    return tuple(
        next(value.resolved_state for value in receipt.gates if value.gate is gate)
        for receipt in result.domain_receipts
    )


def rehash_result(result: ModelabilityResult) -> None:
    digest = stable_hash(
        _result_identity_material(
            a32=result.a32_result,
            recipe=result.recipe,
            requirements=result.requirements,
            state=result.modelability_state,
            economics_state=result.economics_observability_state,
        )
    )
    object.__setattr__(result, "result_id", digest)
    object.__setattr__(result, "content_hash", digest)


def test_a32_is_validated_before_a4_consumes_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    source = inspect.getsource(build_modelability_result)
    assert source.index("_validate_a32_result") < source.index("CpiReleaseRecipe._issue")

    calls = 0
    original = EvidenceResolutionResult._validate_canonical_identity

    def validating_spy(self: EvidenceResolutionResult) -> None:
        nonlocal calls
        calls += 1
        original(self)

    monkeypatch.setattr(EvidenceResolutionResult, "_validate_canonical_identity", validating_spy)
    build_modelability_result(make_a32())
    assert calls >= 1


def test_foreign_and_tampered_a32_are_rejected() -> None:
    with pytest.raises(ModelabilityError, match="concrete type"):
        build_modelability_result(cast(Any, object()))

    a32 = make_a32()
    object.__setattr__(a32.domain_receipts[0], "market_ticker", "FORGED")
    with pytest.raises(ModelabilityError, match="foreign, tampered, or mismatched"):
        build_modelability_result(a32)


def test_unassigned_and_g1_to_g5_force_m1_to_m6_unknown() -> None:
    a32 = make_a32()
    result = build_modelability_result(a32)

    assert all(
        receipt.evidence_domain is EvidenceDomain.UNASSIGNED for receipt in a32.domain_receipts
    )
    assert (
        requirement(result, ModelabilityRequirement.M1_EXACT_DOMAIN_BINDING).state
        is ModelabilityState.UNKNOWN
    )
    gate_map = (
        (
            ResearchabilityGate.G1_SETTLEMENT_PROOF,
            ModelabilityRequirement.M2_SETTLEMENT_LABEL_DEFINITION,
        ),
        (
            ResearchabilityGate.G2_PERMITTED_SOURCE,
            ModelabilityRequirement.M3_PERMITTED_FEATURE_SOURCES,
        ),
        (
            ResearchabilityGate.G3_HISTORICAL_TRUTH,
            ModelabilityRequirement.M4_HISTORICAL_LABEL_AVAILABILITY,
        ),
        (
            ResearchabilityGate.G4_POINT_IN_TIME_RECONSTRUCTION,
            ModelabilityRequirement.M5_POINT_IN_TIME_FEATURE_RECONSTRUCTION,
        ),
        (
            ResearchabilityGate.G5_EVIDENCE_UNIT_POLICY,
            ModelabilityRequirement.M6_EVIDENCE_UNIT_POLICY,
        ),
    )
    for gate, item in gate_map:
        assert a32_gate(a32, gate) == (GateState.UNKNOWN,)
        assert requirement(result, item).state is ModelabilityState.UNKNOWN
    assert result.modelability_state is ModelabilityState.UNKNOWN


def test_missing_evidence_stays_unknown_and_blocked_needs_positive_evidence() -> None:
    result = build_modelability_result(make_a32())
    for item in result.requirements[:6]:
        assert item.state is ModelabilityState.UNKNOWN
        assert item.missing_evidence
        assert item.blocker_evidence == ()

    changed = list(result.requirements)
    changed[0] = replace(changed[0], state=ModelabilityState.BLOCKED)
    with pytest.raises(ModelabilityError, match="BLOCKED requires positive"):
        _validate_requirement_assessments(result.a32_result, tuple(changed))


def test_structural_m7_to_m10_passes_never_promote_overall_modelability() -> None:
    result = build_modelability_result(make_a32())
    for item in result.requirements[6:]:
        assert item.state is ModelabilityState.PASS
        assert item.proof_kind is ModelabilityProofKind.STRUCTURAL
    assert result.modelability_state is ModelabilityState.UNKNOWN
    assert result.empirical_execution_occurred is False


def test_g6_is_inherited_unknown_and_a4_cannot_rewrite_it() -> None:
    result = build_modelability_result(make_a32())
    assert a32_gate(
        result.a32_result, ResearchabilityGate.G6_ECONOMICS_OBSERVABILITY
    ) == (GateState.UNKNOWN,)
    assert result.economics_observability_state is GateState.UNKNOWN

    object.__setattr__(result, "economics_observability_state", GateState.PASS)
    rehash_result(result)
    with pytest.raises(ModelabilityError, match="economics observability posture"):
        result._validate_canonical_identity()


def test_recipe_and_canonical_model_recipe_exist_but_modelability_remains_unknown() -> None:
    result = build_modelability_result(make_a32())
    assert isinstance(result.recipe.model_recipe, ModelRecipe)
    assert result.recipe.receipt_id
    assert result.modelability_state is ModelabilityState.UNKNOWN


def test_valid_strategy_registry_is_structural_only_not_modelability_authority() -> None:
    result = build_modelability_result(make_a32())
    domain = "SCHEDULED_MACRO_RELEASE_RESEARCH_CANDIDATE"
    source = SourceCapability.build(
        source_id="ku-a4-structural-source",
        roles=(SourceRole.SETTLEMENT, SourceRole.STRUCTURED_DATA),
        domains=(domain,),
        authority="STRUCTURAL_CONFIGURATION_ONLY",
        maximum_age_seconds=3600,
        production_allowed=True,
    )
    family = MarketFamilySpec.build(
        family_id="ku-a4-cpi-structural-family",
        domain=domain,
        selector="CPI",
        settlement_mapping_id="UNPROVEN",
        source_ids=(source.source_id,),
        feature_groups=result.recipe.model_recipe.required_feature_groups,
        model_recipe_ids=(result.recipe.model_recipe.recipe_id,),
        enabled=True,
    )
    registry = StrategyRegistry.build(
        sources=(source,),
        model_recipes=(result.recipe.model_recipe,),
        market_families=(family,),
    )

    assert registry.registry_id
    assert tuple(inspect.signature(build_modelability_result).parameters) == ("a32_result",)
    assert result.modelability_state is ModelabilityState.UNKNOWN


def test_tournament_scorecard_fields_have_no_a4_authority_channel() -> None:
    class ForgedTournamentAuthority:
        test_edge_classification = "BEATS_MARKET_ON_UNTOUCHED_TEST"
        promotion_authority = "FULL"
        hypothetical_total_pnl = Decimal("999999")

    with pytest.raises(ModelabilityError, match="concrete type"):
        build_modelability_result(cast(Any, ForgedTournamentAuthority()))

    source = inspect.getsource(modelability_module)
    tree = ast.parse(source)
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert not any("model_tournament" in module for module in imported)


def test_release_fixture_model_card_and_settlement_rows_have_no_authority_channel() -> None:
    release = ReleaseVintage(
        vintage_id="fixture",
        target=ReleaseTarget.CPI,
        series_id="CPI-U",
        reference_period="2026-07",
        scheduled_at=CAPTURED_AT,
        published_at=CAPTURED_AT,
        replay_available_at=CAPTURED_AT,
        value=Decimal("3.0"),
        unit="percent",
        revision_number=0,
        revises_vintage_id=None,
        source="fixture",
    )
    settlement = SettlementLabel.build(
        event_id="event",
        market_ticker="ticker",
        resolved_outcome=True,
        resolved_at=CAPTURED_AT,
        settlement_evidence_id="fixture-settlement",
    )
    for foreign in (release, settlement, {"model_card": "M8_cpi_transparent"}):
        with pytest.raises(ModelabilityError, match="concrete type"):
            build_modelability_result(cast(Any, foreign))


def test_titles_categories_hostnames_source_names_and_routing_do_not_create_modelability() -> None:
    a32 = make_a32(
        [market(strike_type="greater", floor_strike="10")],
        event_row=event(title="Official CPI BLS release", category="Economics"),
        series_row=series(
            title="Official CPI series",
            category="Economics",
            source_name="Bureau of Labor Statistics",
            source_url="https://www.bls.gov/cpi/",
        ),
    )
    result = build_modelability_result(a32)
    assert all(
        receipt.evidence_domain is EvidenceDomain.UNASSIGNED for receipt in a32.domain_receipts
    )
    assert result.modelability_state is ModelabilityState.UNKNOWN


def test_broad_a22_family_membership_does_not_create_modelability() -> None:
    a32 = make_a32([market("KXEVENT-10"), market("KXEVENT-11")])
    assert len(a32.domain_receipts) == 2
    assert {receipt.family for receipt in a32.domain_receipts} == {
        ResearchFamily.BINARY_THRESHOLD
    }
    assert build_modelability_result(a32).modelability_state is ModelabilityState.UNKNOWN


def test_weather_specific_signals_cannot_create_cpi_modelability() -> None:
    a32 = make_a32(
        event_row=event(title="Chicago daily high weather", category="Weather"),
        series_row=series(
            title="Weather threshold",
            category="Weather",
            source_name="The Weather Company",
            source_url="https://weather.com/official-looking",
        ),
    )
    result = build_modelability_result(a32)
    assert result.recipe.target is ReleaseTarget.CPI
    assert result.recipe.evidence_domain is EvidenceDomain.UNASSIGNED
    assert result.modelability_state is ModelabilityState.UNKNOWN


def test_fixture_backed_cpi_examples_cannot_create_empirical_pass() -> None:
    result = build_modelability_result(make_a32(request_locator="fixture://m8-cpi-transparent"))
    assert result.empirical_artifact_status == A31_EMPIRICAL_ARTIFACT_STATUS
    assert result.empirical_execution_occurred is False
    assert result.modelability_state is ModelabilityState.UNKNOWN
    assert (
        requirement(result, ModelabilityRequirement.M4_HISTORICAL_LABEL_AVAILABILITY).state
        is ModelabilityState.UNKNOWN
    )
    assert (
        requirement(
            result, ModelabilityRequirement.M5_POINT_IN_TIME_FEATURE_RECONSTRUCTION
        ).state
        is ModelabilityState.UNKNOWN
    )


def test_recipe_target_is_exact_cpi_release_target_and_type_is_enforced() -> None:
    result = build_modelability_result(make_a32())
    assert result.recipe.target is ReleaseTarget.CPI
    assert type(result.recipe.target) is ReleaseTarget

    object.__setattr__(result.recipe, "target", "CPI")
    with pytest.raises(ModelabilityError, match="semantics are not canonical"):
        result._validate_canonical_identity()


def test_recipe_has_explicit_cutoff_feature_revision_and_finality_rules() -> None:
    recipe = build_modelability_result(make_a32()).recipe
    assert recipe.prediction_cutoff_rule
    assert "feature_available_at <= prediction_cutoff" in recipe.feature_availability_rule
    assert "revised/final" in recipe.target_definition
    assert "revised/final-vintage substitution" in " ".join(recipe.abstention_policy)
    assert "correction/amendment/finality" in recipe.revision_finality_rule


def test_temporal_policy_is_release_time_contiguous_nonrandom_and_test_isolated() -> None:
    recipe = build_modelability_result(make_a32()).recipe
    policy = recipe.split_policy
    assert "release-publication-time walk-forward contiguous" in policy
    assert "no random split" in policy
    assert "no TEST information in fit" in policy
    assert "calibration selection" in policy
    assert "hyperparameter selection" in policy
    assert "abstention thresholds" in policy
    assert "revealed once after selection" in recipe.test_period_policy


def test_baseline_calibration_and_abstention_are_explicit_and_pre_cutoff() -> None:
    recipe = build_modelability_result(make_a32()).recipe
    assert "prior-event base rate" in recipe.baseline_comparator
    assert "before each prediction cutoff" in recipe.baseline_comparator
    assert recipe.calibration_method is CalibrationMethod.IDENTITY
    assert type(recipe.calibration_method) is CalibrationMethod
    assert recipe.model_recipe.calibration_method == CalibrationMethod.IDENTITY.value
    assert recipe.abstention_policy
    assert "fewer than 12 eligible prior initial releases" in " ".join(recipe.abstention_policy)


def test_identical_inputs_are_deterministic() -> None:
    first = build_modelability_result(make_a32())
    second = build_modelability_result(make_a32())
    assert first.recipe.receipt_id == second.recipe.receipt_id
    assert first.recipe.content_hash == second.recipe.content_hash
    assert first.result_id == second.result_id
    assert first.content_hash == second.content_hash


def test_semantically_relevant_recipe_difference_changes_identity() -> None:
    a32 = make_a32()
    values = _canonical_recipe_values(a32)
    original = stable_hash(_recipe_identity_material(values))
    changed = dict(values)
    changed["prediction_cutoff_rule"] = f"{changed['prediction_cutoff_rule']} DIFFERENT"
    assert stable_hash(_recipe_identity_material(changed)) != original


def test_recipe_and_result_cannot_be_directly_publicly_constructed() -> None:
    with pytest.raises(TypeError, match="canonical-A4-issued"):
        CpiReleaseRecipe()
    with pytest.raises(TypeError, match="canonical-A4-issued"):
        ModelabilityResult()


def test_private_capability_can_only_issue_canonical_recipe_semantics() -> None:
    a32 = make_a32()
    recipe = CpiReleaseRecipe._issue(capability=_A4_ISSUANCE_CAPABILITY, a32=a32)
    canonical = build_modelability_result(a32).recipe
    assert recipe == canonical
    recipe._validate_canonical_identity(a32)


def test_mutate_and_rehash_recipe_semantics_is_rejected() -> None:
    result = build_modelability_result(make_a32())
    values = _canonical_recipe_values(result.a32_result)
    forged_definition = f"{result.recipe.label_definition} FORGED"
    values["label_definition"] = forged_definition
    object.__setattr__(result.recipe, "label_definition", forged_definition)
    forged_hash = stable_hash(_recipe_identity_material(values))
    object.__setattr__(result.recipe, "receipt_id", forged_hash)
    object.__setattr__(result.recipe, "content_hash", forged_hash)
    rehash_result(result)

    with pytest.raises(ModelabilityError, match="semantics are not canonical"):
        result._validate_canonical_identity()


@pytest.mark.parametrize("field_name", ["state", "evidence_provenance", "missing_evidence"])
def test_mutate_and_rehash_requirement_semantics_is_rejected(field_name: str) -> None:
    result = build_modelability_result(make_a32())
    changed = list(result.requirements)
    original = changed[0]
    if field_name == "state":
        changed[0] = replace(
            original,
            state=ModelabilityState.PASS,
            proof_kind=ModelabilityProofKind.STRUCTURAL,
            missing_evidence=(),
        )
    elif field_name == "evidence_provenance":
        changed[0] = replace(
            original, evidence_provenance=(*original.evidence_provenance, "FORGED")
        )
    else:
        changed[0] = replace(original, missing_evidence=("MISSING:FORGED",))
    object.__setattr__(result, "requirements", tuple(changed))
    rehash_result(result)

    with pytest.raises(ModelabilityError, match="requirement decisions"):
        result._validate_canonical_identity()


def test_mutate_and_rehash_overall_modelability_state_is_rejected() -> None:
    result = build_modelability_result(make_a32())
    object.__setattr__(result, "modelability_state", ModelabilityState.PASS)
    rehash_result(result)
    with pytest.raises(ModelabilityError, match="modelability state is not canonical"):
        result._validate_canonical_identity()


def test_altered_empirical_execution_is_rejected() -> None:
    result = build_modelability_result(make_a32())
    object.__setattr__(result, "empirical_execution_occurred", True)
    with pytest.raises(ModelabilityError, match="cannot claim empirical execution"):
        result._validate_canonical_identity()


@pytest.mark.parametrize(
    ("field_name", "value"),
    [("research_only", False), ("production_influence", Decimal("1"))],
)
def test_altered_authority_boundary_is_rejected(field_name: str, value: object) -> None:
    result = build_modelability_result(make_a32())
    object.__setattr__(result, field_name, value)
    with pytest.raises(ModelabilityError, match="authority boundary"):
        result._validate_canonical_identity()


def test_a4_exposes_no_readiness_ranking_ev_profit_capital_or_promotion_fields() -> None:
    names = {field.name for field in fields(CpiReleaseRecipe)} | {
        field.name for field in fields(ModelabilityResult)
    }
    forbidden = {
        "readiness_score",
        "family_ranking",
        "expected_value",
        "ev",
        "profitability",
        "profit",
        "capital_allocation",
        "position_size",
        "promotion_authority",
        "deployment_tier",
    }
    assert names.isdisjoint(forbidden)


def test_a4_has_no_execution_account_credential_signer_risk_or_order_dependency() -> None:
    source = inspect.getsource(modelability_module)
    tree = ast.parse(source)
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    forbidden_fragments = (
        "model_tournament",
        "climate_evidence",
        "settlement_dataset",
        "forecast_vintage",
        "execution",
        "account",
        "credential",
        "signer",
        "risk",
        "order",
    )
    assert not any(
        fragment in module
        for module in imported
        for fragment in forbidden_fragments
    )
