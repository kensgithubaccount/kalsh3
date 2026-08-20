"""M27N-W -- operator-only CLI for the Chicago weather execution rehearsal.

Prints a deterministic :class:`services.supervised_canary.m27n_weather_rehearsal.
WeatherExecutionRehearsal` artifact built entirely from an OFFLINE, FIXTURE, in-process
weather-canary scenario constructed by this script. There is no network I/O, no credential or
signer access, no SQLite store, and no order/cancel/amend/decrease capability anywhere in this
file -- see ``tests/test_m27n_weather_execution_rehearsal_cli.py`` for the AST/import-capability
proof.

M27N_REQUEST_TYPE: READ_ONLY
M27N_CREDENTIAL_ACCESS: NO
M27N_SIGN_ACTION: NONE
M27N_SEND_ACTION: NONE
M27N_MUTATION: NO
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.forecasting.weather_calibration import ReplayFidelity
from services.forecasting.weather_calibration_coverage import LeadBucket
from services.forecasting.weather_probability import (
    CLAIM_TYPE,
    SETTLEMENT_MAPPING_STATUS,
    CurrentWeatherForecastEvidence,
    PhysicalTemperatureProxyProbability,
)
from services.forecasting.weather_prospective import (
    FAMILY,
    FROZEN_MODEL_IDENTITIES,
    GHCND_STATION,
    SOURCE,
    STATION,
)
from services.market_universe.pricing import PriceLadder
from services.opportunity_engine.books import OutcomeSide
from services.opportunity_engine.fees import current_event_formula_policy
from services.opportunity_engine.live_economics import (
    MarketEconomicsEvidence,
    MarketEconomicsReplayInput,
    normalize_live_orderbook,
    taker_cost,
)
from services.opportunity_engine.live_fees import (
    CurrentSeriesFeeObservation,
    EventFeeOverride,
    resolve_current_fee_regime,
)
from services.risk_engine.authorization import AuthorizationState, RiskAuthorization
from services.risk_engine.domain import (
    EconomicAction,
    PortfolioRiskSnapshot,
    ReconciliationStatus,
    RiskDecision,
    RiskDecisionState,
    RiskIntent,
    content_hash,
)
from services.supervised_canary.m27d import select_experimental_candidate
from services.supervised_canary.m27n_weather_rehearsal import (
    AccountSnapshotFixture,
    CandidateExposureFixture,
    M13Fixture,
    RulesIdentityFixture,
    SubmissionBudgetFixture,
    build_rehearsal,
    render_rehearsal,
)

TARGET_DATE = date(2026, 8, 20)
TICKER = "KXHIGHCHI-26AUG20-B80"
EVENT_TICKER = "KXHIGHCHI-26AUG20"
SERIES_TICKER = "KXHIGHCHI"


def _forecast(now: datetime) -> CurrentWeatherForecastEvidence:
    reference = now - timedelta(minutes=10)
    return CurrentWeatherForecastEvidence(
        evidence_identity="m27n-fixture-forecast-evidence",
        family_identity=FAMILY,
        authority_identity="CLIMDW",
        settlement_product_id=SOURCE,
        nws_station_id=STATION,
        ghcnd_station_id=GHCND_STATION,
        forecast_reference_time=reference,
        interval_start=reference,
        interval_end=reference + timedelta(hours=18),
        midpoint=reference + timedelta(seconds=54_000),
        local_target_date=TARGET_DATE,
        exact_midpoint_seconds=54_000,
        lead_bucket=LeadBucket.ZERO_TO_24H,
        central_kelvin=Decimal("300.0"),
        central_deg_f=Decimal("80.3"),
        record_number=1,
        raw_grib_sha256="a" * 64,
        extraction_sha256="b" * 64,
        extraction_policy_version="m27n-fixture-v1",
        wgrib2_version="m27n-fixture-v1",
        research_only=True,
    )


def _probability(forecast: CurrentWeatherForecastEvidence) -> PhysicalTemperatureProxyProbability:
    return PhysicalTemperatureProxyProbability(
        result_identity="m27n-fixture-weather-result",
        model_identity=FROZEN_MODEL_IDENTITIES[54_000],
        residual_population_identity="m27n-fixture-population",
        route_source_identity="m27n-fixture-route-source",
        route_policy_identity="m27n-fixture-route-policy",
        market_ticker=TICKER,
        event_ticker=EVENT_TICKER,
        series_ticker=SERIES_TICKER,
        current_forecast_evidence_identity=forecast.evidence_identity,
        central_forecast_deg_f=Decimal("80.3"),
        exact_midpoint_seconds=54_000,
        lead_bucket=LeadBucket.ZERO_TO_24H,
        sample_count=547,
        numerator=492,
        denominator=547,
        probability=Decimal("0.90"),
        probability_resolution=Decimal("0.01"),
        intervals=(),
        distribution_min=Decimal("-5"),
        distribution_max=Decimal("5"),
        distribution_mean=Decimal("0"),
        distribution_median=Decimal("0"),
        diagnostic=None,
        replay_fidelity=ReplayFidelity.FORECAST_VINTAGED_CURRENT_OUTCOME_SNAPSHOT,
        settlement_mapping_status=SETTLEMENT_MAPPING_STATUS,
        claim_type=CLAIM_TYPE,
        research_only=True,
    )


def _economics(now: datetime) -> MarketEconomicsEvidence:
    quantity = Decimal("1.00")
    ladder = PriceLadder.parse("deci_cent", [{"start": "0.0000", "end": "1.0000", "step": ".001"}])
    book_raw = {
        "ticker": TICKER,
        "orderbook_fp": {"yes_dollars": [[".300", "5"]], "no_dollars": [[".650", "5"]]},
    }
    observed = normalize_live_orderbook(
        book_raw,
        ticker=TICKER,
        ladder=ladder,
        source_id="m27n-fixture-book",
        observed_at=now,
        market_rules_hash="m27n-fixture-rules-hash",
    )
    series_payload = {
        "ticker": SERIES_TICKER,
        "title": "Chicago high temperature",
        "category": "Weather",
        "frequency": "daily",
        "tags": [],
        "settlement_sources": [],
        "fee_type": "quadratic_with_maker_fees",
        "fee_multiplier": "1",
        "last_updated_ts": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
    }
    series_observation = CurrentSeriesFeeObservation.parse(series_payload, observed_at=now)
    event_override = EventFeeOverride.parse({})
    regime = resolve_current_fee_regime(series_observation, event_override)
    policy = current_event_formula_policy(
        fee_type=regime.fee_type, fee_multiplier=regime.fee_multiplier
    )
    replay_input = MarketEconomicsReplayInput(observed, ladder, regime, policy)
    yes_cost = taker_cost(observed.book, OutcomeSide.YES, quantity, policy)
    no_cost = taker_cost(observed.book, OutcomeSide.NO, quantity, policy)
    economics = MarketEconomicsEvidence.create(
        market_ticker=TICKER,
        event_ticker=EVENT_TICKER,
        series_ticker=SERIES_TICKER,
        market_source_id="m27n-fixture-source",
        market_rules_hash="m27n-fixture-rules-hash",
        market_metadata_hash="m27n-fixture-metadata",
        price_range_hash=observed.price_range_hash,
        event_fee_hash=regime.event_metadata_hash,
        series_fee_observation_id=regime.series_observation_id,
        resolved_fee_regime_id=regime.regime_id,
        fee_policy_id=policy.policy_id,
        orderbook_source_id=observed.source_id,
        orderbook_source_hash=observed.source_hash,
        market_observed_at=now,
        orderbook_observed_at=now,
        economics_observed_at=now,
        requested_quantity=quantity,
        yes=yes_cost,
        no=no_cost,
        replay_input=replay_input,
    )
    return economics


def build_scenario(
    now: datetime, *, risk_intent_overrides: dict[str, object] | None = None
) -> dict[str, object]:
    """Assemble one complete, internally consistent, all-pass OFFLINE fixture scenario.

    ``risk_intent_overrides`` lets a caller (tests only) substitute individual ``RiskIntent``
    fields while keeping every downstream content hash (intent, decision, authorization)
    internally consistent -- a caller who instead used ``dataclasses.replace`` on an already
    frozen, hash-stamped intent would leave a stale ``content_hash`` behind, which is exactly
    the tamper this module's own ``m13_authorization_fresh`` gate is designed to catch.
    """
    forecast = _forecast(now)
    probability = _probability(forecast)
    economics = _economics(now)
    inputs = ((probability, forecast, economics),)

    candidate_result = select_experimental_candidate(inputs, now=now)
    if candidate_result.selected is None:
        return {"inputs": inputs, "candidate": None}
    candidate = candidate_result.selected

    action = (
        EconomicAction.BUY_YES_OUTCOME
        if candidate.selected_side is OutcomeSide.YES
        else EconomicAction.BUY_NO_OUTCOME
    )
    intent_fields: dict[str, object] = dict(
        intent_id="m27n-fixture-intent",
        created_at=now,
        market_ticker=candidate.market_ticker,
        event_id=candidate.event_ticker,
        correlation_cluster_id=candidate.event_ticker,
        rules_version="m27n-fixture-rules-v1",
        rules_hash="m27n-fixture-rules-hash",
        contract_interpretation_version="v1",
        candidate_id=candidate.candidate_id,
        forecast_id=candidate.eligibility.weather_result_identity,
        economic_action=action,
        outcome_side=candidate.selected_side.value,
        book_side="ASK",
        price=candidate.executable_price,
        quantity=Decimal("1.00"),
        maximum_expected_fee=candidate.maximum_fee,
        maximum_expected_cash_commitment=candidate.maximum_commitment,
        maximum_loss_if_filled=candidate.maximum_loss,
        order_style="LIMIT",
        time_in_force_policy="GTC",
        expires_at=now + timedelta(seconds=30),
        post_only=False,
        cancel_order_on_pause=True,
        reduce_only=False,
        self_trade_prevention="CANCEL_NEWEST",
        required_order_group_policy="NONE",
        client_order_id="m27n-fixture-client-order-id",
        account="m27n-fixture-account",
        subaccount=0,
    )
    intent_fields.update(risk_intent_overrides or {})
    intent = RiskIntent.freeze(**intent_fields)
    snapshot = PortfolioRiskSnapshot.freeze(
        observed_at=now,
        account_snapshot_version="m27n-fixture-account-snapshot-v1",
        reconciliation_version="m27n-fixture-reconciliation-v1",
        cash=Decimal("1000"),
        portfolio_value=Decimal("1000"),
        account_equity=Decimal("1000"),
        protected_reserve=Decimal("0"),
        active_capital_available=Decimal("1000"),
        current_market_risk=Decimal("0"),
        current_event_risk=Decimal("0"),
        current_aggregate_risk=Decimal("0"),
        resting_order_potential_risk=Decimal("0"),
        projected_market_risk=Decimal("0"),
        projected_event_risk=Decimal("0"),
        projected_aggregate_risk=Decimal("0"),
        realized_daily_pnl=Decimal("0"),
        realized_weekly_pnl=Decimal("0"),
        realized_monthly_pnl=Decimal("0"),
        experiment_equity=Decimal("0"),
        experiment_high_water_mark=Decimal("0"),
        experiment_drawdown=Decimal("0"),
        external_positions=0,
        external_orders=0,
        unknown_orders=0,
        account_fresh=True,
        reconciliation_status=ReconciliationStatus.RECONCILED,
        exchange_market_exposure=Decimal("0"),
        exchange_event_exposure=Decimal("0"),
        independently_calculated_market_exposure=Decimal("0"),
        independently_calculated_event_exposure=Decimal("0"),
    )
    decision = RiskDecision.freeze(
        state=RiskDecisionState.PASS_NEXT_GATE,
        intent_hash=intent.content_hash,
        risk_policy_version="m27n-fixture-policy-v1",
        portfolio_state_hash=snapshot.content_hash,
        reconciliation_version=snapshot.reconciliation_version,
        rules_version=intent.rules_version,
        market_data_version="m27n-fixture-market-data-v1",
        loss_state_version="m27n-fixture-loss-v1",
        compliance_state_version="m27n-fixture-compliance-v1",
        kill_state_version="m27n-fixture-kill-v1",
        decided_at=now,
        expires_at=now + timedelta(seconds=5),
        reasons=(),
        display_result="RISK CHECK PASSED",
        production_write_authorized=False,
    )
    authorization_id = content_hash(
        (decision.decision_id, intent.content_hash, "m27n-fixture-safety-state", now.isoformat())
    )
    authorization = RiskAuthorization(
        authorization_id,
        decision.decision_id,
        intent.content_hash,
        snapshot.content_hash,
        "m27n-fixture-policy-v1",
        intent.rules_version,
        "m27n-fixture-safety-state",
        now,
        now + timedelta(seconds=5),
        AuthorizationState.ISSUED,
    )
    m13 = M13Fixture(
        authorization=authorization,
        risk_decision=decision,
        risk_intent=intent,
        risk_snapshot=snapshot,
        global_halt_clear=True,
        compliance_clear=True,
        kills_clear=True,
    )
    account_snapshot = AccountSnapshotFixture(
        account_snapshot_version="m27n-fixture-account-snapshot-v1",
        reconciliation_version="m27n-fixture-reconciliation-v1",
        observed_at=now,
        production_reads_verified=True,
        reconciled=True,
        write_credential_evidence_verified=True,
        signer_runtime_evidence_verified=True,
    )
    exposure = CandidateExposureFixture(
        market_ticker=candidate.market_ticker,
        completed_at=now,
        open_order_count=0,
        position_nonzero=False,
        succeeded=True,
        reason=None,
    )
    rules_identity = RulesIdentityFixture(
        market_ticker=candidate.market_ticker,
        event_ticker=candidate.event_ticker,
        expected_rules_hash=economics.market_rules_hash,
        current_rules_hash=economics.market_rules_hash,
        observed_at=now,
    )
    submission_budget = SubmissionBudgetFixture(
        write_budget_used=False, unresolved_canary_present=False
    )
    return {
        "inputs": inputs,
        "candidate": candidate,
        "m13": m13,
        "account_snapshot": account_snapshot,
        "candidate_exposure": exposure,
        "rules_identity": rules_identity,
        "submission_budget": submission_budget,
        "maximum_accepted_fee": candidate.maximum_fee,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "M27N-W read-only Chicago weather execution rehearsal. Builds one deterministic, "
            "internally consistent OFFLINE fixture scenario and prints the exact non-secret "
            "order request material the existing production execution stack would eventually "
            "build for it. No network, no credentials, no signer, no mutation."
        )
    )
    parser.add_argument(
        "--now",
        type=str,
        default=None,
        help="ISO-8601 UTC timestamp to evaluate at (default: a fixed fixture instant)",
    )
    args = parser.parse_args(argv)
    now = datetime.fromisoformat(args.now) if args.now else datetime(2026, 8, 20, 12, tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    scenario = build_scenario(now)
    result = build_rehearsal(
        now=now,
        candidate_inputs=scenario["inputs"],  # type: ignore[arg-type]
        m13=scenario.get("m13"),  # type: ignore[arg-type]
        account_snapshot=scenario.get("account_snapshot"),  # type: ignore[arg-type]
        candidate_exposure=scenario.get("candidate_exposure"),  # type: ignore[arg-type]
        rules_identity=scenario.get("rules_identity"),  # type: ignore[arg-type]
        submission_budget=scenario.get("submission_budget"),  # type: ignore[arg-type]
        maximum_accepted_fee=scenario.get("maximum_accepted_fee", Decimal("0")),  # type: ignore[arg-type]
    )
    artifact = result.artifact
    print(render_rehearsal(artifact))
    print(json.dumps(artifact.to_json(), indent=2, sort_keys=True))
    print(
        "M27N_REQUEST_TYPE: READ_ONLY  M27N_CREDENTIAL_ACCESS: NO  M27N_SIGN_ACTION: NONE  "
        "M27N_SEND_ACTION: NONE  M27N_MUTATION: NO"
    )
    return 0 if artifact.is_ready() else 2


if __name__ == "__main__":
    raise SystemExit(main())
