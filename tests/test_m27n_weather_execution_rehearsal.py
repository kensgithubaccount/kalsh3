from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

import scripts.run_m27n_weather_execution_rehearsal as fixtures
from services.risk_engine.authorization import AuthorizationState
from services.risk_engine.domain import RiskDecisionState
from services.supervised_canary import m27n_weather_rehearsal as m27n

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


def _scenario(
    now: datetime = NOW, *, risk_intent_overrides: dict[str, object] | None = None
) -> dict[str, Any]:
    return fixtures.build_scenario(now, risk_intent_overrides=risk_intent_overrides)


def _build(now: datetime, scenario: dict[str, Any], **overrides: Any) -> m27n.RehearsalResult:
    kwargs: dict[str, Any] = dict(
        now=now,
        candidate_inputs=scenario["inputs"],
        m13=scenario.get("m13"),
        account_snapshot=scenario.get("account_snapshot"),
        candidate_exposure=scenario.get("candidate_exposure"),
        rules_identity=scenario.get("rules_identity"),
        submission_budget=scenario.get("submission_budget"),
        maximum_accepted_fee=scenario.get("maximum_accepted_fee", Decimal("0")),
    )
    kwargs.update(overrides)
    return m27n.build_rehearsal(**kwargs)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_reaches_rehearsal_ready_with_exact_request_body() -> None:
    scenario = _scenario()
    result = _build(NOW, scenario)
    artifact = result.artifact
    assert artifact.state == "REHEARSAL_READY", artifact.gates.missing
    assert artifact.quantity == "1.00"
    assert artifact.request_method == "POST"
    assert artifact.request_path == "/trade-api/v2/portfolio/events/orders"
    assert artifact.request_origin == "https://external-api.kalshi.com"
    assert artifact.request_body is not None
    assert artifact.request_body["ticker"] == artifact.ticker
    assert artifact.request_body["count"] == "1.00"
    assert artifact.request_body["subaccount"] == 0
    assert artifact.request_body["exchange_index"] == 0
    assert artifact.request_body["side"] in {"bid", "ask"}
    assert artifact.request_body["time_in_force"] == "good_till_canceled"
    assert artifact.request_body_hash is not None
    assert artifact.request_envelope_content_hash is not None
    # Explicitly excluded non-secret-but-forbidden material.
    assert "signature" not in artifact.request_body
    assert "KALSHI-ACCESS-KEY" not in json.dumps(artifact.to_json())


def test_no_qualifying_candidate_abstains() -> None:
    result = m27n.build_rehearsal(
        now=NOW,
        candidate_inputs=(),
        maximum_accepted_fee=Decimal("1"),
    )
    assert result.artifact.state == "ABSTAIN"
    assert result.artifact.abstain_reason == m27n.AbstentionReason.NO_QUALIFYING_CANDIDATE
    assert result.artifact.request_body is None


def test_multiple_qualifying_candidates_abstains() -> None:
    scenario = _scenario()
    probability, forecast, economics = scenario["inputs"][0]
    second_forecast = replace(forecast, local_target_date=forecast.local_target_date)
    second_economics = replace(
        economics,
        evidence_id=economics.evidence_id + "-second",
        market_ticker=economics.market_ticker + "-B",
        event_ticker=economics.event_ticker,
    )
    second_probability = replace(
        probability,
        market_ticker=second_economics.market_ticker,
        result_identity=probability.result_identity + "-second",
    )
    inputs = (
        (probability, forecast, economics),
        (second_probability, second_forecast, second_economics),
    )
    result = m27n.build_rehearsal(
        now=NOW, candidate_inputs=inputs, maximum_accepted_fee=Decimal("1")
    )
    assert result.artifact.state == "ABSTAIN"
    assert result.artifact.abstain_reason == m27n.AbstentionReason.MULTIPLE_QUALIFYING_CANDIDATES


# ---------------------------------------------------------------------------
# Adversarial invariants
# ---------------------------------------------------------------------------


def test_wrong_model_identity_prevents_candidate_selection() -> None:
    now = NOW
    forecast = fixtures._forecast(now)
    probability = replace(fixtures._probability(forecast), model_identity="not-a-frozen-model")
    economics = fixtures._economics(now)
    result = m27n.build_rehearsal(now=now, candidate_inputs=((probability, forecast, economics),))
    assert result.artifact.state == "ABSTAIN"


def test_changed_candidate_identity_blocks_m13_binding() -> None:
    scenario = _scenario()
    tampered_intent = replace(scenario["m13"].risk_intent, candidate_id="not-the-candidate")
    tampered_m13 = replace(scenario["m13"], risk_intent=tampered_intent)
    result = _build(NOW, scenario, m13=tampered_m13)
    assert result.artifact.state == "BLOCKED"
    assert "m13_authorization_bound" in result.artifact.missing_gates


def test_changed_forecast_evidence_identity_blocks_m13_binding() -> None:
    scenario = _scenario()
    tampered_intent = replace(scenario["m13"].risk_intent, forecast_id="not-the-forecast")
    tampered_m13 = replace(scenario["m13"], risk_intent=tampered_intent)
    result = _build(NOW, scenario, m13=tampered_m13)
    assert result.artifact.state == "BLOCKED"
    assert "m13_authorization_bound" in result.artifact.missing_gates


def test_stale_forecast_evidence_abstains() -> None:
    now = NOW
    forecast = replace(fixtures._forecast(now), forecast_reference_time=now - timedelta(hours=2))
    probability = fixtures._probability(forecast)
    economics = fixtures._economics(now)
    result = m27n.build_rehearsal(now=now, candidate_inputs=((probability, forecast, economics),))
    assert result.artifact.state == "ABSTAIN"


def test_stale_orderbook_abstains_before_any_gate_runs() -> None:
    now = NOW
    forecast = fixtures._forecast(now)
    probability = fixtures._probability(forecast)
    economics = replace(fixtures._economics(now), orderbook_observed_at=now - timedelta(minutes=5))
    # M27D itself enforces book freshness at selection time (services.supervised_canary.m27d
    # .MAX_BOOK_AGE), so a stale book never becomes a candidate in the first place.
    result = m27n.build_rehearsal(now=now, candidate_inputs=((probability, forecast, economics),))
    assert result.artifact.state == "ABSTAIN"


def test_tampered_book_price_blocks_price_book_current() -> None:
    """A price mutated on the ``TakerCost`` after construction fails independent replay."""
    scenario = _scenario()
    probability, forecast, economics = scenario["inputs"][0]
    tampered_yes = replace(
        economics.yes, depth=replace(economics.yes.depth, worst_price=Decimal("0.999"))
    )
    tampered_economics = replace(economics, yes=tampered_yes)
    inputs = ((probability, forecast, tampered_economics),)
    result = m27n.build_rehearsal(now=NOW, candidate_inputs=inputs)
    assert result.artifact.state == "BLOCKED"
    assert "price_book_current" in result.artifact.missing_gates


def test_changed_rules_blocks_rules_identity_current() -> None:
    scenario = _scenario()
    tampered_rules = replace(scenario["rules_identity"], current_rules_hash="different-hash")
    result = _build(NOW, scenario, rules_identity=tampered_rules)
    assert result.artifact.state == "BLOCKED"
    assert "rules_identity_current" in result.artifact.missing_gates


def test_wrong_ticker_rules_identity_blocks() -> None:
    scenario = _scenario()
    tampered_rules = replace(scenario["rules_identity"], market_ticker="WRONG-TICKER")
    result = _build(NOW, scenario, rules_identity=tampered_rules)
    assert result.artifact.state == "BLOCKED"
    assert "rules_identity_current" in result.artifact.missing_gates


def test_changed_executable_price_blocks_m13_binding() -> None:
    scenario = _scenario()
    tampered_intent = replace(
        scenario["m13"].risk_intent, price=scenario["candidate"].executable_price + Decimal("0.01")
    )
    tampered_m13 = replace(scenario["m13"], risk_intent=tampered_intent)
    result = _build(NOW, scenario, m13=tampered_m13)
    assert result.artifact.state == "BLOCKED"
    assert "m13_authorization_bound" in result.artifact.missing_gates


def test_quantity_not_one_blocks_m13_binding() -> None:
    scenario = _scenario()
    tampered_intent = replace(scenario["m13"].risk_intent, quantity=Decimal("2.00"))
    tampered_m13 = replace(scenario["m13"], risk_intent=tampered_intent)
    result = _build(NOW, scenario, m13=tampered_m13)
    assert result.artifact.state == "BLOCKED"
    assert "m13_authorization_bound" in result.artifact.missing_gates


def test_insufficient_book_depth_abstains() -> None:
    now = NOW
    forecast = fixtures._forecast(now)
    probability = fixtures._probability(forecast)
    economics = fixtures._economics(now)
    # A quantity larger than the displayed depth (5 contracts) cannot be filled at all --
    # exercised indirectly by requesting the full-size candidate normally, then independently
    # confirming a below-floor book abstains overall via the existing m27d contract.
    thin_book = replace(
        economics,
        requested_quantity=Decimal("1.00"),
        yes=None,
        no=None,
    )
    result = m27n.build_rehearsal(now=now, candidate_inputs=((probability, forecast, thin_book),))
    assert result.artifact.state == "ABSTAIN"


def test_fee_beyond_accepted_bound_blocks() -> None:
    scenario = _scenario()
    too_tight = scenario["candidate"].maximum_fee - Decimal("0.001")
    result = _build(NOW, scenario, maximum_accepted_fee=too_tight)
    assert result.artifact.state == "BLOCKED"
    assert "fee_within_bound" in result.artifact.missing_gates


def test_existing_same_side_position_blocks() -> None:
    scenario = _scenario()
    tampered = replace(scenario["candidate_exposure"], position_nonzero=True)
    result = _build(NOW, scenario, candidate_exposure=tampered)
    assert result.artifact.state == "BLOCKED"
    assert "no_disqualifying_position" in result.artifact.missing_gates


def test_existing_opposite_side_position_blocks() -> None:
    # The underlying exposure evidence (matching services.supervised_canary
    # .candidate_exposure_check.CandidateExposureEvidence) is intentionally side-agnostic --
    # any nonzero position in this market blocks, regardless of side.
    scenario = _scenario()
    tampered = replace(scenario["candidate_exposure"], position_nonzero=True)
    result = _build(NOW, scenario, candidate_exposure=tampered)
    assert result.artifact.state == "BLOCKED"
    assert "no_disqualifying_position" in result.artifact.missing_gates


def test_unresolved_open_order_blocks() -> None:
    scenario = _scenario()
    tampered = replace(scenario["candidate_exposure"], open_order_count=1)
    result = _build(NOW, scenario, candidate_exposure=tampered)
    assert result.artifact.state == "BLOCKED"
    assert "no_unresolved_order" in result.artifact.missing_gates


def test_stale_account_snapshot_blocks() -> None:
    scenario = _scenario()
    tampered = replace(scenario["account_snapshot"], observed_at=NOW - timedelta(minutes=5))
    result = _build(NOW, scenario, account_snapshot=tampered)
    assert result.artifact.state == "BLOCKED"
    assert "account_snapshot_current" in result.artifact.missing_gates


def test_stale_m13_authorization_blocks() -> None:
    scenario = _scenario()
    tampered = replace(scenario["m13"].authorization, expires_at=NOW - timedelta(seconds=1))
    tampered_m13 = replace(scenario["m13"], authorization=tampered)
    result = _build(NOW, scenario, m13=tampered_m13)
    assert result.artifact.state == "BLOCKED"
    assert "m13_authorization_fresh" in result.artifact.missing_gates


def test_changed_m13_authorization_identity_blocks() -> None:
    scenario = _scenario()
    tampered = replace(scenario["m13"].authorization, authorization_id="tampered-id")
    tampered_m13 = replace(scenario["m13"], authorization=tampered)
    result = _build(NOW, scenario, m13=tampered_m13)
    assert result.artifact.state == "BLOCKED"
    assert "m13_authorization_fresh" in result.artifact.missing_gates


def test_consumed_m13_authorization_blocks() -> None:
    scenario = _scenario()
    tampered = replace(scenario["m13"].authorization, state=AuthorizationState.CONSUMED)
    tampered_m13 = replace(scenario["m13"], authorization=tampered)
    result = _build(NOW, scenario, m13=tampered_m13)
    assert result.artifact.state == "BLOCKED"
    assert "m13_authorization_fresh" in result.artifact.missing_gates


def test_risk_decision_not_pass_blocks() -> None:
    scenario = _scenario()
    tampered_decision = replace(scenario["m13"].risk_decision, state=RiskDecisionState.REJECT)
    tampered_m13 = replace(scenario["m13"], risk_decision=tampered_decision)
    result = _build(NOW, scenario, m13=tampered_m13)
    assert result.artifact.state == "BLOCKED"
    assert "m13_authorization_fresh" in result.artifact.missing_gates


def test_unclear_safety_state_blocks() -> None:
    scenario = _scenario()
    tampered_m13 = replace(scenario["m13"], global_halt_clear=False)
    result = _build(NOW, scenario, m13=tampered_m13)
    assert result.artifact.state == "BLOCKED"
    assert "m13_authorization_fresh" in result.artifact.missing_gates


def test_submission_budget_used_blocks() -> None:
    scenario = _scenario()
    tampered = replace(scenario["submission_budget"], write_budget_used=True)
    result = _build(NOW, scenario, submission_budget=tampered)
    assert result.artifact.state == "BLOCKED"
    assert "submission_budget_available" in result.artifact.missing_gates


def test_unresolved_canary_present_blocks() -> None:
    scenario = _scenario()
    tampered = replace(scenario["submission_budget"], unresolved_canary_present=True)
    result = _build(NOW, scenario, submission_budget=tampered)
    assert result.artifact.state == "BLOCKED"
    assert "submission_budget_available" in result.artifact.missing_gates


def test_malformed_time_in_force_blocks_with_reason() -> None:
    scenario = _scenario(
        risk_intent_overrides={"time_in_force_policy": "BOGUS"},
    )
    result = _build(NOW, scenario)
    assert result.artifact.state == "BLOCKED"
    assert result.artifact.request_body is None
    assert "envelope construction rejected" in (result.artifact.warning or "")


def test_no_fixtures_supplied_blocks_every_dependent_gate() -> None:
    scenario = _scenario()
    result = m27n.build_rehearsal(now=NOW, candidate_inputs=scenario["inputs"])
    assert result.artifact.state == "BLOCKED"
    for gate in (
        "rules_identity_current",
        "account_snapshot_current",
        "no_disqualifying_position",
        "no_unresolved_order",
        "m13_authorization_fresh",
        "m13_authorization_bound",
        "submission_budget_available",
    ):
        assert gate in result.artifact.missing_gates


# ---------------------------------------------------------------------------
# Determinism / identity
# ---------------------------------------------------------------------------


def test_exact_replay_is_deterministic() -> None:
    scenario = _scenario()
    first = _build(NOW, scenario)
    second = _build(NOW, scenario)
    assert first.artifact.to_json() == second.artifact.to_json()
    assert first.artifact.content_hash == second.artifact.content_hash
    assert first.artifact.rehearsal_id == second.artifact.rehearsal_id


def test_one_field_mutation_changes_rehearsal_identity() -> None:
    scenario = _scenario()
    baseline = _build(NOW, scenario)
    tampered_rules = replace(scenario["rules_identity"], current_rules_hash="mutated-only-field")
    mutated = _build(NOW, scenario, rules_identity=tampered_rules)
    assert mutated.artifact.content_hash != baseline.artifact.content_hash
    assert mutated.artifact.rehearsal_id != baseline.artifact.rehearsal_id


def test_input_ordering_cannot_change_result() -> None:
    scenario = _scenario()
    probability, forecast, economics = scenario["inputs"][0]
    other_probability = replace(
        probability,
        model_identity="not-a-frozen-model",
        result_identity=probability.result_identity + "-other",
    )
    inputs_a = ((probability, forecast, economics), (other_probability, forecast, economics))
    inputs_b = ((other_probability, forecast, economics), (probability, forecast, economics))
    result_a = _build(NOW, scenario, candidate_inputs=inputs_a)
    result_b = _build(NOW, scenario, candidate_inputs=inputs_b)
    assert result_a.artifact.state == result_b.artifact.state == "REHEARSAL_READY"
    assert result_a.artifact.content_hash == result_b.artifact.content_hash


# ---------------------------------------------------------------------------
# Artifact validation
# ---------------------------------------------------------------------------


def test_validate_rehearsal_artifact_round_trips() -> None:
    scenario = _scenario()
    result = _build(NOW, scenario)
    payload = result.artifact.to_json()
    validation = m27n.validate_rehearsal_artifact(
        payload, expected_candidate_id=result.artifact.candidate_id, now=NOW
    )
    assert validation.valid, validation.reason


def test_validate_rehearsal_artifact_rejects_tampered_hash() -> None:
    scenario = _scenario()
    result = _build(NOW, scenario)
    payload = dict(result.artifact.to_json())
    payload["limit_price"] = "0.001"
    validation = m27n.validate_rehearsal_artifact(payload, expected_candidate_id=None, now=NOW)
    assert not validation.valid


def test_validate_rehearsal_artifact_rejects_stale_payload() -> None:
    scenario = _scenario()
    result = _build(NOW, scenario)
    payload = result.artifact.to_json()
    validation = m27n.validate_rehearsal_artifact(
        payload, expected_candidate_id=None, now=NOW + timedelta(hours=1)
    )
    assert not validation.valid


@pytest.mark.parametrize("bad_payload", [None, "not a dict", 5, []])
def test_validate_rehearsal_artifact_rejects_non_object_payloads(bad_payload: object) -> None:
    validation = m27n.validate_rehearsal_artifact(bad_payload, expected_candidate_id=None, now=NOW)
    assert not validation.valid
