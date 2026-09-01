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
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from services.market_universe.domain import stable_hash

from .domain import OpportunityError
from .structural import ConfirmationState, RelationshipType, StructuralConfirmation, StructuralLead

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
    if confirmation.lead_id != lead.lead_id:
        raise OpportunityError("confirmation does not belong to this exact lead")
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


def compute_lifetime(observations: Sequence[LeadObservation]) -> LeadLifetime:
    if not observations:
        raise OpportunityError("cannot compute a lifetime from zero observations")
    relationship = observations[0].relationship_id
    if any(observation.relationship_id != relationship for observation in observations):
        raise OpportunityError("all observations must share one relationship_id")
    ordered = sorted(observations, key=lambda observation: observation.observed_at)
    seen = [observation for observation in ordered if observation.state in _SEEN_STATES]
    if not seen:
        raise OpportunityError("a lifetime requires at least one 'seen' observation")
    if any(
        observation.state is MeasurementState.DISAPPEARED
        and any(
            later.state in _SEEN_STATES and later.observed_at > observation.observed_at
            for later in ordered
        )
        for observation in ordered
    ):
        raise OpportunityError("lifetime cannot span a closed observation gap")
    if any(
        observation.state is MeasurementState.AMBIGUOUS
        and any(
            later.state in _SEEN_STATES and later.observed_at > observation.observed_at
            for later in ordered
        )
        for observation in ordered
    ):
        raise OpportunityError("lifetime cannot span an ambiguous observation gap")
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
