from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from services.forecasting.wn_a1_alert import DEFAULT_POLICY
from services.forecasting.wn_a1_attempt_store import (
    AttemptStoreError,
    open_isolated_attempt_store,
    replay_decision,
)
from services.forecasting.wn_a1_current_daily_high_authority import (
    CurrentDailyHighContract,
    CurrentDailyHighRouteState,
    WindowStatus,
)
from services.forecasting.wn_a1_domain import AlertSide, AlertState, WnA1Error
from services.forecasting.wn_a1_probability import compute_member_daily_highs
from services.forecasting.wn_a1_runner import (
    _run_evaluation,
    discover_event,
    evaluate_candidate,
    event_ticker_for,
    research_probability_window,
    run_canonical,
    select_candidate,
)
from services.forecasting.wn_a1_weathernext_evidence import (
    MEMBER_COUNT,
    MODEL,
    UNIT,
    VARIABLE,
    EnsembleStatus,
    build_ensemble_evidence,
)
from services.opportunity_engine.books import DepthWalk, OutcomeSide
from services.opportunity_engine.fees import FeeEstimateQuality
from services.opportunity_engine.live_economics import TakerCost
from tests.test_wn_a1_current_daily_high_authority import EARLY_CLOSE, RULES_SECONDARY, series_raw

INIT_TIME = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
SOURCE_OBJECT = "gs://weathernext3_spatial/weathernext_3_0_0/zarr/2026_to_present/20260914_12hr_XX_preds/predictions.zarr/"


def _fake_response(payload: dict, path: str, observed_at: datetime) -> dict:
    body = json.dumps(payload).encode()
    return {
        "path": path,
        "observed_at": observed_at.isoformat(),
        "status": 200,
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "raw_body_b64": base64.b64encode(body).decode("ascii"),
        "bytes": len(body),
        "classification": "SUCCESS",
        "payload": payload,
    }


def _market_row(ticker: str, strike_type: str, floor_strike, cap_strike, rule_bounds: str) -> dict:
    return {
        "ticker": ticker,
        "event_ticker": "KXHIGHCHI-26SEP15",
        "market_type": "binary",
        "status": "active",
        "rules_primary": (
            "If the maximum temperature recorded at Chicago (CLIMDW) for Sep 15, 2026, is "
            f"{rule_bounds}\N{DEGREE SIGN} fahrenheit according to The Weather Company, "
            "then the market resolves to Yes."
        ),
        "rules_secondary": RULES_SECONDARY,
        "price_level_structure": "linear_cent",
        "price_ranges": [{"start": "0.0000", "end": "1.0000", "step": "0.0100"}],
        "strike_type": strike_type,
        "floor_strike": floor_strike,
        "cap_strike": cap_strike,
        "early_close_condition": EARLY_CLOSE,
    }


def test_event_ticker_naming_matches_observed_live_convention() -> None:
    assert event_ticker_for(date(2026, 9, 15)) == "KXHIGHCHI-26SEP15"


def test_discover_event_routes_every_sibling_market() -> None:
    markets = [
        _market_row("KXHIGHCHI-26SEP15-T87", "greater", 87, None, "greater than 87"),
        _market_row("KXHIGHCHI-26SEP15-T80", "less", None, 80, "less than 80"),
    ]
    event_payload = {
        "event": {
            "event_ticker": "KXHIGHCHI-26SEP15",
            "series_ticker": "KXHIGHCHI",
            "title": "Highest temperature in Chicago on Sep 15, 2026?",
            "settlement_sources": [
                {"name": "The Weather Company", "url": "https://weather.com/kalshi"}
            ],
        },
        "markets": markets,
    }
    now = datetime(2026, 9, 14, 15, 0, tzinfo=UTC)

    def fake_get_event(ticker: str):
        return _fake_response(event_payload, "/trade-api/v2/events/" + ticker, now), b""

    def fake_get_series(path: str):
        return _fake_response({"series": series_raw()}, path, now)

    discovery = discover_event(
        event_ticker="KXHIGHCHI-26SEP15", get_event=fake_get_event, get_series=fake_get_series
    )
    assert len(discovery.routes) == 2
    assert len(discovery.supported) == 2
    assert all(r.state is CurrentDailyHighRouteState.SUPPORTED for r in discovery.routes)
    contracts = discovery.contracts_by_ticker
    assert contracts["KXHIGHCHI-26SEP15-T87"].comparator == "GT"
    assert contracts["KXHIGHCHI-26SEP15-T80"].comparator == "LT"
    # Item 3: the real live authority never establishes the settlement window today.
    assert contracts["KXHIGHCHI-26SEP15-T87"].window_status is WindowStatus.NOT_ESTABLISHED


def test_research_probability_window_is_civil_local_day_and_not_authoritative() -> None:
    start, end = research_probability_window(date(2026, 9, 15))
    assert end - start == timedelta(hours=24)
    assert start.hour == 0
    assert start.tzinfo is not None


def _synthetic_members(kelvin_by_sample: dict[int, Decimal]):
    rows = [
        {
            "sample": s,
            "lead_time_hours": 0,
            "lead_subtime_minutes": 0,
            "valid_time": INIT_TIME,
            "value_kelvin": k,
        }
        for s, k in kelvin_by_sample.items()
    ]
    result = build_ensemble_evidence(
        model=MODEL,
        source_object=SOURCE_OBJECT,
        init_time=INIT_TIME,
        acquired_at=INIT_TIME,
        variable=VARIABLE,
        requested_latitude=Decimal("41.80"),
        requested_longitude=Decimal("-87.75"),
        selected_latitude=Decimal("41.80"),
        selected_longitude=Decimal("-87.75"),
        unit=UNIT,
        raw_rows=rows,
        source_content_hash="c" * 64,
    )
    assert result.status is EnsembleStatus.COMPLETE
    assert result.evidence is not None
    members = compute_member_daily_highs(result.evidence, INIT_TIME, INIT_TIME + timedelta(hours=1))
    return result.evidence, members


def _t87_contract():
    markets = [_market_row("KXHIGHCHI-26SEP15-T87", "greater", 87, None, "greater than 87")]
    event_payload = {
        "event": {
            "event_ticker": "KXHIGHCHI-26SEP15",
            "series_ticker": "KXHIGHCHI",
            "title": "x",
            "settlement_sources": [
                {"name": "The Weather Company", "url": "https://weather.com/kalshi"}
            ],
        },
        "markets": markets,
    }
    now = datetime(2026, 9, 14, 15, 0, tzinfo=UTC)
    discovery = discover_event(
        event_ticker="KXHIGHCHI-26SEP15",
        get_event=lambda t: (_fake_response(event_payload, "x", now), b""),
        get_series=lambda p: _fake_response({"series": series_raw()}, p, now),
    )
    return discovery.contracts_by_ticker


def _established_window_contract(contract: CurrentDailyHighContract) -> CurrentDailyHighContract:
    """Only for testing the alert pipeline's TAKE A LOOK mechanics in isolation: hand-sets
    an established window on an otherwise-real contract. The real live authority
    (``route_current_daily_high``) never produces this today -- see item 3 / test_wn_a1_
    current_daily_high_authority.py's window tests for that boundary."""
    from dataclasses import replace

    return replace(contract, window_status=WindowStatus.ESTABLISHED_CIVIL_LOCAL_DAY)


def _fake_taker_cost(total_cost: Decimal, fee: Decimal = Decimal("0")) -> TakerCost:
    depth = DepthWalk(
        requested=Decimal(1),
        filled=Decimal(1),
        average_price=total_cost,
        worst_price=total_cost,
        unfilled=Decimal(0),
        total_cost=total_cost,
        levels_consumed=1,
    )
    return TakerCost(
        OutcomeSide.YES,
        depth,
        fee,
        fee,
        None,
        None,
        None,
        FeeEstimateQuality.DETERMINISTIC_FORMULA_ONLY,
    )


def _fake_snapshot(yes_debit: Decimal | None, no_debit: Decimal | None, observed_at: datetime):
    from services.forecasting.wn_a1_market_economics import KalshiMarketSnapshot

    return KalshiMarketSnapshot(
        market=None,  # type: ignore[arg-type]
        event=None,  # type: ignore[arg-type]
        series=None,  # type: ignore[arg-type]
        yes_best_ask=yes_debit,
        no_best_ask=no_debit,
        yes_taker_cost=None if yes_debit is None else _fake_taker_cost(yes_debit),
        no_taker_cost=None if no_debit is None else _fake_taker_cost(no_debit),
        quantity=Decimal(1),
        fee_policy=None,  # type: ignore[arg-type]
        market_observed_at=observed_at,
        orderbook_observed_at=observed_at,
        snapshot_identity="fake-snapshot",
    )


def test_end_to_end_too_uncertain_because_live_window_is_unestablished() -> None:
    """Item 3's real consequence: the real live authority never establishes the settlement
    window, so even a clearing side-aware gap is hard-capped at TOO UNCERTAIN, never
    TAKE A LOOK, through the real discover_event/route_current_daily_high path."""
    contracts = _t87_contract()
    # 42/64 members (~66%) forecast hot enough to trigger the >87F contract.
    kelvin = {i: Decimal("305.0") if i < 42 else Decimal("299.0") for i in range(MEMBER_COUNT)}
    evidence, members = _synthetic_members(kelvin)
    evaluated_at = datetime(2026, 9, 14, 15, 0, tzinfo=UTC)
    outcome = evaluate_candidate(
        candidate_ticker="KXHIGHCHI-26SEP15-T87",
        sibling_contracts=contracts,
        members=members,
        weathernext_evidence_identity=evidence.evidence_identity,
        ensemble_status=EnsembleStatus.COMPLETE,
        snapshot=_fake_snapshot(Decimal("0.27"), Decimal("0.90"), evaluated_at),
        evaluated_at=evaluated_at,
    )
    assert outcome.decision.state is AlertState.TOO_UNCERTAIN
    assert outcome.decision.side is AlertSide.YES
    assert "TOO UNCERTAIN" in outcome.primary_alert_text
    assert "YES is worth checking" in outcome.primary_alert_text
    assert "Nothing has been bought." in outcome.primary_alert_text
    assert outcome.record.decision_state == "TOO UNCERTAIN"


def test_evaluate_candidate_reaches_take_a_look_once_window_is_established() -> None:
    """Proves the alert pipeline mechanics themselves (not the live window blocker)."""
    contracts = {ticker: _established_window_contract(c) for ticker, c in _t87_contract().items()}
    kelvin = {i: Decimal("305.0") if i < 42 else Decimal("299.0") for i in range(MEMBER_COUNT)}
    evidence, members = _synthetic_members(kelvin)
    evaluated_at = datetime(2026, 9, 14, 15, 0, tzinfo=UTC)
    outcome = evaluate_candidate(
        candidate_ticker="KXHIGHCHI-26SEP15-T87",
        sibling_contracts=contracts,
        members=members,
        weathernext_evidence_identity=evidence.evidence_identity,
        ensemble_status=EnsembleStatus.COMPLETE,
        snapshot=_fake_snapshot(Decimal("0.27"), Decimal("0.90"), evaluated_at),
        evaluated_at=evaluated_at,
    )
    assert outcome.decision.state is AlertState.TAKE_A_LOOK
    assert outcome.decision.side is AlertSide.YES
    assert "TAKE A LOOK" in outcome.primary_alert_text
    assert "YES is worth checking" in outcome.primary_alert_text


def test_end_to_end_no_side_take_a_look_once_window_is_established() -> None:
    contracts = {ticker: _established_window_contract(c) for ticker, c in _t87_contract().items()}
    # 8/64 members (~12.5%) clear 87F -- a cheap NO side is what's actually worth taking.
    kelvin = {i: Decimal("304.5") if i < 8 else Decimal("299.0") for i in range(MEMBER_COUNT)}
    evidence, members = _synthetic_members(kelvin)
    evaluated_at = datetime(2026, 9, 14, 15, 0, tzinfo=UTC)
    outcome = evaluate_candidate(
        candidate_ticker="KXHIGHCHI-26SEP15-T87",
        sibling_contracts=contracts,
        members=members,
        weathernext_evidence_identity=evidence.evidence_identity,
        ensemble_status=EnsembleStatus.COMPLETE,
        snapshot=_fake_snapshot(Decimal("0.80"), Decimal("0.60"), evaluated_at),
        evaluated_at=evaluated_at,
    )
    assert outcome.decision.state is AlertState.TAKE_A_LOOK
    assert outcome.decision.side is AlertSide.NO
    assert "NO is worth checking" in outcome.primary_alert_text


def test_end_to_end_skip_because_neither_side_clears_the_buffer() -> None:
    contracts = _t87_contract()
    # 18/64 members (~28%) clear 87F; both sides priced too close to cost to be worth it.
    kelvin = {i: Decimal("304.5") if i < 18 else Decimal("300.0") for i in range(MEMBER_COUNT)}
    evidence, members = _synthetic_members(kelvin)
    evaluated_at = datetime(2026, 9, 14, 15, 0, tzinfo=UTC)
    outcome = evaluate_candidate(
        candidate_ticker="KXHIGHCHI-26SEP15-T87",
        sibling_contracts=contracts,
        members=members,
        weathernext_evidence_identity=evidence.evidence_identity,
        ensemble_status=EnsembleStatus.COMPLETE,
        snapshot=_fake_snapshot(Decimal("0.25"), Decimal("0.78"), evaluated_at),
        evaluated_at=evaluated_at,
    )
    assert outcome.decision.state is AlertState.SKIP
    assert "SKIP" in outcome.primary_alert_text


def test_absolute_gap_counterexample_skips_through_the_full_pipeline() -> None:
    """Item 4's required counterexample, exercised through evaluate_candidate end-to-end:
    model YES~10%, YES ask~40% (30pp absolute gap), NO all-in debit 95% -> SKIP."""
    contracts = _t87_contract()
    kelvin = {i: Decimal("299.0") if i < 6 else Decimal("295.0") for i in range(MEMBER_COUNT)}
    evidence, members = _synthetic_members(kelvin)
    evaluated_at = datetime(2026, 9, 14, 15, 0, tzinfo=UTC)
    outcome = evaluate_candidate(
        candidate_ticker="KXHIGHCHI-26SEP15-T87",
        sibling_contracts=contracts,
        members=members,
        weathernext_evidence_identity=evidence.evidence_identity,
        ensemble_status=EnsembleStatus.COMPLETE,
        snapshot=_fake_snapshot(Decimal("0.40"), Decimal("0.95"), evaluated_at),
        evaluated_at=evaluated_at,
    )
    assert outcome.decision.state is AlertState.SKIP
    assert outcome.decision.side is None


def test_data_not_ready_when_ensemble_missing() -> None:
    contracts = _t87_contract()
    evaluated_at = datetime(2026, 9, 14, 15, 0, tzinfo=UTC)
    outcome = evaluate_candidate(
        candidate_ticker="KXHIGHCHI-26SEP15-T87",
        sibling_contracts=contracts,
        members=None,
        weathernext_evidence_identity=None,
        ensemble_status=EnsembleStatus.INCOMPLETE,
        snapshot=_fake_snapshot(Decimal("0.25"), Decimal("0.78"), evaluated_at),
        evaluated_at=evaluated_at,
    )
    assert outcome.decision.state is AlertState.DATA_NOT_READY
    assert "DATA NOT READY" in outcome.primary_alert_text


def test_select_candidate_picks_the_largest_supported_buffered_gap() -> None:
    contracts = {**_t87_contract()}
    markets = [_market_row("KXHIGHCHI-26SEP15-T80", "less", None, 80, "less than 80")]
    event_payload = {
        "event": {
            "event_ticker": "KXHIGHCHI-26SEP15",
            "series_ticker": "KXHIGHCHI",
            "title": "x",
            "settlement_sources": [
                {"name": "The Weather Company", "url": "https://weather.com/kalshi"}
            ],
        },
        "markets": markets,
    }
    now = datetime(2026, 9, 14, 15, 0, tzinfo=UTC)
    other_discovery = discover_event(
        event_ticker="KXHIGHCHI-26SEP15",
        get_event=lambda t: (_fake_response(event_payload, "x", now), b""),
        get_series=lambda p: _fake_response({"series": series_raw()}, p, now),
    )
    contracts.update(other_discovery.contracts_by_ticker)
    kelvin = {i: Decimal("305.0") if i < 42 else Decimal("299.0") for i in range(MEMBER_COUNT)}
    _evidence, members = _synthetic_members(kelvin)
    debits = {
        # T87: 42/64 (~65.6%) forecast hot -> YES is cheap at 0.27 -> clears easily.
        "KXHIGHCHI-26SEP15-T87": (Decimal("0.27"), Decimal("0.90")),
        # T80: 22/64 (~34.4%) forecast under 80F -> debits priced at fair value -> no edge.
        "KXHIGHCHI-26SEP15-T80": (Decimal("0.34"), Decimal("0.66")),
    }
    best = select_candidate(contracts, members, debits)
    assert best == "KXHIGHCHI-26SEP15-T87"


def test_select_candidate_returns_none_when_nothing_qualifies() -> None:
    contracts = _t87_contract()
    # 32/64 (50%) forecast hot; debits priced right at fair value -> no side has an edge.
    kelvin = {i: Decimal("305.0") if i < 32 else Decimal("299.0") for i in range(MEMBER_COUNT)}
    _evidence, members = _synthetic_members(kelvin)
    debits = {"KXHIGHCHI-26SEP15-T87": (Decimal("0.50"), Decimal("0.51"))}
    assert select_candidate(contracts, members, debits) is None


# --- Item 5: the canonical entrypoint and durable persistence --------------------------


def test_run_canonical_accepts_no_transport_override_parameters() -> None:
    """The one canonical entrypoint must be structurally incapable of taking a fake
    reader/getter -- a fixture cannot masquerade as a genuine live acquisition."""
    import inspect

    params = inspect.signature(run_canonical).parameters
    forbidden = {
        "get_event",
        "get_series",
        "get_market",
        "get_orderbook",
        "reader",
        "weathernext_reader",
    }
    assert forbidden.isdisjoint(params)


def test_canonical_composition_seam_persists_every_evaluated_attempt(tmp_path) -> None:
    """Exercises the real ``_run_evaluation`` composition seam end-to-end with fully
    injected fakes (the documented internal/fixture-only seam ``run_canonical`` itself
    never exposes), and proves every attempt -- including a DATA NOT READY one -- lands in
    the durable store and is replayable."""
    markets = [_market_row("KXHIGHCHI-26SEP15-T87", "greater", 87, None, "greater than 87")]
    event_payload = {
        "event": {
            "event_ticker": "KXHIGHCHI-26SEP15",
            "series_ticker": "KXHIGHCHI",
            "title": "x",
            "settlement_sources": [
                {"name": "The Weather Company", "url": "https://weather.com/kalshi"}
            ],
        },
        "markets": markets,
    }
    now = datetime(2026, 9, 14, 15, 0, tzinfo=UTC)

    def fake_get_event(ticker: str):
        return _fake_response(event_payload, "x", now), b""

    def fake_get_series(path: str):
        return _fake_response({"series": series_raw()}, path, now)

    def fake_get_market(ticker: str) -> dict[str, object]:
        # Simulates a real Kalshi market/orderbook acquisition failure (e.g. network or
        # validation error): acquire_market_snapshot must surface this as WnA1Error, which
        # _run_evaluation catches and records as snapshot=None -> DATA NOT READY.
        raise WnA1Error("simulated Kalshi market acquisition failure")

    def fake_get_orderbook(ticker: str):
        raise WnA1Error("simulated Kalshi orderbook acquisition failure")

    def fake_weathernext_reader(
        source_object, init_time, latitude, longitude, window_start, window_end
    ):
        rows = [
            {
                "sample": s,
                "lead_time_hours": 0,
                "lead_subtime_minutes": 0,
                "valid_time": init_time,
                "value_kelvin": Decimal("305.0") if s < 42 else Decimal("299.0"),
            }
            for s in range(MEMBER_COUNT)
        ]
        return rows, "d" * 64, Decimal("41.80"), Decimal("-87.75")

    store = open_isolated_attempt_store(tmp_path)
    outcomes = _run_evaluation(
        target_local_date=date(2026, 9, 15),
        weathernext_init_time=INIT_TIME,
        weathernext_source_object=SOURCE_OBJECT,
        weathernext_requested_latitude=Decimal("41.80"),
        weathernext_requested_longitude=Decimal("-87.75"),
        evaluated_at=now,
        store=store,
        policy=DEFAULT_POLICY,
        get_event=fake_get_event,
        get_series=fake_get_series,
        get_market=fake_get_market,
        get_orderbook=fake_get_orderbook,
        weathernext_reader=fake_weathernext_reader,
    )
    assert len(outcomes) == 1
    outcome = outcomes[0]
    # Kalshi market/orderbook acquisition failed (fake raises) -> no executable price ->
    # DATA NOT READY -- and it must still be durably persisted.
    assert outcome.decision.state is AlertState.DATA_NOT_READY
    assert store.count() == 1

    persisted = store.get(outcome.record.record_id)
    assert persisted.market_ticker == "KXHIGHCHI-26SEP15-T87"
    assert persisted.research_only is True
    assert persisted.production_influence == Decimal(0)
    assert persisted.weathernext_evidence_identity is not None
    assert len(persisted.weathernext_members) == MEMBER_COUNT
    assert persisted.weathernext_selected_latitude == Decimal("41.80")

    # Fresh-process replay: reopen the persisted row and reproduce the decision.
    replayed = replay_decision(persisted)
    assert replayed.state == outcome.decision.state
    assert replayed.side == outcome.decision.side


def test_canonical_composition_seam_rejects_duplicate_attempt(tmp_path) -> None:
    markets = [_market_row("KXHIGHCHI-26SEP15-T87", "greater", 87, None, "greater than 87")]
    event_payload = {
        "event": {
            "event_ticker": "KXHIGHCHI-26SEP15",
            "series_ticker": "KXHIGHCHI",
            "title": "x",
            "settlement_sources": [
                {"name": "The Weather Company", "url": "https://weather.com/kalshi"}
            ],
        },
        "markets": markets,
    }
    now = datetime(2026, 9, 14, 15, 0, tzinfo=UTC)

    def fake_get_event(ticker: str):
        return _fake_response(event_payload, "x", now), b""

    def fake_get_series(path: str):
        return _fake_response({"series": series_raw()}, path, now)

    def fake_get_market(ticker: str) -> dict[str, object]:
        raise WnA1Error("simulated Kalshi market acquisition failure")

    def fake_get_orderbook(ticker: str):
        raise WnA1Error("simulated Kalshi orderbook acquisition failure")

    def fake_weathernext_reader(
        source_object, init_time, latitude, longitude, window_start, window_end
    ):
        return [], "e" * 64, Decimal("41.80"), Decimal("-87.75")

    store = open_isolated_attempt_store(tmp_path)
    kwargs = dict(
        target_local_date=date(2026, 9, 15),
        weathernext_init_time=INIT_TIME,
        weathernext_source_object=SOURCE_OBJECT,
        weathernext_requested_latitude=Decimal("41.80"),
        weathernext_requested_longitude=Decimal("-87.75"),
        evaluated_at=now,
        store=store,
        policy=DEFAULT_POLICY,
        get_event=fake_get_event,
        get_series=fake_get_series,
        get_market=fake_get_market,
        get_orderbook=fake_get_orderbook,
        weathernext_reader=fake_weathernext_reader,
    )
    _run_evaluation(**kwargs)
    with pytest.raises(AttemptStoreError, match="duplicate"):
        _run_evaluation(**kwargs)
