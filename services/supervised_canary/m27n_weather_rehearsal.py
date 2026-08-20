"""M27N-W -- operator-only, READ-ONLY Chicago weather execution rehearsal.

This module never arms production, never signs, never sends/cancels/amends/decreases an
order, never touches a credential loader or the real signer, never opens
:class:`services.risk_engine.authorization.AuthorizationStore` or
:class:`services.supervised_canary.store.CanaryStore` (both are SQLite-backed and would
mutate a database file merely by being constructed), and never consumes an M13 authorization,
a canary approval, or the global one-order submission budget. It answers exactly one
question: given already-reviewed, OFFLINE/FIXTURE weather-canary evidence, what is the EXACT
non-secret order request material that the existing, unmodified production execution stack
(:mod:`services.production_execution.requests`/:mod:`services.production_execution.domain`)
would eventually build for this one-contract Chicago weather canary -- stopping strictly
before the credential/signing/transport/mutation boundary
(:mod:`services.production_execution.security_boundary`)?

Design boundary (read before changing anything here):

* Candidate selection/threshold/ranking is delegated entirely to
  :func:`services.supervised_canary.m27d.select_experimental_candidate` -- this module never
  reimplements it, and the Chicago-only weather lane (station ``KMDW``/``USW00014819``, family
  ``POST2020_CHICAGO_MAXT_2P5KM_YGUZ98_03Z``) is enforced there, not here.
* The canonical order/request body is produced exclusively by
  :func:`services.production_execution.requests.create_envelope` -- this module never invents
  a second order schema and never hand-builds a Kalshi request body itself.
* M13 risk, the account snapshot, candidate-specific exposure, and current rules identity are
  all accepted as already-produced OFFLINE FIXTURE evidence (frozen dataclasses), exactly as
  :mod:`services.supervised_canary.m27i` accepts a fresh, already-produced
  ``RiskDecision``/``RiskIntent``/``PortfolioRiskSnapshot`` triple rather than recomputing risk
  math. Unlike M27I, this module never opens a live ``AuthorizationStore`` or ``CanaryStore``:
  both are SQLite-backed and merely constructing one creates/touches a database file, which is
  a persistent-state mutation this milestone must not perform. A caller who wants that
  additional live global-halt/compliance/kill-switch check should run M27I itself, separately,
  before treating a rehearsal as truthful of the current moment.
* This module deliberately does not import
  :mod:`services.supervised_canary.m27i`,
  :mod:`services.supervised_canary.m27j`,
  :mod:`services.supervised_canary.readiness_report`,
  :mod:`services.supervised_canary.candidate_exposure_check`,
  :mod:`services.opportunity_engine.authoritative_economics`, or
  :mod:`services.market_universe.market_snapshot`. Every one of those either opens a live
  store, or its import graph reaches :mod:`services.market_universe.public_read`, which imports
  ``http.client``/``ssl`` -- real network transport modules. This module has zero transport,
  zero credential, and zero signer imports, directly or transitively, so a caller can prove
  that with a plain AST/import scan (see ``tests/test_m27n_weather_execution_rehearsal.py``).
  Facts that those modules would normally supply (current rules identity, M16 account
  readiness) are instead accepted here as independently re-validated OFFLINE FIXTURE evidence.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import fields as dataclass_fields
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from services.forecasting.weather_probability import (
    CLAIM_TYPE,
    SETTLEMENT_MAPPING_STATUS,
    CurrentWeatherForecastEvidence,
    PhysicalTemperatureProxyProbability,
)
from services.forecasting.weather_prospective import FAMILY, FROZEN_MODEL_IDENTITIES
from services.opportunity_engine.books import OutcomeSide
from services.opportunity_engine.domain import OpportunityError
from services.opportunity_engine.live_economics import (
    MarketEconomicsEvidence,
    replay_market_economics,
)
from services.production_execution.domain import ProductionRequestEnvelope
from services.production_execution.requests import create_envelope
from services.risk_engine.authorization import AuthorizationState, RiskAuthorization
from services.risk_engine.domain import (
    EconomicAction,
    PortfolioRiskSnapshot,
    RiskDecision,
    RiskDecisionState,
    RiskIntent,
)
from services.risk_engine.domain import content_hash as risk_content_hash

from .m27d import (
    AUGUST_END,
    AUGUST_START,
    CANARY_STATUS,
    MAX_BOOK_AGE,
    MAX_FORECAST_AGE,
    ONE_CONTRACT,
    CandidateResult,
    CandidateState,
    ExperimentalCandidate,
    select_experimental_candidate,
)

SCHEMA = "kalsh3.m27n.weather-execution-rehearsal.v1"
SOFTWARE_VERSION = "kalsh3.m27n.weather-execution-rehearsal/1"
REHEARSAL_TTL = timedelta(seconds=30)
ENVELOPE_TTL = timedelta(seconds=5)

RULES_FRESHNESS = timedelta(seconds=30)
ACCOUNT_FRESHNESS = timedelta(seconds=30)
EXPOSURE_FRESHNESS = timedelta(seconds=30)

# M13's own real economic-action vocabulary, never a caller-chosen literal.
_ACTION_BY_SIDE = {
    OutcomeSide.YES: EconomicAction.BUY_YES_OUTCOME,
    OutcomeSide.NO: EconomicAction.BUY_NO_OUTCOME,
}

# Legacy M13 time-in-force labels translated to the exact current Kalshi V2 wire vocabulary,
# mirroring (never modifying) the equivalent self-trade-prevention shim already reviewed and
# frozen inside ``services.production_execution.requests.create_envelope``.
_WIRE_TIME_IN_FORCE = {
    "GTC": "good_till_canceled",
    "IOC": "immediate_or_cancel",
    "FOK": "fill_or_kill",
    "good_till_canceled": "good_till_canceled",
    "immediate_or_cancel": "immediate_or_cancel",
    "fill_or_kill": "fill_or_kill",
}

_CandidateInput = tuple[
    PhysicalTemperatureProxyProbability, CurrentWeatherForecastEvidence, MarketEconomicsEvidence
]

GATE_NAMES: tuple[str, ...] = (
    "single_candidate_bound",
    "chicago_lane_bound",
    "model_identity_frozen",
    "forecast_evidence_bound",
    "candidate_identity_bound",
    "rules_identity_current",
    "price_book_current",
    "quantity_is_one",
    "fee_within_bound",
    "account_snapshot_current",
    "no_disqualifying_position",
    "no_unresolved_order",
    "m13_authorization_fresh",
    "m13_authorization_bound",
    "submission_budget_available",
    "clock_safe",
)


class AbstentionReason:
    NO_QUALIFYING_CANDIDATE = "ABSTAIN_NO_QUALIFYING_CANDIDATE"
    MULTIPLE_QUALIFYING_CANDIDATES = "ABSTAIN_MULTIPLE_QUALIFYING_CANDIDATES"


@dataclass(frozen=True, slots=True)
class GateResult:
    passed: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class RehearsalGates:
    results: dict[str, GateResult]

    @property
    def all_pass(self) -> bool:
        return all(result.passed for result in self.results.values())

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(sorted(name for name, result in self.results.items() if not result.passed))

    def to_json(self) -> dict[str, Any]:
        return {
            name: {"pass": result.passed, "reason": result.reason}
            for name, result in self.results.items()
        }


# ---------------------------------------------------------------------------
# Offline fixture evidence -- every field here is caller-supplied, already-produced
# evidence. Nothing in this module acquires any of it over a network or from a live store.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AccountSnapshotFixture:
    """Secret-free, already-produced proof of ordinary M16 account readiness."""

    account_snapshot_version: str
    reconciliation_version: str
    observed_at: datetime
    production_reads_verified: bool
    reconciled: bool
    write_credential_evidence_verified: bool
    signer_runtime_evidence_verified: bool


@dataclass(frozen=True, slots=True)
class CandidateExposureFixture:
    """Secret-free, market-specific proof of no unknown order/position for one ticker."""

    market_ticker: str
    completed_at: datetime
    open_order_count: int
    position_nonzero: bool
    succeeded: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class RulesIdentityFixture:
    """Already-acquired current-side rules identity, compared against the candidate's economics.

    A caller must have independently acquired ``current_rules_hash`` (for example via
    :func:`services.supervised_canary.m27j.acquire_current_market_rules`, run out of band,
    never by this module) before constructing this fixture.
    """

    market_ticker: str
    event_ticker: str
    expected_rules_hash: str
    current_rules_hash: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class M13Fixture:
    """A fresh, already-issued M13 risk triple plus authorization -- never consumed here."""

    authorization: RiskAuthorization
    risk_decision: RiskDecision
    risk_intent: RiskIntent
    risk_snapshot: PortfolioRiskSnapshot
    global_halt_clear: bool
    compliance_clear: bool
    kills_clear: bool


@dataclass(frozen=True, slots=True)
class SubmissionBudgetFixture:
    """Fixture facts about the global one-order canary budget -- never consumed here."""

    write_budget_used: bool
    unresolved_canary_present: bool


# ---------------------------------------------------------------------------
# Output artifact
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WeatherExecutionRehearsal:
    schema: str
    software_version: str
    rehearsal_id: str
    created_at: datetime
    expires_at: datetime
    state: str
    abstain_reason: str | None
    candidate_id: str | None
    ticker: str | None
    event_ticker: str | None
    side: str | None
    action: str | None
    quantity: str | None
    limit_price: str | None
    maximum_fee: str | None
    fee_bound: str | None
    model_identity: str | None
    forecast_evidence_identity: str | None
    economics_evidence_identity: str | None
    rules_hash: str | None
    account_snapshot_identity: str | None
    m13_risk_authorization_identity: str | None
    m13_risk_decision_identity: str | None
    request_method: str | None
    request_path: str | None
    request_origin: str | None
    request_body: dict[str, Any] | None
    request_body_hash: str | None
    request_envelope_content_hash: str | None
    request_execution_id: str | None
    request_client_order_id: str | None
    gates: RehearsalGates
    missing_gates: tuple[str, ...]
    warning: str | None
    content_hash: str = ""

    def __post_init__(self) -> None:
        payload = self._payload_for_hash()
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        object.__setattr__(self, "content_hash", digest)

    def _payload_for_hash(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "software_version": self.software_version,
            "rehearsal_id": self.rehearsal_id,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "state": self.state,
            "abstain_reason": self.abstain_reason,
            "candidate_id": self.candidate_id,
            "ticker": self.ticker,
            "event_ticker": self.event_ticker,
            "side": self.side,
            "action": self.action,
            "quantity": self.quantity,
            "limit_price": self.limit_price,
            "maximum_fee": self.maximum_fee,
            "fee_bound": self.fee_bound,
            "model_identity": self.model_identity,
            "forecast_evidence_identity": self.forecast_evidence_identity,
            "economics_evidence_identity": self.economics_evidence_identity,
            "rules_hash": self.rules_hash,
            "account_snapshot_identity": self.account_snapshot_identity,
            "m13_risk_authorization_identity": self.m13_risk_authorization_identity,
            "m13_risk_decision_identity": self.m13_risk_decision_identity,
            "request_method": self.request_method,
            "request_path": self.request_path,
            "request_origin": self.request_origin,
            "request_body": self.request_body,
            "request_body_hash": self.request_body_hash,
            "request_envelope_content_hash": self.request_envelope_content_hash,
            "request_execution_id": self.request_execution_id,
            "request_client_order_id": self.request_client_order_id,
            "gates": self.gates.to_json(),
            "missing_gates": list(self.missing_gates),
            "warning": self.warning,
        }

    def to_json(self) -> dict[str, Any]:
        return {**self._payload_for_hash(), "content_hash": self.content_hash}

    def is_ready(self) -> bool:
        return self.state == "REHEARSAL_READY"

    def fresh(self, now: datetime) -> bool:
        return self.created_at <= now <= self.expires_at


_REHEARSAL_FIELDS = frozenset(
    {
        "schema",
        "software_version",
        "rehearsal_id",
        "created_at",
        "expires_at",
        "state",
        "abstain_reason",
        "candidate_id",
        "ticker",
        "event_ticker",
        "side",
        "action",
        "quantity",
        "limit_price",
        "maximum_fee",
        "fee_bound",
        "model_identity",
        "forecast_evidence_identity",
        "economics_evidence_identity",
        "rules_hash",
        "account_snapshot_identity",
        "m13_risk_authorization_identity",
        "m13_risk_decision_identity",
        "request_method",
        "request_path",
        "request_origin",
        "request_body",
        "request_body_hash",
        "request_envelope_content_hash",
        "request_execution_id",
        "request_client_order_id",
        "gates",
        "missing_gates",
        "warning",
        "content_hash",
    }
)


@dataclass(frozen=True, slots=True)
class RehearsalValidation:
    valid: bool
    reason: str | None = None


def _canonical_rehearsal_hash(payload: dict[str, Any]) -> str:
    material = {key: value for key, value in payload.items() if key != "content_hash"}
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def validate_rehearsal_artifact(
    payload: object, *, expected_candidate_id: str | None, now: datetime
) -> RehearsalValidation:
    """Independently re-validate a serialized :class:`WeatherExecutionRehearsal` payload.

    Never trusts the serialized ``content_hash``/``state``/``candidate_id`` in isolation --
    recomputes the canonical content hash and rejects any payload whose stored hash does not
    match, whose keys are unexpected, or whose timestamps/candidate binding do not line up.
    """
    if not isinstance(payload, dict):
        return RehearsalValidation(False, "rehearsal artifact payload is not an object")
    if set(payload) != _REHEARSAL_FIELDS:
        return RehearsalValidation(False, "rehearsal artifact has unexpected or missing fields")
    if payload.get("schema") != SCHEMA:
        return RehearsalValidation(False, "rehearsal artifact schema mismatch")
    stored_hash = payload.get("content_hash")
    if not isinstance(stored_hash, str) or not stored_hash:
        return RehearsalValidation(False, "rehearsal artifact content hash missing")
    if _canonical_rehearsal_hash(payload) != stored_hash:
        return RehearsalValidation(
            False, "rehearsal artifact content hash does not match its contents"
        )
    created_at = _parse_timestamp(payload.get("created_at"))
    expires_at = _parse_timestamp(payload.get("expires_at"))
    if created_at is None or expires_at is None:
        return RehearsalValidation(False, "rehearsal artifact timestamps malformed")
    if expires_at <= created_at:
        return RehearsalValidation(False, "rehearsal artifact expiry is not after creation")
    if not (created_at <= now <= expires_at):
        return RehearsalValidation(False, "rehearsal artifact is not fresh at validation time")
    if payload.get("state") not in {"REHEARSAL_READY", "BLOCKED", "ABSTAIN"}:
        return RehearsalValidation(False, "rehearsal artifact state is not recognized")
    if expected_candidate_id is not None and payload.get("candidate_id") != expected_candidate_id:
        return RehearsalValidation(
            False, "rehearsal artifact is not bound to the expected candidate"
        )
    return RehearsalValidation(True, None)


def _parse_timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(UTC)


def _fresh(observed: datetime | None, now: datetime, window: timedelta) -> bool:
    if observed is None:
        return False
    if observed.tzinfo is None or observed.utcoffset() is None:
        return False
    age = now - observed.astimezone(UTC)
    return timedelta(0) <= age <= window


# ---------------------------------------------------------------------------
# Gates -- each independently re-derived from supplied fixture evidence, never trusted
# because a caller's label says so.
# ---------------------------------------------------------------------------


def _model_eligible_gate(candidate: ExperimentalCandidate) -> GateResult:
    eligibility = candidate.eligibility
    reasons: list[str] = []
    if eligibility.status != CANARY_STATUS or eligibility.human_approval_required is not True:
        reasons.append("candidate status is not the frozen supervised experimental canary status")
    if eligibility.claim_type != CLAIM_TYPE:
        reasons.append("candidate claim type is not the frozen GHCN physical-temperature proxy")
    if eligibility.settlement_mapping_status != SETTLEMENT_MAPPING_STATUS:
        reasons.append("candidate settlement mapping is not explicitly unvalidated GHCND proxy")
    if eligibility.model_identity not in FROZEN_MODEL_IDENTITIES.values():
        reasons.append("candidate model identity is not one of the three frozen identities")
    if not (AUGUST_START <= eligibility.target_date <= AUGUST_END):
        reasons.append("candidate target date is outside the August-only canary window")
    return GateResult(not reasons, "; ".join(reasons) if reasons else None)


def _chicago_lane_gate(candidate: ExperimentalCandidate) -> GateResult:
    if candidate.eligibility.source_family != FAMILY:
        return GateResult(False, "candidate is not bound to the frozen Chicago weather lane")
    return GateResult(True, None)


def _candidate_binding_gate(
    candidate: ExperimentalCandidate,
    probability: PhysicalTemperatureProxyProbability,
    forecast: CurrentWeatherForecastEvidence,
    economics: MarketEconomicsEvidence,
) -> tuple[GateResult, GateResult]:
    forecast_reasons: list[str] = []
    if candidate.eligibility.forecast_evidence_identity != forecast.evidence_identity:
        forecast_reasons.append("candidate forecast evidence identity binding mismatch")
    if candidate.eligibility.weather_result_identity != probability.result_identity:
        forecast_reasons.append("candidate weather result identity binding mismatch")
    if candidate.eligibility.model_identity != probability.model_identity:
        forecast_reasons.append("candidate model identity binding mismatch")
    if candidate.eligibility.target_date != forecast.local_target_date:
        forecast_reasons.append("candidate target date binding mismatch")

    candidate_reasons: list[str] = []
    if candidate.economics_evidence_identity != economics.evidence_id:
        candidate_reasons.append("candidate economics evidence identity binding mismatch")
    if candidate.market_ticker != economics.market_ticker:
        candidate_reasons.append("candidate market ticker binding mismatch")
    if candidate.event_ticker != economics.event_ticker:
        candidate_reasons.append("candidate event ticker binding mismatch")
    if candidate.series_ticker != economics.series_ticker:
        candidate_reasons.append("candidate series ticker binding mismatch")
    if economics.requested_quantity != ONE_CONTRACT:
        candidate_reasons.append("candidate economics requested quantity is not exactly one")

    forecast_reason = "; ".join(forecast_reasons) if forecast_reasons else None
    candidate_reason = "; ".join(candidate_reasons) if candidate_reasons else None
    return (
        GateResult(not forecast_reasons, forecast_reason),
        GateResult(not candidate_reasons, candidate_reason),
    )


def _rules_identity_gate(
    rules: RulesIdentityFixture | None,
    candidate: ExperimentalCandidate,
    economics: MarketEconomicsEvidence,
    now: datetime,
) -> GateResult:
    if rules is None:
        return GateResult(False, "no current rules identity evidence supplied")
    ticker_bound = (
        rules.market_ticker == candidate.market_ticker
        and rules.event_ticker == candidate.event_ticker
    )
    if not ticker_bound:
        return GateResult(False, "rules identity evidence is bound to a different market")
    if rules.expected_rules_hash != economics.market_rules_hash:
        return GateResult(False, "rules identity evidence does not match candidate economics")
    if not _fresh(rules.observed_at, now, RULES_FRESHNESS):
        return GateResult(False, "current rules identity evidence is stale")
    if rules.current_rules_hash != rules.expected_rules_hash:
        return GateResult(False, "current live rules identity no longer matches expected hash")
    return GateResult(True, None)


def _price_book_gate(
    candidate: ExperimentalCandidate, economics: MarketEconomicsEvidence, now: datetime
) -> GateResult:
    if not _fresh(economics.orderbook_observed_at, now, MAX_BOOK_AGE):
        return GateResult(False, "orderbook evidence is stale at consumption time")
    try:
        replayed_yes, replayed_no = replay_market_economics(economics)
    except (AttributeError, TypeError, OpportunityError) as exc:
        return GateResult(False, f"economics replay unavailable: {exc}")
    if (replayed_yes, replayed_no) != (economics.yes, economics.no):
        return GateResult(False, "economics evidence failed independent replay consistency")
    cost = economics.yes if candidate.selected_side is OutcomeSide.YES else economics.no
    tradable = (
        cost is not None
        and cost.depth.filled >= ONE_CONTRACT
        and cost.depth.worst_price is not None
        and cost.depth.worst_price == candidate.executable_price
    )
    reason = None if tradable else "current depth or price no longer supports the candidate"
    return GateResult(tradable, reason)


def _fee_gate(candidate: ExperimentalCandidate, maximum_accepted_fee: Decimal) -> GateResult:
    if not (isinstance(maximum_accepted_fee, Decimal) and maximum_accepted_fee.is_finite()):
        return GateResult(False, "accepted fee bound is not a finite Decimal")
    within = candidate.maximum_fee <= maximum_accepted_fee
    return GateResult(within, None if within else "candidate fee exceeds the accepted fee bound")


def _account_snapshot_gate(account: AccountSnapshotFixture | None, now: datetime) -> GateResult:
    if account is None:
        return GateResult(False, "no account snapshot evidence supplied")
    if not _fresh(account.observed_at, now, ACCOUNT_FRESHNESS):
        return GateResult(False, "account snapshot evidence is stale")
    checks = (
        account.production_reads_verified,
        account.reconciled,
        account.write_credential_evidence_verified,
        account.signer_runtime_evidence_verified,
    )
    if not all(checks):
        return GateResult(False, "one or more ordinary M16 readiness facts did not pass")
    return GateResult(True, None)


def _exposure_gates(
    candidate: ExperimentalCandidate, exposure: CandidateExposureFixture | None, now: datetime
) -> tuple[GateResult, GateResult]:
    if exposure is None:
        reason = "no candidate-specific exposure evidence supplied"
        return GateResult(False, reason), GateResult(False, reason)
    if exposure.market_ticker != candidate.market_ticker:
        reason = "candidate exposure evidence is bound to a different market ticker"
        return GateResult(False, reason), GateResult(False, reason)
    if not exposure.succeeded:
        reason = exposure.reason or "candidate exposure evidence did not pass"
        return GateResult(False, reason), GateResult(False, reason)
    if not _fresh(exposure.completed_at, now, EXPOSURE_FRESHNESS):
        reason = "candidate exposure evidence is stale"
        return GateResult(False, reason), GateResult(False, reason)
    orders_ok = exposure.open_order_count == 0
    positions_ok = exposure.position_nonzero is False
    orders_reason = None if orders_ok else "open order exists for this market"
    positions_reason = None if positions_ok else "nonzero position exists for this market"
    return GateResult(orders_ok, orders_reason), GateResult(positions_ok, positions_reason)


def _recompute_domain_hash(obj: Any, *, exclude: frozenset[str]) -> str:
    values = {
        field.name: getattr(obj, field.name)
        for field in dataclass_fields(obj)
        if field.name not in exclude
    }
    return risk_content_hash(values)


def _hash_intact(obj: Any, *, hash_fields: tuple[str, ...]) -> bool:
    digest = _recompute_domain_hash(obj, exclude=frozenset(hash_fields))
    return all(getattr(obj, field) == digest for field in hash_fields)


def _m13_gates(
    candidate: ExperimentalCandidate, m13: M13Fixture | None, now: datetime
) -> tuple[GateResult, GateResult]:
    if m13 is None:
        reason = "no fresh M13 risk authorization/decision/intent/snapshot supplied"
        return GateResult(False, reason), GateResult(False, reason)

    intent, decision, snapshot, authorization = (
        m13.risk_intent,
        m13.risk_decision,
        m13.risk_snapshot,
        m13.authorization,
    )
    freshness_reasons: list[str] = []
    if not _hash_intact(intent, hash_fields=("content_hash",)):
        freshness_reasons.append("risk intent content hash failed independent recomputation")
    if not _hash_intact(snapshot, hash_fields=("content_hash",)):
        freshness_reasons.append("risk snapshot content hash failed independent recomputation")
    if not _hash_intact(decision, hash_fields=("content_hash", "decision_id")):
        freshness_reasons.append("risk decision content hash failed independent recomputation")
    if decision.state != RiskDecisionState.PASS_NEXT_GATE or decision.reasons:
        freshness_reasons.append("risk decision did not cleanly pass")
    decided_at, decision_expires_at = decision.decided_at, decision.expires_at
    if decided_at.tzinfo is None or decision_expires_at.tzinfo is None:
        freshness_reasons.append("risk decision timestamps are not timezone-aware")
    elif not (decided_at <= now <= decision_expires_at):
        freshness_reasons.append("risk decision is outside its own five-second expiry contract")
    if not snapshot.account_fresh:
        freshness_reasons.append("portfolio snapshot reports account data is not fresh")
    if authorization.state != AuthorizationState.ISSUED:
        freshness_reasons.append("M13 authorization is not in the ISSUED state")
    if authorization.expires_at <= now:
        freshness_reasons.append("M13 authorization has expired")
    if not (m13.global_halt_clear and m13.compliance_clear and m13.kills_clear):
        freshness_reasons.append("safety state is not clear at consumption time")
    expected_authorization_id = risk_content_hash(
        (
            authorization.risk_decision_id,
            authorization.intent_hash,
            authorization.safety_state_hash,
            authorization.created_at.isoformat(),
        )
    )
    if authorization.authorization_id != expected_authorization_id:
        freshness_reasons.append("M13 authorization identity failed independent recomputation")

    binding_reasons: list[str] = []
    if decision.intent_hash != intent.content_hash:
        binding_reasons.append("risk decision is not bound to the supplied intent")
    if decision.portfolio_state_hash != snapshot.content_hash:
        binding_reasons.append("risk decision is not bound to the supplied portfolio snapshot")
    if authorization.risk_decision_id != decision.decision_id:
        binding_reasons.append("M13 authorization is not bound to the supplied decision")
    if authorization.intent_hash != intent.content_hash:
        binding_reasons.append("M13 authorization is not bound to the supplied intent")
    if authorization.portfolio_state_hash != snapshot.content_hash:
        binding_reasons.append("M13 authorization is not bound to the supplied portfolio snapshot")
    if authorization.rules_version != intent.rules_version:
        binding_reasons.append("M13 authorization rules version does not match the supplied intent")
    if intent.candidate_id != candidate.candidate_id:
        binding_reasons.append("risk intent is bound to a different candidate")
    if intent.forecast_id != candidate.eligibility.weather_result_identity:
        binding_reasons.append("risk intent forecast binding mismatch")
    if intent.market_ticker != candidate.market_ticker:
        binding_reasons.append("risk intent market binding mismatch")
    if intent.outcome_side != candidate.selected_side.value:
        binding_reasons.append("risk intent side binding mismatch")
    expected_action = _ACTION_BY_SIDE[candidate.selected_side]
    if intent.economic_action != expected_action:
        binding_reasons.append("risk intent economic action binding mismatch")
    if intent.price != candidate.executable_price:
        binding_reasons.append("risk intent price binding mismatch")
    if intent.quantity != ONE_CONTRACT:
        binding_reasons.append("risk intent quantity is not exactly one contract")
    if intent.maximum_loss_if_filled != candidate.maximum_loss:
        binding_reasons.append("risk intent maximum loss binding mismatch")
    if intent.subaccount != 0:
        binding_reasons.append("risk intent is not bound to subaccount 0")

    freshness_reason = "; ".join(freshness_reasons) if freshness_reasons else None
    binding_reason = "; ".join(binding_reasons) if binding_reasons else None
    return (
        GateResult(not freshness_reasons, freshness_reason),
        GateResult(not binding_reasons, binding_reason),
    )


def _submission_budget_gate(budget: SubmissionBudgetFixture | None) -> GateResult:
    if budget is None:
        return GateResult(False, "no submission budget evidence supplied")
    if budget.write_budget_used:
        return GateResult(False, "global one-order canary budget already used")
    if budget.unresolved_canary_present:
        return GateResult(False, "an unresolved canary session already exists")
    return GateResult(True, None)


def _compute_expiry(
    *,
    now: datetime,
    candidate: ExperimentalCandidate,
    forecast: CurrentWeatherForecastEvidence,
    economics: MarketEconomicsEvidence,
    m13: M13Fixture | None,
    account: AccountSnapshotFixture | None,
    exposure: CandidateExposureFixture | None,
    rules: RulesIdentityFixture | None,
) -> datetime:
    deadlines: list[datetime] = [now + REHEARSAL_TTL, candidate.eligibility.expires_at]
    deadlines.append(economics.orderbook_observed_at + MAX_BOOK_AGE)
    deadlines.append(forecast.forecast_reference_time + MAX_FORECAST_AGE)
    if m13 is not None:
        deadlines.append(m13.risk_decision.expires_at)
        deadlines.append(m13.authorization.expires_at)
    if account is not None:
        deadlines.append(account.observed_at + ACCOUNT_FRESHNESS)
    if exposure is not None:
        deadlines.append(exposure.completed_at + EXPOSURE_FRESHNESS)
    if rules is not None:
        deadlines.append(rules.observed_at + RULES_FRESHNESS)
    return min(deadlines)


def _content_hash_hex(material: dict[str, Any]) -> str:
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class RehearsalResult:
    artifact: WeatherExecutionRehearsal


def _abstain_artifact(now: datetime, reason: str) -> WeatherExecutionRehearsal:
    return WeatherExecutionRehearsal(
        schema=SCHEMA,
        software_version=SOFTWARE_VERSION,
        rehearsal_id=_content_hash_hex({"abstain": reason, "created_at": now.isoformat()}),
        created_at=now,
        expires_at=now + REHEARSAL_TTL,
        state="ABSTAIN",
        abstain_reason=reason,
        candidate_id=None,
        ticker=None,
        event_ticker=None,
        side=None,
        action=None,
        quantity=None,
        limit_price=None,
        maximum_fee=None,
        fee_bound=None,
        model_identity=None,
        forecast_evidence_identity=None,
        economics_evidence_identity=None,
        rules_hash=None,
        account_snapshot_identity=None,
        m13_risk_authorization_identity=None,
        m13_risk_decision_identity=None,
        request_method=None,
        request_path=None,
        request_origin=None,
        request_body=None,
        request_body_hash=None,
        request_envelope_content_hash=None,
        request_execution_id=None,
        request_client_order_id=None,
        gates=RehearsalGates({}),
        missing_gates=(),
        warning=None,
    )


def build_rehearsal(
    *,
    now: datetime,
    candidate_inputs: Sequence[_CandidateInput],
    m13: M13Fixture | None = None,
    account_snapshot: AccountSnapshotFixture | None = None,
    candidate_exposure: CandidateExposureFixture | None = None,
    rules_identity: RulesIdentityFixture | None = None,
    submission_budget: SubmissionBudgetFixture | None = None,
    maximum_accepted_fee: Decimal = Decimal("0"),
    expiration: datetime | None = None,
    order_group_id: str | None = None,
) -> RehearsalResult:
    """Deterministically bind one already-valid Chicago weather canary through every pre-send
    invariant and, only if every gate passes, produce the exact canonical order request body
    that :mod:`services.production_execution.requests` would build for it.

    Every input is an OFFLINE FIXTURE: this function performs no network I/O, opens no
    credential or signer, opens no SQLite store, and never calls anything that arms, signs,
    sends, consumes an approval, or consumes the global submission budget.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("rehearsal clock must be timezone-aware")

    candidate_result: CandidateResult = select_experimental_candidate(candidate_inputs, now=now)
    if candidate_result.state is CandidateState.ABSTAIN or candidate_result.selected is None:
        return RehearsalResult(_abstain_artifact(now, AbstentionReason.NO_QUALIFYING_CANDIDATE))
    single_qualifying = (
        len(candidate_result.candidates) == 1
        and candidate_result.selected == candidate_result.candidates[0]
    )
    if not single_qualifying:
        return RehearsalResult(
            _abstain_artifact(now, AbstentionReason.MULTIPLE_QUALIFYING_CANDIDATES)
        )

    candidate = candidate_result.selected
    probability, forecast, economics = next(
        item
        for item in candidate_inputs
        if item[2].evidence_id == candidate.economics_evidence_identity
        and item[1].evidence_identity == candidate.eligibility.forecast_evidence_identity
        and item[0].result_identity == candidate.eligibility.weather_result_identity
    )

    forecast_bound, candidate_bound = _candidate_binding_gate(
        candidate, probability, forecast, economics
    )
    m13_fresh, m13_bound = _m13_gates(candidate, m13, now)
    orders_gate, positions_gate = _exposure_gates(candidate, candidate_exposure, now)

    clock_safe = (
        forecast.forecast_reference_time <= now
        and economics.orderbook_observed_at <= now
        and economics.economics_observed_at <= now
    )

    results: dict[str, GateResult] = {
        "single_candidate_bound": GateResult(True, None),
        "chicago_lane_bound": _chicago_lane_gate(candidate),
        "model_identity_frozen": _model_eligible_gate(candidate),
        "forecast_evidence_bound": forecast_bound,
        "candidate_identity_bound": candidate_bound,
        "rules_identity_current": _rules_identity_gate(rules_identity, candidate, economics, now),
        "price_book_current": _price_book_gate(candidate, economics, now),
        "quantity_is_one": GateResult(Decimal("1.00") == ONE_CONTRACT, None),
        "fee_within_bound": _fee_gate(candidate, maximum_accepted_fee),
        "account_snapshot_current": _account_snapshot_gate(account_snapshot, now),
        "no_disqualifying_position": positions_gate,
        "no_unresolved_order": orders_gate,
        "m13_authorization_fresh": m13_fresh,
        "m13_authorization_bound": m13_bound,
        "submission_budget_available": _submission_budget_gate(submission_budget),
        "clock_safe": GateResult(
            clock_safe, None if clock_safe else "evidence timestamp precedes consumption time"
        ),
    }

    gates = RehearsalGates(results)
    ready = gates.all_pass
    expires_at = _compute_expiry(
        now=now,
        candidate=candidate,
        forecast=forecast,
        economics=economics,
        m13=m13,
        account=account_snapshot,
        exposure=candidate_exposure,
        rules=rules_identity,
    )
    if expires_at <= now:
        ready = False

    envelope: ProductionRequestEnvelope | None = None
    envelope_warning: str | None = None
    if ready:
        assert m13 is not None  # noqa: S101 -- proven by m13_authorization_fresh/bound above
        assert account_snapshot is not None  # noqa: S101
        assert rules_identity is not None  # noqa: S101
        try:
            envelope = _build_envelope(
                now=now,
                candidate=candidate,
                m13=m13,
                expiration=expiration,
                order_group_id=order_group_id,
            )
        except (ValueError, TypeError) as exc:
            ready = False
            envelope_warning = f"envelope construction rejected bound inputs: {exc}"

    state = "REHEARSAL_READY" if ready else "BLOCKED"
    warning = candidate.truth_warning if envelope_warning is None else envelope_warning
    rehearsal_id = _content_hash_hex(
        {
            "candidate_id": candidate.candidate_id,
            "authorization_id": None if m13 is None else m13.authorization.authorization_id,
            "decision_id": None if m13 is None else m13.risk_decision.decision_id,
            "rules_hash": None if rules_identity is None else rules_identity.current_rules_hash,
            "price": str(candidate.executable_price),
            "created_at": now.isoformat(),
        }
    )
    action = _ACTION_BY_SIDE[candidate.selected_side].value
    request_body = json.loads(envelope.canonical_body) if envelope is not None else None

    artifact = WeatherExecutionRehearsal(
        schema=SCHEMA,
        software_version=SOFTWARE_VERSION,
        rehearsal_id=rehearsal_id,
        created_at=now,
        expires_at=expires_at,
        state=state,
        abstain_reason=None,
        candidate_id=candidate.candidate_id,
        ticker=candidate.market_ticker,
        event_ticker=candidate.event_ticker,
        side=candidate.selected_side.value,
        action=action,
        quantity=str(ONE_CONTRACT),
        limit_price=str(candidate.executable_price),
        maximum_fee=str(candidate.maximum_fee),
        fee_bound=str(maximum_accepted_fee),
        model_identity=candidate.eligibility.model_identity,
        forecast_evidence_identity=candidate.eligibility.forecast_evidence_identity,
        economics_evidence_identity=candidate.economics_evidence_identity,
        rules_hash=economics.market_rules_hash,
        account_snapshot_identity=(
            None if account_snapshot is None else account_snapshot.account_snapshot_version
        ),
        m13_risk_authorization_identity=(
            None if m13 is None else m13.authorization.authorization_id
        ),
        m13_risk_decision_identity=None if m13 is None else m13.risk_decision.decision_id,
        request_method=None if envelope is None else envelope.method,
        request_path=None if envelope is None else envelope.path,
        request_origin=None if envelope is None else envelope.origin,
        request_body=request_body,
        request_body_hash=None if envelope is None else envelope.body_hash,
        request_envelope_content_hash=None if envelope is None else envelope.content_hash,
        request_execution_id=None if envelope is None else envelope.execution_id,
        request_client_order_id=None if envelope is None else envelope.client_order_id,
        gates=gates,
        missing_gates=gates.missing,
        warning=warning,
    )
    return RehearsalResult(artifact)


def _build_envelope(
    *,
    now: datetime,
    candidate: ExperimentalCandidate,
    m13: M13Fixture,
    expiration: datetime | None,
    order_group_id: str | None,
) -> ProductionRequestEnvelope:
    intent = m13.risk_intent
    wire_tif = _WIRE_TIME_IN_FORCE.get(intent.time_in_force_policy, intent.time_in_force_policy)
    execution_id = _content_hash_hex(
        {
            "candidate_id": candidate.candidate_id,
            "authorization_id": m13.authorization.authorization_id,
            "decision_id": m13.risk_decision.decision_id,
            "purpose": "m27n-execution-id",
        }
    )
    created_at = now
    return create_envelope(
        execution_id=execution_id,
        authorization_id=m13.authorization.authorization_id,
        decision_id=m13.risk_decision.decision_id,
        intent_hash=intent.content_hash,
        ticker=candidate.market_ticker,
        outcome_side=candidate.selected_side.value,
        price=candidate.executable_price,
        quantity=ONE_CONTRACT,
        tif=wire_tif,
        expiration=expiration,
        post_only=intent.post_only,
        reduce_only=intent.reduce_only,
        cancel_on_pause=intent.cancel_order_on_pause,
        stp=intent.self_trade_prevention,
        order_group_id=order_group_id,
        client_order_id=intent.client_order_id,
        rules_version=intent.rules_version,
        candidate_version=candidate.eligibility.selection_policy_identity,
        portfolio_hash=m13.risk_snapshot.content_hash,
        reconciliation_hash=m13.risk_snapshot.reconciliation_version,
        created_at=created_at,
        expires_at=created_at + ENVELOPE_TTL,
    )


def render_rehearsal(artifact: WeatherExecutionRehearsal) -> str:
    lines = [
        "M27N-W WEATHER EXECUTION REHEARSAL",
        f"schema: {artifact.schema}",
        f"state: {artifact.state}",
        "M27N_REQUEST_TYPE: READ_ONLY",
        "M27N_ARM_ACTION: NONE",
        "M27N_MUTATION: NO",
        "M27N_SIGN_ACTION: NONE",
        "M27N_SEND_ACTION: NONE",
        "M27N_FINAL_ACK_ACTION: NONE",
    ]
    if artifact.state == "ABSTAIN":
        lines.append(f"reason: {artifact.abstain_reason}")
        return "\n".join(lines)
    lines.extend(
        [
            f"ticker: {artifact.ticker}",
            f"side: {artifact.side}",
            f"action: {artifact.action}",
            f"quantity: {artifact.quantity}",
            f"limit_price: {artifact.limit_price}",
            f"maximum_fee: {artifact.maximum_fee}",
            f"model_identity: {artifact.model_identity}",
            f"rehearsal_id: {artifact.rehearsal_id}",
            f"expires_at: {artifact.expires_at.isoformat()}",
        ]
    )
    if artifact.state == "REHEARSAL_READY":
        lines.append(f"request_method: {artifact.request_method}")
        lines.append(f"request_path: {artifact.request_path}")
        lines.append(f"request_body_hash: {artifact.request_body_hash}")
    else:
        lines.append(f"missing_gates: {', '.join(artifact.missing_gates)}")
    return "\n".join(lines)
