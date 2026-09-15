"""WN-A1 alert decision policy and plain-English rendering.

This is a RESEARCH alerting heuristic for data collection, versioned and frozen for this
milestone -- NOT a production promotion threshold and NOT profitability evidence. The
primary alert text may only ever show one of the four ``AlertState`` headline words and
must never claim anything the evidence does not support (see ``_assert_no_banned_claims``).
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
    AlertState,
    WnA1Error,
)
from .wn_a1_probability import BoundaryRiskDiagnostic, RawProbabilityResult

ALERT_POLICY_VERSION = "wn-a1-alert-policy-v1"


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
    GAP_BELOW_RAW_THRESHOLD = "GAP_BELOW_RAW_THRESHOLD"
    GAP_BELOW_BUFFERED_THRESHOLD = "GAP_BELOW_BUFFERED_THRESHOLD"
    BOUNDARY_REVERSAL = "BOUNDARY_REVERSAL"


@dataclass(frozen=True, slots=True)
class AlertDecision:
    state: AlertState
    gate_failures: tuple[GateFailure, ...]
    raw_probability: Decimal | None
    market_yes_probability: Decimal | None
    gap_pp: Decimal | None
    buffered_gap_pp: Decimal | None
    policy_version: str
    research_only: bool = RESEARCH_ONLY
    production_influence: Decimal = PRODUCTION_INFLUENCE


def decide_alert(
    *,
    contract_supported: bool,
    ensemble_complete: bool,
    market_fresh: bool,
    market_yes_probability: Decimal | None,
    raw: RawProbabilityResult | None,
    window_status: WindowStatus | None,
    boundary: BoundaryRiskDiagnostic | None,
    policy: AlertPolicy = DEFAULT_POLICY,
) -> AlertDecision:
    """Apply every required WN-A1 gate; never returns TAKE A LOOK unless all pass."""
    failures: list[GateFailure] = []
    if not contract_supported:
        failures.append(GateFailure.CONTRACT_NOT_SUPPORTED)
    if not ensemble_complete:
        failures.append(GateFailure.ENSEMBLE_INCOMPLETE)
    if not market_fresh:
        failures.append(GateFailure.MARKET_STALE)
    if market_yes_probability is None:
        failures.append(GateFailure.NO_EXECUTABLE_MARKET_PRICE)

    if failures or raw is None or market_yes_probability is None:
        return AlertDecision(
            state=AlertState.DATA_NOT_READY,
            gate_failures=tuple(failures),
            raw_probability=raw.probability if raw is not None else None,
            market_yes_probability=market_yes_probability,
            gap_pp=None,
            buffered_gap_pp=None,
            policy_version=policy.version,
        )

    gap_pp = abs(raw.probability - market_yes_probability) * 100
    buffered_gap_pp = gap_pp - policy.conservative_buffer_pp

    ceiling_reasons: list[GateFailure] = []
    if window_status is not WindowStatus.ESTABLISHED_CIVIL_LOCAL_DAY:
        ceiling_reasons.append(GateFailure.WINDOW_NOT_ESTABLISHED)
    if boundary is not None and boundary.reverses:
        ceiling_reasons.append(GateFailure.BOUNDARY_REVERSAL)

    if gap_pp < policy.raw_gap_threshold_pp:
        return AlertDecision(
            state=AlertState.SKIP,
            gate_failures=(GateFailure.GAP_BELOW_RAW_THRESHOLD,),
            raw_probability=raw.probability,
            market_yes_probability=market_yes_probability,
            gap_pp=gap_pp,
            buffered_gap_pp=buffered_gap_pp,
            policy_version=policy.version,
        )
    if buffered_gap_pp < policy.buffered_gap_threshold_pp:
        return AlertDecision(
            state=AlertState.SKIP,
            gate_failures=(GateFailure.GAP_BELOW_BUFFERED_THRESHOLD,),
            raw_probability=raw.probability,
            market_yes_probability=market_yes_probability,
            gap_pp=gap_pp,
            buffered_gap_pp=buffered_gap_pp,
            policy_version=policy.version,
        )
    if ceiling_reasons:
        return AlertDecision(
            state=AlertState.TOO_UNCERTAIN,
            gate_failures=tuple(ceiling_reasons),
            raw_probability=raw.probability,
            market_yes_probability=market_yes_probability,
            gap_pp=gap_pp,
            buffered_gap_pp=buffered_gap_pp,
            policy_version=policy.version,
        )
    return AlertDecision(
        state=AlertState.TAKE_A_LOOK,
        gate_failures=(),
        raw_probability=raw.probability,
        market_yes_probability=market_yes_probability,
        gap_pp=gap_pp,
        buffered_gap_pp=buffered_gap_pp,
        policy_version=policy.version,
    )


def render_primary_alert(
    *,
    decision: AlertDecision,
    location: str,
    target_date: date,
    hotter_or_colder: str | None = None,
) -> str:
    """Render the plain-English primary alert. No quant jargon, no unsupported claims."""
    header = f"{location} high {target_date.strftime('%b %d')} — {decision.state.value}"
    lines = [header, ""]
    if decision.state is AlertState.DATA_NOT_READY:
        lines += [
            "Not enough verified data is available yet to compare Google's forecast to",
            "Kalshi's price for this contract.",
        ]
    else:
        lines += [
            f"Kalshi says: about {_pct(decision.market_yes_probability)}",
            f"WeatherNext says: about {_pct(decision.raw_probability)}",
            "",
        ]
        if decision.state is AlertState.SKIP:
            lines += [
                "Why:",
                "Kalshi's price and Google's forecast are close enough that this isn't worth",
                "a manual look today.",
            ]
        elif decision.state is AlertState.TOO_UNCERTAIN:
            hint = hotter_or_colder or "different"
            lines += [
                "Why:",
                f"Google's forecast members look meaningfully {hint} than what the market",
                "is pricing, but the exact settlement details or a tight range boundary",
                "make this too uncertain to call.",
                "",
                "Main risk:",
                "A small forecast miss could move the winning range, or the exact rules this",
                "will settle against aren't fully confirmed yet.",
            ]
        else:
            phrase = f"{hotter_or_colder} than" if hotter_or_colder else "different from"
            lines += [
                "Why:",
                f"Google's forecast members are meaningfully {phrase} what the market is pricing.",
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


def _pct(value: Decimal | None) -> str:
    if value is None:
        return "unknown"
    return f"{(value * 100).quantize(Decimal('1'))}%"


def _assert_no_banned_claims(text: str) -> None:
    lowered = text.lower()
    for word in BANNED_ALERT_WORDS:
        if word in lowered:
            raise WnA1Error(f"primary alert text contains a banned claim: {word!r}")
