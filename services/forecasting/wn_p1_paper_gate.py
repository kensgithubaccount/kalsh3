"""WN-P1 Phase 0: fail-closed prospective admission for research-only paper candidates.

No acquisition, persistence, model fitting, fee calculation, order, or production
interfaces. An explicitly empty approved-model registry prevents promoting the
October p50 WeatherNext proxy into a Kalshi settlement probability. A separate,
independently reviewed prospective model-issuance checkpoint is mandatory.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum

POLICY_ID = "wn-p1-prospective-paper-admission-phase0-v1"
FIRST_PROSPECTIVE_TARGET = date(2026, 11, 1)
CITY_SERIES = {
    "boston": "KXHIGHTBOS",
    "miami": "KXHIGHMIA",
    "denver": "KXHIGHDEN",
    "los_angeles": "KXHIGHLAX",
    "seattle": "KXHIGHTSEA",
}
PROVENANCE_SHA_LENGTH = 64
ONE = Decimal(1)
ZERO = Decimal(0)
QUANTITY = ONE
FRESHNESS = timedelta(seconds=120)
RAW_GAP_MIN = Decimal("0.10")
UNCERTAINTY_BUFFER = Decimal("0.05")
BUFFERED_GAP_MIN = Decimal("0.05")

# This is intentionally empty. No approved TWC-settlement-specific multicity
# distribution exists. Adding an entry requires its own independent review,
# independently proven pre-outcome training boundary, and a new policy version.
APPROVED_PROSPECTIVE_MODEL_IDS: frozenset[str] = frozenset()


class PaperGateError(ValueError):
    """Malformed or out-of-scope prospective evidence; never coerce to readiness."""


class PaperState(StrEnum):
    ABSTAIN_NO_MODEL = "ABSTAIN_NO_MODEL"
    ABSTAIN_UNREGISTERED_MODEL = "ABSTAIN_UNREGISTERED_MODEL"
    ABSTAIN_INCOMPLETE_FORECAST = "ABSTAIN_INCOMPLETE_FORECAST"
    ABSTAIN_MISSING_MARKET = "ABSTAIN_MISSING_MARKET"
    ABSTAIN_STALE_MARKET = "ABSTAIN_STALE_MARKET"
    ABSTAIN_FEE_NOT_ATTESTED = "ABSTAIN_FEE_NOT_ATTESTED"
    ABSTAIN_NO_AFTER_COST_GAP = "ABSTAIN_NO_AFTER_COST_GAP"
    PAPER_DIAGNOSTIC_ONLY = "PAPER_DIAGNOSTIC_ONLY"


@dataclass(frozen=True, slots=True)
class PredecisionObservation:
    city_id: str
    target_date: date
    evaluated_at_utc: datetime
    forecast_observed_at_utc: datetime | None
    forecast_ingested_at_utc: datetime | None
    forecast_sha256: str | None
    model_id: str | None
    model_probability_yes: Decimal | None
    model_evidence_sha256: str | None
    event_ticker: str
    sibling_tickers: tuple[str, ...]
    market_observed_at_utc: datetime | None
    market_sha256: str | None
    verified_fee_policy_id: str | None
    book_quantity: Decimal
    yes_fee_inclusive_debit: Decimal | None
    no_fee_inclusive_debit: Decimal | None
    settlement_source: str = "The Weather Company"
    research_only: bool = True
    production_influence: str = "0"
    trading_authority: str = "NONE"


@dataclass(frozen=True, slots=True)
class PaperDecision:
    state: PaperState
    city_id: str
    target_date: date
    evidence_id: str
    selected_paper_side: str | None
    yes_after_cost_gap: Decimal | None
    no_after_cost_gap: Decimal | None
    policy_id: str = POLICY_ID
    research_only: bool = True
    production_influence: str = "0"
    trading_authority: str = "NONE"

    def __post_init__(self) -> None:
        if (
            type(self.state) is not PaperState
            or self.state is PaperState.PAPER_DIAGNOSTIC_ONLY
            or self.policy_id != POLICY_ID
            or self.research_only is not True
            or self.production_influence != "0"
            or self.trading_authority != "NONE"
            or type(self.evidence_id) is not str
            or len(self.evidence_id) != 64
            or any(char not in "0123456789abcdef" for char in self.evidence_id)
            or self.selected_paper_side is not None
            or self.yes_after_cost_gap is not None
            or self.no_after_cost_gap is not None
        ):
            raise PaperGateError("Phase 0 decisions cannot grant paper or trading authority")


def _utc(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PaperGateError(f"{label}: timezone-aware datetime required")
    return value.astimezone(UTC)


def _sha(value: str | None, label: str) -> None:
    if value is None:
        return
    if (
        not isinstance(value, str)
        or len(value) != PROVENANCE_SHA_LENGTH
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise PaperGateError(f"{label}: exact lowercase SHA-256 required")


def _price(value: Decimal | None, label: str) -> None:
    if (
        type(value) is not Decimal
        or not value.is_finite()
        or value <= ZERO
        or value > Decimal("1.5")
    ):
        raise PaperGateError(f"{label}: finite fee-inclusive debit required")


def _admissible_identity(observation: PredecisionObservation) -> tuple[datetime, datetime]:
    if type(observation) is not PredecisionObservation:
        raise PaperGateError("exact predecision observation type required")
    if (
        observation.research_only is not True
        or observation.production_influence != "0"
        or observation.trading_authority != "NONE"
    ):
        raise PaperGateError("research-only authority boundary violated")
    if (
        type(observation.city_id) is not str
        or type(observation.target_date) is not date
        or observation.city_id not in CITY_SERIES
        or observation.target_date < FIRST_PROSPECTIVE_TARGET
    ):
        raise PaperGateError("wrong city or pre-prospective target date")
    if observation.settlement_source != "The Weather Company":
        raise PaperGateError("settlement source is not bound to The Weather Company")
    ticker = (
        CITY_SERIES[observation.city_id] + "-" + observation.target_date.strftime("%y%b%d").upper()
    )
    if observation.event_ticker != ticker:
        raise PaperGateError("event/date/series identity mismatch")
    if (
        type(observation.sibling_tickers) is not tuple
        or len(observation.sibling_tickers) != 6
        or any(type(t) is not str for t in observation.sibling_tickers)
        or len(set(observation.sibling_tickers)) != 6
        or any(not t.startswith(ticker + "-") for t in observation.sibling_tickers)
    ):
        raise PaperGateError("exact six unique sibling identities required")
    if type(observation.book_quantity) is not Decimal or observation.book_quantity != QUANTITY:
        raise PaperGateError("prospective paper unit is exactly one contract")
    due = datetime.combine(observation.target_date, time(9), tzinfo=UTC)
    at = _utc(observation.evaluated_at_utc, "evaluated_at_utc")
    if at < due or at >= due + timedelta(minutes=5):
        raise PaperGateError("observation outside prospective decision interval")
    for label, sha_value in (
        ("forecast_sha256", observation.forecast_sha256),
        ("model_evidence_sha256", observation.model_evidence_sha256),
        ("market_sha256", observation.market_sha256),
    ):
        _sha(sha_value, label)
    if observation.model_probability_yes is not None and (
        type(observation.model_probability_yes) is not Decimal
        or not observation.model_probability_yes.is_finite()
        or not ZERO <= observation.model_probability_yes <= ONE
    ):
        raise PaperGateError("model probability is not an exact finite value in [0,1]")
    for label, price_value in (
        ("yes_fee_inclusive_debit", observation.yes_fee_inclusive_debit),
        ("no_fee_inclusive_debit", observation.no_fee_inclusive_debit),
    ):
        if price_value is not None:
            _price(price_value, label)
    for label, timestamp_value in (
        ("forecast_observed_at_utc", observation.forecast_observed_at_utc),
        ("forecast_ingested_at_utc", observation.forecast_ingested_at_utc),
        ("market_observed_at_utc", observation.market_observed_at_utc),
    ):
        if timestamp_value is not None and _utc(timestamp_value, label) > at:
            raise PaperGateError(f"{label}: observation cannot come from the future")
    return at, due


def _evidence_id(observation: PredecisionObservation) -> str:
    # A pure identity, not a proof that caller-provided bytes were externally acquired.
    def stamp(value: datetime | None) -> str | None:
        return value.astimezone(UTC).isoformat() if value is not None else None

    fields = (
        POLICY_ID,
        observation.city_id,
        observation.target_date.isoformat(),
        stamp(observation.evaluated_at_utc),
        stamp(observation.forecast_observed_at_utc),
        stamp(observation.forecast_ingested_at_utc),
        observation.forecast_sha256,
        observation.model_id,
        str(observation.model_probability_yes),
        observation.model_evidence_sha256,
        observation.event_ticker,
        observation.sibling_tickers,
        stamp(observation.market_observed_at_utc),
        observation.market_sha256,
        observation.verified_fee_policy_id,
        str(observation.book_quantity),
        observation.settlement_source,
        observation.research_only,
        observation.production_influence,
        observation.trading_authority,
        str(observation.yes_fee_inclusive_debit),
        str(observation.no_fee_inclusive_debit),
    )
    return hashlib.sha256(json.dumps(fields, separators=(",", ":")).encode()).hexdigest()


def side_aware_gaps(
    probability_yes: Decimal, yes_debit: Decimal, no_debit: Decimal
) -> tuple[Decimal, Decimal]:
    """Pure diagnostic against *all-in debits*, never displayed/cached market prices."""
    if (
        type(probability_yes) is not Decimal
        or not probability_yes.is_finite()
        or not ZERO <= probability_yes <= ONE
    ):
        raise PaperGateError("invalid model probability")
    _price(yes_debit, "yes_debit")
    _price(no_debit, "no_debit")
    if yes_debit + no_debit < ONE:
        raise PaperGateError("inconsistent two-sided fee-inclusive taker debits")
    return probability_yes - yes_debit, (ONE - probability_yes) - no_debit


def evaluate(observation: PredecisionObservation) -> PaperDecision:
    """Always preserve abstention; no current model can become a paper candidate.

    A future review may add a *separately issued* model identity, but must also
    bind/verify actual raw forecast, Kalshi orderbook and fee issuer evidence.
    This Phase 0 module is not a network or durable-recording implementation.
    """
    at, due = _admissible_identity(observation)
    if observation.model_id is None or observation.model_probability_yes is None:
        state = PaperState.ABSTAIN_NO_MODEL
    elif observation.model_id not in APPROVED_PROSPECTIVE_MODEL_IDS:
        state = PaperState.ABSTAIN_UNREGISTERED_MODEL
    elif (
        observation.forecast_sha256 is None
        or observation.model_evidence_sha256 is None
        or observation.forecast_observed_at_utc is None
        or observation.forecast_ingested_at_utc is None
        or _utc(observation.forecast_observed_at_utc, "forecast_observed_at_utc") > at
        or _utc(observation.forecast_ingested_at_utc, "forecast_ingested_at_utc") >= due
    ):
        state = PaperState.ABSTAIN_INCOMPLETE_FORECAST
    elif observation.market_sha256 is None or observation.market_observed_at_utc is None:
        state = PaperState.ABSTAIN_MISSING_MARKET
    elif at - _utc(observation.market_observed_at_utc, "market_observed_at_utc") > FRESHNESS:
        state = PaperState.ABSTAIN_STALE_MARKET
    elif (
        not observation.verified_fee_policy_id
        or observation.yes_fee_inclusive_debit is None
        or observation.no_fee_inclusive_debit is None
    ):
        state = PaperState.ABSTAIN_FEE_NOT_ATTESTED
    else:
        yes_debit = observation.yes_fee_inclusive_debit
        no_debit = observation.no_fee_inclusive_debit
        yes_gap, no_gap = side_aware_gaps(observation.model_probability_yes, yes_debit, no_debit)
        if (
            max(yes_gap, no_gap) < RAW_GAP_MIN
            or max(yes_gap, no_gap) - UNCERTAINTY_BUFFER < BUFFERED_GAP_MIN
        ):
            state = PaperState.ABSTAIN_NO_AFTER_COST_GAP
        else:
            state = PaperState.PAPER_DIAGNOSTIC_ONLY
    # As no model is approved, the only reachable results are abstentions.
    if state is PaperState.PAPER_DIAGNOSTIC_ONLY:
        raise PaperGateError("paper candidate issuance not enabled at Phase 0")
    return PaperDecision(
        state,
        observation.city_id,
        observation.target_date,
        _evidence_id(observation),
        None,
        None,
        None,
    )
