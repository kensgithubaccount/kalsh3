from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from services.forecasting.weather_probability import physical_temperature_proxy_probability
from services.forecasting.weather_prospective import FROZEN_MODEL_IDENTITIES
from services.opportunity_engine.books import DepthWalk
from services.supervised_canary.m27d import (
    ACKNOWLEDGEMENT,
    AUGUST_END,
    CandidateState,
    ExperimentalApprovalBinding,
    final_experimental_gates,
    select_experimental_candidate,
    validate_experimental_acknowledgement,
)
from tests.test_m27c_weather_probability import chicago_route, current, population


def evidence(
    *, probability: str = "0.70", target: date = date(2026, 8, 31), model: str | None = None
):
    forecast = replace(
        current(),
        local_target_date=target,
        forecast_reference_time=current().forecast_reference_time,
    )
    probability_value = replace(
        physical_temperature_proxy_probability(
            route=chicago_route("greater", 80, None),
            population=population(),
            current=current(),
        ),
        probability=Decimal(probability),
        model_identity=model or FROZEN_MODEL_IDENTITIES[54_000],
        current_forecast_evidence_identity=forecast.evidence_identity,
        exact_midpoint_seconds=54_000,
        market_ticker="M",
        event_ticker="E",
        series_ticker="CLIMDW",
        diagnostic=None,
    )
    depth = DepthWalk(
        Decimal("1.00"),
        Decimal("1.00"),
        Decimal("0.50"),
        Decimal("0.50"),
        Decimal("0"),
        Decimal("0.50"),
        1,
    )
    cost = SimpleNamespace(
        depth=depth, centicent_rounded_fee=Decimal("0"), conservative_total_entry_cost=None
    )
    economics = SimpleNamespace(
        requested_quantity=Decimal("1.00"),
        analysis_type="TAKER_NOW",
        research_only=True,
        orderbook_observed_at=forecast.forecast_reference_time,
        market_ticker="M",
        event_ticker="E",
        series_ticker="CLIMDW",
        yes=cost,
        no=None,
        market_rules_hash="rules",
        evidence_id="economics",
        economics_observed_at=forecast.forecast_reference_time,
    )
    return probability_value, forecast, economics


def qualify(**changes: object):
    probability, forecast, economics = evidence()
    probability = replace(
        probability,
        **{
            k: v for k, v in changes.items() if k in {"probability", "diagnostic", "model_identity"}
        },
    )
    forecast = replace(forecast, **{k: v for k, v in changes.items() if k in {"local_target_date"}})
    economics_values = vars(economics).copy()
    economics_values.update(
        {k: v for k, v in changes.items() if k in {"requested_quantity", "orderbook_observed_at"}}
    )
    economics = SimpleNamespace(**economics_values)
    return select_experimental_candidate(
        ((probability, forecast, economics),),
        now=forecast.forecast_reference_time + timedelta(seconds=10),
    )


def test_exact_threshold_and_august_31_qualify() -> None:
    result = qualify(probability=Decimal("0.70"))
    assert result.state is CandidateState.QUALIFYING_EXPERIMENTAL_CANARY
    assert (
        result.selected is not None
        and result.selected.research_probability_discrepancy == Decimal("0.20")
    )
    assert result.selected.eligibility.target_date == AUGUST_END


@pytest.mark.parametrize("probability", [Decimal("0.6999"), Decimal("0"), Decimal("1")])
def test_threshold_and_boundary_mass_fail_closed(probability: Decimal) -> None:
    assert qualify(probability=probability).state is CandidateState.ABSTAIN


def test_september_and_wrong_model_rejected() -> None:
    assert qualify(local_target_date=date(2026, 9, 1)).state is CandidateState.ABSTAIN
    assert qualify(model_identity="wrong").state is CandidateState.ABSTAIN


def test_diagnostic_boundary_mass_rejected_even_if_probability_is_not_boundary() -> None:
    assert (
        qualify(probability=Decimal("0.70"), diagnostic="EMPIRICAL_BOUNDARY_MASS").state
        is CandidateState.ABSTAIN
    )


def test_quantity_and_stale_book_rejected() -> None:
    assert qualify(requested_quantity=Decimal("1.01")).state is CandidateState.ABSTAIN
    probability, forecast, economics = evidence()
    stale_values = vars(economics).copy()
    stale_values["orderbook_observed_at"] = forecast.forecast_reference_time - timedelta(seconds=31)
    stale = SimpleNamespace(**stale_values)
    assert (
        select_experimental_candidate(
            ((probability, forecast, stale),),
            now=forecast.forecast_reference_time + timedelta(seconds=10),
        ).state
        is CandidateState.ABSTAIN
    )


def test_final_gates_bind_price_forecast_rules_ack_and_global_limit() -> None:
    result = qualify()
    assert result.selected is not None
    candidate = result.selected
    now = candidate.eligibility.created_at + timedelta(seconds=1)
    ok, reasons = final_experimental_gates(
        candidate,
        now=now,
        forecast_evidence_identity=candidate.eligibility.forecast_evidence_identity,
        market_evidence_identity=candidate.economics_evidence_identity,
        exact_price=candidate.executable_price,
        rules_identity="rules",
        current_rules_identity="rules",
        global_submission_count=0,
        unresolved_canary=False,
        unknown_order=False,
        current_position=Decimal("0"),
        acknowledgement=ACKNOWLEDGEMENT,
        approval_acknowledgement_hash=ExperimentalApprovalBinding.create(
            candidate.candidate_id, ACKNOWLEDGEMENT
        ).approval_hash,
    )
    assert ok and not reasons
    blocked, reasons = final_experimental_gates(
        candidate,
        now=now,
        forecast_evidence_identity="changed",
        market_evidence_identity=candidate.economics_evidence_identity,
        exact_price=candidate.executable_price + Decimal(".01"),
        rules_identity="rules",
        current_rules_identity="changed",
        global_submission_count=1,
        unresolved_canary=True,
        unknown_order=True,
        current_position=Decimal("1"),
        acknowledgement="APPROVE THIS ONE-CONTRACT CANARY",
    )
    assert not blocked and {
        "forecast_changed",
        "price_changed",
        "rules_changed",
        "global_one_order_limit_used",
        "unvalidated_proxy_acknowledgement_missing",
    }.issubset(reasons)


def test_acknowledgement_is_stronger_than_generic_canary_confirmation() -> None:
    with pytest.raises(PermissionError):
        validate_experimental_acknowledgement("APPROVE THIS ONE-CONTRACT CANARY")
