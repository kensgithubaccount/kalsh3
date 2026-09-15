from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from services.forecasting.wn_a1_current_daily_high_authority import (
    CurrentDailyHighContract,
    WindowStatus,
)
from services.forecasting.wn_a1_domain import WnA1Error
from services.forecasting.wn_a1_probability import (
    MemberDailyHigh,
    boundary_risk,
    compute_member_daily_highs,
    compute_raw_probability,
    evaluate_predicate,
    kelvin_to_fahrenheit,
)
from services.forecasting.wn_a1_weathernext_evidence import (
    MEMBER_COUNT,
    MODEL,
    UNIT,
    VARIABLE,
    build_ensemble_evidence,
)

INIT_TIME = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
SOURCE_OBJECT = "gs://weathernext3_spatial/weathernext_3_0_0/zarr/2026_to_present/20260914_12hr_XX_preds/predictions.zarr/"


def contract(**changes) -> CurrentDailyHighContract:
    values = dict(
        market_ticker="KXHIGHCHI-26SEP15-T87",
        event_ticker="KXHIGHCHI-26SEP15",
        series_ticker="KXHIGHCHI",
        station_id="CLIMDW",
        location="Chicago",
        measurement="DAILY_MAX",
        local_date=date(2026, 9, 15),
        timezone="America/Chicago",
        lower=Decimal("87"),
        upper=None,
        comparator="GT",
        unit="degF",
        settlement_source="The Weather Company",
        settlement_source_url="https://weather.com/kalshi",
        window_status=WindowStatus.ESTABLISHED_CIVIL_LOCAL_DAY,
        window_start_local=None,
        window_end_local=None,
        window_evidence_text=None,
    )
    values.update(changes)
    return CurrentDailyHighContract(**values)


def test_kelvin_to_fahrenheit_exact() -> None:
    assert kelvin_to_fahrenheit(Decimal("273.15")) == Decimal("32.00")
    assert kelvin_to_fahrenheit(Decimal("373.15")) == Decimal("212.00")


def test_evaluate_predicate_range_greater_less() -> None:
    range_contract = contract(comparator="RANGE", lower=Decimal("86"), upper=Decimal("87"))
    assert evaluate_predicate(Decimal("86.5"), range_contract) is True
    assert evaluate_predicate(Decimal("85.9"), range_contract) is False
    gt_contract = contract(comparator="GT", lower=Decimal("87"), upper=None)
    assert evaluate_predicate(Decimal("87.1"), gt_contract) is True
    assert evaluate_predicate(Decimal("87.0"), gt_contract) is False
    lt_contract = contract(comparator="LT", lower=Decimal("80"), upper=None)
    assert evaluate_predicate(Decimal("79.9"), lt_contract) is True
    assert evaluate_predicate(Decimal("80.0"), lt_contract) is False


def _member_hour_evidence(kelvin_by_sample: dict[int, Decimal]):
    rows = [
        {
            "sample": sample,
            "lead_time_hours": 0,
            "lead_subtime_minutes": 0,
            "valid_time": INIT_TIME,
            "value_kelvin": kelvin,
        }
        for sample, kelvin in kelvin_by_sample.items()
    ]
    result = build_ensemble_evidence(
        model=MODEL,
        source_object=SOURCE_OBJECT,
        init_time=INIT_TIME,
        acquired_at=INIT_TIME,
        variable=VARIABLE,
        latitude=Decimal("41.80"),
        longitude=Decimal("-87.75"),
        unit=UNIT,
        raw_rows=rows,
        source_content_hash="b" * 64,
    )
    assert result.evidence is not None
    return result.evidence


def test_daily_max_computed_member_by_member() -> None:
    evidence = _member_hour_evidence(
        {i: Decimal("300.0") + Decimal(i) / Decimal(100) for i in range(MEMBER_COUNT)}
    )
    members = compute_member_daily_highs(evidence, INIT_TIME, INIT_TIME + timedelta(hours=1))
    assert len(members) == MEMBER_COUNT
    assert members[0].daily_high_f == kelvin_to_fahrenheit(Decimal("300.00"))
    assert all(m.hours_used == 1 for m in members)


def test_raw_probability_numerator_over_64_exact() -> None:
    kelvin = {i: Decimal("304.0") if i < 20 else Decimal("298.0") for i in range(MEMBER_COUNT)}
    evidence = _member_hour_evidence(kelvin)
    members = compute_member_daily_highs(evidence, INIT_TIME, INIT_TIME + timedelta(hours=1))
    c = contract(comparator="GT", lower=Decimal("86"))
    result = compute_raw_probability(members, c)
    assert result.denominator == 64
    assert result.numerator == 20
    assert result.probability == Decimal(20) / Decimal(64)


def test_boundary_risk_plus_and_minus_one_stress() -> None:
    # 33 members just above 87F, 31 just below -- a +/-1F shift can flip the majority side.
    kelvin = {}
    for i in range(MEMBER_COUNT):
        f_target = Decimal("87.3") if i < 33 else Decimal("86.6")
        kelvin[i] = (f_target - 32) * Decimal(5) / Decimal(9) + Decimal("273.15")
    evidence = _member_hour_evidence(kelvin)
    members = compute_member_daily_highs(evidence, INIT_TIME, INIT_TIME + timedelta(hours=1))
    over = contract(market_ticker="KXHIGHCHI-26SEP15-T87", comparator="GT", lower=Decimal("87"))
    under = contract(
        market_ticker="KXHIGHCHI-26SEP15-B85.5",
        comparator="RANGE",
        lower=Decimal("85"),
        upper=Decimal("87"),
    )
    siblings = {over.market_ticker: over, under.market_ticker: under}
    diagnostic = boundary_risk(members, siblings, over.market_ticker)
    assert diagnostic.members_within_one_f_of_boundary == MEMBER_COUNT
    assert diagnostic.minus_one_reverses is True


def test_boundary_risk_no_reversal_far_from_boundary() -> None:
    kelvin = {i: Decimal("308.0") for i in range(MEMBER_COUNT)}  # ~94.7F, far above 87
    evidence = _member_hour_evidence(kelvin)
    members = compute_member_daily_highs(evidence, INIT_TIME, INIT_TIME + timedelta(hours=1))
    over = contract(comparator="GT", lower=Decimal("87"))
    other = contract(market_ticker="other", comparator="LT", lower=Decimal("70"))
    siblings = {over.market_ticker: over, other.market_ticker: other}
    diagnostic = boundary_risk(members, siblings, over.market_ticker)
    assert diagnostic.members_within_one_f_of_boundary == 0
    assert diagnostic.reverses is False


def test_raw_probability_requires_all_64_members() -> None:
    members = tuple(
        MemberDailyHigh(sample=i, daily_high_f=Decimal("80"), hours_used=1) for i in range(63)
    )
    with pytest.raises(WnA1Error):
        compute_raw_probability(members, contract())


def test_member_daily_high_raises_when_member_has_zero_hours_in_window() -> None:
    kelvin = {i: Decimal("300.0") for i in range(MEMBER_COUNT)}
    evidence = _member_hour_evidence(kelvin)
    with pytest.raises(WnA1Error):
        compute_member_daily_highs(
            evidence, INIT_TIME + timedelta(hours=5), INIT_TIME + timedelta(hours=6)
        )
