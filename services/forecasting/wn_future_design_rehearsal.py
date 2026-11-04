"""Synthetic-only probability/economics design algebra. No real-data scorer or I/O."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Context, Decimal, localcontext
from functools import wraps
from typing import Literal, cast

D = Decimal
EXPERIMENT_ID = "WN-FUTURE-PROB-AFTERCOST-20270101-20270731-DRAFT-V1"
CITIES = ("boston", "miami", "denver", "los_angeles", "seattle")
START, END = date(2027, 1, 1), date(2027, 7, 31)
REGISTRATION_DUE = datetime(2026, 12, 15, tzinfo=UTC)
CENT = D("0.01")
ONE = D(1)


def arithmetic[**P, T](function: Callable[P, T]) -> Callable[P, T]:
    @wraps(function)
    def exact(*args: P.args, **kwargs: P.kwargs) -> T:
        with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
            return function(*args, **kwargs)

    return exact


class DesignError(ValueError):
    """Reject a malformed toy input without admitting scientific evidence."""


def require(ok: bool, message: str) -> None:
    if not ok:
        raise DesignError(message)


def number(value: Decimal, *, low: Decimal, high: Decimal) -> Decimal:
    require(
        type(value) is Decimal and value.is_finite() and low <= value <= high,
        "FINITE_DECIMAL_RANGE",
    )
    require(
        -12 <= cast(int, value.as_tuple().exponent) <= 6 and len(value.as_tuple().digits) <= 18,
        "BOUNDED_DECIMAL_PRECISION",
    )
    return value


def fixture_id(value: str) -> None:
    require(
        type(value) is str and value.startswith("SIM-") and 4 < len(value) <= 80,
        "SYNTHETIC_NAMESPACE_REQUIRED",
    )


def aware(value: datetime) -> datetime:
    require(
        type(value) is datetime and value.tzinfo is not None and value.utcoffset() is not None,
        "AWARE_TIME_REQUIRED",
    )
    try:
        return value.astimezone(UTC)
    except (OverflowError, ValueError) as exc:
        raise DesignError("UTC_RANGE") from exc


@dataclass(frozen=True, slots=True)
class NoAuthority:
    synthetic_only: Literal[True] = True
    registered: Literal[False] = False
    live_authorized: Literal[False] = False
    scientific_scoring_authorized: Literal[False] = False
    trading_authority: Literal["NONE"] = "NONE"

    def __post_init__(self) -> None:
        require(
            self.synthetic_only is True
            and self.registered is False
            and self.live_authorized is False
            and self.scientific_scoring_authorized is False
            and self.trading_authority == "NONE",
            "NO_AUTHORITY",
        )


@dataclass(frozen=True, slots=True)
class DraftSlot:
    target_date: date
    city: str
    role: str
    cluster: str
    authority: NoAuthority = field(default_factory=NoAuthority)

    def __post_init__(self) -> None:
        require(
            type(self.authority) is NoAuthority
            and type(self.target_date) is date
            and START <= self.target_date <= END
            and self.city in CITIES
            and self.cluster == self.target_date.isoformat()
            and self.role == role(self.target_date),
            "DRAFT_SLOT_IDENTITY",
        )


def role(day: date) -> str:
    require(type(day) is date and START <= day <= END, "OUTSIDE_DRAFT_COHORT")
    if day <= date(2027, 2, 28):
        return "TRAIN"
    if day <= date(2027, 3, 14):
        return "EMBARGO_TRAIN_FREEZE"
    if day <= date(2027, 4, 14):
        return "CALIBRATION"
    if day <= date(2027, 4, 30):
        return "EMBARGO_CALIBRATION_FREEZE"
    return "UNTOUCHED_HOLDOUT"


def draft_slots(as_of: date = date(2026, 11, 4)) -> tuple[DraftSlot, ...]:
    require(type(as_of) is date and as_of < REGISTRATION_DUE.date(), "NEW_COHORT_REQUIRED")
    return tuple(
        DraftSlot(day, city, role(day), day.isoformat())
        for offset in range((END - START).days + 1)
        for day in (START + timedelta(days=offset),)
        for city in CITIES
    )


@dataclass(frozen=True, slots=True)
class ToyTrajectory:
    member: int
    hourly_fahrenheit: tuple[Decimal, ...]

    def __post_init__(self) -> None:
        require(type(self.member) is int and 0 <= self.member < 64, "MEMBER_ID")
        require(
            type(self.hourly_fahrenheit) is tuple and len(self.hourly_fahrenheit) == 24,
            "ALL_24_HOURS_REQUIRED",
        )
        for value in self.hourly_fahrenheit:
            number(value, low=D("-150"), high=D("150"))


@dataclass(frozen=True, slots=True)
class ToyJointMembers:
    fixture: str
    rows: tuple[ToyTrajectory, ...]
    source_kind: str = "SIM_JOINT_MEMBERS"

    def __post_init__(self) -> None:
        fixture_id(self.fixture)
        require(self.source_kind == "SIM_JOINT_MEMBERS", "P50_OR_MARGINALS_NOT_JOINT")
        require(
            type(self.rows) is tuple
            and len(self.rows) == 64
            and all(type(row) is ToyTrajectory for row in self.rows),
            "ALL_64_MEMBERS_REQUIRED",
        )
        require(sorted(row.member for row in self.rows) == list(range(64)), "MEMBER_COMPLETENESS")


@dataclass(frozen=True, slots=True)
class ToyLadder:
    """Only a declared integer lattice; no rounding of real TWC temperatures."""

    fixture: str
    ranges: tuple[tuple[int, int], ...]
    domain: str = "SIM_EXACT_INTEGER_F"

    def __post_init__(self) -> None:
        fixture_id(self.fixture)
        require(
            self.domain == "SIM_EXACT_INTEGER_F"
            and type(self.ranges) is tuple
            and len(self.ranges) == 4,
            "EXPLICIT_PRECISION_DOMAIN",
        )
        for index, pair in enumerate(self.ranges):
            require(
                type(pair) is tuple
                and len(pair) == 2
                and all(type(value) is int for value in pair)
                and pair[0] <= pair[1],
                "BOUNDS",
            )
            if index:
                require(pair[0] == self.ranges[index - 1][1] + 1, "GAP_OR_OVERLAP")

    def bin(self, value: Decimal) -> int:
        number(value, low=D("-150"), high=D("150"))
        require(value == value.to_integral_value(), "UNPROVEN_PRECISION_NO_ROUNDING")
        if value < self.ranges[0][0]:
            return 0
        for index, (lower, upper) in enumerate(self.ranges, 1):
            if lower <= value <= upper:
                return index
        require(value > self.ranges[-1][1], "UNCLASSIFIED")
        return 5


@dataclass(frozen=True, slots=True)
class ToyProbability:
    fixture: str
    probabilities: tuple[Decimal, ...]
    authority: NoAuthority = field(default_factory=NoAuthority)

    @arithmetic
    def __post_init__(self) -> None:
        fixture_id(self.fixture)
        require(type(self.probabilities) is tuple and len(self.probabilities) == 6, "SIX_BINS")
        for probability in self.probabilities:
            number(probability, low=D(0), high=D(1))
        require(sum(self.probabilities) == 1 and type(self.authority) is NoAuthority, "SIMPLEX")


@arithmetic
def raw_member_probabilities(members: ToyJointMembers, ladder: ToyLadder) -> ToyProbability:
    require(
        type(members) is ToyJointMembers
        and type(ladder) is ToyLadder
        and members.fixture == ladder.fixture,
        "SYNTHETIC_IDENTITY",
    )
    bins = Counter(ladder.bin(max(row.hourly_fahrenheit)) for row in members.rows)
    return ToyProbability(members.fixture, tuple(D(bins[index]) / D(64) for index in range(6)))


@arithmetic
def kelvin_to_fahrenheit(value: Decimal) -> Decimal:
    number(value, low=D("100"), high=D("400"))
    return (value - D("273.15")) * D(9) / D(5) + D(32)


@dataclass(frozen=True, slots=True)
class ToyLoss:
    fixture: str
    brier: Decimal
    logarithmic_loss: Decimal
    authority: NoAuthority = field(default_factory=NoAuthority)

    def __post_init__(self) -> None:
        fixture_id(self.fixture)
        require(type(self.authority) is NoAuthority, "NO_AUTHORITY")
        require(
            type(self.brier) is Decimal and self.brier.is_finite() and 0 <= self.brier <= 2,
            "BRIER_RANGE",
        )
        require(
            type(self.logarithmic_loss) is Decimal
            and not self.logarithmic_loss.is_nan()
            and self.logarithmic_loss >= 0,
            "EXTENDED_LOG_LOSS",
        )


@arithmetic
def score_toy(probabilities: ToyProbability, fixture: str, outcome_bin: int) -> ToyLoss:
    fixture_id(fixture)
    require(
        type(probabilities) is ToyProbability and probabilities.fixture == fixture,
        "SYNTHETIC_IDENTITY",
    )
    require(type(outcome_bin) is int and 0 <= outcome_bin < 6, "TOY_OUTCOME_BIN")
    brier = sum(
        ((p - D(index == outcome_bin)) ** 2 for index, p in enumerate(probabilities.probabilities)),
        D(0),
    )
    p = probabilities.probabilities[outcome_bin]
    with localcontext() as context:
        context.prec = 40
        loss = -p.ln() if p else D("Infinity")
    return ToyLoss(fixture, brier, loss)


@dataclass(frozen=True, slots=True)
class ToyFee:
    """A hypothetical parameter, not the Kalshi fee schedule or fee authority."""

    fixture: str
    coefficient: Decimal
    effective_start: datetime
    effective_end: datetime

    def __post_init__(self) -> None:
        fixture_id(self.fixture)
        number(self.coefficient, low=D(0), high=D(1))
        require(aware(self.effective_start) < aware(self.effective_end), "FEE_EFFECTIVE_INTERVAL")

    @arithmetic
    def cost(self, price: Decimal, at: datetime) -> Decimal:
        number(price, low=CENT, high=D("0.99"))
        require(price % CENT == 0, "CENT_TICK")
        require(aware(self.effective_start) <= aware(at) < aware(self.effective_end), "FEE_STALE")
        return (self.coefficient * price * (1 - price)).quantize(CENT, rounding=ROUND_CEILING)


@dataclass(frozen=True, slots=True)
class ToyQuote:
    fixture: str
    side: str
    ask: Decimal
    available_units: Decimal
    acquired_at: datetime
    decision_at: datetime
    close_at: datetime
    sequence_valid: bool = True
    order_type: str = "FOK_LIMIT"
    requested_units: Decimal = ONE
    partial_execution: bool = False
    latency_depth_verified: bool = True
    market_status: str = "OPEN"
    rules_and_source_consistent: bool = True
    siblings_complete: bool = True

    def __post_init__(self) -> None:
        fixture_id(self.fixture)
        require(self.side in ("YES", "NO"), "SIDE")
        number(self.ask, low=CENT, high=D("0.99"))
        number(self.available_units, low=D(0), high=D(100))
        number(self.requested_units, low=D(0), high=D(100))
        require(self.ask % CENT == 0, "CENT_TICK")
        for value in (self.acquired_at, self.decision_at, self.close_at):
            aware(value)
        for flag in (
            self.sequence_valid,
            self.partial_execution,
            self.latency_depth_verified,
            self.rules_and_source_consistent,
            self.siblings_complete,
        ):
            require(type(flag) is bool, "BOOLEAN_FLAGS")


@dataclass(frozen=True, slots=True)
class ToyEconomics:
    fixture: str
    decision: str
    reason: str
    scenario_lower_bounds: tuple[Decimal, ...] = ()
    authority: NoAuthority = field(default_factory=NoAuthority)

    def __post_init__(self) -> None:
        fixture_id(self.fixture)
        require(
            type(self.authority) is NoAuthority
            and self.decision in ("ABSTAIN", "SYNTHETIC_CANDIDATE_ONLY"),
            "NO_AUTHORITY",
        )
        require(
            all(
                type(value) is Decimal and value.is_finite() for value in self.scenario_lower_bounds
            ),
            "FINITE_TOY_RESULT",
        )
        require(
            self.decision != "SYNTHETIC_CANDIDATE_ONLY"
            or (
                len(self.scenario_lower_bounds) == 3
                and min(self.scenario_lower_bounds) >= D("0.02")
            ),
            "TOY_CANDIDATE_INEQUALITY",
        )


@arithmetic
def after_cost_toy(
    quote: ToyQuote,
    fee: ToyFee | None,
    yes_probability: tuple[Decimal, Decimal] | None,
    fill_probability: tuple[Decimal, Decimal] | None,
) -> ToyEconomics:
    require(type(quote) is ToyQuote, "TOY_QUOTE_REQUIRED")

    def abstain(reason: str) -> ToyEconomics:
        return ToyEconomics(quote.fixture, "ABSTAIN", reason)

    if yes_probability is None:
        return abstain("MISSING_MODEL_PROBABILITY")
    for bounds in (yes_probability, fill_probability):
        if bounds is not None:
            require(type(bounds) is tuple and len(bounds) == 2, "INTERVAL")
            number(bounds[0], low=D(0), high=D(1))
            number(bounds[1], low=D(0), high=D(1))
            require(bounds[0] <= bounds[1], "INTERVAL_ORDER")
    if fee is None:
        return abstain("MISSING_FEE_AUTHORITY_IN_FIXTURE")
    require(type(fee) is ToyFee and fee.fixture == quote.fixture, "SYNTHETIC_IDENTITY")
    if fill_probability is None or fill_probability == (D(0), D(1)):
        return abstain("UNIDENTIFIED_FILL_DISTRIBUTION")
    if quote.order_type != "FOK_LIMIT" or quote.requested_units != 1 or quote.partial_execution:
        return abstain("ORDER_SIZE_TYPE_OR_PARTIAL_FILL")
    if quote.available_units < 1 or not quote.sequence_valid or not quote.latency_depth_verified:
        return abstain("DEPTH_SEQUENCE_OR_LATENCY_UNVERIFIED")
    if (
        quote.market_status != "OPEN"
        or not quote.rules_and_source_consistent
        or not quote.siblings_complete
    ):
        return abstain("LIFECYCLE_RULES_SOURCE_OR_SIBLINGS_UNVERIFIED")
    age = aware(quote.decision_at) - aware(quote.acquired_at)
    if not timedelta(0) <= age <= timedelta(seconds=1):
        return abstain("STALE_OR_FUTURE_QUOTE")
    if aware(quote.close_at) - aware(quote.decision_at) < timedelta(minutes=15):
        return abstain("CLOSED_OR_TOO_NEAR_CLOSE")
    p = yes_probability[0] if quote.side == "YES" else 1 - yes_probability[1]
    qlo, qhi = fill_probability
    lower_bounds = []
    for price_stress, probability_stress in (
        (D("0.01"), D(0)),
        (D("0.02"), D("0.02")),
        (D("0.03"), D("0.05")),
    ):
        maximum = quote.ask + price_stress
        if maximum > D("0.99"):
            return abstain("STRESS_PRICE_OUTSIDE_SUPPORTED_TICKS")
        prices = [quote.ask + CENT * offset for offset in range(int(price_stress / CENT) + 1)]
        try:
            fees = [fee.cost(price, quote.decision_at) for price in prices]
            if max(fees) > D("0.05"):
                return abstain("FEE_EXCEEDS_DRAFT_COST_BOUND")
            cost = max(price + amount for price, amount in zip(prices, fees, strict=True))
        except DesignError:
            return abstain("FEE_STALE_OR_UNSUPPORTED")
        lower_p = max(D(0), p - probability_stress)
        candidates = [qlo, qhi]
        kink = 1 - lower_p
        if qlo <= kink <= qhi:
            candidates.append(kink)
        # Frechet lower joint bound: do not assume fills independent of outcomes.
        lower_bounds.append(min(max(D(0), lower_p + q - 1) - q * cost for q in candidates))
    if min(lower_bounds) < D("0.02"):
        return ToyEconomics(
            quote.fixture, "ABSTAIN", "NO_ROBUST_AFTER_COST_ADVANTAGE", tuple(lower_bounds)
        )
    return ToyEconomics(
        quote.fixture, "SYNTHETIC_CANDIDATE_ONLY", "TOY_INEQUALITY_SATISFIED", tuple(lower_bounds)
    )


def preserve_draft_denominator(
    states: tuple[tuple[date, str, str], ...],
) -> tuple[tuple[date, str, str], ...]:
    expected = {(slot.target_date, slot.city) for slot in draft_slots()}
    allowed = {
        "MISSING",
        "FAILED_ACQUISITION",
        "SKIPPED_SLOT",
        "ABSTAIN",
        "SYNTHETIC_ONLY",
        "EMBARGO",
    }
    observed: dict[tuple[date, str], str] = {}
    for day, city, state in states:
        require((day, city) in expected and (day, city) not in observed, "EXTRA_OR_DUPLICATE_SLOT")
        require(state in allowed, "NEGATIVE_OR_SYNTHETIC_STATES_ONLY")
        observed[day, city] = state
    return tuple(
        (
            slot.target_date,
            slot.city,
            observed.get(
                (slot.target_date, slot.city),
                "EMBARGO" if slot.role.startswith("EMBARGO") else "MISSING",
            ),
        )
        for slot in draft_slots()
    )
