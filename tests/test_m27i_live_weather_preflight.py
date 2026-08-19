from __future__ import annotations

import ast
import base64
import hashlib
import json
import subprocess
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from services.forecasting.weather_probability import physical_temperature_proxy_probability
from services.forecasting.weather_prospective import FROZEN_MODEL_IDENTITIES
from services.market_universe import public_read
from services.market_universe.domain import Market
from services.market_universe.market_snapshot import acquire_market_snapshot
from services.market_universe.pricing import PriceLadder
from services.opportunity_engine.authoritative_economics import (
    AuthoritativeMarketEconomicsBinding,
    build_authoritative_market_economics,
)
from services.opportunity_engine.books import OutcomeSide, walk_depth
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
from services.risk_engine.authorization import AuthorizationStore, FixedClock
from services.risk_engine.domain import (
    ComplianceState,
    EconomicAction,
    KillCategory,
    KillLevel,
    PortfolioRiskSnapshot,
    ReconciliationStatus,
    RiskDecision,
    RiskDecisionState,
    RiskIntent,
)
from services.risk_engine.states import KillState
from services.supervised_canary import m27d, m27i
from services.supervised_canary.candidate_exposure_check import CandidateExposureEvidence
from services.supervised_canary.store import CanaryStore
from tests.test_m27c_weather_probability import chicago_route, current, population

TARGET_DATE = date(2026, 8, 31)


def _weather(*, interval_end: datetime | None = None) -> tuple[object, object]:
    base = current()
    forecast = replace(base, local_target_date=TARGET_DATE)
    if interval_end is not None:
        forecast = replace(forecast, interval_end=interval_end)
    probability = replace(
        physical_temperature_proxy_probability(
            route=chicago_route("greater", 80, None),
            population=population(),
            current=base,
        ),
        probability=Decimal("0.90"),
        model_identity=FROZEN_MODEL_IDENTITIES[54_000],
        current_forecast_evidence_identity=forecast.evidence_identity,
        exact_midpoint_seconds=54_000,
        market_ticker="M",
        event_ticker="E",
        series_ticker="CLIMDW",
        diagnostic=None,
    )
    return probability, forecast


def _series_payload(now: datetime, *, fee_multiplier: str = "1") -> dict[str, object]:
    return {
        "ticker": "S",
        "title": "T",
        "category": "Weather",
        "frequency": "daily",
        "tags": [],
        "settlement_sources": [],
        "fee_type": "quadratic_with_maker_fees",
        "fee_multiplier": fee_multiplier,
        "last_updated_ts": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
    }


def _economics(
    *,
    now: datetime,
    quantity: Decimal = Decimal("1.00"),
    ticker: str = "M",
    price: str = ".300",
    no_price: str = ".650",
    market_rules_hash: str = "rules",
) -> tuple[MarketEconomicsEvidence, CurrentSeriesFeeObservation, EventFeeOverride, object]:
    ladder = PriceLadder.parse("deci_cent", [{"start": "0.0000", "end": "1.0000", "step": ".001"}])
    book_raw = {
        "ticker": ticker,
        "orderbook_fp": {"yes_dollars": [[price, "5"]], "no_dollars": [[no_price, "5"]]},
    }
    observed = normalize_live_orderbook(
        book_raw,
        ticker=ticker,
        ladder=ladder,
        source_id=f"snapshot-{ticker}",
        observed_at=now,
        market_rules_hash=market_rules_hash,
    )
    series_observation = CurrentSeriesFeeObservation.parse(_series_payload(now), observed_at=now)
    event = EventFeeOverride.parse({})
    regime = resolve_current_fee_regime(series_observation, event)
    policy = current_event_formula_policy(
        fee_type=regime.fee_type, fee_multiplier=regime.fee_multiplier
    )
    replay_input = MarketEconomicsReplayInput(observed, ladder, regime, policy)

    def side(outcome: OutcomeSide):
        asks = observed.book.yes_asks if outcome is OutcomeSide.YES else observed.book.no_asks
        if not walk_depth(asks, quantity).complete:
            return None
        return taker_cost(observed.book, outcome, quantity, policy)

    values = dict(
        market_ticker=ticker,
        event_ticker="E" if ticker == "M" else f"E-{ticker}",
        series_ticker="CLIMDW",
        market_source_id="market-source",
        market_rules_hash=market_rules_hash,
        market_metadata_hash="metadata",
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
        yes=side(OutcomeSide.YES),
        no=side(OutcomeSide.NO),
        replay_input=replay_input,
    )
    return MarketEconomicsEvidence.create(**values), series_observation, event, regime


def _m27f_payload(now: datetime, *, completed: datetime | None = None) -> dict[str, object]:
    completed = completed or (now - timedelta(seconds=5))
    stamp = completed.isoformat()
    reads = [
        {
            "name": name,
            "classification": "SUCCESS",
            "started_at": stamp,
            "completed_at": stamp,
            "count": 0,
            "pagination_complete": True,
            "payload_sha256": f"sha-{name}",
            "reason": None,
        }
        for name in ("balance", "positions", "orders", "fills", "settlements")
    ]
    return {
        "schema": "kalsh3.m27f.live-read-acceptance.v3",
        "software_version": "kalsh3.m27f.live-read-acceptance/3",
        "environment": "PRODUCTION",
        "subaccount": 0,
        "key_id_hash": "a" * 64,
        "started_at": stamp,
        "completed_at": stamp,
        "candidate_authority": {
            "classification": "PASS",
            "key_id_hash": "a" * 64,
            "server_scopes": ["read", "write::trade"],
            "server_subaccount": 0,
            "started_at": stamp,
            "completed_at": stamp,
            "source": "EXTERNAL_SERVER_ATTESTATION",
            "reason": None,
        },
        "reads": reads,
        "reconciliation": {
            "classification": "PASS",
            "balance_succeeded": True,
            "open_orders_complete": True,
            "positions_complete": True,
            "fills_complete": True,
            "settlements_complete": True,
            "subaccount_binding_verified": True,
            "fresh": True,
            "reason": None,
        },
    }


def _m27h_payload(now: datetime, *, completed: datetime | None = None) -> dict[str, object]:
    completed = completed or (now - timedelta(seconds=3))
    stamp = completed.isoformat()
    return {
        "schema": "kalsh3.m27h.installed-write-credential.v1",
        "software_version": "x",
        "environment": "PRODUCTION",
        "observed_at": stamp,
        "completed_at": stamp,
        "store_state": "COMMITTED",
        "key_id_hash": "b" * 64,
        "credential_fingerprint": "fp",
        "authority_classification": "PASS",
        "authority_reason": None,
        "signer_classification": "PASS",
        "signer_challenge_domain": "domain",
        "signer_reason": None,
        "signer_completed_at": stamp,
        "classification": "PASS",
        "reason": None,
    }


def _public_payload(
    now: datetime,
    candidate: object,
    *,
    trading_active: bool = True,
    exchange_active: bool = True,
    market_status: str = "open",
    market_ticker: str | None = None,
    event_ticker: str | None = None,
    observed_at: datetime | None = None,
) -> dict[str, object]:
    observed_at = observed_at or now
    stamp = observed_at.isoformat()
    return {
        "schema": "kalsh3.m27e.public-read.v1",
        "host": "https://external-api.kalshi.com",
        "started_at": stamp,
        "exchange_status": {
            "path": "/trade-api/v2/exchange/status",
            "observed_at": stamp,
            "status": 200,
            "body_sha256": "e" * 64,
            "bytes": 10,
            "classification": "SUCCESS",
            "payload": {"trading_active": trading_active, "exchange_active": exchange_active},
        },
        "series": {
            "path": "/trade-api/v2/series/CLIMDW",
            "observed_at": stamp,
            "status": 200,
            "body_sha256": "f" * 64,
            "bytes": 10,
            "classification": "SUCCESS",
            "payload": {},
        },
        "markets": {
            "classification": "SUCCESS",
            "pagination_complete": True,
            "market_count": 1,
            "pages": [
                {
                    "path": "/trade-api/v2/markets?series_ticker=CLIMDW&status=open&limit=1000",
                    "observed_at": stamp,
                    "status": 200,
                    "body_sha256": "g" * 64,
                    "bytes": 10,
                    "classification": "SUCCESS",
                    "payload": {
                        "markets": [
                            {
                                "ticker": market_ticker or candidate.market_ticker,
                                "event_ticker": event_ticker or candidate.event_ticker,
                                "status": market_status,
                            }
                        ],
                        "cursor": "",
                    },
                }
            ],
        },
    }


def _raw_market(
    ticker: str = "M", event_ticker: str = "E", **overrides: object
) -> dict[str, object]:
    raw: dict[str, object] = {
        "ticker": ticker,
        "event_ticker": event_ticker,
        "market_type": "binary",
        "status": "active",
        "rules_primary": "YES if the measured value is at least 80 units.",
        "rules_secondary": "Use the final published report.",
        "settlement_sources": [{"name": "NWS"}],
        "strike_type": "greater",
        "floor_strike": "80",
        "cap_strike": None,
        "functional_strike": None,
        "custom_strike": None,
        "early_close_condition": "none",
        "price_level_structure": "linear_cent",
        "title": "Will the measured value be at least 80 units?",
        "is_provisional": False,
        "volume_fp": "0",
        "open_interest_fp": "0",
    }
    raw.update(overrides)
    return raw


def _snapshot_transport(raw_market: dict[str, object], *, observed_at: datetime, status: int = 200):
    body = json.dumps({"market": raw_market}, sort_keys=True).encode()

    def transport(ticker: str) -> tuple[dict[str, object], bytes]:
        evidence: dict[str, object] = {
            "path": f"{public_read.BASE}/markets/{ticker}",
            "observed_at": observed_at.isoformat(),
            "status": status,
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "bytes": len(body),
            "classification": "SUCCESS" if status == 200 else "HTTP_OR_NETWORK_FAILURE",
        }
        if status == 200:
            evidence["payload"] = json.loads(body)
        return evidence, body

    return transport


def _snapshot_payload(
    now: datetime,
    *,
    ticker: str = "M",
    raw_market: dict[str, object] | None = None,
    observed_at: datetime | None = None,
) -> dict[str, object]:
    """Build a serialized ``AuthoritativeMarketSnapshot`` -- the shared shape used by both the
    current side (M27J) and the expected side (embedded in an M27A authoritative binding)."""
    raw_market = raw_market if raw_market is not None else _raw_market(ticker)
    observed_at = observed_at or now
    snapshot = acquire_market_snapshot(
        ticker,
        clock=lambda: observed_at,
        transport=_snapshot_transport(raw_market, observed_at=observed_at),
    )
    assert snapshot.succeeded, snapshot.reason
    return snapshot.to_json()


def _authoritative_economics(
    now: datetime,
    *,
    ticker: str = "M",
    event_ticker: str = "E",
    quantity: Decimal = Decimal("1.00"),
    price: str = ".300",
    no_price: str = ".650",
    raw_market: dict[str, object] | None = None,
    snapshot_observed_at: datetime | None = None,
    economics_observed_at: datetime | None = None,
) -> tuple[
    MarketEconomicsEvidence,
    AuthoritativeMarketEconomicsBinding,
    dict[str, object],
    CurrentSeriesFeeObservation,
    EventFeeOverride,
    object,
]:
    """Build economics through the AUTHORITATIVE path (never the legacy caller-supplied-hash
    path): derives ``market_rules_hash`` exclusively from an independently-validated
    ``AuthoritativeMarketSnapshot``."""
    raw_market = raw_market if raw_market is not None else _raw_market(ticker, event_ticker)
    economics_observed_at = economics_observed_at or now
    snapshot_payload = _snapshot_payload(
        now, ticker=ticker, raw_market=raw_market, observed_at=snapshot_observed_at
    )
    ladder = PriceLadder.parse("deci_cent", [{"start": "0.0000", "end": "1.0000", "step": ".001"}])
    book_raw = {
        "ticker": ticker,
        "orderbook_fp": {"yes_dollars": [[price, "5"]], "no_dollars": [[no_price, "5"]]},
    }
    series_observation = CurrentSeriesFeeObservation.parse(_series_payload(now), observed_at=now)
    event = EventFeeOverride.parse({})
    regime = resolve_current_fee_regime(series_observation, event)
    policy = current_event_formula_policy(
        fee_type=regime.fee_type, fee_multiplier=regime.fee_multiplier
    )
    economics, binding = build_authoritative_market_economics(
        snapshot_payload=snapshot_payload,
        expected_market_ticker=ticker,
        expected_event_ticker=event_ticker,
        series_ticker="CLIMDW",
        market_source_id="market-source",
        raw_orderbook=book_raw,
        ladder=ladder,
        orderbook_source_id=f"snapshot-{ticker}",
        orderbook_observed_at=now,
        series_fee_observation_id=regime.series_observation_id,
        resolved_fee_regime_id=regime.regime_id,
        event_fee_hash=regime.event_metadata_hash,
        fee_policy=policy,
        fee_regime=regime,
        requested_quantity=quantity,
        economics_observed_at=economics_observed_at,
    )
    return economics, binding, snapshot_payload, series_observation, event, regime


def _exposure(
    market_ticker: str,
    now: datetime,
    *,
    open_orders: int = 0,
    position_nonzero: bool = False,
    completed: datetime | None = None,
) -> CandidateExposureEvidence:
    completed = completed or (now - timedelta(seconds=2))
    return CandidateExposureEvidence(
        schema="kalsh3.m27i.candidate-exposure.v1",
        software_version="x",
        market_ticker=market_ticker,
        started_at=completed,
        completed_at=completed,
        orders_classification="SUCCESS",
        positions_classification="SUCCESS",
        open_order_count=open_orders,
        position_nonzero=position_nonzero,
        classification="PASS",
        reason=None,
    )


def _risk_intent(candidate: object, now: datetime) -> RiskIntent:
    action = (
        EconomicAction.BUY_YES_OUTCOME
        if candidate.selected_side is OutcomeSide.YES
        else EconomicAction.BUY_NO_OUTCOME
    )
    return RiskIntent.freeze(
        intent_id="intent-1",
        created_at=now,
        market_ticker=candidate.market_ticker,
        event_id=candidate.event_ticker,
        correlation_cluster_id=candidate.event_ticker,
        rules_version="v1",
        rules_hash="rules",
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
        client_order_id="kalsh3-v1-abcdefgh",
        account="acct",
        subaccount=0,
    )


def _risk_snapshot(
    m27f_payload: dict[str, object],
    exposure: CandidateExposureEvidence,
    now: datetime,
    *,
    account_snapshot_version: str | None = None,
    reconciliation_version: str | None = None,
    observed_at: datetime | None = None,
    account_fresh: bool = True,
) -> PortfolioRiskSnapshot:
    zero = Decimal("0")
    return PortfolioRiskSnapshot.freeze(
        observed_at=observed_at or now,
        account_snapshot_version=account_snapshot_version
        or m27i.compute_account_snapshot_version(m27f_payload),
        reconciliation_version=reconciliation_version
        or m27i.compute_reconciliation_version(m27f_payload, exposure),
        cash=Decimal("1000"),
        portfolio_value=Decimal("1000"),
        account_equity=Decimal("1000"),
        protected_reserve=zero,
        active_capital_available=Decimal("1000"),
        current_market_risk=zero,
        current_event_risk=zero,
        current_aggregate_risk=zero,
        resting_order_potential_risk=zero,
        projected_market_risk=zero,
        projected_event_risk=zero,
        projected_aggregate_risk=zero,
        realized_daily_pnl=zero,
        realized_weekly_pnl=zero,
        realized_monthly_pnl=zero,
        experiment_equity=zero,
        experiment_high_water_mark=zero,
        experiment_drawdown=zero,
        external_positions=0,
        external_orders=0,
        unknown_orders=0,
        account_fresh=account_fresh,
        reconciliation_status=ReconciliationStatus.RECONCILED,
        exchange_market_exposure=zero,
        exchange_event_exposure=zero,
        independently_calculated_market_exposure=zero,
        independently_calculated_event_exposure=zero,
    )


def _risk_decision(
    intent: RiskIntent,
    snapshot: PortfolioRiskSnapshot,
    now: datetime,
    *,
    state: RiskDecisionState = RiskDecisionState.PASS_NEXT_GATE,
    reasons: tuple = (),
    decided_at: datetime | None = None,
) -> RiskDecision:
    decided_at = decided_at or now
    return RiskDecision.freeze(
        state=state,
        intent_hash=intent.content_hash,
        risk_policy_version="1",
        portfolio_state_hash=snapshot.content_hash,
        reconciliation_version=snapshot.reconciliation_version,
        rules_version=intent.rules_version,
        market_data_version="v1",
        loss_state_version="initial",
        compliance_state_version="v1",
        kill_state_version="v1",
        decided_at=decided_at,
        expires_at=decided_at + timedelta(seconds=5),
        reasons=reasons,
        display_result="RISK CHECK PASSED"
        if state is RiskDecisionState.PASS_NEXT_GATE
        else "RISK CHECK FAILED",
        production_write_authorized=False,
    )


class Context:
    def __init__(self, tmp_path: Path) -> None:
        probability, forecast = _weather()
        self.now = forecast.forecast_reference_time + timedelta(seconds=10)
        self.raw_market = _raw_market()
        (
            economics,
            binding,
            expected_snapshot_payload,
            series_observation,
            event_override,
            regime,
        ) = _authoritative_economics(self.now, raw_market=self.raw_market)
        self.probability, self.forecast, self.economics = probability, forecast, economics
        self.binding = binding
        self.expected_snapshot_payload = expected_snapshot_payload
        self.series_observation = series_observation
        self.event_override = event_override
        self.fee_regime = regime
        self.inputs = ((probability, forecast, economics),)

        result = m27d.select_experimental_candidate(self.inputs, now=self.now)
        assert result.state is m27d.CandidateState.QUALIFYING_EXPERIMENTAL_CANARY
        self.candidate = result.selected

        self.m27f_payload = _m27f_payload(self.now)
        self.m27f_path = tmp_path / "m27f.json"
        self.m27f_path.write_text(json.dumps(self.m27f_payload))
        self.m27h_payload = _m27h_payload(self.now)
        self.m27h_path = tmp_path / "m27h.json"
        self.m27h_path.write_text(json.dumps(self.m27h_payload))
        self.public_path = tmp_path / "public.json"
        self.public_path.write_text(json.dumps(_public_payload(self.now, self.candidate)))
        self.m27j_payload = _snapshot_payload(self.now, raw_market=self.raw_market)
        self.m27j_path = tmp_path / "m27j.json"
        self.m27j_path.write_text(json.dumps(self.m27j_payload))
        self.m27a_binding_payload = self.binding.to_json()
        self.m27a_binding_path = tmp_path / "m27a_binding.json"
        self.m27a_binding_path.write_text(json.dumps(self.m27a_binding_payload))

        self.exposure = _exposure(self.candidate.market_ticker, self.now)

        self.canary_store = CanaryStore(tmp_path / "canary.sqlite")

        self.authorization_store = AuthorizationStore(
            tmp_path / "risk.sqlite", FixedClock(self.now)
        )
        self.authorization_store.set_compliance(
            ComplianceState.CLEAR, actor="OPERATOR", reason="testing"
        )

        self.risk_intent = _risk_intent(self.candidate, self.now)
        self.risk_snapshot = _risk_snapshot(self.m27f_payload, self.exposure, self.now)
        self.risk_decision = _risk_decision(self.risk_intent, self.risk_snapshot, self.now)

    def kwargs(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = dict(
            now=self.now,
            candidate_inputs=self.inputs,
            m27f_evidence_path=self.m27f_path,
            m27h_evidence_path=self.m27h_path,
            public_evidence_path=self.public_path,
            current_series_fee_observation=self.series_observation,
            current_event_fee_override=self.event_override,
            current_event_fee_observed_at=self.now,
            candidate_exposure=self.exposure,
            risk_decision=self.risk_decision,
            risk_intent=self.risk_intent,
            risk_snapshot=self.risk_snapshot,
            authorization_store=self.authorization_store,
            canary_store=self.canary_store,
        )
        base.update(overrides)
        return base


def _rewrite_public_path(ctx: Context, payload: dict[str, object]) -> Path:
    path = ctx.public_path.parent / "public_override.json"
    path.write_text(json.dumps(payload))
    return path


def _rewrite_m27j_path(ctx: Context, payload: dict[str, object]) -> Path:
    path = ctx.m27j_path.parent / "m27j_override.json"
    path.write_text(json.dumps(payload))
    return path


def _rewrite_binding_path(ctx: Context, payload: dict[str, object]) -> Path:
    path = ctx.m27a_binding_path.parent / "m27a_binding_override.json"
    path.write_text(json.dumps(payload))
    return path


# ---------------------------------------------------------------------------
# Happy path / abstention
# ---------------------------------------------------------------------------


def test_otherwise_perfect_happy_path_is_blocked_solely_on_rules_current(tmp_path: Path) -> None:
    """Gemini FINAL delta repair: no reviewed authority for current rules identity exists.

    Every other M27I gate can honestly pass on a fresh, internally-consistent synthetic
    fixture -- but the overall result must still be BLOCKED, and the *only* reason must be
    ``rules_current``. Market existence/status and book executability remain separately
    provable and must both PASS; they must never be aliased into rules currentness.
    """
    ctx = Context(tmp_path)
    result = m27i.build_preflight(**ctx.kwargs())
    artifact = result.artifact
    assert artifact.state == "BLOCKED", artifact.gates.missing
    assert artifact.missing_gates == ("rules_current",)
    assert artifact.gates.results["market_open_current"].passed
    assert artifact.gates.results["book_executable"].passed
    assert artifact.gates.results["market_tradable"].passed
    rules_current = artifact.gates.results["rules_current"]
    assert not rules_current.passed
    assert rules_current.reason == m27i.NO_AUTHORITATIVE_CURRENT_RULES_IDENTITY
    assert artifact.candidate_id == ctx.candidate.candidate_id
    assert artifact.market_ticker == "M"
    assert artifact.warning is not None and "UNVALIDATED" in artifact.warning
    assert artifact.fresh(ctx.now)
    rendered = m27i.render_preflight(artifact)
    assert "MARKET OPEN/CURRENT: PASS" in rendered
    assert "BOOK EXECUTABLE: PASS" in rendered
    assert "RULES CURRENTNESS: BLOCKED" in rendered
    assert "PREFLIGHT_READY" not in rendered


def test_zero_supported_open_markets_abstains(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    result = m27i.build_preflight(**ctx.kwargs(candidate_inputs=()))
    assert result.artifact.state == "ABSTAIN"
    assert result.artifact.abstain_reason == m27i.AbstentionReason.NO_SUPPORTED_OPEN_MARKET


def test_september_target_abstains(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    forecast = replace(ctx.forecast, local_target_date=date(2026, 9, 1))
    inputs = ((ctx.probability, forecast, ctx.economics),)
    result = m27i.build_preflight(**ctx.kwargs(candidate_inputs=inputs))
    assert result.artifact.state == "ABSTAIN"
    assert result.artifact.abstain_reason == m27i.AbstentionReason.OUTSIDE_TARGET_WINDOW


def test_stale_forecast_abstains(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    forecast = replace(
        ctx.forecast, forecast_reference_time=ctx.now - timedelta(hours=2, minutes=1)
    )
    probability = replace(
        ctx.probability, current_forecast_evidence_identity=forecast.evidence_identity
    )
    inputs = ((probability, forecast, ctx.economics),)
    result = m27i.build_preflight(**ctx.kwargs(candidate_inputs=inputs))
    assert result.artifact.state == "ABSTAIN"


def test_boundary_probability_abstains(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    probability = replace(ctx.probability, probability=Decimal("1"))
    inputs = ((probability, ctx.forecast, ctx.economics),)
    result = m27i.build_preflight(**ctx.kwargs(candidate_inputs=inputs))
    assert result.artifact.state == "ABSTAIN"


def test_discrepancy_below_threshold_abstains(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    probability = replace(ctx.probability, probability=Decimal("0.31"))
    inputs = ((probability, ctx.forecast, ctx.economics),)
    result = m27i.build_preflight(**ctx.kwargs(candidate_inputs=inputs))
    assert result.artifact.state == "ABSTAIN"


def test_unsupported_model_identity_abstains(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    forecast = replace(ctx.forecast)
    probability = replace(ctx.probability, model_identity="bogus-unsupported-model-identity")
    inputs = ((probability, forecast, ctx.economics),)
    result = m27i.build_preflight(**ctx.kwargs(candidate_inputs=inputs))
    assert result.artifact.state == "ABSTAIN"


def test_wrong_source_family_abstains(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    forecast = replace(ctx.forecast, family_identity="WRONG_FAMILY")
    probability = replace(
        ctx.probability, current_forecast_evidence_identity=forecast.evidence_identity
    )
    inputs = ((probability, forecast, ctx.economics),)
    result = m27i.build_preflight(**ctx.kwargs(candidate_inputs=inputs))
    assert result.artifact.state == "ABSTAIN"


def test_settlement_mapping_not_unvalidated_proxy_abstains(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    probability = replace(ctx.probability, settlement_mapping_status="VALIDATED")
    inputs = ((probability, ctx.forecast, ctx.economics),)
    result = m27i.build_preflight(**ctx.kwargs(candidate_inputs=inputs))
    assert result.artifact.state == "ABSTAIN"


def test_two_qualifying_candidates_abstains(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    probability2, forecast2 = _weather()
    probability2 = replace(
        probability2, market_ticker="M2", event_ticker="E-M2", probability=Decimal("0.95")
    )
    economics2, _series2, _event2, _regime2 = _economics(now=ctx.now, ticker="M2")
    inputs = ((ctx.probability, ctx.forecast, ctx.economics), (probability2, forecast2, economics2))
    result = m27i.build_preflight(**ctx.kwargs(candidate_inputs=inputs))
    assert result.artifact.state == "ABSTAIN"
    assert result.artifact.abstain_reason == m27i.AbstentionReason.MULTIPLE_QUALIFYING_CANDIDATES


# ---------------------------------------------------------------------------
# Account / M27F / M27H
# ---------------------------------------------------------------------------


def test_stale_m27f_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    stale_payload = _m27f_payload(ctx.now, completed=ctx.now - timedelta(seconds=45))
    stale_path = tmp_path / "m27f_stale.json"
    stale_path.write_text(json.dumps(stale_payload))
    result = m27i.build_preflight(**ctx.kwargs(m27f_evidence_path=stale_path))
    assert result.artifact.state == "BLOCKED"
    assert "account_reconciled" in result.artifact.missing_gates


def test_stale_m27h_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    stale_path = tmp_path / "m27h_stale.json"
    stale_path.write_text(
        json.dumps(_m27h_payload(ctx.now, completed=ctx.now - timedelta(seconds=45)))
    )
    result = m27i.build_preflight(**ctx.kwargs(m27h_evidence_path=stale_path))
    assert result.artifact.state == "BLOCKED"
    assert "write_credential_installed" in result.artifact.missing_gates


def test_missing_m27f_or_m27h_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    result = m27i.build_preflight(**ctx.kwargs(m27f_evidence_path=None, m27h_evidence_path=None))
    assert result.artifact.state == "BLOCKED"
    assert "account_reconciled" in result.artifact.missing_gates
    assert "write_credential_installed" in result.artifact.missing_gates
    assert "m13_verified" in result.artifact.missing_gates


# ---------------------------------------------------------------------------
# Book depth / rules current / market tradable
# ---------------------------------------------------------------------------


def test_price_changed_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    book_raw = {
        "ticker": "M",
        "orderbook_fp": {"yes_dollars": [[".310", "5"]], "no_dollars": [[".640", "5"]]},
    }
    ladder = PriceLadder.parse("deci_cent", [{"start": "0.0000", "end": "1.0000", "step": ".001"}])
    changed_observation = normalize_live_orderbook(
        book_raw,
        ticker="M",
        ladder=ladder,
        source_id="snapshot-2",
        observed_at=ctx.now,
        market_rules_hash="rules",
    )
    replay_input = ctx.economics.replay_input
    changed_replay = MarketEconomicsReplayInput(
        changed_observation, ladder, replay_input.fee_regime, replay_input.fee_policy
    )
    tampered = replace(ctx.economics, replay_input=changed_replay)
    result = m27i.build_preflight(**ctx.kwargs())
    assert result.artifact.missing_gates == ("rules_current",)
    assert result.artifact.gates.results["book_executable"].passed
    # Independent replay-consistency check must fail once the underlying book changes.
    book_tradable = m27i._market_currentness(ctx.candidate, tampered, ctx.now)
    assert not book_tradable.passed


def test_insufficient_depth_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    economics, _series, _event, _regime = _economics(now=ctx.now, quantity=Decimal("1.00"))
    depleted_side = replace(economics.yes, depth=replace(economics.yes.depth, filled=Decimal("0")))
    tampered = replace(economics, yes=depleted_side)
    book_tradable = m27i._market_currentness(ctx.candidate, tampered, ctx.now)
    assert not book_tradable.passed


def test_current_market_ticker_mismatch_blocks_market_open_current(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    payload = _public_payload(ctx.now, ctx.candidate, market_ticker="SOME_OTHER_TICKER")
    path = _rewrite_public_path(ctx, payload)
    result = m27i.build_preflight(**ctx.kwargs(public_evidence_path=path))
    assert result.artifact.state == "BLOCKED"
    assert "market_open_current" in result.artifact.missing_gates
    assert "market_tradable" in result.artifact.missing_gates
    # rules_current is unconditionally BLOCKED regardless -- never conflate the two.
    assert "rules_current" in result.artifact.missing_gates


def test_current_market_closed_blocks_market_open_current(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    payload = _public_payload(ctx.now, ctx.candidate, market_status="closed")
    path = _rewrite_public_path(ctx, payload)
    result = m27i.build_preflight(**ctx.kwargs(public_evidence_path=path))
    assert result.artifact.state == "BLOCKED"
    assert "market_open_current" in result.artifact.missing_gates
    assert "market_tradable" in result.artifact.missing_gates


def test_stale_current_market_evidence_blocks_market_open_current(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    payload = _public_payload(ctx.now, ctx.candidate, observed_at=ctx.now - timedelta(seconds=45))
    path = _rewrite_public_path(ctx, payload)
    result = m27i.build_preflight(**ctx.kwargs(public_evidence_path=path))
    assert result.artifact.state == "BLOCKED"
    assert "market_open_current" in result.artifact.missing_gates
    assert "market_tradable" in result.artifact.missing_gates


# ---------------------------------------------------------------------------
# Rules currentness -- requires BOTH an authoritative economics-market binding AND fresh
# current-side M27J evidence. A bare ``economics.market_rules_hash`` match is never sufficient
# (Gemini M27J delta repair, mandatory adversarial case).
# ---------------------------------------------------------------------------


def test_no_evidence_at_all_blocks_rules_current(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    result = m27i.build_preflight(**ctx.kwargs())
    rules_current = result.artifact.gates.results["rules_current"]
    assert not rules_current.passed
    assert rules_current.reason == m27i.NO_AUTHORITATIVE_CURRENT_RULES_IDENTITY
    assert result.artifact.state != "PREFLIGHT_READY"


def test_binding_without_current_m27j_blocks_rules_current(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    result = m27i.build_preflight(**ctx.kwargs(m27a_binding_evidence_path=ctx.m27a_binding_path))
    rules_current = result.artifact.gates.results["rules_current"]
    assert not rules_current.passed
    assert rules_current.reason == m27i.NO_AUTHORITATIVE_CURRENT_RULES_IDENTITY


def test_current_m27j_without_binding_blocks_rules_current(tmp_path: Path) -> None:
    """MANDATORY Gemini adversarial case: fresh current M27J evidence alone, with no
    authoritative binding, must still leave ``rules_current`` BLOCKED."""
    ctx = Context(tmp_path)
    result = m27i.build_preflight(**ctx.kwargs(m27j_evidence_path=ctx.m27j_path))
    rules_current = result.artifact.gates.results["rules_current"]
    assert not rules_current.passed
    assert rules_current.reason == m27i.NO_AUTHORITATIVE_CURRENT_RULES_IDENTITY
    assert result.artifact.state != "PREFLIGHT_READY"


def test_arbitrary_legacy_economics_hash_matching_current_m27j_still_blocks(tmp_path: Path) -> None:
    """MANDATORY Gemini adversarial case: a caller can trivially set
    ``economics.market_rules_hash`` (via the legacy, still-valid-for-research
    ``normalize_live_orderbook`` path) to equal today's M27J hash and obtain ``H == H``. That
    equality alone must never unlock ``rules_current`` -- only an independently re-validated
    authoritative binding, absent here, can."""
    ctx = Context(tmp_path)
    current_rules_hash = Market.parse(ctx.raw_market).rules_hash
    legacy_economics, _series, _event, _regime = _economics(
        now=ctx.now, market_rules_hash=current_rules_hash
    )
    assert legacy_economics.market_rules_hash == current_rules_hash
    inputs = ((ctx.probability, ctx.forecast, legacy_economics),)
    result = m27i.build_preflight(
        **ctx.kwargs(candidate_inputs=inputs, m27j_evidence_path=ctx.m27j_path)
    )
    rules_current = result.artifact.gates.results["rules_current"]
    assert not rules_current.passed
    assert rules_current.reason == m27i.NO_AUTHORITATIVE_CURRENT_RULES_IDENTITY
    assert result.artifact.state != "PREFLIGHT_READY"


def test_exact_binding_and_current_match_unlocks_rules_current_and_reaches_preflight_ready(
    tmp_path: Path,
) -> None:
    ctx = Context(tmp_path)
    result = m27i.build_preflight(
        **ctx.kwargs(
            m27j_evidence_path=ctx.m27j_path, m27a_binding_evidence_path=ctx.m27a_binding_path
        )
    )
    artifact = result.artifact
    assert artifact.gates.results["rules_current"].passed, artifact.gates.results[
        "rules_current"
    ].reason
    assert artifact.state == "PREFLIGHT_READY", artifact.gates.missing
    rendered = m27i.render_preflight(artifact)
    assert "RULES CURRENTNESS: PASS" in rendered
    assert "PREFLIGHT_READY" in rendered


def test_current_m27j_mismatch_after_valid_binding_blocks(tmp_path: Path) -> None:
    """H1 expected (binding) + H2 current => BLOCKED (continuity broken)."""
    ctx = Context(tmp_path)
    changed_market = _raw_market(rules_primary="YES if the measured value is at least 999 units.")
    mismatched_current = _snapshot_payload(ctx.now, raw_market=changed_market)
    path = _rewrite_m27j_path(ctx, mismatched_current)
    result = m27i.build_preflight(
        **ctx.kwargs(m27j_evidence_path=path, m27a_binding_evidence_path=ctx.m27a_binding_path)
    )
    rules_current = result.artifact.gates.results["rules_current"]
    assert not rules_current.passed
    assert "no longer matches" in (rules_current.reason or "")
    assert result.artifact.state != "PREFLIGHT_READY"


def test_stale_current_m27j_after_valid_binding_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    stale_payload = _snapshot_payload(
        ctx.now, raw_market=ctx.raw_market, observed_at=ctx.now - timedelta(seconds=45)
    )
    path = _rewrite_m27j_path(ctx, stale_payload)
    result = m27i.build_preflight(
        **ctx.kwargs(m27j_evidence_path=path, m27a_binding_evidence_path=ctx.m27a_binding_path)
    )
    assert not result.artifact.gates.results["rules_current"].passed
    assert result.artifact.state != "PREFLIGHT_READY"
    assert "rules_current" in result.artifact.missing_gates


def test_malformed_current_m27j_after_valid_binding_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    malformed = dict(ctx.m27j_payload)
    malformed["rules_hash"] = "not-the-real-hash"
    path = _rewrite_m27j_path(ctx, malformed)
    result = m27i.build_preflight(
        **ctx.kwargs(m27j_evidence_path=path, m27a_binding_evidence_path=ctx.m27a_binding_path)
    )
    assert not result.artifact.gates.results["rules_current"].passed
    assert result.artifact.state != "PREFLIGHT_READY"


def test_forged_rules_current_field_in_m27j_evidence_is_inert(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    tampered = dict(ctx.m27j_payload)
    tampered["rules_current"] = True
    path = _rewrite_m27j_path(ctx, tampered)
    result = m27i.build_preflight(
        **ctx.kwargs(m27j_evidence_path=path, m27a_binding_evidence_path=ctx.m27a_binding_path)
    )
    assert not result.artifact.gates.results["rules_current"].passed


def test_preflight_expiry_capped_to_m27j_expiry(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    tight_observed = ctx.now - timedelta(seconds=29)
    tight_payload = _snapshot_payload(
        ctx.now, raw_market=ctx.raw_market, observed_at=tight_observed
    )
    path = _rewrite_m27j_path(ctx, tight_payload)
    result = m27i.build_preflight(**ctx.kwargs(m27j_evidence_path=path))
    assert result.artifact.expires_at <= ctx.now + timedelta(seconds=1)
    assert result.artifact.expires_at == datetime.fromisoformat(tight_payload["expires_at"])


# ---------------------------------------------------------------------------
# Authoritative economics-market binding -- adversarial attacks (Gemini section 12)
# ---------------------------------------------------------------------------


def test_binding_wrong_economics_evidence_id_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    tampered = dict(ctx.m27a_binding_payload)
    tampered["economics_evidence_id"] = "not-" + str(tampered["economics_evidence_id"])
    path = _rewrite_binding_path(ctx, tampered)
    result = m27i.build_preflight(
        **ctx.kwargs(m27j_evidence_path=ctx.m27j_path, m27a_binding_evidence_path=path)
    )
    assert not result.artifact.gates.results["rules_current"].passed
    assert result.artifact.state != "PREFLIGHT_READY"


def test_binding_references_different_market_snapshot_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    other_snapshot = _snapshot_payload(
        ctx.now, ticker="OTHER", raw_market=_raw_market(ticker="OTHER", event_ticker="OTHER-E")
    )
    tampered = dict(ctx.m27a_binding_payload)
    tampered["expected_snapshot"] = other_snapshot
    path = _rewrite_binding_path(ctx, tampered)
    result = m27i.build_preflight(
        **ctx.kwargs(m27j_evidence_path=ctx.m27j_path, m27a_binding_evidence_path=path)
    )
    assert not result.artifact.gates.results["rules_current"].passed


def test_binding_expected_snapshot_body_tampered_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    tampered_body = json.dumps(
        {"market": _raw_market(rules_primary="TAMPERED")}, sort_keys=True
    ).encode()
    tampered = dict(ctx.m27a_binding_payload)
    tampered["expected_snapshot"] = dict(tampered["expected_snapshot"])
    tampered["expected_snapshot"]["raw_body_b64"] = base64.b64encode(tampered_body).decode()
    path = _rewrite_binding_path(ctx, tampered)
    result = m27i.build_preflight(
        **ctx.kwargs(m27j_evidence_path=ctx.m27j_path, m27a_binding_evidence_path=path)
    )
    assert not result.artifact.gates.results["rules_current"].passed


def test_binding_expected_snapshot_body_sha_tampered_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    tampered = dict(ctx.m27a_binding_payload)
    tampered["expected_snapshot"] = dict(tampered["expected_snapshot"])
    tampered["expected_snapshot"]["body_sha256"] = "e" * 64
    path = _rewrite_binding_path(ctx, tampered)
    result = m27i.build_preflight(
        **ctx.kwargs(m27j_evidence_path=ctx.m27j_path, m27a_binding_evidence_path=path)
    )
    assert not result.artifact.gates.results["rules_current"].passed


def test_binding_expected_snapshot_stamped_rules_hash_tampered_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    tampered = dict(ctx.m27a_binding_payload)
    tampered["expected_snapshot"] = dict(tampered["expected_snapshot"])
    tampered["expected_snapshot"]["rules_hash"] = "f" * 64
    path = _rewrite_binding_path(ctx, tampered)
    result = m27i.build_preflight(
        **ctx.kwargs(m27j_evidence_path=ctx.m27j_path, m27a_binding_evidence_path=path)
    )
    assert not result.artifact.gates.results["rules_current"].passed


def test_binding_expected_ticker_wrong_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    tampered = dict(ctx.m27a_binding_payload)
    tampered["market_ticker"] = "WRONG"
    path = _rewrite_binding_path(ctx, tampered)
    result = m27i.build_preflight(
        **ctx.kwargs(m27j_evidence_path=ctx.m27j_path, m27a_binding_evidence_path=path)
    )
    assert not result.artifact.gates.results["rules_current"].passed


def test_binding_expected_event_wrong_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    tampered = dict(ctx.m27a_binding_payload)
    tampered["event_ticker"] = "WRONG-E"
    path = _rewrite_binding_path(ctx, tampered)
    result = m27i.build_preflight(
        **ctx.kwargs(m27j_evidence_path=ctx.m27j_path, m27a_binding_evidence_path=path)
    )
    assert not result.artifact.gates.results["rules_current"].passed


def test_builder_rejects_snapshot_acquired_after_economics(tmp_path: Path) -> None:
    from services.opportunity_engine.domain import OpportunityError

    ctx = Context(tmp_path)
    late_snapshot = _snapshot_payload(
        ctx.now, raw_market=ctx.raw_market, observed_at=ctx.now + timedelta(seconds=1)
    )
    ladder = PriceLadder.parse("deci_cent", [{"start": "0.0000", "end": "1.0000", "step": ".001"}])
    with pytest.raises(OpportunityError, match="after the economics evaluation"):
        build_authoritative_market_economics(
            snapshot_payload=late_snapshot,
            expected_market_ticker="M",
            expected_event_ticker="E",
            series_ticker="CLIMDW",
            market_source_id="market-source",
            raw_orderbook={
                "ticker": "M",
                "orderbook_fp": {"yes_dollars": [[".300", "5"]], "no_dollars": [[".650", "5"]]},
            },
            ladder=ladder,
            orderbook_source_id="snapshot-M",
            orderbook_observed_at=ctx.now,
            series_fee_observation_id=ctx.fee_regime.series_observation_id,
            resolved_fee_regime_id=ctx.fee_regime.regime_id,
            event_fee_hash=ctx.fee_regime.event_metadata_hash,
            fee_policy=ctx.economics.replay_input.fee_policy,
            fee_regime=ctx.fee_regime,
            requested_quantity=Decimal("1.00"),
            economics_observed_at=ctx.now,
        )


def test_binding_validator_rejects_snapshot_that_does_not_match_economics_market_observed_at(
    tmp_path: Path,
) -> None:
    """A binding cannot swap in a fresher (or older) expected snapshot post-hoc -- the binding's
    ``market_observed_at`` and the live ``economics.market_observed_at`` must agree with the
    embedded snapshot's own true acquisition time."""
    ctx = Context(tmp_path)
    other_snapshot = _snapshot_payload(
        ctx.now, raw_market=ctx.raw_market, observed_at=ctx.now - timedelta(seconds=1)
    )
    tampered = dict(ctx.m27a_binding_payload)
    tampered["expected_snapshot"] = other_snapshot
    tampered["market_observed_at"] = other_snapshot["observed_at"]
    path = _rewrite_binding_path(ctx, tampered)
    result = m27i.build_preflight(
        **ctx.kwargs(m27j_evidence_path=ctx.m27j_path, m27a_binding_evidence_path=path)
    )
    assert not result.artifact.gates.results["rules_current"].passed


def test_binding_orderbook_source_hash_mismatch_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    tampered = dict(ctx.m27a_binding_payload)
    tampered["orderbook_source_hash"] = "different-hash"
    path = _rewrite_binding_path(ctx, tampered)
    result = m27i.build_preflight(
        **ctx.kwargs(m27j_evidence_path=ctx.m27j_path, m27a_binding_evidence_path=path)
    )
    assert not result.artifact.gates.results["rules_current"].passed


def test_binding_price_range_hash_mismatch_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    tampered = dict(ctx.m27a_binding_payload)
    tampered["price_range_hash"] = "different-hash"
    path = _rewrite_binding_path(ctx, tampered)
    result = m27i.build_preflight(
        **ctx.kwargs(m27j_evidence_path=ctx.m27j_path, m27a_binding_evidence_path=path)
    )
    assert not result.artifact.gates.results["rules_current"].passed


def test_binding_metadata_hash_mismatch_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    tampered = dict(ctx.m27a_binding_payload)
    tampered["market_metadata_hash"] = "different-hash"
    path = _rewrite_binding_path(ctx, tampered)
    result = m27i.build_preflight(
        **ctx.kwargs(m27j_evidence_path=ctx.m27j_path, m27a_binding_evidence_path=path)
    )
    assert not result.artifact.gates.results["rules_current"].passed


def test_market_open_current_remains_independent_of_rules_current(tmp_path: Path) -> None:
    """A market can be open and executable while rules_current fails, and vice versa."""
    ctx = Context(tmp_path)
    result = m27i.build_preflight(**ctx.kwargs())
    assert result.artifact.gates.results["market_open_current"].passed
    assert result.artifact.gates.results["book_executable"].passed
    assert result.artifact.gates.results["market_tradable"].passed
    assert not result.artifact.gates.results["rules_current"].passed


def test_open_market_evidence_does_not_unlock_rules_current(tmp_path: Path) -> None:
    """Fresh, valid M27E evidence proves market existence/status, never rules identity."""
    ctx = Context(tmp_path)
    result = m27i.build_preflight(**ctx.kwargs())
    assert result.artifact.gates.results["market_open_current"].passed
    assert not result.artifact.gates.results["rules_current"].passed
    assert (
        result.artifact.gates.results["rules_current"].reason
        == m27i.NO_AUTHORITATIVE_CURRENT_RULES_IDENTITY
    )


def test_matching_market_rules_hash_cannot_self_prove_rules_current(tmp_path: Path) -> None:
    """A candidate/economics ``market_rules_hash`` matching itself proves nothing about now."""
    ctx = Context(tmp_path)
    # sanity: candidate carries a rules-derived identity
    assert ctx.candidate.eligibility.contract_identity
    assert ctx.economics.market_rules_hash == Market.parse(ctx.raw_market).rules_hash
    result = m27i.build_preflight(**ctx.kwargs())
    assert not result.artifact.gates.results["rules_current"].passed


def test_market_metadata_hash_does_not_unlock_rules_current(tmp_path: Path) -> None:
    """A fresh market-metadata/body hash on the M27E evidence is not a rules-identity hash."""
    ctx = Context(tmp_path)
    payload = _public_payload(ctx.now, ctx.candidate)
    entry = payload["markets"]["pages"][0]["payload"]["markets"][0]
    entry["market_metadata_hash"] = ctx.economics.market_metadata_hash
    entry["rules_hash"] = ctx.economics.market_rules_hash
    path = _rewrite_public_path(ctx, payload)
    result = m27i.build_preflight(**ctx.kwargs(public_evidence_path=path))
    assert not result.artifact.gates.results["rules_current"].passed
    assert (
        result.artifact.gates.results["rules_current"].reason
        == m27i.NO_AUTHORITATIVE_CURRENT_RULES_IDENTITY
    )


def test_fresh_orderbook_does_not_unlock_rules_current(tmp_path: Path) -> None:
    """Fresh orderbook / exact price / full depth still cannot prove rules identity."""
    ctx = Context(tmp_path)
    result = m27i.build_preflight(**ctx.kwargs())
    assert result.artifact.gates.results["book_executable"].passed
    assert not result.artifact.gates.results["rules_current"].passed


def test_forged_rules_field_in_public_evidence_cannot_unlock_gate(tmp_path: Path) -> None:
    """A caller-injected ``rules_hash``/``rules_current`` field anywhere is inert."""
    ctx = Context(tmp_path)
    payload = _public_payload(ctx.now, ctx.candidate)
    payload["rules_current"] = True
    payload["rules_hash"] = ctx.economics.market_rules_hash
    payload["exchange_status"]["payload"]["rules_current"] = True
    entry = payload["markets"]["pages"][0]["payload"]["markets"][0]
    entry["rules_current"] = True
    entry["current_rules_identity"] = ctx.economics.market_rules_hash
    path = _rewrite_public_path(ctx, payload)
    result = m27i.build_preflight(**ctx.kwargs(public_evidence_path=path))
    assert not result.artifact.gates.results["rules_current"].passed
    assert (
        result.artifact.gates.results["rules_current"].reason
        == m27i.NO_AUTHORITATIVE_CURRENT_RULES_IDENTITY
    )


def test_no_preflight_ready_regardless_of_market_evidence_shape(tmp_path: Path) -> None:
    """No fixture path reachable through M27E market evidence alone can reach PREFLIGHT_READY."""
    ctx = Context(tmp_path)
    for status in ("open", "OPEN", "active"):
        payload = _public_payload(ctx.now, ctx.candidate, market_status=status)
        path = _rewrite_public_path(ctx, payload)
        result = m27i.build_preflight(**ctx.kwargs(public_evidence_path=path))
        assert result.artifact.state != "PREFLIGHT_READY"
        assert "rules_current" in result.artifact.missing_gates


def test_candidate_identity_mismatch_fails_candidate_current(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    other_forecast = replace(ctx.forecast, evidence_identity="different-forecast-identity")
    result = m27i._candidate_current_gate(
        ctx.candidate, ctx.probability, other_forecast, ctx.economics
    )
    assert not result.passed


# ---------------------------------------------------------------------------
# Fees
# ---------------------------------------------------------------------------


def test_fee_regime_changed_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    other_series = CurrentSeriesFeeObservation.parse(
        {**_series_payload(ctx.now), "fee_multiplier": "2"}, observed_at=ctx.now
    )
    result = m27i.build_preflight(**ctx.kwargs(current_series_fee_observation=other_series))
    assert result.artifact.state == "BLOCKED"
    assert "fee_verified" in result.artifact.missing_gates


def test_event_fee_override_changed_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    other_event = EventFeeOverride.parse(
        {"fee_type_override": "quadratic", "fee_multiplier_override": "3"}
    )
    result = m27i.build_preflight(
        **ctx.kwargs(current_event_fee_override=other_event, current_event_fee_observed_at=ctx.now)
    )
    assert result.artifact.state == "BLOCKED"
    assert "fee_verified" in result.artifact.missing_gates


def test_stale_series_fee_observation_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    stale_series = CurrentSeriesFeeObservation.parse(
        _series_payload(ctx.now), observed_at=ctx.now - timedelta(seconds=45)
    )
    result = m27i.build_preflight(**ctx.kwargs(current_series_fee_observation=stale_series))
    assert result.artifact.state == "BLOCKED"
    assert "fee_verified" in result.artifact.missing_gates


def test_stale_event_fee_observed_at_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    result = m27i.build_preflight(
        **ctx.kwargs(current_event_fee_observed_at=ctx.now - timedelta(seconds=45))
    )
    assert result.artifact.state == "BLOCKED"
    assert "fee_verified" in result.artifact.missing_gates


def test_missing_fee_evidence_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    result = m27i.build_preflight(
        **ctx.kwargs(current_series_fee_observation=None, current_event_fee_override=None)
    )
    assert result.artifact.state == "BLOCKED"
    assert "fee_verified" in result.artifact.missing_gates


def test_bare_resolved_fee_regime_parameter_removed() -> None:
    import inspect

    signature = inspect.signature(m27i.build_preflight)
    assert "current_fee_regime" not in signature.parameters


# ---------------------------------------------------------------------------
# Trading active / exchange active
# ---------------------------------------------------------------------------


def test_trading_inactive_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    payload = _public_payload(ctx.now, ctx.candidate, trading_active=False)
    path = _rewrite_public_path(ctx, payload)
    result = m27i.build_preflight(**ctx.kwargs(public_evidence_path=path))
    assert result.artifact.state == "BLOCKED"
    assert "trading_active" in result.artifact.missing_gates


def test_exchange_inactive_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    payload = _public_payload(ctx.now, ctx.candidate, exchange_active=False)
    path = _rewrite_public_path(ctx, payload)
    result = m27i.build_preflight(**ctx.kwargs(public_evidence_path=path))
    assert result.artifact.state == "BLOCKED"
    assert "exchange_active" in result.artifact.missing_gates


def test_forged_local_trading_active_json_cannot_pass(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    forged = tmp_path / "forged.json"
    forged.write_text(json.dumps({"exchange_status": {"trading_active": True}}))
    result = m27i.build_preflight(**ctx.kwargs(public_evidence_path=forged))
    assert result.artifact.state == "BLOCKED"
    assert "trading_active" in result.artifact.missing_gates
    assert "exchange_active" in result.artifact.missing_gates


def test_malformed_exchange_status_evidence_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    payload = _public_payload(ctx.now, ctx.candidate)
    payload["exchange_status"] = "not-an-object"
    path = _rewrite_public_path(ctx, payload)
    result = m27i.build_preflight(**ctx.kwargs(public_evidence_path=path))
    assert result.artifact.state == "BLOCKED"
    assert "trading_active" in result.artifact.missing_gates


def test_stale_exchange_status_evidence_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    payload = _public_payload(ctx.now, ctx.candidate, observed_at=ctx.now - timedelta(seconds=45))
    path = _rewrite_public_path(ctx, payload)
    result = m27i.build_preflight(**ctx.kwargs(public_evidence_path=path))
    assert result.artifact.state == "BLOCKED"
    assert "trading_active" in result.artifact.missing_gates


def test_wrong_exchange_status_path_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    payload = _public_payload(ctx.now, ctx.candidate)
    payload["exchange_status"]["path"] = "/trade-api/v2/something-else"
    path = _rewrite_public_path(ctx, payload)
    result = m27i.build_preflight(**ctx.kwargs(public_evidence_path=path))
    assert result.artifact.state == "BLOCKED"
    assert "trading_active" in result.artifact.missing_gates


def test_missing_trading_active_field_stays_blocked(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    payload = _public_payload(ctx.now, ctx.candidate)
    del payload["exchange_status"]["payload"]["trading_active"]
    path = _rewrite_public_path(ctx, payload)
    result = m27i.build_preflight(**ctx.kwargs(public_evidence_path=path))
    assert result.artifact.state == "BLOCKED"
    assert "trading_active" in result.artifact.missing_gates


def test_missing_public_evidence_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    result = m27i.build_preflight(**ctx.kwargs(public_evidence_path=None))
    assert result.artifact.state == "BLOCKED"
    assert "trading_active" in result.artifact.missing_gates
    assert "exchange_active" in result.artifact.missing_gates
    assert "market_open_current" in result.artifact.missing_gates
    assert "rules_current" in result.artifact.missing_gates


# ---------------------------------------------------------------------------
# Candidate exposure / burn
# ---------------------------------------------------------------------------


def test_unknown_order_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    exposure = _exposure(ctx.candidate.market_ticker, ctx.now, open_orders=1)
    result = m27i.build_preflight(**ctx.kwargs(candidate_exposure=exposure))
    assert result.artifact.state == "BLOCKED"
    assert "no_unknown_orders" in result.artifact.missing_gates


def test_existing_position_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    exposure = _exposure(ctx.candidate.market_ticker, ctx.now, position_nonzero=True)
    result = m27i.build_preflight(**ctx.kwargs(candidate_exposure=exposure))
    assert result.artifact.state == "BLOCKED"
    assert "no_unknown_positions" in result.artifact.missing_gates


def test_unresolved_canary_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    ctx.canary_store.open_session(
        session_id="s1",
        preview_id="p1",
        approval_id="a1",
        client_order_id="kalsh3-v1-zzzzzzzz",
        now=ctx.now,
    )
    result = m27i.build_preflight(**ctx.kwargs())
    assert result.artifact.state == "BLOCKED"
    assert "no_unknown_positions" in result.artifact.missing_gates


def test_burn_already_used_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    ctx.canary_store.open_session(
        session_id="s1",
        preview_id="p1",
        approval_id="a1",
        client_order_id="kalsh3-v1-zzzzzzzz",
        now=ctx.now,
    )
    ctx.canary_store.record_submission_attempt(session_id="s1", mode="REAL_PRODUCTION")
    result = m27i.build_preflight(**ctx.kwargs())
    assert result.artifact.state == "BLOCKED"
    assert "write_budget_safe" in result.artifact.missing_gates


# ---------------------------------------------------------------------------
# M13 -- binding, hash re-derivation, expiry contract
# ---------------------------------------------------------------------------


def test_m13_denial_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    decision = _risk_decision(
        ctx.risk_intent,
        ctx.risk_snapshot,
        ctx.now,
        state=RiskDecisionState.REJECT,
        reasons=("CASH_INSUFFICIENT",),
    )
    result = m27i.build_preflight(**ctx.kwargs(risk_decision=decision))
    assert result.artifact.state == "BLOCKED"
    assert "m13_verified" in result.artifact.missing_gates


def test_missing_risk_decision_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    result = m27i.build_preflight(
        **ctx.kwargs(risk_decision=None, risk_intent=None, risk_snapshot=None)
    )
    assert result.artifact.state == "BLOCKED"
    assert "m13_verified" in result.artifact.missing_gates


def test_mismatched_candidate_identities_never_bind(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    unrelated_candidate = replace(ctx.candidate, candidate_id="some-other-candidate-id")
    intent = _risk_intent(unrelated_candidate, ctx.now)
    snapshot = _risk_snapshot(ctx.m27f_payload, ctx.exposure, ctx.now)
    decision = _risk_decision(intent, snapshot, ctx.now)
    result = m27i.build_preflight(
        **ctx.kwargs(risk_decision=decision, risk_intent=intent, risk_snapshot=snapshot)
    )
    assert result.artifact.state == "BLOCKED"
    assert "m13_verified" in result.artifact.missing_gates


def test_portfolio_state_hash_mismatch_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    other_snapshot = _risk_snapshot(
        ctx.m27f_payload, ctx.exposure, ctx.now, observed_at=ctx.now - timedelta(seconds=1)
    )
    # decision still binds to the *original* snapshot's hash, not this one.
    result = m27i.build_preflight(**ctx.kwargs(risk_snapshot=other_snapshot))
    assert result.artifact.state == "BLOCKED"
    assert "m13_verified" in result.artifact.missing_gates


def test_reconciliation_version_mismatch_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    snapshot = _risk_snapshot(
        ctx.m27f_payload, ctx.exposure, ctx.now, reconciliation_version="wrong-reconciliation"
    )
    decision = _risk_decision(ctx.risk_intent, snapshot, ctx.now)
    result = m27i.build_preflight(**ctx.kwargs(risk_decision=decision, risk_snapshot=snapshot))
    assert result.artifact.state == "BLOCKED"
    assert "m13_verified" in result.artifact.missing_gates


def test_account_snapshot_version_mismatch_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    snapshot = _risk_snapshot(
        ctx.m27f_payload, ctx.exposure, ctx.now, account_snapshot_version="wrong-account-version"
    )
    decision = _risk_decision(ctx.risk_intent, snapshot, ctx.now)
    result = m27i.build_preflight(**ctx.kwargs(risk_decision=decision, risk_snapshot=snapshot))
    assert result.artifact.state == "BLOCKED"
    assert "m13_verified" in result.artifact.missing_gates


def test_account_state_changed_after_decision_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    later_m27f = _m27f_payload(ctx.now, completed=ctx.now + timedelta(seconds=1))
    later_path = tmp_path / "m27f_later.json"
    later_path.write_text(json.dumps(later_m27f))
    later_snapshot = _risk_snapshot(later_m27f, ctx.exposure, ctx.now)
    later_decision = _risk_decision(ctx.risk_intent, later_snapshot, ctx.now)
    result = m27i.build_preflight(
        **ctx.kwargs(
            m27f_evidence_path=later_path,
            risk_snapshot=later_snapshot,
            risk_decision=later_decision,
        )
    )
    assert result.artifact.state == "BLOCKED"
    assert "m13_verified" in result.artifact.missing_gates


def test_tampered_risk_snapshot_content_hash_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    tampered_snapshot = replace(ctx.risk_snapshot, cash=Decimal("999999"))
    result = m27i.build_preflight(**ctx.kwargs(risk_snapshot=tampered_snapshot))
    assert result.artifact.state == "BLOCKED"
    assert "m13_verified" in result.artifact.missing_gates


def test_tampered_risk_decision_content_hash_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    tampered_decision = replace(ctx.risk_decision, display_result="TAMPERED")
    result = m27i.build_preflight(**ctx.kwargs(risk_decision=tampered_decision))
    assert result.artifact.state == "BLOCKED"
    assert "m13_verified" in result.artifact.missing_gates


def test_tampered_risk_intent_content_hash_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    tampered_intent = replace(ctx.risk_intent, account="someone-else")
    result = m27i.build_preflight(**ctx.kwargs(risk_intent=tampered_intent))
    assert result.artifact.state == "BLOCKED"
    assert "m13_verified" in result.artifact.missing_gates


def test_risk_decision_valid_at_4_999_seconds(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    decided_at = ctx.now - timedelta(seconds=4, milliseconds=999)
    snapshot = _risk_snapshot(ctx.m27f_payload, ctx.exposure, ctx.now, observed_at=decided_at)
    decision = _risk_decision(ctx.risk_intent, snapshot, ctx.now, decided_at=decided_at)
    result = m27i.build_preflight(**ctx.kwargs(risk_decision=decision, risk_snapshot=snapshot))
    assert "m13_verified" not in result.artifact.missing_gates


def test_risk_decision_valid_at_exact_expiry_boundary(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    decided_at = ctx.now - timedelta(seconds=5)
    snapshot = _risk_snapshot(ctx.m27f_payload, ctx.exposure, ctx.now, observed_at=decided_at)
    decision = _risk_decision(ctx.risk_intent, snapshot, ctx.now, decided_at=decided_at)
    result = m27i.build_preflight(**ctx.kwargs(risk_decision=decision, risk_snapshot=snapshot))
    assert "m13_verified" not in result.artifact.missing_gates


def test_risk_decision_expired_by_epsilon_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    decided_at = ctx.now - timedelta(seconds=5, milliseconds=1)
    snapshot = _risk_snapshot(ctx.m27f_payload, ctx.exposure, ctx.now, observed_at=decided_at)
    decision = _risk_decision(ctx.risk_intent, snapshot, ctx.now, decided_at=decided_at)
    result = m27i.build_preflight(**ctx.kwargs(risk_decision=decision, risk_snapshot=snapshot))
    assert "m13_verified" in result.artifact.missing_gates


def test_risk_decision_10_seconds_old_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    decided_at = ctx.now - timedelta(seconds=10)
    snapshot = _risk_snapshot(ctx.m27f_payload, ctx.exposure, ctx.now, observed_at=decided_at)
    decision = _risk_decision(ctx.risk_intent, snapshot, ctx.now, decided_at=decided_at)
    result = m27i.build_preflight(**ctx.kwargs(risk_decision=decision, risk_snapshot=snapshot))
    assert "m13_verified" in result.artifact.missing_gates


def test_global_halt_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    ctx.authorization_store.activate_global_halt(
        actor="OPERATOR", reason="test halt", authenticated=True
    )
    result = m27i.build_preflight(**ctx.kwargs())
    assert result.artifact.state == "BLOCKED"
    assert "global_halt_clear" in result.artifact.missing_gates
    assert "m13_verified" in result.artifact.missing_gates


def test_kill_active_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    ctx.authorization_store.set_kill_state(
        KillState(KillCategory.PORTFOLIO, KillLevel.KILLED, "test", ctx.now), actor="OPERATOR"
    )
    result = m27i.build_preflight(**ctx.kwargs())
    assert result.artifact.state == "BLOCKED"
    assert "kills_clear" in result.artifact.missing_gates


def test_compliance_hold_blocks(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    ctx.authorization_store.set_compliance(ComplianceState.HOLD, actor="OPERATOR", reason="hold")
    result = m27i.build_preflight(**ctx.kwargs())
    assert result.artifact.state == "BLOCKED"
    assert "compliance_clear" in result.artifact.missing_gates


# ---------------------------------------------------------------------------
# Preflight expiry -- minimum of every underlying deadline
# ---------------------------------------------------------------------------


def test_risk_decision_expiring_in_1_second_caps_preflight_expiry(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    decided_at = ctx.now - timedelta(seconds=4)
    snapshot = _risk_snapshot(ctx.m27f_payload, ctx.exposure, ctx.now, observed_at=decided_at)
    decision = _risk_decision(ctx.risk_intent, snapshot, ctx.now, decided_at=decided_at)
    result = m27i.build_preflight(**ctx.kwargs(risk_decision=decision, risk_snapshot=snapshot))
    assert result.artifact.expires_at <= ctx.now + timedelta(seconds=1)


def test_m27f_expiring_in_2_seconds_caps_preflight_expiry(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    m27f_payload = _m27f_payload(ctx.now, completed=ctx.now - timedelta(seconds=28))
    path = tmp_path / "m27f_tight.json"
    path.write_text(json.dumps(m27f_payload))
    snapshot = _risk_snapshot(m27f_payload, ctx.exposure, ctx.now)
    decision = _risk_decision(ctx.risk_intent, snapshot, ctx.now)
    result = m27i.build_preflight(
        **ctx.kwargs(m27f_evidence_path=path, risk_snapshot=snapshot, risk_decision=decision)
    )
    assert result.artifact.expires_at <= ctx.now + timedelta(seconds=2)


def test_m27h_tighter_than_m27f_caps_preflight_expiry(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    m27h_payload = _m27h_payload(ctx.now, completed=ctx.now - timedelta(seconds=29))
    path = tmp_path / "m27h_tight.json"
    path.write_text(json.dumps(m27h_payload))
    result = m27i.build_preflight(**ctx.kwargs(m27h_evidence_path=path))
    assert result.artifact.expires_at <= ctx.now + timedelta(seconds=1)


def test_exposure_tighter_deadline_caps_preflight_expiry(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    exposure = _exposure(
        ctx.candidate.market_ticker,
        ctx.now,
        completed=ctx.now - timedelta(seconds=29, milliseconds=500),
    )
    m27f_payload = _m27f_payload(ctx.now)
    snapshot = _risk_snapshot(m27f_payload, exposure, ctx.now)
    decision = _risk_decision(ctx.risk_intent, snapshot, ctx.now)
    result = m27i.build_preflight(
        **ctx.kwargs(
            candidate_exposure=exposure,
            risk_snapshot=snapshot,
            risk_decision=decision,
        )
    )
    assert result.artifact.expires_at <= ctx.now + timedelta(seconds=1)


def test_orderbook_tighter_deadline_caps_preflight_expiry(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    forecast = replace(ctx.forecast)
    economics, _series, _event, _regime = _economics(now=ctx.now)
    economics = replace(economics, orderbook_observed_at=ctx.now - timedelta(seconds=29))
    inputs = ((ctx.probability, forecast, economics),)
    result = m27i.build_preflight(**ctx.kwargs(candidate_inputs=inputs))
    assert result.artifact.expires_at <= ctx.now + timedelta(seconds=1)


def test_candidate_eligibility_expiry_caps_preflight_expiry(tmp_path: Path) -> None:
    probability, forecast = _weather(interval_end=None)
    now = forecast.forecast_reference_time + timedelta(seconds=10)
    tight_forecast = replace(forecast, interval_end=now + timedelta(seconds=1))
    probability = replace(
        probability, current_forecast_evidence_identity=tight_forecast.evidence_identity
    )
    economics, series_observation, event_override, _regime = _economics(now=now)
    inputs = ((probability, tight_forecast, economics),)

    result_check = m27d.select_experimental_candidate(inputs, now=now)
    assert result_check.state is m27d.CandidateState.QUALIFYING_EXPERIMENTAL_CANARY
    candidate = result_check.selected
    assert candidate.eligibility.expires_at <= now + timedelta(seconds=2)

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        m27f_payload = _m27f_payload(now)
        m27f_path = tmp_path / "m27f.json"
        m27f_path.write_text(json.dumps(m27f_payload))
        m27h_path = tmp_path / "m27h.json"
        m27h_path.write_text(json.dumps(_m27h_payload(now)))
        public_path = tmp_path / "public.json"
        public_path.write_text(json.dumps(_public_payload(now, candidate)))
        exposure = _exposure(candidate.market_ticker, now)
        canary_store = CanaryStore(tmp_path / "canary.sqlite")
        authorization_store = AuthorizationStore(tmp_path / "risk.sqlite", FixedClock(now))
        authorization_store.set_compliance(ComplianceState.CLEAR, actor="OPERATOR", reason="t")
        intent = _risk_intent(candidate, now)
        snapshot = _risk_snapshot(m27f_payload, exposure, now)
        decision = _risk_decision(intent, snapshot, now)

        result = m27i.build_preflight(
            now=now,
            candidate_inputs=inputs,
            m27f_evidence_path=m27f_path,
            m27h_evidence_path=m27h_path,
            public_evidence_path=public_path,
            current_series_fee_observation=series_observation,
            current_event_fee_override=event_override,
            current_event_fee_observed_at=now,
            candidate_exposure=exposure,
            risk_decision=decision,
            risk_intent=intent,
            risk_snapshot=snapshot,
            authorization_store=authorization_store,
            canary_store=canary_store,
        )
    assert result.artifact.expires_at <= now + timedelta(seconds=2)


def test_earliest_deadline_always_wins(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    decided_at = ctx.now - timedelta(seconds=4, milliseconds=500)
    snapshot = _risk_snapshot(ctx.m27f_payload, ctx.exposure, ctx.now, observed_at=decided_at)
    decision = _risk_decision(ctx.risk_intent, snapshot, ctx.now, decided_at=decided_at)
    m27h_payload = _m27h_payload(ctx.now, completed=ctx.now - timedelta(seconds=10))
    m27h_path = tmp_path / "m27h_slack.json"
    m27h_path.write_text(json.dumps(m27h_payload))
    result = m27i.build_preflight(
        **ctx.kwargs(risk_decision=decision, risk_snapshot=snapshot, m27h_evidence_path=m27h_path)
    )
    assert result.artifact.expires_at == decision.expires_at


def test_preflight_expires(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    result = m27i.build_preflight(**ctx.kwargs())
    artifact = result.artifact
    assert not artifact.fresh(artifact.expires_at + timedelta(seconds=1))


def test_unrelated_candidate_cannot_reuse_preflight(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    result = m27i.build_preflight(**ctx.kwargs())
    assert not result.artifact.binds_to("some-other-candidate-id")
    assert result.artifact.binds_to(ctx.candidate.candidate_id)


# ---------------------------------------------------------------------------
# Preflight artifact hash validation
# ---------------------------------------------------------------------------


def test_valid_preflight_artifact_passes_validation(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    result = m27i.build_preflight(**ctx.kwargs())
    validation = m27i.validate_preflight_artifact(
        result.artifact.to_json(),
        expected_candidate_id=ctx.candidate.candidate_id,
        now=ctx.now,
    )
    assert validation.valid, validation.reason


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.__setitem__("candidate_id", "forged-candidate-id"),
        lambda payload: payload.__setitem__("executable_price", "0.01"),
        lambda payload: payload["gates"]["m13_verified"].__setitem__("reason", "forged-reason"),
        lambda payload: payload.__setitem__(
            "expires_at",
            (datetime.fromisoformat(payload["expires_at"]) + timedelta(hours=1)).isoformat(),
        ),
        lambda payload: payload.__setitem__("content_hash", "0" * 64),
    ],
)
def test_tampered_preflight_artifact_rejected(tmp_path: Path, mutate) -> None:
    ctx = Context(tmp_path)
    result = m27i.build_preflight(**ctx.kwargs())
    payload = result.artifact.to_json()
    mutate(payload)
    validation = m27i.validate_preflight_artifact(
        payload, expected_candidate_id=ctx.candidate.candidate_id, now=ctx.now
    )
    assert not validation.valid


def test_no_approval_or_acknowledgement_required(tmp_path: Path) -> None:
    import inspect

    signature = inspect.signature(m27i.build_preflight)
    names = set(signature.parameters)
    forbidden = {"acknowledgement", "approval", "approval_hash", "confirmation"}
    assert not (names & forbidden)


def test_m27d_frozen_file_unchanged() -> None:
    assert Decimal("0.20") == m27d.MIN_RESEARCH_DISCREPANCY
    assert Decimal("1.00") == m27d.ONE_CONTRACT
    assert date(2026, 8, 18) == m27d.AUGUST_START
    assert date(2026, 8, 31) == m27d.AUGUST_END
    assert (
        m27d.ACKNOWLEDGEMENT
        == "I UNDERSTAND THE SETTLEMENT PROXY IS UNVALIDATED AND APPROVE THIS ONE "
        "REAL-MONEY WEATHER CANARY"
    )


def _source(path: Path) -> str:
    return path.read_text()


@pytest.mark.parametrize(
    "path",
    [
        Path("services/supervised_canary/m27i.py"),
        Path("services/supervised_canary/candidate_exposure_check.py"),
        Path("services/supervised_canary/m27j.py"),
    ],
)
def test_no_mutation_or_network_write_source(path: Path) -> None:
    text = _source(Path(__file__).resolve().parent.parent / path)
    forbidden = (
        "production_execute",
        "SignAndSendBoundary",
        "urllib.request.Request(",
        '"POST"',
        "'POST'",
        '"DELETE"',
        "'DELETE'",
        '"PATCH"',
        "'PATCH'",
        "arm(",
        "validate_experimental_acknowledgement",
        "ExperimentalApprovalBinding",
    )
    for token in forbidden:
        assert token not in text, f"forbidden token {token!r} found in {path}"
    # Also confirm the module parses to valid, side-effect-free-at-import-time source.
    ast.parse(text)


def test_no_unconditional_pass_literal_in_readiness_gates() -> None:
    """Gemini blocker 3: no M27I readiness gate may be an unconditional literal ``True``.

    Parses ``build_preflight``'s own gate-assembly dict literal (never the whole file, to avoid
    false positives on legitimately data-conditioned branches inside helper functions) and fails
    if any entry whose key is one of the 21 named gates is a direct
    ``GateResult(True, ...)`` call with a literal boolean ``True`` first argument.
    """
    text = _source(Path(__file__).resolve().parent.parent / "services/supervised_canary/m27i.py")
    tree = ast.parse(text)
    build_preflight = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "build_preflight"
    )
    results_dict = next(node for node in ast.walk(build_preflight) if isinstance(node, ast.Dict))
    offenders = []
    for key, value in zip(results_dict.keys, results_dict.values, strict=True):
        if not (isinstance(key, ast.Constant) and key.value in m27i.GATE_NAMES):
            continue
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "GateResult"
            and value.args
            and isinstance(value.args[0], ast.Constant)
            and value.args[0].value is True
        ):
            offenders.append(key.value)
    assert not offenders, f"gates constructed with an unconditional literal True: {offenders}"


# ---------------------------------------------------------------------------
# Operator output truthfulness (M27I.1): render_preflight must only claim what
# this module itself independently proves about THIS operation, never a global
# production arm-state claim it never inspects.
# ---------------------------------------------------------------------------


_M27I_SCOPE_LINES = (
    "M27I_REQUEST_TYPE: READ_ONLY",
    "M27I_ARM_ACTION: NONE",
    "M27I_MUTATION: NO",
    "M27I_CANARY_BURN_ACTION: NONE",
    "M27I_FINAL_ACK_ACTION: NONE",
)

_FALSE_GLOBAL_STATE_TOKENS = (
    "production_state: DISARMED",
    "PRODUCTION_ARMED",
    "PRODUCTION_WRITE_CREDENTIAL",
)


def test_render_preflight_emits_only_m27i_scoped_execution_facts(tmp_path: Path) -> None:
    ctx = Context(tmp_path)
    result = m27i.build_preflight(**ctx.kwargs())
    rendered = m27i.render_preflight(result.artifact)
    for line in _M27I_SCOPE_LINES:
        assert line in rendered
    for token in _FALSE_GLOBAL_STATE_TOKENS:
        assert token not in rendered


def test_render_preflight_blocked_path_emits_only_m27i_scoped_execution_facts(
    tmp_path: Path,
) -> None:
    """The BLOCKED path (this fixture is blocked solely on ``rules_current``) must carry the
    same truthful, scoped execution facts as PREFLIGHT_READY -- never a global state claim."""
    ctx = Context(tmp_path)
    result = m27i.build_preflight(**ctx.kwargs())
    assert result.artifact.state == "BLOCKED"
    rendered = m27i.render_preflight(result.artifact)
    for line in _M27I_SCOPE_LINES:
        assert line in rendered
    for token in _FALSE_GLOBAL_STATE_TOKENS:
        assert token not in rendered


def test_render_preflight_abstain_path_emits_only_m27i_scoped_execution_facts(
    tmp_path: Path,
) -> None:
    """The ABSTAIN early return must not skip the truthful M27I execution-boundary lines."""
    ctx = Context(tmp_path)
    result = m27i.build_preflight(**ctx.kwargs(candidate_inputs=()))
    assert result.artifact.state == "ABSTAIN"
    rendered = m27i.render_preflight(result.artifact)
    for line in _M27I_SCOPE_LINES:
        assert line in rendered
    for token in _FALSE_GLOBAL_STATE_TOKENS:
        assert token not in rendered
    assert f"reason: {result.artifact.abstain_reason}" in rendered


def test_render_preflight_ready_path_emits_only_m27i_scoped_execution_facts(
    tmp_path: Path,
) -> None:
    ctx = Context(tmp_path)
    result = m27i.build_preflight(
        **ctx.kwargs(
            m27j_evidence_path=ctx.m27j_path, m27a_binding_evidence_path=ctx.m27a_binding_path
        )
    )
    assert result.artifact.state == "PREFLIGHT_READY", result.artifact.gates.missing
    rendered = m27i.render_preflight(result.artifact)
    for line in _M27I_SCOPE_LINES:
        assert line in rendered
    for token in _FALSE_GLOBAL_STATE_TOKENS:
        assert token not in rendered
    # Existing PREFLIGHT_READY/rules-current rendering semantics remain unchanged.
    assert "RULES CURRENTNESS: PASS" in rendered
    assert "PREFLIGHT_READY" in rendered


def test_rules_current_rendering_remains_separate_from_market_open_and_book_executable(
    tmp_path: Path,
) -> None:
    ctx = Context(tmp_path)
    result = m27i.build_preflight(**ctx.kwargs())
    artifact = result.artifact
    assert artifact.state == "BLOCKED"
    assert artifact.missing_gates == ("rules_current",)
    rendered = m27i.render_preflight(artifact)
    assert "MARKET OPEN/CURRENT: PASS" in rendered
    assert "BOOK EXECUTABLE: PASS" in rendered
    assert "RULES CURRENTNESS: BLOCKED" in rendered


def test_frozen_files_have_no_working_tree_changes() -> None:
    frozen = (
        "services/supervised_canary/m27d.py",
        "services/production_execution/security_boundary.py",
        "services/production_execution/transport.py",
        "services/forecasting",
    )
    repo_root = Path(__file__).resolve().parent.parent
    diff = subprocess.run(
        ["/usr/bin/git", "diff", "--stat", *frozen],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert diff.stdout.strip() == ""
