"""KU-A4 fail-closed modelability boundary and first non-weather recipe.

This module consumes only the exact canonical KU-A3.2 result.  It deliberately does not
accept model scores, tournament results, release rows, settlement rows, source hostnames,
or router classifications as authority.  The first non-weather recipe is a structural CPI
scheduled-release experiment definition; current canonical evidence is insufficient to run
that experiment or grant MODELABILITY PASS.

No network I/O, account access, credentials, signer, risk, execution, order, lifecycle,
economics, or production-promotion authority is present here.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from services.forecasting.calibration import CalibrationMethod
from services.forecasting.macro import ReleaseTarget
from services.production_weather_strategy.architecture import ModelRecipe

from .domain import stable_hash
from .empirical_researchability import (
    EmpiricalResearchabilityError,
    EvidenceDomain,
    EvidenceResolutionResult,
)
from .lifecycle import ZERO_INFLUENCE
from .researchability_hard_gates import (
    A31_EMPIRICAL_ARTIFACT_STATUS,
    GateState,
    ResearchabilityGate,
)

A4_POLICY_VERSION = "ku-a4-modelability-v1"
A4_RECIPE_SCHEMA_VERSION = "ku-a4-cpi-recipe-v1"
A4_RESULT_SCHEMA_VERSION = "ku-a4-modelability-result-v1"
_A4_ISSUANCE_CAPABILITY = object()


class ModelabilityError(ValueError):
    """KU-A4 could not prove a conservative canonical modelability result."""


class ModelabilityState(StrEnum):
    PASS = "PASS"  # noqa: S105 -- canonical modelability state, not a password
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class ModelabilityProofKind(StrEnum):
    STRUCTURAL = "STRUCTURAL"
    EMPIRICAL = "EMPIRICAL"


class ModelabilityRequirement(StrEnum):
    M1_EXACT_DOMAIN_BINDING = "M1_EXACT_DOMAIN_BINDING"
    M2_SETTLEMENT_LABEL_DEFINITION = "M2_SETTLEMENT_LABEL_DEFINITION"
    M3_PERMITTED_FEATURE_SOURCES = "M3_PERMITTED_FEATURE_SOURCES"
    M4_HISTORICAL_LABEL_AVAILABILITY = "M4_HISTORICAL_LABEL_AVAILABILITY"
    M5_POINT_IN_TIME_FEATURE_RECONSTRUCTION = "M5_POINT_IN_TIME_FEATURE_RECONSTRUCTION"
    M6_EVIDENCE_UNIT_POLICY = "M6_EVIDENCE_UNIT_POLICY"
    M7_REPRODUCIBLE_RECIPE = "M7_REPRODUCIBLE_RECIPE"
    M8_TEMPORAL_EVALUATION = "M8_TEMPORAL_EVALUATION"
    M9_BASELINE_COMPARATOR = "M9_BASELINE_COMPARATOR"
    M10_CALIBRATION_UNCERTAINTY_ABSTENTION = "M10_CALIBRATION_UNCERTAINTY_ABSTENTION"


MODELABILITY_REQUIREMENT_ORDER: tuple[ModelabilityRequirement, ...] = tuple(ModelabilityRequirement)


@dataclass(frozen=True, slots=True)
class RequirementAssessment:
    requirement: ModelabilityRequirement
    state: ModelabilityState
    evidence_provenance: tuple[str, ...]
    proof_kind: ModelabilityProofKind | None
    missing_evidence: tuple[str, ...]
    blocker_evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True, init=False)
class CpiReleaseRecipe:
    """Canonical structural definition of KU-A4's first non-weather experiment.

    The recipe is intentionally parameterized by a future exact A3 evidence-domain binding.
    It defines the experiment and its leakage controls; it does not claim that the required
    CPI series, source, labels, vintages, or settlement semantics are presently canonical.
    """

    candidate_id: str
    a32_result_id: str
    a32_receipt_ids: tuple[str, ...]
    evidence_domain: EvidenceDomain
    target: ReleaseTarget
    target_definition: str
    prediction_cutoff_rule: str
    feature_definitions: tuple[str, ...]
    feature_transformations: tuple[str, ...]
    feature_availability_rule: str
    label_definition: str
    label_availability_rule: str
    revision_finality_rule: str
    sample_unit: str
    split_policy: str
    training_period_policy: str
    validation_period_policy: str
    test_period_policy: str
    baseline_comparator: str
    model_recipe: ModelRecipe
    seed_policy: str
    calibration_method: CalibrationMethod
    calibration_policy: str
    abstention_policy: tuple[str, ...]
    evaluation_metrics: tuple[str, ...]
    recipe_version: str
    schema_version: str
    receipt_id: str
    content_hash: str
    research_only: bool
    production_influence: Decimal

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("CpiReleaseRecipe is canonical-A4-issued only")

    @classmethod
    def _issue(
        cls,
        *,
        capability: object,
        a32: EvidenceResolutionResult,
    ) -> CpiReleaseRecipe:
        if capability is not _A4_ISSUANCE_CAPABILITY:
            raise ModelabilityError("A4 recipe issuance capability is invalid")
        values = _canonical_recipe_values(a32)
        digest = stable_hash(_recipe_identity_material(values))
        self = object.__new__(cls)
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "schema_version", A4_RECIPE_SCHEMA_VERSION)
        object.__setattr__(self, "receipt_id", digest)
        object.__setattr__(self, "content_hash", digest)
        object.__setattr__(self, "research_only", True)
        object.__setattr__(self, "production_influence", ZERO_INFLUENCE)
        return self

    def _validate_canonical_identity(self, a32: EvidenceResolutionResult) -> None:
        if type(self) is not CpiReleaseRecipe:
            raise ModelabilityError("A4 recipe concrete type is not canonical")
        if self.schema_version != A4_RECIPE_SCHEMA_VERSION:
            raise ModelabilityError("A4 recipe schema identity is invalid")
        if self.research_only is not True or self.production_influence != ZERO_INFLUENCE:
            raise ModelabilityError("A4 recipe authority boundary is invalid")
        if (
            type(self.evidence_domain) is not EvidenceDomain
            or type(self.target) is not ReleaseTarget
            or type(self.calibration_method) is not CalibrationMethod
            or type(self.model_recipe) is not ModelRecipe
        ):
            raise ModelabilityError("A4 recipe semantics are not canonical issuer-derived")
        expected_values = _canonical_recipe_values(a32)
        for name, expected in expected_values.items():
            if getattr(self, name) != expected:
                raise ModelabilityError("A4 recipe semantics are not canonical issuer-derived")
        expected_hash = stable_hash(_recipe_identity_material(expected_values))
        if self.receipt_id != expected_hash or self.content_hash != expected_hash:
            raise ModelabilityError("A4 recipe content-addressed identity mismatch")


@dataclass(frozen=True, slots=True, init=False)
class ModelabilityResult:
    a32_result: EvidenceResolutionResult
    recipe: CpiReleaseRecipe
    requirements: tuple[RequirementAssessment, ...]
    modelability_state: ModelabilityState
    economics_observability_state: GateState
    empirical_execution_occurred: bool
    empirical_artifact_status: str
    schema_version: str
    result_id: str
    content_hash: str
    research_only: bool
    production_influence: Decimal
    _a32_object_identity_seal: tuple[object, ...]

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("ModelabilityResult is canonical-A4-issued only")

    @classmethod
    def _issue(
        cls,
        *,
        capability: object,
        a32: EvidenceResolutionResult,
        recipe: CpiReleaseRecipe,
        requirements: tuple[RequirementAssessment, ...],
    ) -> ModelabilityResult:
        if capability is not _A4_ISSUANCE_CAPABILITY:
            raise ModelabilityError("A4 result issuance capability is invalid")
        state = _overall_modelability_state(a32, requirements)
        economics_state = _economics_state(a32)
        material = _result_identity_material(
            a32=a32,
            recipe=recipe,
            requirements=requirements,
            state=state,
            economics_state=economics_state,
        )
        digest = stable_hash(material)
        self = object.__new__(cls)
        object.__setattr__(self, "a32_result", a32)
        object.__setattr__(self, "recipe", recipe)
        object.__setattr__(self, "requirements", requirements)
        object.__setattr__(self, "modelability_state", state)
        object.__setattr__(self, "economics_observability_state", economics_state)
        object.__setattr__(self, "empirical_execution_occurred", False)
        object.__setattr__(self, "empirical_artifact_status", A31_EMPIRICAL_ARTIFACT_STATUS)
        object.__setattr__(self, "schema_version", A4_RESULT_SCHEMA_VERSION)
        object.__setattr__(self, "result_id", digest)
        object.__setattr__(self, "content_hash", digest)
        object.__setattr__(self, "research_only", True)
        object.__setattr__(self, "production_influence", ZERO_INFLUENCE)
        object.__setattr__(self, "_a32_object_identity_seal", _a32_object_identity(a32))
        return self

    def _validate_canonical_identity(self) -> None:
        if type(self) is not ModelabilityResult:
            raise ModelabilityError("A4 result concrete type is not canonical")
        if self.schema_version != A4_RESULT_SCHEMA_VERSION:
            raise ModelabilityError("A4 result schema identity is invalid")
        if self.research_only is not True or self.production_influence != ZERO_INFLUENCE:
            raise ModelabilityError("A4 result authority boundary is invalid")
        if self.empirical_execution_occurred is not False:
            raise ModelabilityError("A4 v1 cannot claim empirical execution")
        if self.empirical_artifact_status != A31_EMPIRICAL_ARTIFACT_STATUS:
            raise ModelabilityError("A4 empirical-artifact posture is invalid")
        if self._a32_object_identity_seal != _a32_object_identity(self.a32_result):
            raise ModelabilityError("A4 exact A3.2 object binding is invalid")
        _validate_a32_result(self.a32_result)
        self.recipe._validate_canonical_identity(self.a32_result)
        expected_requirements = _requirement_assessments(self.a32_result, self.recipe)
        if self.requirements != expected_requirements:
            raise ModelabilityError("A4 requirement decisions are not canonical issuer-derived")
        _validate_requirement_assessments(self.a32_result, self.requirements)
        expected_state = _overall_modelability_state(self.a32_result, expected_requirements)
        expected_economics = _economics_state(self.a32_result)
        if self.modelability_state is not expected_state:
            raise ModelabilityError("A4 modelability state is not canonical")
        if self.economics_observability_state is not expected_economics:
            raise ModelabilityError("A4 economics observability posture is not canonical")
        expected_hash = stable_hash(
            _result_identity_material(
                a32=self.a32_result,
                recipe=self.recipe,
                requirements=expected_requirements,
                state=expected_state,
                economics_state=expected_economics,
            )
        )
        if self.result_id != expected_hash or self.content_hash != expected_hash:
            raise ModelabilityError("A4 result content-addressed identity mismatch")


def build_modelability_result(a32_result: EvidenceResolutionResult) -> ModelabilityResult:
    """Define the CPI recipe while preserving the exact fail-closed A3.2 posture."""

    _validate_a32_result(a32_result)
    recipe = CpiReleaseRecipe._issue(capability=_A4_ISSUANCE_CAPABILITY, a32=a32_result)
    requirements = _requirement_assessments(a32_result, recipe)
    _validate_requirement_assessments(a32_result, requirements)
    return ModelabilityResult._issue(
        capability=_A4_ISSUANCE_CAPABILITY,
        a32=a32_result,
        recipe=recipe,
        requirements=requirements,
    )


def _canonical_recipe_values(a32: EvidenceResolutionResult) -> dict[str, object]:
    receipt_ids = tuple(sorted(receipt.receipt_id for receipt in a32.domain_receipts))
    calibration_method = CalibrationMethod.IDENTITY
    model_recipe = ModelRecipe.build(
        recipe_id="ku-a4-cpi-transparent-initial-release-v1",
        algorithm="services.forecasting.macro.transparent_release_distribution",
        supported_domains=("SCHEDULED_MACRO_RELEASE_RESEARCH_CANDIDATE",),
        required_feature_groups=(
            "INITIAL_RELEASE_VINTAGES",
            "POINT_IN_TIME_RESIDUALS",
            "RELEASE_CALENDAR",
        ),
        calibration_method=calibration_method.value,
        supports_retraining=True,
        supports_ensemble_weighting=False,
    )
    return {
        "candidate_id": "CPI_INITIAL_RELEASE_TRANSPARENT",
        "a32_result_id": a32.result_id,
        "a32_receipt_ids": receipt_ids,
        "evidence_domain": EvidenceDomain.UNASSIGNED,
        "target": ReleaseTarget.CPI,
        "target_definition": (
            "Predict the initial published BLS CPI release value for the exact series and "
            "reference period later bound by canonical A3 evidence; revised/final substitutes "
            "are prohibited."
        ),
        "prediction_cutoff_rule": (
            "Prediction cutoff must be timezone-aware and strictly precede the scheduled "
            "release; every used feature must be independently proven available no later than "
            "that cutoff."
        ),
        "feature_definitions": (
            "exact release calendar for the bound CPI series/reference period",
            "latest three independently evidenced initial CPI vintages visible by cutoff",
            "point-in-time residual history computed only from prior initial releases",
        ),
        "feature_transformations": (
            "transparent recent-release center over the latest three eligible initial values",
            "empirical residual distribution over only prior eligible release events",
        ),
        "feature_availability_rule": (
            "feature_available_at <= prediction_cutoff; current/final snapshots, later "
            "revisions, post-cutoff corrections, future releases, later forecasts, and later "
            "market state are inadmissible"
        ),
        "label_definition": (
            "exact A3-bound Kalshi boolean settlement outcome for the CPI initial-release "
            "proposition and comparator; no physical-data proxy may replace contract truth"
        ),
        "label_availability_rule": (
            "labels enter training/evaluation only after canonical final settlement evidence "
            "is available and must never be exposed as a feature"
        ),
        "revision_finality_rule": (
            "initial CPI vintages require independently evidenced publication identity; "
            "settlement labels require correction/amendment/finality handling before use"
        ),
        "sample_unit": (
            "one scheduled CPI release event; sibling contracts are grouped to avoid false "
            "independent sample weight"
        ),
        "split_policy": (
            "release-publication-time walk-forward contiguous train/validation/test partitions; "
            "no random split and no TEST information in fit, calibration selection, "
            "hyperparameter selection, or abstention thresholds"
        ),
        "training_period_policy": "earliest contiguous eligible release events",
        "validation_period_policy": "subsequent contiguous eligible release events",
        "test_period_policy": (
            "latest contiguous eligible release events revealed once after selection"
        ),
        "baseline_comparator": (
            "unconditional prior-event base rate computed only from finalized labels available "
            "before each prediction cutoff"
        ),
        "model_recipe": model_recipe,
        "seed_policy": "DETERMINISTIC_NO_RANDOM_SPLIT_NO_STOCHASTIC_FIT",
        "calibration_method": calibration_method,
        "calibration_policy": (
            "chronological calibration diagnostics using canonical CalibrationMethod identity; "
            "any fitted calibrator may use only pre-test settled events"
        ),
        "abstention_policy": (
            "abstain if exact A3 evidence-domain/settlement binding is unavailable",
            "abstain if feature-source permission is unproven",
            "abstain if publication/replay availability is not independently evidenced",
            "abstain if any feature is available after the prediction cutoff",
            "abstain on revised/final-vintage substitution for an initial-release feature",
            "abstain if settlement finality/correction semantics are unresolved",
            "abstain if fewer than 12 eligible prior initial releases exist",
            "abstain on series, unit, reference-period, or comparator mismatch",
        ),
        "evaluation_metrics": (
            "BRIER_SCORE",
            "LOG_LOSS",
            "CALIBRATION_DIAGNOSTICS",
            "ABSTENTION_COVERAGE",
            "TIME_SPLIT_PERFORMANCE",
        ),
        "recipe_version": "ku-a4-cpi-transparent-initial-release-v1",
    }


def _recipe_identity_material(values: dict[str, object]) -> tuple[object, ...]:
    model_recipe = values["model_recipe"]
    if not isinstance(model_recipe, ModelRecipe):
        raise ModelabilityError("A4 model recipe type is invalid")
    calibration_method = values["calibration_method"]
    if type(calibration_method) is not CalibrationMethod:
        raise ModelabilityError("A4 calibration identity is not canonical")
    target = values["target"]
    if type(target) is not ReleaseTarget:
        raise ModelabilityError("A4 release target identity is not canonical")
    evidence_domain = values["evidence_domain"]
    if type(evidence_domain) is not EvidenceDomain:
        raise ModelabilityError("A4 evidence domain identity is not canonical")
    return (
        A4_RECIPE_SCHEMA_VERSION,
        A4_POLICY_VERSION,
        values["candidate_id"],
        values["a32_result_id"],
        values["a32_receipt_ids"],
        evidence_domain.value,
        target.value,
        values["target_definition"],
        values["prediction_cutoff_rule"],
        values["feature_definitions"],
        values["feature_transformations"],
        values["feature_availability_rule"],
        values["label_definition"],
        values["label_availability_rule"],
        values["revision_finality_rule"],
        values["sample_unit"],
        values["split_policy"],
        values["training_period_policy"],
        values["validation_period_policy"],
        values["test_period_policy"],
        values["baseline_comparator"],
        model_recipe.recipe_id,
        model_recipe.content_hash,
        values["seed_policy"],
        calibration_method.value,
        values["calibration_policy"],
        values["abstention_policy"],
        values["evaluation_metrics"],
        values["recipe_version"],
        "RESEARCH_ONLY_NO_EMPIRICAL_EXECUTION_OR_ECONOMIC_AUTHORITY",
        "0",
    )


def _requirement_assessments(
    a32: EvidenceResolutionResult,
    recipe: CpiReleaseRecipe,
) -> tuple[RequirementAssessment, ...]:
    base = (
        f"A3.2_RESULT:{a32.result_id}",
        *(f"A3.2_RECEIPT:{receipt_id}" for receipt_id in recipe.a32_receipt_ids),
        f"A4_RECIPE:{recipe.receipt_id}",
    )
    return (
        RequirementAssessment(
            ModelabilityRequirement.M1_EXACT_DOMAIN_BINDING,
            ModelabilityState.UNKNOWN,
            (*base, "EVIDENCE_DOMAIN:UNASSIGNED"),
            None,
            ("MISSING:EXACT_A3.2_CPI_EVIDENCE_DOMAIN_BINDING",),
            (),
        ),
        RequirementAssessment(
            ModelabilityRequirement.M2_SETTLEMENT_LABEL_DEFINITION,
            ModelabilityState.UNKNOWN,
            (*base, "A3.2_GATE:G1_SETTLEMENT_PROOF=UNKNOWN"),
            None,
            (
                "MISSING:EXACT_SETTLEMENT_TARGET_DOMAIN_BINDING",
                "MISSING:SETTLEMENT_CORRECTION_FINALITY_BINDING",
            ),
            (),
        ),
        RequirementAssessment(
            ModelabilityRequirement.M3_PERMITTED_FEATURE_SOURCES,
            ModelabilityState.UNKNOWN,
            (*base, "A3.2_GATE:G2_PERMITTED_SOURCE=UNKNOWN"),
            None,
            ("MISSING:EXPLICIT_DOMAIN_SOURCE_PERMISSION",),
            (),
        ),
        RequirementAssessment(
            ModelabilityRequirement.M4_HISTORICAL_LABEL_AVAILABILITY,
            ModelabilityState.UNKNOWN,
            (*base, "A3.2_GATE:G3_HISTORICAL_TRUTH=UNKNOWN"),
            None,
            ("MISSING:REPOSITORY_CANONICAL_HISTORICAL_SETTLEMENT_TRUTH",),
            (),
        ),
        RequirementAssessment(
            ModelabilityRequirement.M5_POINT_IN_TIME_FEATURE_RECONSTRUCTION,
            ModelabilityState.UNKNOWN,
            (*base, "A3.2_GATE:G4_POINT_IN_TIME_RECONSTRUCTION=UNKNOWN"),
            None,
            (
                "MISSING:REPOSITORY_CANONICAL_POINT_IN_TIME_VINTAGES",
                "MISSING:INDEPENDENT_RELEASE_PUBLICATION_AVAILABILITY_PROOF",
            ),
            (),
        ),
        RequirementAssessment(
            ModelabilityRequirement.M6_EVIDENCE_UNIT_POLICY,
            ModelabilityState.UNKNOWN,
            (*base, "A3.2_GATE:G5_EVIDENCE_UNIT_POLICY=UNKNOWN"),
            None,
            ("MISSING:REVIEWED_DOMAIN_EVIDENCE_UNIT_POLICY",),
            (),
        ),
        RequirementAssessment(
            ModelabilityRequirement.M7_REPRODUCIBLE_RECIPE,
            ModelabilityState.PASS,
            (*base, "A4_STRUCTURAL:CONTENT_ADDRESSED_RECIPE"),
            ModelabilityProofKind.STRUCTURAL,
            (),
            (),
        ),
        RequirementAssessment(
            ModelabilityRequirement.M8_TEMPORAL_EVALUATION,
            ModelabilityState.PASS,
            (*base, "A4_STRUCTURAL:RELEASE_TIME_WALK_FORWARD_NO_RANDOM_SPLIT"),
            ModelabilityProofKind.STRUCTURAL,
            (),
            (),
        ),
        RequirementAssessment(
            ModelabilityRequirement.M9_BASELINE_COMPARATOR,
            ModelabilityState.PASS,
            (*base, "A4_STRUCTURAL:PRE_CUTOFF_BASE_RATE_BASELINE"),
            ModelabilityProofKind.STRUCTURAL,
            (),
            (),
        ),
        RequirementAssessment(
            ModelabilityRequirement.M10_CALIBRATION_UNCERTAINTY_ABSTENTION,
            ModelabilityState.PASS,
            (
                *base,
                f"A4_STRUCTURAL:CALIBRATION_METHOD={recipe.calibration_method.value}",
                "A4_STRUCTURAL:EXPLICIT_ABSTENTION_POLICY",
            ),
            ModelabilityProofKind.STRUCTURAL,
            (),
            (),
        ),
    )


def _validate_requirement_assessments(
    a32: EvidenceResolutionResult,
    requirements: tuple[RequirementAssessment, ...],
) -> None:
    if tuple(item.requirement for item in requirements) != MODELABILITY_REQUIREMENT_ORDER:
        raise ModelabilityError("A4 result must contain exactly M1-M10 once each")
    for item in requirements:
        if type(item.requirement) is not ModelabilityRequirement:
            raise ModelabilityError("A4 requirement tag type is not canonical")
        if type(item.state) is not ModelabilityState:
            raise ModelabilityError("A4 modelability state type is not canonical")
        if item.proof_kind is not None and type(item.proof_kind) is not ModelabilityProofKind:
            raise ModelabilityError("A4 proof-kind type is not canonical")
        if item.state is ModelabilityState.BLOCKED and not item.blocker_evidence:
            raise ModelabilityError("A4 BLOCKED requires positive canonical blocker evidence")
        if item.state is ModelabilityState.PASS and item.proof_kind is None:
            raise ModelabilityError("A4 PASS requires positive canonical proof")
        if item.state is ModelabilityState.UNKNOWN and item.blocker_evidence:
            raise ModelabilityError("A4 UNKNOWN cannot carry blocker evidence")
    if (
        any(receipt.evidence_domain is EvidenceDomain.UNASSIGNED for receipt in a32.domain_receipts)
        and requirements[0].state is not ModelabilityState.UNKNOWN
    ):
        raise ModelabilityError("A3.2 UNASSIGNED cannot receive modelability PASS")


def _overall_modelability_state(
    a32: EvidenceResolutionResult,
    requirements: tuple[RequirementAssessment, ...],
) -> ModelabilityState:
    if any(item.state is ModelabilityState.BLOCKED for item in requirements):
        return ModelabilityState.BLOCKED
    required_empirical = requirements[:6]
    if any(item.state is not ModelabilityState.PASS for item in required_empirical):
        return ModelabilityState.UNKNOWN
    if any(receipt.evidence_domain is EvidenceDomain.UNASSIGNED for receipt in a32.domain_receipts):
        return ModelabilityState.UNKNOWN
    if any(item.state is not ModelabilityState.PASS for item in requirements):
        return ModelabilityState.UNKNOWN
    return ModelabilityState.PASS


def _economics_state(a32: EvidenceResolutionResult) -> GateState:
    states = {
        next(
            resolution.resolved_state
            for resolution in receipt.gates
            if resolution.gate is ResearchabilityGate.G6_ECONOMICS_OBSERVABILITY
        )
        for receipt in a32.domain_receipts
    }
    if states == {GateState.PASS}:
        return GateState.PASS
    if states == {GateState.BLOCKED}:
        return GateState.BLOCKED
    return GateState.UNKNOWN


def _result_identity_material(
    *,
    a32: EvidenceResolutionResult,
    recipe: CpiReleaseRecipe,
    requirements: tuple[RequirementAssessment, ...],
    state: ModelabilityState,
    economics_state: GateState,
) -> tuple[object, ...]:
    return (
        A4_RESULT_SCHEMA_VERSION,
        A4_POLICY_VERSION,
        A31_EMPIRICAL_ARTIFACT_STATUS,
        a32.result_id,
        tuple(receipt.receipt_id for receipt in a32.domain_receipts),
        recipe.receipt_id,
        tuple(
            (
                item.requirement.value,
                item.state.value,
                item.evidence_provenance,
                item.proof_kind.value if item.proof_kind is not None else None,
                item.missing_evidence,
                item.blocker_evidence,
            )
            for item in requirements
        ),
        state.value,
        economics_state.value,
        False,
        "RESEARCH_ONLY_NO_EV_PROFITABILITY_LIFECYCLE_OR_EXECUTION_AUTHORITY",
        "0",
    )


def _validate_a32_result(a32: EvidenceResolutionResult) -> None:
    if type(a32) is not EvidenceResolutionResult:
        raise ModelabilityError("A3.2 result concrete type is not canonical")
    try:
        a32._validate_canonical_identity()
    except EmpiricalResearchabilityError as exc:
        raise ModelabilityError("A3.2 evidence is foreign, tampered, or mismatched") from exc


def _a32_object_identity(a32: EvidenceResolutionResult) -> tuple[object, ...]:
    return (
        a32,
        a32.a31_result,
        a32.a31_result.a22_result,
        a32.a31_result.a22_result.a2_result,
        a32.a31_result.a22_result.a2_result.census,
        a32.a31_result.a22_result.a2_result.census.capture,
        *a32.domain_receipts,
        *a32.a31_result.family_receipts,
        *a32.a31_result.a22_result.mappings,
    )
