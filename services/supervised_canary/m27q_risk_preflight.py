"""M27Q -- pure first-canary M13 preflight risk producer.

This module composes the existing deterministic M13 primitives into the
``RiskIntent`` / ``PortfolioRiskSnapshot`` / ``RiskDecision`` triple that
M27I already consumes and independently re-validates.

It deliberately has no network transport, credential access, signer,
SQLite store, human approval, M13 authorization issuance, execution
authorization, burn, or order capability.

The scope is intentionally narrower than the general M13 engine: it is for
the FIRST supervised weather canary only. Existing account activity is not
priced or modeled here. Any position, order, fill, settlement, prior real
submission/fill, or unresolved canary blocks construction rather than being
interpreted as zero risk.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from services.opportunity_engine.books import OutcomeSide
from services.risk_engine.domain import (
    EconomicAction,
    PortfolioRiskSnapshot,
    ReconciliationStatus,
    RequiredOrderGroupPolicy,
    RiskDecision,
    RiskIntent,
    content_hash,
)
from services.risk_engine.engine import RiskEvaluationContext, evaluate_risk
from services.risk_engine.invariants import (
    CANONICAL_POLICY,
    NewRiskReadiness,
    validate_policy_is_not_weaker,
)
from services.risk_engine.ledger import (
    ExperimentCapitalLedger,
    ExposureProjection,
    available_active_capital,
    project_full_fill,
)
from services.risk_engine.policy import RiskPolicy
from services.risk_engine.states import SafetyState

from .candidate_exposure_check import CandidateExposureEvidence
from .live_read_acceptance import (
    SCHEMA as M27F_SCHEMA,
)
from .live_read_acceptance import (
    USER_DATA_FRESHNESS,
    LiveReadAcceptanceBundle,
)
from .m27d import ONE_CONTRACT, ExperimentalCandidate
from .m27i import compute_account_snapshot_version, compute_reconciliation_version

SOFTWARE_VERSION = "kalsh3.m27q.first-canary-risk-preflight/1"


class M27QRiskError(RuntimeError):
    """First-canary risk material could not be produced safely."""


@dataclass(frozen=True, slots=True)
class FirstCanaryDurableState:
    """Read-only facts that must come from the durable shared canary state."""

    production_state: str
    real_submission_count: int
    real_fill_count: int
    unresolved_canary_present: bool

    def __post_init__(self) -> None:
        for name, value in (
            ("real_submission_count", self.real_submission_count),
            ("real_fill_count", self.real_fill_count),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise M27QRiskError(f"{name} is malformed")
        if not isinstance(self.unresolved_canary_present, bool):
            raise M27QRiskError("unresolved_canary_present is malformed")

    @property
    def pristine(self) -> bool:
        return (
            self.production_state == "DISARMED"
            and self.real_submission_count == 0
            and self.real_fill_count == 0
            and not self.unresolved_canary_present
        )


@dataclass(frozen=True, slots=True)
class RiskContextVersions:
    """Already-validated version identities supplied by the evidence orchestrator."""

    rules_version: str
    rules_hash: str
    contract_interpretation_version: str
    market_data_version: str
    loss_state_version: str
    compliance_state_version: str
    kill_state_version: str

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise M27QRiskError(f"{name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class M27QRiskTriple:
    software_version: str
    intent: RiskIntent
    snapshot: PortfolioRiskSnapshot
    projection: ExposureProjection
    decision: RiskDecision

    @property
    def clean_pass(self) -> bool:
        return self.decision.state.value == "PASS_NEXT_GATE" and not self.decision.reasons


def _aware(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise M27QRiskError(f"{field} must be timezone-aware")
    return value


def _parse_timestamp(value: object, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise M27QRiskError(f"{field} is malformed")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise M27QRiskError(f"{field} is malformed") from exc
    return _aware(parsed, field=field)


def _m27f_read_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    reads = payload.get("reads")
    if not isinstance(reads, list):
        raise M27QRiskError("M27F reads are malformed")

    result: dict[str, dict[str, Any]] = {}
    for item in reads:
        if not isinstance(item, dict):
            raise M27QRiskError("M27F read entry is malformed")
        name = item.get("name")
        if not isinstance(name, str) or name in result:
            raise M27QRiskError("M27F read identity is malformed or duplicated")
        result[name] = item

    required = {"balance", "positions", "orders", "fills", "settlements"}
    if set(result) != required:
        raise M27QRiskError("M27F read set is not exactly the required portfolio sweep")
    return result


def _validate_bundle(
    bundle: LiveReadAcceptanceBundle,
    *,
    now: datetime,
) -> dict[str, Any]:
    evidence = bundle.evidence
    facts = bundle.account_facts

    if facts is None:
        raise M27QRiskError("M27F did not produce transient account facts")

    payload = evidence.to_json()
    if payload.get("schema") != M27F_SCHEMA:
        raise M27QRiskError("M27F evidence schema mismatch")
    if payload.get("environment") != "PRODUCTION" or payload.get("subaccount") != 0:
        raise M27QRiskError("M27F evidence is not production subaccount 0")

    reconciliation = payload.get("reconciliation")
    if not isinstance(reconciliation, dict):
        raise M27QRiskError("M27F reconciliation is malformed")
    if (
        reconciliation.get("classification") != "PASS"
        or reconciliation.get("fresh") is not True
        or reconciliation.get("subaccount_binding_verified") is not True
    ):
        raise M27QRiskError("M27F reconciliation did not pass")

    completed_at = _parse_timestamp(
        payload.get("completed_at"),
        field="M27F completed_at",
    )
    if facts.completed_at != completed_at:
        raise M27QRiskError("transient account facts are not from the exact M27F sweep")
    if completed_at > now or now - completed_at > USER_DATA_FRESHNESS:
        raise M27QRiskError("M27F account facts are stale or future-dated")

    reads = _m27f_read_map(payload)
    if any(read.get("classification") != "SUCCESS" for read in reads.values()):
        raise M27QRiskError("one or more M27F portfolio reads did not succeed")

    if reads["balance"].get("payload_sha256") != facts.balance_payload_sha256:
        raise M27QRiskError("cash facts do not bind to the M27F balance payload")

    expected_counts = {
        "positions": facts.position_count,
        "orders": facts.order_count,
        "fills": facts.fill_count,
        "settlements": facts.settlement_count,
    }
    for name, expected in expected_counts.items():
        if reads[name].get("count") != expected:
            raise M27QRiskError(f"transient {name} count does not bind to the M27F artifact")

    if not facts.pristine_account_activity:
        raise M27QRiskError("first canary requires zero positions, orders, fills, and settlements")

    return payload


def _validate_candidate_exposure(
    exposure: CandidateExposureEvidence,
    candidate: ExperimentalCandidate,
    *,
    now: datetime,
) -> None:
    completed_at = _aware(
        exposure.completed_at,
        field="candidate exposure completed_at",
    )

    if exposure.market_ticker != candidate.market_ticker:
        raise M27QRiskError("candidate exposure ticker mismatch")
    if not exposure.succeeded:
        raise M27QRiskError("candidate exposure check did not pass")
    if exposure.open_order_count != 0:
        raise M27QRiskError("first canary requires zero candidate open orders")
    if exposure.position_nonzero is not False:
        raise M27QRiskError("first canary requires zero candidate position")
    if completed_at > now or now - completed_at > USER_DATA_FRESHNESS:
        raise M27QRiskError("candidate exposure evidence is stale or future-dated")


def _validate_first_canary_state(state: FirstCanaryDurableState) -> None:
    if not state.pristine:
        raise M27QRiskError("durable first-canary state is not pristine and DISARMED")


def _validate_empty_loss_state(
    safety: SafetyState,
    *,
    expected_version: str,
) -> None:
    losses = safety.losses
    if losses.version != expected_version:
        raise M27QRiskError("loss-state version mismatch")
    if any(
        value != Decimal(0)
        for value in (
            losses.daily_loss,
            losses.weekly_loss,
            losses.monthly_loss,
            losses.drawdown,
        )
    ):
        raise M27QRiskError("first canary requires zero prior experiment losses")
    if (
        losses.weekly_review_required
        or losses.monthly_review_required
        or losses.experiment_halt_required
        or losses.reasons
    ):
        raise M27QRiskError("first canary loss state is not pristine")


def _client_order_id(candidate: ExperimentalCandidate) -> str:
    digest = content_hash(
        (
            "m27q-first-canary-client-order-id",
            candidate.candidate_id,
            candidate.market_ticker,
            candidate.selected_side.value,
        )
    )
    return f"kalsh3-v1-m27q-{digest[:20]}"


def build_first_canary_risk_triple(
    *,
    candidate: ExperimentalCandidate,
    m27f_bundle: LiveReadAcceptanceBundle,
    candidate_exposure: CandidateExposureEvidence,
    durable_state: FirstCanaryDurableState,
    readiness: NewRiskReadiness,
    safety: SafetyState,
    order_group: RequiredOrderGroupPolicy,
    versions: RiskContextVersions,
    client_order_id_unique: bool,
    conflicting_bot_order: bool,
    authorization_service_available: bool,
    now: datetime,
    policy: RiskPolicy = CANONICAL_POLICY,
) -> M27QRiskTriple:
    """Build and evaluate M13 risk material without issuing authorization.

    A clean PASS means only ``PASS_NEXT_GATE``. It never authorizes production
    execution and never creates a durable M13 authorization/reservation.
    """
    now = _aware(now, field="risk evaluation now")
    validate_policy_is_not_weaker(policy)

    for name, value in (
        ("client_order_id_unique", client_order_id_unique),
        ("conflicting_bot_order", conflicting_bot_order),
        ("authorization_service_available", authorization_service_available),
    ):
        if not isinstance(value, bool):
            raise M27QRiskError(f"{name} must be bool")

    if not isinstance(readiness, NewRiskReadiness):
        raise M27QRiskError("readiness must be NewRiskReadiness")
    if not isinstance(safety, SafetyState):
        raise M27QRiskError("safety must be SafetyState")
    if not isinstance(order_group, RequiredOrderGroupPolicy):
        raise M27QRiskError("order_group must be RequiredOrderGroupPolicy")

    m27f_payload = _validate_bundle(m27f_bundle, now=now)
    facts = m27f_bundle.account_facts
    if facts is None:  # structurally impossible after _validate_bundle
        raise M27QRiskError("transient account facts disappeared")

    _validate_candidate_exposure(candidate_exposure, candidate, now=now)
    _validate_first_canary_state(durable_state)
    _validate_empty_loss_state(
        safety,
        expected_version=versions.loss_state_version,
    )

    account_snapshot_version = compute_account_snapshot_version(m27f_payload)
    reconciliation_version = compute_reconciliation_version(
        m27f_payload,
        candidate_exposure,
    )

    action = (
        EconomicAction.BUY_YES_OUTCOME
        if candidate.selected_side is OutcomeSide.YES
        else EconomicAction.BUY_NO_OUTCOME
    )

    client_order_id = _client_order_id(candidate)
    intent_id = content_hash(
        (
            "m27q-first-canary-intent",
            candidate.candidate_id,
            account_snapshot_version,
            reconciliation_version,
            client_order_id,
        )
    )

    intent = RiskIntent.freeze(
        intent_id=intent_id,
        created_at=now,
        market_ticker=candidate.market_ticker,
        event_id=candidate.event_ticker,
        correlation_cluster_id=candidate.event_ticker,
        rules_version=versions.rules_version,
        rules_hash=versions.rules_hash,
        contract_interpretation_version=versions.contract_interpretation_version,
        candidate_id=candidate.candidate_id,
        forecast_id=candidate.eligibility.weather_result_identity,
        economic_action=action,
        outcome_side=candidate.selected_side.value,
        book_side="ASK",
        price=candidate.executable_price,
        quantity=ONE_CONTRACT,
        maximum_expected_fee=candidate.maximum_fee,
        maximum_expected_cash_commitment=candidate.maximum_commitment,
        maximum_loss_if_filled=candidate.maximum_loss,
        order_style="LIMIT",
        time_in_force_policy="FILL_OR_KILL",
        expires_at=now + timedelta(seconds=30),
        post_only=False,
        cancel_order_on_pause=True,
        reduce_only=False,
        self_trade_prevention="cancel_newest",
        required_order_group_policy=order_group.policy_id,
        client_order_id=client_order_id,
        account="PRIMARY",
        subaccount=0,
    )

    # First-canary durable/account invariants prove no existing bot or account
    # exposure. The canonical empty experiment ledger therefore has no inferred
    # historical P&L or commitments.
    ledger = ExperimentCapitalLedger.build(
        (),
        starting_capital=policy.active_capital,
    )

    projection = project_full_fill(
        intent=intent,
        current_market_risk=Decimal(0),
        current_event_risk=Decimal(0),
        current_aggregate_risk=Decimal(0),
        existing_resting_market_risk=Decimal(0),
        existing_resting_event_risk=Decimal(0),
        existing_resting_aggregate_risk=Decimal(0),
    )

    account_equity_lower_bound = facts.cash
    active_capital = available_active_capital(
        account_equity=account_equity_lower_bound,
        committed=ledger.experiment_cash_committed,
        pending_commitments=ledger.pending_experiment_orders,
        policy=policy,
    )

    snapshot_observed_at = max(
        facts.completed_at,
        candidate_exposure.completed_at,
    )

    snapshot = PortfolioRiskSnapshot.freeze(
        observed_at=snapshot_observed_at,
        account_snapshot_version=account_snapshot_version,
        reconciliation_version=reconciliation_version,
        cash=facts.cash,
        # Kalshi portfolio_value semantics remain deliberately unvalidated.
        portfolio_value=None,
        # Available cash is used as a conservative lower bound for account equity.
        account_equity=account_equity_lower_bound,
        protected_reserve=policy.protected_reserve,
        active_capital_available=active_capital,
        current_market_risk=Decimal(0),
        current_event_risk=Decimal(0),
        current_aggregate_risk=Decimal(0),
        resting_order_potential_risk=Decimal(0),
        projected_market_risk=projection.market_risk,
        projected_event_risk=projection.event_risk,
        projected_aggregate_risk=projection.aggregate_risk,
        realized_daily_pnl=ledger.experiment_realized_pnl,
        realized_weekly_pnl=ledger.experiment_realized_pnl,
        realized_monthly_pnl=ledger.experiment_realized_pnl,
        experiment_equity=ledger.experiment_equity,
        experiment_high_water_mark=ledger.high_water_mark,
        experiment_drawdown=ledger.drawdown,
        external_positions=0,
        external_orders=0,
        unknown_orders=0,
        account_fresh=True,
        reconciliation_status=ReconciliationStatus.RECONCILED,
        exchange_market_exposure=Decimal(0),
        exchange_event_exposure=Decimal(0),
        independently_calculated_market_exposure=Decimal(0),
        independently_calculated_event_exposure=Decimal(0),
    )

    context = RiskEvaluationContext(
        market_data_version=versions.market_data_version,
        loss_state_version=versions.loss_state_version,
        compliance_state_version=versions.compliance_state_version,
        kill_state_version=versions.kill_state_version,
        expected_rules_hash=versions.rules_hash,
        readiness=readiness,
        safety=safety,
        order_group=order_group,
        client_order_id_unique=client_order_id_unique,
        client_order_id_namespace_valid=True,
        conflicting_bot_order=conflicting_bot_order,
        authorization_service_available=authorization_service_available,
    )

    decision = evaluate_risk(
        intent=intent,
        snapshot=snapshot,
        projection=projection,
        policy=policy,
        context=context,
        now=now,
    )

    return M27QRiskTriple(
        SOFTWARE_VERSION,
        intent,
        snapshot,
        projection,
        decision,
    )
