"""WN-A1 alert decision policy and plain-English rendering.

This is a RESEARCH alerting heuristic for data collection, versioned and frozen for this
milestone -- NOT a production promotion threshold and NOT profitability evidence. The
primary alert text may only ever show one of the four ``AlertState`` headline words and
must never claim anything the evidence does not support (see ``_assert_no_banned_claims``).

Side-aware executable economics: a candidate is never selected off the absolute distance
between the raw WeatherNext probability and a market price. Each side is compared
independently against its own conservative, fee-inclusive taker debit --
``model_probability_yes`` vs. the YES side's debit, and ``1 - model_probability_yes`` vs.
the NO side's debit -- and a side is only ever selected when its own buffered gap clears
the frozen research thresholds. A large absolute YES-price/model gap is worthless evidence
if the NO side is what is actually cheap to take (or vice versa); see
``test_absolute_gap_without_executable_side_is_skipped`` for the concrete counterexample
this guards against.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from .wn_a1_current_daily_high_authority import WindowStatus
from .wn_a1_domain import (
    BANNED_ALERT_WORDS,
    PRODUCTION_INFLUENCE,
    RESEARCH_ONLY,
    AlertSide,
    AlertState,
    WnA1Error,
)
from .wn_a1_probability import BoundaryRiskDiagnostic, RawProbabilityResult

ALERT_POLICY_VERSION = "wn-a1-alert-policy-v2-side-aware"


@dataclass(frozen=True, slots=True)
class AlertPolicy:
    raw_gap_threshold_pp: Decimal = Decimal(10)
    buffered_gap_threshold_pp: Decimal = Decimal(5)
    conservative_buffer_pp: Decimal = Decimal(5)
    version: str = ALERT_POLICY_VERSION


DEFAULT_POLICY = AlertPolicy()


class GateFailure(StrEnum):
    CONTRACT_NOT_SUPPORTED = "CONTRACT_NOT_SUPPORTED"
    ENSEMBLE_INCOMPLETE = "ENSEMBLE_INCOMPLETE"
    MARKET_STALE = "MARKET_STALE"
    NO_EXECUTABLE_MARKET_PRICE = "NO_EXECUTABLE_MARKET_PRICE"
    WINDOW_NOT_ESTABLISHED = "WINDOW_NOT_ESTABLISHED"
    NO_SIDE_CLEARS_RAW_THRESHOLD = "NO_SIDE_CLEARS_RAW_THRESHOLD"
    NO_SIDE_CLEARS_BUFFERED_THRESHOLD = "NO_SIDE_CLEARS_BUFFERED_THRESHOLD"
    BOUNDARY_REVERSAL = "BOUNDARY_REVERSAL"


@dataclass(frozen=True, slots=True)
class AlertDecision:
    state: AlertState
    gate_failures: tuple[GateFailure, ...]
    side: AlertSide | None
    model_probability_yes: Decimal | None
    yes_conservative_debit: Decimal | None
    no_conservative_debit: Decimal | None
    yes_gap_pp: Decimal | None
    yes_buffered_gap_pp: Decimal | None
    no_gap_pp: Decimal | None
    no_buffered_gap_pp: Decimal | None
    selected_gap_pp: Decimal | None
    selected_buffered_gap_pp: Decimal | None
    policy_version: str
    research_only: bool = RESEARCH_ONLY
    production_influence: Decimal = PRODUCTION_INFLUENCE


def decide_alert(
    *,
    contract_supported: bool,
    ensemble_complete: bool,
    market_fresh: bool,
    yes_conservative_debit: Decimal | None,
    no_conservative_debit: Decimal | None,
    raw: RawProbabilityResult | None,
    window_status: WindowStatus | None,
    boundary: BoundaryRiskDiagnostic | None,
    policy: AlertPolicy = DEFAULT_POLICY,
) -> AlertDecision:
    """Apply every required WN-A1 gate; never returns TAKE A LOOK unless all pass.

    Each side is evaluated independently: YES's model probability against the YES taker
    debit, NO's complement probability against the NO taker debit. A side is only ever
    selected when its own raw AND buffered gap clear the frozen thresholds -- absolute
    disagreement between the model and a single side's price is never trade direction.
    """
    failures: list[GateFailure] = []
    if not contract_supported:
        failures.append(GateFailure.CONTRACT_NOT_SUPPORTED)
    if not ensemble_complete:
        failures.append(GateFailure.ENSEMBLE_INCOMPLETE)
    if not market_fresh:
        failures.append(GateFailure.MARKET_STALE)
    if yes_conservative_debit is None and no_conservative_debit is None:
        failures.append(GateFailure.NO_EXECUTABLE_MARKET_PRICE)

    model_probability_yes = raw.probability if raw is not None else None

    if failures or raw is None or model_probability_yes is None:
        return AlertDecision(
            state=AlertState.DATA_NOT_READY,
            gate_failures=tuple(failures),
            side=None,
            model_probability_yes=model_probability_yes,
            yes_conservative_debit=yes_conservative_debit,
            no_conservative_debit=no_conservative_debit,
            yes_gap_pp=None,
            yes_buffered_gap_pp=None,
            no_gap_pp=None,
            no_buffered_gap_pp=None,
            selected_gap_pp=None,
            selected_buffered_gap_pp=None,
            policy_version=policy.version,
        )

    model_probability_no = Decimal(1) - model_probability_yes
    yes_gap_pp, yes_buffered_gap_pp = _side_gap(
        model_probability_yes, yes_conservative_debit, policy.conservative_buffer_pp
    )
    no_gap_pp, no_buffered_gap_pp = _side_gap(
        model_probability_no, no_conservative_debit, policy.conservative_buffer_pp
    )
    yes_qualifies = _qualifies(yes_gap_pp, yes_buffered_gap_pp, policy)
    no_qualifies = _qualifies(no_gap_pp, no_buffered_gap_pp, policy)

    def _base(
        state: AlertState,
        gate_failures: tuple[GateFailure, ...],
        side: AlertSide | None,
        selected_gap_pp: Decimal | None,
        selected_buffered_gap_pp: Decimal | None,
    ) -> AlertDecision:
        return AlertDecision(
            state=state,
            gate_failures=gate_failures,
            side=side,
            model_probability_yes=model_probability_yes,
            yes_conservative_debit=yes_conservative_debit,
            no_conservative_debit=no_conservative_debit,
            yes_gap_pp=yes_gap_pp,
            yes_buffered_gap_pp=yes_buffered_gap_pp,
            no_gap_pp=no_gap_pp,
            no_buffered_gap_pp=no_buffered_gap_pp,
            selected_gap_pp=selected_gap_pp,
            selected_buffered_gap_pp=selected_buffered_gap_pp,
            policy_version=policy.version,
        )

    if not yes_qualifies and not no_qualifies:
        any_side_clears_raw = (
            yes_gap_pp is not None and yes_gap_pp >= policy.raw_gap_threshold_pp
        ) or (no_gap_pp is not None and no_gap_pp >= policy.raw_gap_threshold_pp)
        reason = (
            GateFailure.NO_SIDE_CLEARS_BUFFERED_THRESHOLD
            if any_side_clears_raw
            else GateFailure.NO_SIDE_CLEARS_RAW_THRESHOLD
        )
        return _base(AlertState.SKIP, (reason,), None, None, None)

    if yes_qualifies and no_qualifies:
        assert (  # noqa: S101 -- proven non-None by _qualifies() above
            yes_gap_pp is not None
            and yes_buffered_gap_pp is not None
            and no_gap_pp is not None
            and no_buffered_gap_pp is not None
        )
        side, gap, buffered = (
            (AlertSide.YES, yes_gap_pp, yes_buffered_gap_pp)
            if yes_buffered_gap_pp >= no_buffered_gap_pp
            else (AlertSide.NO, no_gap_pp, no_buffered_gap_pp)
        )
    elif yes_qualifies:
        assert (  # noqa: S101 -- proven non-None by _qualifies() above
            yes_gap_pp is not None and yes_buffered_gap_pp is not None
        )
        side, gap, buffered = AlertSide.YES, yes_gap_pp, yes_buffered_gap_pp
    else:
        assert (  # noqa: S101 -- proven non-None by _qualifies() above
            no_gap_pp is not None and no_buffered_gap_pp is not None
        )
        side, gap, buffered = AlertSide.NO, no_gap_pp, no_buffered_gap_pp

    ceiling_reasons: list[GateFailure] = []
    if window_status is not WindowStatus.ESTABLISHED_CIVIL_LOCAL_DAY:
        ceiling_reasons.append(GateFailure.WINDOW_NOT_ESTABLISHED)
    if boundary is not None and boundary.reverses:
        ceiling_reasons.append(GateFailure.BOUNDARY_REVERSAL)

    if ceiling_reasons:
        return _base(AlertState.TOO_UNCERTAIN, tuple(ceiling_reasons), side, gap, buffered)
    return _base(AlertState.TAKE_A_LOOK, (), side, gap, buffered)


def _side_gap(
    model_probability: Decimal, debit: Decimal | None, buffer_pp: Decimal
) -> tuple[Decimal | None, Decimal | None]:
    if debit is None:
        return None, None
    gap_pp = (model_probability - debit) * 100
    return gap_pp, gap_pp - buffer_pp


def _qualifies(
    gap_pp: Decimal | None, buffered_gap_pp: Decimal | None, policy: AlertPolicy
) -> bool:
    return (
        gap_pp is not None
        and buffered_gap_pp is not None
        and gap_pp >= policy.raw_gap_threshold_pp
        and buffered_gap_pp >= policy.buffered_gap_threshold_pp
    )


def render_primary_alert(
    *,
    decision: AlertDecision,
    location: str,
    target_date: date,
) -> str:
    """Render the plain-English primary alert. No quant jargon, no unsupported claims."""
    header = f"{location} high {target_date.strftime('%b %d')} — {decision.state.value}"
    lines = [header, ""]
    if decision.state is AlertState.DATA_NOT_READY:
        lines += [
            "Not enough verified data is available yet to compare Google's forecast to",
            "Kalshi's price for this contract.",
        ]
    elif decision.state is AlertState.SKIP:
        lines += [
            "Why:",
            "After Kalshi's trading fees, neither the YES side nor the NO side is priced",
            "far enough from Google's forecast to be worth a manual look today.",
        ]
    else:
        side_phrase = (
            "YES is worth checking" if decision.side is AlertSide.YES else "NO is worth checking"
        )
        lines += [side_phrase, ""]
        if decision.state is AlertState.TOO_UNCERTAIN:
            lines += [
                "Why:",
                "After Kalshi's trading fees, that side looks meaningfully cheaper than what",
                "Google's forecast members suggest, but the exact settlement details or a",
                "tight range boundary make this too uncertain to call.",
                "",
                "Main risk:",
                "A small forecast miss could move the winning range, or the exact rules this",
                "will settle against aren't fully confirmed yet.",
            ]
        else:
            lines += [
                "Why:",
                "After Kalshi's trading fees, that side looks meaningfully cheaper than what",
                "Google's forecast members suggest.",
                "",
                "Main risk:",
                "A 1-2 degree forecast miss could move the winning range.",
                "",
                "Suggestion:",
                "Worth checking manually before you trade.",
            ]
    lines += ["", "Nothing has been bought."]
    text = "\n".join(lines)
    _assert_no_banned_claims(text)
    return text


def _assert_no_banned_claims(text: str) -> None:
    lowered = text.lower()
    for word in BANNED_ALERT_WORDS:
        if word in lowered:
            raise WnA1Error(f"primary alert text contains a banned claim: {word!r}")
