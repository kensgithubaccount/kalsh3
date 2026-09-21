from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from services.forecasting.wn_a1_alert import GateFailure
from services.forecasting.wn_a1_attempt_store import (
    AttemptStoreError,
    WnA1Attempt,
    WnA1RunStart,
    WnA1RunTerminal,
    open_isolated_attempt_store,
    replay_decision,
    replay_weathernext_evidence,
    weathernext_member_rows,
)
from services.forecasting.wn_a1_current_daily_high_authority import WindowStatus
from services.forecasting.wn_a1_domain import AlertSide, AlertState
from services.forecasting.wn_a1_weathernext_evidence import (
    MEMBER_COUNT,
    MODEL,
    UNIT,
    VARIABLE,
    build_ensemble_evidence,
)

INIT_TIME = datetime(2026, 9, 15, 5, 0, tzinfo=UTC)  # 2026-09-15 00:00 America/Chicago
SOURCE_OBJECT = "gs://weathernext3_spatial/weathernext_3_0_0/zarr/2026_to_present/20260915_05hr_XX_preds/predictions.zarr/"


def _evidence(kelvin_by_sample: dict[int, Decimal]):
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
        source_content_hash="f" * 64,
    )
    assert result.evidence is not None
    return result.evidence


_UNSET_OBSERVED_AT = object()


def _attempt(
    *,
    evaluated_at: datetime,
    evidence,
    decision_state: str,
    side: str | None,
    window_status: str = WindowStatus.NOT_ESTABLISHED.value,
    kalshi_orderbook_observed_at: datetime | object | None = _UNSET_OBSERVED_AT,
    decision_gate_failures: tuple[str, ...] = (),
) -> WnA1Attempt:
    from services.forecasting.wn_a1_alert import DEFAULT_POLICY
    from services.market_universe.domain import stable_hash

    # Default to a fresh snapshot (observed exactly at evaluation time) so every existing
    # caller that doesn't care about Item A's freshness mechanics keeps getting a
    # market_fresh=True replay, matching this module's pre-existing fixtures. Callers that
    # want to test staleness or a missing snapshot pass an explicit value (including None).
    if kalshi_orderbook_observed_at is _UNSET_OBSERVED_AT:
        kalshi_orderbook_observed_at = evaluated_at
    assert kalshi_orderbook_observed_at is None or isinstance(
        kalshi_orderbook_observed_at, datetime
    )
    observed_at_iso = (
        "" if kalshi_orderbook_observed_at is None else kalshi_orderbook_observed_at.isoformat()
    )
    attempt_id = stable_hash(
        (
            "test-attempt",
            evidence.evidence_identity,
            decision_state,
            evaluated_at.isoformat(),
            observed_at_iso,
        )
    )
    return WnA1Attempt(
        attempt_id=attempt_id,
        run_id="test-run-" + attempt_id[:16],
        target_local_date="2026-09-15",
        market_ticker="KXHIGHCHI-26SEP15-T87",
        event_ticker="KXHIGHCHI-26SEP15",
        series_ticker="KXHIGHCHI",
        contract_policy_identity="contract-policy-hash",
        contract_policy_version="wn-a1-current-live-daily-high-twc-authority-v1",
        contract_comparator="GT",
        contract_lower=Decimal("87"),
        contract_upper=None,
        research_window_start=INIT_TIME,
        research_window_end=datetime(2026, 9, 16, 5, 0, tzinfo=UTC),
        window_status=window_status,
        weathernext_evidence_identity=evidence.evidence_identity,
        weathernext_model=evidence.model,
        weathernext_source_object=evidence.source_object,
        weathernext_init_time=evidence.init_time,
        weathernext_acquired_at=evidence.acquired_at,
        weathernext_requested_latitude=evidence.requested_latitude,
        weathernext_requested_longitude=evidence.requested_longitude,
        weathernext_selected_latitude=evidence.selected_latitude,
        weathernext_selected_longitude=evidence.selected_longitude,
        weathernext_source_content_hash=evidence.source_content_hash,
        weathernext_members=weathernext_member_rows(evidence),
        kalshi_snapshot_identity="kalshi-snapshot-hash",
        kalshi_orderbook_observed_at=kalshi_orderbook_observed_at,
        alert_policy_version=DEFAULT_POLICY.version,
        decision_state=decision_state,
        decision_gate_failures=decision_gate_failures,
        side=side,
        model_probability_yes=Decimal("0.65625"),
        yes_conservative_debit=Decimal("0.27"),
        no_conservative_debit=Decimal("0.90"),
        yes_gap_pp=Decimal("38.625"),
        yes_buffered_gap_pp=Decimal("33.625"),
        no_gap_pp=Decimal("-55.625"),
        no_buffered_gap_pp=Decimal("-60.625"),
        yes_fee_quality="DETERMINISTIC_FORMULA_ONLY",
        no_fee_quality="DETERMINISTIC_FORMULA_ONLY",
        boundary_members_within_one_f=0,
        boundary_baseline_ticker=None,
        boundary_plus_one_ticker=None,
        boundary_minus_one_ticker=None,
        evaluated_at=evaluated_at,
    )


def _hot_evidence():
    kelvin = {i: Decimal("305.0") if i < 42 else Decimal("299.0") for i in range(MEMBER_COUNT)}
    return _evidence(kelvin)


def test_append_and_get_round_trip(tmp_path) -> None:
    store = open_isolated_attempt_store(tmp_path)
    evidence = _hot_evidence()
    attempt = _attempt(
        evaluated_at=datetime(2026, 9, 15, 15, 0, tzinfo=UTC),
        evidence=evidence,
        decision_state="TOO UNCERTAIN",
        side="YES",
    )
    store.append(attempt)
    reopened = store.get(attempt.attempt_id)
    assert reopened.market_ticker == attempt.market_ticker
    assert reopened.weathernext_evidence_identity == evidence.evidence_identity
    assert len(reopened.weathernext_members) == MEMBER_COUNT
    assert reopened.research_only is True
    assert reopened.production_influence == Decimal(0)


def test_fresh_process_reopen_from_a_new_store_instance(tmp_path) -> None:
    """A genuinely fresh process opens a brand-new store object pointed at the same file."""
    evidence = _hot_evidence()
    attempt = _attempt(
        evaluated_at=datetime(2026, 9, 15, 15, 0, tzinfo=UTC),
        evidence=evidence,
        decision_state="TOO UNCERTAIN",
        side="YES",
    )
    open_isolated_attempt_store(tmp_path).append(attempt)

    fresh_store = open_isolated_attempt_store(tmp_path)  # brand-new instance, same file
    reopened = fresh_store.get(attempt.attempt_id)
    assert reopened.weathernext_evidence_identity == evidence.evidence_identity
    assert reopened.weathernext_members == attempt.weathernext_members


def test_duplicate_attempt_persistence_fails_closed(tmp_path) -> None:
    store = open_isolated_attempt_store(tmp_path)
    evidence = _hot_evidence()
    attempt = _attempt(
        evaluated_at=datetime(2026, 9, 15, 15, 0, tzinfo=UTC),
        evidence=evidence,
        decision_state="SKIP",
        side=None,
    )
    store.append(attempt)
    with pytest.raises(AttemptStoreError, match="duplicate"):
        store.append(attempt)
    assert store.count() == 1


def test_duplicate_attempt_fails_closed_even_across_fresh_store_instances(tmp_path) -> None:
    evidence = _hot_evidence()
    attempt = _attempt(
        evaluated_at=datetime(2026, 9, 15, 15, 0, tzinfo=UTC),
        evidence=evidence,
        decision_state="SKIP",
        side=None,
    )
    open_isolated_attempt_store(tmp_path).append(attempt)
    with pytest.raises(AttemptStoreError, match="duplicate"):
        open_isolated_attempt_store(tmp_path).append(attempt)


def test_append_only_rejects_update_and_delete(tmp_path) -> None:
    store = open_isolated_attempt_store(tmp_path)
    evidence = _hot_evidence()
    attempt = _attempt(
        evaluated_at=datetime(2026, 9, 15, 15, 0, tzinfo=UTC),
        evidence=evidence,
        decision_state="SKIP",
        side=None,
    )
    store.append(attempt)
    with sqlite3.connect(store.path) as db:
        with pytest.raises(sqlite3.DatabaseError, match="append only"):
            db.execute(
                "UPDATE wn_a1_attempts SET decision_state='TAKE A LOOK' WHERE attempt_id=?",
                (attempt.attempt_id,),
            )
        with pytest.raises(sqlite3.DatabaseError, match="append only"):
            db.execute("DELETE FROM wn_a1_attempts WHERE attempt_id=?", (attempt.attempt_id,))
    assert store.get(attempt.attempt_id).decision_state == "SKIP"


def test_persists_grid_coordinates_and_both_side_economics(tmp_path) -> None:
    store = open_isolated_attempt_store(tmp_path)
    evidence = _hot_evidence()
    attempt = _attempt(
        evaluated_at=datetime(2026, 9, 15, 15, 0, tzinfo=UTC),
        evidence=evidence,
        decision_state="TAKE A LOOK",
        side="YES",
    )
    store.append(attempt)
    reopened = store.get(attempt.attempt_id)
    assert reopened.weathernext_requested_latitude == Decimal("41.80")
    assert reopened.weathernext_requested_longitude == Decimal("-87.75")
    assert reopened.weathernext_selected_latitude == Decimal("41.80")
    assert reopened.weathernext_selected_longitude == Decimal("-87.75")
    assert reopened.yes_conservative_debit == Decimal("0.27")
    assert reopened.no_conservative_debit == Decimal("0.90")
    assert reopened.yes_fee_quality == "DETERMINISTIC_FORMULA_ONLY"
    assert reopened.side == "YES"
    assert reopened.research_only is True
    assert reopened.production_influence == Decimal(0)


def test_replay_weathernext_evidence_reproduces_the_bound_identity(tmp_path) -> None:
    evidence = _hot_evidence()
    attempt = _attempt(
        evaluated_at=datetime(2026, 9, 15, 15, 0, tzinfo=UTC),
        evidence=evidence,
        decision_state="TAKE A LOOK",
        side="YES",
    )
    open_isolated_attempt_store(tmp_path).append(attempt)
    reopened = open_isolated_attempt_store(tmp_path).get(attempt.attempt_id)
    replayed_evidence = replay_weathernext_evidence(reopened)
    assert replayed_evidence.evidence_identity == evidence.evidence_identity
    assert len(replayed_evidence.members) == len(evidence.members)


def test_replay_decision_reproduces_take_a_look_from_persisted_data(tmp_path) -> None:
    """Fresh-process replay: reopen persisted data and RECOMPUTE the decision (not merely
    recompute a hash from the in-memory dataclass). Uses an established window to prove the
    replay mechanism itself -- today's real live authority never persists that window
    status (item 3); see test_end_to_end_too_uncertain_because_live_window_is_unestablished
    in test_wn_a1_runner.py for what a real attempt looks like today."""
    evidence = _hot_evidence()  # 42/64 hot -> raw probability 0.65625 for GT 87
    attempt = _attempt(
        evaluated_at=datetime(2026, 9, 15, 15, 0, tzinfo=UTC),
        evidence=evidence,
        decision_state="TAKE A LOOK",
        side="YES",
        window_status=WindowStatus.ESTABLISHED_CIVIL_LOCAL_DAY.value,
    )
    store = open_isolated_attempt_store(tmp_path)
    store.append(attempt)

    fresh_store = open_isolated_attempt_store(tmp_path)
    reopened = fresh_store.get(attempt.attempt_id)
    replayed = replay_decision(reopened)
    assert replayed.state is AlertState.TAKE_A_LOOK
    assert replayed.side is AlertSide.YES
    assert replayed.model_probability_yes == Decimal(42) / Decimal(64)


def test_replay_decision_reproduces_data_not_ready_without_evidence(tmp_path) -> None:
    evidence = _hot_evidence()
    attempt = _attempt(
        evaluated_at=datetime(2026, 9, 15, 15, 0, tzinfo=UTC),
        evidence=evidence,
        decision_state="DATA NOT READY",
        side=None,
    )
    from dataclasses import replace

    no_evidence_attempt = replace(
        attempt,
        weathernext_evidence_identity=None,
        weathernext_model=None,
        weathernext_source_object=None,
        weathernext_init_time=None,
        weathernext_acquired_at=None,
        weathernext_requested_latitude=None,
        weathernext_requested_longitude=None,
        weathernext_selected_latitude=None,
        weathernext_selected_longitude=None,
        weathernext_source_content_hash=None,
        weathernext_members=(),
        yes_conservative_debit=None,
        no_conservative_debit=None,
    )
    store = open_isolated_attempt_store(tmp_path)
    store.append(no_evidence_attempt)
    reopened = store.get(no_evidence_attempt.attempt_id)
    replayed = replay_decision(reopened)
    assert replayed.state is AlertState.DATA_NOT_READY


def test_replay_decision_reproduces_market_stale_state(tmp_path) -> None:
    """Item A's exact required counterexample: the original orderbook snapshot was more
    than 5 minutes old, so the original decision was DATA NOT READY / MARKET_STALE. A
    fresh-process replay must recompute freshness from the persisted
    ``kalshi_orderbook_observed_at``/``evaluated_at`` pair and reproduce exactly the same
    state and gate failure -- never hardcode ``market_fresh=True``."""
    evidence = _hot_evidence()
    evaluated_at = datetime(2026, 9, 15, 15, 0, tzinfo=UTC)
    stale_observed_at = evaluated_at - timedelta(minutes=6)
    attempt = _attempt(
        evaluated_at=evaluated_at,
        evidence=evidence,
        decision_state="DATA NOT READY",
        side=None,
        window_status=WindowStatus.ESTABLISHED_CIVIL_LOCAL_DAY.value,
        kalshi_orderbook_observed_at=stale_observed_at,
        decision_gate_failures=(GateFailure.MARKET_STALE.value,),
    )
    store = open_isolated_attempt_store(tmp_path)
    store.append(attempt)

    fresh_store = open_isolated_attempt_store(tmp_path)
    reopened = fresh_store.get(attempt.attempt_id)
    assert reopened.kalshi_orderbook_observed_at == stale_observed_at
    replayed = replay_decision(reopened)
    assert replayed.state is AlertState.DATA_NOT_READY
    assert GateFailure.MARKET_STALE in replayed.gate_failures


def test_replay_decision_preserves_missing_market_snapshot_state(tmp_path) -> None:
    """If the original run never acquired a market snapshot at all (as opposed to
    acquiring a stale one), that unavailable state must be preserved on replay too --
    never silently treated as fresh."""
    evidence = _hot_evidence()
    evaluated_at = datetime(2026, 9, 15, 15, 0, tzinfo=UTC)
    attempt = _attempt(
        evaluated_at=evaluated_at,
        evidence=evidence,
        decision_state="DATA NOT READY",
        side=None,
        window_status=WindowStatus.ESTABLISHED_CIVIL_LOCAL_DAY.value,
        kalshi_orderbook_observed_at=None,
        decision_gate_failures=(
            GateFailure.MARKET_STALE.value,
            GateFailure.NO_EXECUTABLE_MARKET_PRICE.value,
        ),
    )
    attempt = replace(attempt, yes_conservative_debit=None, no_conservative_debit=None)
    store = open_isolated_attempt_store(tmp_path)
    store.append(attempt)

    reopened = open_isolated_attempt_store(tmp_path).get(attempt.attempt_id)
    assert reopened.kalshi_orderbook_observed_at is None
    replayed = replay_decision(reopened)
    assert replayed.state is AlertState.DATA_NOT_READY
    assert GateFailure.MARKET_STALE in replayed.gate_failures


def test_tampered_member_rows_fail_replay_closed(tmp_path) -> None:
    """If the persisted member rows were somehow altered, replay must fail closed rather
    than silently reproducing a different probability than what was actually decided."""
    evidence = _hot_evidence()
    attempt = _attempt(
        evaluated_at=datetime(2026, 9, 15, 15, 0, tzinfo=UTC),
        evidence=evidence,
        decision_state="TAKE A LOOK",
        side="YES",
    )
    tampered_rows = list(attempt.weathernext_members)
    sample, lead_h, lead_m, valid_iso, _kelvin = tampered_rows[0]
    tampered_rows[0] = (sample, lead_h, lead_m, valid_iso, "999.99")
    from dataclasses import replace

    tampered = replace(attempt, weathernext_members=tuple(tampered_rows))
    with pytest.raises(AttemptStoreError, match="evidence identity"):
        replay_weathernext_evidence(tampered)


def test_safety_invariants_enforced_on_construction() -> None:
    evidence = _hot_evidence()
    attempt = _attempt(
        evaluated_at=datetime(2026, 9, 15, 15, 0, tzinfo=UTC),
        evidence=evidence,
        decision_state="SKIP",
        side=None,
    )
    assert attempt.research_only is True
    assert attempt.production_influence == Decimal(0)


def test_attempts_for_date_lists_every_persisted_attempt(tmp_path) -> None:
    store = open_isolated_attempt_store(tmp_path)
    evidence = _hot_evidence()
    for state, side, minute in (("TAKE A LOOK", "YES", 10), ("SKIP", None, 20)):
        store.append(
            _attempt(
                evaluated_at=datetime(2026, 9, 15, 15, minute, tzinfo=UTC),
                evidence=evidence,
                decision_state=state,
                side=side,
            )
        )
    rows = store.attempts_for_date(evidence.init_time.date())
    assert {r.decision_state for r in rows} == {"TAKE A LOOK", "SKIP"}
    assert store.count() == 2


# --- Item F: the two-phase (START -> terminal) durable run journal -----------------------


def _run_start(run_id: str, *, pipeline_started_at: datetime) -> WnA1RunStart:
    return WnA1RunStart(
        run_id=run_id,
        target_local_date="2026-09-15",
        event_ticker="KXHIGHCHI-26SEP15",
        weathernext_source_object=SOURCE_OBJECT,
        weathernext_init_time=INIT_TIME,
        weathernext_requested_latitude=Decimal("41.80"),
        weathernext_requested_longitude=Decimal("-87.75"),
        alert_policy_version="wn-a1-alert-policy-v2-side-aware",
        pipeline_started_at=pipeline_started_at,
    )


def _run_terminal(
    run_id: str, *, status: str, pipeline_started_at: datetime, terminal_at: datetime
) -> WnA1RunTerminal:
    return WnA1RunTerminal(
        run_id=run_id,
        status=status,
        pipeline_started_at=pipeline_started_at,
        terminal_at=terminal_at,
        target_local_date="2026-09-15",
        event_ticker="KXHIGHCHI-26SEP15",
        weathernext_source_object=SOURCE_OBJECT,
        weathernext_init_time=INIT_TIME,
        weathernext_requested_latitude=Decimal("41.80"),
        weathernext_requested_longitude=Decimal("-87.75"),
        discovery_state="OK",
        discovery_reason=None,
        weathernext_state="COMPLETE",
        weathernext_reason=None,
        weathernext_acquired_at=INIT_TIME,
        route_outcomes=(("KXHIGHCHI-26SEP15-T87", "EVALUATED", "DATA NOT READY"),),
        attempt_ids=(),
        alert_policy_version="wn-a1-alert-policy-v2-side-aware",
        failure_reason=None,
    )


def test_run_start_and_terminal_round_trip(tmp_path) -> None:
    store = open_isolated_attempt_store(tmp_path)
    started_at = datetime(2026, 9, 14, 14, 59, tzinfo=UTC)
    start = _run_start("run-1", pipeline_started_at=started_at)
    store.register_run_start(start)
    reopened_start = store.get_run_start("run-1")
    assert reopened_start.pipeline_started_at == started_at
    assert reopened_start.research_only is True
    assert reopened_start.production_influence == Decimal(0)

    terminal = _run_terminal(
        "run-1",
        status="COMPLETED",
        pipeline_started_at=started_at,
        terminal_at=started_at + timedelta(minutes=1),
    )
    store.append_run_terminal(terminal)
    reopened_terminal = store.get_run_terminal("run-1")
    assert reopened_terminal.status == "COMPLETED"
    assert reopened_terminal.route_outcomes == terminal.route_outcomes
    assert reopened_terminal.research_only is True
    assert reopened_terminal.production_influence == Decimal(0)


def test_duplicate_run_start_fails_closed(tmp_path) -> None:
    store = open_isolated_attempt_store(tmp_path)
    started_at = datetime(2026, 9, 14, 14, 59, tzinfo=UTC)
    start = _run_start("run-dup", pipeline_started_at=started_at)
    store.register_run_start(start)
    with pytest.raises(AttemptStoreError, match="duplicate"):
        store.register_run_start(start)


def test_duplicate_run_terminal_fails_closed(tmp_path) -> None:
    store = open_isolated_attempt_store(tmp_path)
    started_at = datetime(2026, 9, 14, 14, 59, tzinfo=UTC)
    store.register_run_start(_run_start("run-dup-terminal", pipeline_started_at=started_at))
    terminal = _run_terminal(
        "run-dup-terminal",
        status="COMPLETED",
        pipeline_started_at=started_at,
        terminal_at=started_at + timedelta(minutes=1),
    )
    store.append_run_terminal(terminal)
    with pytest.raises(AttemptStoreError, match="duplicate"):
        store.append_run_terminal(terminal)


def test_run_status_is_started_before_any_terminal_exists(tmp_path) -> None:
    store = open_isolated_attempt_store(tmp_path)
    started_at = datetime(2026, 9, 14, 14, 59, tzinfo=UTC)
    store.register_run_start(_run_start("run-pending", pipeline_started_at=started_at))
    assert store.run_status("run-pending") == "STARTED"
    with pytest.raises(AttemptStoreError, match="no terminal record"):
        store.get_run_terminal("run-pending")


def test_run_status_raises_for_a_completely_unknown_run(tmp_path) -> None:
    store = open_isolated_attempt_store(tmp_path)
    with pytest.raises(AttemptStoreError, match="unknown"):
        store.run_status("never-registered")


def test_interrupted_run_is_durably_visible_after_fresh_process_reopen(tmp_path) -> None:
    """Item F5's required interruption test: persist START (and one linked market attempt,
    simulating an attempt that completed before the crash), inject a failure BEFORE the
    terminal record is ever written (simulating the process dying), then reopen the SQLite
    file from a brand-new store instance and prove the run is durably visible as
    STARTED/interrupted, with its already-linked attempt row still attributable to it --
    never silently disappeared."""
    started_at = datetime(2026, 9, 14, 14, 59, tzinfo=UTC)
    run_id = "run-interrupted"
    store = open_isolated_attempt_store(tmp_path)
    store.register_run_start(_run_start(run_id, pipeline_started_at=started_at))

    evidence = _hot_evidence()
    linked_attempt = _attempt(
        evaluated_at=datetime(2026, 9, 14, 15, 0, tzinfo=UTC),
        evidence=evidence,
        decision_state="DATA NOT READY",
        side=None,
    )
    linked_attempt = replace(linked_attempt, run_id=run_id)
    store.append(linked_attempt)

    # Simulate the process dying here -- no terminal record is ever appended.

    # A genuinely fresh process reopens a brand-new store instance pointed at the same file.
    fresh_store = open_isolated_attempt_store(tmp_path)
    assert fresh_store.run_status(run_id) == "STARTED"
    with pytest.raises(AttemptStoreError, match="no terminal record"):
        fresh_store.get_run_terminal(run_id)

    # The linked market attempt is still durably visible and attributable to this run.
    linked = fresh_store.attempts_for_run(run_id)
    assert len(linked) == 1
    assert linked[0].attempt_id == linked_attempt.attempt_id
    assert linked[0].run_id == run_id

    # The run's own registration record is intact, unaffected by the interruption.
    start = fresh_store.get_run_start(run_id)
    assert start.pipeline_started_at == started_at
