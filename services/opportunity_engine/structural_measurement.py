"""M27B.2 continuous structural-lead measurement.

This module adds a repeated-scan measurement layer on top of the canonical, accepted M27B /
M27B.1 sibling-threshold scanner (:mod:`services.opportunity_engine.structural`). It does not
reimplement discovery or exact confirmation -- it consumes :class:`StructuralLead` and
:class:`StructuralConfirmation` objects produced by that module and adds only:

* a scan-invariant :func:`relationship_id` so the same sibling-threshold relationship can be
  tracked across many scans even though the canonical, quote-content-addressed
  ``StructuralLead.lead_id`` changes on every scan by design;
* one immutable :class:`LeadObservation` per scan/relationship, built through exactly one of six
  narrow, validated constructor functions (never a general-purpose free-form constructor);
* pure lifetime aggregation (:func:`compute_lifetime`) and run-level summarization
  (:func:`summarize_run`) that keep lead frequency, lead persistence, and after-cost
  executability as three separate reported dimensions, never blended into one score.

Every object here is permanently ``research_only=True`` and ``production_influence=0``. No
component in this module estimates a final or guaranteed net profit -- canonical
:func:`services.opportunity_engine.structural.confirm_structural_lead` already fixes
``final_net_profit``/``guaranteed_net_profit`` to ``None`` forever, and this module never
manufactures a substitute. ``AFTER_COST_POSITIVE_RESEARCH`` is always and only a labeled research
estimate of ``formula_adjusted_structural_gap`` being positive, never a profitability or
executability claim.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, fields
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from services.market_universe.domain import stable_hash

from .domain import OpportunityError
from .structural import (
    POLICY_VERSION,
    ConfirmationState,
    RelationshipType,
    StructuralConfirmation,
    StructuralLead,
)

ZERO = Decimal("0")
MEASUREMENT_POLICY_VERSION = "m27b2-structural-measurement-v1"

_ALIVE_STATES_REQUIRE_REASON = frozenset({"DISCOVERY_ONLY", "STALE", "DISAPPEARED", "AMBIGUOUS"})


class MeasurementState(StrEnum):
    """The eight economics states this measurement layer distinguishes.

    No state, on its own, ever authorizes a trade. Only ``EXACT_CONFIRMED`` and
    ``AFTER_COST_POSITIVE_RESEARCH`` derive from a canonical
    :class:`~services.opportunity_engine.structural.StructuralConfirmation`; the remaining six
    describe why that canonical confirmation was not reached, is no longer current, or has become
    unreliable.
    """

    DISCOVERY_ONLY = "DISCOVERY_ONLY"
    EXACT_CONFIRMED = "EXACT_CONFIRMED"
    INSUFFICIENT_DEPTH = "INSUFFICIENT_DEPTH"
    FEE_UNKNOWN = "FEE_UNKNOWN"
    AFTER_COST_POSITIVE_RESEARCH = "AFTER_COST_POSITIVE_RESEARCH"
    STALE = "STALE"
    DISAPPEARED = "DISAPPEARED"
    AMBIGUOUS = "AMBIGUOUS"


class FeeTreatment(StrEnum):
    """How (if at all) canonical fee logic was applied to this observation."""

    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    FEE_UNKNOWN = "FEE_UNKNOWN"
    CANONICAL_FORMULA_FEE = "CANONICAL_FORMULA_FEE"


_SEEN_STATES = frozenset(
    {
        MeasurementState.DISCOVERY_ONLY,
        MeasurementState.INSUFFICIENT_DEPTH,
        MeasurementState.FEE_UNKNOWN,
        MeasurementState.EXACT_CONFIRMED,
        MeasurementState.AFTER_COST_POSITIVE_RESEARCH,
        MeasurementState.STALE,
    }
)


def relationship_id(lead: StructuralLead) -> str:
    """Scan-invariant identity for "the same sibling-threshold relationship, revisited".

    Deliberately excludes ``lead.broad_quote_source_hash``/``lead.narrow_quote_source_hash``.
    Canonical ``StructuralLead.lead_id`` binds those quote-content hashes, so it changes on every
    scan purely because prices moved -- grouping by ``lead_id`` would treat every scan's price
    movement as a brand-new lead and make lifetime tracking meaningless. This identity instead
    binds only WHAT is being compared: cohort, tickers, thresholds, rules/metadata hashes, and
    source authority. A change to any of those (e.g. a contract-rules amendment) is correctly a
    new relationship; a change in quoted price alone is not.
    """
    return stable_hash(
        (
            MEASUREMENT_POLICY_VERSION,
            "relationship-id",
            lead.cohort_identity,
            lead.broad_market_ticker,
            lead.narrow_market_ticker,
            str(lead.broad_threshold),
            str(lead.narrow_threshold),
            lead.broad_rules_hash,
            lead.broad_metadata_hash,
            lead.narrow_rules_hash,
            lead.narrow_metadata_hash,
            lead.source_authority,
        )
    )


def _validate_lead(lead: StructuralLead) -> StructuralLead:
    if type(lead) is not StructuralLead:
        raise OpportunityError("measurement requires an exact StructuralLead")
    if not lead.research_only or lead.production_influence != ZERO:
        raise OpportunityError("structural lead must remain research-only")
    if not lead.exact_confirmation_required:
        raise OpportunityError("structural lead must require exact confirmation")
    required = (
        lead.lead_id,
        lead.cohort_identity,
        lead.event_ticker,
        lead.broad_market_ticker,
        lead.narrow_market_ticker,
        lead.broad_quote_source_hash,
        lead.narrow_quote_source_hash,
        lead.broad_rules_hash,
        lead.broad_metadata_hash,
        lead.narrow_rules_hash,
        lead.narrow_metadata_hash,
        lead.source_authority,
    )
    if any(type(value) is not str or not value for value in required):
        raise OpportunityError("structural lead identities and source authority are required")
    expected = stable_hash(
        (
            POLICY_VERSION,
            lead.cohort_identity,
            lead.broad_market_ticker,
            lead.narrow_market_ticker,
            str(lead.broad_threshold),
            str(lead.narrow_threshold),
            lead.broad_quote_source_hash,
            lead.narrow_quote_source_hash,
            lead.broad_rules_hash,
            lead.broad_metadata_hash,
            lead.narrow_rules_hash,
            lead.narrow_metadata_hash,
            lead.source_authority,
        )
    )
    if lead.lead_id != expected:
        raise OpportunityError("structural lead identity formula mismatch")
    return lead


def _expected_confirmation_id(lead: StructuralLead, confirmation: StructuralConfirmation) -> str:
    return stable_hash(
        (
            POLICY_VERSION,
            lead.lead_id,
            confirmation.broad_evidence_id,
            confirmation.narrow_evidence_id,
            confirmation.state,
            str(confirmation.requested_quantity),
        )
    )


def _finite_decimal(value: Decimal, field_name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise OpportunityError(f"{field_name} must be finite")


def _validate_confirmation(
    lead: StructuralLead, confirmation: StructuralConfirmation
) -> StructuralConfirmation:
    if type(confirmation) is not StructuralConfirmation:
        raise OpportunityError("measurement requires an exact StructuralConfirmation")
    if confirmation.lead_id != lead.lead_id:
        raise OpportunityError("confirmation does not belong to this exact lead")
    if not confirmation.research_only or confirmation.production_influence != ZERO:
        raise OpportunityError("structural confirmation must remain research-only")
    if confirmation.final_net_profit is not None or confirmation.guaranteed_net_profit is not None:
        raise OpportunityError("structural confirmation must not contain profit authority")
    if (
        not confirmation.confirmation_id
        or not confirmation.broad_evidence_id
        or not confirmation.narrow_evidence_id
    ):
        raise OpportunityError("confirmation identities are required")
    if confirmation.confirmation_id != _expected_confirmation_id(lead, confirmation):
        raise OpportunityError("confirmation identity formula mismatch")
    _finite_decimal(confirmation.requested_quantity, "requested quantity")
    if confirmation.requested_quantity <= ZERO:
        raise OpportunityError("requested quantity must be positive")
    if confirmation.broad_side.value != "YES" or confirmation.narrow_side.value != "NO":
        raise OpportunityError("confirmation outcome sides are incoherent")

    economic_values = (
        confirmation.minimum_guaranteed_settlement_payout,
        confirmation.exact_gross_package_cost,
        confirmation.gross_structural_gap,
        confirmation.broad_centicent_formula_fee,
        confirmation.narrow_centicent_formula_fee,
        confirmation.centicent_formula_fees,
        confirmation.formula_adjusted_structural_gap,
    )
    for name, value in zip(
        (
            "minimum payout",
            "gross package cost",
            "gross structural gap",
            "broad formula fee",
            "narrow formula fee",
            "formula fees",
            "formula-adjusted structural gap",
        ),
        economic_values,
        strict=True,
    ):
        if value is not None:
            _finite_decimal(value, name)

    if confirmation.state in (
        ConfirmationState.INSUFFICIENT_BROAD_YES_DEPTH,
        ConfirmationState.INSUFFICIENT_NARROW_NO_DEPTH,
    ):
        if any(value is not None for value in economic_values):
            raise OpportunityError("insufficient-depth confirmation cannot contain economics")
        return confirmation

    if confirmation.state is not ConfirmationState.FINAL_FEE_UNKNOWN_PREFILL:
        raise OpportunityError("unknown confirmation state")
    required = (
        confirmation.minimum_guaranteed_settlement_payout,
        confirmation.exact_gross_package_cost,
        confirmation.gross_structural_gap,
        confirmation.broad_centicent_formula_fee,
        confirmation.narrow_centicent_formula_fee,
        confirmation.centicent_formula_fees,
        confirmation.formula_adjusted_structural_gap,
    )
    if any(value is None for value in required):
        raise OpportunityError("final confirmation economics are incomplete")
    payout = confirmation.minimum_guaranteed_settlement_payout
    cost = confirmation.exact_gross_package_cost
    gross = confirmation.gross_structural_gap
    broad_fee = confirmation.broad_centicent_formula_fee
    narrow_fee = confirmation.narrow_centicent_formula_fee
    fees = confirmation.centicent_formula_fees
    adjusted = confirmation.formula_adjusted_structural_gap
    if (
        payout is None
        or cost is None
        or gross is None
        or broad_fee is None
        or narrow_fee is None
        or fees is None
        or adjusted is None
    ):
        raise OpportunityError("final confirmation economics are incomplete")
    if payout != confirmation.requested_quantity or cost is None or cost < ZERO:
        raise OpportunityError("confirmation payout or cost is incoherent")
    if gross != payout - cost or fees != broad_fee + narrow_fee or adjusted != gross - fees:
        raise OpportunityError("confirmation fee-adjusted economics are incoherent")
    return confirmation


@dataclass(frozen=True, slots=True)
class LeadObservation:
    """One immutable, content-addressed observation of one relationship on one scan.

    Binds exact event, exact market tickers, thresholds, the exact observation timestamp, exact
    quote/evidence identities (when available), which ordering relationship was violated, the
    gross apparent gap, available size/depth (when observable), the fee treatment applied, and
    the resulting confirmation state -- per the M27B.2 measurement-unit requirement.
    """

    observation_id: str
    relationship_id: str
    scan_run_id: str
    observed_at: datetime
    event_ticker: str
    broad_market_ticker: str
    narrow_market_ticker: str
    broad_threshold: Decimal
    narrow_threshold: Decimal
    relationship_type: RelationshipType
    lead_id: str | None
    broad_quote_source_hash: str | None
    narrow_quote_source_hash: str | None
    gross_apparent_gap: Decimal | None
    indicative_quantity: Decimal | None
    confirmed_depth: Decimal | None
    fee_treatment: FeeTreatment
    formula_adjusted_gap: Decimal | None
    confirmation_id: str | None
    state: MeasurementState
    blocker_reason: str | None
    source_authority: str
    research_only: bool = True
    production_influence: Decimal = ZERO

    def __post_init__(self) -> None:
        if not self.research_only or self.production_influence != ZERO:
            raise OpportunityError("lead observation must remain research-only")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise OpportunityError("observed_at must be timezone-aware")
        if self.state is MeasurementState.AFTER_COST_POSITIVE_RESEARCH and (
            self.formula_adjusted_gap is None or self.formula_adjusted_gap <= ZERO
        ):
            raise OpportunityError(
                "AFTER_COST_POSITIVE_RESEARCH requires a positive formula-adjusted gap"
            )
        if self.state is MeasurementState.EXACT_CONFIRMED and self.confirmation_id is None:
            raise OpportunityError("EXACT_CONFIRMED requires a canonical confirmation identity")
        if (
            self.state
            in (MeasurementState.EXACT_CONFIRMED, MeasurementState.AFTER_COST_POSITIVE_RESEARCH)
            and self.fee_treatment is not FeeTreatment.CANONICAL_FORMULA_FEE
        ):
            raise OpportunityError(
                f"{self.state} requires canonical formula-fee treatment, never a substitute"
            )
        if (
            self.state is MeasurementState.FEE_UNKNOWN
            and self.fee_treatment is not FeeTreatment.FEE_UNKNOWN
        ):
            raise OpportunityError("FEE_UNKNOWN state requires FEE_UNKNOWN fee treatment")
        if self.state is MeasurementState.FEE_UNKNOWN and self.formula_adjusted_gap is not None:
            raise OpportunityError("FEE_UNKNOWN may never carry a positive-after-cost claim")
        if self.state.value in _ALIVE_STATES_REQUIRE_REASON and self.blocker_reason is None:
            raise OpportunityError(f"{self.state} requires an explicit blocker/reason")


def _canonical_field(value: object) -> object:
    """Coerce one observation field into a JSON-representable value for content addressing.

    ``stable_hash`` delegates to plain ``json.dumps`` with no custom encoder (see
    ``services.market_universe.domain.stable_hash``), so every ``Decimal``/``datetime``/
    ``StrEnum`` field must be converted explicitly -- exactly as canonical
    ``services.opportunity_engine.structural`` always does (``str(lead.broad_threshold)``, etc.)
    rather than relying on identity/dataclass-name hashing.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    return value


def _observation_id(fields: dict[str, object]) -> str:
    canonical = {key: _canonical_field(value) for key, value in fields.items()}
    return stable_hash((MEASUREMENT_POLICY_VERSION, "lead-observation", canonical))


def _base_fields(
    lead: StructuralLead, *, relationship_id_value: str, scan_run_id: str, observed_at: datetime
) -> dict[str, object]:
    _validate_lead(lead)
    return dict(
        relationship_id=relationship_id_value,
        scan_run_id=scan_run_id,
        observed_at=observed_at,
        event_ticker=lead.event_ticker,
        broad_market_ticker=lead.broad_market_ticker,
        narrow_market_ticker=lead.narrow_market_ticker,
        broad_threshold=lead.broad_threshold,
        narrow_threshold=lead.narrow_threshold,
        relationship_type=lead.relationship_type,
        lead_id=lead.lead_id,
        broad_quote_source_hash=lead.broad_quote_source_hash,
        narrow_quote_source_hash=lead.narrow_quote_source_hash,
        source_authority=lead.source_authority,
    )


def observation_content_identity(observation: LeadObservation) -> str:
    if type(observation) is not LeadObservation:
        raise OpportunityError("only exact LeadObservation may be validated")
    values = {
        field.name: getattr(observation, field.name)
        for field in fields(observation)
        if field.name not in {"observation_id", "research_only", "production_influence"}
    }
    return _observation_id(values)


def _validate_previous(previous: LeadObservation) -> LeadObservation:
    if type(previous) is not LeadObservation:
        raise OpportunityError("lifecycle transition requires an exact LeadObservation")
    if previous.research_only is not True or previous.production_influence != ZERO:
        raise OpportunityError("previous observation must remain research-only")
    if not all(
        type(value) is str and bool(value)
        for value in (
            previous.observation_id,
            previous.relationship_id,
            previous.scan_run_id,
            previous.event_ticker,
            previous.broad_market_ticker,
            previous.narrow_market_ticker,
            previous.source_authority,
        )
    ):
        raise OpportunityError("previous observation identities are required")
    if observation_content_identity(previous) != previous.observation_id:
        raise OpportunityError("previous observation identity formula mismatch")
    return previous


def record_discovery_only(
    lead: StructuralLead,
    *,
    relationship_id_value: str,
    scan_run_id: str,
    observed_at: datetime,
    blocker_reason: str,
) -> LeadObservation:
    """A lead the canonical scanner discovered but exact confirmation could not be attempted.

    Used whenever exact confirmation requires authority that is not currently available (market
    snapshot acquisition failed, orderbook acquisition failed, or the contract specification is
    not strategy-supported) -- the requirement is never weakened; the lead is recorded as
    unconfirmed with an explicit blocker instead.
    """
    if not blocker_reason:
        raise OpportunityError("DISCOVERY_ONLY requires a non-empty blocker reason")
    base = _base_fields(
        lead,
        relationship_id_value=relationship_id_value,
        scan_run_id=scan_run_id,
        observed_at=observed_at,
    )
    fields = dict(
        base,
        gross_apparent_gap=lead.indicative_gross_gap,
        indicative_quantity=lead.indicative_quantity,
        confirmed_depth=None,
        fee_treatment=FeeTreatment.NOT_ATTEMPTED,
        formula_adjusted_gap=None,
        confirmation_id=None,
        state=MeasurementState.DISCOVERY_ONLY,
        blocker_reason=blocker_reason,
    )
    return LeadObservation(_observation_id(fields), **fields)  # type: ignore[arg-type]


def record_fee_unknown(
    lead: StructuralLead,
    *,
    relationship_id_value: str,
    scan_run_id: str,
    observed_at: datetime,
    gross_apparent_gap: Decimal,
    confirmed_depth: Decimal | None,
    blocker_reason: str,
) -> LeadObservation:
    """Executable depth was independently validated, but canonical fee-regime resolution failed.

    Never claims a positive after-cost edge: ``formula_adjusted_gap`` is fixed to ``None``.
    """
    if not blocker_reason:
        raise OpportunityError("FEE_UNKNOWN requires a non-empty blocker reason")
    if not gross_apparent_gap.is_finite():
        raise OpportunityError("gross_apparent_gap must be finite")
    base = _base_fields(
        lead,
        relationship_id_value=relationship_id_value,
        scan_run_id=scan_run_id,
        observed_at=observed_at,
    )
    fields = dict(
        base,
        gross_apparent_gap=gross_apparent_gap,
        indicative_quantity=lead.indicative_quantity,
        confirmed_depth=confirmed_depth,
        fee_treatment=FeeTreatment.FEE_UNKNOWN,
        formula_adjusted_gap=None,
        confirmation_id=None,
        state=MeasurementState.FEE_UNKNOWN,
        blocker_reason=blocker_reason,
    )
    return LeadObservation(_observation_id(fields), **fields)  # type: ignore[arg-type]


def record_exact_confirmation(
    lead: StructuralLead,
    confirmation: StructuralConfirmation,
    *,
    relationship_id_value: str,
    scan_run_id: str,
    observed_at: datetime,
) -> LeadObservation:
    """Derive a measurement observation from a canonical exact confirmation.

    Never weakens or recomputes canonical ``confirm_structural_lead`` output -- every economics
    value here is read directly from ``confirmation``. Fails closed if the confirmation does not
    belong to this exact lead.
    """
    _validate_lead(lead)
    _validate_confirmation(lead, confirmation)
    base = _base_fields(
        lead,
        relationship_id_value=relationship_id_value,
        scan_run_id=scan_run_id,
        observed_at=observed_at,
    )
    if confirmation.state in (
        ConfirmationState.INSUFFICIENT_BROAD_YES_DEPTH,
        ConfirmationState.INSUFFICIENT_NARROW_NO_DEPTH,
    ):
        fields = dict(
            base,
            gross_apparent_gap=lead.indicative_gross_gap,
            indicative_quantity=lead.indicative_quantity,
            confirmed_depth=None,
            fee_treatment=FeeTreatment.NOT_ATTEMPTED,
            formula_adjusted_gap=None,
            confirmation_id=confirmation.confirmation_id,
            state=MeasurementState.INSUFFICIENT_DEPTH,
            blocker_reason=f"insufficient executable depth: {confirmation.state.value}",
        )
        return LeadObservation(_observation_id(fields), **fields)  # type: ignore[arg-type]

    if (
        confirmation.gross_structural_gap is None
        or confirmation.formula_adjusted_structural_gap is None
    ):
        raise OpportunityError(
            "final-fee-unknown-prefill confirmation missing its required gross/fee-adjusted gap"
        )
    adjusted = confirmation.formula_adjusted_structural_gap
    state = (
        MeasurementState.AFTER_COST_POSITIVE_RESEARCH
        if adjusted > ZERO
        else MeasurementState.EXACT_CONFIRMED
    )
    fields = dict(
        base,
        gross_apparent_gap=confirmation.gross_structural_gap,
        indicative_quantity=lead.indicative_quantity,
        confirmed_depth=confirmation.requested_quantity,
        fee_treatment=FeeTreatment.CANONICAL_FORMULA_FEE,
        formula_adjusted_gap=adjusted,
        confirmation_id=confirmation.confirmation_id,
        state=state,
        blocker_reason=None,
    )
    return LeadObservation(_observation_id(fields), **fields)  # type: ignore[arg-type]


def record_stale(
    previous: LeadObservation,
    *,
    scan_run_id: str,
    observed_at: datetime,
    blocker_reason: str,
) -> LeadObservation:
    """The scan still finds this relationship, but a fresh confirmation attempt's own acquired
    evidence failed its independent freshness check. Distinct from ``DISCOVERY_ONLY``: this
    relationship has previously produced real evidence; only this cycle's revisit is stale."""
    _validate_previous(previous)
    if not blocker_reason:
        raise OpportunityError("STALE requires a non-empty blocker reason")
    fields = dict(
        relationship_id=previous.relationship_id,
        scan_run_id=scan_run_id,
        observed_at=observed_at,
        event_ticker=previous.event_ticker,
        broad_market_ticker=previous.broad_market_ticker,
        narrow_market_ticker=previous.narrow_market_ticker,
        broad_threshold=previous.broad_threshold,
        narrow_threshold=previous.narrow_threshold,
        relationship_type=previous.relationship_type,
        lead_id=previous.lead_id,
        broad_quote_source_hash=None,
        narrow_quote_source_hash=None,
        gross_apparent_gap=None,
        indicative_quantity=None,
        confirmed_depth=None,
        fee_treatment=FeeTreatment.NOT_ATTEMPTED,
        formula_adjusted_gap=None,
        confirmation_id=None,
        state=MeasurementState.STALE,
        blocker_reason=blocker_reason,
        source_authority=previous.source_authority,
    )
    return LeadObservation(_observation_id(fields), **fields)  # type: ignore[arg-type]


def record_disappeared(
    previous: LeadObservation, *, scan_run_id: str, observed_at: datetime
) -> LeadObservation:
    """The canonical scan no longer produces any lead for this relationship: the underlying
    ordering is no longer inverted, or one leg is no longer an eligible market. Closes the
    lifetime at the scan that first failed to reproduce it."""
    _validate_previous(previous)
    if previous.state is MeasurementState.AMBIGUOUS:
        raise OpportunityError("AMBIGUOUS cannot be converted to DISAPPEARED")
    fields = dict(
        relationship_id=previous.relationship_id,
        scan_run_id=scan_run_id,
        observed_at=observed_at,
        event_ticker=previous.event_ticker,
        broad_market_ticker=previous.broad_market_ticker,
        narrow_market_ticker=previous.narrow_market_ticker,
        broad_threshold=previous.broad_threshold,
        narrow_threshold=previous.narrow_threshold,
        relationship_type=previous.relationship_type,
        lead_id=None,
        broad_quote_source_hash=None,
        narrow_quote_source_hash=None,
        gross_apparent_gap=None,
        indicative_quantity=None,
        confirmed_depth=None,
        fee_treatment=FeeTreatment.NOT_ATTEMPTED,
        formula_adjusted_gap=None,
        confirmation_id=None,
        state=MeasurementState.DISAPPEARED,
        blocker_reason="lead no longer reproduced by canonical structural discovery",
        source_authority=previous.source_authority,
    )
    return LeadObservation(_observation_id(fields), **fields)  # type: ignore[arg-type]


def record_ambiguous(
    previous: LeadObservation, *, scan_run_id: str, observed_at: datetime, blocker_reason: str
) -> LeadObservation:
    """The cohort backing this relationship became structurally ambiguous on a later scan (for
    example canonical ``DUPLICATE_THRESHOLD``/``MIXED_CUSTOM_STRIKE_PRESENCE`` abstention) rather
    than cleanly absent -- recorded distinctly so it is never conflated with ``DISAPPEARED``."""
    _validate_previous(previous)
    if not blocker_reason:
        raise OpportunityError("AMBIGUOUS requires a non-empty blocker reason")
    fields = dict(
        relationship_id=previous.relationship_id,
        scan_run_id=scan_run_id,
        observed_at=observed_at,
        event_ticker=previous.event_ticker,
        broad_market_ticker=previous.broad_market_ticker,
        narrow_market_ticker=previous.narrow_market_ticker,
        broad_threshold=previous.broad_threshold,
        narrow_threshold=previous.narrow_threshold,
        relationship_type=previous.relationship_type,
        lead_id=None,
        broad_quote_source_hash=None,
        narrow_quote_source_hash=None,
        gross_apparent_gap=None,
        indicative_quantity=None,
        confirmed_depth=None,
        fee_treatment=FeeTreatment.NOT_ATTEMPTED,
        formula_adjusted_gap=None,
        confirmation_id=None,
        state=MeasurementState.AMBIGUOUS,
        blocker_reason=blocker_reason,
        source_authority=previous.source_authority,
    )
    return LeadObservation(_observation_id(fields), **fields)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class LeadLifetime:
    """Pure aggregate over every :class:`LeadObservation` sharing one ``relationship_id``.

    ``observed_lifetime_lower_bound``/``upper_bound`` are directly-observed bounds, never an
    estimate: the lower bound is the exact span between the first and last scan that actually saw
    the relationship; ambiguous observations are censoring records and do not count as seen. A
    relationship ending in an affirmative state is still active and right-censored, while one
    ending in ``AMBIGUOUS`` is closed by an explicit ambiguity censor. ``disappeared_at`` and
    ``ambiguity_censored_at`` are mutually exclusive; only disappearance supplies an upper bound.
    """

    relationship_id: str
    event_ticker: str
    broad_market_ticker: str
    narrow_market_ticker: str
    first_seen_at: datetime
    last_seen_at: datetime
    observation_count: int
    consecutive_observations: int
    still_active: bool
    disappeared_at: datetime | None
    ambiguity_censored_at: datetime | None
    observed_lifetime_lower_bound_seconds: Decimal
    observed_lifetime_upper_bound_seconds: Decimal | None
    maximum_gross_inversion: Decimal | None
    maximum_confirmed_depth: Decimal | None
    maximum_after_cost_gap: Decimal | None

    def __post_init__(self) -> None:
        identities = (
            self.relationship_id,
            self.event_ticker,
            self.broad_market_ticker,
            self.narrow_market_ticker,
        )
        if any(type(value) is not str or not value for value in identities):
            raise OpportunityError("lifetime identities are required")
        for name, value in (
            ("first_seen_at", self.first_seen_at),
            ("last_seen_at", self.last_seen_at),
            ("disappeared_at", self.disappeared_at),
            ("ambiguity_censored_at", self.ambiguity_censored_at),
        ):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise OpportunityError(f"{name} must be timezone-aware")
        if self.first_seen_at > self.last_seen_at:
            raise OpportunityError("first_seen_at must be no later than last_seen_at")
        if type(self.observation_count) is not int or self.observation_count < 1:
            raise OpportunityError("observation_count must be at least one")
        if (
            type(self.consecutive_observations) is not int
            or not 0 <= self.consecutive_observations <= self.observation_count
        ):
            raise OpportunityError("consecutive_observations must be within observation_count")
        expected_lower = Decimal(str((self.last_seen_at - self.first_seen_at).total_seconds()))
        if (
            not isinstance(self.observed_lifetime_lower_bound_seconds, Decimal)
            or not self.observed_lifetime_lower_bound_seconds.is_finite()
            or self.observed_lifetime_lower_bound_seconds < ZERO
            or self.observed_lifetime_lower_bound_seconds != expected_lower
        ):
            raise OpportunityError("lifetime lower bound is inconsistent")
        active = (
            self.still_active and self.disappeared_at is None and self.ambiguity_censored_at is None
        )
        disappeared = (
            not self.still_active
            and self.disappeared_at is not None
            and self.ambiguity_censored_at is None
        )
        ambiguity_censored = (
            not self.still_active
            and self.disappeared_at is None
            and self.ambiguity_censored_at is not None
        )
        if sum((active, disappeared, ambiguity_censored)) != 1:
            raise OpportunityError(
                "lifetime status must be exactly one of active, disappeared, or ambiguity-censored"
            )
        if active and self.observed_lifetime_upper_bound_seconds is not None:
            raise OpportunityError("active lifetime cannot have an upper bound")
        if active and self.consecutive_observations < 1:
            raise OpportunityError("active lifetime requires a positive consecutive count")
        if disappeared and self.observed_lifetime_upper_bound_seconds is None:
            raise OpportunityError("disappeared lifetime requires an upper bound")
        if disappeared and self.consecutive_observations != 0:
            raise OpportunityError("disappeared lifetime requires zero consecutive observations")
        if ambiguity_censored and self.observed_lifetime_upper_bound_seconds is not None:
            raise OpportunityError("ambiguity-censored lifetime cannot have an upper bound")
        if ambiguity_censored and self.consecutive_observations != 0:
            raise OpportunityError(
                "ambiguity-censored lifetime requires zero consecutive observations"
            )
        terminal_at = self.disappeared_at or self.ambiguity_censored_at
        if terminal_at is not None and terminal_at < self.last_seen_at:
            raise OpportunityError("terminal timestamp must be no earlier than last_seen_at")
        if disappeared:
            disappeared_at = self.disappeared_at
            if disappeared_at is None:  # pragma: no cover - status invariant above
                raise OpportunityError("disappeared lifetime requires a timestamp")
            expected_upper = Decimal(str((disappeared_at - self.first_seen_at).total_seconds()))
            upper = self.observed_lifetime_upper_bound_seconds
            if (
                not isinstance(upper, Decimal)
                or not upper.is_finite()
                or upper < ZERO
                or upper != expected_upper
                or upper < self.observed_lifetime_lower_bound_seconds
            ):
                raise OpportunityError("lifetime upper bound is inconsistent")


def compute_lifetime(observations: Sequence[LeadObservation]) -> LeadLifetime:
    if not observations:
        raise OpportunityError("cannot compute a lifetime from zero observations")
    relationship = observations[0].relationship_id
    if any(observation.relationship_id != relationship for observation in observations):
        raise OpportunityError("all observations must share one relationship_id")
    scan_ids = [observation.scan_run_id for observation in observations]
    if len(set(scan_ids)) != len(scan_ids):
        raise OpportunityError("an episode cannot contain duplicate scan_run_id values")
    identities = {
        (
            observation.event_ticker,
            observation.broad_market_ticker,
            observation.narrow_market_ticker,
        )
        for observation in observations
    }
    if len(identities) != 1:
        raise OpportunityError("all observations must share event and market identities")
    ordered = sorted(observations, key=lambda observation: observation.observed_at)
    seen = [observation for observation in ordered if observation.state in _SEEN_STATES]
    if not seen:
        raise OpportunityError("a lifetime requires at least one 'seen' observation")
    for index, observation in enumerate(ordered):
        if (
            observation.state in (MeasurementState.DISAPPEARED, MeasurementState.AMBIGUOUS)
            and index < len(ordered) - 1
        ):
            gap = (
                "closed observation gap"
                if observation.state is MeasurementState.DISAPPEARED
                else "ambiguous observation gap"
            )
            raise OpportunityError(f"terminal observation cannot be followed: {gap}")
    first_seen_at = seen[0].observed_at
    last_seen_at = seen[-1].observed_at
    terminal = ordered[-1]
    if terminal.state is MeasurementState.DISAPPEARED:
        disappeared_at = terminal.observed_at
        ambiguity_censored_at = None
        still_active = False
    elif terminal.state is MeasurementState.AMBIGUOUS:
        disappeared_at = None
        ambiguity_censored_at = terminal.observed_at
        still_active = False
    else:
        disappeared_at = None
        ambiguity_censored_at = None
        still_active = True

    consecutive = 0
    for observation in reversed(ordered):
        if observation.state is MeasurementState.DISAPPEARED:
            break
        if observation.state in _SEEN_STATES:
            consecutive += 1
        else:  # pragma: no cover - every non-DISAPPEARED state is currently in _SEEN_STATES
            break

    lower_bound = Decimal(str((last_seen_at - first_seen_at).total_seconds()))
    upper_bound = (
        Decimal(str((disappeared_at - first_seen_at).total_seconds()))
        if disappeared_at is not None
        else None
    )
    gross_values = [o.gross_apparent_gap for o in ordered if o.gross_apparent_gap is not None]
    depth_values = [o.confirmed_depth for o in ordered if o.confirmed_depth is not None]
    after_cost_values = [
        o.formula_adjusted_gap for o in ordered if o.formula_adjusted_gap is not None
    ]
    return LeadLifetime(
        relationship,
        observations[0].event_ticker,
        observations[0].broad_market_ticker,
        observations[0].narrow_market_ticker,
        first_seen_at,
        last_seen_at,
        len(ordered),
        consecutive,
        still_active,
        disappeared_at,
        ambiguity_censored_at,
        lower_bound,
        upper_bound,
        max(gross_values) if gross_values else None,
        max(depth_values) if depth_values else None,
        max(after_cost_values) if after_cost_values else None,
    )


def _median(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    count = len(ordered)
    middle = count // 2
    if count % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


@dataclass(frozen=True, slots=True)
class MeasurementRunSummary:
    """Event-level summary with separate frequency, persistence, and executability sections.

    Persistence categories are mutually exclusive: affirmative right-censored lifetimes are
    active, disappeared lifetimes have an observed disappearance, and ambiguity-censored
    lifetimes have neither an active nor disappearance claim.
    """

    # -- frequency --
    scans_completed: int
    independent_cohorts_observed: int
    discovery_lead_count: int
    leads_per_event: Decimal | None

    # -- persistence --
    lead_lifetime_lower_bound_seconds: tuple[Decimal, ...]
    lead_lifetime_median_seconds: Decimal | None
    still_active_count: int
    disappeared_count: int
    ambiguity_censored_count: int

    # -- after-cost executability --
    exact_confirmation_attempts: int
    exact_confirmation_rate: Decimal | None
    after_cost_positive_count: int
    gross_gap_distribution: tuple[Decimal, ...]
    depth_distribution: tuple[Decimal, ...]

    missing_evidence_reasons: tuple[tuple[str, int], ...]
    research_only: bool = True
    production_influence: Decimal = ZERO


def summarize_run(
    observations: Sequence[LeadObservation],
    lifetimes: Sequence[LeadLifetime],
    *,
    scans_completed: int,
    independent_cohorts_observed: int,
) -> MeasurementRunSummary:
    if scans_completed < 0:
        raise OpportunityError("scans_completed must be non-negative")
    lifetime_ids = [lifetime.relationship_id for lifetime in lifetimes]
    if len(set(lifetime_ids)) != len(lifetime_ids):
        raise OpportunityError("summary lifetimes must have unique relationship identities")
    discovery_lead_count = len({o.relationship_id for o in observations if o.lead_id is not None})
    leads_per_event = None
    events = {o.event_ticker for o in observations}
    if events:
        leads_per_event = Decimal(discovery_lead_count) / Decimal(len(events))

    lifetime_lowers = tuple(
        lifetime.observed_lifetime_lower_bound_seconds for lifetime in lifetimes
    )
    still_active = sum(1 for lifetime in lifetimes if lifetime.still_active)
    disappeared = sum(1 for lifetime in lifetimes if lifetime.disappeared_at is not None)
    ambiguity_censored = sum(
        1 for lifetime in lifetimes if lifetime.ambiguity_censored_at is not None
    )
    if still_active + disappeared + ambiguity_censored != len(lifetimes):
        raise OpportunityError("lifetime status categories must be exhaustive")

    confirmable_states = (
        MeasurementState.INSUFFICIENT_DEPTH,
        MeasurementState.EXACT_CONFIRMED,
        MeasurementState.AFTER_COST_POSITIVE_RESEARCH,
    )
    attempts = sum(1 for o in observations if o.state in confirmable_states)
    exact = sum(
        1
        for o in observations
        if o.state
        in (MeasurementState.EXACT_CONFIRMED, MeasurementState.AFTER_COST_POSITIVE_RESEARCH)
    )
    exact_rate = (Decimal(exact) / Decimal(attempts)) if attempts else None
    after_cost_positive = sum(
        1 for o in observations if o.state is MeasurementState.AFTER_COST_POSITIVE_RESEARCH
    )

    gross_values = tuple(
        o.gross_apparent_gap for o in observations if o.gross_apparent_gap is not None
    )
    depth_values = tuple(o.confirmed_depth for o in observations if o.confirmed_depth is not None)

    reasons: dict[str, int] = {}
    for observation in observations:
        if observation.blocker_reason is not None:
            reasons[observation.blocker_reason] = reasons.get(observation.blocker_reason, 0) + 1

    return MeasurementRunSummary(
        scans_completed,
        independent_cohorts_observed,
        discovery_lead_count,
        leads_per_event,
        lifetime_lowers,
        _median(list(lifetime_lowers)),
        still_active,
        disappeared,
        ambiguity_censored,
        attempts,
        exact_rate,
        after_cost_positive,
        gross_values,
        depth_values,
        tuple(sorted(reasons.items())),
    )
