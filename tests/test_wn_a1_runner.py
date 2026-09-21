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
    _run_invocation_id,
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


def _fixed_clock(value: datetime):
    """A deterministic injected clock (Item E) that always returns the same instant.

    Since every chronology requirement in Item E is a non-strict ``<=``, a single fixed
    instant for every ``clock()`` call within one ``_run_evaluation`` invocation is a valid
    (if degenerate) chronology and keeps most fixtures simple; tests that specifically need
    to prove ordering use ``_advancing_clock`` instead.
    """
    return lambda: value


def _advancing_clock(*values: datetime):
    """An injected clock (Item E) that returns each of ``values`` in order, one per call --
    for tests that need to prove ``pipeline_started_at < weathernext_acquired_at <
    decision_at`` are genuinely distinct, ordered instants rather than one fixed timestamp.
    """
    iterator = iter(values)
    return lambda: next(iterator)


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
            "lead_subtime_hours": 0,
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
    reader/getter/clock -- a fixture cannot masquerade as a genuine live acquisition, and no
    caller can backdate a canonical decision (Item E)."""
    import inspect

    params = inspect.signature(run_canonical).parameters
    forbidden = {
        "get_event",
        "get_series",
        "get_market",
        "get_orderbook",
        "reader",
        "weathernext_reader",
        "store",
        "policy",
        "evaluated_at",
        "clock",
    }
    assert forbidden.isdisjoint(params)


def test_run_canonical_signature_is_exactly_the_evaluation_identity_inputs() -> None:
    """Item C/E's static/signature regression: ``run_canonical`` must expose ONLY the
    target-date / WeatherNext-run identity inputs needed to identify the requested
    evaluation -- never a store, policy, transport, reader, or clock override of any kind.
    A caller-selected persistence destination, policy, or timestamp would let a fixture
    masquerade as a genuine canonical live run, or let a caller backdate a decision."""
    import inspect

    params = set(inspect.signature(run_canonical).parameters)
    assert params == {
        "target_local_date",
        "weathernext_init_time",
        "weathernext_source_object",
        "weathernext_requested_latitude",
        "weathernext_requested_longitude",
    }


def test_run_invocation_id_has_no_wall_clock_input() -> None:
    """Item E/F static proof: the run-identity function itself has no wall-clock/evaluation-
    timestamp parameter -- duplicate-run detection is purely a function of the requested
    evaluation's logical identity (event/date/WeatherNext-run/policy), never of when it
    happened to be attempted. ``weathernext_init_time`` is the requested forecast run
    identity, not a wall clock, so it is expected and excluded from this check."""
    import inspect

    params = set(inspect.signature(_run_invocation_id).parameters)
    assert params == {
        "event_ticker",
        "target_local_date",
        "weathernext_init_time",
        "weathernext_source_object",
        "weathernext_requested_latitude",
        "weathernext_requested_longitude",
        "policy_version",
    }
    assert "evaluated_at" not in params
    assert "clock" not in params


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
                "lead_subtime_hours": 0,
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
        clock=_fixed_clock(now),
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
    # Item A: no market snapshot was acquired (fake raises) -> that unavailable state is
    # bound into the persisted attempt, never a hardcoded freshness assumption.
    assert persisted.kalshi_orderbook_observed_at is None

    # Fresh-process replay: reopen the persisted row and reproduce the decision.
    replayed = replay_decision(persisted)
    assert replayed.state == outcome.decision.state
    assert replayed.side == outcome.decision.side

    # Item F3: the attempt binds to its parent run.
    run_id = _run_invocation_id(
        event_ticker="KXHIGHCHI-26SEP15",
        target_local_date=date(2026, 9, 15),
        weathernext_init_time=INIT_TIME,
        weathernext_source_object=SOURCE_OBJECT,
        weathernext_requested_latitude=Decimal("41.80"),
        weathernext_requested_longitude=Decimal("-87.75"),
        policy_version=DEFAULT_POLICY.version,
    )
    assert persisted.run_id == run_id
    assert store.attempts_for_run(run_id) == (persisted,)

    # Item F: exactly one START and one COMPLETED terminal record exist for this invocation.
    assert store.run_start_count() == 1
    assert store.run_terminal_count() == 1
    assert store.run_status(run_id) == "COMPLETED"
    terminal = store.get_run_terminal(run_id)
    assert terminal.discovery_state == "OK"
    assert terminal.weathernext_state == "COMPLETE"
    assert terminal.attempt_ids == (outcome.record.record_id,)
    assert terminal.status == "COMPLETED"
    assert terminal.research_only is True
    assert terminal.production_influence == Decimal(0)


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
        clock=_fixed_clock(now),
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


def test_duplicate_run_identity_rejected_before_any_external_acquisition(tmp_path) -> None:
    """Item F's required attack test: register/run one invocation, then attempt the EXACT
    same run identity again with getters/readers that would return CHANGED market evidence
    and would raise loudly if called at all -- duplicate rejection must occur BEFORE any
    getter/reader is ever invoked, and no additional market-attempt rows may be created."""
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
        rows = [
            {
                "sample": s,
                "lead_time_hours": 0,
                "lead_subtime_hours": 0,
                "valid_time": init_time,
                "value_kelvin": Decimal("305.0") if s < 42 else Decimal("299.0"),
            }
            for s in range(MEMBER_COUNT)
        ]
        return rows, "d" * 64, Decimal("41.80"), Decimal("-87.75")

    store = open_isolated_attempt_store(tmp_path)
    first_kwargs = dict(
        target_local_date=date(2026, 9, 15),
        weathernext_init_time=INIT_TIME,
        weathernext_source_object=SOURCE_OBJECT,
        weathernext_requested_latitude=Decimal("41.80"),
        weathernext_requested_longitude=Decimal("-87.75"),
        clock=_fixed_clock(now),
        store=store,
        policy=DEFAULT_POLICY,
        get_event=fake_get_event,
        get_series=fake_get_series,
        get_market=fake_get_market,
        get_orderbook=fake_get_orderbook,
        weathernext_reader=fake_weathernext_reader,
    )
    _run_evaluation(**first_kwargs)
    assert store.count() == 1
    assert store.run_terminal_count() == 1

    def poisoned_get_event(ticker: str):
        raise AssertionError("duplicate run must be rejected before any getter is called")

    def poisoned_get_series(path: str):
        raise AssertionError("duplicate run must be rejected before any getter is called")

    def poisoned_get_market(ticker: str) -> dict[str, object]:
        raise AssertionError("duplicate run must be rejected before any getter is called")

    def poisoned_get_orderbook(ticker: str):
        raise AssertionError("duplicate run must be rejected before any getter is called")

    def poisoned_weathernext_reader(*args, **kwargs):
        raise AssertionError("duplicate run must be rejected before any reader is called")

    # Same exact logical run identity (same date/WeatherNext-run/policy), a different
    # wall-clock instant, and getters that would return entirely different (and here,
    # loudly-failing) evidence if ever invoked.
    with pytest.raises(AttemptStoreError, match="duplicate"):
        _run_evaluation(
            target_local_date=date(2026, 9, 15),
            weathernext_init_time=INIT_TIME,
            weathernext_source_object=SOURCE_OBJECT,
            weathernext_requested_latitude=Decimal("41.80"),
            weathernext_requested_longitude=Decimal("-87.75"),
            clock=_fixed_clock(now + timedelta(hours=1)),
            store=store,
            policy=DEFAULT_POLICY,
            get_event=poisoned_get_event,
            get_series=poisoned_get_series,
            get_market=poisoned_get_market,
            get_orderbook=poisoned_get_orderbook,
            weathernext_reader=poisoned_weathernext_reader,
        )
    # No poisoned getter/reader was ever called (none raised its AssertionError instead of
    # the expected AttemptStoreError), and no additional market-attempt row was created.
    assert store.count() == 1
    assert store.run_terminal_count() == 1


# --- Item D/F: every invocation leaves a durable, pre-registered run-level trace --------


def _base_run_kwargs(store, *, get_event, get_series, get_market, get_orderbook, reader):
    return dict(
        target_local_date=date(2026, 9, 15),
        weathernext_init_time=INIT_TIME,
        weathernext_source_object=SOURCE_OBJECT,
        weathernext_requested_latitude=Decimal("41.80"),
        weathernext_requested_longitude=Decimal("-87.75"),
        clock=_fixed_clock(datetime(2026, 9, 14, 15, 0, tzinfo=UTC)),
        store=store,
        policy=DEFAULT_POLICY,
        get_event=get_event,
        get_series=get_series,
        get_market=get_market,
        get_orderbook=get_orderbook,
        weathernext_reader=reader,
    )


def _base_run_id() -> str:
    return _run_invocation_id(
        event_ticker="KXHIGHCHI-26SEP15",
        target_local_date=date(2026, 9, 15),
        weathernext_init_time=INIT_TIME,
        weathernext_source_object=SOURCE_OBJECT,
        weathernext_requested_latitude=Decimal("41.80"),
        weathernext_requested_longitude=Decimal("-87.75"),
        policy_version=DEFAULT_POLICY.version,
    )


def test_discovery_failure_still_leaves_a_durable_run_record(tmp_path) -> None:
    """If Kalshi event discovery itself fails, run_canonical must not silently return zero
    records with no trace -- a durable run-level record must still exist."""

    def failing_get_event(ticker: str):
        raise WnA1Error("simulated Kalshi event discovery failure")

    def unused_get_series(path: str):
        raise AssertionError("should not be called")

    def unused_reader(*args, **kwargs):
        return [], "f" * 64, Decimal("41.80"), Decimal("-87.75")

    store = open_isolated_attempt_store(tmp_path)
    kwargs = _base_run_kwargs(
        store,
        get_event=failing_get_event,
        get_series=unused_get_series,
        get_market=lambda t: (_ for _ in ()).throw(WnA1Error("unused")),
        get_orderbook=lambda t: (_ for _ in ()).throw(WnA1Error("unused")),
        reader=unused_reader,
    )
    outcomes = _run_evaluation(**kwargs)
    assert outcomes == ()
    assert store.count() == 0  # no market was ever evaluated
    assert store.run_start_count() == 1
    assert store.run_terminal_count() == 1

    run_id = _base_run_id()
    assert store.run_status(run_id) == "COMPLETED"
    terminal = store.get_run_terminal(run_id)
    assert terminal.discovery_state == "DISCOVERY_FAILED"
    assert "simulated Kalshi event discovery failure" in (terminal.discovery_reason or "")
    assert terminal.attempt_ids == ()
    assert terminal.status == "COMPLETED"
    assert terminal.research_only is True
    assert terminal.production_influence == Decimal(0)

    # Duplicate exact invocation identity must fail closed.
    with pytest.raises(AttemptStoreError, match="duplicate"):
        _run_evaluation(**kwargs)


def test_all_routes_abstained_still_leaves_a_durable_run_record(tmp_path) -> None:
    """Every route ABSTAINing (e.g. Kalshi's rule shape changed) must still be durably
    represented, even though zero markets were ever supported/evaluated."""
    markets = [_market_row("KXHIGHCHI-26SEP15-T87", "greater", 87, None, "greater than 87")]
    markets[0]["rules_primary"] = "unsupported rule shape"
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

    def unused_reader(*args, **kwargs):
        return [], "f" * 64, Decimal("41.80"), Decimal("-87.75")

    store = open_isolated_attempt_store(tmp_path)
    kwargs = _base_run_kwargs(
        store,
        get_event=fake_get_event,
        get_series=fake_get_series,
        get_market=lambda t: (_ for _ in ()).throw(WnA1Error("unused")),
        get_orderbook=lambda t: (_ for _ in ()).throw(WnA1Error("unused")),
        reader=unused_reader,
    )
    outcomes = _run_evaluation(**kwargs)
    assert outcomes == ()
    assert store.count() == 0
    assert store.run_start_count() == 1
    assert store.run_terminal_count() == 1

    terminal = store.get_run_terminal(_base_run_id())
    assert terminal.discovery_state == "OK"
    assert len(terminal.route_outcomes) == 1
    ticker, state, reason = terminal.route_outcomes[0]
    assert ticker == "KXHIGHCHI-26SEP15-T87"
    assert state == "ABSTAIN"
    assert reason is not None
    assert terminal.attempt_ids == ()
    assert terminal.status == "COMPLETED"


def test_run_start_registered_before_discovery_or_acquisition(tmp_path) -> None:
    """Item F1: the START record must exist even if EVERY subsequent step fails -- proving
    it really is written first, not merely "early". Discovery and WeatherNext acquisition
    are independent phases (one failing does not skip the other), so only ``get_market``/
    ``get_orderbook`` are structurally unreachable here (discovery fails -> zero routes ->
    the per-market loop never runs) -- those two fixtures assert they are never called."""

    def failing_get_event(ticker: str):
        raise WnA1Error("simulated Kalshi event discovery failure")

    def failing_get_series(path: str):
        raise WnA1Error("unused")

    def failing_reader(*args, **kwargs):
        raise WnA1Error("simulated WeatherNext acquisition failure")

    store = open_isolated_attempt_store(tmp_path)
    kwargs = _base_run_kwargs(
        store,
        get_event=failing_get_event,
        get_series=failing_get_series,
        get_market=lambda t: (_ for _ in ()).throw(AssertionError("unreachable: no routes")),
        get_orderbook=lambda t: (_ for _ in ()).throw(AssertionError("unreachable: no routes")),
        reader=failing_reader,
    )
    _run_evaluation(**kwargs)
    run_id = _base_run_id()
    start = store.get_run_start(run_id)
    assert start.run_id == run_id
    assert start.research_only is True
    assert start.production_influence == Decimal(0)
    terminal = store.get_run_terminal(run_id)
    assert terminal.discovery_state == "DISCOVERY_FAILED"
    assert terminal.weathernext_state == "ACQUISITION_FAILED"


# --- Item E: internally-captured, distinguished, and ordered timestamps -----------------


def _successful_snapshot_fixtures(market_row: dict, event_payload: dict, observed_at: datetime):
    """A genuine successful ``acquire_market_snapshot`` path (unlike this file's other
    fixtures, which only ever exercise the "Kalshi acquisition failed" -> snapshot=None
    branch) -- needed to prove Item E's chronology over a real orderbook_observed_at."""

    def fake_get_market(ticker: str) -> dict[str, object]:
        return _fake_response({"market": market_row}, "x", observed_at)

    def fake_get_event(ticker: str):
        return _fake_response(event_payload, "x", observed_at), b""

    def fake_get_series(path: str):
        return _fake_response({"series": series_raw()}, path, observed_at)

    def fake_get_orderbook(ticker: str):
        payload = {
            "orderbooks": [
                {
                    "orderbook_fp": {
                        "yes_dollars": [["0.30", "10"]],
                        "no_dollars": [["0.65", "10"]],
                    }
                }
            ]
        }
        return _fake_response(payload, "x", observed_at), b""

    return fake_get_market, fake_get_event, fake_get_series, fake_get_orderbook


def test_decision_at_is_captured_after_and_ordered_past_required_evidence(tmp_path) -> None:
    """Items E2/E4/E5: ``pipeline_started_at`` < WeatherNext ``acquired_at`` <=
    ``decision_at``, and the Kalshi response ``observed_at`` values the decision actually
    used are <= ``decision_at`` -- proven end-to-end through a genuine successful market
    snapshot (not just the market-acquisition-failure fixtures used elsewhere), with three
    distinct internally-captured instants (never one collapsed field, never a caller
    parameter)."""
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
    kalshi_observed_at = datetime(2026, 9, 14, 14, 58, 0, tzinfo=UTC)
    fake_get_market, fake_get_event, fake_get_series, fake_get_orderbook = (
        _successful_snapshot_fixtures(markets[0], event_payload, kalshi_observed_at)
    )

    def fake_weathernext_reader(
        source_object, init_time, latitude, longitude, window_start, window_end
    ):
        rows = [
            {
                "sample": s,
                "lead_time_hours": 0,
                "lead_subtime_hours": 0,
                "valid_time": init_time,
                "value_kelvin": Decimal("305.0") if s < 42 else Decimal("299.0"),
            }
            for s in range(MEMBER_COUNT)
        ]
        return rows, "d" * 64, Decimal("41.80"), Decimal("-87.75")

    pipeline_started_at = datetime(2026, 9, 14, 14, 59, 0, tzinfo=UTC)
    weathernext_acquired_at = datetime(2026, 9, 14, 14, 59, 30, tzinfo=UTC)
    decision_at = datetime(2026, 9, 14, 15, 0, 0, tzinfo=UTC)
    terminal_at = datetime(2026, 9, 14, 15, 0, 1, tzinfo=UTC)
    clock = _advancing_clock(pipeline_started_at, weathernext_acquired_at, decision_at, terminal_at)

    store = open_isolated_attempt_store(tmp_path)
    outcomes = _run_evaluation(
        target_local_date=date(2026, 9, 15),
        weathernext_init_time=INIT_TIME,
        weathernext_source_object=SOURCE_OBJECT,
        weathernext_requested_latitude=Decimal("41.80"),
        weathernext_requested_longitude=Decimal("-87.75"),
        clock=clock,
        store=store,
        policy=DEFAULT_POLICY,
        get_event=fake_get_event,
        get_series=fake_get_series,
        get_market=fake_get_market,
        get_orderbook=fake_get_orderbook,
        weathernext_reader=fake_weathernext_reader,
    )
    assert len(outcomes) == 1
    persisted = store.get(outcomes[0].record.record_id)

    # Three genuinely distinct, correctly-ordered instants -- never collapsed into one.
    assert persisted.weathernext_acquired_at == weathernext_acquired_at
    assert persisted.evaluated_at == decision_at  # the per-market decision_at
    assert persisted.kalshi_orderbook_observed_at == kalshi_observed_at
    assert pipeline_started_at < weathernext_acquired_at < decision_at
    assert kalshi_observed_at <= decision_at

    run_id = _run_invocation_id(
        event_ticker="KXHIGHCHI-26SEP15",
        target_local_date=date(2026, 9, 15),
        weathernext_init_time=INIT_TIME,
        weathernext_source_object=SOURCE_OBJECT,
        weathernext_requested_latitude=Decimal("41.80"),
        weathernext_requested_longitude=Decimal("-87.75"),
        policy_version=DEFAULT_POLICY.version,
    )
    start = store.get_run_start(run_id)
    assert start.pipeline_started_at == pipeline_started_at
    terminal = store.get_run_terminal(run_id)
    assert terminal.weathernext_acquired_at == weathernext_acquired_at
    assert terminal.terminal_at == terminal_at
    assert terminal.pipeline_started_at < terminal.weathernext_acquired_at < terminal_at


def test_weathernext_acquisition_rejects_when_captured_timestamp_precedes_init_time(
    tmp_path,
) -> None:
    """Items B/E3: if the internally-captured WeatherNext acquisition timestamp somehow
    precedes ``weathernext_init_time`` (e.g. severe clock skew), the existing chronology
    check in ``build_ensemble_evidence`` fails closed and the run records
    ACQUISITION_FAILED -- never silently accepting a forecast as available before it could
    exist. This is the exact init-after-acquisition rejection, exercised through the real
    internal-clock acquisition boundary rather than a caller-supplied ``acquired_at``."""
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
    kalshi_observed_at = datetime(2026, 9, 14, 11, 0, 0, tzinfo=UTC)
    fake_get_market, fake_get_event, fake_get_series, fake_get_orderbook = (
        _successful_snapshot_fixtures(markets[0], event_payload, kalshi_observed_at)
    )

    def fake_weathernext_reader(
        source_object, init_time, latitude, longitude, window_start, window_end
    ):
        return [], "d" * 64, Decimal("41.80"), Decimal("-87.75")

    pipeline_started_at = INIT_TIME - timedelta(hours=1)
    # The clock returned for the WeatherNext acquisition boundary is BEFORE
    # weathernext_init_time -- chronologically impossible, must reject.
    bad_acquired_at = INIT_TIME - timedelta(minutes=1)
    decision_at = INIT_TIME + timedelta(hours=2)
    terminal_at = decision_at + timedelta(seconds=1)
    clock = _advancing_clock(pipeline_started_at, bad_acquired_at, decision_at, terminal_at)

    store = open_isolated_attempt_store(tmp_path)
    outcomes = _run_evaluation(
        target_local_date=date(2026, 9, 15),
        weathernext_init_time=INIT_TIME,
        weathernext_source_object=SOURCE_OBJECT,
        weathernext_requested_latitude=Decimal("41.80"),
        weathernext_requested_longitude=Decimal("-87.75"),
        clock=clock,
        store=store,
        policy=DEFAULT_POLICY,
        get_event=fake_get_event,
        get_series=fake_get_series,
        get_market=fake_get_market,
        get_orderbook=fake_get_orderbook,
        weathernext_reader=fake_weathernext_reader,
    )
    assert len(outcomes) == 1
    assert outcomes[0].decision.state is AlertState.DATA_NOT_READY

    run_id = _run_invocation_id(
        event_ticker="KXHIGHCHI-26SEP15",
        target_local_date=date(2026, 9, 15),
        weathernext_init_time=INIT_TIME,
        weathernext_source_object=SOURCE_OBJECT,
        weathernext_requested_latitude=Decimal("41.80"),
        weathernext_requested_longitude=Decimal("-87.75"),
        policy_version=DEFAULT_POLICY.version,
    )
    terminal = store.get_run_terminal(run_id)
    assert terminal.weathernext_state == "ACQUISITION_FAILED"
    assert "acquisition" in (terminal.weathernext_reason or "").lower()
    assert terminal.weathernext_acquired_at is None


def test_chronology_violation_fails_closed_and_is_recorded_as_a_failed_run(tmp_path) -> None:
    """Item E5: if a market's ``decision_at`` would precede the Kalshi evidence it is based
    on (here, an orderbook response timestamped in the future relative to the injected
    clock -- an internal-consistency violation that must never be silently accepted), the
    run fails closed. The terminal record still exists (Item F: no invocation ever
    disappears without a trace) and is marked FAILED with the linked attempt IDs
    accumulated before the failure."""
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
    # The Kalshi orderbook response claims to have been observed AFTER the decision_at the
    # clock will produce -- chronologically impossible.
    future_observed_at = datetime(2026, 9, 14, 16, 0, 0, tzinfo=UTC)
    fake_get_market, fake_get_event, fake_get_series, fake_get_orderbook = (
        _successful_snapshot_fixtures(markets[0], event_payload, future_observed_at)
    )

    def fake_weathernext_reader(
        source_object, init_time, latitude, longitude, window_start, window_end
    ):
        rows = [
            {
                "sample": s,
                "lead_time_hours": 0,
                "lead_subtime_hours": 0,
                "valid_time": init_time,
                "value_kelvin": Decimal("305.0") if s < 42 else Decimal("299.0"),
            }
            for s in range(MEMBER_COUNT)
        ]
        return rows, "d" * 64, Decimal("41.80"), Decimal("-87.75")

    pipeline_started_at = datetime(2026, 9, 14, 14, 59, 0, tzinfo=UTC)
    weathernext_acquired_at = datetime(2026, 9, 14, 14, 59, 30, tzinfo=UTC)
    decision_at = datetime(2026, 9, 14, 15, 0, 0, tzinfo=UTC)  # BEFORE future_observed_at
    terminal_at = datetime(2026, 9, 14, 15, 0, 1, tzinfo=UTC)
    clock = _advancing_clock(pipeline_started_at, weathernext_acquired_at, decision_at, terminal_at)

    store = open_isolated_attempt_store(tmp_path)
    with pytest.raises(WnA1Error, match="chronology"):
        _run_evaluation(
            target_local_date=date(2026, 9, 15),
            weathernext_init_time=INIT_TIME,
            weathernext_source_object=SOURCE_OBJECT,
            weathernext_requested_latitude=Decimal("41.80"),
            weathernext_requested_longitude=Decimal("-87.75"),
            clock=clock,
            store=store,
            policy=DEFAULT_POLICY,
            get_event=fake_get_event,
            get_series=fake_get_series,
            get_market=fake_get_market,
            get_orderbook=fake_get_orderbook,
            weathernext_reader=fake_weathernext_reader,
        )
    assert store.count() == 0  # no attempt was fabricated for the violating market

    run_id = _run_invocation_id(
        event_ticker="KXHIGHCHI-26SEP15",
        target_local_date=date(2026, 9, 15),
        weathernext_init_time=INIT_TIME,
        weathernext_source_object=SOURCE_OBJECT,
        weathernext_requested_latitude=Decimal("41.80"),
        weathernext_requested_longitude=Decimal("-87.75"),
        policy_version=DEFAULT_POLICY.version,
    )
    assert store.run_status(run_id) == "FAILED"
    terminal = store.get_run_terminal(run_id)
    assert terminal.status == "FAILED"
    assert terminal.failure_reason is not None
    assert "chronology" in terminal.failure_reason.lower()
    assert terminal.attempt_ids == ()


def test_stale_market_replay_remains_identical_under_the_new_timestamp_model(tmp_path) -> None:
    """Item E6/preservation: a market whose orderbook snapshot was already >5 minutes old
    by the internally-captured ``decision_at`` must reach DATA NOT READY/MARKET_STALE
    originally, and a fresh-process replay of the persisted row must reproduce exactly the
    same state and gate failure -- proving Item A's stale-market replay guarantee still
    holds now that ``decision_at`` is captured internally rather than passed in."""
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
    stale_observed_at = datetime(2026, 9, 14, 14, 50, 0, tzinfo=UTC)  # will be >5min stale
    fake_get_market, fake_get_event, fake_get_series, fake_get_orderbook = (
        _successful_snapshot_fixtures(markets[0], event_payload, stale_observed_at)
    )

    def fake_weathernext_reader(
        source_object, init_time, latitude, longitude, window_start, window_end
    ):
        rows = [
            {
                "sample": s,
                "lead_time_hours": 0,
                "lead_subtime_hours": 0,
                "valid_time": init_time,
                "value_kelvin": Decimal("305.0") if s < 42 else Decimal("299.0"),
            }
            for s in range(MEMBER_COUNT)
        ]
        return rows, "d" * 64, Decimal("41.80"), Decimal("-87.75")

    pipeline_started_at = datetime(2026, 9, 14, 14, 59, 0, tzinfo=UTC)
    weathernext_acquired_at = datetime(2026, 9, 14, 14, 59, 30, tzinfo=UTC)
    decision_at = datetime(2026, 9, 14, 15, 0, 0, tzinfo=UTC)  # 10 min after stale_observed_at
    terminal_at = datetime(2026, 9, 14, 15, 0, 1, tzinfo=UTC)
    clock = _advancing_clock(pipeline_started_at, weathernext_acquired_at, decision_at, terminal_at)

    store = open_isolated_attempt_store(tmp_path)
    outcomes = _run_evaluation(
        target_local_date=date(2026, 9, 15),
        weathernext_init_time=INIT_TIME,
        weathernext_source_object=SOURCE_OBJECT,
        weathernext_requested_latitude=Decimal("41.80"),
        weathernext_requested_longitude=Decimal("-87.75"),
        clock=clock,
        store=store,
        policy=DEFAULT_POLICY,
        get_event=fake_get_event,
        get_series=fake_get_series,
        get_market=fake_get_market,
        get_orderbook=fake_get_orderbook,
        weathernext_reader=fake_weathernext_reader,
    )
    assert len(outcomes) == 1
    original = outcomes[0].decision
    assert original.state is AlertState.DATA_NOT_READY
    from services.forecasting.wn_a1_alert import GateFailure

    assert GateFailure.MARKET_STALE in original.gate_failures

    # Fresh-process replay: brand-new store instance, same on-disk file.
    fresh_store = open_isolated_attempt_store(tmp_path)
    persisted = fresh_store.get(outcomes[0].record.record_id)
    replayed = replay_decision(persisted)
    assert replayed.state == original.state
    assert replayed.gate_failures == original.gate_failures
