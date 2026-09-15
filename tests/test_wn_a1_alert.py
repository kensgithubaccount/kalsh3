from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from services.forecasting.wn_a1_alert import (
    DEFAULT_POLICY,
    AlertPolicy,
    GateFailure,
    decide_alert,
    render_primary_alert,
)
from services.forecasting.wn_a1_current_daily_high_authority import WindowStatus
from services.forecasting.wn_a1_domain import BANNED_ALERT_WORDS, AlertSide, AlertState, WnA1Error
from services.forecasting.wn_a1_probability import BoundaryRiskDiagnostic, RawProbabilityResult


def raw(probability: Decimal) -> RawProbabilityResult:
    numerator = int(probability * 64)
    members = ()  # not inspected by decide_alert
    return RawProbabilityResult(
        numerator=numerator,
        denominator=64,
        probability=probability,
        members=members,
        min_f=Decimal("70"),
        median_f=Decimal("85"),
        max_f=Decimal("95"),
    )


def stable_boundary() -> BoundaryRiskDiagnostic:
    return BoundaryRiskDiagnostic(
        members_within_one_f_of_boundary=0,
        baseline_preferred_ticker="A",
        plus_one_preferred_ticker="A",
        minus_one_preferred_ticker="A",
    )


def reversing_boundary() -> BoundaryRiskDiagnostic:
    return BoundaryRiskDiagnostic(
        members_within_one_f_of_boundary=40,
        baseline_preferred_ticker="A",
        plus_one_preferred_ticker="A",
        minus_one_preferred_ticker="B",
    )


def test_take_a_look_when_yes_side_clears_every_gate() -> None:
    # model YES=42%, YES all-in debit=27% -> 15pp raw gap, 10pp buffered gap.
    decision = decide_alert(
        contract_supported=True,
        ensemble_complete=True,
        market_fresh=True,
        yes_conservative_debit=Decimal("0.27"),
        no_conservative_debit=Decimal("0.90"),
        raw=raw(Decimal("0.42")),
        window_status=WindowStatus.ESTABLISHED_CIVIL_LOCAL_DAY,
        boundary=stable_boundary(),
    )
    assert decision.state is AlertState.TAKE_A_LOOK
    assert decision.gate_failures == ()
    assert decision.side is AlertSide.YES
    assert decision.yes_gap_pp == Decimal("15.00")
    assert decision.selected_gap_pp == Decimal("15.00")


def test_take_a_look_when_no_side_clears_every_gate() -> None:
    # model YES=20% -> model NO=80%. NO all-in debit is cheap (0.60) -> 20pp NO-side gap;
    # the YES side is nowhere close (debit 0.50 against a 20% model probability).
    decision = decide_alert(
        contract_supported=True,
        ensemble_complete=True,
        market_fresh=True,
        yes_conservative_debit=Decimal("0.50"),
        no_conservative_debit=Decimal("0.60"),
        raw=raw(Decimal("0.20")),
        window_status=WindowStatus.ESTABLISHED_CIVIL_LOCAL_DAY,
        boundary=stable_boundary(),
    )
    assert decision.state is AlertState.TAKE_A_LOOK
    assert decision.side is AlertSide.NO
    assert decision.no_gap_pp == Decimal("20.00")


def test_absolute_gap_without_executable_side_is_skipped() -> None:
    """Item 4's required counterexample: a large ABSOLUTE model/YES-ask gap must never be
    trade direction. model YES=10%, YES ask~40% (30pp absolute gap -- the old buggy logic
    would flag this), but the NO all-in debit is 95%: neither side actually clears the
    buffered threshold once fees/spread are accounted for, so this must SKIP."""
    decision = decide_alert(
        contract_supported=True,
        ensemble_complete=True,
        market_fresh=True,
        yes_conservative_debit=Decimal("0.40"),
        no_conservative_debit=Decimal("0.95"),
        raw=raw(Decimal("0.10")),
        window_status=WindowStatus.ESTABLISHED_CIVIL_LOCAL_DAY,
        boundary=stable_boundary(),
    )
    assert decision.state is AlertState.SKIP
    assert decision.side is None
    assert decision.gate_failures == (GateFailure.NO_SIDE_CLEARS_RAW_THRESHOLD,)
    # The old |model - yes_ask| logic would have computed a 30pp gap here.
    old_buggy_absolute_gap = abs(Decimal("0.10") - Decimal("0.40")) * 100
    assert old_buggy_absolute_gap == Decimal("30.00")


def test_skip_when_no_side_clears_raw_threshold() -> None:
    decision = decide_alert(
        contract_supported=True,
        ensemble_complete=True,
        market_fresh=True,
        yes_conservative_debit=Decimal("0.35"),
        no_conservative_debit=Decimal("0.68"),
        raw=raw(Decimal("0.35")),
        window_status=WindowStatus.ESTABLISHED_CIVIL_LOCAL_DAY,
        boundary=stable_boundary(),
    )
    assert decision.state is AlertState.SKIP
    assert decision.gate_failures == (GateFailure.NO_SIDE_CLEARS_RAW_THRESHOLD,)


def test_skip_when_gap_survives_raw_but_not_buffer() -> None:
    # Use a wider conservative buffer (9pp) so a 12pp raw gap clears the 10pp raw
    # threshold but the buffered gap (12 - 9 = 3pp) falls below the 5pp buffered floor.
    policy = AlertPolicy(
        raw_gap_threshold_pp=Decimal(10),
        buffered_gap_threshold_pp=Decimal(5),
        conservative_buffer_pp=Decimal(9),
    )
    decision = decide_alert(
        contract_supported=True,
        ensemble_complete=True,
        market_fresh=True,
        yes_conservative_debit=Decimal("0.30"),
        no_conservative_debit=Decimal("0.90"),
        raw=raw(Decimal("0.42")),  # 12pp raw gap on YES
        window_status=WindowStatus.ESTABLISHED_CIVIL_LOCAL_DAY,
        boundary=stable_boundary(),
        policy=policy,
    )
    assert decision.state is AlertState.SKIP
    assert decision.gate_failures == (GateFailure.NO_SIDE_CLEARS_BUFFERED_THRESHOLD,)


def test_too_uncertain_when_window_not_established() -> None:
    decision = decide_alert(
        contract_supported=True,
        ensemble_complete=True,
        market_fresh=True,
        yes_conservative_debit=Decimal("0.27"),
        no_conservative_debit=Decimal("0.90"),
        raw=raw(Decimal("0.42")),
        window_status=WindowStatus.NOT_ESTABLISHED,
        boundary=stable_boundary(),
    )
    assert decision.state is AlertState.TOO_UNCERTAIN
    assert decision.side is AlertSide.YES
    assert GateFailure.WINDOW_NOT_ESTABLISHED in decision.gate_failures


def test_too_uncertain_when_boundary_stress_reverses_candidate() -> None:
    decision = decide_alert(
        contract_supported=True,
        ensemble_complete=True,
        market_fresh=True,
        yes_conservative_debit=Decimal("0.27"),
        no_conservative_debit=Decimal("0.90"),
        raw=raw(Decimal("0.42")),
        window_status=WindowStatus.ESTABLISHED_CIVIL_LOCAL_DAY,
        boundary=reversing_boundary(),
    )
    assert decision.state is AlertState.TOO_UNCERTAIN
    assert GateFailure.BOUNDARY_REVERSAL in decision.gate_failures


def test_data_not_ready_when_contract_not_supported() -> None:
    decision = decide_alert(
        contract_supported=False,
        ensemble_complete=True,
        market_fresh=True,
        yes_conservative_debit=Decimal("0.27"),
        no_conservative_debit=Decimal("0.90"),
        raw=raw(Decimal("0.42")),
        window_status=None,
        boundary=None,
    )
    assert decision.state is AlertState.DATA_NOT_READY
    assert GateFailure.CONTRACT_NOT_SUPPORTED in decision.gate_failures
    assert decision.side is None


def test_data_not_ready_when_ensemble_incomplete() -> None:
    decision = decide_alert(
        contract_supported=True,
        ensemble_complete=False,
        market_fresh=True,
        yes_conservative_debit=Decimal("0.27"),
        no_conservative_debit=Decimal("0.90"),
        raw=None,
        window_status=WindowStatus.ESTABLISHED_CIVIL_LOCAL_DAY,
        boundary=None,
    )
    assert decision.state is AlertState.DATA_NOT_READY
    assert GateFailure.ENSEMBLE_INCOMPLETE in decision.gate_failures


def test_data_not_ready_when_market_stale() -> None:
    decision = decide_alert(
        contract_supported=True,
        ensemble_complete=True,
        market_fresh=False,
        yes_conservative_debit=Decimal("0.27"),
        no_conservative_debit=Decimal("0.90"),
        raw=raw(Decimal("0.42")),
        window_status=WindowStatus.ESTABLISHED_CIVIL_LOCAL_DAY,
        boundary=stable_boundary(),
    )
    assert decision.state is AlertState.DATA_NOT_READY
    assert GateFailure.MARKET_STALE in decision.gate_failures


def test_data_not_ready_when_no_executable_price_on_either_side() -> None:
    decision = decide_alert(
        contract_supported=True,
        ensemble_complete=True,
        market_fresh=True,
        yes_conservative_debit=None,
        no_conservative_debit=None,
        raw=raw(Decimal("0.42")),
        window_status=WindowStatus.ESTABLISHED_CIVIL_LOCAL_DAY,
        boundary=stable_boundary(),
    )
    assert decision.state is AlertState.DATA_NOT_READY
    assert GateFailure.NO_EXECUTABLE_MARKET_PRICE in decision.gate_failures


def test_one_sided_book_still_evaluates_the_available_side() -> None:
    """Only the YES side has a displayed, executable price; NO is illiquid. YES alone must
    still be evaluatable -- this is not NO_EXECUTABLE_MARKET_PRICE."""
    decision = decide_alert(
        contract_supported=True,
        ensemble_complete=True,
        market_fresh=True,
        yes_conservative_debit=Decimal("0.27"),
        no_conservative_debit=None,
        raw=raw(Decimal("0.42")),
        window_status=WindowStatus.ESTABLISHED_CIVIL_LOCAL_DAY,
        boundary=stable_boundary(),
    )
    assert decision.state is AlertState.TAKE_A_LOOK
    assert decision.side is AlertSide.YES
    assert decision.no_gap_pp is None


@pytest.mark.parametrize(
    "state_factory",
    [
        lambda: decide_alert(
            contract_supported=True,
            ensemble_complete=True,
            market_fresh=True,
            yes_conservative_debit=Decimal("0.27"),
            no_conservative_debit=Decimal("0.90"),
            raw=raw(Decimal("0.42")),
            window_status=WindowStatus.ESTABLISHED_CIVIL_LOCAL_DAY,
            boundary=stable_boundary(),
        ),
        lambda: decide_alert(
            contract_supported=True,
            ensemble_complete=True,
            market_fresh=True,
            yes_conservative_debit=Decimal("0.35"),
            no_conservative_debit=Decimal("0.68"),
            raw=raw(Decimal("0.35")),
            window_status=WindowStatus.ESTABLISHED_CIVIL_LOCAL_DAY,
            boundary=stable_boundary(),
        ),
        lambda: decide_alert(
            contract_supported=True,
            ensemble_complete=True,
            market_fresh=True,
            yes_conservative_debit=Decimal("0.27"),
            no_conservative_debit=Decimal("0.90"),
            raw=raw(Decimal("0.42")),
            window_status=WindowStatus.NOT_ESTABLISHED,
            boundary=stable_boundary(),
        ),
        lambda: decide_alert(
            contract_supported=False,
            ensemble_complete=True,
            market_fresh=True,
            yes_conservative_debit=Decimal("0.27"),
            no_conservative_debit=Decimal("0.90"),
            raw=raw(Decimal("0.42")),
            window_status=None,
            boundary=None,
        ),
    ],
)
def test_primary_alert_text_never_contains_banned_claims(state_factory) -> None:
    decision = state_factory()
    text = render_primary_alert(
        decision=decision, location="Chicago", target_date=date(2026, 9, 15)
    )
    lowered = text.lower()
    for word in BANNED_ALERT_WORDS:
        assert word not in lowered
    assert "nothing has been bought" in lowered


def test_render_primary_alert_names_the_side_plainly_for_yes() -> None:
    decision = decide_alert(
        contract_supported=True,
        ensemble_complete=True,
        market_fresh=True,
        yes_conservative_debit=Decimal("0.27"),
        no_conservative_debit=Decimal("0.90"),
        raw=raw(Decimal("0.42")),
        window_status=WindowStatus.ESTABLISHED_CIVIL_LOCAL_DAY,
        boundary=stable_boundary(),
    )
    text = render_primary_alert(
        decision=decision, location="Chicago", target_date=date(2026, 9, 15)
    )
    assert "TAKE A LOOK" in text
    assert "YES is worth checking" in text
    for other in (AlertState.SKIP, AlertState.TOO_UNCERTAIN, AlertState.DATA_NOT_READY):
        assert other.value not in text


def test_render_primary_alert_names_the_side_plainly_for_no() -> None:
    decision = decide_alert(
        contract_supported=True,
        ensemble_complete=True,
        market_fresh=True,
        yes_conservative_debit=Decimal("0.50"),
        no_conservative_debit=Decimal("0.60"),
        raw=raw(Decimal("0.20")),
        window_status=WindowStatus.ESTABLISHED_CIVIL_LOCAL_DAY,
        boundary=stable_boundary(),
    )
    text = render_primary_alert(
        decision=decision, location="Chicago", target_date=date(2026, 9, 15)
    )
    assert "TAKE A LOOK" in text
    assert "NO is worth checking" in text


def test_assert_no_banned_claims_raises_on_injected_banned_word() -> None:
    from services.forecasting.wn_a1_alert import _assert_no_banned_claims

    with pytest.raises(WnA1Error):
        _assert_no_banned_claims("This is a guaranteed win, TAKE A LOOK.")


def test_alert_decision_has_zero_production_influence() -> None:
    decision = decide_alert(
        contract_supported=True,
        ensemble_complete=True,
        market_fresh=True,
        yes_conservative_debit=Decimal("0.27"),
        no_conservative_debit=Decimal("0.90"),
        raw=raw(Decimal("0.42")),
        window_status=WindowStatus.ESTABLISHED_CIVIL_LOCAL_DAY,
        boundary=stable_boundary(),
    )
    assert decision.production_influence == Decimal(0)
    assert decision.research_only is True


def test_policy_is_versioned_and_frozen_for_this_milestone() -> None:
    assert DEFAULT_POLICY.version == "wn-a1-alert-policy-v2-side-aware"
    assert DEFAULT_POLICY.raw_gap_threshold_pp == Decimal(10)
    assert DEFAULT_POLICY.buffered_gap_threshold_pp == Decimal(5)
