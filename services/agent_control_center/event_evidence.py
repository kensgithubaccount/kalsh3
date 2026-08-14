"""M26E point-in-time exchange-event identity and evidence sufficiency.

This module is a downstream research projection.  It never rewrites M26C or
M26D identities and has no execution, governance, allocation, or network
dependency.  An exchange event is a grouping fact, not proof of statistical
independence.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from services.market_universe.domain import Event, Market

from .domain import ZERO_INFLUENCE
from .evaluation import EVALUATION_POLICY_VERSION, EvaluationEligibility, effective_evaluations

if TYPE_CHECKING:
    from .comparison import PairwiseComparison
    from .evaluation import ReceiptEvaluation
    from .evaluation_store import EvaluationStore

MARKET_EVENT_BINDING_VERSION = "m26e-market-event-binding-v1"
EVENT_EVIDENCE_MANIFEST_VERSION = "m26e-event-evidence-manifest-v1"
EVENT_ASSESSMENT_VERSION = "m26e-event-assessment-v1"
M26E_EVIDENCE_POLICY_VERSION = "m26e-event-evidence-sufficiency-v1"
WITHIN_EVENT_AGGREGATION_VERSION = "m26e-within-event-mean-paired-brier-v1"
POINT_IN_TIME_POLICY_VERSION = "m26e-observed-not-after-evaluation-source-v1"


class EventEvidenceError(ValueError):
    """Required event evidence is malformed, conflicting, or corrupt."""


class ExchangeEventIdentityState(StrEnum):
    PROVEN = "PROVEN"
    UNPROVEN = "UNPROVEN"
    CONFLICTED = "CONFLICTED"
    MISSING = "MISSING"


class IndependenceState(StrEnum):
    NOT_ASSESSED = "NOT_ASSESSED"
    NOT_PROVEN = "NOT_PROVEN"
    DEPENDENT = "DEPENDENT"
    PROVEN_DISTINCT_UNDER_POLICY = "PROVEN_DISTINCT_UNDER_POLICY"


class EvidenceSufficiencyState(StrEnum):
    NO_EVIDENCE = "NO_EVIDENCE"
    EVENT_IDENTITY_INCOMPLETE = "EVENT_IDENTITY_INCOMPLETE"
    INDEPENDENCE_UNPROVEN = "INDEPENDENCE_UNPROVEN"
    EARLY = "EARLY"
    DESCRIPTIVE_SUFFICIENT = "DESCRIPTIVE_SUFFICIENT"
    EVIDENCE_UNAVAILABLE = "EVIDENCE_UNAVAILABLE"


class ReviewEligibility(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"


class ObservationAuthorityState(StrEnum):
    """Authority is never inferred from caller-provided hashes or identifiers."""

    UNVERIFIED = "UNVERIFIED"
    ARCHIVE_VERIFIED = "ARCHIVE_VERIFIED"


def _utc(value: object, name: str) -> None:
    if not isinstance(value, datetime):
        raise EventEvidenceError(f"{name} must be a UTC datetime")
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise EventEvidenceError(f"{name} must be UTC")


def _time(value: datetime) -> str:
    _utc(value, "timestamp")
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def _sha(value: str, name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise EventEvidenceError(f"{name} must be a lowercase SHA-256 identity")


@dataclass(frozen=True, slots=True)
class UniverseEventObservation:
    """Unverified candidate Market/Event material observed together.

    Caller-provided hashes and IDs are content to validate, never authority.
    ``ARCHIVE_VERIFIED`` is reserved for a future constructor backed by a
    reconstructable immutable archive; no such constructor exists today.
    """

    observation_id: str
    market_ticker: str
    market_event_ticker: str
    event_ticker: str
    series_ticker: str
    market_metadata_hash: str
    event_metadata_hash: str
    observed_at: datetime
    provenance_hash: str
    authority_state: ObservationAuthorityState = field(
        default=ObservationAuthorityState.UNVERIFIED, init=False
    )
    production_influence: Decimal = ZERO_INFLUENCE

    def __post_init__(self) -> None:
        _utc(self.observed_at, "observed_at")
        for value, name in (
            (self.observation_id, "observation_id"),
            (self.market_metadata_hash, "market_metadata_hash"),
            (self.event_metadata_hash, "event_metadata_hash"),
            (self.provenance_hash, "provenance_hash"),
        ):
            _sha(value, name)
        if not all(
            (self.market_ticker, self.market_event_ticker, self.event_ticker, self.series_ticker)
        ):
            raise EventEvidenceError("universe event observation identity is incomplete")
        if self.production_influence != ZERO_INFLUENCE:
            raise EventEvidenceError("event observations have exactly zero production influence")
        if self.authority_state is not ObservationAuthorityState.UNVERIFIED:
            raise EventEvidenceError(
                "archive authority requires a repository-verified archive adapter"
            )

    @classmethod
    def from_entities(
        cls,
        market: Market,
        event: Event,
        *,
        observation_id: str,
        observed_at: datetime,
        provenance_hash: str,
    ) -> UniverseEventObservation:
        """Capture candidate parsed objects without ticker/title inference or authority."""
        return cls(
            observation_id,
            market.ticker,
            market.event_ticker,
            event.ticker,
            event.series_ticker,
            market.metadata_hash,
            event.metadata_hash,
            observed_at,
            provenance_hash,
        )

    def material(self) -> dict[str, str]:
        return {
            "event_metadata_hash": self.event_metadata_hash,
            "event_ticker": self.event_ticker,
            "market_event_ticker": self.market_event_ticker,
            "market_metadata_hash": self.market_metadata_hash,
            "market_ticker": self.market_ticker,
            "observation_id": self.observation_id,
            "observed_at": _time(self.observed_at),
            "authority_state": self.authority_state.value,
            "production_influence": "0",
            "provenance_hash": self.provenance_hash,
            "series_ticker": self.series_ticker,
        }


@dataclass(frozen=True, slots=True)
class EvaluatedMarketEventBinding:
    binding_id: str
    market_ticker: str
    state: ExchangeEventIdentityState
    event_ticker: str | None
    series_ticker: str | None
    market_metadata_hash: str | None
    event_metadata_hash: str | None
    source_observation_id: str | None
    provenance_hash: str | None
    observation_authority_state: ObservationAuthorityState | None
    observed_at: datetime | None
    as_of: datetime
    detail: str
    binding_policy_version: str = MARKET_EVENT_BINDING_VERSION
    production_influence: Decimal = ZERO_INFLUENCE

    def __post_init__(self) -> None:
        _utc(self.as_of, "as_of")
        if self.observed_at is not None:
            _utc(self.observed_at, "observed_at")
            if self.observed_at > self.as_of:
                raise EventEvidenceError("event observation occurs after historical as-of")
        if self.production_influence != ZERO_INFLUENCE:
            raise EventEvidenceError("event bindings have exactly zero production influence")
        if self.state is ExchangeEventIdentityState.PROVEN:
            raise EventEvidenceError(
                "authoritative historical identity is unavailable without an archive verifier"
            )
        if self.binding_id != _binding_identity(self):
            raise EventEvidenceError("event binding identity mismatch")


def _binding_identity(binding: EvaluatedMarketEventBinding) -> str:
    return _hash(
        {
            "as_of": _time(binding.as_of),
            "binding_policy_version": binding.binding_policy_version,
            "detail": binding.detail,
            "domain": MARKET_EVENT_BINDING_VERSION,
            "event_metadata_hash": binding.event_metadata_hash,
            "event_ticker": binding.event_ticker,
            "market_metadata_hash": binding.market_metadata_hash,
            "market_ticker": binding.market_ticker,
            "observed_at": None if binding.observed_at is None else _time(binding.observed_at),
            "observation_authority_state": (
                None
                if binding.observation_authority_state is None
                else binding.observation_authority_state.value
            ),
            "production_influence": "0",
            "provenance_hash": binding.provenance_hash,
            "series_ticker": binding.series_ticker,
            "source_observation_id": binding.source_observation_id,
            "state": binding.state.value,
        }
    )


def bind_market_event(
    market_ticker: str,
    *,
    as_of: datetime,
    observations: tuple[UniverseEventObservation, ...],
) -> EvaluatedMarketEventBinding:
    """Validate point-in-time candidate grouping without inventing archive authority."""
    _utc(as_of, "as_of")
    by_observation_id: dict[str, str] = {}
    observation_id_conflict = False
    for row in observations:
        material = _hash(row.material())
        prior = by_observation_id.setdefault(row.observation_id, material)
        if prior != material:
            observation_id_conflict = True
    if observation_id_conflict:
        conflict_values: dict[str, object] = dict(
            market_ticker=market_ticker,
            state=ExchangeEventIdentityState.CONFLICTED,
            event_ticker=None,
            series_ticker=None,
            market_metadata_hash=None,
            event_metadata_hash=None,
            source_observation_id=None,
            provenance_hash=None,
            observation_authority_state=None,
            observed_at=None,
            as_of=as_of,
            detail="An observation_id was reused with different immutable material",
        )
        draft = cast(
            EvaluatedMarketEventBinding,
            SimpleNamespace(
                **conflict_values,
                binding_policy_version=MARKET_EVENT_BINDING_VERSION,
                production_influence=ZERO_INFLUENCE,
            ),
        )
        return EvaluatedMarketEventBinding(_binding_identity(draft), **conflict_values)  # type: ignore[arg-type]
    eligible = sorted(
        (
            row
            for row in observations
            if row.market_ticker == market_ticker and row.observed_at <= as_of
        ),
        key=lambda row: (_time(row.observed_at), row.observation_id),
    )
    if not eligible:
        values: dict[str, object] = dict(
            market_ticker=market_ticker,
            state=ExchangeEventIdentityState.MISSING,
            event_ticker=None,
            series_ticker=None,
            market_metadata_hash=None,
            event_metadata_hash=None,
            source_observation_id=None,
            provenance_hash=None,
            observation_authority_state=None,
            observed_at=None,
            as_of=as_of,
            detail=("No candidate universe observation was available by the as-of time"),
        )
        draft = cast(
            EvaluatedMarketEventBinding,
            SimpleNamespace(
                **values,
                binding_policy_version=MARKET_EVENT_BINDING_VERSION,
                production_influence=ZERO_INFLUENCE,
            ),
        )
    else:
        facts = {(row.market_event_ticker, row.event_ticker, row.series_ticker) for row in eligible}
        mismatched = any(row.market_event_ticker != row.event_ticker for row in eligible)
        versions_by_time: dict[datetime, set[tuple[str, str]]] = defaultdict(set)
        for row in eligible:
            versions_by_time[row.observed_at].add(
                (row.market_metadata_hash, row.event_metadata_hash)
            )
        ambiguous_snapshot = any(len(versions) != 1 for versions in versions_by_time.values())
        if mismatched or len(facts) != 1 or ambiguous_snapshot:
            values = dict(
                market_ticker=market_ticker,
                state=ExchangeEventIdentityState.CONFLICTED,
                event_ticker=None,
                series_ticker=None,
                market_metadata_hash=None,
                event_metadata_hash=None,
                source_observation_id=None,
                provenance_hash=None,
                observation_authority_state=None,
                observed_at=None,
                as_of=as_of,
                detail="Candidate observations disagree on market, event, or series identity",
            )
            draft = cast(
                EvaluatedMarketEventBinding,
                SimpleNamespace(
                    **values,
                    binding_policy_version=MARKET_EVENT_BINDING_VERSION,
                    production_influence=ZERO_INFLUENCE,
                ),
            )
        else:
            # Version transitions that preserve identity are valid. Use the latest
            # historical observation, never an observation after the cohort as-of.
            row = eligible[-1]
            values = dict(
                market_ticker=market_ticker,
                state=ExchangeEventIdentityState.UNPROVEN,
                event_ticker=row.event_ticker,
                series_ticker=row.series_ticker,
                market_metadata_hash=row.market_metadata_hash,
                event_metadata_hash=row.event_metadata_hash,
                source_observation_id=row.observation_id,
                provenance_hash=row.provenance_hash,
                observation_authority_state=row.authority_state,
                observed_at=row.observed_at,
                as_of=as_of,
                detail=(
                    "Candidate point-in-time Market.event_ticker matches Event.ticker, but no "
                    "repository-verified historical archive proves its authority"
                ),
            )
            draft = cast(
                EvaluatedMarketEventBinding,
                SimpleNamespace(
                    **values,
                    binding_policy_version=MARKET_EVENT_BINDING_VERSION,
                    production_influence=ZERO_INFLUENCE,
                ),
            )
    return EvaluatedMarketEventBinding(_binding_identity(draft), **values)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class EventEvidenceManifest:
    manifest_id: str
    source_universe_id: str
    market_tickers: tuple[str, ...]
    bindings: tuple[EvaluatedMarketEventBinding, ...]
    included_source_ids: tuple[str, ...]
    excluded_source_items: tuple[tuple[str, str], ...]
    time_policy_version: str = POINT_IN_TIME_POLICY_VERSION
    binding_policy_version: str = MARKET_EVENT_BINDING_VERSION
    production_influence: Decimal = ZERO_INFLUENCE

    def __post_init__(self) -> None:
        if self.production_influence != ZERO_INFLUENCE:
            raise EventEvidenceError("event manifests have exactly zero production influence")
        if self.market_tickers != tuple(sorted(set(self.market_tickers))):
            raise EventEvidenceError("manifest markets must be complete, unique, and canonical")
        if tuple(row.market_ticker for row in self.bindings) != self.market_tickers:
            raise EventEvidenceError("manifest must bind every declared market exactly once")
        _validate_manifest_bindings(self.bindings)
        if self.manifest_id != _manifest_identity(self):
            raise EventEvidenceError("event manifest identity mismatch")


def _validate_manifest_bindings(bindings: tuple[EvaluatedMarketEventBinding, ...]) -> None:
    """Revalidate authoritative invariants without trusting constructor history."""
    for binding in bindings:
        if binding.production_influence != ZERO_INFLUENCE:
            raise EventEvidenceError("manifest binding has nonzero production influence")
        if binding.binding_id != _binding_identity(binding):
            raise EventEvidenceError("manifest binding identity mismatch")
        if (
            binding.state is ExchangeEventIdentityState.PROVEN
            or binding.observation_authority_state is ObservationAuthorityState.ARCHIVE_VERIFIED
        ):
            raise EventEvidenceError(
                "manifest cannot contain authoritative identity without an archive verifier"
            )


def _manifest_identity(value: EventEvidenceManifest) -> str:
    return _hash(
        {
            "binding_ids": [row.binding_id for row in value.bindings],
            "binding_policy_version": value.binding_policy_version,
            "domain": EVENT_EVIDENCE_MANIFEST_VERSION,
            "excluded_source_items": value.excluded_source_items,
            "included_source_ids": value.included_source_ids,
            "market_tickers": value.market_tickers,
            "production_influence": "0",
            "source_universe_id": value.source_universe_id,
            "time_policy_version": value.time_policy_version,
        }
    )


def _make_manifest(
    *,
    source_universe_id: str,
    market_as_of: dict[str, datetime],
    included_source_ids: tuple[str, ...],
    excluded_source_items: tuple[tuple[str, str], ...],
    observations: tuple[UniverseEventObservation, ...],
) -> EventEvidenceManifest:
    markets = tuple(sorted(market_as_of))
    bindings = tuple(
        bind_market_event(ticker, as_of=market_as_of[ticker], observations=observations)
        for ticker in markets
    )
    values = dict(
        source_universe_id=source_universe_id,
        market_tickers=markets,
        bindings=bindings,
        included_source_ids=tuple(sorted(included_source_ids)),
        excluded_source_items=tuple(sorted(excluded_source_items)),
    )
    draft = cast(
        EventEvidenceManifest,
        SimpleNamespace(
            **values,
            time_policy_version=POINT_IN_TIME_POLICY_VERSION,
            binding_policy_version=MARKET_EVENT_BINDING_VERSION,
            production_influence=ZERO_INFLUENCE,
        ),
    )
    return EventEvidenceManifest(_manifest_identity(draft), **values)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class EvidenceSufficiencyPolicy:
    minimum_proven_independent_units: int = 50
    policy_version: str = M26E_EVIDENCE_POLICY_VERSION

    def __post_init__(self) -> None:
        if self.minimum_proven_independent_units <= 0:
            raise EventEvidenceError("evidence minimum must be positive")


DEFAULT_EVIDENCE_SUFFICIENCY_POLICY = EvidenceSufficiencyPolicy()


@dataclass(frozen=True, slots=True)
class EventEvidenceAssessment:
    assessment_id: str
    manifest_id: str
    market_count: int
    candidate_exchange_event_count: int
    proven_exchange_event_count: int | None
    unresolved_market_event_count: int | None
    dependence_cluster_count: int | None
    proven_independent_evidence_unit_count: int | None
    exchange_event_identity_state: ExchangeEventIdentityState
    independence_state: IndependenceState
    evidence_state: EvidenceSufficiencyState
    review_eligibility: ReviewEligibility
    policy_version: str
    binding_policy_version: str
    aggregation_policy_version: str
    explanation: str
    interval: None = None
    production_influence: Decimal = ZERO_INFLUENCE

    def __post_init__(self) -> None:
        if self.production_influence != ZERO_INFLUENCE or self.interval is not None:
            raise EventEvidenceError("M26E assessments have zero influence and no interval")
        if self.assessment_id != _assessment_identity(self):
            raise EventEvidenceError("event assessment identity mismatch")


def _assessment_identity(value: EventEvidenceAssessment) -> str:
    return _hash(
        {
            "dependence_cluster_count": value.dependence_cluster_count,
            "domain": EVENT_ASSESSMENT_VERSION,
            "aggregation_policy_version": value.aggregation_policy_version,
            "binding_policy_version": value.binding_policy_version,
            "candidate_exchange_event_count": value.candidate_exchange_event_count,
            "evidence_state": value.evidence_state.value,
            "exchange_event_identity_state": value.exchange_event_identity_state.value,
            "explanation": value.explanation,
            "independence_state": value.independence_state.value,
            "manifest_id": value.manifest_id,
            "market_count": value.market_count,
            "policy_version": value.policy_version,
            "production_influence": "0",
            "proven_exchange_event_count": value.proven_exchange_event_count,
            "proven_independent_evidence_unit_count": value.proven_independent_evidence_unit_count,
            "review_eligibility": value.review_eligibility.value,
            "unresolved_market_event_count": value.unresolved_market_event_count,
        }
    )


def assess_manifest(
    manifest: EventEvidenceManifest,
    policy: EvidenceSufficiencyPolicy = DEFAULT_EVIDENCE_SUFFICIENCY_POLICY,
) -> EventEvidenceAssessment:
    """Count proven exchange groups; conservatively leave independence unproven."""
    # Defense in depth: a same-process caller can bypass frozen-dataclass
    # constructors, so assessment repeats the manifest's trust-boundary checks.
    _validate_manifest_bindings(manifest.bindings)
    conflicted = [
        row for row in manifest.bindings if row.state is ExchangeEventIdentityState.CONFLICTED
    ]
    if conflicted:
        values = dict(
            manifest_id=manifest.manifest_id,
            market_count=len(manifest.market_tickers),
            candidate_exchange_event_count=0,
            proven_exchange_event_count=None,
            unresolved_market_event_count=None,
            dependence_cluster_count=None,
            proven_independent_evidence_unit_count=None,
            exchange_event_identity_state=ExchangeEventIdentityState.CONFLICTED,
            independence_state=IndependenceState.NOT_ASSESSED,
            evidence_state=EvidenceSufficiencyState.EVIDENCE_UNAVAILABLE,
            review_eligibility=ReviewEligibility.NOT_ELIGIBLE,
            policy_version=policy.policy_version,
            binding_policy_version=manifest.binding_policy_version,
            aggregation_policy_version=WITHIN_EVENT_AGGREGATION_VERSION,
            explanation=(
                "EVENT EVIDENCE UNAVAILABLE: conflicting authoritative identity blocks the "
                "complete assessment."
            ),
        )
    else:
        proven = [
            row for row in manifest.bindings if row.state is ExchangeEventIdentityState.PROVEN
        ]
        candidates = [
            row
            for row in manifest.bindings
            if row.state
            in {
                ExchangeEventIdentityState.PROVEN,
                ExchangeEventIdentityState.UNPROVEN,
            }
        ]
        unresolved = len(manifest.bindings) - len(proven)
        event_count = len({row.event_ticker for row in proven})
        candidate_event_count = len({row.event_ticker for row in candidates})
        reported_event_count = event_count if proven else None
        if not manifest.bindings:
            state = EvidenceSufficiencyState.NO_EVIDENCE
        elif unresolved:
            state = EvidenceSufficiencyState.EVENT_IDENTITY_INCOMPLETE
        else:
            state = EvidenceSufficiencyState.INDEPENDENCE_UNPROVEN
        values = dict(
            manifest_id=manifest.manifest_id,
            market_count=len(manifest.market_tickers),
            candidate_exchange_event_count=candidate_event_count,
            proven_exchange_event_count=reported_event_count,
            unresolved_market_event_count=unresolved,
            dependence_cluster_count=None,
            proven_independent_evidence_unit_count=None,
            exchange_event_identity_state=(
                ExchangeEventIdentityState.PROVEN
                if not unresolved and manifest.bindings
                else ExchangeEventIdentityState.UNPROVEN
            ),
            independence_state=IndependenceState.NOT_PROVEN,
            evidence_state=state,
            review_eligibility=ReviewEligibility.NOT_ELIGIBLE,
            policy_version=policy.policy_version,
            binding_policy_version=manifest.binding_policy_version,
            aggregation_policy_version=WITHIN_EVENT_AGGREGATION_VERSION,
            explanation=(
                f"{len(manifest.market_tickers)} scored markets map consistently to "
                f"{candidate_event_count} candidate Kalshi events, but the authoritative "
                "historical "
                "event count is unavailable because no verified archive boundary exists; "
                "independence across distinct exchange events has not been established."
            ),
        )
    draft = cast(
        EventEvidenceAssessment, SimpleNamespace(**values, production_influence=ZERO_INFLUENCE)
    )
    return EventEvidenceAssessment(_assessment_identity(draft), **values)  # type: ignore[arg-type]


def manifest_from_evaluation_store(
    store: EvaluationStore,
    *,
    agent_id: str,
    agent_version: str,
    start_at: datetime,
    end_at: datetime,
    generated_at: datetime,
    observations: tuple[UniverseEventObservation, ...],
    policy_version: str = EVALUATION_POLICY_VERSION,
) -> EventEvidenceManifest:
    """Derive the complete effective M26C universe; callers cannot submit market IDs."""
    source = store.build_manifest(
        agent_id=agent_id,
        agent_version=agent_version,
        start_at=start_at,
        end_at=end_at,
        generated_at=generated_at,
        policy_version=policy_version,
    )
    effective = effective_evaluations(store.all(), policy_version=policy_version)
    selected = tuple(
        row
        for row in effective
        if row.agent_id == agent_id
        and row.agent_version == agent_version
        and start_at <= row.evaluated_at <= end_at
    )
    eligible = tuple(row for row in selected if row.eligibility is EvaluationEligibility.ELIGIBLE)
    as_of: dict[str, datetime] = {}
    for row in eligible:
        prior = as_of.get(row.market_ticker)
        when = row.target.source_observed_at
        as_of[row.market_ticker] = when if prior is None else min(prior, when)
    return _make_manifest(
        source_universe_id=source.manifest_id,
        market_as_of=as_of,
        included_source_ids=tuple(row.evaluation_id for row in eligible),
        excluded_source_items=tuple(
            (row.evaluation_id, row.eligibility.value)
            for row in selected
            if row.eligibility is not EvaluationEligibility.ELIGIBLE
        ),
        observations=observations,
    )


def manifest_from_pairwise_comparison(
    comparison: PairwiseComparison,
    evaluations: tuple[ReceiptEvaluation, ...],
    *,
    observations: tuple[UniverseEventObservation, ...],
) -> EventEvidenceManifest:
    """Bind every shared unit and exclusion from one immutable M26D cohort."""
    if comparison.cohort is None:
        raise EventEvidenceError("pairwise event evidence requires an M26D cohort")
    by_id = {row.evaluation_id: row for row in evaluations}
    as_of: dict[str, datetime] = {}
    included: list[str] = []
    for shared in comparison.cohort.shared_units:
        a = by_id.get(shared.contender_a_evaluation_id)
        b = by_id.get(shared.contender_b_evaluation_id)
        if a is None or b is None:
            raise EventEvidenceError("complete pairwise cohort source evaluation is missing")
        if (
            a.market_ticker != shared.unit.market_ticker
            or b.market_ticker != shared.unit.market_ticker
        ):
            raise EventEvidenceError("pairwise unit disagrees with source evaluation market")
        as_of[shared.unit.market_ticker] = min(
            a.target.source_observed_at, b.target.source_observed_at
        )
        included.extend((a.evaluation_id, b.evaluation_id))
    return _make_manifest(
        source_universe_id=comparison.cohort.cohort_id,
        market_as_of=as_of,
        included_source_ids=tuple(included),
        excluded_source_items=tuple(
            (row.unit_id, row.reason) for row in comparison.cohort.excluded_units
        ),
        observations=observations,
    )


@dataclass(frozen=True, slots=True)
class EventPairedDifference:
    event_ticker: str
    shared_market_count: int
    mean_a_minus_b_brier: Decimal
    aggregation_policy_version: str = WITHIN_EVENT_AGGREGATION_VERSION


def aggregate_pairwise_by_exchange_event(
    comparison: PairwiseComparison,
    evaluations: tuple[ReceiptEvaluation, ...],
    manifest: EventEvidenceManifest,
) -> tuple[EventPairedDifference, ...]:
    """Equal-weight markets within an event, producing one descriptive event row."""
    if comparison.cohort is None:
        return ()
    by_id = {row.evaluation_id: row for row in evaluations}
    binding_by_market = {row.market_ticker: row for row in manifest.bindings}
    market_differences: list[tuple[str, Decimal]] = []
    for shared in comparison.cohort.shared_units:
        a = by_id.get(shared.contender_a_evaluation_id)
        b = by_id.get(shared.contender_b_evaluation_id)
        if a is None or b is None or a.model_brier is None or b.model_brier is None:
            raise EventEvidenceError("pairwise source evaluation is missing or corrupt")
        market_differences.append((shared.unit.market_ticker, a.model_brier - b.model_brier))
    return aggregate_market_differences_by_exchange_event(
        tuple(market_differences), manifest, binding_by_market=binding_by_market
    )


def aggregate_market_differences_by_exchange_event(
    market_differences: tuple[tuple[str, Decimal], ...],
    manifest: EventEvidenceManifest,
    *,
    binding_by_market: dict[str, EvaluatedMarketEventBinding] | None = None,
) -> tuple[EventPairedDifference, ...]:
    """Aggregate each declared market exactly once, then equal-weight event rows."""
    bindings = (
        {row.market_ticker: row for row in manifest.bindings}
        if binding_by_market is None
        else binding_by_market
    )
    if len({ticker for ticker, _difference in market_differences}) != len(market_differences):
        raise EventEvidenceError("a market cannot contribute twice to event aggregation")
    grouped: dict[str, list[Decimal]] = defaultdict(list)
    for market_ticker, difference in market_differences:
        binding = bindings.get(market_ticker)
        if binding is None or binding.state not in {
            ExchangeEventIdentityState.PROVEN,
            ExchangeEventIdentityState.UNPROVEN,
        }:
            raise EventEvidenceError("complete cohort lacks a consistent candidate event binding")
        if binding.event_ticker is None:
            raise EventEvidenceError("proven event binding lost its event ticker")
        if not difference.is_finite():
            raise EventEvidenceError("paired market difference must be finite Decimal")
        grouped[binding.event_ticker].append(difference)
    return tuple(
        EventPairedDifference(event, len(values), sum(values, Decimal(0)) / Decimal(len(values)))
        for event, values in sorted(grouped.items())
    )
